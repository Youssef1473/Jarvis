"""Text-to-Speech synthesis using Kokoro-82M neural TTS, Windows SAPI5, and Edge-TTS."""
import asyncio
import io
import logging
import os
import queue
import re
import sys
import threading
import time
from typing import Dict, Any, Optional
import sounddevice as sd
import soundfile as sf
import win32com.client

logger = logging.getLogger("Jarvis.TTS")


class TextToSpeech:
    """Streams and plays synthesized neural audio with instant interrupt capability and local fallback."""

    def __init__(self, config: Dict[str, Any]):
        tts_cfg = config.get("tts", {})
        self.engine = tts_cfg.get("engine", "kokoro").lower()
        
        # Kokoro Neural Voice Settings
        self.kokoro_voice = tts_cfg.get("kokoro_voice", "bm_george")
        self.kokoro_speed = float(tts_cfg.get("kokoro_speed", 1.05))
        self.kokoro_lang = tts_cfg.get("kokoro_lang", "en-gb")
        self.cpu_threads = int(tts_cfg.get("cpu_threads", 4))
        base_dir = config.get("_base_dir", os.getcwd())
        self.kokoro_model_path = os.path.join(base_dir, tts_cfg.get("kokoro_model", "data/models/kokoro/kokoro-v1.0.onnx"))
        self.kokoro_voices_path = os.path.join(base_dir, tts_cfg.get("kokoro_voices", "data/models/kokoro/voices-v1.0.bin"))
        
        # SAPI5 Settings
        self.sapi_voice = tts_cfg.get("sapi_voice", "David")
        self.sapi_rate = tts_cfg.get("sapi_rate", 1)
        
        # Edge-TTS Settings
        self.voice = tts_cfg.get("voice", "en-GB-RyanNeural")
        self.rate = tts_cfg.get("rate", "+10%")
        self.volume = tts_cfg.get("volume", "+0%")
        self.pitch = tts_cfg.get("pitch", "+0Hz")
        self.stream_timeout_seconds = max(10.0, float(tts_cfg.get("stream_timeout_seconds", 300)))
        
        self._is_speaking = False
        self._stop_event = threading.Event()
        
        # Streaming worker state (Double-buffered: text queue -> audio queue)
        self._sentence_queue: queue.Queue = queue.Queue()
        self._audio_queue: queue.Queue = queue.Queue()
        self._synth_thread: Optional[threading.Thread] = None
        self._playback_thread: Optional[threading.Thread] = None
        self._stream_active = False

        # Kokoro engine instance
        self._kokoro = None
        if self.engine == "kokoro":
            self._init_kokoro()

        # Initialize local SAPI5 instance as fallback
        self._sapi = None
        self._init_sapi()

    def _init_kokoro(self):
        """Initializes Kokoro neural engine with constrained CPU threading to prevent CPU spikes."""
        try:
            # Silence phonemizer stderr mismatch warnings
            try:
                import phonemizer.logger
                phonemizer.logger.get_logger("quiet")
                logging.getLogger("phonemizer").setLevel(logging.ERROR)
            except Exception:
                pass

            if os.path.exists(self.kokoro_model_path) and os.path.exists(self.kokoro_voices_path):
                from jarvis.audio.stt import _setup_nvidia_dll_paths
                _setup_nvidia_dll_paths()
                import onnxruntime as rt
                from kokoro_onnx import Kokoro
                t0 = time.time()
                opts = rt.SessionOptions()
                opts.intra_op_num_threads = self.cpu_threads
                opts.inter_op_num_threads = 2
                opts.log_severity_level = 3
                opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
                available = rt.get_available_providers()
                providers = []
                if "CUDAExecutionProvider" in available:
                    providers.append("CUDAExecutionProvider")
                elif "DmlExecutionProvider" in available:
                    providers.append("DmlExecutionProvider")
                providers.append("CPUExecutionProvider")
                sess = rt.InferenceSession(self.kokoro_model_path, sess_options=opts, providers=providers)
                self._kokoro = Kokoro.from_session(sess, self.kokoro_voices_path)
                active_prov = sess.get_providers()[0] if hasattr(sess, "get_providers") else providers[0]
                logger.info(f"Kokoro-82M neural engine loaded ({self.kokoro_voice}) via {active_prov} in {time.time()-t0:.2f}s.")
            else:
                logger.warning("Kokoro model files not found. Defaulting to SAPI5.")
                self.engine = "sapi5"
        except Exception as e:
            logger.warning(f"Kokoro initialization failed: {e}. Defaulting to SAPI5.")
            self.engine = "sapi5"

    def _init_sapi(self):
        try:
            self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
            self._sapi.Rate = self.sapi_rate
        except Exception as e:
            logger.warning(f"Local SAPI5 voice initialization failed: {e}")

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def stop(self):
        """Immediately interrupts and halts speech playback across all engines."""
        self._stop_event.set()
        self._stream_active = False

        # Empty queues
        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
            except Exception:
                break

        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except Exception:
                break

        # Halt sounddevice audio stream
        try:
            sd.stop()
        except Exception:
            pass

        # Halt SAPI5 speech via SVSFPurgeBeforeSpeak (flag 2)
        if self._sapi:
            try:
                self._sapi.Speak("", 2)
            except Exception:
                pass

        self._is_speaking = False

    def _clean_text_for_speech(self, text: str) -> str:
        """Strips markdown and formatting so speech sounds completely natural."""
        if not text:
            return ""
        # Remove markdown horizontal rules (e.g. ---, ___, ***)
        cleaned = re.sub(r"[-*_]{2,}", "", text.strip())
        # Remove bold/italic markers
        cleaned = re.sub(r"[*_~`#]", "", cleaned)
        # Remove bullet points and numbered lists
        cleaned = re.sub(r"^\s*[-*•]+\s*", "", cleaned)
        cleaned = re.sub(r"^\s*\d+\.\s*", "", cleaned)
        # Remove URLs
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        # Normalize whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        # Must contain at least one pronounceable alphanumeric character
        if not re.search(r"[a-zA-Z0-9]", cleaned):
            return ""
        return cleaned

    def _split_into_audio_chunks(self, text: str) -> list[str]:
        """Splits text into natural clause phrases (commas, semicolons, colons, dashes)
        to enable ultra-low Time-to-First-Audio (<50ms) via progressive chunked synthesis."""
        if not text:
            return []

        # Split on natural pause delimiters
        raw_parts = re.split(r'([,;:—–\n]+)', text)
        chunks = []
        current = ""

        for part in raw_parts:
            if not part:
                continue
            current += part
            words = current.strip().split()
            # If current buffer ends in punctuation and has at least 3 words, emit chunk
            if re.search(r'[,;:—–\n]$', current.strip()) and len(words) >= 3:
                chunks.append(current.strip())
                current = ""
            elif len(words) >= 12:
                chunks.append(current.strip())
                current = ""

        if current.strip():
            if chunks and len(current.strip().split()) < 3:
                chunks[-1] = chunks[-1] + " " + current.strip()
            else:
                chunks.append(current.strip())

        return [c for c in chunks if c]

    def start_streaming_session(self):
        """Initializes a double-buffered streaming session with concurrent pre-synthesis."""
        self._stop_event.clear()
        self._is_speaking = True
        self._stream_active = True

        if self.engine == "kokoro" and self._kokoro:
            while not self._sentence_queue.empty():
                try:
                    self._sentence_queue.get_nowait()
                except Exception:
                    break

            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except Exception:
                    break

            # Stage 1: Synthesis Producer Worker (Computes next sentence audio in background)
            def _synth_worker():
                while self._stream_active and not self._stop_event.is_set():
                    try:
                        sentence = self._sentence_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if sentence is None or self._stop_event.is_set():
                        self._audio_queue.put(None)
                        break

                    clean_text = self._clean_text_for_speech(sentence)
                    if not clean_text:
                        continue

                    sub_chunks = self._split_into_audio_chunks(clean_text)
                    for chunk in sub_chunks:
                        if self._stop_event.is_set() or not self._stream_active:
                            break
                        try:
                            samples, sr = self._kokoro.create(
                                chunk,
                                voice=self.kokoro_voice,
                                speed=self.kokoro_speed,
                                lang=self.kokoro_lang
                            )
                            if not self._stop_event.is_set():
                                self._audio_queue.put((samples, sr))
                        except Exception as e:
                            logger.error(f"Kokoro synth error on '{chunk}': {e}")

            # Stage 2: Audio Playback Consumer Worker (Plays pre-buffered audio seamlessly)
            def _playback_worker():
                while self._stream_active and not self._stop_event.is_set():
                    try:
                        item = self._audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if item is None or self._stop_event.is_set():
                        break

                    samples, sr = item
                    try:
                        sd.play(samples, sr)
                        sd.wait()
                    except Exception as e:
                        logger.error(f"Playback error: {e}")

                self._is_speaking = False

            self._synth_thread = threading.Thread(target=_synth_worker, daemon=True)
            self._playback_thread = threading.Thread(target=_playback_worker, daemon=True)
            self._synth_thread.start()
            self._playback_thread.start()

        else:
            # SAPI5 streaming path
            import pythoncom
            try:
                pythoncom.CoInitialize()
                self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
                self._sapi.Rate = self.sapi_rate
                if self.sapi_voice:
                    for i in range(self._sapi.GetVoices().Count):
                        v = self._sapi.GetVoices().Item(i)
                        if self.sapi_voice.lower() in v.GetDescription().lower():
                            self._sapi.Voice = v
                            break
            except Exception as e:
                logger.error(f"Failed to start streaming SAPI session: {e}")

    def queue_sentence(self, sentence: str):
        """Enqueues and immediately begins pre-synthesizing a sentence in real time."""
        text = sentence.strip()
        if not text or self._stop_event.is_set():
            return

        if self.engine == "kokoro" and self._kokoro:
            self._sentence_queue.put(text)
        elif self._sapi:
            clean_text = self._clean_text_for_speech(text)
            if clean_text:
                try:
                    self._sapi.Speak(clean_text, 1)  # 1 = Async
                except Exception as e:
                    logger.error(f"SAPI queue error: {e}")

    def finish_streaming_session(self) -> bool:
        """Waits for all queued speech to finish; returns False only on a drain timeout."""
        if self.engine == "kokoro" and self._kokoro:
            self._sentence_queue.put(None)  # Signal synth worker

            # Do not declare the HUD idle while queued synthesis or audio is
            # still running. Polling also lets the normal barge-in stop event
            # release this wait immediately.
            deadline = time.monotonic() + self.stream_timeout_seconds
            workers = [self._synth_thread, self._playback_thread]
            timed_out = False
            for worker in workers:
                while worker and worker.is_alive():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    worker.join(timeout=min(0.1, remaining))
                if timed_out:
                    break

            if timed_out:
                logger.error(
                    "Speech stream did not drain within %.1f seconds; stopping playback.",
                    self.stream_timeout_seconds,
                )
                self.stop()
                for worker in workers:
                    if worker and worker.is_alive():
                        worker.join(timeout=2.0)

            self._is_speaking = False
            self._stream_active = False
            return not timed_out

        else:
            import pythoncom
            if self._sapi:
                try:
                    while not self._stop_event.is_set():
                        if self._sapi.WaitUntilDone(100):
                            break
                    if self._stop_event.is_set():
                        self._sapi.Speak("", 2)
                except Exception:
                    pass
                finally:
                    self._is_speaking = False
                    self._sapi = None
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
            return True

    def speak_sync(self, text: str):
        """Synchronously speaks text with Kokoro or SAPI."""
        clean_text = self._clean_text_for_speech(text)
        if not clean_text:
            return

        self._stop_event.clear()
        self._is_speaking = True

        if self.engine == "kokoro" and self._kokoro:
            try:
                sub_chunks = self._split_into_audio_chunks(clean_text)
                for chunk in sub_chunks:
                    if self._stop_event.is_set():
                        break
                    samples, sr = self._kokoro.create(
                        chunk,
                        voice=self.kokoro_voice,
                        speed=self.kokoro_speed,
                        lang=self.kokoro_lang
                    )
                    if not self._stop_event.is_set():
                        sd.play(samples, sr)
                        sd.wait()
            except Exception as e:
                logger.error(f"Kokoro speak_sync error: {e}. Falling back to SAPI5.")
                self._speak_local_sapi(clean_text)
            finally:
                self._is_speaking = False
        else:
            self._speak_local_sapi(clean_text)

    def _speak_local_sapi(self, text: str):
        """Speaks via local Windows SAPI5 engine."""
        import pythoncom
        pythoncom.CoInitialize()
        self._is_speaking = True
        try:
            sapi = win32com.client.Dispatch("SAPI.SpVoice")
            sapi.Rate = self.sapi_rate
            if self.sapi_voice:
                for i in range(sapi.GetVoices().Count):
                    v = sapi.GetVoices().Item(i)
                    if self.sapi_voice.lower() in v.GetDescription().lower():
                        sapi.Voice = v
                        break
            sapi.Speak(text, 1)
            while not self._stop_event.is_set():
                if sapi.WaitUntilDone(100):
                    break
            if self._stop_event.is_set():
                try:
                    sapi.Speak("", 2)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"SAPI5 speech error: {e}")
        finally:
            self._is_speaking = False
            pythoncom.CoUninitialize()

    async def speak(self, text: str):
        """Asynchronous speak helper."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.speak_sync, text)

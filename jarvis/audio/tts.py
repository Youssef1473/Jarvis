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
from typing import Dict, Any, Optional, List
import sounddevice as sd
import soundfile as sf
import win32com.client

from jarvis.audio.chunker import StreamingSentenceChunker
from jarvis.audio.ring_buffer import AudioRingBuffer
from jarvis.audio.affinity import pin_and_elevate_current_thread

logger = logging.getLogger("Jarvis.TTS")

__all__ = ["TextToSpeech", "StreamingSentenceChunker", "AudioRingBuffer"]


def _set_thread_realtime_priority(tag: str = "KokoroTTS"):
    """Pins current thread to P-Cores and sets priority to THREAD_PRIORITY_HIGHEST for zero audio jitter."""
    pin_and_elevate_current_thread(tag=tag)


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
        self.stream_timeout_seconds = max(5.0, float(tts_cfg.get("stream_timeout_seconds", 15.0)))
        
        self._is_speaking = False
        self._stop_event = threading.Event()
        
        # Streaming worker state & Speculative Voice Reflex pipeline
        self._sentence_queue: queue.Queue = queue.Queue()
        self._audio_queue: queue.Queue = queue.Queue()
        self._synth_thread: Optional[threading.Thread] = None
        self._playback_thread: Optional[threading.Thread] = None
        self._output_stream: Optional[sd.OutputStream] = None
        self._stream_active = False

        # Speculative token chunker & high-throughput lock-free audio ring buffer
        self._chunker = StreamingSentenceChunker()
        self._ring_buffer = AudioRingBuffer(capacity_samples=24000 * 30)

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
            # Silence phonemizer stderr mismatch warnings and protect Windows atexit teardown
            try:
                import phonemizer.logger
                phonemizer.logger.get_logger("quiet")
                logging.getLogger("phonemizer").setLevel(logging.ERROR)
                from phonemizer.backend.espeak.api import EspeakAPI
                _orig_del = getattr(EspeakAPI, "_delete_win32", None)
                if _orig_del:
                    def _safe_del_win32(self):
                        try:
                            if getattr(self, "_library", None) and getattr(self._library, "_handle", None):
                                _orig_del(self)
                        except Exception:
                            pass
                    EspeakAPI._delete_win32 = _safe_del_win32
            except Exception:
                pass

            if os.path.exists(self.kokoro_model_path) and os.path.exists(self.kokoro_voices_path):
                from jarvis.audio.stt import _setup_nvidia_dll_paths
                _setup_nvidia_dll_paths()
                import onnxruntime as rt
                rt.set_default_logger_severity(3)
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

        # Reset speculative token chunker
        if hasattr(self, "_chunker"):
            self._chunker.reset()

        # Clear audio ring buffer (< 1 microsecond atomic reset)
        if hasattr(self, "_ring_buffer"):
            self._ring_buffer.clear()

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

        # Halt & close continuous sounddevice hardware output stream immediately via abort
        stream = getattr(self, "_output_stream", None)
        if stream:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
            self._output_stream = None
        else:
            # Fallback halt for default sounddevice stream
            try:
                sd.stop()
            except Exception:
                pass

        # Halt SAPI5 speech only if sapi engine was active
        if self.engine == "sapi5" and self._sapi:
            try:
                self._sapi.Speak("", 2)
            except Exception:
                pass

        # Join any running synthesis and playback worker threads cleanly
        synth = getattr(self, "_synth_thread", None)
        if synth and synth.is_alive():
            try:
                synth.join(timeout=0.3)
            except Exception:
                pass

        play = getattr(self, "_playback_thread", None)
        if play and play.is_alive():
            try:
                play.join(timeout=0.3)
            except Exception:
                pass

        self._is_speaking = False

        # Clear PyTorch / ONNX CUDA cache if present
        if "torch" in sys.modules:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        import gc
        gc.collect()

    def _clean_text_for_speech(self, text: str) -> str:
        """Normalizes times, numbers, dates, units, and formatting for natural spoken delivery."""
        if not text:
            return ""
        from jarvis.audio.normalizer import SpeechNormalizer
        cleaned = SpeechNormalizer.normalize(text)
        if not re.search(r"[a-zA-Z0-9]", cleaned):
            return ""
        return cleaned

    def _split_into_audio_chunks(self, text: str) -> list[str]:
        """Splits text into natural clause phrases (commas, semicolons, dashes)
        to enable ultra-low Time-to-First-Audio (<50ms) via progressive chunked synthesis.
        Guards colons inside timestamps (e.g. 12:15) so times are never fragmented."""
        if not text:
            return []

        # Split on natural pause delimiters, ensuring colons between digits (like 12:15) are NOT split
        raw_parts = re.split(r'([,;\n]+|(?<!\d):|:(?!\d)|[—–]+)', text)
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

        if hasattr(self, "_chunker"):
            self._chunker.reset()
        if hasattr(self, "_ring_buffer"):
            self._ring_buffer.clear()

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

            # Stage 1: Dedicated Synthesis Producer Worker with Real-Time Priority
            def _synth_worker():
                _set_thread_realtime_priority("KokoroSynthWorker")
                while self._stream_active and not self._stop_event.is_set():
                    try:
                        sentence = self._sentence_queue.get(timeout=0.05)
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
                                # Write directly to the audio ring buffer for gapless WASAPI playback
                                self._ring_buffer.write(samples)
                                # Keep legacy audio queue populated for backwards compatibility
                                self._audio_queue.put((samples, sr))
                        except Exception as e:
                            logger.error(f"Kokoro synth error on '{chunk}': {e}")

            # Audio callback reading from lock-free audio ring buffer
            _cb_priority_elevated = False

            def _audio_callback(outdata, frames, time_info, status):
                nonlocal _cb_priority_elevated
                if not _cb_priority_elevated:
                    _set_thread_realtime_priority("WASAPIPlaybackCallback")
                    _cb_priority_elevated = True

                if self._stop_event.is_set():
                    outdata.fill(0.0)
                    return
                samples, n = self._ring_buffer.read(frames)
                if n > 0:
                    if outdata.ndim == 2 and outdata.shape[1] >= 2:
                        outdata[:n, 0] = samples[:n]
                        outdata[:n, 1] = samples[:n]
                    elif outdata.ndim == 2:
                        outdata[:n, 0] = samples[:n]
                    else:
                        outdata[:n] = samples[:n]
                if n < frames:
                    if outdata.ndim == 2:
                        outdata[n:, :].fill(0.0)
                    else:
                        outdata[n:].fill(0.0)

            # Stage 2: Hardware WASAPI / sounddevice Output Stream
            use_stream_callback = True
            try:
                self._output_stream = sd.OutputStream(
                    samplerate=24000,
                    channels=2,
                    dtype='float32',
                    callback=_audio_callback
                )
                self._output_stream.start()
            except Exception as e:
                logger.warning(f"Could not open hardware OutputStream callback: {e}. Falling back to standard playback.")
                use_stream_callback = False
                self._output_stream = None

            def _playback_worker():
                _set_thread_realtime_priority("WASAPIPlaybackWorker")
                while self._stream_active and not self._stop_event.is_set():
                    try:
                        item = self._audio_queue.get(timeout=0.05)
                    except queue.Empty:
                        continue

                    if item is None or self._stop_event.is_set():
                        break

                    if not use_stream_callback:
                        samples, sr = item
                        try:
                            sd.play(samples, sr)
                            sd.wait()
                        except Exception as e:
                            logger.error(f"Playback error: {e}")

                self._is_speaking = False

            self._synth_thread = threading.Thread(target=_synth_worker, daemon=True, name="Jarvis-TTS-Synth")
            self._playback_thread = threading.Thread(target=_playback_worker, daemon=True, name="Jarvis-TTS-Play")
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

    def feed_token(self, token: str) -> List[str]:
        """Streams an incremental LLM token into the speculative chunker.
        Immediately dispatches newly-delimited phoneme clauses to the real-time synthesis queue."""
        if not self._stream_active or self._stop_event.is_set():
            return []

        emitted_chunks = self._chunker.feed(token)
        for chunk in emitted_chunks:
            self.queue_sentence(chunk)
        return emitted_chunks

    def flush_tokens(self) -> List[str]:
        """Flushes any remaining text in the speculative chunker into the synthesis queue."""
        if not self._stream_active or self._stop_event.is_set():
            return []

        remaining_chunks = self._chunker.flush()
        for chunk in remaining_chunks:
            self.queue_sentence(chunk)
        return remaining_chunks

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
            # Flush any un-emitted text in the speculative chunker
            self.flush_tokens()

            self._sentence_queue.put(None)  # Signal synth worker

            # Do not declare the HUD idle while queued synthesis or audio is
            # still running. Polling also lets the normal barge-in stop event
            # release this wait immediately.
            deadline = time.monotonic() + self.stream_timeout_seconds
            workers = [self._synth_thread, self._playback_thread]
            timed_out = False

            # Wait for synthesis thread to finish
            if self._synth_thread:
                while self._synth_thread.is_alive():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    self._synth_thread.join(timeout=min(0.05, remaining))

            # Wait for audio ring buffer / hardware output stream to drain
            while (not self._ring_buffer.is_empty) and not self._stop_event.is_set() and not timed_out:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                time.sleep(0.01)

            # Brief cushion for DMA hardware buffer flush
            if not self._stop_event.is_set() and not timed_out:
                time.sleep(0.05)

            # Stop and close the hardware output stream
            if self._output_stream:
                try:
                    self._output_stream.stop()
                    self._output_stream.close()
                except Exception:
                    pass
                self._output_stream = None

            if self._playback_thread and self._playback_thread.is_alive():
                self._playback_thread.join(timeout=0.2)

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

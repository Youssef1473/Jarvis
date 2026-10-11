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
        
        self._is_speaking = False
        self._stop_event = threading.Event()
        
        # Streaming worker state
        self._sentence_queue: queue.Queue = queue.Queue()
        self._stream_thread: Optional[threading.Thread] = None
        self._stream_active = False

        # Kokoro engine instance
        self._kokoro = None
        if self.engine == "kokoro":
            self._init_kokoro()

        # Initialize local SAPI5 instance as fallback
        self._sapi = None
        self._init_sapi()

    def _init_kokoro(self):
        """Initializes Kokoro neural engine."""
        try:
            if os.path.exists(self.kokoro_model_path) and os.path.exists(self.kokoro_voices_path):
                from kokoro_onnx import Kokoro
                t0 = time.time()
                self._kokoro = Kokoro(self.kokoro_model_path, self.kokoro_voices_path)
                logger.info(f"Kokoro-82M neural engine loaded ({self.kokoro_voice}) in {time.time()-t0:.2f}s.")
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
        
        # Empty queue
        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
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
        self._stream_active = False

    def _clean_text_for_speech(self, text: str) -> str:
        """Strips markdown and formatting so speech sounds completely natural."""
        # Remove bold/italic markers
        cleaned = re.sub(r"[*_~`#]", "", text)
        # Remove bullet points and numbered lists
        cleaned = re.sub(r"^\s*[-*•]\s*", "", cleaned)
        cleaned = re.sub(r"^\s*\d+\.\s*", "", cleaned)
        # Remove URLs
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        # Normalize whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def start_streaming_session(self):
        """Initializes a streaming session for real-time sentence-by-sentence delivery."""
        self._stop_event.clear()
        self._is_speaking = True
        self._stream_active = True

        if self.engine == "kokoro" and self._kokoro:
            # Drain queue
            while not self._sentence_queue.empty():
                try:
                    self._sentence_queue.get_nowait()
                except Exception:
                    break

            def _stream_worker():
                while self._stream_active and not self._stop_event.is_set():
                    try:
                        sentence = self._sentence_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if sentence is None or self._stop_event.is_set():
                        break

                    clean_text = self._clean_text_for_speech(sentence)
                    if not clean_text:
                        continue

                    try:
                        samples, sr = self._kokoro.create(
                            clean_text,
                            voice=self.kokoro_voice,
                            speed=self.kokoro_speed,
                            lang=self.kokoro_lang
                        )
                        if self._stop_event.is_set():
                            break

                        sd.play(samples, sr)
                        # Wait for this sentence to finish playing or interrupt
                        while sd.get_stream().active and not self._stop_event.is_set():
                            time.sleep(0.04)
                    except Exception as e:
                        logger.error(f"Kokoro playback error on '{clean_text}': {e}")

                self._is_speaking = False

            self._stream_thread = threading.Thread(target=_stream_worker, daemon=True)
            self._stream_thread.start()

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
        """Enqueues and immediately begins speaking a sentence in real time."""
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

    def finish_streaming_session(self):
        """Waits for all queued sentences to complete playback or until interrupted."""
        if self.engine == "kokoro" and self._kokoro:
            self._sentence_queue.put(None)  # Sentinel to stop worker
            if self._stream_thread and self._stream_thread.is_alive():
                self._stream_thread.join(timeout=30.0)
            self._is_speaking = False
            self._stream_active = False

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

    def speak_sync(self, text: str):
        """Synchronously speaks text with Kokoro or SAPI."""
        clean_text = self._clean_text_for_speech(text)
        if not clean_text:
            return

        self._stop_event.clear()
        self._is_speaking = True

        if self.engine == "kokoro" and self._kokoro:
            try:
                samples, sr = self._kokoro.create(
                    clean_text,
                    voice=self.kokoro_voice,
                    speed=self.kokoro_speed,
                    lang=self.kokoro_lang
                )
                if not self._stop_event.is_set():
                    sd.play(samples, sr)
                    while sd.get_stream().active and not self._stop_event.is_set():
                        time.sleep(0.04)
                    if self._stop_event.is_set():
                        sd.stop()
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

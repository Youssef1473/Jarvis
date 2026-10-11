"""Text-to-Speech synthesis using edge-tts with automatic local Windows SAPI5 fallback."""
import asyncio
import io
import logging
import threading
from typing import Dict, Any, Optional
import edge_tts
import sounddevice as sd
import soundfile as sf
import win32com.client

logger = logging.getLogger("Jarvis.TTS")


class TextToSpeech:
    """Streams and plays synthesized neural audio with instant interrupt capability and local fallback."""

    def __init__(self, config: Dict[str, Any]):
        tts_cfg = config.get("tts", {})
        self.voice = tts_cfg.get("voice", "en-GB-RyanNeural")
        self.rate = tts_cfg.get("rate", "+10%")
        self.volume = tts_cfg.get("volume", "+0%")
        self.pitch = tts_cfg.get("pitch", "+0Hz")
        self._is_speaking = False
        self._stop_event = threading.Event()
        self._use_local_sapi_only = False

        # Initialize local SAPI5 fallback voice
        self._sapi = None
        self._init_sapi()

    def _init_sapi(self):
        try:
            self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
            # Increase rate slightly (+1) for alert delivery
            self._sapi.Rate = 1
        except Exception as e:
            logger.warning(f"Local SAPI5 voice initialization failed: {e}")

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def stop(self):
        """Immediately interrupts and halts speech playback across all engines."""
        self._stop_event.set()
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

    async def _synthesize_edge_audio(self, text: str) -> bytes:
        """Synthesizes text to in-memory MP3 audio bytes using edge-tts."""
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            volume=self.volume,
            pitch=self.pitch,
        )
        audio_buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.extend(chunk["data"])
        return bytes(audio_buffer)

    def _speak_local_sapi(self, text: str):
        """Speaks via local Windows SAPI5 engine (runs 100% offline)."""
        import pythoncom
        pythoncom.CoInitialize()
        self._is_speaking = True
        try:
            sapi = win32com.client.Dispatch("SAPI.SpVoice")
            sapi.Rate = 1
            # Flag 1 = SVSFlagsAsync
            sapi.Speak(text, 1)
            # Wait while SAPI is speaking
            while sapi.Status.RunningState == 2 and not self._stop_event.is_set():
                import time
                time.sleep(0.05)

            if self._stop_event.is_set():
                sapi.Speak("", 2)
        except Exception as e:
            logger.error(f"SAPI5 speech error: {e}")
        finally:
            self._is_speaking = False
            pythoncom.CoUninitialize()

    async def speak(self, text: str):
        """Synthesizes and speaks text using Edge-TTS with instant SAPI5 fallback."""
        if not text or not text.strip():
            return

        self._stop_event.clear()
        self._is_speaking = True

        # Fast path if Edge-TTS already failed this session
        if self._use_local_sapi_only:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._speak_local_sapi, text)
            return

        # Attempt Edge-TTS first
        try:
            mp3_bytes = await self._synthesize_edge_audio(text)
            if self._stop_event.is_set():
                self._is_speaking = False
                return

            with io.BytesIO(mp3_bytes) as bio:
                data, fs = sf.read(bio)

            if self._stop_event.is_set():
                self._is_speaking = False
                return

            sd.play(data, fs)
            while sd.get_stream().active and not self._stop_event.is_set():
                await asyncio.sleep(0.05)

            if self._stop_event.is_set():
                sd.stop()

        except Exception as e:
            logger.warning(f"Edge-TTS failed ({e}). Defaulting to local Windows SAPI5 voice for this session.")
            self._use_local_sapi_only = True
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._speak_local_sapi, text)
        finally:
            self._is_speaking = False

    def speak_sync(self, text: str):
        """Synchronous speech execution."""
        asyncio.run(self.speak(text))

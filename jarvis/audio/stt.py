"""Speech-to-Text transcription module using faster-whisper with CUDA acceleration."""
import logging
from typing import Dict, Any, Optional
import numpy as np

logger = logging.getLogger("Jarvis.STT")


class SpeechToText:
    """Wrapper around faster-whisper with automatic GPU/CPU selection."""

    def __init__(self, config: Dict[str, Any]):
        stt_cfg = config.get("stt", {})
        self.model_size = stt_cfg.get("model_size", "small")
        self.preferred_device = stt_cfg.get("device", "cuda")
        self.compute_type = stt_cfg.get("compute_type", "float16")
        self.language = stt_cfg.get("language", "en")
        self.model = None
        self._init_model()

    def _init_model(self):
        """Initializes the Faster-Whisper model on CUDA, falling back to CPU if necessary."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("faster_whisper package is not installed.")
            return

        device = self.preferred_device
        compute_type = self.compute_type

        # Try CUDA first if requested
        if device == "cuda":
            try:
                logger.info(f"Loading faster-whisper '{self.model_size}' on CUDA (float16)...")
                self.model = WhisperModel(self.model_size, device="cuda", compute_type=compute_type)
                logger.info("Successfully loaded faster-whisper on CUDA.")
                return
            except Exception as e:
                logger.warning(f"Failed to initialize faster-whisper on CUDA ({e}). Falling back to CPU...")

        # Fallback to CPU with int8
        try:
            logger.info(f"Loading faster-whisper '{self.model_size}' on CPU (int8)...")
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            logger.info("Successfully loaded faster-whisper on CPU.")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper on CPU: {e}")
            self.model = None

    def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> str:
        """
        Transcribes a 1D float32 numpy array of audio samples (normalized between -1.0 and 1.0).
        """
        if self.model is None:
            logger.error("Whisper model is not initialized.")
            return ""

        if len(audio_data) == 0:
            return ""

        # Normalize audio to float32 between -1.0 and 1.0
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        # Ensure amplitude is normalized
        max_abs = np.max(np.abs(audio_data))
        if max_abs > 1.0:
            audio_data = audio_data / max_abs

        try:
            segments, info = self.model.transcribe(
                audio_data,
                beam_size=3,
                language=self.language,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400),
            )
            text = " ".join([segment.text for segment in segments]).strip()
            return text
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return ""

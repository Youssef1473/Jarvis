"""Speech-to-Text transcription module using faster-whisper with CUDA acceleration and CPU fallback."""
import os
import sys
from pathlib import Path
import logging
from typing import Dict, Any, Optional
import numpy as np

logger = logging.getLogger("Jarvis.STT")


def _setup_nvidia_dll_paths():
    """Adds nvidia CUDA/cuBLAS/cuDNN library paths from site-packages if present."""
    if sys.platform != "win32":
        return
    try:
        venv_site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
        if venv_site.exists():
            for bin_dir in venv_site.glob("*/bin"):
                if bin_dir.is_dir():
                    try:
                        os.add_dll_directory(str(bin_dir))
                        logger.info(f"Registered NVIDIA DLL path: {bin_dir}")
                    except Exception:
                        pass
    except Exception:
        pass


class SpeechToText:
    """Wrapper around faster-whisper with automatic GPU/CPU selection and runtime fallback."""

    def __init__(self, config: Dict[str, Any]):
        _setup_nvidia_dll_paths()
        stt_cfg = config.get("stt", {})
        self.model_size = stt_cfg.get("model_size", "small")
        self.preferred_device = stt_cfg.get("device", "cuda")
        self.compute_type = stt_cfg.get("compute_type", "float16")
        self.language = stt_cfg.get("language", "en")
        self.model = None
        self._init_model()

    def _init_model(self):
        """Initializes the Faster-Whisper model on CUDA with validation, falling back to CPU."""
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
                logger.info(f"Loading faster-whisper '{self.model_size}' on CUDA ({compute_type})...")
                candidate_model = WhisperModel(self.model_size, device="cuda", compute_type=compute_type)
                # Verify that CUDA DLLs actually load with a 50ms warm-up sample
                dummy_audio = np.zeros(800, dtype=np.float32)
                _ = list(candidate_model.transcribe(dummy_audio, language="en", vad_filter=False)[0])
                self.model = candidate_model
                logger.info("Successfully validated faster-whisper on CUDA GPU.")
                return
            except Exception as e:
                logger.warning(f"CUDA initialization or DLL load failed ({e}). Falling back to CPU...")

        # Fallback to CPU with int8 (fast on multi-core 12th Gen i5)
        try:
            logger.info(f"Loading faster-whisper '{self.model_size}' on CPU (int8)...")
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", cpu_threads=6)
            logger.info("Successfully loaded faster-whisper on CPU.")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper on CPU: {e}")
            self.model = None

    def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> str:
        """
        Transcribes audio array with automatic CPU fallback on runtime errors.
        """
        if self.model is None:
            logger.error("Whisper model is not initialized.")
            return ""

        if len(audio_data) == 0:
            return ""

        # Normalize audio to float32 between -1.0 and 1.0
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

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
            logger.warning(f"Transcription error encountered: {e}. Switching to CPU...")
            try:
                from faster_whisper import WhisperModel
                self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", cpu_threads=6)
                segments, info = self.model.transcribe(
                    audio_data,
                    beam_size=3,
                    language=self.language,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=400),
                )
                text = " ".join([segment.text for segment in segments]).strip()
                return text
            except Exception as e2:
                logger.error(f"CPU transcription fallback also failed: {e2}")
                return ""

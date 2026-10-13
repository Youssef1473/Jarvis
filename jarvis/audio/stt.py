"""Speech-to-Text transcription module using faster-whisper with CUDA acceleration and CPU fallback."""
import os
import sys
from pathlib import Path
import logging
import re
from typing import Dict, Any, Optional
import numpy as np

from jarvis.audio.affinity import pin_and_elevate_current_thread

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
                        os.environ["PATH"] = f"{bin_dir};{os.environ.get('PATH', '')}"
                        logger.info(f"Registered NVIDIA DLL path: {bin_dir}")
                    except Exception:
                        pass
    except Exception:
        pass


class SpeechToText:
    """Wrapper around faster-whisper with automatic GPU/CPU selection and runtime fallback."""

    COMMON_PHONETIC_FIXES = [
        (re.compile(r"\bbrave\s+tower\b", re.IGNORECASE), "Brave tab"),
        (re.compile(r"\bbrave\s+top\b", re.IGNORECASE), "Brave tab"),
        (re.compile(r"\bbrave\s+tap\b", re.IGNORECASE), "Brave tab"),
        (re.compile(r"\bbrave\s+tag\b", re.IGNORECASE), "Brave tab"),
        (re.compile(r"\bbe\s+open\s+it\b", re.IGNORECASE), "will you open it"),
        (re.compile(r"\bchrome\s+tower\b", re.IGNORECASE), "Chrome tab"),
        (re.compile(r"\bchrome\s+top\b", re.IGNORECASE), "Chrome tab"),
        (re.compile(r"\bedge\s+tower\b", re.IGNORECASE), "Edge tab"),
        (re.compile(r"\bvs\s+cold\b", re.IGNORECASE), "VS Code"),
        (re.compile(r"\bvs\s+cope\b", re.IGNORECASE), "VS Code"),
    ]

    @classmethod
    def clean_transcription(cls, text: str) -> str:
        """Applies domain phonetic corrections for common browser and system commands."""
        if not text:
            return ""
        cleaned = text
        for pattern, replacement in cls.COMMON_PHONETIC_FIXES:
            cleaned = pattern.sub(replacement, cleaned)
        return cleaned.strip()

    def __init__(self, config: Dict[str, Any]):
        _setup_nvidia_dll_paths()
        stt_cfg = config.get("stt", {})
        self.model_size = stt_cfg.get("model_size", "small")
        self.preferred_device = stt_cfg.get("device", "cuda")
        self.compute_type = stt_cfg.get("compute_type", "float16")
        self.language = stt_cfg.get("language", "en")
        self.beam_size = int(stt_cfg.get("beam_size", 1))
        self.trim_silence = bool(stt_cfg.get("trim_silence", True))
        self.vad_filter = bool(stt_cfg.get("vad_filter", False))
        self.cpu_threads = int(stt_cfg.get("cpu_threads", 4))
        self.initial_prompt = stt_cfg.get("initial_prompt", "Jarvis, Sir, computer, volume, battery, system.")
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

        # Fallback to CPU with int8 (capped to 4 threads to prevent CPU spikes)
        try:
            logger.info(f"Loading faster-whisper '{self.model_size}' on CPU (int8, {self.cpu_threads} threads)...")
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", cpu_threads=self.cpu_threads)
            logger.info("Successfully loaded faster-whisper on CPU.")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper on CPU: {e}")
            self.model = None

    def stop(self):
        """Unloads the speech recognition model and releases CUDA GPU memory allocations."""
        if self.model is not None:
            self.model = None
            logger.info("Unloaded faster-whisper model from memory.")
        if "torch" in sys.modules:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        import gc
        gc.collect()

    @staticmethod
    def _trim_audio_silence(audio: np.ndarray, sample_rate: int = 16000, threshold: float = 0.008, pad_ms: int = 300) -> np.ndarray:
        """Trims leading and trailing silence from audio array using RMS energy while protecting trailing phonemes."""
        if len(audio) == 0:
            return audio

        frame_size = int(sample_rate * 0.02)
        pad_samples = int(sample_rate * (pad_ms / 1000.0))
        num_frames = len(audio) // frame_size
        if num_frames == 0:
            return audio

        frames = audio[:num_frames * frame_size].reshape(num_frames, frame_size)
        rms = np.sqrt(np.mean(frames ** 2, axis=1))
        active_indices = np.where(rms > threshold)[0]
        if len(active_indices) == 0:
            return audio

        start_frame = max(0, active_indices[0])
        end_frame = min(num_frames, active_indices[-1] + 1)
        start_sample = max(0, start_frame * frame_size - pad_samples)
        end_sample = min(len(audio), end_frame * frame_size + pad_samples)
        return audio[start_sample:end_sample]

    def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> str:
        """
        Transcribes audio array with beam search decoding, domain prompt conditioning, and automatic fallback.
        """
        pin_and_elevate_current_thread(tag="WhisperSTT")
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

        # Energy-based silence pre-trimming with safe 300ms boundary protection
        audio_to_transcribe = self._trim_audio_silence(audio_data, sample_rate=sample_rate) if self.trim_silence else audio_data

        try:
            segments, info = self.model.transcribe(
                audio_to_transcribe,
                beam_size=self.beam_size,
                language=self.language,
                temperature=0.0,
                condition_on_previous_text=False,
                initial_prompt=self.initial_prompt,
                vad_filter=self.vad_filter,
                vad_parameters=dict(
                    min_silence_duration_ms=300,
                    speech_pad_ms=200,
                ),
            )
            text = " ".join([segment.text for segment in segments]).strip()
            return self.clean_transcription(text)
        except Exception as e:
            logger.warning(f"Transcription error encountered: {e}. Switching to CPU...")
            try:
                from faster_whisper import WhisperModel
                self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", cpu_threads=self.cpu_threads)
                segments, info = self.model.transcribe(
                    audio_to_transcribe,
                    beam_size=self.beam_size,
                    language=self.language,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    initial_prompt=self.initial_prompt,
                    vad_filter=self.vad_filter,
                    vad_parameters=dict(
                        min_silence_duration_ms=300,
                        speech_pad_ms=200,
                    ),
                )
                text = " ".join([segment.text for segment in segments]).strip()
                return self.clean_transcription(text)
            except Exception as e2:
                logger.error(f"CPU transcription fallback also failed: {e2}")
                return ""

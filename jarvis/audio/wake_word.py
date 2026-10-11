"""Wake-word listener using openWakeWord ('Hey Jarvis')."""
import logging
import threading
from typing import Dict, Any, Callable, Optional
import numpy as np
import sounddevice as sd

logger = logging.getLogger("Jarvis.WakeWord")


class WakeWordDetector:
    """Continuously monitors microphone stream for the wake word ('Hey Jarvis')."""

    def __init__(
        self,
        config: Dict[str, Any],
        on_wake_callback: Optional[Callable[[], None]] = None,
        is_speaking_callback: Optional[Callable[[], bool]] = None
    ):
        audio_cfg = config.get("audio", {})
        self.wake_word = audio_cfg.get("wake_word", "hey jarvis")
        self.threshold = audio_cfg.get("wake_word_threshold", 0.55)
        self.sample_rate = audio_cfg.get("sample_rate", 16000)
        self.on_wake_callback = on_wake_callback
        self.is_speaking_callback = is_speaking_callback

        self.model = None
        self._is_running = False
        self._stream = None
        self._thread: Optional[threading.Thread] = None

    def _init_model(self):
        """Initializes openWakeWord model."""
        try:
            import openwakeword
            from openwakeword.model import Model

            # openWakeWord provides built-in models including "hey_jarvis"
            # Download models if not cached
            openwakeword.utils.download_models()
            self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            logger.info("Loaded openWakeWord 'hey_jarvis' model successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize openWakeWord: {e}")
            self.model = None

    def _audio_callback(self, indata, frames, time, status):
        """Processes audio chunks from the sounddevice InputStream."""
        if not self._is_running or self.model is None:
            return

        # Acoustic Echo Suppression: Do not detect wake word while Jarvis is speaking
        if self.is_speaking_callback and self.is_speaking_callback():
            return

        # indata is float32 [-1.0, 1.0], openWakeWord expects 16-bit PCM integer values
        audio_chunk = (indata[:, 0] * 32767).astype(np.int16)

        try:
            # Predict scores for audio chunk
            prediction = self.model.predict(audio_chunk)
            for model_name, score in prediction.items():
                if score >= self.threshold:
                    logger.info(f"Wake word detected! Score: {score:.3f}")
                    # Reset model buffer to prevent double triggers
                    self.model.reset()
                    if self.on_wake_callback:
                        self.on_wake_callback()
                    break
        except Exception as e:
            logger.error(f"Error predicting wake word: {e}")

    def start(self):
        """Starts wake word listening in the background."""
        if self._is_running:
            return

        if self.model is None:
            self._init_model()

        if self.model is None:
            logger.warning("openWakeWord model unavailable. Wake word detection disabled.")
            return

        self._is_running = True
        try:
            # Block size 1280 samples = 80ms chunks at 16kHz
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=1280,
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.info("Wake word detector started listening.")
        except Exception as e:
            logger.error(f"Failed to start audio stream for wake word: {e}")
            self._is_running = False

    def stop(self):
        """Stops wake word detection."""
        self._is_running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

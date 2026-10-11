"""Central orchestrator coordinating audio, hotkeys, STT, LLM, tools, and TTS."""
import asyncio
import logging
import threading
import time
from typing import Dict, Any, List
import numpy as np
import sounddevice as sd

from jarvis.audio.sound_effects import SoundEffects
from jarvis.audio.hotkey_listener import HotkeyListener
from jarvis.audio.stt import SpeechToText
from jarvis.audio.tts import TextToSpeech
from jarvis.audio.wake_word import WakeWordDetector
from jarvis.core.memory import MemoryManager
from jarvis.core.safety import SafetyGate
from jarvis.core.brain import JarvisBrain

logger = logging.getLogger("Jarvis.Orchestrator")


class JarvisOrchestrator:
    """Coordinates Jarvis's sensory inputs, cognitive loop, and speech output."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sample_rate = config.get("audio", {}).get("sample_rate", 16000)

        # Initialize sub-systems
        self.sfx = SoundEffects(config)
        self.memory = MemoryManager(config)
        self.safety = SafetyGate(config)
        self.stt = SpeechToText(config)
        self.tts = TextToSpeech(config)
        self.brain = JarvisBrain(config, self.memory, self.safety)

        # Recording state for Push-to-Talk (ALT)
        self._is_recording = False
        self._recorded_frames: List[np.ndarray] = []
        self._record_stream: sd.InputStream | None = None
        self._lock = threading.Lock()

        # Listeners
        self.hotkey_listener = HotkeyListener(
            on_press_callback=self._on_ptt_press,
            on_release_callback=self._on_ptt_release,
            on_any_key_callback=self._on_key_activity,
        )
        self.wake_detector = WakeWordDetector(
            config,
            on_wake_callback=self._on_wake_word,
        )

        self._running = False

    def _on_key_activity(self):
        """Barge-in hook: interrupts speech if user starts typing or presses a key."""
        if self.tts.is_speaking:
            self.tts.stop()

    def _audio_record_callback(self, indata, frames, time_info, status):
        """Accumulates microphone audio during push-to-talk."""
        if self._is_recording:
            with self._lock:
                self._recorded_frames.append(indata.copy())

    def _on_ptt_press(self):
        """Called immediately when user holds ALT."""
        # Barge-in: interrupt any active speech
        if self.tts.is_speaking:
            self.tts.stop()

        logger.info("ALT pressed: Listening...")
        self.sfx.play_wake()

        with self._lock:
            self._recorded_frames = []
            self._is_recording = True

        try:
            self._record_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=self._audio_record_callback,
            )
            self._record_stream.start()
        except Exception as e:
            logger.error(f"Failed to start audio recording stream: {e}")
            self._is_recording = False

    def _on_ptt_release(self):
        """Called immediately when user releases ALT."""
        if not self._is_recording:
            return

        logger.info("ALT released: Processing speech...")
        self._is_recording = False

        if self._record_stream:
            try:
                self._record_stream.stop()
                self._record_stream.close()
            except Exception:
                pass
            self._record_stream = None

        with self._lock:
            if not self._recorded_frames:
                logger.info("No audio captured.")
                return
            audio_array = np.concatenate(self._recorded_frames, axis=0).flatten()

        # Execute transcription and response in a separate worker thread
        threading.Thread(target=self._process_audio, args=(audio_array,), daemon=True).start()

    def _on_wake_word(self):
        """Called when openWakeWord detects 'Hey Jarvis'."""
        if self._is_recording:
            return

        if self.tts.is_speaking:
            self.tts.stop()

        logger.info("Wake word detected! Listening for command...")
        self.sfx.play_wake()

        # Record a 4-second utterance following wake word
        def record_and_process():
            try:
                duration = 4.0
                recorded = sd.rec(int(duration * self.sample_rate), samplerate=self.sample_rate, channels=1, dtype="float32")
                sd.wait()
                audio_array = recorded.flatten()
                self._process_audio(audio_array)
            except Exception as e:
                logger.error(f"Wake word capture error: {e}")

        threading.Thread(target=record_and_process, daemon=True).start()

    def _process_audio(self, audio_array: np.ndarray):
        """Transcribes audio, sends to brain, and delivers spoken output."""
        self.sfx.play_think()
        text = self.stt.transcribe(audio_array, sample_rate=self.sample_rate)
        if not text or len(text.strip()) < 2:
            logger.info("Audio was unintelligible or empty.")
            return

        print(f"\n[Sir]: {text}")
        self.handle_command(text)

    def handle_command(self, query: str):
        """Processes a text query, executes tools, and speaks the reply."""
        self.sfx.play_think()
        reply = self.brain.process_query(query)
        print(f"[Jarvis]: {reply}\n")
        self.sfx.play_done()

        # Speak the response asynchronously
        asyncio.run(self.tts.speak(reply))

    def start(self):
        """Starts all background listeners."""
        self._running = True
        logger.info("Starting Jarvis Hotkey Listener (Hold ALT to talk)...")
        self.hotkey_listener.start()

        logger.info("Starting Wake Word Detector ('Hey Jarvis')...")
        self.wake_detector.start()

        # Play system ready chime
        self.sfx.play_done()
        print("\n" + "=" * 60)
        print(" JARVIS 2.0 ONLINE & OPERATIONAL")
        print("  - Hold ALT to talk (Push-to-Talk)")
        print("  - Say 'Hey Jarvis' to wake via voice")
        print("  - Type directly below and press Enter to chat")
        print("  - Press Ctrl+C or type 'exit' to quit")
        print("=" * 60 + "\n")

    def run_interactive_loop(self):
        """Console input loop for text typing alongside voice."""
        self.start()
        try:
            while self._running:
                user_input = input("[Sir]: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "shutdown"):
                    print("[Jarvis]: Shutting down systems. Goodbye, Sir.")
                    break
                self.handle_command(user_input)
        except (KeyboardInterrupt, EOFError):
            print("\n[Jarvis]: Terminating session. Have a good day, Sir.")
        finally:
            self.stop()

    def stop(self):
        """Stops all running listeners and cleanup."""
        self._running = False
        self.hotkey_listener.stop()
        self.wake_detector.stop()
        self.tts.stop()

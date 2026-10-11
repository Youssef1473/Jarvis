"""Central orchestrator coordinating audio, hotkeys, STT, LLM, tools, TTS, and HUD overlay."""
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
from jarvis.ui.hud import JarvisHUD

logger = logging.getLogger("Jarvis.Orchestrator")


class JarvisOrchestrator:
    """Coordinates Jarvis's sensory inputs, cognitive loop, speech output, and holographic HUD."""

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
        self.hud = JarvisHUD()

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
            self.hud.set_state("idle")

    def _audio_record_callback(self, indata, frames, time_info, status):
        """Accumulates microphone audio during push-to-talk."""
        if self._is_recording:
            with self._lock:
                self._recorded_frames.append(indata.copy())

    def _on_ptt_press(self):
        """Called immediately when user holds ALT."""
        if self.tts.is_speaking:
            self.tts.stop()

        logger.info("ALT pressed: Listening...")
        self.hud.set_state("listening")
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
            self.hud.set_state("idle")

    def _on_ptt_release(self):
        """Called immediately when user releases ALT."""
        if not self._is_recording:
            return

        logger.info("ALT released: Processing speech...")
        self.hud.set_state("thinking")
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
                self.hud.set_state("idle")
                return
            audio_array = np.concatenate(self._recorded_frames, axis=0).flatten()

        threading.Thread(target=self._process_audio, args=(audio_array,), daemon=True).start()

    def _on_wake_word(self):
        """Called when openWakeWord detects 'Hey Jarvis'."""
        if self._is_recording:
            return

        if self.tts.is_speaking:
            self.tts.stop()

        logger.info("Wake word detected! Listening with dynamic VAD auto-cutoff...")
        self.hud.set_state("listening")
        self.sfx.play_wake()

        def record_and_process_vad():
            try:
                from openwakeword.vad import VAD
                vad = VAD()
                captured_chunks = []
                block_size = 1280
                sample_rate = self.sample_rate

                speech_started = False
                silence_chunk_count = 0
                max_chunks = int(7.0 * sample_rate / block_size)
                initial_timeout_chunks = int(2.5 * sample_rate / block_size)

                with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=block_size) as stream:
                    for chunk_idx in range(max_chunks):
                        if self._is_recording:
                            break

                        data, _ = stream.read(block_size)
                        captured_chunks.append(data.copy())

                        # Convert float32 to int16 PCM for VAD scoring
                        data_int16 = (data[:, 0] * 32767).astype(np.int16)
                        vad_score = vad.predict(data_int16)

                        if vad_score >= 0.35:
                            speech_started = True
                            silence_chunk_count = 0
                        elif speech_started:
                            if vad_score < 0.28:
                                silence_chunk_count += 1
                                # 7 silent chunks * 80ms = ~560ms of silence -> auto-cutoff!
                                if silence_chunk_count >= 7:
                                    logger.info(f"VAD auto-cutoff triggered (speech concluded at {chunk_idx * 0.08:.1f}s).")
                                    break
                        else:
                            if chunk_idx >= initial_timeout_chunks:
                                logger.info("VAD: no speech detected after wake word.")
                                break

                if captured_chunks:
                    audio_array = np.concatenate(captured_chunks, axis=0).flatten()
                    self._process_audio(audio_array)
                else:
                    self.hud.set_state("idle")

            except Exception as e:
                logger.error(f"VAD recording error: {e}")
                self.hud.set_state("idle")

        threading.Thread(target=record_and_process_vad, daemon=True).start()

    def _process_audio(self, audio_array: np.ndarray):
        """Transcribes audio, sends to brain, and delivers spoken output."""
        self.hud.set_state("thinking")
        self.sfx.play_think()
        text = self.stt.transcribe(audio_array, sample_rate=self.sample_rate)
        if not text or len(text.strip()) < 2:
            logger.info("Audio was unintelligible or empty.")
            self.hud.set_state("idle")
            return

        print(f"\n[Sir]: {text}")
        self.handle_command(text)

    @staticmethod
    def is_shutdown_command(query: str) -> bool:
        """Determines if query is a departure or shutdown request."""
        clean = "".join(c.lower() for c in query if c.isalnum() or c.isspace())
        triggers = [
            "goodbye", "good bye", "bye", "shutdown", "shut down",
            "exit", "quit", "thats it for now", "that is it for now",
            "thats all for now", "that is all for now",
            "power down", "deactivate", "go to sleep", "sleep now",
            "terminate", "close jarvis", "stop working"
        ]
        return any(t in clean for t in triggers)

    def handle_command(self, query: str):
        """Processes a text query, executes tools, and speaks the reply."""
        # Immediate direct shutdown handling
        if self.is_shutdown_command(query):
            farewell = "Powering down systems. Goodbye, Sir."
            print(f"[Jarvis]: {farewell}\n")
            self.sfx.play_done()
            self.hud.set_state("speaking")
            asyncio.run(self.tts.speak(farewell))
            self.hud.set_state("idle")
            self.stop()
            import os
            time.sleep(0.3)
            os._exit(0)
            return

        self.hud.set_state("thinking")
        self.sfx.play_think()

        # Start streaming pipelined speech session
        self.hud.set_state("speaking")
        self.tts.start_streaming_session()

        def queue_speech(sentence: str):
            self.tts.queue_sentence(sentence)

        reply = self.brain.process_query(query, sentence_callback=queue_speech)
        print(f"[Jarvis]: {reply}\n")
        self.sfx.play_done()

        # Complete playback of remaining queued audio
        self.tts.finish_streaming_session()
        self.hud.set_state("idle")

        # Check if shutdown tool was invoked during LLM reasoning
        from jarvis.skills.os_control import is_shutdown_requested
        if is_shutdown_requested():
            self.stop()
            import os
            time.sleep(0.3)
            os._exit(0)

    def start(self):
        """Starts all background listeners and the HUD overlay."""
        self._running = True
        logger.info("Starting Jarvis HUD Holographic Overlay...")
        self.hud.start()

        logger.info("Starting Jarvis Hotkey Listener (Hold ALT to talk)...")
        self.hotkey_listener.start()

        logger.info("Starting Wake Word Detector ('Hey Jarvis')...")
        self.wake_detector.start()

        self.sfx.play_done()
        print("\n" + "=" * 60)
        print(" JARVIS 2.0 ONLINE & OPERATIONAL")
        print("  - Floating Arc-Reactor HUD active (Drag to move)")
        print("  - Hold ALT to talk (Push-to-Talk)")
        print("  - Say 'Hey Jarvis' to wake via voice")
        print("  - Type directly below and press Enter to chat")
        print("  - Say 'Goodbye Jarvis' or type 'exit' to shut down")
        print("=" * 60 + "\n")

        # Spoken welcome greeting on launch
        self.hud.set_state("speaking")
        asyncio.run(self.tts.speak("Jarvis systems operational. All sub-systems online, Sir."))
        self.hud.set_state("idle")

    def run_interactive_loop(self):
        """Console input loop for text typing alongside voice."""
        self.start()
        try:
            while self._running:
                user_input = input("[Sir]: ").strip()
                if not user_input:
                    continue
                self.handle_command(user_input)
        except (KeyboardInterrupt, EOFError):
            print("\n[Jarvis]: Terminating session. Have a good day, Sir.")
            self.stop()
            import os
            os._exit(0)
        finally:
            self.stop()

    def stop(self):
        """Stops all running listeners and cleanup."""
        self._running = False
        self.hotkey_listener.stop()
        self.wake_detector.stop()
        self.tts.stop()
        self.hud.stop()

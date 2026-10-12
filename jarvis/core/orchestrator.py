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
from jarvis.core.heartbeat import JarvisHeartbeat
from jarvis.core.services import ManagedServiceManager
from jarvis.ui.hud import JarvisHUD

logger = logging.getLogger("Jarvis.Orchestrator")


def trim_process_memory(aggressive: bool = False):
    """Run low-cost collection; reserve working-set eviction for explicit opt-in."""
    import gc
    gc.collect(0)
    if not aggressive:
        return
    gc.collect()
    try:
        import ctypes
        ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
    except Exception:
        pass


class JarvisOrchestrator:
    """Coordinates Jarvis's sensory inputs, cognitive loop, speech output, holographic HUD, and proactive heartbeat."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sample_rate = config.get("audio", {}).get("sample_rate", 16000)

        # Initialize sub-systems
        self.sfx = SoundEffects(config)
        self.memory = MemoryManager(config)
        try:
            import jarvis.skills.productivity as prod_skills
            prod_skills.set_memory_manager(self.memory)
        except Exception:
            pass
        self.safety = SafetyGate(config)
        self.stt = SpeechToText(config)
        self.tts = TextToSpeech(config)
        self.service_manager = ManagedServiceManager()
        self.brain = JarvisBrain(config, self.memory, self.safety, service_manager=self.service_manager)
        self.hud = JarvisHUD()
        self.heartbeat = JarvisHeartbeat(self, config)

        # Web-Powered Holographic HUD Server
        ui_cfg = config.get("ui", {})
        if ui_cfg.get("enabled", True):
            from jarvis.ui.server import JarvisUIService
            self.ui_service = JarvisUIService(
                orchestrator=self,
                host=ui_cfg.get("host", "127.0.0.1"),
                port=int(ui_cfg.get("port", 20129)),
            )
        else:
            self.ui_service = None

        # Mute state
        self._is_muted = False

        # Recording state for Push-to-Talk (ALT)
        self._is_recording = False
        self._recorded_frames: List[np.ndarray] = []
        self._record_stream: sd.InputStream | None = None
        self._lock = threading.Lock()

        # Listeners
        self.hotkey_listener = HotkeyListener(
            config=config,
            on_press_callback=self._on_ptt_press,
            on_release_callback=self._on_ptt_release,
            on_any_key_callback=None,
        )
        self.wake_detector = WakeWordDetector(
            config,
            on_wake_callback=self._on_wake_word,
            is_speaking_callback=lambda: self.tts.is_speaking,
        )

        self._running = False
        self._stopped = False
        self._stop_lock = threading.Lock()

    def set_state(self, new_state: str):
        """Unified state switcher notifying both desktop Tkinter HUD and Web Holographic HUD."""
        self.hud.set_state(new_state)
        if getattr(self, "ui_service", None):
            self.ui_service.broadcast_state(new_state)

    def is_muted(self) -> bool:
        """Returns True if voice listening is currently muted."""
        return self._is_muted

    def toggle_mute(self) -> bool:
        """Toggles audio sensing mute state. Returns new muted status."""
        self._is_muted = not self._is_muted
        if getattr(self, "ui_service", None):
            self.ui_service.broadcast_telemetry({"is_muted": self._is_muted})

        if self._is_muted:
            logger.info("Jarvis audio sensors MUTED.")
            if self.tts.is_speaking:
                self.tts.stop()
            self.wake_detector.stop()
            self.set_state("idle")
        else:
            logger.info("Jarvis audio sensors UNMUTED.")
            self.wake_detector.start()
            self.sfx.play_wake()
        return self._is_muted

    def _on_key_activity(self):
        """Barge-in hook: interrupts speech if user starts typing or presses a key."""
        if self.tts.is_speaking:
            self.tts.stop()
            self.set_state("idle")

    def _audio_record_callback(self, indata, frames, time_info, status):
        """Accumulates microphone audio during push-to-talk."""
        if self._is_recording:
            with self._lock:
                self._recorded_frames.append(indata.copy())

    def _on_ptt_press(self):
        """Called immediately when user presses PTT trigger (MB2 or configured key)."""
        if self._is_muted:
            return

        if self.tts.is_speaking:
            self.tts.stop()

        with self._lock:
            self._recorded_frames = []
            self._is_recording = True
            self._ptt_press_time = time.time()

        is_mouse = getattr(self.hotkey_listener, "mouse_target_btn", None) is not None
        if is_mouse:
            # Non-blocking delayed cue: chime and show HUD only if held >= 200ms
            def _delayed_cue():
                time.sleep(0.20)
                if self._is_recording:
                    logger.info("PTT held (mouse): Listening...")
                    self.set_state("listening")
                    self.sfx.play_wake()

            threading.Thread(target=_delayed_cue, daemon=True).start()
        else:
            # Dedicated keyboard key (e.g. Fin / End): immediate activation
            logger.info("PTT pressed (Fin / End): Listening...")
            self.set_state("listening")
            self.sfx.play_wake()

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
            self.set_state("idle")

    def _on_ptt_release(self):
        """Called immediately when user releases PTT trigger."""
        if not self._is_recording:
            return

        press_duration = time.time() - getattr(self, "_ptt_press_time", 0.0)
        self._is_recording = False

        if self._record_stream:
            try:
                self._record_stream.stop()
                self._record_stream.close()
            except Exception:
                pass
            self._record_stream = None

        # Filter out accidental mouse taps (< 220ms) if using mouse button
        is_mouse = getattr(self.hotkey_listener, "mouse_target_btn", None) is not None
        if is_mouse and press_duration < 0.22:
            self.set_state("idle")
            return

        logger.info(f"PTT released (held {press_duration:.2f}s): Processing speech...")
        self.set_state("thinking")

        with self._lock:
            if not self._recorded_frames:
                logger.info("No audio captured.")
                self.set_state("idle")
                return
            audio_array = np.concatenate(self._recorded_frames, axis=0).flatten()

        threading.Thread(target=self._process_audio, args=(audio_array,), daemon=True).start()

    def _on_wake_word(self):
        """Called when openWakeWord detects 'Hey Jarvis'."""
        if self._is_muted or self._is_recording:
            return

        if self.tts.is_speaking:
            self.tts.stop()

        logger.info("Wake word detected! Listening with dynamic VAD auto-cutoff...")
        self.set_state("listening")
        self.sfx.play_wake()

        def record_and_process_vad():
            try:
                vad = self.wake_detector.get_vad()
                if vad is None:
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
                    self.set_state("idle")

            except Exception as e:
                logger.error(f"VAD recording error: {e}")
                self.set_state("idle")

        threading.Thread(target=record_and_process_vad, daemon=True).start()

    def _process_audio(self, audio_array: np.ndarray):
        """Transcribes audio, sends to brain, and delivers spoken output."""
        self.set_state("thinking")
        self.sfx.play_think()
        text = self.stt.transcribe(audio_array, sample_rate=self.sample_rate)
        if not text or len(text.strip()) < 2:
            logger.info("Audio was unintelligible or empty.")
            self.set_state("idle")
            return

        print(f"\n[Sir]: {text}")
        self.handle_command(text)

    @staticmethod
    def is_shutdown_command(query: str) -> bool:
        """Determines if query is a departure or shutdown request."""
        clean = "".join(c.lower() for c in query if c.isalnum() or c.isspace())
        normalized = " ".join(clean.split())
        if normalized in {
            "stop", "stop please", "please stop", "can you stop", "could you stop",
            "exit", "quit", "goodbye", "good bye", "bye",
        }:
            return True
        triggers = [
            "goodbye", "good bye", "bye", "shutdown", "shut down",
            "exit", "quit", "thats it for now", "that is it for now",
            "thats all for now", "that is all for now",
            "power down", "deactivate", "go to sleep", "sleep now",
            "power off", "go offline", "terminate", "close jarvis", "stop jarvis",
            "stop listening", "stop working", "stop all services", "stop all the services",
            "stop every service", "shut yourself down"
        ]
        return any(t in normalized for t in triggers)

    def handle_command(self, query: str):
        """Processes a text query, executes tools, and speaks the reply."""
        # Broadcast prompt to Holographic HUD
        if getattr(self, "ui_service", None):
            self.ui_service.broadcast_message("user", query)

        # Immediate direct shutdown handling
        if self.is_shutdown_command(query):
            farewell = "Powering down systems. Goodbye, Sir."
            print(f"[Jarvis]: {farewell}\n")
            if getattr(self, "ui_service", None):
                self.ui_service.broadcast_message("jarvis", farewell)
            self.sfx.play_done()
            self.set_state("speaking")
            asyncio.run(self.tts.speak(farewell))
            self.set_state("idle")
            self.stop()
            import os
            time.sleep(0.3)
            os._exit(0)
            return

        self.set_state("thinking")
        self.sfx.play_think()

        # Start streaming pipelined speech session
        self.set_state("speaking")
        self.tts.start_streaming_session()

        def queue_speech(sentence: str):
            self.tts.queue_sentence(sentence)

        reply = self.brain.process_query(query, sentence_callback=queue_speech)
        # Complete playback of remaining queued audio
        speech_completed = self.tts.finish_streaming_session()
        if not speech_completed:
            reply += (
                "\n\n[Speech playback timed out. The complete response is shown here, "
                "but Jarvis could not finish speaking it.]"
            )

        print(f"[Jarvis]: {reply}\n")

        # Publish the response after playback drain so the HUD state and
        # transcript reflect the actual end of speech.
        if getattr(self, "ui_service", None):
            self.ui_service.broadcast_message("jarvis", reply)

        self.set_state("idle")
        self.sfx.play_done()
        trim_process_memory(self.config.get("performance", {}).get("aggressive_working_set_trim", False))

        # Check if shutdown tool was invoked during LLM reasoning
        from jarvis.skills.os_control import is_shutdown_requested
        if is_shutdown_requested():
            self.stop()
            import os
            time.sleep(0.3)
            os._exit(0)

    def start(self):
        """Starts all background listeners, Web HUD, and the desktop overlay."""
        self._running = True

        # Launch Web Holographic HUD Server
        if getattr(self, "ui_service", None):
            logger.info("Starting Jarvis Holographic Web HUD Server...")
            self.ui_service.start()
            if self.config.get("ui", {}).get("auto_open", False):
                try:
                    import webbrowser
                    webbrowser.open(f"http://{self.ui_service.host}:{self.ui_service.port}")
                except Exception:
                    pass

        if self.config.get("hud", {}).get("desktop_overlay", False):
            logger.info("Starting Jarvis HUD Desktop Overlay...")
            self.hud.start()

        logger.info("Starting Jarvis Hotkey Listener (Hold ALT to talk)...")
        self.hotkey_listener.start()

        logger.info("Starting Wake Word Detector ('Hey Jarvis')...")
        self.wake_detector.start()

        logger.info("Starting Jarvis Proactive Heartbeat Daemon...")
        self.heartbeat.start()

        self.sfx.play_done()
        hud_url = f"http://{self.ui_service.host}:{self.ui_service.port}" if self.ui_service else "Disabled"
        print("\n" + "=" * 60)
        print(" JARVIS 3.0 ONLINE & OPERATIONAL")
        print(f"  - Holographic Web HUD active at {hud_url}")
        print("  - Floating Arc-Reactor HUD active (Drag to move)")
        print("  - Hold Fin (End) key to talk (Push-to-Talk)")
        print("  - Say 'Hey Jarvis' to wake via voice")
        print("  - Ambient Proactive Heartbeat active (Briefings & Alerts)")
        print("  - Type directly below and press Enter to chat")
        print("  - Say 'Goodbye Jarvis' or type 'exit' to shut down")
        print("=" * 60 + "\n")

        # Spoken welcome greeting on launch
        self.set_state("speaking")
        asyncio.run(self.tts.speak("Jarvis systems operational. All sub-systems online, Sir."))
        self.set_state("idle")
        trim_process_memory(self.config.get("performance", {}).get("aggressive_working_set_trim", False))

    def run_interactive_loop(self):
        """Main execution loop for voice and console interaction."""
        self.start()
        try:
            import sys
            is_interactive = sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
            if is_interactive:
                while self._running:
                    try:
                        user_input = input("[Sir]: ").strip()
                        if not user_input:
                            continue
                        self.handle_command(user_input)
                    except (EOFError, KeyboardInterrupt):
                        break
            else:
                # Running in stealth / background mode without terminal stdin
                while self._running:
                    time.sleep(0.5)
        except (KeyboardInterrupt, EOFError):
            print("\n[Jarvis]: Terminating session. Have a good day, Sir.")
            self.stop()
            import os
            os._exit(0)
        finally:
            self.stop()

    def stop(self):
        """Stops Jarvis's internal workers and every managed child service."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            self._running = False

        cleanup_steps = [
            ("Web HUD", getattr(self, "ui_service", None)),
            ("heartbeat", self.heartbeat),
            ("hotkey listener", self.hotkey_listener),
            ("wake detector", self.wake_detector),
            ("text-to-speech", self.tts),
            ("desktop HUD", self.hud),
        ]
        for name, component in cleanup_steps:
            if component is None:
                continue
            try:
                component.stop()
            except Exception:
                logger.exception("Could not stop Jarvis %s cleanly.", name)

        self.service_manager.stop_all()

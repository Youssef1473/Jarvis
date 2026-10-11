"""Global keyboard hotkey listener for Jarvis 2.0 (Push-to-Talk via ALT)."""
import threading
from typing import Callable, Optional
from pynput import keyboard


class HotkeyListener:
    """Listens globally for ALT key press and release for Push-to-Talk."""

    def __init__(
        self,
        on_press_callback: Optional[Callable[[], None]] = None,
        on_release_callback: Optional[Callable[[], None]] = None,
        on_any_key_callback: Optional[Callable[[], None]] = None,
    ):
        self.on_press_callback = on_press_callback
        self.on_release_callback = on_release_callback
        self.on_any_key_callback = on_any_key_callback
        self._is_alt_pressed = False
        self._listener: Optional[keyboard.Listener] = None
        self._lock = threading.Lock()

    def _is_alt_key(self, key) -> bool:
        """Determines if the given key event corresponds to ALT."""
        return key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r)

    def _on_key_down(self, key):
        if self.on_any_key_callback:
            try:
                self.on_any_key_callback()
            except Exception:
                pass

        if self._is_alt_key(key):
            with self._lock:
                if not self._is_alt_pressed:
                    self._is_alt_pressed = True
                    if self.on_press_callback:
                        try:
                            self.on_press_callback()
                        except Exception as e:
                            print(f"[Hotkey] Error in on_press_callback: {e}")

    def _on_key_up(self, key):
        if self._is_alt_key(key):
            with self._lock:
                if self._is_alt_pressed:
                    self._is_alt_pressed = False
                    if self.on_release_callback:
                        try:
                            self.on_release_callback()
                        except Exception as e:
                            print(f"[Hotkey] Error in on_release_callback: {e}")

    def start(self):
        """Starts listening in a dedicated daemon thread."""
        if self._listener is None:
            self._listener = keyboard.Listener(
                on_press=self._on_key_down,
                on_release=self._on_key_up,
            )
            self._listener.daemon = True
            self._listener.start()

    def stop(self):
        """Stops the global listener."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

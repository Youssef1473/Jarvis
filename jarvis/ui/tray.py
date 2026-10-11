"""System tray icon and background control for Jarvis 2.0."""
import threading
from PIL import Image, ImageDraw
import pystray


def create_tray_icon_image() -> Image.Image:
    """Procedurally creates an Arc-Reactor cyan circular icon for the system tray."""
    width = 64
    height = 64
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Outer cyan glowing ring
    draw.ellipse((4, 4, 60, 60), outline=(0, 210, 255, 255), width=4)
    # Inner reactor core
    draw.ellipse((18, 18, 46, 46), fill=(0, 240, 255, 255))
    # Center core highlight
    draw.ellipse((26, 26, 38, 38), fill=(255, 255, 255, 255))

    return image


class SystemTrayApp:
    """Manages the Windows taskbar notification tray icon."""

    def __init__(self, on_quit_callback=None):
        self.on_quit_callback = on_quit_callback
        self.icon = None

    def _on_quit(self, icon, item):
        if self.on_quit_callback:
            self.on_quit_callback()
        icon.stop()

    def run(self):
        """Runs the tray icon event loop."""
        image = create_tray_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("Jarvis 2.0 (Online)", lambda: None, enabled=False),
            pystray.MenuItem("Exit", self._on_quit)
        )
        self.icon = pystray.Icon("Jarvis2.0", image, "Jarvis 2.0 Assistant", menu)
        self.icon.run()

    def run_in_background(self):
        """Starts the tray icon in a dedicated daemon thread."""
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread

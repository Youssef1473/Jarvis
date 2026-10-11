"""Screen capture and visual perception for Jarvis 2.0."""
import io
import logging
from typing import Optional
from PIL import Image, ImageGrab

logger = logging.getLogger("Jarvis.Vision")


class ScreenPerception:
    """Captures desktop screenshots and prepares images for Gemini Vision analysis."""

    @staticmethod
    def capture_screen(max_width: int = 1600) -> Optional[Image.Image]:
        """Captures full screen and scales down proportionally for fast LLM inference."""
        try:
            screenshot = ImageGrab.grab(all_screens=False)
            # Scale down if width exceeds max_width to keep payload small and fast
            if screenshot.width > max_width:
                scale = max_width / float(screenshot.width)
                new_size = (max_width, int(screenshot.height * scale))
                screenshot = screenshot.resize(new_size, Image.Resampling.LANCZOS)
            return screenshot
        except Exception as e:
            logger.error(f"Failed to capture screen: {e}")
            return None

    @staticmethod
    def capture_screen_bytes(max_width: int = 1600, quality: int = 80) -> Optional[bytes]:
        """Captures screen and returns compressed JPEG bytes."""
        img = ScreenPerception.capture_screen(max_width=max_width)
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

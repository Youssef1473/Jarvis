"""Screen inspection and visual analysis skill for Jarvis 2.0."""
import logging
from typing import Optional
from jarvis.skills.base import SkillRegistry
from jarvis.vision.screen_capture import ScreenPerception

logger = logging.getLogger("Jarvis.Skills.Vision")


@SkillRegistry.register(
    name="inspect_screen",
    description="Captures the user's current screen to inspect errors, open applications, code, or visual documents."
)
def inspect_screen(query: str = "Describe what is currently visible on the screen") -> str:
    """Captures and inspects the user's active monitor screen."""
    logger.info(f"Screen inspection requested with query: {query}")
    screenshot = ScreenPerception.capture_screen(max_width=1600)
    if screenshot is None:
        return "I was unable to capture the screen display, Sir. Please ensure desktop permissions are granted."

    # Return confirmation that screen was grabbed
    return f"Active screen successfully captured ({screenshot.width}x{screenshot.height}). Analyzing visual display now..."

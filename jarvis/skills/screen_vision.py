"""Screen inspection and visual perception skill for Jarvis 2.0.

Provides multimodal visual perception of Sir's workstation screen using
ScreenPerception desktop capture and Google Gemini Multimodal Vision.
"""
import io
import logging
import os
import sys
from typing import Optional

from jarvis.skills.base import SkillRegistry
from jarvis.vision.screen_capture import ScreenPerception

logger = logging.getLogger("Jarvis.Skills.Vision")

# Priority cascade for cloud vision inference
VISION_CANDIDATE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
]


def _analyze_screen_with_gemini(image_bytes: bytes, query: str) -> Optional[str]:
    """Sends screen image bytes to Gemini Multimodal Vision API."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.warning("GEMINI_API_KEY is not configured for visual perception.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = (
            "You are JARVIS's visual cortex. Describe what is currently displayed on Sir's workstation screen "
            "with clarity, technical precision, and British composure. "
            "Identify active applications, open windows, text, code, browser tabs, or media. "
            f"Address Sir's focus: '{query}'."
        )

        for model_name in VISION_CANDIDATE_MODELS:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                        prompt,
                    ],
                )
                if response and response.text:
                    logger.info("Screen vision successfully analyzed via model '%s'.", model_name)
                    return response.text.strip()
            except Exception as model_err:
                logger.debug("Vision model '%s' failed (%s). Cascading to next candidate...", model_name, model_err)

    except Exception as err:
        logger.error("Error connecting to Gemini Vision API: %s", err)

    return None


@SkillRegistry.register(
    name="inspect_screen",
    description="Captures and visually analyzes Sir's active workstation screen to inspect open apps, errors, documents, or content."
)
def inspect_screen(query: str = "Describe what is currently visible on the screen") -> str:
    """Captures and inspects Sir's desktop display with multimodal vision."""
    logger.info("Screen inspection requested with query: %s", query)

    # 1. Capture screen
    image_bytes = ScreenPerception.capture_screen_bytes(max_width=1280, quality=75)
    if not image_bytes:
        return "I was unable to capture your display, Sir. Please ensure desktop permissions and interactive session access are granted."

    # 2. Analyze via Gemini Multimodal Vision
    analysis = _analyze_screen_with_gemini(image_bytes, query)
    if analysis:
        return analysis

    # 3. Graceful fallback using active OS window telemetry & running apps
    try:
        from jarvis.skills.os_control import get_active_window, list_running_applications
        active = get_active_window()
        apps = list_running_applications()
        return (
            f"Visual cloud inference was momentarily unavailable, Sir. However, according to desktop telemetry:\n"
            f"{active}\n\n"
            f"{apps}"
        )
    except Exception:
        return "I captured your screen display, Sir, but encountered a momentary delay in visual processing."


@SkillRegistry.register(
    name="see_screen",
    description="Visually perceives Sir's desktop screen in real-time, describing open applications, code, browser tabs, or text."
)
def see_screen(focus: str = "Describe what is visible on the screen") -> str:
    """Convenience alias for screen perception."""
    return inspect_screen(query=focus)

"""Unit tests for multimodal screen vision and perception skill."""
import unittest
from unittest.mock import patch, MagicMock

from jarvis.skills.base import SkillRegistry
import jarvis.skills.screen_vision as screen_vision


class TestScreenVisionSkill(unittest.TestCase):
    """Verifies screen vision skill registration, Gemini multimodal analysis, and fallbacks."""

    def test_skills_registered(self):
        """Verifies inspect_screen and see_screen are registered in SkillRegistry."""
        tools = SkillRegistry.get_all_tools()
        self.assertIn("inspect_screen", tools)
        self.assertIn("see_screen", tools)

    @patch("jarvis.skills.screen_vision.ScreenPerception.capture_screen_bytes")
    def test_capture_failure_graceful(self, mock_capture):
        """When screen capture fails, an informative message must be returned."""
        mock_capture.return_value = None
        result = screen_vision.inspect_screen()
        self.assertIn("unable to capture your display", result)

    @patch("jarvis.skills.screen_vision._analyze_screen_with_gemini")
    @patch("jarvis.skills.screen_vision.ScreenPerception.capture_screen_bytes")
    def test_gemini_vision_success(self, mock_capture, mock_gemini):
        """When Gemini analysis succeeds, its visual description must be returned."""
        mock_capture.return_value = b"\xff\xd8\xff\xe0fakejpegdata"
        mock_gemini.return_value = "VS Code is open displaying the test suite."

        result = screen_vision.inspect_screen(query="What is open?")
        self.assertEqual(result, "VS Code is open displaying the test suite.")

    @patch("jarvis.skills.os_control.get_active_window")
    @patch("jarvis.skills.screen_vision._analyze_screen_with_gemini")
    @patch("jarvis.skills.screen_vision.ScreenPerception.capture_screen_bytes")
    def test_gemini_fallback_to_window_telemetry(self, mock_capture, mock_gemini, mock_window):
        """When cloud vision analysis returns None, fallback to OS window telemetry."""
        mock_capture.return_value = b"\xff\xd8\xff\xe0fakejpegdata"
        mock_gemini.return_value = None
        mock_window.return_value = "Active Application: Terminal (Process: pwsh.exe)"

        result = screen_vision.inspect_screen()
        self.assertIn("Visual cloud inference was momentarily unavailable", result)
        self.assertIn("Active Application: Terminal", result)

    @patch("jarvis.skills.screen_vision.inspect_screen")
    def test_see_screen_alias(self, mock_inspect):
        """Verifies see_screen delegates to inspect_screen."""
        mock_inspect.return_value = "Test response"
        res = screen_vision.see_screen(focus="Check tabs")
        mock_inspect.assert_called_once_with(query="Check tabs")
        self.assertEqual(res, "Test response")


if __name__ == "__main__":
    unittest.main()

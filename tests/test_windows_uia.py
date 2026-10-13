"""Automated tests for pywinauto Windows UI Automation (UIA) tools."""
import sys
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.skills.base import SkillRegistry
import jarvis.skills.windows_uia as w_uia
import jarvis.skills.os_control as os_ctrl


class TestWindowsUIASuite(unittest.TestCase):
    """Verifies pywinauto UIA support, element inspection, and focus routing."""

    def test_uia_supported_on_windows(self):
        """Verifies UIA availability detection."""
        self.assertTrue(w_uia.is_uia_supported())

    def test_inspect_window_controls_empty(self):
        """Verifies empty window title is politely rejected."""
        res = SkillRegistry.execute("inspect_window_controls", window_title="")
        self.assertIn("cannot be empty", res.lower())

    def test_inspect_window_controls_nonexistent(self):
        """Verifies non-existent window handling."""
        res = SkillRegistry.execute("inspect_window_controls", window_title="nonexistent_xyz_998877")
        self.assertIn("could not find", res.lower())

    def test_focus_window_uia_empty(self):
        """Verifies empty title returns None cleanly without error."""
        res = w_uia.focus_window_uia("")
        self.assertIsNone(res)

    def test_os_control_focus_window_with_uia_fallback(self):
        """Verifies focus_window seamlessly handles non-existent windows via UIA/Win32."""
        res = os_ctrl.focus_window("nonexistent_app_window_9988")
        self.assertTrue(
            "no open window matching" in res.lower() or "failed to focus window" in res.lower()
        )


if __name__ == "__main__":
    unittest.main()

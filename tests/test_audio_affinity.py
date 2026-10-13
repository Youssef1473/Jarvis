"""Unit tests for Windows Win32 Thread Affinity and Real-Time Priority Scheduler."""
import sys
import unittest
from unittest.mock import patch, MagicMock

from jarvis.audio.affinity import (
    get_pcore_affinity_mask,
    pin_thread_to_pcores,
    elevate_thread_priority,
    pin_and_elevate_current_thread,
    THREAD_PRIORITY_HIGHEST,
    THREAD_PRIORITY_TIME_CRITICAL,
)


class TestAudioAffinity(unittest.TestCase):
    def test_get_pcore_affinity_mask_topology(self):
        """Verifies calculation of Performance core affinity bitmask across CPU topologies."""
        with patch("os.cpu_count", return_value=16):
            # 16-thread hybrid CPU (i5-12500H): 8 P-threads (mask 0x00FF = 255)
            self.assertEqual(get_pcore_affinity_mask(), 0x00FF)

        with patch("os.cpu_count", return_value=12):
            self.assertEqual(get_pcore_affinity_mask(), 0x00FF)

        with patch("os.cpu_count", return_value=8):
            # 8-core CPU: all 8 cores (mask 0xFF = 255)
            self.assertEqual(get_pcore_affinity_mask(), 0xFF)

        with patch("os.cpu_count", return_value=4):
            # 4-core CPU: mask 0xF = 15
            self.assertEqual(get_pcore_affinity_mask(), 0x0F)

    @unittest.skipUnless(sys.platform == "win32", "Win32 native API required for live affinity tests")
    def test_live_win32_pin_and_elevate(self):
        """Verifies live Win32 thread affinity and priority elevation on Windows host."""
        success = pin_and_elevate_current_thread(tag="UnitTestAffinity")
        self.assertTrue(success, "Win32 pin_and_elevate_current_thread should succeed on Windows")

    @unittest.skipUnless(sys.platform == "win32", "Win32 native API required for live affinity tests")
    def test_live_win32_elevate_priority_levels(self):
        """Verifies setting THREAD_PRIORITY_HIGHEST and TIME_CRITICAL on Windows host."""
        res_high = elevate_thread_priority(priority=THREAD_PRIORITY_HIGHEST)
        self.assertTrue(res_high)

        res_critical = elevate_thread_priority(priority=THREAD_PRIORITY_TIME_CRITICAL)
        self.assertTrue(res_critical)

        # Revert back to HIGHEST
        elevate_thread_priority(priority=THREAD_PRIORITY_HIGHEST)

    def test_non_windows_graceful_fallback(self):
        """Verifies that on non-Windows platforms, methods safely return False without error."""
        with patch("sys.platform", "linux"):
            self.assertFalse(pin_thread_to_pcores())
            self.assertFalse(elevate_thread_priority())
            self.assertFalse(pin_and_elevate_current_thread())


if __name__ == "__main__":
    unittest.main()

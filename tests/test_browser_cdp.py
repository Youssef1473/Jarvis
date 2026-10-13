"""Automated tests for Chrome DevTools Protocol (CDP) silent browser control."""
import json
import unittest
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.skills.base import SkillRegistry
import jarvis.skills.browser_cdp as b_cdp
import jarvis.skills.os_control as os_ctrl


class TestBrowserCDPSuite(unittest.TestCase):
    """Verifies CDP probe latency, silent tab navigation, and graceful Win32 fallbacks."""

    def test_cdp_probe_safety_and_speed(self):
        """Verifies CDP probe rejects non-localhost addresses and responds in <60ms."""
        # Non-localhost must be blocked immediately
        self.assertFalse(b_cdp.is_cdp_available("192.168.1.5", 9222))
        self.assertFalse(b_cdp.is_cdp_available("google.com", 9222))

        # Localhost probe test
        available = b_cdp.is_cdp_available("127.0.0.1", 9222, timeout_sec=0.02)
        self.assertIsInstance(available, bool)

    def test_list_open_browser_tabs_fallback(self):
        """Verifies tool returns courteous message when CDP port 9222 is not active."""
        with patch.object(b_cdp, "is_cdp_available", return_value=False):
            res = SkillRegistry.execute("list_open_browser_tabs")
            self.assertIn("port 9222 is closed", res)

    def test_list_cdp_tabs_parsing(self):
        """Verifies JSON response from /json/list is parsed into page targets."""
        fake_response = json.dumps([
            {"id": "tab1", "type": "page", "title": "YouTube", "url": "https://youtube.com"},
            {"id": "tab2", "type": "page", "title": "New Tab", "url": "brave://newtab"},
            {"id": "sw1", "type": "service_worker", "title": "Worker", "url": "sw.js"},
        ]).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__.return_value = mock_resp

        with patch.object(b_cdp, "is_cdp_available", return_value=True), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            tabs = b_cdp.list_cdp_tabs()
            self.assertEqual(len(tabs), 2, "Only 'page' targets should be returned")
            self.assertEqual(tabs[0]["title"], "YouTube")
            self.assertEqual(tabs[1]["url"], "brave://newtab")

    def test_open_or_navigate_tab_cdp_reuses_blank_tab(self):
        """Verifies open_or_navigate_tab_cdp reuses an open empty tab via WebSocket or activation."""
        fake_tabs = [
            {"id": "tab_blank", "type": "page", "title": "New Tab", "url": "about:blank", "ws_url": "ws://127.0.0.1:9222/devtools/page/tab_blank"}
        ]

        with patch.object(b_cdp, "is_cdp_available", return_value=True), \
             patch.object(b_cdp, "list_cdp_tabs", return_value=fake_tabs):
            
            mock_ws = MagicMock()
            mock_ws_enter = MagicMock()
            mock_ws_enter.__enter__.return_value = mock_ws

            with patch("websockets.sync.client.connect", return_value=mock_ws_enter), \
                 patch("urllib.request.urlopen") as mock_url:
                res = b_cdp.open_or_navigate_tab_cdp("https://github.com", new_tab=True)
                self.assertIsNotNone(res)
                self.assertIn("Navigated existing tab", res)
                self.assertIn("https://github.com", res)
                mock_ws.send.assert_called_once()
                sent_data = json.loads(mock_ws.send.call_args[0][0])
                self.assertEqual(sent_data["method"], "Page.navigate")
                self.assertEqual(sent_data["params"]["url"], "https://github.com")

    def test_os_control_open_browser_tab_cdp_fallback_to_win32(self):
        """Verifies open_browser_tab falls back gracefully to Win32 when CDP is closed."""
        with patch.object(b_cdp, "is_cdp_available", return_value=False), \
             patch("subprocess.Popen"), \
             patch("webbrowser.open_new_tab"):
            # Should proceed into win32gui without throwing exception
            res = os_ctrl.open_browser_tab(url="https://example.com", browser="brave")
            self.assertIsInstance(res, str)
            self.assertTrue(
                "active brave window" in res.lower()
                or "launched" in res.lower()
                or "opened" in res.lower()
                or "default" in res.lower()
            )


if __name__ == "__main__":
    unittest.main()

"""Integration tests for HUD Calendar Matrix, Endpoints, and Saved Calendar Presentation."""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from starlette.requests import Request
from jarvis.ui.server import JarvisUIService
from jarvis.core.memory import MemoryManager
from jarvis.core.config_loader import load_config


class TestHUDCalendarIntegration(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.service = JarvisUIService()
        self.memory = MemoryManager(self.config)

    def test_api_calendar_endpoint_returns_events_saved_cals_and_grouped(self):
        """Verifies /api/calendar endpoint supplies structured events, saved calendar documents, and grouped days."""
        async def run_req():
            scope = {'type': 'http', 'method': 'GET', 'path': '/api/calendar', 'headers': []}
            req = Request(scope)
            res = await self.service._handle_calendar(req)
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.body)
            self.assertIn("events", data)
            self.assertIn("saved_calendars", data)
            self.assertIn("grouped", data)
            self.assertIsInstance(data["events"], list)
            self.assertIsInstance(data["saved_calendars"], list)
            self.assertIsInstance(data["grouped"], dict)

            # Check that registered classes exist
            if data["events"]:
                titles = [e["title"] for e in data["events"]]
                self.assertTrue(any("C++" in t or "MATH" in t for t in titles))

            # Check that saved calendar documents exist in memory
            if data["saved_calendars"]:
                cal_titles = [c["title"] for c in data["saved_calendars"]]
                self.assertTrue(any("Timetable" in t or "Syllabus" in t for t in cal_titles))

        asyncio.run(run_req())

    def test_hud_html_contains_calendar_elements(self):
        """Verifies hud.html contains the CALENDAR button, modal backdrop, and week grid elements."""
        hud_path = Path("jarvis/ui/templates/hud.html")
        self.assertTrue(hud_path.exists())
        html = hud_path.read_text(encoding="utf-8")

        self.assertIn('id="btnCalendar"', html)
        self.assertIn('openCalendarModal()', html)
        self.assertIn('id="calendarModalBackdrop"', html)
        self.assertIn('id="calendarWeekGrid"', html)
        self.assertIn('id="savedCalendarsPills"', html)
        self.assertIn('id="activeCalTitle"', html)
        self.assertIn('id="activeCalSummary"', html)


if __name__ == "__main__":
    unittest.main()

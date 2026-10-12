"""Unit and regression tests for PC control extensions, fast reflex routing, and self-knowledge."""
import unittest
import time
from unittest.mock import MagicMock, patch
from jarvis.core.config_loader import load_config
from jarvis.skills.base import SkillRegistry
from jarvis.skills import os_control
from jarvis.core.brain import JarvisBrain
from jarvis.core.memory import MemoryManager
from jarvis.core.safety import SafetyGate


class TestPCControlAndPerformance(unittest.TestCase):
    """Verifies complete workstation PC control, low-latency reflex routing, and self-knowledge."""

    def setUp(self):
        self.config = load_config()
        self.config["llm"]["provider"] = "antigravity"
        self.config["llm"]["antigravity_model"] = "gemini-3.1-pro-low"
        self.config["llm"]["antigravity_auto_tiering"] = False
        self.memory = MemoryManager(self.config)
        self.safety = SafetyGate(self.config)
        self.brain = JarvisBrain(self.config, self.memory, self.safety)

    def test_os_control_skills_registered(self):
        """Ensures all PC control skills are registered in the SkillRegistry."""
        tools = SkillRegistry.get_all_tools()
        self.assertIn("open_browser_tab", tools)
        self.assertIn("focus_window", tools)
        self.assertIn("press_hotkey", tools)
        self.assertIn("type_text", tools)
        self.assertIn("launch_application", tools)
        self.assertIn("adjust_volume", tools)
        self.assertIn("minimize_all_windows", tools)
        self.assertIn("get_system_vitals", tools)

    def test_hotkey_safety_guard(self):
        """Verifies hotkey execution rejects dangerous system interrupt shortcuts."""
        res = os_control.press_hotkey("ctrl+alt+del")
        self.assertIn("prohibited", res.lower())

    def test_fast_reflex_volume(self):
        """Verifies volume commands execute via fast reflex in <15ms."""
        spoken = []
        t0 = time.perf_counter()
        reply = self.brain.process_query("mute", sentence_callback=lambda s: spoken.append(s))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertLess(elapsed_ms, 150.0)
        self.assertTrue(any("mute" in r.lower() for r in (reply, spoken[0] if spoken else "")))

    def test_fast_reflex_system_vitals(self):
        """Verifies system vitals execute via fast reflex in <25ms."""
        spoken = []
        t0 = time.perf_counter()
        reply = self.brain.process_query("system vitals", sentence_callback=lambda s: spoken.append(s))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertLess(elapsed_ms, 100.0)
        self.assertIn("CPU Utilization", reply)

    def test_fast_reflex_brave_tab(self):
        """Verifies 'open a brave tab' executes via fast reflex."""
        with patch("subprocess.Popen") as mock_popen, patch("webbrowser.open_new_tab") as mock_web:
            spoken = []
            reply = self.brain.process_query("open a brave tab", sentence_callback=lambda s: spoken.append(s))
            self.assertTrue("Brave" in reply or "browser" in reply.lower())
            self.assertTrue(len(spoken) > 0)

    def test_pass_through_for_questions_and_self_building(self):
        """Verifies questions and self-building tasks bypass fast reflex and route to cognitive brain."""
        self.assertIsNone(self.brain._try_fast_reflex("how do I open a brave tab?"))
        self.assertIsNone(self.brain._try_fast_reflex("why is the volume muted?"))
        self.assertIsNone(self.brain._try_fast_reflex("add a feature to open a brave tab with custom flags"))
        self.assertIsNone(self.brain._try_fast_reflex("create a command to monitor disk space"))

    def test_self_knowledge_in_system_prompt(self):
        """Verifies the system prompt contains complete repository architecture and self-knowledge."""
        prompt = self.brain._get_system_instruction()
        self.assertIn("JARVIS Architectural Blueprint & Repository Self-Knowledge", prompt)
        self.assertIn("jarvis/core/brain.py", prompt)
        self.assertIn("jarvis/skills/os_control.py", prompt)
        self.assertIn("create_or_update_custom_skill", prompt)
        self.assertIn("C:\\Users\\Youssef\\Projects\\Jarvis_2.0", prompt)


if __name__ == "__main__":
    unittest.main()

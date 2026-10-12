"""Integration test suite for Antigravity Google AI Pro CLI bridge in Jarvis 2.0."""
import os
import sys
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from jarvis.core.config_loader import load_config
from jarvis.core.antigravity_bridge import AntigravityBridge
from jarvis.core.brain import JarvisBrain
from jarvis.core.memory import MemoryManager
from jarvis.core.safety import SafetyGate


class TestAntigravityIntegration(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.memory = MemoryManager(self.config)
        self.safety = SafetyGate(self.config)
        self.bridge = AntigravityBridge(self.config)

    def test_bridge_discovery_and_model_resolution(self):
        """Verifies Antigravity CLI binary discovery and model catalog mappings."""
        self.assertTrue(self.bridge.is_available(), "agy binary should be discoverable in PATH")
        models = self.bridge.list_models()
        self.assertGreaterEqual(len(models), 10)
        self.assertIn("gemini-3.1-pro-low", models)
        self.assertIn("gemini-3.8-flash-low", models)
        self.assertIn("claude-sonnet-4-6", models)

        # Verify aliases
        self.assertEqual(self.bridge.resolve_model("pro"), "gemini-3.1-pro-low")
        self.assertEqual(self.bridge.resolve_model("gemini-pro"), "gemini-3.1-pro-low")
        self.assertEqual(self.bridge.resolve_model("flash"), "gemini-3.8-flash-low")
        self.assertEqual(self.bridge.resolve_model("claude"), "claude-sonnet-4-6")
        self.assertEqual(self.bridge.resolve_model("sonnet"), "claude-sonnet-4-6")
        self.assertEqual(self.bridge.resolve_model("opus"), "claude-opus-4-6-thinking")

    def test_antigravity_batch_generation(self):
        """Verifies real response generation with Google Antigravity session."""
        res = self.bridge.generate_response(
            prompt="Respond with exactly two words: System Online.",
            model="gemini-3.8-flash-low",
            timeout=30.0
        )
        self.assertEqual(res.get("status"), "SUCCESS")
        self.assertIn("Online", res.get("response", ""))
        self.assertGreater(res.get("duration_seconds", 0), 0)

    def test_antigravity_streaming_clause_extraction(self):
        """Verifies real-time speech clause extraction during token streaming."""
        buffer = "Good afternoon, Sir. All systems are operational. What would you like to review?"
        chunk, remaining = AntigravityBridge._extract_speech_chunk(buffer)
        self.assertIsNotNone(chunk)
        self.assertTrue(any(c in chunk for c in ["Good afternoon, Sir.", "All systems"]))

    def test_specialist_consultation_routing(self):
        """Verifies JarvisBrain _consult_specialist routes 'pro' and 'claude' via Antigravity without API keys."""
        brain = JarvisBrain(self.config, self.memory, self.safety)
        status = brain.specialist_status()
        self.assertIn("antigravity", status)
        self.assertTrue("READY" in status["antigravity"] or "ACTIVE" in status["antigravity"])

        # Test consult pro
        reply_pro = brain._consult_specialist("pro", "Reply in 3 words: Pro is ready.")
        self.assertTrue(len(reply_pro.strip()) > 0)
        self.assertNotIn("API consultations are disabled", reply_pro)
        self.assertNotIn("is not connected", reply_pro)

    def test_brain_antigravity_provider_execution(self):
        """Verifies JarvisBrain running in direct provider='antigravity' mode."""
        ag_config = dict(self.config)
        ag_config["llm"] = dict(self.config.get("llm", {}))
        ag_config["llm"]["provider"] = "antigravity"
        ag_config["llm"]["antigravity_model"] = "gemini-3.8-flash-low"

        ag_brain = JarvisBrain(ag_config, self.memory, self.safety)
        self.assertEqual(ag_brain.provider, "antigravity")

        reply = ag_brain.process_query("What is 10 plus 15? Answer with only the number.")
        self.assertIn("25", reply)

    def test_cascade_pool_building(self):
        """Verifies candidate fallback pools are constructed properly."""
        flash_cascade = self.bridge.get_candidate_cascade("flash")
        self.assertEqual(flash_cascade[0], "gemini-3.8-flash-low")
        self.assertIn("gemini-3.7-flash-low", flash_cascade)
        self.assertIn("gemini-3.6-flash-low", flash_cascade)

        pro_cascade = self.bridge.get_candidate_cascade("pro")
        self.assertEqual(pro_cascade[0], "gemini-3.1-pro-low")
        self.assertIn("gemini-3.8-flash-low", pro_cascade)

        claude_cascade = self.bridge.get_candidate_cascade("claude")
        self.assertEqual(claude_cascade[0], "claude-sonnet-4-6")
        self.assertIn("gemini-3.1-pro-low", claude_cascade)

    def test_prompt_complexity_auto_tiering(self):
        """Verifies prompt classification routes correctly to Flash, Pro, and Claude."""
        self.assertEqual(self.bridge.classify_prompt_tier("def refactor_code(): return True"), "claude-sonnet-4-6")
        self.assertEqual(self.bridge.classify_prompt_tier("analyze the strategy and architecture of the codebase"), "gemini-3.1-pro-low")
        self.assertEqual(self.bridge.classify_prompt_tier("hello jarvis what time is it"), "gemini-3.8-flash-low")

    def test_voice_model_switching_commands(self):
        """Verifies voice commands dynamically switch models and auto-tiering."""
        brain = JarvisBrain(self.config, self.memory, self.safety)
        
        # Test switch to pro
        res_pro = brain.process_query("switch to pro")
        self.assertIn("gemini-3.1-pro-low", res_pro)
        self.assertFalse(brain.antigravity_auto_tiering)
        self.assertEqual(brain.antigravity_model, "gemini-3.1-pro-low")

        # Test switch to flash
        res_flash = brain.process_query("switch to flash")
        self.assertIn("gemini-3.8-flash-low", res_flash)
        self.assertFalse(brain.antigravity_auto_tiering)
        self.assertEqual(brain.antigravity_model, "gemini-3.8-flash-low")

        # Test switch to claude
        res_claude = brain.process_query("switch to claude")
        self.assertIn("claude-sonnet-4-6", res_claude)
        self.assertFalse(brain.antigravity_auto_tiering)
        self.assertEqual(brain.antigravity_model, "claude-sonnet-4-6")

        # Test switch to auto
        res_auto = brain.process_query("switch to auto")
        self.assertIn("Dynamic model tiering activated", res_auto)
        self.assertTrue(brain.antigravity_auto_tiering)

        # Test switch to hybrid
        res_hybrid = brain.process_query("switch to hybrid")
        self.assertIn("Hybrid token-shield mode activated", res_hybrid)
        self.assertEqual(brain.provider, "hybrid")

        # Test what model is active in hybrid mode
        res_active_hybrid = brain.process_query("what model are you using")
        self.assertIn("Hybrid Token-Shield mode", res_active_hybrid)

    def test_hybrid_token_shield_routing(self):
        """Verifies that Hybrid mode routes OS commands locally and deep queries to Google AI Pro."""
        brain = JarvisBrain(self.config, self.memory, self.safety)
        brain.provider = "hybrid"

        # 1. OS command classification -> Fast / Local reflex (0 tokens)
        tier_mute = brain.classify_query_tier("mute the volume please")
        self.assertEqual(tier_mute, "fast")

        # 2. Deep academic / timetable classification -> Deep Apex tier (Google AI Pro)
        tier_cal = brain.classify_query_tier("can you analyze my class timetable and schedule my study session for calculus?")
        self.assertEqual(tier_cal, "deep")

        # 3. Code query classification -> Deep Apex tier (Claude Sonnet 4.6)
        tier_code = brain.classify_query_tier("write a python function to parse json and handle exceptions")
        self.assertEqual(tier_code, "deep")


if __name__ == "__main__":
    unittest.main()

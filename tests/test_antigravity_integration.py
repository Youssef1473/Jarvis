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


if __name__ == "__main__":
    unittest.main()

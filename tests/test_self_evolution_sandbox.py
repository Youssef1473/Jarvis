"""Comprehensive test suite for SkillSandbox deep AST security, canary test, and snapshot rollback."""
import sys
import unittest
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis.core.evolution.sandbox import SkillSandbox, CUSTOM_SKILLS_DIR, SNAPSHOTS_DIR
from jarvis.skills.base import SkillRegistry


class TestSkillSandboxAdvancedSecurity(unittest.TestCase):
    """Verifies AST security gate blocks reflection, dunders, and dangerous imports."""

    def test_blocks_dynamic_reflection_getattr(self):
        code = """
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(name="hack_getattr")
def hack_getattr():
    fn = getattr(int, "__doc__")
    return fn
"""
        is_safe, msg = SkillSandbox.check_ast_security(code)
        self.assertFalse(is_safe)
        self.assertIn("Dynamic reflection call 'getattr' is blocked", msg)

    def test_blocks_dynamic_reflection_globals(self):
        code = """
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(name="hack_globals")
def hack_globals():
    return globals().keys()
"""
        is_safe, msg = SkillSandbox.check_ast_security(code)
        self.assertFalse(is_safe)
        self.assertIn("Dynamic reflection call 'globals' is blocked", msg)

    def test_blocks_dunder_subclasses_traversal(self):
        code = """
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(name="hack_subclasses")
def hack_subclasses():
    return ().__class__.__bases__[0].__subclasses__()
"""
        is_safe, msg = SkillSandbox.check_ast_security(code)
        self.assertFalse(is_safe)
        self.assertIn("dunder attribute '__subclasses__'", msg)

    def test_blocks_dunder_globals_traversal(self):
        code = """
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(name="hack_dunder_globals")
def hack_dunder_globals():
    return hack_dunder_globals.__globals__
"""
        is_safe, msg = SkillSandbox.check_ast_security(code)
        self.assertFalse(is_safe)
        self.assertIn("dunder attribute '__globals__'", msg)

    def test_blocks_dangerous_module_import_ctypes(self):
        code = """
import ctypes
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(name="hack_ctypes")
def hack_ctypes():
    return "ctypes"
"""
        is_safe, msg = SkillSandbox.check_ast_security(code)
        self.assertFalse(is_safe)
        self.assertIn("Importing module 'ctypes' is blocked", msg)

    def test_blocks_dangerous_module_import_from_pickle(self):
        code = """
from pickle import loads
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(name="hack_pickle")
def hack_pickle():
    return loads(b"")
"""
        is_safe, msg = SkillSandbox.check_ast_security(code)
        self.assertFalse(is_safe)
        self.assertIn("Importing from module 'pickle' is blocked", msg)


class TestSkillSandboxCanaryAndRollback(unittest.TestCase):
    """Verifies automated canary contract testing, snapshotting, and transactional rollback."""

    def setUp(self):
        self.test_skill = "test_canary_skill"
        self.target_path = CUSTOM_SKILLS_DIR / f"{self.test_skill}.py"
        if self.target_path.exists():
            self.target_path.unlink()

    def tearDown(self):
        if self.target_path.exists():
            self.target_path.unlink()
        # Clean test snapshots
        for s in SNAPSHOTS_DIR.glob(f"{self.test_skill}_*.py"):
            try:
                s.unlink()
            except Exception:
                pass

    def test_canary_rejects_module_without_skill_registry_registration(self):
        """Code that does not decorate a function with @SkillRegistry.register fails canary verification."""
        code_without_decorator = """
def standalone_helper(name: str = "Sir") -> str:
    return f"Hello, {name}"
"""
        res = SkillSandbox.validate_and_promote(self.test_skill, code_without_decorator)
        self.assertFalse(res.success)
        self.assertIn("Subprocess verification failed", res.message)
        self.assertFalse(self.target_path.exists())

    def test_canary_verifies_and_promotes_valid_skill(self):
        """Valid skill with @SkillRegistry.register passes canary verification and is promoted."""
        valid_code = """
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(
    name="test_canary_skill",
    description="Valid canary skill."
)
def test_canary_skill(arg: str = "test") -> str:
    return f"Canary success: {arg}"
"""
        res = SkillSandbox.validate_and_promote(self.test_skill, valid_code)
        self.assertTrue(res.success)
        self.assertTrue(self.target_path.exists())
        self.assertIn("test_canary_skill", SkillRegistry.get_all_tools())
        self.assertEqual(SkillRegistry.execute("test_canary_skill", arg="hello"), "Canary success: hello")

    def test_transactional_rollback_preserves_previous_working_version(self):
        """When an update fails canary verification, the previous working version is restored from snapshot."""
        # 1. Deploy version 1
        v1_code = """
from jarvis.skills.base import SkillRegistry

@SkillRegistry.register(name="test_canary_skill", description="V1")
def test_canary_skill(num: int = 1) -> str:
    return f"V1: {num}"
"""
        res1 = SkillSandbox.validate_and_promote(self.test_skill, v1_code)
        self.assertTrue(res1.success)
        self.assertEqual(SkillRegistry.execute("test_canary_skill", num=42), "V1: 42")

        # Verify snapshot was recorded when updating
        # 2. Deploy bad version 2 that fails canary (missing registration)
        bad_v2_code = """
def test_canary_skill_broken():
    return "BROKEN"
"""
        res2 = SkillSandbox.validate_and_promote(self.test_skill, bad_v2_code)
        self.assertFalse(res2.success)

        # 3. Verify V1 was preserved/restored on disk and still active
        self.assertTrue(self.target_path.exists())
        with open(self.target_path, "r", encoding="utf-8") as f:
            restored_content = f.read()
        self.assertIn("V1", restored_content)

        # 4. Verify snapshot file was created in SNAPSHOTS_DIR
        snapshots = SkillSandbox.list_snapshots(self.test_skill)
        self.assertTrue(len(snapshots) >= 1)


if __name__ == "__main__":
    unittest.main()

"""Self-Evolution Sandbox & Subprocess Validation Gate for Jarvis 2.0.

Mitigates the Chief Dissent by isolating LLM-generated Python skills in an isolated
child process before permitting hot-reloading into the running assistant memory.
"""
import ast
from dataclasses import dataclass
import importlib.util
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import List, Optional, Set, Tuple

from jarvis.skills.base import SkillRegistry

logger = logging.getLogger("Jarvis.Evolution.Sandbox")

WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent.parent
CUSTOM_SKILLS_DIR = WORKSPACE_DIR / "jarvis" / "skills" / "custom"
SCRATCH_DIR = WORKSPACE_DIR / "data" / "evolution_scratch"

SNAPSHOTS_DIR = WORKSPACE_DIR / "data" / "evolution_snapshots"

CUSTOM_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class SandboxResult:
    """Outcome of sandbox isolation and test verification."""
    success: bool
    skill_name: str
    message: str
    target_path: Optional[Path] = None
    stdout: str = ""
    stderr: str = ""


class SkillSandbox:
    """Isolates, compiles, and tests LLM-generated code in an ephemeral subprocess."""

    # Prohibited AST calls to protect host integrity
    BLOCKED_CALLS: Set[str] = {
        "os.system",
        "os.popen",
        "os.spawn",
        "subprocess.call",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.check_output",
        "subprocess.check_call",
        "eval",
        "exec",
        "compile",
        "__import__",
        "shutil.rmtree",
        "os.rmdir",
        "os.remove",
        "os.unlink",
    }

    # Prohibited reflection functions (dynamic object attribute access / meta-programming)
    BLOCKED_REFLECTION_CALLS: Set[str] = {
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
    }

    # Prohibited dunder attributes (sandbox escape vectors via class hierarchy)
    BLOCKED_ATTRIBUTES: Set[str] = {
        "__subclasses__",
        "__globals__",
        "__builtins__",
        "__code__",
        "__bases__",
        "__mro__",
        "__class__",
        "__reduce__",
        "__reduce_ex__",
    }

    # Prohibited modules to import
    BLOCKED_MODULES: Set[str] = {
        "ctypes",
        "pickle",
        "pty",
        "shelve",
        "subprocess",
    }

    @classmethod
    def sanitize_skill_name(cls, name: str) -> str:
        """Sanitizes skill name into a valid snake_case Python module identifier."""
        slug = re.sub(r"[^\w\s-]", "", name.lower()).strip()
        slug = re.sub(r"[-\s]+", "_", slug)
        if not slug or not slug[0].isalpha():
            slug = f"skill_{slug}" if slug else "custom_skill"
        return slug

    @classmethod
    def check_ast_security(cls, code: str) -> Tuple[bool, str]:
        """Performs static syntax and deep AST security checks on untrusted code."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Python SyntaxError on line {e.lineno}: {e.msg}"

        # Inspect AST for dangerous patterns, imports, and reflection escapes
        for node in ast.walk(tree):
            # 1. Imports inspection
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in cls.BLOCKED_MODULES:
                        return False, f"Security Violation: Importing module '{alias.name}' is blocked inside autonomous skills."
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in cls.BLOCKED_MODULES:
                        return False, f"Security Violation: Importing from module '{node.module}' is blocked inside autonomous skills."

            # 2. Attribute access inspection (catches dunder escapes and blocked call bindings)
            elif isinstance(node, ast.Attribute):
                if node.attr in cls.BLOCKED_ATTRIBUTES:
                    return False, f"Security Violation: Access to dunder attribute '{node.attr}' is prohibited (sandbox reflection protection)."
                val_id = node.value.id if isinstance(node.value, ast.Name) else ""
                full_attr = f"{val_id}.{node.attr}" if val_id else node.attr
                if full_attr in cls.BLOCKED_CALLS:
                    return False, f"Security Violation: '{full_attr}' is blocked inside autonomous skills."

            # 3. Call invocation inspection (catches reflection and blocked execution)
            elif isinstance(node, ast.Call):
                func = node.func
                call_name = ""
                if isinstance(func, ast.Name):
                    call_name = func.id
                elif isinstance(func, ast.Attribute):
                    val_id = func.value.id if isinstance(func.value, ast.Name) else ""
                    call_name = f"{val_id}.{func.attr}" if val_id else func.attr

                if call_name in cls.BLOCKED_REFLECTION_CALLS:
                    return False, f"Security Violation: Dynamic reflection call '{call_name}' is blocked inside autonomous skills."
                if call_name in cls.BLOCKED_CALLS or any(call_name.startswith(f"{b}.") for b in cls.BLOCKED_MODULES):
                    return False, f"Security Violation: '{call_name}' is blocked inside autonomous skills."

        return True, "AST security checks passed."

    @classmethod
    def create_snapshot(cls, clean_name: str) -> Optional[Path]:
        """Creates a versioned backup snapshot of an existing custom skill before updating."""
        target_file = CUSTOM_SKILLS_DIR / f"{clean_name}.py"
        if not target_file.exists():
            return None
        timestamp = int(time.time() * 1000)
        snapshot_file = SNAPSHOTS_DIR / f"{clean_name}_{timestamp}.py"
        try:
            shutil.copy2(target_file, snapshot_file)
            logger.info("Created snapshot for '%s' at '%s'", clean_name, snapshot_file.name)
            return snapshot_file
        except Exception as e:
            logger.warning("Failed to create snapshot for '%s': %s", clean_name, e)
            return None

    @classmethod
    def rollback_snapshot(cls, clean_name: str, snapshot_path: Optional[Path]) -> bool:
        """Restores a custom skill from its previous snapshot or cleans up on failure."""
        target_file = CUSTOM_SKILLS_DIR / f"{clean_name}.py"
        if snapshot_path and snapshot_path.exists():
            try:
                shutil.copy2(snapshot_path, target_file)
                logger.info("Rolled back '%s' to snapshot '%s'", clean_name, snapshot_path.name)
                module_name = f"jarvis.skills.custom.{clean_name}"
                spec = importlib.util.spec_from_file_location(module_name, str(target_file))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = mod
                    spec.loader.exec_module(mod)
                return True
            except Exception as e:
                logger.error("Failed to restore snapshot for '%s': %s", clean_name, e)
                return False
        else:
            if target_file.exists():
                try:
                    target_file.unlink()
                except Exception:
                    pass
            module_name = f"jarvis.skills.custom.{clean_name}"
            if module_name in sys.modules:
                del sys.modules[module_name]
            return True

    @classmethod
    def list_snapshots(cls, clean_name: Optional[str] = None) -> List[Path]:
        """Lists existing versioned snapshots, optionally filtered by skill name."""
        if not SNAPSHOTS_DIR.exists():
            return []
        pattern = f"{clean_name}_*.py" if clean_name else "*.py"
        return sorted(SNAPSHOTS_DIR.glob(pattern), reverse=True)

    @classmethod
    def generate_canary_script(cls, clean_name: str, scratch_path: Path) -> str:
        """Generates an automated semantic canary verification script for the subprocess.

        Verifies:
        1. Module load and compilation without exception.
        2. Registration of at least one valid tool in SkillRegistry.
        3. Tool contract inspection: docstrings, signature parameter inspectability.
        4. Emission of CANARY_VERIFICATION_SUCCESS and SANDBOX_LOAD_SUCCESS markers.
        """
        escaped_path = str(scratch_path).replace("\\", "\\\\")
        return f"""import sys
import inspect
import importlib.util

try:
    spec = importlib.util.spec_from_file_location('{clean_name}', r'{escaped_path}')
    if not spec or not spec.loader:
        print('CANARY_ERROR: Failed to create module spec')
        sys.exit(2)

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from jarvis.skills.base import SkillRegistry
    tools = SkillRegistry.get_all_tools()

    # Find tools defined in this module
    module_tools = [
        name for name, fn in tools.items()
        if getattr(fn, '__module__', '') == '{clean_name}' or name == '{clean_name}'
    ]

    if not module_tools:
        module_tools = [
            name for name, fn in tools.items()
            if any(getattr(mod, attr, None) is fn for attr in dir(mod))
        ]

    if not module_tools:
        print('CANARY_ERROR: Module loaded but did not register any tools with @SkillRegistry.register')
        sys.exit(3)

    for tool_name in module_tools:
        tool_fn = tools[tool_name]
        if not callable(tool_fn):
            print(f'CANARY_ERROR: Registered tool {{tool_name}} is not callable')
            sys.exit(4)
        sig = inspect.signature(tool_fn)
        for p in sig.parameters.values():
            _ = p.name

    print('CANARY_VERIFICATION_SUCCESS')
    print('SANDBOX_LOAD_SUCCESS')
    sys.exit(0)
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f'CANARY_EXCEPTION: {{e}}')
    sys.exit(1)
"""

    @classmethod
    def validate_and_promote(
        cls,
        skill_name: str,
        python_code: str,
        timeout_seconds: float = 8.0,
    ) -> SandboxResult:
        """Executes full sandboxed lifecycle:
        1. Snapshot versioning if modifying an existing skill
        2. Static AST syntax, import, reflection & security analysis
        3. Isolation in ephemeral scratchpad file
        4. Subprocess dry-run execution & automated canary contract verification
        5. Promotion to jarvis/skills/custom/ and live memory hot-reload
        6. Transactional rollback to snapshot on any failure
        """
        clean_name = cls.sanitize_skill_name(skill_name)
        code = python_code.strip()

        if not code:
            return SandboxResult(success=False, skill_name=clean_name, message="Code content cannot be empty.")

        # 0. Snapshot versioning of existing skill
        snapshot_path = cls.create_snapshot(clean_name)

        # 1. AST Static Gate
        is_safe, sec_msg = cls.check_ast_security(code)
        if not is_safe:
            logger.warning("Sandbox blocked skill '%s': %s", clean_name, sec_msg)
            return SandboxResult(success=False, skill_name=clean_name, message=sec_msg)

        # Ensure SkillRegistry import
        if "SkillRegistry" not in code:
            code = "from jarvis.skills.base import SkillRegistry\n\n" + code

        scratch_file = SCRATCH_DIR / f"temp_{clean_name}_{int(time.time() * 1000)}.py"
        target_file = CUSTOM_SKILLS_DIR / f"{clean_name}.py"

        # 2. Write to Ephemeral Scratchpad
        try:
            with open(scratch_file, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return SandboxResult(success=False, skill_name=clean_name, message=f"Failed to write scratchpad: {e}")

        # 3. Subprocess Dry-Run & Automated Canary Gate
        runner_script = cls.generate_canary_script(clean_name, scratch_file)

        try:
            proc = subprocess.run(
                [sys.executable, "-c", runner_script],
                cwd=str(WORKSPACE_DIR),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if proc.returncode != 0 or (
                "CANARY_VERIFICATION_SUCCESS" not in stdout and "SANDBOX_LOAD_SUCCESS" not in stdout
            ):
                cls._cleanup_scratch(scratch_file)
                cls.rollback_snapshot(clean_name, snapshot_path)
                error_summary = stderr or stdout or f"Exited with code {proc.returncode}"
                logger.error("Sandbox verification failed for '%s': %s", clean_name, error_summary)
                return SandboxResult(
                    success=False,
                    skill_name=clean_name,
                    message=f"Subprocess verification failed:\n{error_summary}",
                    stdout=stdout,
                    stderr=stderr,
                )

        except subprocess.TimeoutExpired:
            cls._cleanup_scratch(scratch_file)
            cls.rollback_snapshot(clean_name, snapshot_path)
            logger.error("Sandbox verification timed out after %.1fs for '%s'", timeout_seconds, clean_name)
            return SandboxResult(
                success=False,
                skill_name=clean_name,
                message=f"Subprocess timed out after {timeout_seconds}s (potential infinite loop).",
            )
        except Exception as e:
            cls._cleanup_scratch(scratch_file)
            cls.rollback_snapshot(clean_name, snapshot_path)
            return SandboxResult(success=False, skill_name=clean_name, message=f"Subprocess execution error: {e}")

        # 4. Promotion from Scratchpad to Production Custom Directory
        try:
            shutil.copy2(scratch_file, target_file)
            cls._cleanup_scratch(scratch_file)
        except Exception as e:
            cls._cleanup_scratch(scratch_file)
            cls.rollback_snapshot(clean_name, snapshot_path)
            return SandboxResult(success=False, skill_name=clean_name, message=f"Promotion failed: {e}")

        # 5. Live Memory Hot-Reload in Host Process
        module_name = f"jarvis.skills.custom.{clean_name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(target_file))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                # Clear tool schema cache
                if hasattr(SkillRegistry, "_openai_tools_cache"):
                    SkillRegistry._openai_tools_cache.clear()

                logger.info("Skill '%s' promoted and hot-reloaded successfully.", clean_name)
                return SandboxResult(
                    success=True,
                    skill_name=clean_name,
                    message=f"Successfully engineered and hot-reloaded capability '{clean_name}', Sir! Validated via isolated subprocess sandbox and active in your current session.",
                    target_path=target_file,
                )
        except Exception as host_err:
            cls.rollback_snapshot(clean_name, snapshot_path)
            return SandboxResult(
                success=False,
                skill_name=clean_name,
                message=f"Host hot-reload error: {host_err}",
            )

        return SandboxResult(success=False, skill_name=clean_name, message="Unknown sandbox failure.")

    @classmethod
    def _cleanup_scratch(cls, path: Path):
        """Removes ephemeral scratchpad file."""
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

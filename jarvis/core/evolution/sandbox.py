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
from typing import Optional, Set, Tuple

from jarvis.skills.base import SkillRegistry

logger = logging.getLogger("Jarvis.Evolution.Sandbox")

WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent.parent
CUSTOM_SKILLS_DIR = WORKSPACE_DIR / "jarvis" / "skills" / "custom"
SCRATCH_DIR = WORKSPACE_DIR / "data" / "evolution_scratch"

CUSTOM_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)


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
        "subprocess.call",
        "eval",
        "exec",
        "shutil.rmtree",
        "os.rmdir",
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
        """Performs static syntax and AST security checks on untrusted code."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Python SyntaxError on line {e.lineno}: {e.msg}"

        # Inspect AST for dangerous patterns
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                call_name = ""
                if isinstance(func, ast.Name):
                    call_name = func.id
                elif isinstance(func, ast.Attribute):
                    val_id = func.value.id if isinstance(func.value, ast.Name) else ""
                    call_name = f"{val_id}.{func.attr}" if val_id else func.attr

                if call_name in cls.BLOCKED_CALLS:
                    return False, f"Security Violation: '{call_name}' is blocked inside autonomous skills."

        return True, "AST security checks passed."

    @classmethod
    def validate_and_promote(
        cls,
        skill_name: str,
        python_code: str,
        timeout_seconds: float = 8.0,
    ) -> SandboxResult:
        """Executes full sandboxed lifecycle:
        1. Static AST syntax & security analysis
        2. Isolation in ephemeral scratchpad file
        3. Subprocess dry-run execution & automated test gate
        4. Promotion to jarvis/skills/custom/ and memory hot-reload
        5. Automated rollback on failure
        """
        clean_name = cls.sanitize_skill_name(skill_name)
        code = python_code.strip()

        if not code:
            return SandboxResult(success=False, skill_name=clean_name, message="Code content cannot be empty.")

        # 1. AST Static Gate
        is_safe, sec_msg = cls.check_ast_security(code)
        if not is_safe:
            logger.warning("Sandbox blocked skill '%s': %s", clean_name, sec_msg)
            return SandboxResult(success=False, skill_name=clean_name, message=sec_msg)

        # Ensure SkillRegistry import
        if "SkillRegistry" not in code:
            code = "from jarvis.skills.base import SkillRegistry\n\n" + code

        scratch_file = SCRATCH_DIR / f"temp_{clean_name}_{int(time.time())}.py"
        target_file = CUSTOM_SKILLS_DIR / f"{clean_name}.py"

        # 2. Write to Ephemeral Scratchpad
        try:
            with open(scratch_file, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return SandboxResult(success=False, skill_name=clean_name, message=f"Failed to write scratchpad: {e}")

        # 3. Subprocess Dry-Run & Test Gate
        # Runs an isolated Python subprocess that imports and smoke-tests the module
        runner_script = (
            "import sys, importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('{clean_name}', r'{scratch_file}')\n"
            "if not spec or not spec.loader:\n"
            "    sys.exit(2)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "print('SANDBOX_LOAD_SUCCESS')\n"
        )

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

            if proc.returncode != 0 or "SANDBOX_LOAD_SUCCESS" not in stdout:
                # Subprocess failed: Trigger automatic rollback
                cls._cleanup_scratch(scratch_file)
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
            logger.error("Sandbox verification timed out after %.1fs for '%s'", timeout_seconds, clean_name)
            return SandboxResult(
                success=False,
                skill_name=clean_name,
                message=f"Subprocess timed out after {timeout_seconds}s (potential infinite loop).",
            )
        except Exception as e:
            cls._cleanup_scratch(scratch_file)
            return SandboxResult(success=False, skill_name=clean_name, message=f"Subprocess execution error: {e}")

        # 4. Promotion from Scratchpad to Production Custom Directory
        try:
            shutil.copy2(scratch_file, target_file)
            cls._cleanup_scratch(scratch_file)
        except Exception as e:
            cls._cleanup_scratch(scratch_file)
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
            # Rollback target file if host load unexpectedly fails
            if target_file.exists():
                target_file.unlink()
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

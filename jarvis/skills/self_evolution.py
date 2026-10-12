"""Autonomous Self-Evolution & Dynamic Skill Creation Engine for Jarvis 2.0.
Enables Jarvis to write, validate, register, and hot-reload new Python skills
into his own running brain at runtime using Google AI Pro models.
"""
import ast
import importlib.util
import logging
import os
from pathlib import Path
import re
import sys
from typing import List, Optional

from jarvis.skills.base import SkillRegistry

logger = logging.getLogger("Jarvis.SelfEvolution")

CUSTOM_SKILLS_DIR = Path(__file__).resolve().parent / "custom"
CUSTOM_SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_skill_name(name: str) -> str:
    """Sanitizes skill name into a valid snake_case Python module identifier."""
    slug = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    slug = re.sub(r"[-\s]+", "_", slug)
    if not slug or not slug[0].isalpha():
        slug = f"skill_{slug}" if slug else "custom_skill"
    return slug


@SkillRegistry.register(
    name="create_or_update_custom_skill",
    description=(
        "Autonomous Self-Building: Creates, validates, and hot-reloads a new Python capability or skill "
        "into Jarvis's live brain without requiring a restart. The python_code must define function(s) "
        "decorated with @SkillRegistry.register(name='...', description='...')."
    ),
)
def create_or_update_custom_skill(
    skill_name: str,
    python_code: str,
    description: str = "",
) -> str:
    """Validates, writes, and hot-reloads a custom Python skill into Jarvis's running process."""
    clean_name = _sanitize_skill_name(skill_name)
    code = python_code.strip()

    if not code:
        return "Error: python_code cannot be empty."

    # 1. Pre-execution AST Syntax Validation
    try:
        ast.parse(code)
    except SyntaxError as e:
        logger.error("Syntax error in generated skill '%s': %s", clean_name, e)
        return (
            f"Failed to create skill '{clean_name}' due to a Python SyntaxError on line {e.lineno}:\n"
            f"{e.msg}\n\nPlease revise the code and try again."
        )

    # 2. Ensure SkillRegistry import is present if not already in code
    if "SkillRegistry" not in code:
        code = "from jarvis.skills.base import SkillRegistry\n\n" + code

    target_file = CUSTOM_SKILLS_DIR / f"{clean_name}.py"

    # Track tools registered before loading
    tools_before = set(SkillRegistry.get_all_tools().keys())

    # 3. Write validated code to jarvis/skills/custom/<clean_name>.py
    try:
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        logger.error("Failed to write custom skill file %s: %s", target_file, e)
        return f"Error writing custom skill to disk: {e}"

    # 4. Dynamic Import & Hot-Reload via importlib
    module_name = f"jarvis.skills.custom.{clean_name}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(target_file))
        if spec is None or spec.loader is None:
            return f"Error creating import specification for {target_file}."

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Clear tool schema cache so new tools are immediately visible to LLMs
        if hasattr(SkillRegistry, "_openai_tools_cache"):
            SkillRegistry._openai_tools_cache.clear()

        tools_after = set(SkillRegistry.get_all_tools().keys())
        newly_registered = list(tools_after - tools_before)

        logger.info(
            "Autonomous Skill Engineered: '%s' hot-reloaded successfully. New tools: %s",
            clean_name,
            newly_registered,
        )

        tools_info = (
            f"Registered new tool(s): {', '.join(newly_registered)}."
            if newly_registered
            else "Skill module loaded successfully (no new @SkillRegistry.register decorators detected)."
        )
        return (
            f"Successfully engineered and hot-reloaded capability '{clean_name}', Sir! "
            f"{tools_info} It is immediately active in your current session."
        )

    except Exception as e:
        logger.error("Dynamic import error for skill '%s': %s", clean_name, e)
        # Attempt to remove the failing file so it doesn't break future launches
        try:
            if target_file.exists():
                target_file.unlink()
        except Exception:
            pass
        return (
            f"Failed to hot-reload skill '{clean_name}' due to runtime execution error:\n"
            f"{type(e).__name__}: {e}\n\nPlease diagnose the issue and try again."
        )


@SkillRegistry.register(
    name="list_custom_skills",
    description="Lists all dynamically engineered custom skills created by Jarvis.",
)
def list_custom_skills() -> str:
    """Lists all dynamically created custom skill modules."""
    if not CUSTOM_SKILLS_DIR.exists():
        return "No custom skills directory found."

    skills = [
        f.stem
        for f in CUSTOM_SKILLS_DIR.glob("*.py")
        if f.stem != "__init__" and not f.stem.startswith(".")
    ]

    if not skills:
        return "No custom skills have been dynamically created yet, Sir."

    lines = [f"- {s} (`jarvis/skills/custom/{s}.py`)" for s in sorted(skills)]
    return f"Active Custom Autonomous Skills ({len(skills)}):\n" + "\n".join(lines)


@SkillRegistry.register(
    name="delete_custom_skill",
    description="Removes a dynamically engineered custom skill from Jarvis's brain and filesystem.",
)
def delete_custom_skill(skill_name: str) -> str:
    """Deletes a custom skill module file and unloads it."""
    clean_name = _sanitize_skill_name(skill_name)
    target_file = CUSTOM_SKILLS_DIR / f"{clean_name}.py"

    if not target_file.exists():
        return f"Custom skill '{clean_name}' does not exist."

    try:
        target_file.unlink()
        module_name = f"jarvis.skills.custom.{clean_name}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        # Invalidate schema cache
        if hasattr(SkillRegistry, "_openai_tools_cache"):
            SkillRegistry._openai_tools_cache.clear()

        return f"Successfully deleted custom skill '{clean_name}', Sir."
    except Exception as e:
        return f"Error deleting custom skill '{clean_name}': {e}"


def load_all_custom_skills() -> List[str]:
    """Scans jarvis/skills/custom/ and imports all custom skill modules on startup."""
    loaded = []
    if not CUSTOM_SKILLS_DIR.exists():
        return loaded

    for file_path in CUSTOM_SKILLS_DIR.glob("*.py"):
        if file_path.stem == "__init__" or file_path.stem.startswith("."):
            continue

        module_name = f"jarvis.skills.custom.{file_path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                loaded.append(file_path.stem)
                logger.info("Loaded custom autonomous skill: %s", file_path.stem)
        except Exception as e:
            logger.warning("Failed to auto-load custom skill %s on startup: %s", file_path.name, e)

    return loaded


# Automatically load existing custom skills when this module is imported
load_all_custom_skills()

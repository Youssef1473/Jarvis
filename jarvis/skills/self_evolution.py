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
    """Validates, writes, and hot-reloads a custom Python skill via the isolated SkillSandbox."""
    from jarvis.core.evolution.sandbox import SkillSandbox
    result = SkillSandbox.validate_and_promote(skill_name=skill_name, python_code=python_code)
    return result.message


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

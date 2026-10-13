"""Autonomous Self-Evolution & Dynamic Skill Creation Engine for Jarvis 2.0.
Enables Jarvis to write, validate, register, and hot-reload new Python skills
into his own running brain at runtime using Google AI Pro models.
"""
import ast
import importlib.util
import json
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

_reflex_engine_instance = None


def set_reflex_engine(engine):
    """Binds the active DynamicReflexEngine to the self-evolution inspection skills."""
    global _reflex_engine_instance
    _reflex_engine_instance = engine


def _get_reflex_engine():
    global _reflex_engine_instance
    if _reflex_engine_instance is None:
        from jarvis.core.reflex_engine import DynamicReflexEngine
        _reflex_engine_instance = DynamicReflexEngine()
    return _reflex_engine_instance


@SkillRegistry.register(
    name="verify_learned_habits",
    description=(
        "Audits and inspects all dynamically learned reflexes and habits. "
        "Verifies adaptation speed, comparing Turn 1 cloud reasoning latency vs Turn 2 instant local reflex execution."
    ),
)
def verify_learned_habits() -> str:
    """Returns an auditable verification report of all habits Jarvis has learned."""
    engine = _get_reflex_engine()
    habits = engine.list_learned_habits()
    if not habits:
        return (
            "I have not yet cataloged any dynamic habits, Sir. Whenever you ask me to perform a new action, "
            "I will learn the sequence and promote it to instant reflex memory for subsequent turns."
        )

    lines = [f"### Dynamic Reflex & Habit Audit ({len(habits)} Learned Behaviors)\n"]
    for i, h in enumerate(habits, start=1):
        phrase = h["trigger_phrase"]
        skill = h["skill_name"]
        kwargs = h["skill_kwargs"]
        hits = h["hit_count"]
        t1 = h["turn1_latency_ms"]
        refl = h["reflex_latency_ms"]
        refl_str = f"{refl:.1f}ms" if refl > 0 else "<15ms"
        lines.append(
            f"**{i}. \"{phrase}\"**\n"
            f"   • Action: `{skill}({json.dumps(kwargs)})`\n"
            f"   • Hit Count: {hits} times\n"
            f"   • Adaptation Benchmark: Turn 1 ({t1:.0f}ms cloud reasoning) ➔ Turn 2 ({refl_str} local reflex)"
        )

    return "\n\n".join(lines)

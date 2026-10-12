"""Productivity, reminders, notes, and long-term memory tools for Jarvis."""
import datetime
from jarvis.skills.base import SkillRegistry
from jarvis.core.memory import MemoryManager
from jarvis.core.config_loader import load_config

_memory_instance: MemoryManager | None = None


def set_memory_manager(mgr: MemoryManager):
    """Sets the shared singleton MemoryManager instance."""
    global _memory_instance
    _memory_instance = mgr


def _get_memory() -> MemoryManager:
    global _memory_instance
    if _memory_instance is None:
        cfg = load_config()
        _memory_instance = MemoryManager(cfg)
    return _memory_instance


@SkillRegistry.register(
    name="get_current_time_and_date",
    description="Returns the exact current local date, day of the week, and time."
)
def get_current_time_and_date() -> str:
    """Returns formatted current timestamp."""
    now = datetime.datetime.now()
    return now.strftime("Today is %A, %B %d, %Y. The current time is %I:%M:%S %p.")


@SkillRegistry.register(
    name="add_task_or_reminder",
    description="Adds a new task, reminder, or to-do item to the persistent task list."
)
def add_task_or_reminder(description: str, due_date: str | None = None) -> str:
    """Stores a task in SQLite."""
    mem = _get_memory()
    return mem.add_task(description, due_date)


@SkillRegistry.register(
    name="list_pending_tasks",
    description="Retrieves all currently pending tasks and reminders."
)
def list_pending_tasks() -> str:
    """Lists incomplete tasks."""
    mem = _get_memory()
    tasks = mem.get_pending_tasks()
    if not tasks:
        return "You have no pending tasks, Sir."
    formatted = [f"#{t['id']}: {t['description']}" + (f" (Due: {t['due_date']})" if t['due_date'] else "") for t in tasks]
    return "\n".join(formatted)


@SkillRegistry.register(
    name="complete_task",
    description="Marks a task as completed by its numerical ID."
)
def complete_task(task_id: int) -> str:
    """Completes a task."""
    mem = _get_memory()
    return mem.complete_task(task_id)


@SkillRegistry.register(
    name="remember_fact",
    description="Stores a permanent personal fact, preference, or piece of knowledge about the user or projects in long-term memory."
)
def remember_fact(key: str, value: str, category: str = "general") -> str:
    """Stores a fact in SQLite."""
    mem = _get_memory()
    return mem.remember_fact(key, value, category)


@SkillRegistry.register(
    name="recall_fact",
    description="Recalls a stored fact, user preference, or personal knowledge by key or semantic topic description."
)
def recall_fact(key: str) -> str:
    """Recalls a stored fact using exact key match with semantic vector fallback."""
    mem = _get_memory()
    val = mem.get_fact(key)
    if val:
        return f"Stored fact for '{key}': {val}"

    # Semantic vector recall fallback
    matches = mem.semantic_search_facts(key, top_k=3, min_score=0.12)
    if matches:
        formatted = []
        for m in matches:
            formatted.append(f"• [{m['category']}] {m['key']}: {m['value']} (Relevance: {m['relevance_score']:.0%})")
        return f"Semantic memory recall for '{key}':\n" + "\n".join(formatted)

    return f"No stored fact or semantic match found for '{key}'."


@SkillRegistry.register(
    name="recall_past_conversation",
    description="Searches previous conversation logs and dialogues with Sir by topic, question, or keyword. Use this whenever Sir asks what you previously discussed, past decisions, earlier commands, or past topics."
)
def recall_past_conversation(query: str, limit: int = 5) -> str:
    """Searches past conversation logs in SQLite memory."""
    mem = _get_memory()
    results = mem.search_interactions(query, limit=limit)
    if not results:
        # Fallback to semantic vector dialogue search
        results = mem.semantic_search_interactions(query, top_k=limit, min_score=0.10)
    if not results:
        return f"No past conversation logs found matching '{query}', Sir."

    formatted = []
    for r in results:
        ts = r.get("created_at", "Unknown time")
        u_msg = r.get("user_input", "")
        j_msg = r.get("jarvis_reply", "")
        score_str = f" [Match: {r['relevance_score']:.0%}]" if "relevance_score" in r else ""
        formatted.append(f"[{ts}]{score_str}\nSir: {u_msg}\nJarvis: {j_msg}")

    return "Past Dialogue Logs Found:\n\n" + "\n\n---\n\n".join(formatted)


@SkillRegistry.register(
    name="semantic_search_memory",
    description="Searches long-term factual memory and conversation archives using semantic vector similarity."
)
def semantic_search_memory(query: str, search_type: str = "all") -> str:
    """Performs semantic vector search across facts and past conversations."""
    mem = _get_memory()
    outputs = []

    if search_type in ("all", "facts"):
        facts = mem.semantic_search_facts(query, top_k=3)
        if facts:
            f_lines = [f"• [{f['category']}] {f['key']}: {f['value']} (Score: {f['relevance_score']:.0%})" for f in facts]
            outputs.append("Matching Facts & Knowledge:\n" + "\n".join(f_lines))

    if search_type in ("all", "conversations", "dialogue"):
        dialogues = mem.semantic_search_interactions(query, top_k=3)
        if dialogues:
            d_lines = []
            for d in dialogues:
                d_lines.append(f"[{d.get('created_at', 'Past')}] (Relevance: {d['relevance_score']:.0%})\nSir: {d['user_input']}\nJarvis: {d['jarvis_reply']}")
            outputs.append("Matching Conversation Logs:\n" + "\n---\n".join(d_lines))

    if not outputs:
        return f"No semantic memories or dialogues found matching '{query}', Sir."
    return "\n\n".join(outputs)


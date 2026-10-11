"""Calendar, schedule management, and Stark executive morning briefing protocol."""
import datetime
from typing import Dict, Any, List
from jarvis.skills.base import SkillRegistry
from jarvis.core.memory import MemoryManager
from jarvis.core.config_loader import load_config
from jarvis.skills.web_search import get_weather

_memory_instance = None


def _get_memory() -> MemoryManager:
    global _memory_instance
    if _memory_instance is None:
        cfg = load_config()
        _memory_instance = MemoryManager(cfg)
    return _memory_instance


@SkillRegistry.register(
    name="schedule_calendar_event",
    description="Schedules a meeting, appointment, or calendar event with title, date/time, and optional location."
)
def schedule_calendar_event(title: str, start_time: str, end_time: str = "", location: str = "") -> str:
    """Adds a calendar event to the schedule."""
    mem = _get_memory()
    return mem.add_calendar_event(title, start_time, end_time or None, location or None)


@SkillRegistry.register(
    name="list_calendar_events",
    description="Lists all upcoming scheduled meetings and calendar appointments."
)
def list_calendar_events(include_all: bool = False) -> str:
    """Returns scheduled events."""
    mem = _get_memory()
    events = mem.get_calendar_events(include_cancelled=include_all)
    if not events:
        return "You have no scheduled meetings or appointments on your calendar, Sir."

    formatted = []
    for e in events:
        loc = f" at {e['location']}" if e.get("location") else ""
        end = f" to {e['end_time']}" if e.get("end_time") else ""
        formatted.append(f"#{e['id']}: '{e['title']}' scheduled for {e['start_time']}{end}{loc}")
    return "\n".join(formatted)


@SkillRegistry.register(
    name="cancel_calendar_event",
    description="Cancels a scheduled meeting or calendar event by its numerical ID."
)
def cancel_calendar_event(event_id: int) -> str:
    """Cancels a scheduled event."""
    mem = _get_memory()
    return mem.cancel_calendar_event(event_id)


@SkillRegistry.register(
    name="morning_protocol",
    description="Executes the full executive morning briefing protocol: date/time, weather report, today's schedule, pending tasks, and global headlines."
)
def morning_protocol(city: str = "") -> str:
    """Executes Jarvis executive morning briefing."""
    mem = _get_memory()
    now = datetime.datetime.now()
    greeting_time = now.strftime("%A, %B %d, %Y, %I:%M %p")

    # Determine city: check passed arg, or saved preference, or default London
    target_city = city.strip() if city else (mem.get_fact("city") or "London")

    # 1. Weather
    try:
        weather_info = get_weather(target_city)
    except Exception:
        weather_info = "Weather telemetry unavailable."

    # 2. Schedule
    events = mem.get_calendar_events()
    if events:
        schedule_summary = f"You have {len(events)} item(s) on your schedule today: " + "; ".join(
            [f"'{e['title']}' at {e['start_time']}" for e in events[:3]]
        )
    else:
        schedule_summary = "Your calendar is completely clear today, Sir."

    # 3. Tasks
    tasks = mem.get_pending_tasks()
    if tasks:
        task_summary = f"You have {len(tasks)} pending task(s), including: " + "; ".join(
            [t["description"] for t in tasks[:3]]
        )
    else:
        task_summary = "All pending action items are up to date."

    # 4. Email check (if configured)
    email_section = ""
    try:
        from jarvis.skills.email_manager import check_unread_emails
        email_info = check_unread_emails()
        if "Neither Gmail nor Outlook" not in email_info:
            email_section = f"\nInbox Telemetry: {email_info}"
    except Exception:
        pass

    briefing = (
        f"Good morning, Sir. Today is {greeting_time}.\n"
        f"Atmospheric conditions in {target_city}: {weather_info}\n"
        f"Agenda: {schedule_summary}\n"
        f"Action Items: {task_summary}"
        f"{email_section}\n"
        f"All core systems and telemetry remain at optimal efficiency. Standing by for your instructions."
    )
    return briefing

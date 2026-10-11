"""Long-term persistent memory management using SQLite for Jarvis 2.0."""
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
import datetime
import logging

logger = logging.getLogger("Jarvis.Memory")


class MemoryManager:
    """Stores user preferences, facts, tasks, and conversation history in SQLite."""

    def __init__(self, config: Dict[str, Any]):
        base_dir = Path(config.get("_base_dir", "."))
        db_rel_path = config.get("memory", {}).get("db_path", "data/jarvis_memory.db")
        self.db_path = base_dir / db_rel_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Facts & User Profile
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT DEFAULT 'general',
                    key TEXT UNIQUE,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Tasks & Reminders
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    due_date TEXT,
                    is_completed BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Interaction History
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_input TEXT NOT NULL,
                    jarvis_reply TEXT NOT NULL,
                    tool_calls TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Calendar Events
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    location TEXT,
                    is_cancelled BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    # Fact & Preference Management
    def remember_fact(self, key: str, value: str, category: str = "general") -> str:
        """Stores or updates a persistent fact about the user or system."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO facts (category, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    updated_at = CURRENT_TIMESTAMP
            """, (category, key.lower().strip(), value.strip()))
            conn.commit()
        return f"Remembered: '{key}' is '{value}'."

    def get_fact(self, key: str) -> Optional[str]:
        """Retrieves a specific stored fact."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM facts WHERE key = ?", (key.lower().strip(),))
            row = cursor.fetchone()
            return row["value"] if row else None

    def get_all_facts(self) -> List[Dict[str, str]]:
        """Retrieves all stored facts."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT category, key, value FROM facts ORDER BY category, key")
            return [dict(row) for row in cursor.fetchall()]

    # Tasks & Reminders
    def add_task(self, description: str, due_date: Optional[str] = None) -> str:
        """Adds a task or reminder."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tasks (description, due_date, is_completed)
                VALUES (?, ?, 0)
            """, (description.strip(), due_date))
            conn.commit()
            task_id = cursor.lastrowid
        return f"Task #{task_id} added: {description}"

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Retrieves all incomplete tasks."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, description, due_date, created_at FROM tasks WHERE is_completed = 0")
            return [dict(row) for row in cursor.fetchall()]

    def complete_task(self, task_id: int) -> str:
        """Marks a task as completed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET is_completed = 1 WHERE id = ?", (task_id,))
            conn.commit()
            if cursor.rowcount > 0:
                return f"Task #{task_id} marked as completed."
            return f"Task #{task_id} not found."

    # Calendar Events
    def add_calendar_event(self, title: str, start_time: str, end_time: Optional[str] = None, location: Optional[str] = None) -> str:
        """Adds a calendar event."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO calendar_events (title, start_time, end_time, location, is_cancelled)
                VALUES (?, ?, ?, ?, 0)
            """, (title.strip(), start_time.strip(), end_time, location))
            conn.commit()
            event_id = cursor.lastrowid
        return f"Event #{event_id} scheduled: '{title}' at {start_time}" + (f" ({location})" if location else "") + "."

    def get_calendar_events(self, include_cancelled: bool = False) -> List[Dict[str, Any]]:
        """Retrieves active calendar events."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT id, title, start_time, end_time, location FROM calendar_events WHERE is_cancelled = 0 ORDER BY start_time"
            if include_cancelled:
                query = "SELECT id, title, start_time, end_time, location, is_cancelled FROM calendar_events ORDER BY start_time"
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def cancel_calendar_event(self, event_id: int) -> str:
        """Marks a calendar event as cancelled."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE calendar_events SET is_cancelled = 1 WHERE id = ?", (event_id,))
            conn.commit()
            if cursor.rowcount > 0:
                return f"Event #{event_id} has been cancelled, Sir."
            return f"Event #{event_id} was not found."

    # Interaction History
    def log_interaction(self, user_input: str, jarvis_reply: str, tool_calls: Optional[str] = None):
        """Logs a conversation turn."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO interactions (user_input, jarvis_reply, tool_calls)
                VALUES (?, ?, ?)
            """, (user_input, jarvis_reply, tool_calls or ""))
            conn.commit()

    def get_context_summary(self) -> str:
        """Generates a brief summary of remembered facts for the LLM prompt."""
        facts = self.get_all_facts()
        if not facts:
            return "No prior facts recorded."
        lines = [f"- {f['key']}: {f['value']}" for f in facts]
        return "\n".join(lines)

"""Long-term persistent memory management and adaptive self-learning for Jarvis 2.0."""
import datetime
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Dict, Any, List, Optional

logger = logging.getLogger("Jarvis.Memory")


class MemoryManager:
    """Stores user preferences, facts, tasks, and conversation history in SQLite with adaptive learning."""

    def __init__(self, config: Dict[str, Any]):
        self.llm_config = config.get("llm", {})
        memory_config = config.get("memory", {})
        self.background_learning_enabled = bool(memory_config.get("background_learning", False))
        self._learning_lock = threading.Lock()
        self._learning_in_flight = False
        self._context_summary_lock = threading.Lock()
        self._context_summary_cache: Optional[str] = None
        base_dir = Path(config.get("_base_dir", "."))
        db_rel_path = memory_config.get("db_path", "data/jarvis_memory.db")
        self.db_path = base_dir / db_rel_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
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
            """, (category.lower().strip(), key.lower().strip(), value.strip()))
            conn.commit()
        with self._context_summary_lock:
            self._context_summary_cache = None
        return f"Remembered: '{key}' is '{value}'."

    def get_fact(self, key: str) -> Optional[str]:
        """Retrieves a specific stored fact."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM facts WHERE key = ?", (key.lower().strip(),))
            row = cursor.fetchone()
            return row["value"] if row else None

    def search_facts(self, query: str) -> List[Dict[str, str]]:
        """Searches remembered facts matching a query keyword."""
        q = f"%{query.lower().strip()}%"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT category, key, value FROM facts
                WHERE key LIKE ? OR value LIKE ? OR category LIKE ?
                ORDER BY category, key
            """, (q, q, q))
            return [dict(row) for row in cursor.fetchall()]

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

    # Interaction History & Autonomous Self-Learning
    def log_interaction(self, user_input: str, jarvis_reply: str, tool_calls: Optional[str] = None):
        """Logs a conversation turn."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO interactions (user_input, jarvis_reply, tool_calls)
                VALUES (?, ?, ?)
            """, (user_input, jarvis_reply, tool_calls or ""))
            conn.commit()

    def get_recent_interactions(self, limit: int = 8) -> List[Dict[str, Any]]:
        """Retrieves the most recent N interaction turns in chronological order."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_input, jarvis_reply, tool_calls, created_at
                FROM interactions
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in reversed(rows)]

    def search_interactions(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches past conversation logs by keyword or phrase across questions and answers."""
        terms = [t.strip().lower() for t in query.split() if len(t.strip()) > 2]
        if not terms:
            terms = [query.strip().lower()]

        conditions = []
        params = []
        for term in terms:
            conditions.append("(LOWER(user_input) LIKE ? OR LOWER(jarvis_reply) LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])

        where_clause = " OR ".join(conditions)
        sql = f"""
            SELECT id, user_input, jarvis_reply, tool_calls, created_at
            FROM interactions
            WHERE {where_clause}
            ORDER BY id DESC
            LIMIT ?
        """
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            return [dict(r) for r in cursor.fetchall()]

    def auto_learn_from_interaction(
        self,
        user_text: str,
        reply_text: str,
        groq_api_key: Optional[str] = None,
        provider: str = "groq",
    ):
        """Asynchronously analyzes user utterances to extract and persist persistent preferences, facts, and habits."""
        cleaned = user_text.strip().lower()
        if not self.background_learning_enabled:
            return
        # Fast filter for greetings, one-word queries, and short commands
        trivial = {"hey jarvis", "exit", "quit", "goodbye", "help", "who are you", "yes", "no", "ok", "okay", "cancel", "thanks", "thank you"}
        if len(cleaned) < 8 or cleaned in trivial:
            return

        # A second local generation competes directly with the live reply on a
        # small GPU. When enabled, serialize learning so turns never pile up.
        with self._learning_lock:
            if self._learning_in_flight:
                return
            self._learning_in_flight = True

        def _extract():
            system_prompt = (
                "You are an ambient background memory extractor for Sir's personal assistant JARVIS. "
                "Analyze the user's utterance and determine if Sir revealed any permanent personal facts, "
                "preferences, habits, routine details, projects, or behavioral rules about himself. "
                "If so, output a JSON array of objects with keys: 'category' (identity|preference|habit|project|general), "
                "'key' (snake_case, concise), 'value' (clear description). "
                "If no persistent personal facts about Sir were shared (e.g. routine queries, system commands, transient questions), "
                "output ONLY an empty array: []\n"
                "Output valid JSON without markdown fences."
            )

            if provider == "ollama":
                api_key = "ollama"
                base_url = self.llm_config.get("ollama_base_url", "http://127.0.0.1:11434/v1")
                model = self.llm_config.get("ollama_model", "qwen3.5:2b-q4_K_M")
            elif provider == "groq":
                api_key = groq_api_key or os.getenv("GROQ_API_KEY", "").strip()
                base_url = "https://api.groq.com/openai/v1"
                model = self.llm_config.get("groq_model", "qwen/qwen3.8-27b")
            else:
                # Memory extraction must never turn local mode into a cloud call.
                return
            if not api_key:
                return

            try:
                import time
                if provider == "groq":
                    time.sleep(1.0)  # Stagger slightly to avoid burst contention with speech
                from openai import OpenAI
                client = OpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    max_retries=0,
                    timeout=90.0 if provider == "ollama" else 8.0,
                )
                res = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"User statement: '{user_text}'"}
                    ],
                    max_tokens=200,
                    temperature=0.0,
                    **({"reasoning_effort": "none"} if provider == "ollama" else {}),
                )

                content = res.choices[0].message.content.strip()
                if content.startswith("```"):
                    content = re.sub(r"^```(?:json)?\n?", "", content)
                    content = re.sub(r"\n?```$", "", content)

                items = json.loads(content)
                if isinstance(items, list):
                    for item in items:
                        key = item.get("key", "").strip()
                        val = item.get("value", "").strip()
                        cat = item.get("category", "general").strip()
                        if key and val:
                            self.remember_fact(key, val, cat)
                            logger.info(f"Learned new fact about Sir: [{cat}] {key} -> {val}")
            except Exception as e:
                logger.debug("%s background memory extraction notice: %s", provider, e)
            finally:
                with self._learning_lock:
                    self._learning_in_flight = False

        threading.Thread(target=_extract, daemon=True).start()

    def get_context_summary(self) -> str:
        """Generates a structured, categorized summary of remembered facts for the LLM prompt."""
        with self._context_summary_lock:
            if self._context_summary_cache is not None:
                return self._context_summary_cache

        facts = self.get_all_facts()
        if not facts:
            summary = "No prior facts recorded."
            with self._context_summary_lock:
                self._context_summary_cache = summary
            return summary

        by_cat = {}
        for f in facts:
            cat = f.get("category", "general").capitalize()
            by_cat.setdefault(cat, []).append(f"{f['key'].replace('_', ' ').capitalize()}: {f['value']}")

        sections = []
        for cat, items in sorted(by_cat.items()):
            sections.append(f"[{cat}]\n" + "\n".join(f"• {item}" for item in items))
        summary = "\n\n".join(sections)
        with self._context_summary_lock:
            self._context_summary_cache = summary
        return summary

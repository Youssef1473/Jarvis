"""Long-term persistent memory management and adaptive self-learning for Jarvis 2.0."""
import datetime
import json
import logging
import math
import os
from collections import Counter
from pathlib import Path
import re
import sqlite3
import threading
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("Jarvis.Memory")


class VectorMemoryEngine:
    """Local vector recall engine using TF-IDF subword and token n-gram vectors with cosine similarity.
    Runs 100% locally with zero external network or model download dependencies in <1ms."""

    SEMANTIC_SYNONYMS = {
        "editor": ["ide", "vscode", "code", "programming", "sublime", "pycharm", "cursor", "neovim", "vim"],
        "ide": ["editor", "vscode", "code", "development", "programming"],
        "code": ["programming", "script", "developer", "ide", "editor", "software"],
        "favorite": ["preferred", "best", "liked", "fav", "top", "choice"],
        "preference": ["favorite", "preferred", "setting", "likes", "choice"],
        "music": ["song", "audio", "spotify", "playlist", "track", "tunes", "artist"],
        "work": ["job", "project", "career", "task", "company", "office", "business"],
        "home": ["house", "apartment", "residence", "address", "location"],
        "car": ["vehicle", "automobile", "drive", "transport"],
        "drink": ["coffee", "tea", "water", "beverage"],
        "food": ["meal", "lunch", "dinner", "breakfast", "snack"],
        "os": ["operating system", "windows", "linux", "mac", "wsl"],
        "city": ["town", "location", "country", "place", "residence"],
        "watchdog": ["sentinel", "monitor", "telemetry", "hardware", "vitals"],
        "note": ["notes", "memo", "reminder", "scratchpad", "jot"],
    }

    def tokenize(self, text: str) -> List[str]:
        """Extracts word tokens, semantic aliases, and subword n-grams."""
        if not text:
            return []
        cleaned = re.sub(r"[^\w\s]", " ", text.lower())
        words = [w for w in cleaned.split() if len(w) > 1]

        tokens = list(words)
        for w in words:
            syns = self.SEMANTIC_SYNONYMS.get(w, [])
            tokens.extend(syns)

        # Add character 3-grams and 4-grams for root matching and typo resilience
        for w in words:
            if len(w) >= 3:
                for i in range(len(w) - 2):
                    tokens.append(f"#{w[i:i+3]}")
            if len(w) >= 4:
                for i in range(len(w) - 3):
                    tokens.append(f"##{w[i:i+4]}")

        return tokens

    def vectorize(self, text: str, idf_weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """Calculates L2-normalized TF-IDF vector for a given text."""
        tokens = self.tokenize(text)
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = len(tokens)

        vec = {}
        for token, count in counts.items():
            tf = count / total
            idf = idf_weights.get(token, 1.0) if idf_weights else 1.0
            vec[token] = tf * idf

        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            for k in vec:
                vec[k] /= norm
        return vec

    def compute_idf(self, corpus: List[str]) -> Dict[str, float]:
        """Computes inverse document frequency across the corpus."""
        n_docs = len(corpus)
        if n_docs == 0:
            return {}
        doc_freq = Counter()
        for doc in corpus:
            unique_tokens = set(self.tokenize(doc))
            for tok in unique_tokens:
                doc_freq[tok] += 1

        idf = {}
        for tok, df in doc_freq.items():
            idf[tok] = math.log((1 + n_docs) / (1 + df)) + 1.0
        return idf

    @staticmethod
    def cosine_similarity(vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """Computes cosine similarity between two unit-normalized sparse vectors."""
        if not vec1 or not vec2:
            return 0.0
        if len(vec1) > len(vec2):
            vec1, vec2 = vec2, vec1
        return sum(val * vec2.get(k, 0.0) for k, val in vec1.items())


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
        self.vector_engine = VectorMemoryEngine()
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

    def semantic_search_facts(self, query: str, top_k: int = 5, min_score: float = 0.12) -> List[Dict[str, Any]]:
        """Finds stored facts semantically relevant to query using cosine similarity vectors."""
        facts = self.get_all_facts()
        if not facts:
            return []

        corpus = [f"{f.get('category', '')} {f.get('key', '')} {f.get('value', '')}" for f in facts]
        idf = self.vector_engine.compute_idf(corpus)
        query_vec = self.vector_engine.vectorize(query, idf)
        if not query_vec:
            return []

        scored = []
        for f, doc_text in zip(facts, corpus):
            doc_vec = self.vector_engine.vectorize(doc_text, idf)
            score = self.vector_engine.cosine_similarity(query_vec, doc_vec)
            if score >= min_score:
                f_dict = dict(f)
                f_dict["relevance_score"] = round(score, 3)
                scored.append(f_dict)

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored[:top_k]

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

    def semantic_search_interactions(self, query: str, top_k: int = 5, min_score: float = 0.10) -> List[Dict[str, Any]]:
        """Searches past conversation history using semantic vector similarity."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, user_input, jarvis_reply, tool_calls, created_at
                FROM interactions
                ORDER BY id DESC
                LIMIT 150
            """)
            rows = [dict(r) for r in cursor.fetchall()]

        if not rows:
            return []

        corpus = [f"{r['user_input']} {r['jarvis_reply']}" for r in rows]
        idf = self.vector_engine.compute_idf(corpus)
        query_vec = self.vector_engine.vectorize(query, idf)
        if not query_vec:
            return []

        scored = []
        for r, doc_text in zip(rows, corpus):
            doc_vec = self.vector_engine.vectorize(doc_text, idf)
            score = self.vector_engine.cosine_similarity(query_vec, doc_vec)
            if score >= min_score:
                r_dict = dict(r)
                r_dict["relevance_score"] = round(score, 3)
                scored.append(r_dict)

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored[:top_k]

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

    def get_context_summary(self, query: Optional[str] = None) -> str:
        """Generates a structured, categorized summary of remembered facts for the LLM prompt.
        If a query is provided, semantically prioritizes matched knowledge at the top."""
        if not query:
            with self._context_summary_lock:
                if self._context_summary_cache is not None:
                    return self._context_summary_cache

        facts = self.get_all_facts()
        if not facts:
            summary = "No prior facts recorded."
            if not query:
                with self._context_summary_lock:
                    self._context_summary_cache = summary
            return summary

        semantic_section = ""
        if query:
            matched = self.semantic_search_facts(query, top_k=4, min_score=0.15)
            if matched:
                lines = [f"• {m['key'].replace('_', ' ').capitalize()}: {m['value']}" for m in matched]
                semantic_section = "[Directly Relevant Knowledge to Current Context]\n" + "\n".join(lines) + "\n\n"

        by_cat = {}
        for f in facts:
            cat = f.get("category", "general").capitalize()
            by_cat.setdefault(cat, []).append(f"{f['key'].replace('_', ' ').capitalize()}: {f['value']}")

        sections = []
        for cat, items in sorted(by_cat.items()):
            sections.append(f"[{cat}]\n" + "\n".join(f"• {item}" for item in items))
        summary = semantic_section + "\n\n".join(sections)
        if not query:
            with self._context_summary_lock:
                self._context_summary_cache = summary
        return summary

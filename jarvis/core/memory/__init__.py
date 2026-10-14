"""Long-term persistent memory management and adaptive self-learning for Jarvis 2.0."""
import contextlib
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
from typing import Dict, Any, List, Optional, Tuple, Callable

logger = logging.getLogger("Jarvis.Memory")

_FTS_STOPWORDS = {
    'what', 'whats', 'who', 'when', 'where', 'why', 'how', 'tell', 'me', 'about',
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'we', 'you', 'i', 'he', 'she', 'they', 'it', 'did', 'do', 'does', 'our',
    'my', 'your', 'their', 'his', 'her', 'its', 'that', 'this', 'these', 'those',
    'can', 'could', 'would', 'should', 'will', 'shall', 'may', 'might', 'must',
    'say', 'said', 'saying', 'talk', 'talked', 'talking', 'ask', 'asked', 'asking',
    'please', 'recall', 'remember', 'past', 'conversation', 'previous', 'discussed',
    'regarding', 'concerning', 'related',
    'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as', 'into',
    'and', 'or', 'not', 'but', 'if', 'then', 'so', 'have', 'has', 'had'
}


def sanitize_fts5_query(query: str) -> Optional[str]:
    """Sanitizes raw user query for safe SQLite FTS5 search.
    Removes punctuation/special FTS operators, filters conversational stopwords,
    and returns OR-delimited prefix wildcard terms, e.g. '"weather"* OR "tomorrow"*'.
    Returns None if no searchable tokens remain."""
    if not query or not isinstance(query, str):
        return None
    cleaned = re.sub(r"[^\w\s]", " ", query.lower())
    words = [w for w in cleaned.split() if len(w) > 1]
    filtered = [w for w in words if w not in _FTS_STOPWORDS]
    tokens = filtered if filtered else words
    if not tokens:
        return None
    return " OR ".join(f'"{t}"*' for t in tokens)


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

    def dense_vectorize(self, text: str, dim: int = 64) -> List[float]:
        """Calculates deterministic L2-normalized 64-dim float vector for sqlite-vec KNN search."""
        import hashlib
        tokens = self.tokenize(text)
        if not tokens:
            return [0.0] * dim
        vec = [0.0] * dim
        for tok in tokens:
            h = int.from_bytes(hashlib.md5(tok.encode("utf-8")).digest()[:4], "little")
            idx = h % dim
            sign = 1.0 if (h // dim) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm > 0 else vec


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

    @contextlib.contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA mmap_size=268435456;")
        conn.execute("PRAGMA cache_size=-64000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.row_factory = sqlite3.Row
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
        except Exception:
            pass
        try:
            with conn:
                yield conn
        finally:
            conn.close()

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
            # Staged Documents (Context-Gated Intake)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS staged_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_hash TEXT,
                    file_size INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'PENDING_CONTEXT',
                    user_context TEXT,
                    staged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)
            # Knowledge Documents Archive
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    summary TEXT,
                    raw_content TEXT,
                    file_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Episodic Lessons & Self-Correction Buffer (Tier 3 Reflexion)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS episodic_lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    trigger_context TEXT NOT NULL,
                    failure_signature TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tool_name, failure_signature)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_tool ON episodic_lessons(tool_name)")

            # SQLite FTS5 Virtual Tables & Real-Time Sync Triggers
            try:
                # Facts FTS5 Virtual Table & Triggers
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                        category,
                        key,
                        value,
                        content='facts',
                        content_rowid='id'
                    )
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                        INSERT INTO facts_fts(rowid, category, key, value)
                        VALUES (new.id, new.category, new.key, new.value);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                        INSERT INTO facts_fts(facts_fts, rowid, category, key, value)
                        VALUES ('delete', old.id, old.category, old.key, old.value);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                        INSERT INTO facts_fts(facts_fts, rowid, category, key, value)
                        VALUES ('delete', old.id, old.category, old.key, old.value);
                        INSERT INTO facts_fts(rowid, category, key, value)
                        VALUES (new.id, new.category, new.key, new.value);
                    END;
                """)

                # Documents FTS5 Virtual Table & Triggers
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        title,
                        category,
                        summary,
                        raw_content,
                        content='documents',
                        content_rowid='id'
                    )
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                        INSERT INTO documents_fts(rowid, title, category, summary, raw_content)
                        VALUES (new.id, new.title, new.category, new.summary, new.raw_content);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                        INSERT INTO documents_fts(documents_fts, rowid, title, category, summary, raw_content)
                        VALUES ('delete', old.id, old.title, old.category, old.summary, old.raw_content);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                        INSERT INTO documents_fts(documents_fts, rowid, title, category, summary, raw_content)
                        VALUES ('delete', old.id, old.title, old.category, old.summary, old.raw_content);
                        INSERT INTO documents_fts(rowid, title, category, summary, raw_content)
                        VALUES (new.id, new.title, new.category, new.summary, new.raw_content);
                    END;
                """)

                # Interactions FTS5 Virtual Table & Triggers
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS interactions_fts USING fts5(
                        user_input,
                        jarvis_reply,
                        content='interactions',
                        content_rowid='id'
                    )
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS interactions_ai AFTER INSERT ON interactions BEGIN
                        INSERT INTO interactions_fts(rowid, user_input, jarvis_reply)
                        VALUES (new.id, new.user_input, new.jarvis_reply);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS interactions_ad AFTER DELETE ON interactions BEGIN
                        INSERT INTO interactions_fts(interactions_fts, rowid, user_input, jarvis_reply)
                        VALUES ('delete', old.id, old.user_input, old.jarvis_reply);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS interactions_au AFTER UPDATE ON interactions BEGIN
                        INSERT INTO interactions_fts(interactions_fts, rowid, user_input, jarvis_reply)
                        VALUES ('delete', old.id, old.user_input, old.jarvis_reply);
                        INSERT INTO interactions_fts(rowid, user_input, jarvis_reply)
                        VALUES (new.id, new.user_input, new.jarvis_reply);
                    END;
                """)

                # Episodic Lessons FTS5 Virtual Table & Triggers
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS episodic_lessons_fts USING fts5(
                        tool_name,
                        trigger_context,
                        failure_signature,
                        lesson,
                        content='episodic_lessons',
                        content_rowid='id'
                    )
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS episodic_lessons_ai AFTER INSERT ON episodic_lessons BEGIN
                        INSERT INTO episodic_lessons_fts(rowid, tool_name, trigger_context, failure_signature, lesson)
                        VALUES (new.id, new.tool_name, new.trigger_context, new.failure_signature, new.lesson);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS episodic_lessons_ad AFTER DELETE ON episodic_lessons BEGIN
                        INSERT INTO episodic_lessons_fts(episodic_lessons_fts, rowid, tool_name, trigger_context, failure_signature, lesson)
                        VALUES ('delete', old.id, old.tool_name, old.trigger_context, old.failure_signature, old.lesson);
                    END;
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS episodic_lessons_au AFTER UPDATE ON episodic_lessons BEGIN
                        INSERT INTO episodic_lessons_fts(episodic_lessons_fts, rowid, tool_name, trigger_context, failure_signature, lesson)
                        VALUES ('delete', old.id, old.tool_name, old.trigger_context, old.failure_signature, old.lesson);
                        INSERT INTO episodic_lessons_fts(rowid, tool_name, trigger_context, failure_signature, lesson)
                        VALUES (new.id, new.tool_name, new.trigger_context, new.failure_signature, new.lesson);
                    END;
                """)

                # Schema metadata table for one-time migrations and rebuild tracking
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)

                # One-time rebuild to backfill any pre-existing rows into FTS indexes
                cursor.execute("SELECT 1 FROM schema_meta WHERE key = 'fts5_initialized'")
                if not cursor.fetchone():
                    try:
                        cursor.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
                        cursor.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
                        cursor.execute("INSERT INTO interactions_fts(interactions_fts) VALUES('rebuild')")
                        cursor.execute("INSERT INTO episodic_lessons_fts(episodic_lessons_fts) VALUES('rebuild')")
                        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_initialized', '1')")
                    except Exception as rb_err:
                        logger.debug("FTS5 initial rebuild notice: %s", rb_err)
            except Exception as fts_err:
                logger.warning("SQLite FTS5 virtual tables could not be initialized (falling back to standard SQL): %s", fts_err)

            # sqlite-vec Native C Vector Virtual Table & Real-Time Sync Triggers
            try:
                import sqlite_vec
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
                        fact_id INTEGER PRIMARY KEY,
                        embedding float[64]
                    )
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS facts_vec_ad AFTER DELETE ON facts BEGIN
                        DELETE FROM facts_vec WHERE fact_id = old.id;
                    END;
                """)
                # Sync any unindexed facts into facts_vec
                cursor.execute("""
                    SELECT f.id, f.category, f.key, f.value
                    FROM facts f
                    LEFT JOIN facts_vec v ON f.id = v.fact_id
                    WHERE v.fact_id IS NULL
                """)
                unindexed = cursor.fetchall()
                for f in unindexed:
                    text = f"{f['category']} {f['key']} {f['value']}"
                    vec = self.vector_engine.dense_vectorize(text)
                    blob = sqlite_vec.serialize_float32(vec)
                    cursor.execute("INSERT OR REPLACE INTO facts_vec(fact_id, embedding) VALUES (?, ?)", (f["id"], blob))

                # Documents sqlite-vec virtual table & sync triggers
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_vec USING vec0(
                        doc_id INTEGER PRIMARY KEY,
                        embedding float[64]
                    )
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS documents_vec_ad AFTER DELETE ON documents BEGIN
                        DELETE FROM documents_vec WHERE doc_id = old.id;
                    END;
                """)
                # Sync any unindexed documents into documents_vec
                cursor.execute("""
                    SELECT d.id, d.title, d.category, d.summary, d.raw_content
                    FROM documents d
                    LEFT JOIN documents_vec dv ON d.id = dv.doc_id
                    WHERE dv.doc_id IS NULL
                """)
                unindexed_docs = cursor.fetchall()
                for d in unindexed_docs:
                    raw_snippet = (d["raw_content"] or "")[:500]
                    text = f"{d['title']} {d['category']} {d['summary']} {raw_snippet}"
                    vec = self.vector_engine.dense_vectorize(text)
                    blob = sqlite_vec.serialize_float32(vec)
                    cursor.execute("INSERT OR REPLACE INTO documents_vec(doc_id, embedding) VALUES (?, ?)", (d["id"], blob))

                # Lessons sqlite-vec virtual table & sync triggers
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS lessons_vec USING vec0(
                        lesson_id INTEGER PRIMARY KEY,
                        embedding float[64]
                    )
                """)
                cursor.execute("""
                    CREATE TRIGGER IF NOT EXISTS lessons_vec_ad AFTER DELETE ON episodic_lessons BEGIN
                        DELETE FROM lessons_vec WHERE lesson_id = old.id;
                    END;
                """)
                # Sync any unindexed lessons into lessons_vec
                cursor.execute("""
                    SELECT l.id, l.tool_name, l.trigger_context, l.failure_signature, l.lesson
                    FROM episodic_lessons l
                    LEFT JOIN lessons_vec lv ON l.id = lv.lesson_id
                    WHERE lv.lesson_id IS NULL
                """)
                unindexed_lessons = cursor.fetchall()
                for l in unindexed_lessons:
                    text = f"{l['tool_name']} {l['trigger_context']} {l['failure_signature']} {l['lesson']}"
                    vec = self.vector_engine.dense_vectorize(text)
                    blob = sqlite_vec.serialize_float32(vec)
                    cursor.execute("INSERT OR REPLACE INTO lessons_vec(lesson_id, embedding) VALUES (?, ?)", (l["id"], blob))
            except Exception as vec_err:
                logger.debug("sqlite-vec virtual table initialization note: %s", vec_err)

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

            # Index into sqlite-vec facts_vec
            try:
                import sqlite_vec
                cursor.execute("SELECT id FROM facts WHERE key = ?", (key.lower().strip(),))
                row = cursor.fetchone()
                if row:
                    fact_id = row["id"]
                    text = f"{category} {key} {value}"
                    vec = self.vector_engine.dense_vectorize(text)
                    blob = sqlite_vec.serialize_float32(vec)
                    cursor.execute("INSERT OR REPLACE INTO facts_vec(fact_id, embedding) VALUES (?, ?)", (fact_id, blob))
            except Exception as e:
                logger.debug("sqlite-vec fact indexing notice: %s", e)

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

    def update_user_preference(self, key: str, value: str) -> str:
        """Stores or updates a core user preference or behavioral calibration."""
        clean_key = str(key or "").lower().strip().replace(" ", "_")
        clean_val = str(value or "").strip()
        if not clean_key or not clean_val:
            return "Preference key and value cannot be empty, Sir."

        self.remember_fact(clean_key, clean_val, category="user_preference")
        return f"Core preference updated: '{clean_key}' is now set to '{clean_val}', Sir."

    def get_user_preferences(self) -> Dict[str, str]:
        """Retrieves all core user preferences as a key-value dictionary."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT key, value FROM facts
                WHERE category IN ('user_preference', 'preference', 'core_preference', 'habit')
                ORDER BY updated_at DESC
            """)
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    def format_core_preferences_block(self) -> str:
        """Formats active core user preferences into a structured block for working memory."""
        prefs = self.get_user_preferences()
        if not prefs:
            return ""
        lines = [f"- {k}: {v}" for k, v in sorted(prefs.items())]
        return "Core User Profile & Preferences (Always adhere to these directives):\n" + "\n".join(lines)

    def search_facts(self, query: str) -> List[Dict[str, str]]:
        """Searches remembered facts matching a query keyword using FTS5 BM25 with LIKE fallback."""
        sanitized = sanitize_fts5_query(query)
        if sanitized:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT f.category, f.key, f.value
                        FROM facts_fts ft
                        JOIN facts f ON ft.rowid = f.id
                        WHERE facts_fts MATCH ?
                        ORDER BY bm25(facts_fts) ASC
                    """, (sanitized,))
                    rows = [dict(row) for row in cursor.fetchall()]
                    if rows:
                        return rows
            except Exception as e:
                logger.debug("FTS5 facts search fallback to LIKE: %s", e)

        # Fallback to standard LIKE matching
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
        """Finds stored facts semantically relevant to query using sqlite-vec KNN search with TF-IDF fallback."""
        # 1. Native C sqlite-vec KNN Fast Path (<0.2ms)
        try:
            import sqlite_vec
            q_vec = self.vector_engine.dense_vectorize(query)
            q_blob = sqlite_vec.serialize_float32(q_vec)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT f.category, f.key, f.value, v.distance
                    FROM facts_vec v
                    JOIN facts f ON v.fact_id = f.id
                    WHERE v.embedding MATCH ? AND k = ?
                    ORDER BY v.distance ASC
                """, (q_blob, max(1, top_k * 2)))
                knn_rows = cursor.fetchall()
                if knn_rows:
                    results = []
                    for row in knn_rows:
                        dist = float(row["distance"])
                        sim = max(0.0, 1.0 - (dist / 2.0))
                        if sim >= min_score:
                            results.append({
                                "category": row["category"],
                                "key": row["key"],
                                "value": row["value"],
                                "relevance_score": round(sim, 3)
                            })
                    if results:
                        results.sort(key=lambda x: x["relevance_score"], reverse=True)
                        return results[:top_k]
        except Exception as e:
            logger.debug("sqlite-vec KNN search fallback: %s", e)

        # 2. In-Memory TF-IDF Fallback
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

    def hybrid_search_facts(self, query: str, top_k: int = 5, k_rrf: int = 60) -> List[Dict[str, Any]]:
        """Hybrid search combining FTS5 BM25 and sqlite-vec KNN via Reciprocal Rank Fusion (RRF)."""
        fts_results = self.search_facts(query)
        vec_results = self.semantic_search_facts(query, top_k=max(top_k * 2, 10))

        # Map by unique key
        scores: Dict[str, float] = {}
        fact_map: Dict[str, Dict[str, Any]] = {}

        # 1. RRF score from FTS5 ranking
        for rank, f in enumerate(fts_results, start=1):
            key = f["key"]
            scores[key] = scores.get(key, 0.0) + (1.0 / (k_rrf + rank))
            fact_map[key] = f

        # 2. RRF score from Vector KNN ranking
        for rank, f in enumerate(vec_results, start=1):
            key = f["key"]
            scores[key] = scores.get(key, 0.0) + (1.0 / (k_rrf + rank))
            if key not in fact_map:
                fact_map[key] = f

        # Sort by fused score
        sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
        results = []
        for key in sorted_keys[:top_k]:
            item = dict(fact_map[key])
            item["rrf_score"] = round(scores[key], 4)
            results.append(item)
        return results

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
        """Adds a calendar event with deduplication for identical title and start time."""
        clean_title = title.strip()
        clean_start = start_time.strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM calendar_events
                WHERE is_cancelled = 0 AND LOWER(title) = ? AND start_time = ?
            """, (clean_title.lower(), clean_start))
            existing = cursor.fetchone()
            if existing:
                event_id = existing["id"]
                cursor.execute("""
                    UPDATE calendar_events
                    SET end_time = COALESCE(?, end_time),
                        location = COALESCE(?, location)
                    WHERE id = ?
                """, (end_time, location, event_id))
                conn.commit()
                return f"Event #{event_id} updated: '{clean_title}' at {clean_start}" + (f" ({location})" if location else "") + "."

            cursor.execute("""
                INSERT INTO calendar_events (title, start_time, end_time, location, is_cancelled)
                VALUES (?, ?, ?, ?, 0)
            """, (clean_title, clean_start, end_time, location))
            conn.commit()
            event_id = cursor.lastrowid
        return f"Event #{event_id} scheduled: '{clean_title}' at {clean_start}" + (f" ({location})" if location else "") + "."

    def get_calendar_events(self, include_cancelled: bool = False) -> List[Dict[str, Any]]:
        """Retrieves active calendar events."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT id, title, start_time, end_time, location, created_at FROM calendar_events WHERE is_cancelled = 0 ORDER BY start_time"
            if include_cancelled:
                query = "SELECT id, title, start_time, end_time, location, created_at, is_cancelled FROM calendar_events ORDER BY start_time"
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def cancel_calendar_event(self, event_id: Any = None, title: Optional[str] = None) -> str:
        """Marks a calendar event as cancelled by numerical ID or by matching title."""
        # Normalize arguments if event_id is passed as title string or digits
        if event_id is not None:
            if isinstance(event_id, str):
                s_id = event_id.strip()
                if s_id.isdigit():
                    event_id = int(s_id)
                elif not title:
                    title = s_id
                    event_id = None
                else:
                    event_id = None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if event_id is not None:
                cursor.execute("UPDATE calendar_events SET is_cancelled = 1 WHERE id = ?", (int(event_id),))
                conn.commit()
                if cursor.rowcount > 0:
                    return f"Event #{event_id} has been cancelled, Sir."
                return f"Event #{event_id} was not found."

            if title and title.strip():
                clean_title = title.strip()
                cursor.execute("""
                    SELECT id, title FROM calendar_events
                    WHERE is_cancelled = 0 AND LOWER(title) LIKE ?
                """, (f"%{clean_title.lower()}%",))
                rows = cursor.fetchall()
                if not rows:
                    return f"No active calendar event matching '{clean_title}' was found, Sir."

                cancelled_ids = [r["id"] for r in rows]
                cancelled_titles = [r["title"] for r in rows]
                placeholders = ",".join("?" * len(cancelled_ids))
                cursor.execute(f"UPDATE calendar_events SET is_cancelled = 1 WHERE id IN ({placeholders})", cancelled_ids)
                conn.commit()
                return f"Cancelled calendar event(s): {', '.join(cancelled_titles)}, Sir."

            # If neither provided, cancel the most recent or upcoming active event
            cursor.execute("SELECT id, title FROM calendar_events WHERE is_cancelled = 0 ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE calendar_events SET is_cancelled = 1 WHERE id = ?", (row["id"],))
                conn.commit()
                return f"Cancelled event '{row['title']}' (#{row['id']}), Sir."
            return "No active calendar events to cancel, Sir."

    # Staged & Ingested Document Management
    def stage_document(self, filename: str, file_path: str, file_hash: str = "", file_size: int = 0) -> Dict[str, Any]:
        """Registers an uploaded document into the staged queue awaiting user context."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO staged_documents (filename, file_path, file_hash, file_size, status)
                VALUES (?, ?, ?, ?, 'PENDING_CONTEXT')
            """, (filename.strip(), file_path.strip(), file_hash, file_size))
            conn.commit()
            doc_id = cursor.lastrowid
            return {
                "id": doc_id,
                "filename": filename.strip(),
                "file_path": file_path.strip(),
                "status": "PENDING_CONTEXT",
            }

    def get_staged_documents(self, status: str = "PENDING_CONTEXT") -> List[Dict[str, Any]]:
        """Retrieves documents waiting in the staged queue."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status == "ALL":
                cursor.execute("SELECT * FROM staged_documents ORDER BY id DESC")
            else:
                cursor.execute("SELECT * FROM staged_documents WHERE status = ? ORDER BY id DESC", (status,))
            return [dict(row) for row in cursor.fetchall()]

    def get_staged_document(self, doc_id: Optional[int] = None, filename: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Finds a specific staged document or the most recent pending one."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if doc_id:
                cursor.execute("SELECT * FROM staged_documents WHERE id = ?", (doc_id,))
            elif filename:
                cursor.execute(
                    "SELECT * FROM staged_documents WHERE LOWER(filename) LIKE ? AND status = 'PENDING_CONTEXT' ORDER BY id DESC LIMIT 1",
                    (f"%{filename.lower().strip()}%",)
                )
            else:
                cursor.execute("SELECT * FROM staged_documents WHERE status = 'PENDING_CONTEXT' ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None

    def mark_document_processed(self, doc_id: int, user_context: str = "") -> None:
        """Marks a staged document as processed with the provided context."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE staged_documents
                SET status = 'PROCESSED', user_context = ?, processed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (user_context, doc_id))
            conn.commit()

    def save_document_knowledge(
        self,
        title: str,
        category: str = "general",
        summary: str = "",
        raw_content: str = "",
        file_path: str = ""
    ) -> int:
        """Archives document content and structured summary into the persistent knowledge archive."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO documents (title, category, summary, raw_content, file_path)
                VALUES (?, ?, ?, ?, ?)
            """, (title.strip(), category.lower().strip(), summary.strip(), raw_content.strip(), file_path.strip()))
            doc_id = cursor.lastrowid

            # Index into documents_vec
            try:
                import sqlite_vec
                raw_snippet = (raw_content or "")[:500]
                text = f"{title} {category} {summary} {raw_snippet}"
                vec = self.vector_engine.dense_vectorize(text)
                blob = sqlite_vec.serialize_float32(vec)
                cursor.execute("INSERT OR REPLACE INTO documents_vec(doc_id, embedding) VALUES (?, ?)", (doc_id, blob))
            except Exception as e:
                logger.debug("sqlite-vec document indexing notice: %s", e)

            conn.commit()
            return doc_id

    def search_documents(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches archived document summaries and titles using FTS5 BM25 with LIKE fallback."""
        sanitized = sanitize_fts5_query(query)
        if sanitized:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT d.id, d.title, d.category, d.summary, d.file_path, d.created_at
                        FROM documents_fts df
                        JOIN documents d ON df.rowid = d.id
                        WHERE documents_fts MATCH ?
                        ORDER BY bm25(documents_fts) ASC
                        LIMIT ?
                    """, (sanitized, limit))
                    rows = [dict(row) for row in cursor.fetchall()]
                    if rows:
                        return rows
            except Exception as e:
                logger.debug("FTS5 documents search fallback to LIKE: %s", e)

        # Fallback to standard LIKE matching
        q = f"%{query.lower().strip()}%"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, category, summary, file_path, created_at
                FROM documents
                WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(category) LIKE ?
                ORDER BY id DESC LIMIT ?
            """, (q, q, q, limit))
            return [dict(row) for row in cursor.fetchall()]

    def semantic_search_documents(self, query: str, top_k: int = 5, min_score: float = 0.10) -> List[Dict[str, Any]]:
        """Finds archived documents semantically relevant to query using sqlite-vec KNN search with TF-IDF fallback."""
        try:
            import sqlite_vec
            q_vec = self.vector_engine.dense_vectorize(query)
            q_blob = sqlite_vec.serialize_float32(q_vec)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT d.id, d.title, d.category, d.summary, d.file_path, d.created_at, dv.distance
                    FROM documents_vec dv
                    JOIN documents d ON dv.doc_id = d.id
                    WHERE dv.embedding MATCH ? AND k = ?
                    ORDER BY dv.distance ASC
                """, (q_blob, max(1, top_k * 2)))
                knn_rows = cursor.fetchall()
                if knn_rows:
                    results = []
                    for row in knn_rows:
                        dist = float(row["distance"])
                        sim = max(0.0, 1.0 - (dist / 2.0))
                        if sim >= min_score:
                            results.append({
                                "id": row["id"],
                                "title": row["title"],
                                "category": row["category"],
                                "summary": row["summary"],
                                "file_path": row["file_path"],
                                "created_at": row["created_at"],
                                "relevance_score": round(sim, 3),
                            })
                    if results:
                        results.sort(key=lambda x: x["relevance_score"], reverse=True)
                        return results[:top_k]
        except Exception as e:
            logger.debug("sqlite-vec documents KNN search fallback: %s", e)

        # In-Memory TF-IDF Fallback
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, category, summary, file_path, created_at, raw_content FROM documents ORDER BY id DESC LIMIT 100")
            docs = [dict(r) for r in cursor.fetchall()]

        if not docs:
            return []

        corpus = [f"{d['title']} {d['category']} {d['summary']} {(d.get('raw_content') or '')[:500]}" for d in docs]
        idf = self.vector_engine.compute_idf(corpus)
        query_vec = self.vector_engine.vectorize(query, idf)
        if not query_vec:
            return []

        scored = []
        for d, doc_text in zip(docs, corpus):
            doc_vec = self.vector_engine.vectorize(doc_text, idf)
            score = self.vector_engine.cosine_similarity(query_vec, doc_vec)
            if score >= min_score:
                d_copy = {k: v for k, v in d.items() if k != "raw_content"}
                d_copy["relevance_score"] = round(score, 3)
                scored.append(d_copy)

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored[:top_k]

    def hybrid_search_documents(self, query: str, limit: int = 5, k_rrf: int = 60) -> List[Dict[str, Any]]:
        """Hybrid search combining FTS5 BM25 and sqlite-vec KNN via Reciprocal Rank Fusion (RRF)."""
        fts_results = self.search_documents(query, limit=max(limit * 2, 10))
        vec_results = self.semantic_search_documents(query, top_k=max(limit * 2, 10))

        scores: Dict[int, float] = {}
        doc_map: Dict[int, Dict[str, Any]] = {}

        # 1. RRF score from FTS5 ranking
        for rank, d in enumerate(fts_results, start=1):
            doc_id = d["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k_rrf + rank))
            doc_map[doc_id] = d

        # 2. RRF score from Vector KNN ranking
        for rank, d in enumerate(vec_results, start=1):
            doc_id = d["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k_rrf + rank))
            if doc_id not in doc_map:
                doc_map[doc_id] = d

        # Sort by fused score
        sorted_ids = sorted(scores.keys(), key=lambda d_id: scores[d_id], reverse=True)
        results = []
        for doc_id in sorted_ids[:limit]:
            item = dict(doc_map[doc_id])
            item["rrf_score"] = round(scores[doc_id], 4)
            results.append(item)
        return results

    def get_document_by_id(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a single archived document by its primary key ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, category, summary, raw_content, file_path, created_at
                FROM documents WHERE id = ?
            """, (doc_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_recent_documents(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieves recently archived documents."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, category, summary, raw_content, file_path, created_at
                FROM documents ORDER BY id DESC LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def format_document_reference_block(self, docs: List[Dict[str, Any]]) -> str:
        """Formats retrieved document chunks using Chunk Sandwiching and metadata headers under inert XML tags.
        Bypasses 'Lost in the Middle' attention degradation by placing highest-relevance chunks at the boundaries."""
        if not docs:
            return ""

        # Sort by relevance / rrf score descending if present (Chunk Sandwiching)
        sorted_docs = sorted(
            docs,
            key=lambda x: x.get("rrf_score", x.get("relevance_score", 0.0)),
            reverse=True
        )

        chunks = []
        for d in sorted_docs:
            title = d.get("title", "Untitled Document")
            cat = d.get("category", "general")
            date = d.get("created_at", "Unknown Date")
            summary = d.get("summary", "").strip()
            raw = (d.get("raw_content") or "").strip()
            content = summary if summary else (raw[:600] if raw else "No content summary recorded.")
            header = f"[Document #{d.get('id', '?')}: {title} | Category: {cat} | Archived: {date}]"
            chunks.append(f"{header}\n{content}")

        inner = "\n\n---\n\n".join(chunks)
        return f"<reference_data>\n{inner}\n</reference_data>"

    def get_saved_calendars(self) -> List[Dict[str, Any]]:
        """Retrieves all calendar and schedule documents archived in memory."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, category, summary, file_path, created_at
                FROM documents
                WHERE LOWER(category) IN ('schedule', 'syllabus')
                   OR LOWER(title) LIKE '%timetable%'
                   OR LOWER(title) LIKE '%calendar%'
                   OR LOWER(title) LIKE '%schedule%'
                ORDER BY id DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def delete_saved_calendar(self, doc_id: int) -> bool:
        """Deletes a saved calendar document from knowledge memory."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_calendar_event(self, event_id: int) -> bool:
        """Permanently deletes a calendar event by numerical ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
            conn.commit()
            return cursor.rowcount > 0

    def clear_calendar_events(self) -> int:
        """Deletes all calendar events from the active database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM calendar_events")
            conn.commit()
            return cursor.rowcount

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
        """Searches past conversation logs by keyword or phrase using FTS5 BM25 with LIKE fallback."""
        sanitized = sanitize_fts5_query(query)
        if sanitized:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT i.id, i.user_input, i.jarvis_reply, i.tool_calls, i.created_at
                        FROM interactions_fts inf
                        JOIN interactions i ON inf.rowid = i.id
                        WHERE interactions_fts MATCH ?
                        ORDER BY bm25(interactions_fts) ASC
                        LIMIT ?
                    """, (sanitized, limit))
                    rows = [dict(r) for r in cursor.fetchall()]
                    if rows:
                        return rows
            except Exception as e:
                logger.debug("FTS5 interactions search fallback to LIKE: %s", e)

        # Fallback to standard LIKE matching
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
        extractor_fn: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None,
    ):
        """Asynchronously analyzes user utterances and interactions to extract and persist
        user preferences, habits, active technical topics, and facts across sessions."""
        cleaned = user_text.strip().lower()
        if not self.background_learning_enabled:
            return
        # Fast filter for trivial greetings, one-word queries, and short commands
        trivial = {"hey jarvis", "exit", "quit", "goodbye", "help", "who are you", "yes", "no", "ok", "okay", "cancel", "thanks", "thank you"}
        if len(cleaned) < 8 or cleaned in trivial:
            return

        with self._learning_lock:
            if self._learning_in_flight:
                return
            self._learning_in_flight = True

        def _extract():
            try:
                items: List[Dict[str, Any]] = []
                if extractor_fn is not None:
                    try:
                        extracted = extractor_fn(user_text, reply_text)
                        if isinstance(extracted, list):
                            items = extracted
                    except Exception as e:
                        logger.debug("Custom extractor notice: %s", e)

                if not items:
                    # Built-in provider fallback (ollama / groq)
                    system_prompt = (
                        "You are an ambient background memory extractor for Sir's personal assistant JARVIS. "
                        "Analyze the user's utterance and determine if Sir revealed any permanent personal facts, "
                        "preferences, habits, routine details, projects, or behavioral rules about himself. "
                        "If so, output a JSON array of objects with keys: 'category' (identity|preference|habit|project|topic|general), "
                        "'key' (snake_case, concise), 'value' (clear description). "
                        "If no persistent personal facts about Sir were shared, output ONLY an empty array: []\n"
                        "Output valid JSON without markdown fences."
                    )
                    if provider == "ollama":
                        api_key = "ollama"
                        base_url = self.llm_config.get("ollama_base_url", "http://127.0.0.1:11434/v1")
                        model = self.llm_config.get("ollama_model", "qwen3.5:2b-q4_K_M")
                    elif provider == "groq" or (groq_api_key or os.getenv("GROQ_API_KEY")):
                        api_key = groq_api_key or os.getenv("GROQ_API_KEY", "").strip()
                        base_url = "https://api.groq.com/openai/v1"
                        model = self.llm_config.get("groq_model", "qwen/qwen3.8-27b")
                    else:
                        api_key = None

                    if api_key:
                        import time
                        if provider == "groq":
                            time.sleep(1.0)
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
                            max_tokens=250,
                            temperature=0.0,
                            **({"reasoning_effort": "none"} if provider == "ollama" else {}),
                        )
                        content = res.choices[0].message.content.strip()
                        if content.startswith("```"):
                            content = re.sub(r"^```(?:json)?\n?", "", content)
                            content = re.sub(r"\n?```$", "", content)
                        parsed = json.loads(content)
                        if isinstance(parsed, list):
                            items = parsed

                # Sensitive data patterns to prevent storing secrets/credentials
                sensitive_pattern = re.compile(
                    r"(?:api[_-]?key|secret|token|password|bearer|auth|credit[_-]?card)[\s:=]+[\"']?([A-Za-z0-9_\-\.]{12,})[\"']?",
                    re.IGNORECASE
                )

                if isinstance(items, list):
                    for item in items:
                        key = str(item.get("key", "")).strip().lower().replace(" ", "_")
                        val = str(item.get("value", "")).strip()
                        cat = str(item.get("category", "general")).strip().lower()

                        if not key or not val:
                            continue

                        # Security check: redact or skip sensitive credentials
                        if sensitive_pattern.search(val) or sensitive_pattern.search(key):
                            logger.warning("Redacting sensitive credential token from background memory inscription: %s", key)
                            continue

                        # Dual-crystallization:
                        # 1. User preferences & habits go to Tier 1 core user preferences (loaded every turn)
                        if cat in ("preference", "habit", "identity") or "preference" in key or "habit" in key:
                            self.update_user_preference(key, val)

                        # 2. All items are permanently recorded into Tier 2 vector memory with sqlite-vec & FTS5
                        self.remember_fact(key, val, cat)
                        logger.info("Learned context about Sir: [%s] %s -> %s", cat, key, val)

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
            matched = self.hybrid_search_facts(query, top_k=4)
            if matched:
                lines = [f"• {m['key'].replace('_', ' ').capitalize()}: {m['value']}" for m in matched]
                semantic_section = "[Directly Relevant Knowledge to Current Context]\n" + "\n".join(lines) + "\n\n"

        by_cat = {}
        for f in facts:
            cat = f.get("category", "general").capitalize()
            by_cat.setdefault(cat, []).append(f"{f['key'].replace('_', ' ').capitalize()}: {f['value']}")

        sections = []
        for cat, items in sorted(by_cat.items()):
            # Bound items per category to prevent prompt bloat & Stanford/Berkeley 'Lost in the Middle' attention drops
            max_items = 4 if query else 8
            bounded_items = items[:max_items]
            sections.append(f"[{cat}]\n" + "\n".join(f"• {item}" for item in bounded_items))
        summary = semantic_section + "\n\n".join(sections)
        if not query:
            with self._context_summary_lock:
                self._context_summary_cache = summary
        return summary

    # Tier 3: Episodic Reflection & Self-Correction
    def record_episodic_lesson(
        self,
        tool_name: str,
        trigger_context: str,
        failure_signature: str,
        lesson: str,
    ) -> int:
        """Records an episodic failure post-mortem or heuristic lesson in SQLite WAL."""
        t_clean = (tool_name or "").strip()
        f_clean = (failure_signature or "").strip()[:300]
        c_clean = (trigger_context or "").strip()[:300]
        l_clean = (lesson or "").strip()

        if not t_clean or not l_clean:
            return 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO episodic_lessons (
                    tool_name, trigger_context, failure_signature, lesson, hit_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(tool_name, failure_signature) DO UPDATE SET
                    hit_count = episodic_lessons.hit_count + 1,
                    lesson = excluded.lesson,
                    trigger_context = excluded.trigger_context,
                    updated_at = CURRENT_TIMESTAMP
            """, (t_clean, c_clean, f_clean, l_clean))
            cursor.execute("SELECT id FROM episodic_lessons WHERE tool_name = ? AND failure_signature = ?", (t_clean, f_clean))
            row = cursor.fetchone()
            lesson_id = row["id"] if row else cursor.lastrowid

            # Index into lessons_vec
            try:
                import sqlite_vec
                embed_text = f"{t_clean} {c_clean} {f_clean} {l_clean}"
                vec = self.vector_engine.dense_vectorize(embed_text)
                blob = sqlite_vec.serialize_float32(vec)
                cursor.execute("INSERT OR REPLACE INTO lessons_vec(lesson_id, embedding) VALUES (?, ?)", (lesson_id, blob))
            except Exception as e:
                logger.debug(f"sqlite-vec lesson indexing notice: {e}")

            conn.commit()
            logger.info(f"Recorded episodic lesson #{lesson_id} for tool '{t_clean}': {l_clean[:60]}...")
            return lesson_id

    def recall_episodic_lessons(
        self,
        query: str,
        tool_name: Optional[str] = None,
        top_k: int = 3,
        min_sim: float = 0.30,
        k_rrf: int = 60,
    ) -> List[Dict[str, Any]]:
        """Recalls relevant episodic lessons using Hybrid RRF search (FTS5 BM25 + sqlite-vec KNN)."""
        if not query:
            return []

        # 1. FTS5 Search
        fts_matches = []
        sanitized = sanitize_fts5_query(query)
        if sanitized:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    sql = """
                        SELECT l.id, l.tool_name, l.trigger_context, l.failure_signature, l.lesson, l.hit_count
                        FROM episodic_lessons_fts lf
                        JOIN episodic_lessons l ON lf.rowid = l.id
                        WHERE episodic_lessons_fts MATCH ?
                    """
                    params = [sanitized]
                    if tool_name:
                        sql += " AND l.tool_name = ?"
                        params.append(tool_name.strip())
                    sql += " ORDER BY bm25(episodic_lessons_fts) ASC LIMIT ?"
                    params.append(max(top_k * 2, 8))
                    cursor.execute(sql, params)
                    fts_matches = [dict(r) for r in cursor.fetchall()]
            except Exception as e:
                logger.debug(f"FTS5 lessons search fallback: {e}")

        # 2. sqlite-vec KNN Search
        vec_matches = []
        try:
            import sqlite_vec
            q_vec = self.vector_engine.dense_vectorize(query)
            q_blob = sqlite_vec.serialize_float32(q_vec)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                sql = """
                    SELECT l.id, l.tool_name, l.trigger_context, l.failure_signature, l.lesson, l.hit_count, lv.distance
                    FROM lessons_vec lv
                    JOIN episodic_lessons l ON lv.lesson_id = l.id
                    WHERE lv.embedding MATCH ? AND k = ?
                """
                params = [q_blob, max(top_k * 2, 8)]
                if tool_name:
                    sql += " AND l.tool_name = ?"
                    params.append(tool_name.strip())
                sql += " ORDER BY lv.distance ASC"
                cursor.execute(sql, params)
                for r in cursor.fetchall():
                    dist = float(r["distance"])
                    cos_sim = max(0.0, 1.0 - ((dist * dist) / 2.0))
                    if cos_sim >= min_sim:
                        item = dict(r)
                        item["sim"] = round(cos_sim, 3)
                        vec_matches.append(item)
        except Exception as e:
            logger.debug(f"sqlite-vec lessons KNN search fallback: {e}")

        # 3. Reciprocal Rank Fusion
        scores: Dict[int, float] = {}
        lesson_map: Dict[int, Dict[str, Any]] = {}

        for rank, item in enumerate(fts_matches, start=1):
            l_id = item["id"]
            scores[l_id] = scores.get(l_id, 0.0) + (1.0 / (k_rrf + rank))
            lesson_map[l_id] = item

        for rank, item in enumerate(vec_matches, start=1):
            l_id = item["id"]
            scores[l_id] = scores.get(l_id, 0.0) + (1.0 / (k_rrf + rank))
            if l_id not in lesson_map:
                lesson_map[l_id] = item

        sorted_ids = sorted(scores.keys(), key=lambda lid: scores[lid], reverse=True)
        results = []
        for l_id in sorted_ids[:top_k]:
            res_item = dict(lesson_map[l_id])
            res_item["rrf_score"] = round(scores[l_id], 4)
            results.append(res_item)
        return results


from .obsidian_sync import (
    ObsidianContextSyncEngine,
    ObsidianChunk,
    ObsidianNoteParser,
    SyncStats,
)

__all__ = [
    "MemoryManager",
    "VectorMemoryEngine",
    "ObsidianContextSyncEngine",
    "ObsidianChunk",
    "ObsidianNoteParser",
    "SyncStats",
]

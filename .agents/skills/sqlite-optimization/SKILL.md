---
name: sqlite-optimization
description: >-
  High-performance SQLite WAL tuning, memory-mapped I/O, FTS5 BM25 search,
  and local C-vector search via sqlite-vec for Jarvis 2.0. Eliminates Windows file-lock
  contention and enables zero-server semantic similarity retrieval.
---

# SQLite Performance Tuning & Vector Memory Engine

This skill optimizes Jarvis 2.0 persistent memory, habit cache, and vector embeddings
for maximum throughput, concurrency, and sub-millisecond query latency.

---

## 1. Production SQLite Pragmas for Windows

Every database connection in Jarvis (`MemoryManager`, `ReflexEngine`) should execute
these pragmas immediately upon opening:

```python
conn.execute("PRAGMA journal_mode=WAL;")              # Write-Ahead Logging for non-blocking concurrent reads
conn.execute("PRAGMA synchronous=NORMAL;")            # Reduces fsync frequency without compromising WAL durability
conn.execute("PRAGMA busy_timeout=5000;")             # 5s wait instead of instant SQLITE_BUSY error
conn.execute("PRAGMA mmap_size=268435456;")           # 256MB memory-mapped I/O for lightning-fast reads
conn.execute("PRAGMA cache_size=-64000;")             # 64MB in-memory page cache
conn.execute("PRAGMA temp_store=MEMORY;")             # Store temporary tables in RAM
```

---

## 2. In-Database Vector Search with `sqlite-vec`

Load `sqlite-vec` without external server infrastructure (Milvus/Pinecone/Chroma):

```python
import sqlite3
import sqlite_vec
import struct

conn = sqlite3.connect("data/jarvis_memory.db")
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)

# Create virtual vector table for 384-dim (e.g. all-MiniLM-L6-v2) or custom embeddings
conn.execute("""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
    embedding float[384]
);
""")

def serialize_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

# KNN Query (sub-2ms)
def search_nearest(conn, query_vec: list[float], limit=5):
    cur = conn.execute("""
        SELECT rowid, distance
        FROM vec_memory
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?;
    """, [serialize_vector(query_vec), limit])
    return cur.fetchall()
```

---

## 3. Safe Context-Managed Connection Protocol

Always wrap SQLite connections with explicit contextmanager cleanup to avoid WAL file locks on Windows:

```python
import contextlib
import sqlite3

@contextlib.contextmanager
def get_db_connection(db_path: str):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()
```

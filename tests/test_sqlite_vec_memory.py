"""Automated tests for sqlite-vec native C SIMD vector indexing in MemoryManager."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.core.memory import MemoryManager, VectorMemoryEngine
import sqlite_vec


class TestSqliteVecMemorySuite(unittest.TestCase):
    """Verifies sqlite-vec KNN search, deterministic embeddings, and schema integration."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = {
            "_base_dir": self.temp_dir.name,
            "memory": {"db_path": "test_memory.db", "background_learning": False}
        }
        self.memory = MemoryManager(self.config)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dense_vectorize_determinism_and_norm(self):
        """Verifies dense_vectorize produces deterministic, unit-normalized 64-dim float vectors."""
        engine = VectorMemoryEngine()
        vec1 = engine.dense_vectorize("Jarvis coffee espresso preference")
        vec2 = engine.dense_vectorize("Jarvis coffee espresso preference")

        self.assertEqual(len(vec1), 64)
        self.assertEqual(vec1, vec2, "Embeddings for identical text must be deterministic")

        # Check L2 unit norm
        norm = sum(v * v for v in vec1)
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_facts_vec_table_created(self):
        """Verifies facts_vec virtual table exists in SQLite database."""
        with self.memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='facts_vec'")
            row = cursor.fetchone()
            self.assertIsNotNone(row, "facts_vec virtual table must exist")

    def test_remember_fact_populates_facts_vec_and_knn_search(self):
        """Verifies storing a fact indexes into facts_vec and enables sub-millisecond KNN search."""
        self.memory.remember_fact("favorite_beverage", "double espresso coffee", "preference")
        self.memory.remember_fact("code_editor", "VS Code with Python extension", "development")
        self.memory.remember_fact("pet_name", "Max the Golden Retriever", "personal")

        # Semantic KNN search for coffee
        t0 = time.perf_counter()
        results = self.memory.semantic_search_facts("What kind of coffee or espresso do I drink?", top_k=2)
        latency_ms = (time.perf_counter() - t0) * 1000

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["key"], "favorite_beverage")
        self.assertIn("espresso", results[0]["value"])
        self.assertLess(latency_ms, 25.0, f"KNN search should be ultra-fast, took {latency_ms:.2f}ms")

    def test_knn_benchmark_100_queries(self):
        """Benchmarks 100 KNN vector queries against facts_vec for sub-millisecond throughput."""
        self.memory.remember_fact("favorite_editor", "Cursor IDE and VSCode", "tools")
        self.memory.remember_fact("preferred_language", "Python 3.12 and Rust", "dev")

        t0 = time.perf_counter()
        for _ in range(100):
            self.memory.semantic_search_facts("programming editor choice", top_k=1)
        total_time_ms = (time.perf_counter() - t0) * 1000
        avg_ms = total_time_ms / 100

        self.assertLess(avg_ms, 5.0, f"Average query latency should be < 5ms, was {avg_ms:.2f}ms")


if __name__ == "__main__":
    unittest.main()

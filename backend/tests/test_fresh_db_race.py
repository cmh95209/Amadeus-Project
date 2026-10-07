# -*- coding: utf-8 -*-
"""Offline checks for the fresh-database startup race (2026-10-07).

On a fresh install the FIRST request after the launcher starts must migrate
the brand-new memory.db (add the japanese/active/greeting columns, seed the
'General' chat), while the startup page fires a burst of requests at the
same time (conversations, memory, greet, ...). The pre-fix code did
check-then-ALTER for each column, so two racing requests could both see
"column missing" and both run the ALTER; the second one crashed with
'duplicate column name' (observed in the fresh VMware Win11 VM run: her
welcome line was lost and the page fell into a cascade of 'database is
locked' 500s, because the crashed request also leaked its connection).

Fixes under test (memory.py):
- _add_column() treats sqlite's 'duplicate column name' as "a concurrent
  request already added it" instead of an error;
- the 'General' seed is one guarded INSERT, which a burst cannot double;
- the migration work is committed explicitly, so it sticks no matter which
  request ran it (before, some callers rolled the whole upgrade back);
- _connect_db() registers a finalizer on every connection, so a crashed or
  leaking request can no longer hold the database locked.
"""
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import memory as store


class FreshDbRaceTest(unittest.TestCase):
    """A temp memory.db swapped in via the store's module-level path
    (read at call time, so patching works)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem_db = str(Path(self.tmp.name) / "memory.db")
        self._p_db = patch.object(store, "PATH_TO_MEMORY", self.mem_db)
        self._p_db.start()

    def tearDown(self):
        self._p_db.stop()
        self.tmp.cleanup()

    # ---------- helpers ----------

    def _table_columns(self, table: str):
        conn = sqlite3.connect(self.mem_db)
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def _general_rows(self) -> int:
        conn = sqlite3.connect(self.mem_db)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE title = 'General'"
            ).fetchone()[0]
        finally:
            conn.close()

    def _assert_fully_migrated(self):
        for col in ("audio", "conversation_id", "japanese", "active", "greeting"):
            self.assertIn(col, self._table_columns("messages"))
        for col in ("rolling_summary", "summary_covers"):
            self.assertIn(col, self._table_columns("conversations"))

    # ---------- tests ----------

    def test_fresh_migration_is_idempotent(self):
        """Two sequential requests over a brand-new database: the second
        re-runs the whole migration, changes nothing, and the database ends
        with exactly one default chat."""
        c1 = store._connect_db()
        store._ensure_messages_table(c1)
        c1.close()

        c2 = store._connect_db()
        store._ensure_messages_table(c2)
        c2.close()

        self._assert_fully_migrated()
        self.assertEqual(self._general_rows(), 1)

    def test_add_column_accepts_concurrent_winner(self):
        """If the column already exists (a concurrent request added it),
        _add_column is a clean no-op that reports it did not add it."""
        c = store._connect_db()
        c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
        c.commit()
        self.assertTrue(store._add_column(c, "messages", "greeting",
                                          "INTEGER DEFAULT 0"))
        self.assertFalse(store._add_column(c, "messages", "greeting",
                                           "INTEGER DEFAULT 0"))
        c.close()

    def test_add_column_absorbs_duplicate_column_error(self):
        """The exact VM crash, made deterministic: the check (forced) says
        the column is missing, the ALTER runs, sqlite answers 'duplicate
        column name' - the helper must swallow it like a concurrent winner.
        Any OTHER OperationalError must still propagate."""
        c = store._connect_db()
        c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, "
                  "greeting INTEGER DEFAULT 0)")
        c.commit()
        with patch.object(store, "_column_exists", return_value=False):
            self.assertFalse(store._add_column(c, "messages", "greeting",
                                               "INTEGER DEFAULT 0"))
            with self.assertRaises(sqlite3.OperationalError):
                # an unparseable "column name" = a genuine SQL failure, not
                # a duplicate - it must not be absorbed
                store._add_column(c, "messages", ")", "TEXT")
        c.close()

    def test_concurrent_startup_burst_migrates_cleanly(self):
        """The startup burst, re-created offline: several connections
        migrate the same fresh database at the same time. Every request
        must succeed, and the result is exactly one default chat with all
        columns present."""
        n = 8
        barrier = threading.Barrier(n)
        errors = []

        def worker():
            try:
                conn = store._connect_db()
                try:
                    barrier.wait(timeout=10)
                    store._ensure_messages_table(conn)
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001 - collected per thread
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(errors, [])

        self._assert_fully_migrated()
        self.assertEqual(self._general_rows(), 1)
        conn = sqlite3.connect(self.mem_db)
        total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        conn.close()
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()

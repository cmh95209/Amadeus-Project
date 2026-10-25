"""Per-chat turn numbers (2026-10-25).

The user's Nth message in a chat is turn N; her reply (every version), her
greetings and any other line of hers before the next user message share it;
lines before the first user message are turn 0. Numbers are never shifted
(an older deleted message leaves a gap; the newest one's number is free
again, so undo + send again keeps the turn), and chats that existed before the
column are numbered once from their order. Throwaway databases only.
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import memory as store


class _DbCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self._dir.name) / "memory.db")
        self._p = patch.object(store, "PATH_TO_MEMORY", self.db)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._dir.cleanup()

    def turns(self, conv):
        conn = sqlite3.connect(self.db)
        try:
            return [tuple(r) for r in conn.execute(
                "SELECT role, turn FROM messages WHERE conversation_id = ? ORDER BY id",
                (conv,))]
        finally:
            conn.close()


class TurnNumberTests(_DbCase):
    def test_a_chat_counts_its_user_messages(self):
        store.append_message("assistant", "Welcome.", conversation_id=1, is_greeting=True)
        store.append_message("user", "hi", conversation_id=1)
        store.append_message("assistant", "Hi.", conversation_id=1)
        store.append_message("assistant", "Hello (regenerated).", conversation_id=1)
        store.append_message("user", "how are you", conversation_id=1)
        store.append_message("assistant", "Fine.", conversation_id=1)
        self.assertEqual(self.turns(1), [("assistant", 0), ("user", 1), ("assistant", 1),
                                         ("assistant", 1), ("user", 2), ("assistant", 2)])
        self.assertEqual(store.current_turn(1), 2)

    def test_each_chat_has_its_own_count(self):
        store.append_message("user", "a", conversation_id=1)
        store.append_message("user", "b", conversation_id=2)
        store.append_message("user", "c", conversation_id=2)
        store.append_message("user", "d", conversation_id=1)
        self.assertEqual(self.turns(1), [("user", 1), ("user", 2)])
        self.assertEqual(self.turns(2), [("user", 1), ("user", 2)])

    def test_an_older_deleted_message_leaves_a_gap(self):
        first = store.append_message("user", "a", conversation_id=1)
        store.append_message("user", "b", conversation_id=1)
        store.delete_message(first)
        self.assertEqual(self.turns(1), [("user", 2)])
        self.assertEqual(store.message_turn(store.append_message("user", "c", conversation_id=1)), 3)

    def test_undo_then_send_again_keeps_the_turn(self):
        store.append_message("user", "a", conversation_id=1)
        newest = store.append_message("user", "b", conversation_id=1)
        store.delete_message(newest)
        self.assertEqual(store.message_turn(store.append_message("user", "b2", conversation_id=1)), 2)

    def test_a_greeting_shares_the_current_turn(self):
        store.append_message("user", "a", conversation_id=1)
        gid = store.append_greeting_if_quiet(1, "Welcome back.", "おかえり。", after_user_id=None)
        self.assertEqual(store.message_turn(gid), 1)

    def test_unknown_message_and_empty_chat(self):
        self.assertIsNone(store.message_turn(999))
        self.assertEqual(store.current_turn(7), 0)


class BackfillTests(_DbCase):
    def test_old_chats_are_numbered_once_in_order(self):
        conn = sqlite3.connect(self.db)
        conn.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT,
                        content TEXT, created_at TEXT NOT NULL DEFAULT '2026-01-01 00:00',
                        audio TEXT, conversation_id INTEGER)""")
        rows = [("assistant", 1), ("user", 1), ("assistant", 1), ("user", 2),
                ("user", 1), ("assistant", 1), ("assistant", 2)]
        conn.executemany("INSERT INTO messages (role, content, conversation_id) VALUES (?, 'x', ?)",
                         rows)
        conn.commit()
        conn.close()
        self.assertEqual(store.current_turn(1), 2)        # migration runs here
        self.assertEqual(self.turns(1), [("assistant", 0), ("user", 1), ("assistant", 1),
                                         ("user", 2), ("assistant", 2)])
        self.assertEqual(self.turns(2), [("user", 1), ("assistant", 1)])
        # A new message continues the count.
        store.append_message("user", "next", conversation_id=1)
        self.assertEqual(self.turns(1)[-1], ("user", 3))


if __name__ == "__main__":
    unittest.main()

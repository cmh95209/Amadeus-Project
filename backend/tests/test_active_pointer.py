# -*- coding: utf-8 -*-
"""Offline checks for the active-conversation pointer (2026-10-06).

The pointer is a one-line note file (data/active_conversation.txt) that
remembers which chat tab the user was last in. Tab clicks, new chats and
deletions keep it current; a page reload (browser tab discard, refresh,
launcher restart) re-derives the open tab from it.

When the note is missing, unreadable, or names a session that no longer
exists, _active_conv_id_from_conn() recovers and rewrites the note. The
2026-10-06 change: the recovery target is the MOST RECENTLY USED session
(newest updated_at - the same successor rule the delete path uses), not
blindly the first one (General). Observed incident: the note was lost
out-of-band on a live machine and the app dumped the user back onto
General even though their last chat was a different tab. On a fresh
install the first (seeded 'General') session is also the most recent one,
so fresh-install behaviour is unchanged.

All tests use a temp memory DB + temp pointer file (the patched
module-level paths) and never touch the real data/ files. Timestamps are
relative to "now": _ensure_messages_table seeds a 'General' row whose
updated_at is the seeding moment, so the "last used" tab always gets a
FUTURE offset to win the ordering, exactly like a user who used it last.
"""
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import memory as store


def _ts(hours_offset: float) -> str:
    return (datetime.now() + timedelta(hours=hours_offset)).strftime(
        "%Y-%m-%d %H:%M")


class _PointerHarness:
    """A temporary memory DB + pointer file, swapped in via the store's
    module-level path constants (read at call time, so patching works).
    The first conn() seeds the table the way the app does: one 'General'
    conversation (id 1)."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem_db = str(Path(self.tmp.name) / "memory.db")
        self.active_file = str(Path(self.tmp.name) / "active_conversation.txt")
        self._p_db = patch.object(store, "PATH_TO_MEMORY", self.mem_db)
        self._p_act = patch.object(store, "PATH_TO_ACTIVE_CONV",
                                   self.active_file)
        self._p_db.start()
        self._p_act.start()
        Path(self.active_file).write_text("", encoding="utf-8")
        self.seed_id = None

    def close(self):
        self._p_db.stop()
        self._p_act.stop()
        self.tmp.cleanup()

    def conn(self):
        c = sqlite3.connect(self.mem_db)
        store._ensure_messages_table(c)
        store._ensure_conversations(c)
        if self.seed_id is None:
            row = c.execute("SELECT id FROM conversations ORDER BY id ASC "
                            "LIMIT 1").fetchone()
            self.seed_id = row[0] if row else None
        return c

    def new_conv(self, title, hours_offset):
        c = self.conn()
        cid = c.execute("INSERT INTO conversations (title) VALUES (?)",
                        (title,)).lastrowid
        ts = _ts(hours_offset)
        c.execute("UPDATE conversations SET created_at = ?, updated_at = ? "
                  "WHERE id = ?", (ts, ts, cid))
        c.commit()
        c.close()
        return cid

    def drop_conv(self, conv_id):
        c = self.conn()
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        c.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        c.commit()
        c.close()

    def note(self, text):
        Path(self.active_file).write_text(text, encoding="utf-8")

    def note_value(self):
        return Path(self.active_file).read_text(encoding="utf-8").strip()


class ActivePointerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.h = _PointerHarness()
        self.addCleanup(self.h.close)

    # -- the 2026-10-06 behaviour: lost note -> last-used tab ------------

    def test_empty_note_recovers_most_recently_used(self):
        self.h.new_conv("Old chat", -2)
        self.h.new_conv("Genshin", -1)
        last = self.h.new_conv("Sorry-not-talking", +1)  # used last
        self.h.note("")  # the note was lost

        self.assertEqual(store.load_active_conversation(), last)
        # ...and the note is rewritten so the recovery sticks.
        self.assertEqual(self.h.note_value(), str(last))

    def test_corrupt_note_recovers_most_recently_used(self):
        self.h.new_conv("Old chat", -2)
        last = self.h.new_conv("Later", +1)
        self.h.note("not-a-number")

        self.assertEqual(store.load_active_conversation(), last)
        self.assertEqual(self.h.note_value(), str(last))

    def test_fresh_install_empty_note_still_lands_on_first(self):
        # Only the seeded 'General' row exists: "most recently used" IS the
        # first one, so a fresh install behaves exactly as before the change.
        self.h.conn()  # seed, like app startup does
        self.h.note("")

        self.assertEqual(store.load_active_conversation(), self.h.seed_id)
        self.assertEqual(self.h.note_value(), str(self.h.seed_id))

    # -- a note naming a deleted session ---------------------------------

    def test_deleted_conversation_note_recovers_among_survivors(self):
        last = self.h.new_conv("Later", +1)
        gone = self.h.new_conv("Gone", +0.5)
        self.h.drop_conv(gone)
        self.h.note(str(gone))  # the note still names the deleted tab

        self.assertEqual(store.load_active_conversation(), last)
        self.assertEqual(self.h.note_value(), str(last))

    # -- regression: a usable note is never second-guessed ---------------

    def test_valid_note_is_respected_even_if_older(self):
        self.h.conn()  # seed: the user is deliberately in this (older) tab
        self.h.note(str(self.h.seed_id))
        self.h.new_conv("Later", +1)  # another tab was used more recently

        self.assertEqual(store.load_active_conversation(), self.h.seed_id)
        self.assertEqual(self.h.note_value(), str(self.h.seed_id))

    def test_valid_note_points_to_existing_and_is_untouched(self):
        last = self.h.new_conv("Later", +1)
        self.h.note(str(last))

        self.assertEqual(store.load_active_conversation(), last)
        self.assertEqual(self.h.note_value(), str(last))

    def test_recovered_id_round_trips_into_a_valid_note(self):
        # Whatever the recovery picks, the note it writes must name a
        # conversation that exists on a subsequent read (no second reset).
        last = self.h.new_conv("Later", +1)
        self.h.note("")

        first = store.load_active_conversation()
        self.assertEqual(first, last)
        self.assertEqual(store.load_active_conversation(), last)


if __name__ == "__main__":
    unittest.main()

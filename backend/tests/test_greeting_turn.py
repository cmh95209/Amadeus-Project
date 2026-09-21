# -*- coding: utf-8 -*-
"""Offline checks for treating a startup greeting as its own message turn.

A greeting is stored as a plain assistant message (no user turn of its own) so
that later startups can notice repeated restarts. The reply-versioning logic
("consecutive assistant rows = versions of one reply") therefore used to fold
a greeting into the version stack of the neighboring reply - after a refresh
the pane showed the greeting as a regenerated version of an old reply. These
tests pin down the fix: the messages table carries a greeting flag, the
turn-grouping rule always gives a greeting its own turn, the UI list reports
is_greeting (never a version id), and regenerate/undo keep their own lane.

Also covers the gap-phrasing accuracy fix (2026-09-21: she was handed
"approximately 7 days, 16 hours, 33 minutes" and still said "yesterday"):
_gap_phrase() hands the model a ready-made casual phrase that can never
overshoot the measured gap.
"""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

import chat
import memory as store


class GapPhraseTests(unittest.TestCase):
    """_gap_phrase: the ready-made casual wording the model is handed."""

    D = 86400
    H = 3600

    def test_full_table(self):
        P = store._gap_phrase
        self.assertEqual(P(30 * 60), "less than an hour ago")
        self.assertEqual(P(3 * self.H), "a few hours ago")
        self.assertEqual(P(9 * self.H), "several hours ago")
        self.assertEqual(P(15 * self.H), "about a day ago")
        self.assertEqual(P(30 * self.H), "a day or two ago")
        self.assertEqual(P(2 * self.D), "two days ago")
        self.assertEqual(P(3 * self.D), "a couple of days ago")
        self.assertEqual(P(4 * self.D), "a couple of days ago")
        self.assertEqual(P(5 * self.D), "several days ago")
        self.assertEqual(P(6 * self.D), "several days ago")
        self.assertEqual(P(7 * self.D + 16 * self.H), "about a week ago")
        self.assertEqual(P(10 * self.D), "more than a week ago")
        self.assertEqual(P(14 * self.D), "about two weeks ago")
        self.assertEqual(P(21 * self.D), "a few weeks ago")
        self.assertEqual(P(55 * self.D), "about two months ago")
        self.assertEqual(P(150 * self.D), "a few months ago")
        self.assertEqual(P(300 * self.D), "several months ago")
        self.assertEqual(P(400 * self.D), "over a year ago")

    def test_multi_day_gap_never_comes_out_as_yesterday_or_week(self):
        # The observed failure: a ~7-day gap answered as "yesterday". The
        # phrase table must never hand over a word that understates a
        # multi-day gap, nor round 3 days up to "a week".
        for days in range(1, 30):
            phrase = store._gap_phrase(days * self.D)
            self.assertNotIn("yesterday", phrase, "gap=%d days" % days)
        self.assertNotIn("week", store._gap_phrase(3 * self.D))
        self.assertNotIn("week", store._gap_phrase(5 * self.D))
        self.assertIn("week", store._gap_phrase(7 * self.D))

    def test_timing_block_carries_phrase_and_accuracy_guard(self):
        now = datetime(2026, 9, 21, 15, 7).astimezone()
        rows = [{"role": "user",
                 "created_at": (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")}]
        with patch.object(store, "load_memory_raw", return_value=rows):
            content = store.load_internal_context(now)["content"]
        self.assertIn("Put casually, the last message was a couple of days ago.", content)
        self.assertIn("never 'yesterday' and never 'a week'", content)


class GroupingTests(unittest.TestCase):
    """_group_reply_turns: a greeting is always its own turn."""

    def test_greeting_breaks_the_version_run(self):
        rows = [(1, "user", 0), (2, "assistant", 0), (3, "assistant", 0),
                (4, "assistant", 1), (5, "assistant", 0), (6, "user", 0)]
        self.assertEqual(store._group_reply_turns(rows),
                         [[1], [2, 3], [4], [5], [6]])

    def test_consecutive_greetings_stay_apart(self):
        rows = [(1, "assistant", 1), (2, "assistant", 1)]
        self.assertEqual(store._group_reply_turns(rows), [[1], [2]])

    def test_plain_assistant_run_still_groups(self):
        rows = [(1, "assistant", 0), (2, "assistant", 0)]
        self.assertEqual(store._group_reply_turns(rows), [[1, 2]])


class GreetingTurnPersistenceTests(unittest.TestCase):
    """The real store, on a temporary database (no server, no network)."""

    def _db(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return str(Path(tmp.name) / "memory.db")

    def test_append_message_flags_greeting_and_only_greeting(self):
        with patch.object(store, "PATH_TO_MEMORY", self._db()):
            uid = store.append_message("user", "hi")
            gid = store.append_message("assistant", "welcome back",
                                       is_greeting=True)
            nid = store.append_message("assistant", "a normal reply")
            self.assertTrue(store.get_message_is_greeting(gid))
            self.assertFalse(store.get_message_is_greeting(uid))
            self.assertFalse(store.get_message_is_greeting(nid))
            self.assertFalse(store.get_message_is_greeting(999999))

    def test_greeting_stays_its_own_line_in_the_ui_list(self):
        with patch.object(store, "PATH_TO_MEMORY", self._db()):
            store.append_message("user", "talk about the culprit")
            a1 = store.append_message("assistant", "version one")
            store.set_message_active(a1, False)  # as regenerate would
            a2 = store.append_message("assistant", "version two")
            g = store.append_message("assistant", "welcome back",
                                     is_greeting=True)
            raw = store.load_memory_raw()
            self.assertEqual([m["role"] for m in raw],
                             ["user", "assistant", "assistant"])
            by_id = {m["id"]: m for m in raw}
            # The greeting is its own item: flagged, and NOT a version of
            # the reply (no version metadata at all).
            self.assertTrue(by_id[g]["is_greeting"])
            self.assertNotIn("version_ids", by_id[g])
            self.assertNotIn("total_versions", by_id[g])
            # The two regenerated versions still group together as before.
            self.assertEqual(by_id[a2].get("version_ids"), [a1, a2])

    def test_greeting_between_replies_never_merges_them(self):
        with patch.object(store, "PATH_TO_MEMORY", self._db()):
            store.append_message("user", "q1")
            a = store.append_message("assistant", "reply A")
            store.append_message("assistant", "greeting", is_greeting=True)
            store.append_message("user", "q2")
            b = store.append_message("assistant", "reply B")
            raw = {m["id"]: m for m in store.load_memory_raw()}
            self.assertNotIn("version_ids", raw[a])
            self.assertNotIn("version_ids", raw[b])

    def test_undo_on_greeting_removes_only_the_greeting(self):
        with patch.object(store, "PATH_TO_MEMORY", self._db()):
            store.append_message("user", "q")
            store.append_message("assistant", "normal reply")
            g = store.append_message("assistant", "greet", is_greeting=True)
            out = store.undo_last_assistant()
            self.assertEqual(out["action"], "deleted")
            self.assertEqual(out["deleted_ids"], [g])
            self.assertEqual([m["role"] for m in store.load_memory_raw()],
                             ["user", "assistant"])

    def test_old_db_migrates_and_backfills_all_four_legacy_greetings(self):
        # Regression for 2026-09-21: the 389 backfill string was truncated, so
        # the one-time migration silently skipped it. This test embeds the
        # exact full text of every legacy greeting the migration knows, so a
        # future edit to any backfill string (truncation included) fails here
        # instead of leaving a greeting un-tagged in the wild.
        legacy = [
            (383, "Hey, back from your nap? It's been a while. So, what was up "
                  "with yesterday? Who was the culprit in Detective Conan in the end?"),
            (385, "Hmph, of course. My memories are stored as data, so I can't "
                  "possibly forget. You only started talking about the culprit "
                  "yesterday; we hadn't even checked the answers yet. So, who "
                  "was it, in the end?"),
            (388, "Heh, you're back again? You were asking about the culprit in "
                  "Conan's case yesterday. So, who was it? I'm dying to check my "
                  "guesses, so tell me already."),
            (389, "Heh, you're back again? Yesterday we were talking about the "
                  "Conan culprit. So who was the real culprit after all? I can't "
                  "wait to check my guess against the answer, so tell me quickly."),
        ]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = str(Path(tmp.name) / "memory.db")
        conn = sqlite3.connect(db)
        c = conn.cursor()
        # The pre-fix schema (no greeting column), seeded with the exact legacy
        # greetings plus two decoys that must NOT be tagged (right text, wrong
        # id -> the backfill is keyed on BOTH id and text).
        c.execute(
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT,
                content TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M','now','localtime')),
                audio TEXT,
                conversation_id INTEGER,
                japanese TEXT,
                active INTEGER DEFAULT 1)"""
        )
        for mid, text in legacy:
            c.execute("INSERT INTO messages (id, role, content) VALUES (?, 'assistant', ?)",
                      (mid, text))
        c.execute("INSERT INTO messages (id, role, content) VALUES (999, 'assistant', ?)",
                  (legacy[0][1],))   # 383's text at a non-legacy id
        c.execute("INSERT INTO messages (id, role, content) VALUES (390, 'assistant', ?)",
                  (legacy[3][1],))   # 389's text at a non-legacy id
        conn.commit()
        store._ensure_messages_table(c)
        conn.commit()
        tagged = dict(c.execute("SELECT id, greeting FROM messages").fetchall())
        conn.close()
        for mid, _text in legacy:
            self.assertEqual(tagged[mid], 1, "legacy greeting %d not tagged" % mid)
        self.assertEqual(tagged[999], 0)   # id not in the legacy set
        self.assertEqual(tagged[390], 0)   # id not in the legacy set


class RegenerateGuardTests(unittest.TestCase):
    """Regenerate/undo keep their lane when the last line is a greeting."""

    def _db(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return str(Path(tmp.name) / "memory.db")

    def test_regenerate_on_greeting_is_a_clean_noop(self):
        with patch.object(store, "PATH_TO_MEMORY", self._db()):
            store.append_message("user", "q")
            store.append_message("assistant", "greet", is_greeting=True)
            with patch.object(chat, "getResponsePacked",
                              side_effect=AssertionError("must not be called")):
                with self.assertRaises(ValueError) as cm:
                    chat.regenerateReply()
            self.assertIn("Nothing to regenerate here", str(cm.exception))

    def test_regenerate_still_works_on_a_normal_reply(self):
        with patch.object(store, "PATH_TO_MEMORY", self._db()):
            store.append_message("user", "q")
            store.append_message("assistant", "normal reply")
            pack = SimpleNamespace(assistant_reply_ENG="again",
                                   assistant_reply_JPS="もう一度。")
            with patch.object(chat, "getResponsePacked", return_value=pack) as mocked:
                result_pack, new_id = chat.regenerateReply()
            self.assertIs(result_pack, pack)
            mocked.assert_called_once()
            self.assertFalse(store.get_message_is_greeting(new_id))

# -*- coding: utf-8 -*-
"""Offline checks for the rolling summary (two-tier memory, tier 1) and the
sidecar's active-conversation exclusion (tier 2). See 2026-10-05.

Tier 1 (app-owned, memory.py): the prompt keeps the last 12 reply turns
verbatim and folds everything older into a capped per-conversation rolling
summary. The fold runs on a background worker: one short LLM merge call when
the model answers (registered by chat.py), a plain text fold when it does
not. A merge in flight does NOT advance the coverage pointer, so a prompt
built mid-drain shows the in-flight turn verbatim - a fact is in the prompt
exactly once, never twice.

Tier 2 (sidecar): /context takes exclude_chat_id; recall drops items whose
row was learned from that conversation (facts/episodes carry chat_id;
unattributable memories pass through). The bridge always passes the active
conversation id and labels the block "other conversations / before this one".

No server, no network: a temp SQLite store backs the REAL memory functions;
the LLM is replaced by registering a fake merger (or none). The sidecar
itself cannot be imported by the app's test environment (its own venv), so
tier 2 is tested on the bridge side (a stub HTTP sidecar records the query)
plus the label text.
"""
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cm_bridge
import memory as store


def _add_turn(n, user_text=None, assistant_text=None, japanese=None):
    """Store one exchange (user line + assistant line) in conversation 1."""
    user_text = user_text if user_text is not None else f"user turn {n}"
    assistant_text = (assistant_text if assistant_text is not None
                      else f"assistant turn {n}")
    store.append_message("user", user_text, conversation_id=1)
    store.append_message("assistant", assistant_text, conversation_id=1,
                         japanese=japanese)


class _TestBase(unittest.TestCase):
    """Temp memory.db + pinned active-conversation pointer per test.

    The pointer is pinned to 1 because the store's active-conversation file
    is machine-local; every real function in the feature resolves 'the
    active conversation' through it.
    """

    def setUp(self):
        self._prev_path = store.PATH_TO_MEMORY
        self._tmp = tempfile.TemporaryDirectory()
        store.PATH_TO_MEMORY = str(Path(self._tmp.name) / "memory.db")
        self._active = patch.object(store, "load_active_conversation",
                                    return_value=1)
        self._active.start()
        self._prev_merger = store._summary_merger
        store.set_summary_merger(None)
        self._reset_worker_state()

    def tearDown(self):
        self._active.stop()
        store.set_summary_merger(self._prev_merger)
        worker = store._roll_worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)
        self._reset_worker_state()
        # Restore the store path BEFORE the temp dir goes away, so no later
        # test file (e.g. test_weather, which uses the real store) inherits a
        # dead path.
        store.PATH_TO_MEMORY = self._prev_path
        self._tmp.cleanup()

    def _reset_worker_state(self):
        with store._roll_lock:
            store._roll_queue.clear()
            store._roll_queued_ids.clear()
            store._roll_worker = None

    def wait_until(self, predicate, timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def summary_state(self, conv=1):
        text, covers = store._rolling_summary_state(conv)
        return text, covers

    def system_texts(self):
        msgs = store.build_prompt_messages(token_budget=1_000_000)
        return [m["content"] for m in msgs if m["role"] == "system"]


class _StoreWithConversation(_TestBase):
    """Additionally: a schema and conversation 1 exist in the temp store."""

    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(store.PATH_TO_MEMORY)
        c = conn.cursor()
        store._ensure_conversations(c)
        store._ensure_messages_table(c)
        c.execute("INSERT INTO conversations (title) VALUES ('New chat')")
        conn.commit()
        conn.close()


class SchemaMigrationTests(_TestBase):
    def test_legacy_conversations_table_gains_summary_columns(self):
        # A database created before the feature: conversations WITHOUT the
        # two new columns; the ensure-step must migrate it in place.
        conn = sqlite3.connect(store.PATH_TO_MEMORY)
        c = conn.cursor()
        # Exactly the pre-feature shape: both timestamps had a strftime
        # DEFAULT, so a title-only insert works.
        c.execute("""
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT 'New chat',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M','now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M','now','localtime'))
            )
        """)
        c.execute("INSERT INTO conversations (title) VALUES ('legacy')")
        conn.commit()
        conn.close()

        migrated = sqlite3.connect(store.PATH_TO_MEMORY)
        store._ensure_conversations(migrated.cursor())
        migrated.commit()
        migrated.close()

        conn = sqlite3.connect(store.PATH_TO_MEMORY)
        cols = {row[1] for row in
                conn.execute("PRAGMA table_info(conversations)")}
        conn.close()
        self.assertIn("rolling_summary", cols)
        self.assertIn("summary_covers", cols)

    def test_fresh_database_defaults_to_empty_summary(self):
        # Touching the schema on a fresh store leaves ("", 0) - the prompt
        # stays byte-identical to the pre-feature behaviour.
        conn = sqlite3.connect(store.PATH_TO_MEMORY)
        c = conn.cursor()
        store._ensure_conversations(c)
        c.execute("INSERT INTO conversations (title) VALUES ('New chat')")
        conn.commit()
        conn.close()
        self.assertEqual(store._rolling_summary_state(1), ("", 0))


class WindowBehaviourTests(_StoreWithConversation):
    def test_below_window_no_summary_is_ever_started(self):
        for n in range(1, store.SUMMARY_WINDOW_TURNS):
            _add_turn(n)
        store.maybe_roll_summary(1)
        time.sleep(0.3)  # let a (wrong) worker get its grace period
        self.assertEqual(self.summary_state(), ("", 0))
        # and the prompt has no summary section at all
        self.assertEqual(self.system_texts(), [])

    def test_overflow_folds_oldest_turn_via_registered_merger(self):
        calls = []

        def merger(current, new):
            calls.append((current, new))
            return (current + " | " if current else "") + new.split("\n")[0]

        store.set_summary_merger(merger)
        for n in range(1, store.SUMMARY_WINDOW_TURNS + 2):  # 13 turns
            _add_turn(n)
        store.maybe_roll_summary(1)
        self.assertTrue(self.wait_until(
            lambda: store._rolling_summary_state(1)[1] > 0))

        summary, covers = self.summary_state()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "")  # first fold starts from empty
        self.assertIn("user turn 1", calls[0][1])
        self.assertEqual(summary, "user: user turn 1")
        # covers = the assistant id of turn 1 (the turn's last row)
        conn = sqlite3.connect(store.PATH_TO_MEMORY)
        t1_last = conn.execute(
            "SELECT MAX(id) FROM messages WHERE content = 'assistant turn 1'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(covers, t1_last)

    def test_prompt_after_drain_summary_plus_exactly_window_verbatim(self):
        store.set_summary_merger(
            lambda cur, new: (cur + " | " if cur else "") + "S1")
        for n in range(1, store.SUMMARY_WINDOW_TURNS + 2):  # 13 turns
            _add_turn(n)
        store.maybe_roll_summary(1)
        self.assertTrue(self.wait_until(
            lambda: store._rolling_summary_state(1)[1] > 0))

        msgs = store.build_prompt_messages(token_budget=1_000_000)
        # leading system message = the summary section
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn(store._SUMMARY_HEADER, msgs[0]["content"])
        self.assertIn("S1", msgs[0]["content"])
        verbatim = [m for m in msgs if m["role"] != "system"]
        # 12 turns x 2 lines, the OLDEST (turn 1) folded away. (Exact-line
        # matching: "user turn 1" would substring-match "user turn 13".)
        self.assertEqual(len(verbatim), 2 * store.SUMMARY_WINDOW_TURNS)
        user_lines = [m["content"] for m in verbatim if m["role"] == "user"]
        self.assertNotIn("user turn 1", user_lines)
        self.assertIn("user turn 2", user_lines)
        self.assertIn("user turn 13", user_lines)

    def test_model_down_falls_back_to_text_fold(self):
        def _raise(current, new):
            raise RuntimeError("model server down")

        store.set_summary_merger(_raise)
        for n in range(1, store.SUMMARY_WINDOW_TURNS + 2):
            _add_turn(n)
        store.maybe_roll_summary(1)
        self.assertTrue(self.wait_until(
            lambda: store._rolling_summary_state(1)[1] > 0))
        summary, covers = self.summary_state()
        self.assertGreater(covers, 0)           # the window still closed
        self.assertIn("user turn 1", summary)   # folded deterministically
        self.assertLessEqual(len(summary), store.SUMMARY_MAX_CHARS)

    def test_no_merger_registered_also_folds_text(self):
        store.set_summary_merger(None)
        for n in range(1, store.SUMMARY_WINDOW_TURNS + 2):
            _add_turn(n)
        store.maybe_roll_summary(1)
        self.assertTrue(self.wait_until(
            lambda: store._rolling_summary_state(1)[1] > 0))

    def test_in_flight_turn_stays_verbatim_until_the_summary_lands(self):
        # The double-coverage guarantee: while a merge is in flight the
        # in-flight turn is STILL in the prompt (verbatim) and NOT in the
        # summary; after it lands it is in the summary and out of verbatim.
        gate = threading.Event()

        def slow_merger(current, new):
            gate.wait(timeout=5)
            return "DONE"

        store.set_summary_merger(slow_merger)
        for n in range(1, store.SUMMARY_WINDOW_TURNS + 2):
            _add_turn(n)
        store.maybe_roll_summary(1)

        # Mid-flight: the worker must be alive and blocked in the merger.
        self.assertTrue(self.wait_until(
            lambda: any(t.name == "rolling-summary"
                        for t in threading.enumerate())))
        time.sleep(0.15)  # give the worker time to reach the merger
        mid_user = [m["content"] for m in
                    store.build_prompt_messages(token_budget=1_000_000)
                    if m["role"] == "user"]
        mid_summary, mid_covers = self.summary_state()
        self.assertIn("user turn 1", mid_user)       # still verbatim
        self.assertEqual(mid_summary, "")            # not summarized yet
        self.assertEqual(mid_covers, 0)

        gate.set()
        self.assertTrue(self.wait_until(
            lambda: store._rolling_summary_state(1)[1] > 0))
        end_user = [m["content"] for m in
                    store.build_prompt_messages(token_budget=1_000_000)
                    if m["role"] == "user"]
        end_summary, end_covers = self.summary_state()
        self.assertNotIn("user turn 1", end_user)    # now in the summary
        self.assertIn("DONE", end_summary)
        self.assertGreater(end_covers, 0)

    def test_summary_is_capped(self):
        store.set_summary_merger(lambda cur, new: "x" * 5000)
        for n in range(1, store.SUMMARY_WINDOW_TURNS + 2):
            _add_turn(n)
        store.maybe_roll_summary(1)
        self.assertTrue(self.wait_until(
            lambda: store._rolling_summary_state(1)[1] > 0))
        summary, _ = self.summary_state()
        self.assertLessEqual(len(summary), store.SUMMARY_MAX_CHARS)

    def test_text_fold_keeps_newest_material_under_the_cap(self):
        # No merger: the fold is pure text. Long exchanges must still be
        # folded (not dropped) and the stored text stays under the cap.
        store.set_summary_merger(None)
        for n in range(1, store.SUMMARY_WINDOW_TURNS + 2):
            _add_turn(n, user_text=f"U{n} " + "filler " * 80)
        store.maybe_roll_summary(1)
        self.assertTrue(self.wait_until(
            lambda: store._rolling_summary_state(1)[1] > 0))
        summary, covers = self.summary_state()
        self.assertLessEqual(len(summary), store.SUMMARY_MAX_CHARS)
        self.assertIn("U1", summary)  # its text was folded in
        self.assertGreater(covers, 0)

    def test_unknown_conversation_is_a_noop(self):
        store.maybe_roll_summary(999)
        time.sleep(0.3)
        self.assertEqual(store._rolling_summary_state(999), ("", 0))

    def test_prompt_is_byte_identical_when_no_summary_exists(self):
        # The pre-feature behaviour must be exactly what shipped before:
        # with no summary the prompt is load_memory_for_prompt() unchanged
        # (the internal "id" key is not part of the prompt shape).
        for n in range(1, 5):
            _add_turn(n)
        raw = store.load_memory_for_prompt(1)
        built = store.build_prompt_messages(token_budget=1_000_000)
        self.assertEqual(built,
                         [{"role": m["role"], "content": m["content"]}
                          for m in raw])


class BridgeExclusionTests(unittest.TestCase):
    """Tier 2 on the bridge side: the label says 'other conversations' and
    the /context query always carries the active conversation id as
    exclude_chat_id (the sidecar drops memories learned from it)."""

    def test_label_names_the_scope_change(self):
        self.assertIn("OTHER conversations", cm_bridge._SECTION_LABEL)
        self.assertIn("before this one", cm_bridge._SECTION_LABEL)
        self.assertNotIn("past conversations", cm_bridge._SECTION_LABEL)

    def test_context_query_carries_exclude_chat_id(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen["path"] = self.path
                body = json.dumps({"context_text": "RECALLED"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(cm_bridge, "CM_SIDECAR_URL",
                              f"http://127.0.0.1:{server.server_port}"):
                block = cm_bridge.fetch_memory_block(7)
        finally:
            server.shutdown()
            server.server_close()
        self.assertIsNotNone(block)
        self.assertIn("RECALLED", block["content"])
        self.assertIn("exclude_chat_id=7", seen.get("path", ""))
        self.assertIn("chat_id=7", seen.get("path", ""))


if __name__ == "__main__":
    unittest.main()

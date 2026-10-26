"""Her "hold on, let me check" line + the live web-ON stream (2026-10-25).

The moment a real search starts, a short line of hers is written in parallel
and saved as her own message (messages.aside): never a version of the answer,
joined onto the answer in her prompt history (roles keep alternating), removed
with the answer by Undo, dropped when the answer is ready first. Web-ON replies
stream status steps, the hold-on line and the reply. Throwaway databases;
faked model calls.
"""

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_web_search_resilience import chat  # noqa: E402

import api  # noqa: E402
import memory as store  # noqa: E402


def stream_events(resp):
    body = resp.get_data(as_text=True)
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


class _Db(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self._dir.name) / "memory.db")
        self._patches = [patch.object(store, "PATH_TO_MEMORY", self.db),
                         patch.object(store, "load_active_conversation", return_value=1)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._dir.cleanup()
        chat.take_turn_sources()
        chat._turn.aside_emit = None
        chat._turn.aside_run = None

    def row_ids(self):
        store.current_turn(1)          # makes sure the table exists
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute("SELECT id FROM messages ORDER BY id")]
        finally:
            conn.close()


class StoreTests(_Db):
    def chat_with_aside(self):
        u = store.append_message("user", "Who is on the banner?", conversation_id=1)
        a = store.append_message("assistant", "The banner? Hang on, let me check.", conversation_id=1,
                                 japanese="バナー？ちょっと調べるね。", aside=True)
        r = store.append_message("assistant", "Skirk and Escoffier.", conversation_id=1,
                                 japanese="スカークとエスコフィエよ。")
        return u, a, r

    def test_an_aside_is_its_own_line_never_a_version(self):
        u, a, r = self.chat_with_aside()
        r2 = store.append_message("assistant", "Skirk + Escoffier (again).", conversation_id=1,
                                  japanese="スカークとエスコフィエ。")
        store.set_message_active(r, False)
        rows = store.load_memory_raw(1)
        self.assertEqual([m["id"] for m in rows], [u, a, r2])
        self.assertTrue(rows[1]["is_aside"])
        self.assertNotIn("total_versions", rows[1])
        self.assertEqual(rows[2]["total_versions"], 2)
        self.assertNotIn("is_aside", rows[2])
        self.assertNotIn("is_greeting", rows[1])
        self.assertEqual(store.get_trailing_turn(1), [r, r2])

    def test_prompt_history_joins_the_aside_onto_the_answer(self):
        self.chat_with_aside()
        store.append_message("user", "Thanks!", conversation_id=1)
        msgs = store.load_memory_for_prompt(1)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[1]["content"], "バナー？ちょっと調べるね。スカークとエスコフィエよ。")

    def test_an_aside_without_an_answer_is_left_out_of_the_prompt(self):
        u = store.append_message("user", "Who is on the banner?", conversation_id=1)
        store.append_message("assistant", "Hang on.", conversation_id=1, japanese="待って。", aside=True)
        self.assertEqual([m["id"] for m in store.load_memory_for_prompt(1)], [u])

    def test_undo_removes_the_answer_and_its_aside(self):
        u, a, r = self.chat_with_aside()
        result = store.undo_last_assistant(1)
        self.assertEqual(result["deleted_ids"], [a, r])
        self.assertEqual(self.row_ids(), [u])

    def test_regenerate_target_skips_the_aside(self):
        u, a, r = self.chat_with_aside()
        self.assertEqual(store.get_message_before([r], 1)["id"], u)
        self.assertTrue(store.get_message_is_aside(a))
        self.assertFalse(store.get_message_is_aside(r))

    def test_regenerate_refuses_a_lone_aside(self):
        store.append_message("user", "Who?", conversation_id=1)
        store.append_message("assistant", "Hang on.", conversation_id=1, japanese="待って。", aside=True)
        with self.assertRaises(ValueError):
            chat.regenerateReply()


class AsideWriterTests(_Db):
    CONTEXT = [{"role": "user", "content": "Who is on the Genshin banner?"}]

    def start(self, emit, write_delay=0.0, pack=None):
        pack = pack or chat.AmadeusPack(assistant_reply_JPS="ちょっと調べるね。",
                                        assistant_reply_ENG="Hang on, let me check.")

        def fake_two_step(client, messages, context, label):
            time.sleep(write_delay)
            fake_two_step.messages = messages
            return pack
        chat._begin_turn_sources(1)
        chat._turn.reply_context = self.CONTEXT
        chat._turn.aside_emit = emit
        self._p2 = [patch.object(chat, "get_llm", return_value=SimpleNamespace(bind=lambda **k: "client")),
                    patch.object(chat, "_is_local", return_value=True),
                    patch.object(chat, "_two_step_pack", side_effect=fake_two_step)]
        for p in self._p2:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._p2])
        chat._maybe_start_aside("Genshin Impact current banner")
        return fake_two_step

    def test_written_saved_and_sent(self):
        got = []
        done = threading.Event()

        def emit(aid, pack):
            got.append((aid, pack.assistant_reply_ENG))
            done.set()
        fake = self.start(emit)
        self.assertTrue(done.wait(5))
        run = chat._turn.aside_run
        run.thread.join(5)
        self.assertEqual(got[0][1], "Hang on, let me check.")
        self.assertTrue(store.get_message_is_aside(got[0][0]))
        # The note rides on the latest user message - still one user message.
        user_msgs = [m for m in fake.messages if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1)
        self.assertIn('look this up on the web for the user right now: "Genshin Impact current banner"',
                      user_msgs[0]["content"])
        self.assertIn("Do NOT answer", user_msgs[0]["content"])
        self.assertEqual(chat.close_aside(), got[0][0])
        # Started only once per reply.
        chat._turn.aside_run = None

    def test_she_is_shown_her_earlier_lines_and_retries_a_repeat(self):
        # Two earlier hold-on lines in this chat (2026-10-26: word for word the same).
        store.append_message("assistant", "Hang on.", conversation_id=1,
                             japanese="ちょっと待って、今調べてみるわね。", aside=True)
        store.append_message("assistant", "Hang on.", conversation_id=1,
                             japanese="ちょっと待って、今調べてみるわね。", aside=True)
        self.assertEqual(store.recent_aside_lines(1),
                         ["ちょっと待って、今調べてみるわね。"] * 2)
        packs = [chat.AmadeusPack(assistant_reply_JPS="ちょっと待って、今調べてみるわね。",
                                  assistant_reply_ENG="Hang on, let me look."),
                 chat.AmadeusPack(assistant_reply_JPS="原神のバナー？今見てみるわ。",
                                  assistant_reply_ENG="The Genshin banner? Let me look.")]
        prompts = []

        def fake_two_step(client, messages, context, label):
            prompts.append(messages)
            return packs[len(prompts) - 1]
        got = []
        done = threading.Event()
        chat._begin_turn_sources(1)
        chat._turn.reply_context = self.CONTEXT
        chat._turn.aside_emit = lambda aid, pack: (got.append(pack.assistant_reply_JPS), done.set())
        with patch.object(chat, "get_llm", return_value=SimpleNamespace(bind=lambda **k: "client")), \
             patch.object(chat, "_is_local", return_value=True), \
             patch.object(chat, "_two_step_pack", side_effect=fake_two_step):
            chat._maybe_start_aside("Genshin Impact current banner")
            self.assertTrue(done.wait(5))
            chat._turn.aside_run.thread.join(5)
        self.assertEqual(len(prompts), 2)                                   # one retry
        note = [m for m in prompts[0] if m.get("role") == "user"][-1]["content"]
        self.assertIn("Word it differently from your earlier hold-on lines", note)
        self.assertIn("「ちょっと待って、今調べてみるわね。」", note)
        self.assertIn("Name what you are checking", note)
        self.assertEqual(got, ["原神のバナー？今見てみるわ。"])
        chat.close_aside()

    def test_dropped_when_the_answer_is_first(self):
        got = []
        self.start(lambda aid, pack: got.append(aid), write_delay=0.3)
        run = chat._turn.aside_run
        self.assertIsNone(chat.close_aside())
        run.thread.join(5)
        self.assertEqual(got, [])
        self.assertEqual(self.row_ids(), [])

    def test_not_started_without_the_live_route(self):
        chat._begin_turn_sources(1)
        chat._turn.reply_context = self.CONTEXT
        chat._turn.aside_emit = None
        chat._maybe_start_aside("x")
        self.assertIsNone(getattr(chat._turn, "aside_run", None))

    def test_a_failed_line_is_skipped(self):
        got = []
        fail = chat._honest_plain_failure_pack()
        self.start(lambda aid, pack: got.append(aid), pack=fail)
        chat._turn.aside_run.thread.join(5)
        self.assertEqual(got, [])
        self.assertIsNone(chat.close_aside())

    def test_reused_notes_need_no_aside(self):
        store.save_lookup(1, 1, "Genshin Impact current banner", None, "reader",
                          [{"title": "x", "url": "https://x.example/"}],
                          [{"url": "https://x.example/", "notes": "- Skirk"}])
        chat._begin_turn_sources(1)
        chat._turn.reply_context = self.CONTEXT
        chat._turn.aside_emit = lambda *a: None
        with patch.object(chat, "_maybe_start_aside") as start:
            chat._web_search_with_content("Genshin Impact current banner")
        start.assert_not_called()

    def test_a_failed_reply_takes_its_aside_along(self):
        uid_holder = {}

        def failing(context, internal_context=None):
            chat._begin_turn_sources(1)
            run = chat._AsideRun()
            run.saved_id = store.append_message("assistant", "Hang on.", conversation_id=1,
                                                japanese="待って。", aside=True)
            chat._turn.aside_run = run
            raise RuntimeError("server down")
        with patch.object(chat, "getResponsePacked", side_effect=failing), \
             patch.object(chat.cm_bridge, "save_turn"), \
             patch.object(store, "load_internal_context", return_value=None):
            with self.assertRaises(RuntimeError):
                chat.getOutputPacked("Who is on the banner?")
        self.assertEqual(self.row_ids(), [])
        del uid_holder


class StreamTests(_Db):
    def post(self, path="/", body=None, work=None):
        with patch.object(api, "has_api_key", return_value=True), \
             patch.object(chat.store, "load_web_access", return_value=True), \
             patch.object(api, "getOutputPacked", side_effect=work), \
             patch.object(api, "regenerateReply", side_effect=work):
            return api.application.test_client().post(path, json=body or {"user_input": "banner?"})

    def test_events_arrive_in_order(self):
        def work(text):
            chat._progress("searching", 'Searching the web: "Genshin banner"')
            aid = store.append_message("assistant", "Hang on.", conversation_id=1,
                                       japanese="待って。", aside=True)
            chat._turn.aside_emit(aid, chat.AmadeusPack(assistant_reply_JPS="待って。",
                                                        assistant_reply_ENG="Hang on."))
            chat._progress("thinking", "Amadeus is thinking...")
            rid = store.append_message("assistant", "Skirk.", conversation_id=1, japanese="スカーク。")
            return (chat.AmadeusPack(assistant_reply_JPS="スカーク。", assistant_reply_ENG="Skirk."),
                    7, rid, 1)
        resp = self.post(work=work)
        self.assertTrue(resp.mimetype.startswith("text/event-stream"))
        events = stream_events(resp)
        self.assertEqual([e["phase"] for e in events], ["status", "aside", "status", "reply"])
        self.assertEqual(events[0]["text"], 'Searching the web: "Genshin banner"')
        self.assertEqual(events[1]["response"], "Hang on.")
        self.assertTrue(events[1]["speech_id"])
        self.assertEqual(events[3]["response"], "Skirk.")
        self.assertEqual(events[3]["user_id"], 7)
        self.assertEqual(events[3]["sources"], [])
        self.assertIsNone(getattr(chat._turn, "progress", None))   # request thread untouched

    def test_an_error_is_the_last_event(self):
        def work(text):
            raise RuntimeError("Connection refused")
        events = stream_events(self.post(work=work))
        self.assertEqual(events[-1]["phase"], "error")
        self.assertTrue(events[-1]["message"])

    def test_regenerate_streams_without_an_aside(self):
        store.append_message("user", "banner?", conversation_id=1)
        store.append_message("assistant", "Skirk.", conversation_id=1)
        seen = {}

        def work():
            seen["aside"] = chat._turn.aside_emit
            chat._progress("searching", "Searching the web: \"x\"")
            return chat.AmadeusPack(assistant_reply_JPS="a", assistant_reply_ENG="b"), 5
        events = stream_events(self.post("/regenerate", work=work))
        self.assertIsNone(seen["aside"])
        self.assertEqual([e["phase"] for e in events], ["status", "reply"])
        self.assertEqual(events[-1]["assistant_id"], 5)

    def test_regenerate_refuses_a_lone_aside(self):
        store.append_message("user", "banner?", conversation_id=1)
        store.append_message("assistant", "Hang on.", conversation_id=1, aside=True)
        resp = self.post("/regenerate", work=lambda: None)
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()

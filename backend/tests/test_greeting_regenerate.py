"""Greeting regenerate (2026-10-15).

Her latest greeting can be written again for the SAME moment (its saved
kind, tab and the tab the user came from); the old line stays as an
earlier version behind the version arrows. Scripted first-launch lines and
anything that is not her latest visible line are refused. While the new
line is written the old version is hidden, so she never reacts to her own
previous version as an "unanswered greeting".
"""

from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402 - the shared harness


class _FakeLLM:
    """Answers every call with a fixed pack (or raises), records the prompt,
    and can run a side effect first (e.g. the user typing)."""

    def __init__(self, eng="Hm. You again.", jps="ふーん、また来たのね。",
                 error=None, before=None):
        self.eng, self.jps, self.error, self.before = eng, jps, error, before
        self.messages = None

    def bind_tools(self, tools, tool_choice=None):
        return self

    def invoke(self, messages):
        if self.messages is None:
            self.messages = messages
        if self.before:
            self.before()
            self.before = None
        if self.error:
            raise self.error
        return SimpleNamespace(
            content="", tool_call_chunks=None,
            tool_calls=[{"name": "AmadeusPack", "args": {
                "assistant_reply_JPS": self.jps,
                "assistant_reply_ENG": self.eng}}])


class _Base(_FreshAppTests):
    def setUp(self):
        super().setUp()
        self.memory.save_ceremony_state("done")
        self.chat.setKey("sk-test")
        self.tab_a = self.memory.load_active_conversation()
        self.memory.append_message("user", "ALPHA-USER-LINE",
                                   conversation_id=self.tab_a)
        self.memory.append_message("assistant", "ALPHA-HER-REPLY",
                                   conversation_id=self.tab_a, japanese="あ")

    def patched(self, fake):
        stack = [
            patch.object(self.chat, "get_llm", lambda *a, **k: fake),
            patch.object(self.chat, "reset_llm", lambda: None),
            patch.object(self.chat, "_finalize", lambda pack, llm: pack),
            patch.object(self.chat, "ja_voice", SimpleNamespace(
                build_voice_context=lambda trust: {"role": "system", "content": "v"})),
            patch.object(self.chat, "stats", SimpleNamespace(load_stat=lambda key: 50.0)),
        ]

        class _Ctx:
            def __enter__(_self):
                for p in stack:
                    p.start()

            def __exit__(_self, *exc):
                for p in reversed(stack):
                    p.stop()
        return _Ctx()

    def greet(self, fake=None, **kw):
        with self.patched(fake or _FakeLLM(eng="First hello.", jps="最初のこんにちは。")):
            return self.chat.generate_greeting(**kw)

    def regen(self, message_id, fake=None):
        with self.patched(fake or _FakeLLM()):
            return self.chat.regenerate_greeting(message_id)

    def db_row(self, mid):
        conn = sqlite3.connect("data/memory.db")
        try:
            return conn.execute(
                "SELECT active, greeting, greeting_kind, greeting_from, greeting_group "
                "FROM messages WHERE id = ?", (mid,)).fetchone()
        finally:
            conn.close()

    def prompt_text(self, fake):
        return "\n".join(str(m.get("content")) for m in fake.messages)


class SchemaAndKindTests(_Base):
    def test_old_database_gains_the_columns_and_keeps_its_rows(self):
        # An old-shaped database: the columns as they were before this work.
        Path("data").mkdir(exist_ok=True)
        old = "data/old.db"
        conn = sqlite3.connect(old)
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, "
            "content TEXT, created_at TEXT NOT NULL DEFAULT "
            "(strftime('%Y-%m-%d %H:%M','now','localtime')), audio TEXT, "
            "conversation_id INTEGER, japanese TEXT, active INTEGER DEFAULT 1, "
            "greeting INTEGER DEFAULT 0)")
        conn.execute("INSERT INTO messages (role, content, conversation_id, greeting) "
                     "VALUES ('assistant', 'old hello', 1, 1)")
        conn.commit()
        self.memory._ensure_messages_table(conn.cursor())
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
        row = conn.execute("SELECT content, greeting, greeting_kind, greeting_from, "
                           "greeting_group FROM messages").fetchone()
        conn.close()
        for col in ("greeting_kind", "greeting_from", "greeting_group"):
            self.assertIn(col, cols)
        self.assertEqual(row, ("old hello", 1, None, None, None))

    def test_greetings_record_why_she_spoke(self):
        _p, sid, _c = self.greet()
        self.assertEqual(self.db_row(sid)[2:], ("startup", None, None))
        tab_b = self.memory.create_conversation("Tab B")
        _p, wid, _c = self.greet(mode="switch", conversation_id=tab_b,
                                 previous_conversation_id=self.tab_a)
        self.assertEqual(self.db_row(wid)[2:], ("switch", self.tab_a, None))

    def test_ceremony_lines_are_scripted(self):
        self.memory.save_ceremony_state("")
        with patch.object(self.api, "model_ready",
                          return_value={"ready": False, "reason": "down"}):
            res = self.client.post("/greet", json={})
        self.assertEqual(self.db_row(res.json["assistant_id"])[2], "scripted")

    def test_ui_rows_say_whether_a_greeting_can_be_regenerated(self):
        _p, sid, _c = self.greet()
        sc, _ = self.chat._store_greeting_line(
            self.chat.AmadeusPack(assistant_reply_ENG="Intro.",
                                  assistant_reply_JPS="紹介。"), self.tab_a)
        items = {m["id"]: m for m in self.memory.load_memory_raw(self.tab_a)}
        self.assertIs(items[sid]["greeting_regenerable"], True)
        self.assertIs(items[sc]["greeting_regenerable"], False)
        reply = next(m for m in items.values() if m["content"] == "ALPHA-HER-REPLY")
        self.assertNotIn("greeting_regenerable", reply)


class RegenerateTests(_Base):
    def test_new_version_and_arrows(self):
        _p, g1, _c = self.greet()
        _p, g2, conv = self.regen(g1)
        self.assertEqual(conv, self.tab_a)
        self.assertEqual(self.db_row(g1)[0], 0)
        self.assertEqual(self.db_row(g2)[0], 1)
        self.assertEqual(self.db_row(g1)[4], g1)  # the group is v1's id
        self.assertEqual(self.db_row(g2)[4], g1)
        self.assertEqual(self.db_row(g2)[2], "startup")
        shown = [m for m in self.memory.load_memory_raw(self.tab_a) if m.get("is_greeting")]
        self.assertEqual(len(shown), 1)
        self.assertEqual((shown[0]["id"], shown[0]["version"], shown[0]["total_versions"]),
                         (g2, 2, 2))
        # Flip back with the arrows (the existing version route).
        self.memory.activate_version(g1)
        shown = [m for m in self.memory.load_memory_raw(self.tab_a) if m.get("is_greeting")]
        self.assertEqual((shown[0]["id"], shown[0]["version"]), (g1, 1))

    def test_third_version_joins_the_same_group(self):
        _p, g1, _c = self.greet()
        _p, g2, _c = self.regen(g1)
        _p, g3, _c = self.regen(g2)
        self.assertEqual(self.db_row(g3)[4], g1)
        shown = [m for m in self.memory.load_memory_raw(self.tab_a) if m.get("is_greeting")]
        self.assertEqual((shown[0]["id"], shown[0]["total_versions"]), (g3, 3))

    def test_regenerating_an_earlier_version_while_viewed(self):
        _p, g1, _c = self.greet()
        _p, g2, _c = self.regen(g1)
        self.memory.activate_version(g1)      # viewing v1 again
        _p, g3, _c = self.regen(g1)
        self.assertEqual([self.db_row(i)[0] for i in (g1, g2, g3)], [0, 0, 1])

    def test_failure_restores_the_old_version(self):
        _p, g1, _c = self.greet()
        with self.assertRaises(RuntimeError):
            self.regen(g1, _FakeLLM(error=RuntimeError("model down")))
        self.assertEqual(self.db_row(g1)[0], 1)
        greetings = [r for r in self.rows() if r[5]]
        self.assertEqual(len(greetings), 1)

    def test_user_speaking_during_regenerate_restores_the_old_version(self):
        _p, g1, _c = self.greet()
        fake = _FakeLLM(before=lambda: self.memory.append_message(
            "user", "wait, I'm here", conversation_id=self.tab_a))
        with self.assertRaises(self.chat.GreetingSuperseded):
            self.regen(g1, fake)
        self.assertEqual(self.db_row(g1)[0], 1)

    def test_only_the_latest_line(self):
        _p, g1, _c = self.greet()
        self.memory.append_message("user", "hi!", conversation_id=self.tab_a)
        with self.assertRaisesRegex(ValueError, "latest"):
            self.regen(g1)

    def test_scripted_lines_are_refused(self):
        sid, _ = self.chat._store_greeting_line(
            self.chat.AmadeusPack(assistant_reply_ENG="Intro.",
                                  assistant_reply_JPS="紹介。"), self.tab_a)
        with self.assertRaisesRegex(ValueError, "scripted"):
            self.regen(sid)

    def test_replies_and_user_lines_are_refused(self):
        reply_id = self.memory.append_message("assistant", "a reply",
                                              conversation_id=self.tab_a)
        with self.assertRaises(ValueError):
            self.regen(reply_id)
        with self.assertRaises(LookupError):
            self.regen(999999)

    def test_she_does_not_see_the_version_being_replaced(self):
        # Without the hide, the replaced greeting would be the tab's last
        # line, unanswered - and she would tease the user for ignoring it.
        _p, g1, _c = self.greet()
        fake = _FakeLLM()
        self.regen(g1, fake)
        text = self.prompt_text(fake)
        self.assertNotIn("First hello.", text)
        self.assertNotIn("最初のこんにちは", text)
        self.assertNotIn("never answered it", text)
        self.assertNotIn("has not replied to it", text)
        self.assertIn("ALPHA-HER-REPLY", text)

    def test_switch_greeting_regenerates_as_a_switch_from_the_same_tab(self):
        tab_b = self.memory.create_conversation("Bravo Tab")
        self.memory.append_message("user", "BRAVO-LINE", conversation_id=tab_b)
        _p, g1, _c = self.greet(mode="switch", conversation_id=self.tab_a,
                                previous_conversation_id=tab_b)
        self.memory.set_active_conversation(tab_b)   # user is elsewhere now
        fake = _FakeLLM()
        _p, g2, conv = self.regen(g1, fake)
        self.assertEqual(conv, self.tab_a)
        self.assertEqual(self.db_row(g2)[2:4], ("switch", tab_b))
        text = self.prompt_text(fake)
        self.assertIn("[The user has switched to this conversation.]", text)
        self.assertIn('You were just in the conversation "Bravo Tab"', text)

    def test_old_greeting_without_a_kind_regenerates_as_startup(self):
        legacy = self.memory.append_message(
            "assistant", "legacy hello", conversation_id=self.tab_a,
            japanese="昔のこんにちは", is_greeting=True)
        self.assertIsNone(self.db_row(legacy)[2])
        fake = _FakeLLM()
        _p, g2, _c = self.regen(legacy, fake)
        self.assertEqual(self.db_row(g2)[2], "startup")
        self.assertIn("[The user just opened the app.]", self.prompt_text(fake))
        items = {m["id"]: m for m in self.memory.load_memory_raw(self.tab_a)}
        self.assertEqual(items[g2]["total_versions"], 2)


class GroupingTests(_Base):
    def test_separate_greetings_stay_separate_turns(self):
        _p, g1, _c = self.greet()
        _p, g2, _c = self.greet()      # a second, unrelated greeting
        turns = self.memory._group_reply_turns(
            [(g1, "assistant", 1, None), (g2, "assistant", 1, None)])
        self.assertEqual(turns, [[g1], [g2]])
        shown = [m for m in self.memory.load_memory_raw(self.tab_a) if m.get("is_greeting")]
        self.assertEqual([m["id"] for m in shown], [g1, g2])
        self.assertNotIn("version", shown[0])

    def test_reply_versions_are_unchanged(self):
        turns = self.memory._group_reply_turns([
            (1, "user", 0), (2, "assistant", 0), (3, "assistant", 0),
            (4, "assistant", 1, None), (5, "user", 0), (6, "assistant", 0)])
        self.assertEqual(turns, [[1], [2, 3], [4], [5], [6]])


class HiddenVersionFixTests(_Base):
    """Helpers that read 'her last line' now skip hidden versions."""

    def test_last_assistant_line_skips_hidden_versions(self):
        hidden = self.memory.append_message("assistant", "HIDDEN-VERSION",
                                            conversation_id=self.tab_a)
        self.memory.set_message_active(hidden, False)
        last = self.memory.load_last_assistant_line(self.tab_a)
        self.assertEqual(last["content"], "ALPHA-HER-REPLY")

    def test_previous_tab_summary_skips_hidden_versions(self):
        tab_b = self.memory.create_conversation("Tab B")
        hidden = self.memory.append_message("assistant", "HIDDEN-VERSION",
                                            conversation_id=self.tab_a)
        self.memory.set_message_active(hidden, False)
        summary = self.memory.load_previous_tab_summary(
            self.tab_a, exclude_conversation_id=tab_b)
        self.assertIn("ALPHA-HER-REPLY", summary["last_message"])


class RouteTests(_Base):
    def model(self, ready=True):
        return patch.object(self.api, "model_ready",
                            return_value={"ready": ready, "reason": "down"})

    def test_route_returns_the_new_version(self):
        _p, g1, _c = self.greet()
        with self.model(), self.patched(_FakeLLM(eng="Again, hello.")):
            res = self.client.post(f"/greetings/{g1}/regenerate")
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertIs(body["ready"], True)
        self.assertEqual(body["response"], "Again, hello.")
        self.assertEqual(body["conversation_id"], self.tab_a)
        self.assertIn("speech_id", body)

    def test_route_errors(self):
        _p, g1, _c = self.greet()
        with self.model():
            self.assertEqual(self.client.post("/greetings/999999/regenerate").status_code, 404)
        self.memory.append_message("user", "hi", conversation_id=self.tab_a)
        with self.model():
            res = self.client.post(f"/greetings/{g1}/regenerate")
        self.assertEqual(res.status_code, 400)
        self.assertIn("latest", res.json["message"])
        with self.model(ready=False):
            self.assertEqual(self.client.post(f"/greetings/{g1}/regenerate").status_code, 503)

    def test_route_model_failure_is_502_and_restores(self):
        _p, g1, _c = self.greet()
        with self.model(), self.patched(_FakeLLM(error=RuntimeError("boom"))):
            res = self.client.post(f"/greetings/{g1}/regenerate")
        self.assertEqual(res.status_code, 502)
        self.assertEqual(self.db_row(g1)[0], 1)


if __name__ == "__main__":
    unittest.main()

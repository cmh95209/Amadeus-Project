"""Greeting bug fixes (2026-10-15 review):

1. The switch greeting knows the tab the user LEFT. The client activates
   the target tab BEFORE it asks for the greeting, so the route reads the
   previous tab from the request body ({"previous_id": n}) instead of the
   (already switched) active tab.
2. The time-lie net lets a genuine welcome-back through: on a real return,
   absence wording ("久しぶり", "it's been a while") is true, not a lie -
   only an OVERSTATED gap is still caught.
3. A greeting is pinned to the tab it was written for: the history it reads
   and the row it stores follow that tab, even if the active tab changes
   while the line is being generated.
"""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402 - the shared harness


class SwitchGreetingPreviousTabTests(_FreshAppTests):
    """Fix 1: the route forwards the tab the user left."""

    def setUp(self):
        super().setUp()
        self.memory.save_ceremony_state("done")
        self.chat.setKey("sk-test")
        self.tab_a = self.memory.load_active_conversation()
        self.tab_b = self.memory.create_conversation("Tab B")
        self.pack = self.chat.AmadeusPack(
            assistant_reply_ENG="So, this one.", assistant_reply_JPS="で、こっち。")
        self.seen = {}

        def fake_generate(**kw):
            self.seen.update(kw)
            return self.pack, 7, kw.get("conversation_id")
        self.fake_generate = fake_generate

    def switch_like_the_client(self, body):
        # The real client order: activate the target, THEN ask for the line.
        self.client.post(f"/conversations/{self.tab_b}/activate")
        with self.model(True), patch.object(
                self.api, "generate_greeting", side_effect=self.fake_generate):
            return self.client.post(f"/conversations/{self.tab_b}/greet",
                                    json=body)

    def test_previous_id_from_body_reaches_the_generator(self):
        res = self.switch_like_the_client({"previous_id": self.tab_a})
        self.assertIs(res.json["ready"], True)
        self.assertEqual(self.seen["conversation_id"], self.tab_b)
        self.assertEqual(self.seen["previous_conversation_id"], self.tab_a)

    def test_without_body_falls_back_to_no_usable_previous_tab(self):
        # Old client: the only thing left to read is the active tab, which is
        # the target itself - the summary lookup then drops it (None).
        self.switch_like_the_client({})
        self.assertEqual(self.seen["previous_conversation_id"], self.tab_b)
        self.assertIsNone(self.memory.load_previous_tab_summary(
            self.tab_b, exclude_conversation_id=self.tab_b))

    def test_invalid_previous_id_is_ignored(self):
        for bad in ("2", True, None, 1.5):
            with self.subTest(bad=bad):
                self.seen.clear()
                res = self.switch_like_the_client({"previous_id": bad})
                self.assertEqual(res.status_code, 200)
                self.assertNotEqual(self.seen["previous_conversation_id"], bad)

    def test_previous_tab_summary_names_the_left_tab(self):
        self.memory.append_message("user", "good night!",
                                   conversation_id=self.tab_a)
        summary = self.memory.load_previous_tab_summary(
            self.tab_a, exclude_conversation_id=self.tab_b)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["conversation_id"], self.tab_a)
        self.assertIn("good night!", summary["last_message"])


class ReturnWelcomeBackTests(_FreshAppTests):
    """Fix 2: absence wording is true on a genuine return."""

    @staticmethod
    def sit(situation, phrase=None):
        last = ({"phrase": phrase, "time": None, "conversation_id": 1,
                 "title": "T"} if phrase else None)
        return {"situation": situation, "unanswered_fresh": False,
                "last_contact": last}

    def test_absence_wording_passes_on_a_return(self):
        sit = self.sit("return", phrase="a couple of days ago")
        for eng, ja in (
            ("Long time no see.", "久しぶり。"),
            ("It's been a while, huh.", "久しぶりだね。"),
            ("Since we last talked, I read three papers.", ""),
            ("Haven't heard from you, so I wondered.", "連絡がなかったから。"),
        ):
            with self.subTest(eng=eng, ja=ja):
                self.assertIsNone(
                    self.chat._greeting_time_lie_check(sit, eng, ja))

    def test_overstated_gap_is_still_caught_on_a_return(self):
        sit = self.sit("return", phrase="two days ago")
        self.assertIsNotNone(self.chat._greeting_time_lie_check(
            sit, "Long time no see - it's been a week!", "久しぶり、一週間ぶりだね。"))
        self.assertIsNotNone(self.chat._greeting_time_lie_check(
            sit, "", "久しぶり。何ヶ月ぶり?"))

    def test_absence_wording_is_still_a_lie_when_never_away(self):
        for situation in ("continuous", "first", "unanswered", "unknown"):
            with self.subTest(situation=situation):
                self.assertIsNotNone(self.chat._greeting_time_lie_check(
                    self.sit(situation), "", "久しぶり。"))


class FallbackLinesTests(_FreshAppTests):
    """Fix 6: the static backup lines are complete, bilingual and can never
    trip the time-lie net in the situation they are served for."""

    SITUATIONS = ("first", "continuous", "unanswered", "return", "unknown")

    def test_every_situation_has_a_line(self):
        self.assertEqual(set(self.chat._GREETING_FALLBACK_LINES),
                         set(self.SITUATIONS))

    def test_lines_pass_the_net_for_their_own_situation(self):
        for situation in self.SITUATIONS:
            en, ja = self.chat._GREETING_FALLBACK_LINES[situation]
            for fresh in (False, True):
                with self.subTest(situation=situation, fresh=fresh):
                    sit = {"situation": situation, "unanswered_fresh": fresh,
                           "last_contact": {"phrase": "less than an hour ago",
                                            "time": None}}
                    self.assertIsNone(
                        self.chat._greeting_time_lie_check(sit, en, ja))

    def test_lines_are_bilingual_and_complete(self):
        for situation, (en, ja) in self.chat._GREETING_FALLBACK_LINES.items():
            with self.subTest(situation=situation):
                self.assertRegex(en, r"[A-Za-z]")
                self.assertNotRegex(ja, r"[A-Za-z]")
                self.assertRegex(en.rstrip(), r"[.?!]$")
                self.assertRegex(ja.rstrip(), r"[。？！]$")

    def test_only_the_return_line_welcomes_back(self):
        for situation, (en, ja) in self.chat._GREETING_FALLBACK_LINES.items():
            if situation == "return":
                continue
            with self.subTest(situation=situation):
                self.assertNotIn("welcome back", en.lower())
                self.assertNotIn("おかえり", ja)


class _SwitchingLLM:
    """A fake model that, while 'writing' the line, lets the user switch to
    another tab (the race fix 3 closes). Records the prompt it was given."""

    def __init__(self, memory, switch_to):
        self.memory = memory
        self.switch_to = switch_to
        self.messages = None

    def bind_tools(self, tools, tool_choice=None):
        return self

    def invoke(self, messages):
        self.memory.set_active_conversation(self.switch_to)
        if self.messages is None:
            self.messages = messages
        return SimpleNamespace(
            content="", tool_call_chunks=None,
            tool_calls=[{"name": "AmadeusPack", "args": {
                "assistant_reply_JPS": "で、こっちの話ね。",
                "assistant_reply_ENG": "So, about this one."}}])


class GreetingPinnedToItsTabTests(_FreshAppTests):
    """Fix 3: the history read and the stored row follow the greeted tab."""

    def setUp(self):
        super().setUp()
        self.memory.save_ceremony_state("done")
        self.chat.setKey("sk-test")
        self.tab_a = self.memory.load_active_conversation()
        self.memory.append_message("user", "ALPHA-ONLY-LINE",
                                   conversation_id=self.tab_a)
        self.tab_b = self.memory.create_conversation("Tab B")
        self.memory.append_message("user", "BRAVO-ONLY-LINE",
                                   conversation_id=self.tab_b)

    def run_greeting(self, fake, **kw):
        fake_stats = SimpleNamespace(load_stat=lambda key: 50.0)
        fake_ja = SimpleNamespace(
            build_voice_context=lambda trust: {"role": "system", "content": "v"})
        with patch.object(self.chat, "get_llm", lambda *a, **k: fake), \
             patch.object(self.chat, "_finalize", lambda pack, llm: pack), \
             patch.object(self.chat, "ja_voice", fake_ja), \
             patch.object(self.chat, "stats", fake_stats):
            return self.chat.generate_greeting(**kw)

    def prompt_text(self, fake):
        return "\n".join(str(m.get("content")) for m in fake.messages)

    def greeting_rows(self):
        return [(r[2], r[4]) for r in self.rows() if r[5]]

    def test_switch_greeting_lands_in_its_tab_when_user_hops_on(self):
        self.memory.set_active_conversation(self.tab_a)
        fake = _SwitchingLLM(self.memory, switch_to=self.tab_b)
        _pack, _aid, conv_id = self.run_greeting(
            fake, mode="switch", conversation_id=self.tab_a)
        self.assertEqual(conv_id, self.tab_a)
        self.assertEqual(self.greeting_rows(),
                         [("So, about this one.", self.tab_a)])

    def test_startup_greeting_reads_and_stores_the_tab_active_at_start(self):
        self.memory.set_active_conversation(self.tab_a)
        fake = _SwitchingLLM(self.memory, switch_to=self.tab_b)
        _pack, _aid, conv_id = self.run_greeting(fake)
        self.assertEqual(conv_id, self.tab_a)
        self.assertEqual(self.greeting_rows(),
                         [("So, about this one.", self.tab_a)])

    def test_history_comes_from_the_greeted_tab_not_the_active_one(self):
        # The user is already looking at B when A's greeting is written.
        self.memory.set_active_conversation(self.tab_b)
        fake = _SwitchingLLM(self.memory, switch_to=self.tab_b)
        self.run_greeting(fake, mode="switch", conversation_id=self.tab_a)
        history = [m for m in fake.messages if m.get("role") != "system"]
        history_text = "\n".join(str(m.get("content")) for m in history)
        self.assertIn("ALPHA-ONLY-LINE", history_text)
        self.assertNotIn("BRAVO-ONLY-LINE", history_text)

    def test_switch_route_no_longer_changes_the_active_tab(self):
        # The user switched on to B; a late greeting request for A must not
        # yank the active tab back to A.
        self.memory.set_active_conversation(self.tab_b)
        pack = self.chat.AmadeusPack(
            assistant_reply_ENG="Hm.", assistant_reply_JPS="ふむ。")
        with self.model(True), patch.object(
                self.api, "generate_greeting",
                side_effect=lambda **kw: (pack, 7, kw["conversation_id"])):
            res = self.client.post(f"/conversations/{self.tab_a}/greet",
                                   json={"previous_id": self.tab_b})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["conversation_id"], self.tab_a)
        self.assertEqual(self.memory.load_active_conversation(), self.tab_b)

    def test_switch_route_unknown_tab_is_404(self):
        with self.model(True):
            res = self.client.post("/conversations/9999/greet", json={})
        self.assertEqual(res.status_code, 404)

    def test_build_prompt_messages_default_is_still_the_active_tab(self):
        self.memory.set_active_conversation(self.tab_b)
        text = "\n".join(m["content"] for m in self.memory.build_prompt_messages())
        self.assertIn("BRAVO-ONLY-LINE", text)
        self.assertNotIn("ALPHA-ONLY-LINE", text)


if __name__ == "__main__":
    unittest.main()

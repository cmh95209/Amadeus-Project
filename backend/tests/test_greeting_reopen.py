"""Reopening the app over her own unanswered greetings (2026-10-29).

  1. the startup greeting's closing line says how many greetings are still
     unanswered and asks for a different topic and wording (the switch path
     had this already; startup did not, so reopens looped on "were you
     busy?");
  2. her earlier greetings stay visible (she may escalate), the count resets
     once the user writes, and a regenerate's hidden version is not counted;
  3. the SITUATION note states the facts neutrally - no "they read your
     greeting and left without saying anything".
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_greeting_regenerate import _Base, _FakeLLM  # noqa: E402 - shared harness

PLAIN = "[The user just opened the app.]"


class ReopenTests(_Base):
    def greet_text(self):
        fake = _FakeLLM(eng="Another hello.", jps="またこんにちは。")
        result = self.greet(fake)
        return self.prompt_text(fake), result

    def test_no_unanswered_greeting_keeps_the_plain_line(self):
        text, _ = self.greet_text()
        self.assertIn(PLAIN, text)
        self.assertNotIn("with no reply yet", text)

    def test_one_unanswered_greeting(self):
        self.greet()                      # "First hello." - never answered
        text, _ = self.greet_text()
        self.assertIn("greeted them here once with no reply yet", text)
        self.assertIn("Do not reuse the topic or wording", text)
        self.assertIn("First hello.", text)   # still visible: she may escalate
        self.assertNotIn(PLAIN, text)

    def test_count_grows_and_resets_when_the_user_writes(self):
        self.greet()
        self.greet()
        text, _ = self.greet_text()
        self.assertIn("greeted them here 2 times with no reply yet", text)
        self.memory.append_message("user", "sorry, I'm here", conversation_id=self.tab_a)
        text, _ = self.greet_text()
        self.assertIn(PLAIN, text)
        self.assertEqual(self.memory.unanswered_greeting_count(self.tab_a), 1)

    def test_regenerated_version_is_not_counted(self):
        _p, g1, _c = self.greet()
        fake = _FakeLLM()
        self.regen(g1, fake)
        self.assertIn(PLAIN, self.prompt_text(fake))

    def test_fixed_first_launch_lines_do_not_count(self):
        self.memory.append_greeting_if_quiet(
            self.tab_a, "Intro line.", "はじめまして。", None,
            kind=self.memory.GREETING_KIND_SCRIPTED)
        self.assertEqual(self.memory.unanswered_greeting_count(self.tab_a), 0)
        self.greet()
        self.assertEqual(self.memory.unanswered_greeting_count(self.tab_a), 1)

    def test_situation_note_is_neutral(self):
        self.greet()
        text, _ = self.greet_text()
        self.assertIn("there is no reply to it yet", text)
        self.assertIn("This is not a welcome-back moment", text)
        for old in ("left without saying", "never answered it",
                    "left your greeting unanswered"):
            self.assertNotIn(old, text)

    def test_switch_path_is_unchanged(self):
        tab_b = self.memory.create_conversation("Tab B")
        fake = _FakeLLM()
        self.greet(fake, mode="switch", conversation_id=tab_b,
                   previous_conversation_id=self.tab_a)
        text = self.prompt_text(fake)
        self.assertIn("[The user has switched to this conversation.]", text)
        self.assertNotIn("with no reply yet", text)

    def test_unanswered_greetings_raise_her_feelings_once_each(self):
        import feelings
        feelings_mod = feelings
        self.greet()
        self.assertEqual(round(feelings_mod.current()["irritation"]), 0)
        self.greet()                      # over 1 unanswered greeting
        first = feelings_mod.current()["irritation"]
        self.assertGreater(first, 0)
        self.greet()                      # over 2: a bigger sting
        second = feelings_mod.current()["irritation"]
        self.assertGreater(second - first, first - 1)
        self.assertIn("2 greetings got no reply",
                      feelings_mod.current()["irritation_reason"])


if __name__ == "__main__":
    unittest.main()

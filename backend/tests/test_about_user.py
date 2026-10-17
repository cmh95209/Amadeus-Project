# -*- coding: utf-8 -*-
"""Offline checks for the "about you" settings: pronouns + what she calls you.

Pronouns ('' = not set) reach every place a model writes about the user in
the third person; the address setting is either fixed by the user or "let
her decide", which offers forms by trust tier and pronouns (ja_voice
address_rule, user-approved table 2026-10-16). Covers, with no server and no
network (the test_ceremony harness: temp data dir, llm/tts stubbed):
  1. the two files: defaults, validation, caps;
  2. the routes: defaults, round trip, bad input;
  3. the address rule: no name = no rule, the tier x pronoun table, fixed
     and custom choices, the Close teasing note, and the voice block;
  4. the pronoun line in her prompt, the English-ending line, and the
     translation / summary prompts - all absent when nothing is set.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402


class FileTests(_FreshAppTests):
    def test_defaults(self):
        self.assertEqual(self.memory.load_pronouns(), "")
        self.assertEqual(self.memory.load_address(), "auto")
        self.assertEqual(self.memory.pronoun_sentence(), "")

    def test_pronouns_round_trip_and_cap(self):
        self.assertEqual(self.memory.save_pronouns("  she/her "), "she/her")
        self.assertEqual(self.memory.pronoun_sentence(), "The user uses she/her pronouns.")
        self.assertEqual(len(self.memory.save_pronouns("x" * 100)), 40)
        self.memory.save_pronouns("")
        self.assertEqual(self.memory.load_pronouns(), "")

    def test_address_validation(self):
        for value, expected in (("kun", "kun"), ("none", "none"), ("bogus", "auto"),
                                ("", "auto"), ("custom:  アレックスっち ", "custom:アレックスっち"),
                                ("custom:   ", "auto")):
            with self.subTest(value=value):
                self.assertEqual(self.memory.save_address(value), expected)
                self.assertEqual(self.memory.load_address(), expected)

    def test_corrupt_address_file_means_auto(self):
        Path("data").mkdir(exist_ok=True)
        Path("data/user_address.txt").write_text("garbage", encoding="utf-8")
        self.assertEqual(self.memory.load_address(), "auto")


class RouteTests(_FreshAppTests):
    def test_defaults_and_round_trip(self):
        got = self.client.get("/getUserProfile").json
        self.assertEqual((got["pronouns"], got["address"]), ("", "auto"))
        res = self.client.post("/setUserProfile", json={"pronouns": "he/him", "address": "kun"})
        self.assertEqual(res.status_code, 200)
        got = self.client.get("/getUserProfile").json
        self.assertEqual((got["pronouns"], got["address"]), ("he/him", "kun"))

    def test_one_field_leaves_the_other(self):
        self.client.post("/setUserProfile", json={"pronouns": "he/him", "address": "kun"})
        self.client.post("/setUserProfile", json={"address": "auto"})
        self.assertEqual(self.memory.load_pronouns(), "he/him")

    def test_bad_input_is_400(self):
        for body in ({}, {"other": 1}, {"pronouns": 5}, {"address": None}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/setUserProfile", json=body).status_code, 400)


class AddressRuleTests(_FreshAppTests):
    def setUp(self):
        super().setUp()
        sys.modules.pop("ja_voice", None)
        import ja_voice
        self.ja_voice = ja_voice

    def _named(self, pronouns="", address="auto"):
        self.memory.save_username("Alex")
        self.memory.save_username_reading("アレックス")
        self.memory.save_pronouns(pronouns)
        self.memory.save_address(address)

    def test_no_name_no_rule(self):
        self.assertEqual(self.ja_voice.address_rule("Close"), "")

    def test_tier_table(self):
        cases = [
            ("he/him", "Distant", ["アレックスさん"], ["アレックスくん"]),
            ("he/him", "Warming up", ["アレックスさん", "アレックスくん"], ["アレックスちゃん"]),
            ("he/him", "Familiar", ["アレックスくん / アレックス."], ["アレックスさん"]),
            ("she/her", "Warming up", ["アレックスさん / アレックスちゃん"], ["アレックスくん"]),
            ("she/her", "Familiar", ["アレックスちゃん / アレックス."], ["くん"]),
            ("they/them", "Warming up", ["call them: アレックスさん."], ["くん", "ちゃん"]),
            ("", "Familiar", ["call them: アレックス."], ["くん", "ちゃん", "さん"]),
            ("xe/xem", "Close", ["call them: アレックス."], ["teasing"]),
        ]
        for pronouns, tier, present, absent in cases:
            with self.subTest(pronouns=pronouns, tier=tier):
                self._named(pronouns)
                rule = self.ja_voice.address_rule(tier)
                for text in present:
                    self.assertIn(text, rule)
                for text in absent:
                    self.assertNotIn(text, rule)

    def test_close_teasing_note(self):
        self._named("he/him")
        self.assertIn("Only when teasing them, アレックスくん", self.ja_voice.address_rule("Close"))
        self._named("she/her")
        self.assertIn("Only when teasing them, アレックスちゃん", self.ja_voice.address_rule("Close"))

    def test_fixed_and_custom(self):
        self._named("he/him", "chan")
        self.assertIn("always call them アレックスちゃん", self.ja_voice.address_rule("Distant"))
        self._named("", "none")
        self.assertIn("always call them アレックス -", self.ja_voice.address_rule("Close"))
        self._named("", "custom:アレっち")
        self.assertIn("always call them アレっち", self.ja_voice.address_rule("Distant"))

    def test_name_without_reading_uses_the_name(self):
        self.memory.save_username("Alex")
        self.assertIn("call them: Alexさん.", self.ja_voice.address_rule("Distant"))

    def test_voice_block_carries_rule_only_with_a_name(self):
        with patch.object(self.ja_voice, "load_voice_block", return_value="BLOCK"):
            plain = self.ja_voice.build_voice_context(50)["content"]
            self.assertNotIn("ADDRESSING THE USER", plain)
            self._named("he/him")
            named = self.ja_voice.build_voice_context(50)["content"]
        self.assertIn("ADDRESSING THE USER", named)
        self.assertTrue(named.endswith("BLOCK"))

    def test_voice_block_asks_for_katakana_foreign_words(self):
        # Voice layer 3: prevention in the prompt (speech_text is the net).
        with patch.object(self.ja_voice, "load_voice_block", return_value="BLOCK"):
            content = self.ja_voice.build_voice_context(50)["content"]
        self.assertIn("FOREIGN WORDS:", content)
        self.assertIn("Python -> パイソン", content)

    def test_rule_failure_never_breaks_the_block(self):
        with patch.object(self.ja_voice, "load_voice_block", return_value="BLOCK"), \
             patch.object(self.ja_voice, "address_rule", side_effect=RuntimeError("x")):
            self.assertIn("BLOCK", self.ja_voice.build_voice_context(50)["content"])


class PromptHintTests(_FreshAppTests):
    def test_internal_context_lines(self):
        base = self.memory.load_internal_context()["content"]
        self.assertNotIn("pronouns", base)
        self.memory.save_username("Alex")
        self.memory.save_pronouns("they/them")
        text = self.memory.load_internal_context()["content"]
        self.assertIn("keep it in the English line too (Alex-kun, Alex-san)", text)
        self.assertIn("The user uses they/them pronouns. Use them whenever", text)

    def test_user_hint_lines(self):
        self.assertEqual(self.chat._user_hint_lines(), [])
        self.memory.save_username("Alex")
        self.memory.save_username_reading("アレックス")
        self.memory.save_pronouns("he/him")
        hints = self.chat._user_hint_lines()
        self.assertIn("(written アレックス in Japanese)", hints[0])
        self.assertIn("Alex-kun", hints[0])
        self.assertEqual(hints[1], "The user uses he/him pronouns.")

    def test_translation_prompt_carries_hints(self):
        self.memory.save_username("Alex")
        self.memory.save_username_reading("アレックス")
        seen = []
        fake = SimpleNamespace(invoke=lambda messages: seen.extend(messages)
                               or SimpleNamespace(content="Welcome back, Alex-kun."))
        out = self.chat._translate_reply_en(fake, "おかえり、アレックスくん。", [])
        self.assertEqual(out, "Welcome back, Alex-kun.")
        self.assertIn("Write it as Alex in English", seen[0]["content"])

    def test_summary_prompt_carries_pronouns_only_when_set(self):
        seen = []
        fake = SimpleNamespace(invoke=lambda messages: seen.extend(messages)
                               or SimpleNamespace(content="summary"))
        with patch.object(self.chat, "get_llm", return_value=fake):
            self.chat._merge_summary_llm("", "USER: hi")
            self.assertNotIn("pronouns", seen[-1]["content"])
            self.memory.save_pronouns("she/her")
            self.chat._merge_summary_llm("", "USER: hi")
        self.assertIn("The user uses she/her pronouns. Use them when the summary",
                      seen[-1]["content"])


if __name__ == "__main__":
    unittest.main()

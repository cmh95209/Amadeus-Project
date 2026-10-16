# -*- coding: utf-8 -*-
"""Offline checks for voice layer 2: how she SAYS the user's name.

The voice engine reads a Latin name ("Alex") with its English model. A kana
reading saved next to the name ("アレックス") is (a) handed to her prompt so she
writes it in her Japanese line and (b) swapped in by the TTS rewriter
(covered in test_speech_text.py). Covers, with no server and no network
(the test_ceremony harness: temp data dir, llm/tts stubbed):
  1. the reading file: absent = unset, save/load strips, '' clears;
  2. the routes: round trip, a changed name drops the old reading unless a
     new one is sent, bad input is a 400;
  3. /suggestUsernameReading: rule readings with no model call, one model
     call otherwise, non-kana answers and failures give '', an existing
     reading is never overwritten;
  4. the prompt line and the wake instruction carry the reading only when
     it is set (prompts stay byte-identical without it).
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402


class ReadingFileTests(_FreshAppTests):
    def test_absent_means_unset(self):
        self.assertEqual(self.memory.load_username_reading(), "")

    def test_save_load_strips_and_empty_clears(self):
        self.assertEqual(self.memory.save_username_reading(" アレックス "), "アレックス")
        self.assertEqual(self.memory.load_username_reading(), "アレックス")
        self.memory.save_username_reading("")
        self.assertEqual(self.memory.load_username_reading(), "")


class RouteTests(_FreshAppTests):
    def test_round_trip_with_reading(self):
        res = self.client.post("/setUsername", json={"username": "Alex", "reading": "アレックス"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["reading"], "アレックス")
        got = self.client.get("/getUsername").json
        self.assertEqual((got["username"], got["reading"]), ("Alex", "アレックス"))

    def test_same_name_without_reading_keeps_it(self):
        # Older clients send only the name: an unchanged name keeps its reading.
        self.client.post("/setUsername", json={"username": "Alex", "reading": "アレックス"})
        res = self.client.post("/setUsername", json={"username": "Alex"})
        self.assertEqual(res.json["reading"], "アレックス")

    def test_changed_name_without_reading_clears_it(self):
        self.client.post("/setUsername", json={"username": "Alex", "reading": "アレックス"})
        res = self.client.post("/setUsername", json={"username": "Sam"})
        self.assertEqual(res.json["reading"], "")
        self.assertEqual(self.memory.load_username_reading(), "")

    def test_bad_reading_is_400(self):
        res = self.client.post("/setUsername", json={"username": "Alex", "reading": 5})
        self.assertEqual(res.status_code, 400)


class SuggestTests(_FreshAppTests):
    def _fake(self, answer=None, error=None):
        calls = []

        def invoke(messages):
            calls.append(messages)
            if error:
                raise error
            return SimpleNamespace(content=answer)
        return SimpleNamespace(invoke=invoke), calls

    def _suggest(self, fake):
        with patch.object(self.chat, "get_llm", return_value=fake):
            return self.client.post("/suggestUsernameReading").json["reading"]

    def test_model_reading_is_saved(self):
        self.memory.save_username("Alex")
        fake, calls = self._fake("「アレックス」")
        self.assertEqual(self._suggest(fake), "アレックス")
        self.assertEqual(len(calls), 1)
        self.assertIn("Alex", calls[0][0]["content"])
        self.assertEqual(self.memory.load_username_reading(), "アレックス")

    def test_initials_need_no_model_call(self):
        self.memory.save_username("JD")
        fake, calls = self._fake("unused")
        self.assertEqual(self._suggest(fake), "ジェーディー")
        self.assertEqual(calls, [])

    def test_non_kana_answer_or_failure_gives_empty(self):
        self.memory.save_username("Alex")
        for fake, _ in (self._fake("Sure! It is アレックス."),
                        self._fake(error=RuntimeError("server down"))):
            with self.subTest():
                self.assertEqual(self._suggest(fake), "")
                self.assertEqual(self.memory.load_username_reading(), "")

    def test_existing_reading_is_not_overwritten(self):
        self.memory.save_username("Alex")
        self.memory.save_username_reading("ケン")
        fake, calls = self._fake("アレックス")
        self.assertEqual(self._suggest(fake), "ケン")
        self.assertEqual(calls, [])

    def test_no_name_does_nothing(self):
        fake, calls = self._fake("アレックス")
        self.assertEqual(self._suggest(fake), "")
        self.assertEqual(calls, [])


class PromptTests(_FreshAppTests):
    def test_prompt_line_carries_reading_only_when_set(self):
        self.memory.save_username("Alex")
        without = self.memory.load_internal_context()["content"]
        self.assertIn("The user goes by Alex", without)
        self.assertNotIn("write their name as", without)
        self.memory.save_username_reading("アレックス")
        with_reading = self.memory.load_internal_context()["content"]
        self.assertIn("In your Japanese line, write their name as アレックス.", with_reading)

    def test_wake_instruction_carries_reading(self):
        self.memory.save_username("Alex")
        self.memory.save_username_reading("アレックス")
        seen = []

        class _Bound:
            def invoke(self, messages):
                seen.extend(messages)
                return SimpleNamespace(content="", tool_calls=[{
                    "name": "AmadeusPack", "id": "w1",
                    "args": {"assistant_reply_JPS": "おはよう。",
                             "assistant_reply_ENG": "Morning."}}])
        fake = SimpleNamespace(bind_tools=lambda tools, tool_choice=None: _Bound())
        with patch.object(self.chat, "get_llm", return_value=fake), \
             patch.object(self.chat, "reset_llm"), \
             patch.object(self.chat, "_finalize", lambda pack, llm: pack), \
             patch.object(self.chat, "stats",
                          SimpleNamespace(load_stat=lambda key: 62.0)):
            self.chat.generate_greeting(mode="wake")
        system = [m["content"] for m in seen if m["role"] == "system"]
        self.assertIn("they go by Alex (written アレックス in Japanese)", system[0])


if __name__ == "__main__":
    unittest.main()

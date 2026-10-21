# -*- coding: utf-8 -*-
"""Offline checks for plain-text replies on LOCAL models (2026-10-21): her
built-in model / the user's own server answers her Japanese line as plain
text instead of the reply form (reliability test: form 9-30/30 failures per
model, plain text 0-3/30). Cloud keeps the form (test_voice_first covers it).
  1. normal message: plain text, no form, plain-text rules in the prompt;
  2. an unusable plain reply gets ONE retry, then the honest line;
  3. regenerate (getResponsePacked, web off): Japanese line + translated
     English line, no form;
  4. greetings: the same two steps;
  5. translation passes never translate nothing, and reject a request
     ("Please provide the Japanese text...") as a translation.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
for _p in (BACKEND, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import chat  # noqa: E402
import memory as store  # noqa: E402
from chat import AmadeusPack  # noqa: E402
import test_voice_first as tvf  # noqa: E402
_FakeLLM, _fake_context = tvf._FakeLLM, tvf._fake_context

JA = "今日はいい天気ね。散歩でもする？"
EN = "Nice weather today. Want to go for a walk?"
LOCAL = "http://127.0.0.1:8091/v1"


class _LocalFake(_FakeLLM):
    openai_api_base = LOCAL

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.invoked = []

    def invoke(self, messages):
        self.invoked.append(messages)
        return super().invoke(messages)


class PlainReplyTests(unittest.TestCase):
    _patches = tvf.VoiceFirstTests._patches
    _run = tvf.VoiceFirstTests._run

    def test_normal_message_is_plain_text(self):
        fake = _LocalFake()
        fake, pack, exc, _ = self._run(fake, plain_script=[JA], call="response")
        self.assertIsNone(exc)
        self.assertEqual(pack.assistant_reply_JPS, JA)
        self.assertEqual((fake.structured_calls, fake.forced_calls), (0, 0))
        prompt = "\n".join(m.get("content", "") for m in fake.invoked[0])
        self.assertIn("as plain text", prompt)
        self.assertNotIn("assistant_reply_JPS", prompt)

    def test_label_and_quotes_are_removed(self):
        fake = _LocalFake()
        _, pack, exc, _ = self._run(fake, plain_script=["JA: 「" + JA + "」"], call="response")
        self.assertIsNone(exc)
        self.assertEqual(pack.assistant_reply_JPS, JA)

    def test_one_retry_then_good(self):
        fake = _LocalFake()
        _, pack, exc, _ = self._run(fake, plain_script=["", JA], call="response")
        self.assertIsNone(exc)
        self.assertEqual(pack.assistant_reply_JPS, JA)
        self.assertEqual(fake.plain_calls, 2)

    def test_two_unusable_replies_give_the_honest_line(self):
        fake = _LocalFake()
        loop = "<|tool_call>call:AmadeusReply{" * 3
        _, pack, exc, _ = self._run(fake, plain_script=[loop, "Sure, here you go."], call="response")
        self.assertIsNone(exc)
        self.assertEqual(pack.assistant_reply_JPS, chat._honest_plain_failure_pack().assistant_reply_JPS)
        self.assertEqual(fake.plain_calls, 2)

    def test_regenerate_path_two_steps(self):
        fake = _LocalFake()
        with tempfile.TemporaryDirectory() as directory:
            patches = self._patches(fake, str(Path(directory) / "memory.db"))
            fake.plain_script = [JA, EN]
            for p in patches:
                p.start()
            try:
                pack = chat.getResponsePacked(_fake_context(),
                                              internal_context={"role": "system", "content": "TIMING"})
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual((pack.assistant_reply_JPS, pack.assistant_reply_ENG), (JA, EN))
        self.assertEqual((fake.structured_calls, fake.forced_calls), (0, 0))
        self.assertEqual(fake.plain_calls, 2)


class _GreetFake:
    openai_api_base = LOCAL

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.tools = 0

    def bind_tools(self, *a, **k):
        self.tools += 1
        raise AssertionError("the reply form must not be used on a local model")

    def invoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.script.pop(0))


class GreetingTests(unittest.TestCase):
    def test_local_greeting_is_plain_text_plus_translation(self):
        fake = _GreetFake(["おかえり。今日は何をしてたの？", "Welcome back. What were you up to today?"])
        fake_ja = SimpleNamespace(build_voice_context=lambda trust: {"role": "system", "content": "VOICE"})
        with tempfile.TemporaryDirectory() as directory:
            mem_db = str(Path(directory) / "memory.db")
            with patch.object(chat, "get_llm", lambda *a, **k: fake), \
                 patch.object(chat, "reset_llm", lambda: None), \
                 patch.object(chat, "_finalize", lambda pack, llm: pack), \
                 patch.object(chat, "ja_voice", fake_ja), \
                 patch.object(chat, "stats", SimpleNamespace(load_stat=lambda key: 30.0)), \
                 patch.object(chat, "cm_bridge", SimpleNamespace(fetch_memory_block=lambda *a, **k: None)), \
                 patch.object(store, "PATH_TO_MEMORY", mem_db), \
                 patch.object(store, "load_default_personality_messages",
                              return_value=[{"role": "system", "content": "PERSONALITY"}]), \
                 patch.object(store, "build_prompt_messages",
                              return_value=[{"role": "user", "content": "Hi"},
                                            {"role": "assistant", "content": "やあ。"}]), \
                 patch.object(store, "load_active_conversation", return_value=1):
                pack, _aid, _cid = chat.generate_greeting()
            sqlite3.connect(mem_db).close()
        self.assertEqual(pack.assistant_reply_JPS, "おかえり。今日は何をしてたの？")
        self.assertEqual(pack.assistant_reply_ENG, "Welcome back. What were you up to today?")
        self.assertEqual(fake.tools, 0)


class TranslationGuardTests(unittest.TestCase):
    class _Never:
        def invoke(self, messages):
            raise AssertionError("must not translate an empty line")

    def test_nothing_is_never_translated(self):
        pack = chat._ensure_english(AmadeusPack(assistant_reply_JPS="", assistant_reply_ENG=""), self._Never())
        self.assertEqual(pack.assistant_reply_ENG, "")
        self.assertEqual(chat._ensure_japanese_jps("…", self._Never()), "…")
        self.assertEqual(chat._translate_reply_en(self._Never(), " 。", []), "")

    def test_a_request_is_not_a_translation(self):
        ask = SimpleNamespace(invoke=lambda m: SimpleNamespace(
            content="Please provide the Japanese text you would like me to translate."))
        pack = chat._ensure_english(AmadeusPack(assistant_reply_JPS=JA, assistant_reply_ENG=""), ask)
        self.assertEqual(pack.assistant_reply_ENG, JA)  # her line, not the request
        self.assertEqual(chat._translate_reply_en(ask, JA, []), "")


if __name__ == "__main__":
    unittest.main()

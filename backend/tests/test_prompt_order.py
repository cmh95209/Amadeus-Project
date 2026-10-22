# -*- coding: utf-8 -*-
"""Offline checks for prompt order A (2026-10-22).

A local server (and cloud prompt caching) reuses a prompt only up to the first
changed byte, so her prompt must START with the blocks that never change:
  1. the voice rules carry no trust / tier / address lines (those change);
     the closeness block carries them;
  2. two replies with different memories, time, trust and history share the
     whole fixed start (personality, voice rules, reply rules, web rules), and
     the changing blocks all come after it - on the normal and the
     voice-first reply paths.
All LLM calls and the stores are stubbed; no network, no live data.
"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cm_bridge
import chat
import memory as store
import stats
import ja_voice

_PERSONALITY = {"role": "system", "content": "PERSONALITY"}


class _FakeStructured:
    def __init__(self, sink):
        self._sink = sink

    def invoke(self, messages):
        self._sink["messages"] = messages
        return chat.AmadeusPack(assistant_reply_JPS="こんにちは",
                                assistant_reply_ENG="Hello.")


class _FakeLLM:
    def __init__(self, sink):
        self._sink = sink

    def with_structured_output(self, schema, method=None):
        return _FakeStructured(self._sink)


def _voice_patches():
    return [mock.patch.object(ja_voice, "load_voice_block", lambda: "VOICE RULES"),
            mock.patch.object(ja_voice, "address_rule", lambda tier: "ADDRESS " + tier)]


class VoiceSplitTests(unittest.TestCase):
    def test_rules_fixed_closeness_changes(self):
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            rules = ja_voice.build_voice_rules()["content"]
            low = ja_voice.build_closeness_context(10)["content"]
            high = ja_voice.build_closeness_context(90)["content"]
        self.assertNotIn("trust", rules.split("FOREIGN WORDS")[0])
        self.assertNotIn("ADDRESS", rules)
        self.assertTrue(rules.endswith("VOICE RULES"))
        self.assertIn("trust 10 out of 100", low)
        self.assertIn("trust 90 out of 100", high)
        self.assertIn("ADDRESS", low)
        combined = ja_voice.build_voice_context  # kept: closeness + rules in one
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            self.assertEqual(combined(10)["content"], low + "\n\n" + rules)


class PromptOrderTests(unittest.TestCase):
    def _run(self, fn, trust, memory_text, timing, history):
        sink = {}
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            for target, name, value in [
                (chat, "get_llm", lambda *a, **k: _FakeLLM(sink)),
                (store, "load_web_access", lambda: False),
                (store, "load_character_book_messages", lambda _last: []),
                (store, "load_default_personality_messages", lambda: [dict(_PERSONALITY)]),
                (store, "load_active_conversation", lambda: 1),
                (stats, "load_stat", lambda _name: trust),
                (chat, "_finalize", lambda p, llm: p),
                (chat, "_web_off_honesty_net", lambda p: p),
                (cm_bridge, "fetch_memory_block",
                 lambda _cid: {"role": "system", "content": memory_text}),
            ]:
                stack.enter_context(mock.patch.object(target, name, value))
            fn(list(history), internal_context={"role": "system", "content": timing})
        return sink["messages"]

    def _check(self, fn, rules):
        one = self._run(fn, 20, "MEMORY tea", "TIMING 10:00",
                        [{"role": "user", "content": "hi"}])
        two = self._run(fn, 80, "MEMORY rain", "TIMING 11:30",
                        [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": "やあ"},
                         {"role": "user", "content": "how are you?"}])
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            fixed = "\n\n".join(["PERSONALITY", ja_voice.build_voice_rules()["content"],
                                 rules["content"], chat.NO_WEB_BLOCK["content"]])
        for msgs, memory_text, timing in [(one, "MEMORY tea", "TIMING 10:00"),
                                          (two, "MEMORY rain", "TIMING 11:30")]:
            system = msgs[0]["content"]
            self.assertEqual([m["role"] for m in msgs].count("system"), 1)
            self.assertTrue(system.startswith(fixed + "\n\nCLOSENESS"))
            rest = system[len(fixed):]
            self.assertLess(rest.index("CLOSENESS"), rest.index(memory_text))
            self.assertLess(rest.index(memory_text), rest.index(timing))
            self.assertTrue(system.endswith(timing))

    def test_normal_reply(self):
        self._check(chat.getResponsePacked, chat._PACK_RULES)

    def test_voice_first_reply(self):
        self._check(chat.getResponsePackedVoiceFirst, chat._PACK_RULES_JA_ONLY)


if __name__ == "__main__":
    unittest.main()

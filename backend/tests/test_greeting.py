# -*- coding: utf-8 -*-
"""Offline checks for the startup greeting (2026-09-20).

No server, no network. A faked LLM (patched over chat.get_llm) answers the
forced AmadeusPack call through the SAME bind_tools/_invoke_forced path the
normal reply uses (the NInfer tool_choice auto-retry lives there), and a
temporary SQLite store backs the real append_message so persistence is
asserted end to end. Covers:
  1. generate_greeting() stores EXACTLY ONE assistant line (no fake user
     turn) and returns (pack, assistant_id, conversation_id);
  2. the greeting prompt carries her live personality, the greeting
     instruction, the closeness voice block, the NO_WEB block and recent
     memory - merged into ONE leading system message - and never offers the
     web_search tool;
  3. the forced call offers ONLY the AmadeusPack tool with
     tool_choice="required" (the 2026-09-21 fix: the greeting must use the
     same call ladder as a normal reply, which is what carries NInfer's
     tool_choice auto-retry);
  4. a first-call failure spends exactly ONE bounded retry (fresh client);
  5. an empty reply on both attempts raises the max-output-tokens signal and
     stores nothing;
  6. api.model_ready() gates on the /models probe alone (no LLM call).
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

import api
import chat
import memory as store
from chat import AmadeusPack


GREET_MARK = "The user just opened the app"
PERSONALITY_MARK = "GREETING_TEST_PERSONALITY"
VOICELINE_MARK = "VOICELINE_BLOCK"


class FakeReply:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_chunks = None


def pack_call(eng="Welcome back.", jps="おかえり。"):
    return [{"name": "AmadeusPack",
             "args": {"assistant_reply_JPS": jps, "assistant_reply_ENG": eng}}]


class _FakeBound:
    def __init__(self, handler):
        self._handler = handler

    def invoke(self, messages):
        return self._handler(messages)


class _FakeLLM:
    """Stands in for get_llm(): bind_tools(...).invoke(messages) - the exact
    surface generate_greeting() uses via _invoke_forced - records the prompt
    and answers with a canned AmadeusPack tool call (or raises per the
    attempt script)."""

    def __init__(self, script=None, eng="Welcome back.", jps="おかえり。"):
        self.script = list(script) if script else []
        self.eng = eng
        self.jps = jps
        self.calls = 0
        self.last_messages = None
        self.last_tool_choice = None
        self.last_tool_names = None

    def bind_tools(self, tools, tool_choice=None):
        self.last_tool_choice = tool_choice
        self.last_tool_names = [t.get("function", {}).get("name")
                                if isinstance(t, dict) else None
                                for t in tools]
        return _FakeBound(self._invoke)

    def _invoke(self, messages):
        self.calls += 1
        self.last_messages = messages
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step  # a FakeReply
        return FakeReply(tool_calls=pack_call(self.eng, self.jps))


def _fake_store_messages():
    return [
        {"role": "user", "content": "What did you end up deciding?"},
        {"role": "assistant", "content": "Decided to take the night train."},
    ]


class GreetingGenerationTests(unittest.TestCase):
    def _run(self, script=None):
        """Run the REAL generate_greeting() against a faked LLM and a temp
        SQLite store. Returns (fake_llm, result_or_exc, rows, reset_calls,
        mem_db)."""
        fake = _FakeLLM(script=script)
        reset_calls = []
        fake_ja = SimpleNamespace(build_voice_context=lambda trust: {
            "role": "system", "content": VOICELINE_MARK + " (trust 62)"})
        fake_stats = SimpleNamespace(load_stat=lambda key: 62.0)
        with tempfile.TemporaryDirectory() as directory:
            mem_db = str(Path(directory) / "memory.db")
            with patch.object(chat, "get_llm", lambda *a, **k: fake), \
                 patch.object(chat, "reset_llm", side_effect=lambda: reset_calls.append(1)), \
                 patch.object(chat, "_finalize", lambda pack, llm: pack), \
                 patch.object(chat, "ja_voice", fake_ja), \
                 patch.object(chat, "stats", fake_stats), \
                 patch.object(store, "PATH_TO_MEMORY", mem_db), \
                 patch.object(store, "load_default_personality_messages",
                              return_value=[{"role": "system",
                                             "content": PERSONALITY_MARK}]), \
                 patch.object(store, "build_prompt_messages",
                              return_value=_fake_store_messages()), \
                 patch.object(store, "load_active_conversation", return_value=1):
                try:
                    result = chat.generate_greeting()
                    exc = None
                except Exception as e:
                    result, exc = None, e
            conn = sqlite3.connect(mem_db)
            try:
                # Ensure the schema exists even when nothing was stored
                # (the table is created lazily on first append).
                c = conn.cursor()
                store._ensure_messages_table(c)
                conn.commit()
                rows = conn.execute(
                    "SELECT id, role, content, japanese, conversation_id "
                    "FROM messages ORDER BY id").fetchall()
            finally:
                conn.close()
        return fake, result, exc, rows, reset_calls, mem_db

    def test_stores_one_assistant_line_no_fake_user_turn(self):
        fake, result, exc, rows, reset_calls, _ = self._run()
        self.assertIsNone(exc)
        pack, assistant_id, conv_id = result
        self.assertEqual(pack.assistant_reply_ENG, "Welcome back.")
        self.assertEqual(pack.assistant_reply_JPS, "おかえり。")
        self.assertEqual(conv_id, 1)
        # EXACTLY ONE row: her assistant line (stored in her voice), and NO
        # invented user turn.
        self.assertEqual(len(rows), 1)
        row_id, role, content, japanese, conv = rows[0]
        self.assertEqual(role, "assistant")
        self.assertEqual(content, "Welcome back.")
        self.assertEqual(japanese, "おかえり。")
        self.assertEqual(conv, 1)
        self.assertEqual(assistant_id, row_id)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(reset_calls, [])

    def test_prompt_carries_personality_instruction_and_no_web(self):
        fake, _result, exc, _rows, _reset, _db = self._run()
        self.assertIsNone(exc)
        messages = fake.last_messages
        self.assertIsNotNone(messages)
        # All leading system blocks are merged into ONE system message.
        system_msgs = [m for m in messages if m["role"] == "system"]
        self.assertEqual(len(system_msgs), 1)
        content = system_msgs[0]["content"]
        self.assertIn(PERSONALITY_MARK, content)               # live personality
        self.assertIn(GREET_MARK, content)                     # the loose instruction
        self.assertIn(VOICELINE_MARK, content)                 # closeness voice block
        self.assertIn("WEB ACCESS IS CURRENTLY OFF", content)  # NO_WEB_BLOCK
        self.assertIn("Write only Amadeus's spoken dialogue", content)  # pack rules
        self.assertNotIn("PROACTIVE WEB SEARCH", content)      # search never offered
        # Recent memory follows as ordinary turns, and the prompt always
        # ends on the synthetic arrival line (prompt-only, never stored in
        # memory - it only exists so servers accept "her turn to speak").
        self.assertEqual(messages[-3:], [
            {"role": "user", "content": "What did you end up deciding?"},
            {"role": "assistant", "content": "Decided to take the night train."},
            {"role": "user", "content": "[The user just opened the app.]"}])

    def test_forced_call_offers_only_amadeuspack_tool(self):
        # Regression anchor for the 2026-09-21 NInfer fix: the greeting must
        # go through the same forced-call ladder as a normal reply (which is
        # what carries the tool_choice="required" -> "auto" auto-retry).
        fake, _result, exc, _rows, _reset, _db = self._run()
        self.assertIsNone(exc)
        self.assertEqual(fake.last_tool_names, ["AmadeusPack"])
        self.assertEqual(fake.last_tool_choice, "required")

    def test_bounded_retry_after_first_failure(self):
        first_failure = RuntimeError("simulated dropped connection")
        fake, result, exc, rows, reset_calls, _ = self._run(script=[first_failure])
        self.assertIsNone(exc)
        self.assertEqual(fake.calls, 2)          # one bounded retry
        self.assertEqual(len(reset_calls), 1)    # fresh client before the retry
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "assistant")

    def test_empty_reply_raises_signal_and_stores_nothing(self):
        # Both attempts come back with no tool call and no text (the clip
        # shape) -> the max-output-tokens signal, and nothing enters memory.
        empty = FakeReply(content="")
        fake, _result, exc, rows, reset_calls, _ = self._run(script=[empty, empty])
        self.assertIsNotNone(exc)
        self.assertIn("max-output-tokens", str(exc))
        self.assertEqual(fake.calls, 2)
        self.assertEqual(len(rows), 0)           # nothing entered memory


class ModelReadyGateTests(unittest.TestCase):
    """api.model_ready() is a pure /models probe - no LLM generation."""

    def _probe(self, reachable, models, error=None):
        return {"reachable": reachable, "models": models, "error": error}

    def _patches(self, probe):
        return (
            patch.object(api.llm, "test_server", return_value=probe),
            patch.object(api.memory, "load_llm_server", return_value=""),
            patch.object(api.llm, "_server_url",
                         return_value="http://127.0.0.1:8080/v1"),
        )

    def test_unreachable_server_not_ready(self):
        p1, p2, p3 = self._patches(self._probe(False, [], "ConnectionError: ..."))
        with p1, p2, p3, patch.object(api, "getLLMModel", return_value="qwen3.8-27b"):
            out = api.model_ready()
        self.assertFalse(out["ready"])
        self.assertIn("unreachable", out["reason"])

    def test_model_not_served_not_ready(self):
        p1, p2, p3 = self._patches(self._probe(True, ["other-model"]))
        with p1, p2, p3, patch.object(api, "getLLMModel", return_value="qwen3.8-27b"):
            out = api.model_ready()
        self.assertFalse(out["ready"])
        self.assertIn("not served", out["reason"])

    def test_no_model_configured_not_ready(self):
        p1, p2, p3 = self._patches(None)
        with p1, p2, p3, patch.object(api, "getLLMModel", return_value=""):
            out = api.model_ready()
        self.assertFalse(out["ready"])

    def test_ready_when_served(self):
        p1, p2, p3 = self._patches(self._probe(True, ["unsloth/qwen3.8-27b"]))
        with p1, p2, p3, patch.object(api, "getLLMModel", return_value="qwen3.8-27b"):
            out = api.model_ready()
        self.assertTrue(out["ready"])
        self.assertEqual(out["reason"], "")


if __name__ == "__main__":
    unittest.main()

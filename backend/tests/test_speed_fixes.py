# -*- coding: utf-8 -*-
"""Offline checks for the first speed fixes (local-llm-plan §10, 2026-10-20):
  1. her reply format on a LOCAL server is asked with tool_choice "required"
     (llama-server rejects the named-function form, so every reply used to be
     written twice); cloud keeps with_structured_output;
  2. a reply package whose field holds Gemma's tool-call text or a long loop
     is recognised as unusable; normal replies are not;
  3. her built-in model's output cap is lower than the cloud's.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from langchain_core.messages import AIMessage  # noqa: E402

import chat  # noqa: E402
import llm  # noqa: E402
from chat import AmadeusPack, AmadeusReply  # noqa: E402


class _Bound:
    def __init__(self, reply):
        self.reply = reply
        self.invoked = 0

    def invoke(self, convo):
        self.invoked += 1
        return self.reply


class _Client:
    def __init__(self, base, reply=None):
        self.openai_api_base = base
        self.reply = reply
        self.bind_kwargs = None
        self.structured = 0

    def bind_tools(self, tools, **kwargs):
        self.tools = tools
        self.bind_kwargs = kwargs
        self.bound = _Bound(self.reply)
        return self.bound

    def with_structured_output(self, schema, **kwargs):
        self.structured += 1
        return _Bound(schema(assistant_reply_JPS="クラウド"))


def _call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "1"}])


class StructuredRequestTests(unittest.TestCase):
    def test_local_server_asks_required_once(self):
        client = _Client("http://127.0.0.1:8091/v1", _call("AmadeusReply", {"assistant_reply_JPS": "こんにちは"}))
        out = chat._structured(client, AmadeusReply, [{"role": "user", "content": "hi"}])
        self.assertEqual(out.assistant_reply_JPS, "こんにちは")
        self.assertEqual(client.bind_kwargs.get("tool_choice"), "required")
        self.assertEqual(client.bound.invoked, 1)
        self.assertEqual(client.structured, 0)

    def test_lan_server_counts_as_local(self):
        self.assertTrue(llm.is_local_server(_Client("http://192.168.0.244:8080/v1")))
        # every private range, not only 172.16.x (Docker / WSL / routers: 172.17-172.31)
        for url in ("http://172.20.5.2:8080/v1", "http://10.0.0.7:1234/v1", "http://[::1]:8080/v1",
                    "http://my-pc.local:8080/v1", "http://localhost:8888/v1"):
            self.assertTrue(llm.is_local_server(_Client(url)), url)
        for url in ("https://openrouter.ai/api/v1", "http://172.32.0.1:8080/v1", "http://8.8.8.8/v1"):
            self.assertFalse(llm.is_local_server(_Client(url)), url)

    def test_local_server_without_a_call_gives_none(self):
        client = _Client("http://localhost:8091/v1", AIMessage(content="ただのテキスト"))
        self.assertIsNone(chat._structured(client, AmadeusReply, []))

    def test_cloud_unchanged(self):
        client = _Client("https://openrouter.ai/api/v1")
        out = chat._structured(client, AmadeusReply, [])
        self.assertEqual(client.structured, 1)
        self.assertIsNone(client.bind_kwargs)
        self.assertEqual(out.assistant_reply_JPS, "クラウド")


class DegenerateReplyTests(unittest.TestCase):
    LOOP = "<|tool_call>call:AmadeusPack{assistant_reply_ENG:\n" * 200

    def test_tool_call_text_inside_the_package(self):
        pack = AmadeusPack(assistant_reply_JPS="", assistant_reply_ENG=self.LOOP)
        self.assertIn("tool-call", chat._degenerate_pack(pack))
        self.assertTrue(chat._degenerate_field("はい。<|tool_call>call:AmadeusReply{"))

    def test_long_loop_without_markup(self):
        looped = "それは面白いデータね、もう一度確認するわ。" * 40
        self.assertIn("loop", chat._degenerate_field(looped))

    def test_normal_replies_pass(self):
        jps = ("時間旅行ね。相対性理論で言えば、未来へ進むことはもう証明されているわ。"
               "でも過去へ戻るとなると、因果律の矛盾をどう扱うかが問題になるの。") * 2
        pack = AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG="Time travel, huh? " * 3)
        self.assertEqual(chat._degenerate_pack(pack), "")
        self.assertEqual(chat._degenerate_pack({"assistant_reply_JPS": "ええ", "assistant_reply_ENG": None}), "")
        # A short repeated phrase is a style choice, not a loop.
        self.assertEqual(chat._degenerate_field("ふふ、ふふ、ふふ。いいわよ。"), "")


class OutputCapTests(unittest.TestCase):
    def test_builtin_cap_is_lower(self):
        self.assertLess(llm.BUILTIN_MAX_TOKENS, 4096)
        llm.reset_llm()
        with patch.object(llm, "_server_url", return_value="http://127.0.0.1:8091/v1"), \
             patch.object(llm.builtin_llm, "active", return_value=True), \
             patch.object(llm.builtin_llm, "model_name", return_value="amadeus-builtin"):
            client = llm.get_llm("", "x")
        llm.reset_llm()
        self.assertEqual(client.max_tokens, llm.BUILTIN_MAX_TOKENS)


class EmbedderBitsTests(unittest.TestCase):
    """The memory helper (own venv) picks 32-bit only on PCs with plenty of
    memory. Its two small functions are run here on their own (no torch)."""

    def _functions(self, ram):
        import ast
        import os
        source = (BACKEND.parent / "memory_sidecar" / "cm_sidecar.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "_embedder_bits")
                or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "EMBED_32BIT_MIN_RAM_GB")]
        scope = {"os": os, "_total_ram_gb": lambda: ram}
        exec(compile(ast.Module(body=keep, type_ignores=[]), "cm_sidecar.py", "exec"), scope)
        return scope["_embedder_bits"]

    def test_choice(self):
        with patch.dict("os.environ", {"CM_EMBED_BITS": ""}):
            self.assertEqual(self._functions(32.0)(), 32)
            self.assertEqual(self._functions(16.0)(), 16)
            self.assertEqual(self._functions(None)(), 16)
        with patch.dict("os.environ", {"CM_EMBED_BITS": "16"}):
            self.assertEqual(self._functions(64.0)(), 16)


if __name__ == "__main__":
    unittest.main()

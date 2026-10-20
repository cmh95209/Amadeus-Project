# -*- coding: utf-8 -*-
"""Offline checks for Settings -> Reasoning (MH 2026-10-20; replaced the Deep
thinking switch):
  1. the three saved levels, defaults, and the upgrade from Deep thinking ON;
  2. each level becomes the right request for each kind of server (built-in /
     local llama.cpp, OpenRouter, Gemini, other cloud) incl. the output cap;
  3. a server that refuses "reasoning off" gets a softer setting, once;
  4. which calls use which level (her replies vs translations vs helpers);
  5. the routes, the memory helper's learning, the speed log, llama-server.
"""
import ast
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
for _p in (BACKEND, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import builtin_server  # noqa: E402
import llm  # noqa: E402
import memory  # noqa: E402
import timing  # noqa: E402
from test_ceremony import _FreshAppTests  # noqa: E402


class _TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for name, path in (("PATH_TO_REASONING", d / "reasoning.json"),
                           ("PATH_TO_DEEP_THINKING", d / "deep_thinking.txt"),
                           ("DATA_DIR", str(d))):
            p = patch.object(memory, name, str(path))
            p.start()
            self.addCleanup(p.stop)
        self.dir = d


class StorageTests(_TempData):
    def test_round_trip_and_invalid_values(self):
        self.assertEqual(memory.load_reasoning(), {})
        memory.save_reasoning({"replies_custom": "high", "translations": "nonsense", "x": "low"})
        self.assertEqual(memory.load_reasoning(), {"replies_custom": "high"})
        memory.save_reasoning({"translations": "low"})
        self.assertEqual(memory.load_reasoning(), {"replies_custom": "high", "translations": "low"})

    def test_deep_thinking_on_becomes_medium(self):
        (self.dir / "deep_thinking.txt").write_text("1", encoding="utf-8")
        self.assertEqual(memory.load_reasoning(),
                         {"replies_builtin": "medium", "replies_custom": "medium"})
        self.assertTrue((self.dir / "reasoning.json").exists())

    def test_deep_thinking_off_changes_nothing(self):
        (self.dir / "deep_thinking.txt").write_text("0", encoding="utf-8")
        self.assertEqual(memory.load_reasoning(), {})

    def test_defaults_per_brain(self):
        with patch.object(llm, "_configured_server_url", return_value="https://openrouter.ai/api/v1"):
            self.assertEqual(llm.reasoning_levels(),
                             {"replies_builtin": "off", "replies_custom": "low", "translations": "off"})
        with patch.object(llm, "_configured_server_url", return_value="http://192.168.0.244:8080/v1"):
            self.assertEqual(llm.reasoning_levels()["replies_custom"], "off")
        with patch.object(llm, "_configured_server_url", return_value="https://openrouter.ai/api/v1"), \
             patch.object(llm.builtin_llm, "active", return_value=True):
            self.assertEqual(llm.reasoning_for("replies"), "off")
            self.assertEqual(llm.reasoning_for("translations"), "off")
            self.assertEqual(llm.reasoning_for("helper"), "off")
        memory.save_reasoning({"replies_builtin": "low", "translations": "medium"})
        with patch.object(llm, "_configured_server_url", return_value="https://openrouter.ai/api/v1"), \
             patch.object(llm.builtin_llm, "active", return_value=True):
            self.assertEqual(llm.reasoning_for("replies"), "low")
            self.assertEqual(llm.reasoning_for("translations"), "medium")


class RequestTests(unittest.TestCase):
    def _client(self, url, level, builtin=False):
        llm.reset_llm()
        with patch.object(llm, "_server_url", return_value=url), \
             patch.object(llm.builtin_llm, "active", return_value=builtin), \
             patch.object(llm.builtin_llm, "model_name", return_value="amadeus-builtin"), \
             patch.object(memory, "load_sampling", return_value=memory.default_sampling()):
            client = llm.get_llm("k", "m", reasoning=level)
        llm.reset_llm()
        return client

    def test_builtin(self):
        off = self._client("http://127.0.0.1:8091/v1", "off", builtin=True)
        self.assertEqual(off.extra_body, {"chat_template_kwargs": {"enable_thinking": False}})
        self.assertEqual(off.max_tokens, llm.BUILTIN_MAX_TOKENS)
        low = self._client("http://127.0.0.1:8091/v1", "low", builtin=True)
        self.assertEqual(low.extra_body, {"chat_template_kwargs": {"enable_thinking": True},
                                          "thinking_budget_tokens": 512})
        self.assertEqual(low.max_tokens, llm.BUILTIN_MAX_TOKENS + 512)

    def test_openrouter(self):
        self.assertEqual(self._client("https://openrouter.ai/api/v1", "off").extra_body,
                         {"reasoning": {"effort": "minimal"}})
        high = self._client("https://openrouter.ai/api/v1", "high")
        self.assertEqual(high.extra_body, {"reasoning": {"effort": "high"}})
        self.assertGreaterEqual(high.max_tokens, 4096)

    def test_gemini_and_other_cloud(self):
        url = "https://generativelanguage.googleapis.com/v1beta/openai"
        self.assertEqual(self._client(url, "off").extra_body, {"reasoning_effort": "minimal"})
        self.assertEqual(self._client(url, "medium").extra_body, {"reasoning_effort": "medium"})
        self.assertIsNone(self._client("https://api.example.com/v1", "off").extra_body)
        self.assertEqual(self._client("https://api.example.com/v1", "low").extra_body,
                         {"reasoning_effort": "low"})

    def test_old_call_shape(self):
        llm.reset_llm()
        with patch.object(llm, "_server_url", return_value="http://localhost:8888/v1"), \
             patch.object(llm.builtin_llm, "active", return_value=False), \
             patch.object(memory, "load_sampling", return_value=memory.default_sampling()):
            client = llm.get_llm("k", "m", enable_thinking=True)
        llm.reset_llm()
        self.assertEqual(client.extra_body["thinking_budget_tokens"], 1024)


class _Refused(Exception):
    status_code = 400


class FallbackTests(unittest.TestCase):
    MANDATORY = _Refused("Error code: 400 - Reasoning is mandatory for this endpoint and cannot be disabled.")

    def test_ladder(self):
        f = llm._reasoning_fallback
        self.assertEqual(f({"reasoning": {"enabled": False}}, self.MANDATORY), {"reasoning": {"effort": "minimal"}})
        self.assertEqual(f({"reasoning": {"effort": "minimal"}}, self.MANDATORY), {"reasoning": {"effort": "low"}})
        self.assertEqual(f({"reasoning": {"effort": "high"}, "x": 1}, self.MANDATORY), {"x": 1})
        self.assertEqual(f({"reasoning_effort": "minimal"}, self.MANDATORY), {"reasoning_effort": "low"})
        self.assertIsNone(f({"reasoning": {"effort": "minimal"}}, _Refused("bad temperature")))
        other = Exception("Reasoning is mandatory")
        self.assertIsNone(f({"reasoning": {"effort": "minimal"}}, other))  # no 400 status

    def test_client_retries_once_with_the_softer_setting(self):
        client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url="https://openrouter.ai/api/v1",
                                            extra_body={"reasoning": {"effort": "minimal"}})
        calls = []

        def fake(self_, messages, stop=None, run_manager=None, **kw):
            calls.append(dict(self_.extra_body or {}))
            if len(calls) == 1:
                raise FallbackTests.MANDATORY
            return "result"

        with patch.object(llm.ChatOpenAI, "_generate", fake), redirect_stdout(io.StringIO()):
            self.assertEqual(client._generate([]), "result")
        self.assertEqual(calls, [{"reasoning": {"effort": "minimal"}}, {"reasoning": {"effort": "low"}}])
        self.assertEqual(client.extra_body, {"reasoning": {"effort": "low"}})


class WhichCallTests(unittest.TestCase):
    def test_replies_and_translations_levels(self):
        import chat
        seen = []

        def fake_get_llm(key, model, enable_thinking=False, reasoning=None):
            seen.append(reasoning)
            return object()

        levels = {"replies": "low", "translations": "high"}
        with patch.object(chat, "get_llm", fake_get_llm), \
             patch.object(chat._llm_module, "reasoning_for", side_effect=lambda k: levels[k]):
            chat._reply_llm()
            chat._translation_llm()
        self.assertEqual(seen, ["low", "high"])

    def test_no_deep_thinking_paths_left(self):
        src = (BACKEND / "chat.py").read_text(encoding="utf-8")
        self.assertNotIn("load_deep_thinking", src)
        self.assertNotIn("enable_thinking=True", src)


class RouteTests(_FreshAppTests):
    def setUp(self):
        super().setUp()
        p = patch.object(self.api, "llm", llm)
        p.start()
        self.addCleanup(p.stop)
        q = patch.object(llm, "_configured_server_url", return_value="https://openrouter.ai/api/v1")
        q.start()
        self.addCleanup(q.stop)
        mem = sys.modules["memory"]  # the temp-dir copy the app uses
        r = patch.object(llm, "memory", mem)
        r.start()
        self.addCleanup(r.stop)

    def test_get_set_and_old_aliases(self):
        info = self.client.get("/reasoning").json
        self.assertEqual((info["replies_custom"], info["translations"], info["mode"], info["custom_is_cloud"]),
                         ("low", "off", "custom", True))
        res = self.client.post("/reasoning", json={"replies_custom": "high"})
        self.assertEqual(res.json["replies_custom"], "high")
        self.assertEqual(self.client.post("/reasoning", json={"replies_custom": "max"}).status_code, 400)
        self.assertEqual(self.client.post("/reasoning", json={}).status_code, 400)
        self.assertTrue(self.client.get("/getDeepThinking").json["enabled"])
        self.client.post("/setDeepThinking", json={"enabled": False})
        self.assertEqual(self.client.get("/reasoning").json["replies_custom"], "off")


class OtherPartsTests(unittest.TestCase):
    def test_llama_server_keeps_thoughts_apart(self):
        cmd = builtin_server.server_command({"server_exe": "x", "context": 16384}, "m.gguf")
        self.assertEqual(cmd[cmd.index("--reasoning-format") + 1], "deepseek")

    def test_speed_log_shows_thinking(self):
        cloud = timing.parse_server_report({"usage": {"prompt_tokens": 10, "completion_tokens": 300,
                                                      "completion_tokens_details": {"reasoning_tokens": 250}}})
        self.assertIn("of it thinking: 250", timing.describe_reports([cloud]))
        local = timing.parse_server_report({"timings": {"prompt_n": 5, "predicted_n": 9},
                                            "choices": [{"message": {"reasoning_content": "abcd"}}]})
        self.assertIn("of it thinking: 4 characters", timing.describe_reports([local]))

    def test_memory_learning_follows_translations(self):
        source = (BACKEND.parent / "memory_sidecar" / "cm_sidecar.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ("_learning_reasoning", "_reasoning_body"))
                or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("_REASONING_LEVELS", "_THINKING_BUDGETS"))]
        with tempfile.TemporaryDirectory() as d:
            scope = {"os": os, "json": json, "KEY_FILE": os.path.join(d, "api_key.txt")}
            exec(compile(ast.Module(body=keep, type_ignores=[]), "cm_sidecar.py", "exec"), scope)
            self.assertEqual(scope["_learning_reasoning"](), "off")
            Path(d, "reasoning.json").write_text('{"translations": "low"}', encoding="utf-8")
            self.assertEqual(scope["_learning_reasoning"](), "low")
        body = scope["_reasoning_body"]
        self.assertEqual(body("https://openrouter.ai/api/v1", "off"), {"reasoning": {"effort": "minimal"}})
        self.assertEqual(body("http://127.0.0.1:8091/v1", "low"),
                         {"chat_template_kwargs": {"enable_thinking": True}, "thinking_budget_tokens": 512})
        self.assertEqual(body("http://127.0.0.1:8091/v1", "off"), {"chat_template_kwargs": {"enable_thinking": False}})


if __name__ == "__main__":
    unittest.main()

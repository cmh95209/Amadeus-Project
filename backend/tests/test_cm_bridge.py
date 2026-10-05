# -*- coding: utf-8 -*-
"""Offline checks for the CharacterMemory bridge (Phase 1, 2026-09-30).

Guards the hard guarantees of cm_bridge + the chat.py splice:

1. service DOWN (unreachable sidecar)  -> fetch_memory_block() is None and
   the prompt getResponsePacked assembles is byte-identical to the
   no-memory baseline (the leading system text joins to the same string);
2. service up but EMPTY (nothing learned) -> the same byte-identical result;
3. non-empty context -> exactly ONE labeled section, token-capped, folded
   into the single leading system message (the Qwen single-system-message
   rule is preserved by _merge_leading_system_messages);
4. save_turn is fire-and-forget and silent: it never raises when the
   service is down, and it posts exactly the /save contract payload;
5. the greeting path is scope-guarded: since the 2026-10-05 greeting
   rework it may READ the recall block (fetch_memory_block, mirroring the
   reply path - sidecar down/empty degrades to None) but must never WRITE
   to the sidecar (save_turn stays out of generate_greeting: greeting
   lines are synthetic and mirroring them would pollute extraction).

All LLM calls are stubbed; the sidecar is a local HTTP stub (stdlib
http.server) or a dead port. No network, no live data, no new deps.
"""
import contextlib
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
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
_INTERNAL = {"role": "system", "content": "TIMING"}
_VOICE = {"role": "system", "content": "VOICE"}
_HISTORY = [{"role": "user", "content": "where do I live?"}]


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


class _StubHandler(BaseHTTPRequestHandler):
    """Serves a canned /context payload and records /save bodies."""
    context_payload = {"context_text": ""}
    posts = []

    def do_GET(self):
        body = json.dumps(self.context_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _StubHandler.posts.append((self.path, self.rfile.read(n).decode("utf-8")))
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_stub_server(payload) -> HTTPServer:
    _StubHandler.context_payload = payload
    _StubHandler.posts = []
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class BridgeServiceDownTests(unittest.TestCase):
    """The sidecar is unreachable: everything must degrade silently."""

    def setUp(self):
        self._old_url = cm_bridge.CM_SIDECAR_URL
        # Port 1 on loopback: nothing listens there, connect fails fast.
        cm_bridge.CM_SIDECAR_URL = "http://127.0.0.1:1"

    def tearDown(self):
        cm_bridge.CM_SIDECAR_URL = self._old_url

    def test_fetch_down_returns_none(self):
        self.assertIsNone(cm_bridge.fetch_memory_block(1))

    def test_fetch_none_chat_id_returns_none(self):
        self.assertIsNone(cm_bridge.fetch_memory_block(None))

    def test_save_silent_when_down(self):
        # Must return immediately and never raise (fire-and-forget).
        cm_bridge.save_turn(1, "user", "hello")
        cm_bridge.save_turn(1, "assistant", "hi")
        # empty content / unknown role: no work at all, still no raise
        cm_bridge.save_turn(1, "user", "   ")
        cm_bridge.save_turn(1, "system", "nope")
        time.sleep(0.3)  # let any spawned worker hit the dead port quietly


def _expected_messages(memory_block=None):
    """The exact baseline getResponsePacked assembles when the memory
    service contributes nothing - the byte-identical reference."""
    return chat._merge_leading_system_messages(
        [_PERSONALITY]
        + []
        + ([memory_block] if memory_block else [])
        + [_INTERNAL]
        + [chat._PACK_RULES]
        + [_VOICE]
        + [chat.NO_WEB_BLOCK]
        + _HISTORY
    )


def _system_text(messages):
    """The full prompt string the model would see (what 'byte-identical'
    means here)."""
    return "\n\n".join(m["content"] for m in messages if m.get("role") == "system")


class BridgeReplyPromptTests(unittest.TestCase):
    """Drive the REAL getResponsePacked with a stubbed LLM and store."""

    def _run_reply(self, memory_block):
        sink = {}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(chat, "get_llm",
                                                 lambda *a, **k: _FakeLLM(sink)))
            stack.enter_context(mock.patch.object(store, "load_web_access",
                                                 lambda: False))
            stack.enter_context(mock.patch.object(store, "load_deep_thinking",
                                                 lambda: False))
            stack.enter_context(mock.patch.object(
                store, "load_character_book_messages", lambda _last: []))
            stack.enter_context(mock.patch.object(
                store, "load_default_personality_messages",
                lambda: list([_PERSONALITY])))
            stack.enter_context(mock.patch.object(
                store, "load_active_conversation", lambda: 1))
            stack.enter_context(mock.patch.object(
                ja_voice, "build_voice_context", lambda _stat: dict(_VOICE)))
            stack.enter_context(mock.patch.object(stats, "load_stat",
                                                 lambda _name: 3))
            stack.enter_context(mock.patch.object(chat, "_finalize",
                                                 lambda p, llm: p))
            stack.enter_context(mock.patch.object(
                chat, "_web_off_honesty_net", lambda p: p))
            stack.enter_context(mock.patch.object(
                cm_bridge, "fetch_memory_block",
                lambda _cid: memory_block))
            chat.getResponsePacked(list(_HISTORY), internal_context=dict(_INTERNAL))
        return sink["messages"]

    def test_service_down_prompt_byte_identical(self):
        got = self._run_reply(None)
        want = _expected_messages(None)
        self.assertEqual(got, want)
        self.assertEqual(_system_text(got), _system_text(want))

    def test_splices_exactly_one_labeled_section(self):
        block = {"role": "system", "content": "LONG-TERM MEMORY (...): USER_FACTS\n- x"}
        got = self._run_reply(block)
        # single leading system message (Qwen rule), label + payload inside
        self.assertEqual([m for m in got if m.get("role") == "system"], [got[0]])
        self.assertEqual(got[0]["content"].count("LONG-TERM MEMORY"), 1)
        self.assertIn("USER_FACTS\n- x", got[0]["content"])
        # everything after the merged system line is untouched
        self.assertEqual(got[1:], _expected_messages(None)[1:])

    def test_merge_keeps_single_system_message(self):
        merged = chat._merge_leading_system_messages(
            [_PERSONALITY, _INTERNAL, _VOICE, {"role": "user", "content": "u"}])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["role"], "system")
        self.assertEqual(merged[0]["content"],
                         "\n\n".join(p["content"] for p in
                                     (_PERSONALITY, _INTERNAL, _VOICE)))


class BridgeContextShapeTests(unittest.TestCase):
    """fetch_memory_block against a local HTTP stub of the sidecar."""

    def setUp(self):
        self._old_url = cm_bridge.CM_SIDECAR_URL
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        cm_bridge.CM_SIDECAR_URL = self._old_url

    def _point_at(self, server):
        port = server.server_address[1]
        cm_bridge.CM_SIDECAR_URL = f"http://127.0.0.1:{port}"

    def test_empty_context_returns_none(self):
        self.server = _start_stub_server({"context_text": ""})
        self._point_at(self.server)
        self.assertIsNone(cm_bridge.fetch_memory_block(1))

    def test_wraps_labeled_section(self):
        self.server = _start_stub_server(
            {"context_text": "USER_FACTS\n- the user lives in Puchong"})
        self._point_at(self.server)
        block = cm_bridge.fetch_memory_block(1)
        self.assertEqual(block["role"], "system")
        self.assertTrue(block["content"].startswith(cm_bridge._SECTION_LABEL))
        self.assertIn("the user lives in Puchong", block["content"])

    def test_token_cap(self):
        long_text = "\n".join(f"episode line {i}" for i in range(300))
        self.server = _start_stub_server({"context_text": long_text})
        self._point_at(self.server)
        block = cm_bridge.fetch_memory_block(1)
        content = block["content"]
        self.assertLessEqual(len(content),
                             len(cm_bridge._SECTION_LABEL)
                             + cm_bridge.CM_MAX_CHARS
                             + len(cm_bridge._TRUNCATED_NOTE))
        self.assertTrue(content.endswith(cm_bridge._TRUNCATED_NOTE))


class BridgeSaveContractTests(unittest.TestCase):
    """save_turn posts exactly the /save contract the sidecar accepts."""

    def setUp(self):
        self._old_url = cm_bridge.CM_SIDECAR_URL
        self.server = _start_stub_server({"context_text": ""})
        port = self.server.server_address[1]
        cm_bridge.CM_SIDECAR_URL = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        cm_bridge.CM_SIDECAR_URL = self._old_url

    def test_posts_save_payload(self):
        cm_bridge.save_turn(7, "user", "hello there")
        deadline = time.monotonic() + 3
        while not _StubHandler.posts and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(_StubHandler.posts), 1)
        path, body = _StubHandler.posts[0]
        self.assertEqual(path, "/save")
        self.assertEqual(json.loads(body),
                         {"chat_id": "7", "role": "user",
                          "content": "hello there"})

    def test_ignores_empty_and_bad_role(self):
        cm_bridge.save_turn(7, "user", "   ")
        cm_bridge.save_turn(7, "system", "nope")
        time.sleep(0.3)
        self.assertEqual(_StubHandler.posts, [])


class GreetingScopeGuardTests(unittest.TestCase):
    """Since the 2026-10-05 greeting rework, greetings READ the recall
    block (mirroring the reply path) but must never WRITE to the sidecar:
    a greeting line is synthetic (no user turn behind it), and mirroring it
    into the memory store would let it be recalled in other conversations -
    extraction pollution. The write side (save_turn) stays out of
    generate_greeting entirely."""

    def test_generate_greeting_reads_but_never_writes(self):
        import inspect
        source = inspect.getsource(chat.generate_greeting)
        # Reading is allowed (and expected: the same block her replies get).
        self.assertIn("fetch_memory_block", source)
        # Writing is the extraction-pollution guard: never in the greeting
        # path.
        self.assertNotIn("save_turn", source)

    def test_splice_lives_only_in_getResponsePacked(self):
        import inspect
        self.assertIn("fetch_memory_block",
                      inspect.getsource(chat.getResponsePacked))
        self.assertNotIn("fetch_memory_block",
                         inspect.getsource(chat.getOutputPacked))


if __name__ == "__main__":
    unittest.main()

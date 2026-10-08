# -*- coding: utf-8 -*-
"""Offline checks for the voice-first reply (2026-07).

Web-OFF turns split the reply into two calls: call 1 (AmadeusReply) writes
and stores ONLY the Japanese line so her voice can start at once; call 2
(a short background translation) fills the English display field and the
stored row while she is talking. Web-ON turns, regenerate, and greetings
keep the original single-call path.

No server, no network. A faked LLM (patched over chat.get_llm) answers the
structured call through the same with_structured_output surface the real
path uses, and a temporary SQLite store backs the real append_message /
update_message_content so persistence is asserted end to end. Covers:

  1. the JA-only call has exactly one field (no English field to decode);
  2. the JA-only pack rules forbid English output and keep the honesty lines;
  3. call 1 returns the Japanese line with an EMPTY English field, and its
     prompt carries the JA-only rules (not the two-field pack rules);
  4. the failure ladder: a clipped call falls back to a forced AmadeusReply
     retry, then to plain-text salvage (English text is translated to
     Japanese so her voice stays in Japanese);
  5. the web-off honesty net still swaps in the honest line on a false
     search claim in the Japanese line;
  6. getOutputPackedVoiceFirst stores the turn (English text empty after
     call 1) and the background backfill stores the English line - or her
     Japanese line itself when the translation fails;
  7. a call-1 total failure removes the user turn (memory stays clean);
  8. the POST / route speaks the two-event stream when web access is OFF
     (voice event, then text event) and the original single JSON when it is
     ON.
"""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import api
import chat
import cm_bridge
import memory as store
from chat import AmadeusPack, AmadeusReply


PERSONALITY_MARK = "VOICEFIRST_TEST_PERSONALITY"
VOICE_MARK = "VOICEFIRST_VOICE_BLOCK"
JA_LINE = "こんにちは、今日はどんな感じ？"
EN_LINE = "Hi, how is it going today?"


class FakeReply:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_chunks = None


def reply_call(jps=JA_LINE):
    return [{"name": "AmadeusReply",
             "args": {"assistant_reply_JPS": jps}}]


class _FakeStructured:
    def __init__(self, llm, schema):
        self._llm = llm
        self._schema = schema

    def invoke(self, messages):
        llm = self._llm
        llm.last_structured_messages = messages
        llm.last_structured_schema = self._schema
        llm.structured_calls += 1
        if llm.structured_script:
            step = llm.structured_script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step  # AmadeusReply / dict / None
        if self._schema is AmadeusReply:
            return AmadeusReply(assistant_reply_JPS=llm.jps)
        return AmadeusPack(assistant_reply_JPS=llm.jps, assistant_reply_ENG=llm.eng)


class _FakeBound:
    def __init__(self, handler):
        self._handler = handler

    def invoke(self, messages):
        return self._handler(messages)


class _FakeLLM:
    """Stands in for get_llm() on every surface the reply path uses:
    with_structured_output (call 1 / the single-call path), bind_tools +
    _invoke_forced (the forced retry), and plain invoke (the call-2
    translation, the JA repair pass, the salvage translations)."""

    def __init__(self, jps=JA_LINE, eng=EN_LINE,
                 structured_script=None, plain_script=None, forced_reply=None):
        self.jps = jps
        self.eng = eng
        self.structured_script = list(structured_script) if structured_script else []
        self.plain_script = list(plain_script) if plain_script else []
        self.forced_reply = forced_reply
        self.structured_calls = 0
        self.plain_calls = 0
        self.forced_calls = 0
        self.last_structured_messages = None
        self.last_structured_schema = None
        self.last_tool_choice = None
        self.last_tool_names = None

    def with_structured_output(self, schema, method=None):
        return _FakeStructured(self, schema)

    def bind_tools(self, tools, tool_choice=None):
        self.last_tool_choice = tool_choice
        self.last_tool_names = [t.get("function", {}).get("name")
                                if isinstance(t, dict) else None
                                for t in tools]
        return _FakeBound(self._invoke_forced)

    def _invoke_forced(self, messages):
        self.forced_calls += 1
        if isinstance(self.forced_reply, Exception):
            raise self.forced_reply
        return self.forced_reply

    def invoke(self, messages):
        self.plain_calls += 1
        if self.plain_script:
            step = self.plain_script.pop(0)
            if isinstance(step, Exception):
                raise step
            return SimpleNamespace(content=step)
        return SimpleNamespace(content=self.eng)


def _fake_context():
    return [
        {"role": "user", "content": "What did you end up deciding?"},
        {"role": "assistant", "content": "Decided to take the night train."},
        {"role": "user", "content": "Tell me how you are today."},
    ]


class VoiceFirstTests(unittest.TestCase):
    """Chat-level checks (call 1 + the turn flow) against a faked LLM and a
    temp SQLite store - the same isolation pattern as test_greeting."""

    def _patches(self, fake, mem_db, structured_script=None, plain_script=None,
                 forced_reply=None):
        """Common patch set. structured_script / plain_script / forced_reply
        may also live on `fake` - these exist so each test can pass them
        explicitly and keep the fake itself simple."""
        fake_ja = SimpleNamespace(build_voice_context=lambda trust: {
            "role": "system", "content": VOICE_MARK})
        fake_stats = SimpleNamespace(load_stat=lambda key: 62.0)
        return [
            patch.object(chat, "get_llm", lambda *a, **k: fake),
            patch.object(chat, "reset_llm", lambda: None),
            patch.object(chat, "ja_voice", fake_ja),
            patch.object(chat, "stats", fake_stats),
            patch.object(store, "PATH_TO_MEMORY", mem_db),
            patch.object(store, "load_default_personality_messages",
                         return_value=[{"role": "system",
                                        "content": PERSONALITY_MARK}]),
            patch.object(store, "build_prompt_messages",
                         return_value=_fake_context()),
            patch.object(store, "load_active_conversation", return_value=1),
            patch.object(store, "load_deep_thinking", return_value=False),
            patch.object(store, "load_web_access", return_value=False),
            patch.object(cm_bridge, "CM_SIDECAR_URL", "http://127.0.0.1:1"),
        ]

    def _run(self, fake, structured_script=None, plain_script=None,
             forced_reply=None, call="output"):
        """Run call 1 (or the full turn flow) and read the temp store back.

        Returns (result_or_exc, rows) where rows is
        [(id, role, content, japanese), ...] ordered by id.
        """
        if structured_script is not None:
            fake.structured_script = list(structured_script)
        if plain_script is not None:
            fake.plain_script = list(plain_script)
        if forced_reply is not None:
            fake.forced_reply = forced_reply
        with tempfile.TemporaryDirectory() as directory:
            mem_db = str(Path(directory) / "memory.db")
            patches = self._patches(fake, mem_db)
            for p in patches:
                p.start()
            try:
                try:
                    if call == "response":
                        result = chat.getResponsePackedVoiceFirst(
                            _fake_context(),
                            internal_context={"role": "system", "content": "TIMING"})
                        exc = None
                    else:
                        result = chat.getOutputPackedVoiceFirst("hi there")
                        exc = None
                except Exception as e:
                    result, exc = None, e
                # The backfill thread (call 2) writes the English line to the
                # store and only then signals - waiting keeps that write
                # strictly before both this read and the patch teardown.
                if isinstance(result, chat.VoiceFirstTurn):
                    result.wait_en(timeout=5.0)
                conn = sqlite3.connect(mem_db)
                try:
                    c = conn.cursor()
                    store._ensure_messages_table(c)
                    conn.commit()
                    rows = conn.execute(
                        "SELECT id, role, content, japanese FROM messages ORDER BY id"
                    ).fetchall()
                finally:
                    conn.close()
            finally:
                for p in patches:
                    p.stop()
        return fake, result, exc, rows

    # -- schema & prompt ---------------------------------------------------

    def test_ja_only_schema_has_exactly_one_field(self):
        # The whole point: call 1 decodes no English field, so her voice
        # starts as soon as the Japanese line is out.
        self.assertEqual(set(AmadeusReply.model_fields), {"assistant_reply_JPS"})

    def test_ja_only_pack_rules_forbid_english_and_keep_honesty(self):
        content = chat._PACK_RULES_JA_ONLY["content"]
        self.assertIn("NO English field", content)
        self.assertIn("NOT a word-for-word", content)
        self.assertIn("Never end a reply with a promise to check", content)
        self.assertIn("guessing a number", content)
        # and the old two-field instruction is NOT in the JA-only rules
        self.assertNotIn("THEN write assistant_reply_ENG", content)

    def test_call1_returns_ja_line_with_empty_english(self):
        fake = _FakeLLM()
        fake, pack, exc, _rows = self._run(fake, call="response")
        self.assertIsNone(exc)
        self.assertEqual(pack.assistant_reply_JPS, JA_LINE)
        self.assertEqual(pack.assistant_reply_ENG, "")
        self.assertEqual(fake.structured_calls, 1)
        # the forced ladder never ran on a clean call
        self.assertEqual(fake.forced_calls, 0)
        self.assertEqual(fake.plain_calls, 0)

    def test_call1_prompt_carries_ja_only_rules_not_pack_rules(self):
        fake = _FakeLLM()
        fake, _pack, exc, _rows = self._run(fake, call="response")
        self.assertIsNone(exc)
        messages = fake.last_structured_messages
        system_msgs = [m for m in messages if m["role"] == "system"]
        # All leading system blocks merge into ONE system message.
        self.assertEqual(len(system_msgs), 1)
        merged = system_msgs[0]["content"]
        self.assertIn(PERSONALITY_MARK, merged)
        self.assertIn(VOICE_MARK, merged)
        self.assertIn("NO English field", merged)          # JA-only pack rules
        self.assertIn("WEB ACCESS IS CURRENTLY OFF", merged)  # NO_WEB_BLOCK
        self.assertNotIn("THEN write assistant_reply_ENG", merged)

    def test_call1_schema_offered_is_amadeus_reply(self):
        fake = _FakeLLM()
        fake, _pack, exc, _rows = self._run(fake, call="response")
        self.assertIsNone(exc)
        self.assertIs(fake.last_structured_schema, AmadeusReply)

    # -- failure ladder -----------------------------------------------------

    def test_clipped_call_falls_back_to_forced_retry_and_salvage(self):
        # Call 1 decodes to NOTHING -> one forced AmadeusRetry that answers in
        # plain ENGLISH text -> the salvage translates it to Japanese so her
        # voice stays in Japanese.
        fake = _FakeLLM(jps=JA_LINE, eng=EN_LINE)
        fake, pack, exc, _rows = self._run(
            fake, call="response",
            structured_script=[None],
            forced_reply=FakeReply(content="Hello there."),
            plain_script=[JA_LINE])
        self.assertIsNone(exc)
        self.assertEqual(pack.assistant_reply_JPS, JA_LINE)  # translated EN->JA
        self.assertEqual(pack.assistant_reply_ENG, "")
        self.assertEqual(fake.forced_calls, 1)
        self.assertEqual(fake.last_tool_choice, "required")
        self.assertEqual(fake.last_tool_names, ["AmadeusReply"])

    def test_forced_retry_returns_amadeus_reply_tool_call(self):
        fake = _FakeLLM()
        fake, pack, exc, _rows = self._run(
            fake, call="response",
            structured_script=[None],
            forced_reply=FakeReply(tool_calls=reply_call(JA_LINE)))
        self.assertIsNone(exc)
        self.assertEqual(pack.assistant_reply_JPS, JA_LINE)
        self.assertEqual(pack.assistant_reply_ENG, "")
        self.assertEqual(fake.forced_calls, 1)

    def test_honesty_net_still_swaps_false_search_claim(self):
        fake = _FakeLLM()
        fake, pack, exc, _rows = self._run(
            fake, call="response",
            structured_script=[AmadeusReply(
                assistant_reply_JPS="ウェブで検索したの。最新のニュースを調べたわ。")])
        self.assertIsNone(exc)
        # The net replaced the line with its static honest line; the English
        # field is still empty (the backfill would translate the honest line).
        self.assertNotIn("検索した", pack.assistant_reply_JPS)
        self.assertIn("ウェブがオフ", pack.assistant_reply_JPS)
        self.assertEqual(pack.assistant_reply_ENG, "")

    # -- turn flow (call 1 + background call 2) ------------------------------

    def test_turn_stores_ja_line_then_backfill_stores_english(self):
        fake = _FakeLLM()
        fake, turn, exc, rows = self._run(
            fake, call="output", plain_script=[EN_LINE])
        self.assertIsNone(exc)
        # Call 1 stored her line; the English text was empty at that moment.
        self.assertEqual(turn.pack.assistant_reply_JPS, JA_LINE)
        self.assertEqual(turn.pack.assistant_reply_ENG, "")
        english = turn.wait_en(timeout=5.0)
        self.assertEqual(english, EN_LINE)
        self.assertEqual(rows[0][1], "user")
        self.assertEqual(rows[0][2], "hi there")
        self.assertEqual(rows[1][1], "assistant")
        self.assertEqual(rows[1][2], EN_LINE)      # backfill updated the row
        self.assertEqual(rows[1][3], JA_LINE)      # ...in her voice
        self.assertEqual(len(rows), 2)

    def test_backfill_failure_shows_japanese_line_instead(self):
        fake = _FakeLLM()
        fake, turn, exc, rows = self._run(
            fake, call="output",
            plain_script=[RuntimeError("model server dropped mid-translation")])
        self.assertIsNone(exc)
        self.assertEqual(turn.wait_en(timeout=5.0), JA_LINE)
        assistant_rows = [r for r in rows if r[1] == "assistant"]
        self.assertEqual(len(assistant_rows), 1)
        self.assertEqual(assistant_rows[0][2], JA_LINE)

    def test_backfill_verbatim_echo_with_latin_is_rejected(self):
        # 2026-10-08 incident: a weak model repeated her Japanese line as the
        # "English" translation; it slipped the letter check because the line
        # contained Latin words ("Hong"), so Japanese text landed in the
        # English box. The echo check (a near-copy of her real line) rejects
        # it, and the stored row keeps her actual Japanese line instead.
        ja = "Hong、今日はいい天気ね。ゆっくり休んでね。"
        echo = "Hong、今日はいい天気ね。ゆっくり休んで"  # near-echo: lost the final ね
        fake = _FakeLLM(jps=ja)
        fake, turn, exc, rows = self._run(
            fake, call="output", plain_script=[echo])
        self.assertIsNone(exc)
        self.assertNotEqual(turn.wait_en(timeout=5.0), echo)  # the echo was rejected
        assistant_rows = [r for r in rows if r[1] == "assistant"]
        self.assertEqual(len(assistant_rows), 1)
        self.assertEqual(assistant_rows[0][2], ja)
        self.assertEqual(assistant_rows[0][3], ja)

    def test_backfill_translation_carrying_japanese_is_accepted(self):
        # She can teach Japanese: her English line legitimately carries
        # Japanese words ("The word 猫 (neko) means cat..."). The echo guard
        # must reject only near-copies of her Japanese line - never a
        # genuine translation that carries Japanese.
        ja = "「猫」は「ねこ」と読みます。"
        en = 'The word 猫 is read as "neko" - want to learn more?'
        fake = _FakeLLM(jps=ja)
        fake, turn, exc, rows = self._run(
            fake, call="output", plain_script=[en])
        self.assertIsNone(exc)
        self.assertEqual(turn.wait_en(timeout=5.0), en)

    def test_call1_total_failure_removes_user_turn(self):
        fake = _FakeLLM()
        fake, _turn, exc, rows = self._run(
            fake, call="output",
            structured_script=[RuntimeError("connection reset")],
            forced_reply=RuntimeError("still down"))
        self.assertIsInstance(exc, RuntimeError)
        # The user turn was removed: nothing in memory.
        self.assertEqual(rows, [])

    # -- the route protocol ---------------------------------------------------

    def _route(self, web_on, fake):
        """POST / through the Flask test client with the full patch set."""
        fake_ja = SimpleNamespace(build_voice_context=lambda trust: {
            "role": "system", "content": VOICE_MARK})
        fake_stats = SimpleNamespace(load_stat=lambda key: 62.0)
        with tempfile.TemporaryDirectory() as directory:
            mem_db = str(Path(directory) / "memory.db")
            patches = self._patches(fake, mem_db)
            # load_web_access / load_deep_thinking already patched False above;
            # the test controls the branch explicitly:
            patches.append(patch.object(store, "load_web_access", return_value=web_on))
            patches.append(patch.object(api, "has_api_key", return_value=True))
            if web_on:
                # The web-on branch runs the search loop - out of scope here;
                # pin the protocol branch with the original single-call path.
                # (Patch the name the api module imported, not chat's.)
                single = AmadeusPack(assistant_reply_JPS=JA_LINE,
                                     assistant_reply_ENG=EN_LINE)
                patches.append(patch.object(
                    api, "getOutputPacked",
                    side_effect=lambda user_input: (
                        single, 1, 2, 1)))
            for p in patches:
                p.start()
            try:
                client = api.application.test_client()
                response = client.post("/", json={"user_input": "hi there"})
                body = response.get_data(as_text=True)
            finally:
                for p in patches:
                    p.stop()
            conn = sqlite3.connect(mem_db)
            try:
                c = conn.cursor()
                store._ensure_messages_table(c)
                conn.commit()
                rows = conn.execute(
                    "SELECT id, role, content, japanese FROM messages ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
        # Keep the test from leaking speech ids into the real registry.
        speech_id = None
        try:
            first_event = _first_event(body)
            speech_id = first_event.get("speech_id")
        except Exception:
            pass
        if speech_id:
            with api._speech_requests_lock:
                api._speech_requests.pop(speech_id, None)
        return response, body, rows

    def test_route_web_off_speaks_two_phase_stream(self):
        fake = _FakeLLM()
        response, body, rows = self._route(False, fake)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/event-stream"))

        events = _events(body)
        self.assertEqual(len(events), 2)
        voice, text = events[0], events[1]
        self.assertEqual(voice["phase"], "voice")
        self.assertIsInstance(voice["speech_id"], str)
        self.assertTrue(voice["speech_id"])
        self.assertEqual(voice["user_id"], 1)
        self.assertEqual(voice["assistant_id"], 2)
        self.assertEqual(voice["conversation_id"], 1)
        self.assertEqual(text["phase"], "text")
        self.assertEqual(text["response"], EN_LINE)
        # The voice event precedes the text event: audio can start before the
        # display line exists.
        self.assertLess(body.index('"phase": "voice"'),
                        body.index('"phase": "text"'))
        # And the stored row carries the English line (the backfill landed).
        self.assertEqual(rows[1][1], "assistant")
        self.assertEqual(rows[1][2], EN_LINE)
        self.assertEqual(rows[1][3], JA_LINE)

    def test_route_web_on_keeps_single_json_protocol(self):
        fake = _FakeLLM()
        response, body, _rows = self._route(True, fake)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("application/json"))
        data = json.loads(body)
        self.assertEqual(data["response"], EN_LINE)
        self.assertIsInstance(data["speech_id"], str)
        self.assertEqual(data["assistant_id"], 2)
        # The web-on branch never touched the voice-first machinery.
        self.assertEqual(fake.structured_calls, 0)
        self.assertEqual(fake.plain_calls, 0)

    def test_route_survives_an_ansi_1252_console(self):
        """Regression (2026-10-07, fresh en-US Windows VM): on an
        English-locale PC the launcher's log file encodes the service
        console in Windows-1252, which cannot hold her Japanese lines -
        the route's own print of the reply crashed every chat message
        with UnicodeEncodeError (a 500) although the reply had been
        generated and stored. main.py now re-encodes the process console
        to UTF-8 at startup (console_encoding); this simulates the VM:
        an ANSI-1252 console, the startup re-encode, then the full
        voice-first route.
        """
        import console_encoding
        fake = _FakeLLM()
        saved_stdout = sys.stdout
        ansi = io.TextIOWrapper(io.BytesIO(), encoding="cp1252",
                                line_buffering=True)
        sys.stdout = ansi
        try:
            # The request WITHOUT the fix: printing her Japanese line on
            # a Windows-1252 console raises.
            with self.assertRaises(UnicodeEncodeError):
                ansi.write(JA_LINE)
            # main.py's startup line re-encodes the console, after which
            # the route's own print of the same line survives.
            ansi = io.TextIOWrapper(io.BytesIO(), encoding="cp1252",
                                    line_buffering=True)
            sys.stdout = ansi
            console_encoding.force_utf8_stdio()
            response, body, _rows = self._route(False, fake)
        finally:
            sys.stdout = saved_stdout
        self.assertEqual(response.status_code, 200)
        events = _events(body)
        self.assertEqual([e["phase"] for e in events], ["voice", "text"])
        self.assertEqual(events[1]["response"], EN_LINE)


def _first_event(body):
    for line in body.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise ValueError("no data event in stream: " + body[:200])


def _events(body):
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out


class ContextLinesTests(unittest.TestCase):
    """The short context the call-2 translation sees (not the whole prompt)."""

    def test_labels_and_order(self):
        lines = chat._voice_first_context_lines([
            {"role": "user", "content": "first user"},
            {"role": "assistant", "content": "first amadeus"},
            {"role": "user", "content": "second user"},
        ])
        self.assertEqual(lines, [
            "user: first user",
            "you: first amadeus",
            "user: second user",
        ])

    def test_caps_at_four_and_truncates_long_lines(self):
        lines = chat._voice_first_context_lines([
            {"role": "user", "content": "u" * 400},
            {"role": "assistant", "content": "a" * 400},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"},
        ])
        # The FOUR most recent lines, oldest first; long lines are capped.
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "you: " + "a" * 300)   # capped
        self.assertEqual(lines[1], "user: c")
        self.assertEqual(lines[2], "you: d")
        self.assertEqual(lines[3], "user: e")

    def test_skips_blank_lines(self):
        lines = chat._voice_first_context_lines([
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "hi"},
        ])
        self.assertEqual(lines, ["you: hi"])


if __name__ == "__main__":
    unittest.main()

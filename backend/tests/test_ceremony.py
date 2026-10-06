# -*- coding: utf-8 -*-
"""Offline checks for the first-launch ceremony (2026-10-05 design).

No server, no network, no LLM: the ceremony's state machine is a pure
function (ceremony.decide), the four fixed lines are constants, the wake-up
is generated through the usual greeting ladder with a faked LLM, and the
route tests drive the real Flask app against a temporary data directory.
Covers:
  1. the persisted state file: absent -> intro -> connecting -> done, and a
     corrupt value self-heals back to "intro";
  2. the username file (personal, like the other data/ files) and its
     standing prompt line (present when set, byte-absent when clear);
  3. the pure decision table: first launch (model down / already running),
     settings saved, re-saved (just_saved), the once-per-launch reminder
     after ~2.5 min, the wake-up, done-forever;
  4. the approved line texts, including the TTS phonetic split (display JA
     vs spoken JA for the intro line);
  5. the /greet route end to end: intro line stored as a greeting row with
     the SPOKEN text minted for speech, silent probes, trying, reminder,
     wake-up, fallback on generation failure, the normal-greeting fall-
     through on a brand-new install whose model is already up, and the
     switch-greeting silence while the ceremony runs;
  6. the wake prompt: the user-approved instruction (name-ask vs
     name-addressed variant) and the first-connection arrival line.
"""
import importlib
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BACKEND = str(Path(__file__).resolve().parents[1])


class _FreshAppTests(unittest.TestCase):
    """The suite's shared harness: chdir into a temp data dir (memory.py's
    DATA_DIR is relative, so EVERY data file lands here) and reimport the
    backend modules against it, with llm/tts stubbed out (the same trick
    test_personality.py uses). The live app's data directory is never
    touched."""

    def setUp(self):
        if BACKEND not in sys.path:
            sys.path.insert(0, BACKEND)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.previous_cwd = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.previous_cwd)
        stubs = patch.dict(sys.modules, {
            'llm': SimpleNamespace(
                get_llm=lambda *a, **k: None,
                reset_llm=lambda: None,
                maybe_strip_rejected_params=lambda *a, **k: False,
                test_server=lambda *a, **k: {"reachable": False, "models": [],
                                             "error": "stub"},
                _server_url=lambda: "http://127.0.0.1:9",
            ),
            'tts': SimpleNamespace(
                streamVoiceChunks=lambda text, save_path=None: iter(()),
                renderVoiceToPath=lambda text, path: False,
            ),
            'cm_bridge': SimpleNamespace(fetch_memory_block=lambda *a, **k: None),
        })
        stubs.start()
        self.addCleanup(stubs.stop)
        for name in ('api', 'chat', 'memory', 'ceremony', 'chat_interactions'):
            sys.modules.pop(name, None)
        self.api = importlib.import_module('api')
        self.chat = importlib.import_module('chat')
        self.memory = importlib.import_module('memory')
        self.ceremony = importlib.import_module('ceremony')
        self.client = self.api.application.test_client()
        self.ceremony.reset_run_tracker()
        self.addCleanup(self.ceremony.reset_run_tracker)

    # --- small helpers ----------------------------------------------------
    def state(self) -> str:
        try:
            return Path("data/ceremony_state.txt").read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def rows(self):
        if not Path("data/memory.db").exists():
            return []
        conn = sqlite3.connect("data/memory.db")
        try:
            c = conn.cursor()
            self.memory._ensure_messages_table(c)
            conn.commit()
            return c.execute(
                "SELECT id, role, content, japanese, conversation_id, greeting "
                "FROM messages ORDER BY id").fetchall()
        finally:
            conn.close()

    def model(self, ready: bool, reason: str = ""):
        return patch.object(self.api, "model_ready",
                            return_value={"ready": ready, "reason": reason})

    def greet(self, just_saved: bool = False):
        body = {"just_saved": just_saved} if just_saved else {}
        return self.client.post("/greet", json=body)


class CeremonyStateFileTests(_FreshAppTests):
    def test_absent_file_means_not_started(self):
        self.assertEqual(self.memory.load_ceremony_state(), "")

    def test_round_trip(self):
        for state in ("intro", "connecting", "done"):
            self.memory.save_ceremony_state(state)
            self.assertEqual(self.memory.load_ceremony_state(), state)
            self.assertEqual(self.state(), state)

    def test_corrupt_value_self_heals_to_intro(self):
        Path("data").mkdir(exist_ok=True)
        Path("data/ceremony_state.txt").write_text("garbage\r\n", encoding="utf-8")
        self.assertEqual(self.memory.load_ceremony_state(), "intro")

    def test_empty_value_means_not_started(self):
        Path("data").mkdir(exist_ok=True)
        Path("data/ceremony_state.txt").write_text("  \n", encoding="utf-8")
        self.assertEqual(self.memory.load_ceremony_state(), "")


class UsernameFileTests(_FreshAppTests):
    def test_absent_means_unset(self):
        self.assertEqual(self.memory.load_username(), "")

    def test_save_load_strips(self):
        self.assertEqual(self.memory.save_username("  Alex "), "Alex")
        self.assertEqual(self.memory.load_username(), "Alex")

    def test_empty_clears(self):
        self.memory.save_username("Alex")
        self.memory.save_username("")
        self.assertEqual(self.memory.load_username(), "")


class DecideTableTests(_FreshAppTests):
    """ceremony.decide() is pure: (state, model_ready, settings_present,
    just_saved, now) -> (kind, new_state). The tracker is per-launch
    process state, reset in setUp."""

    def test_done_is_terminal(self):
        self.assertEqual(self.ceremony.decide("done", True, True),
                         (None, "done"))
        self.assertEqual(self.ceremony.decide("done", False, False),
                         (None, "done"))

    def test_first_launch_model_already_running(self):
        # Brand-new install whose brain is already on: normal greeting,
        # ceremony done - no waiting story to tell.
        self.assertEqual(self.ceremony.decide("", True, True),
                         ("normal", "done"))

    def test_first_launch_model_down(self):
        self.assertEqual(
            self.ceremony.decide("", False, False), ("intro", "intro"))
        # ...and the probe within the same launch says nothing more.
        self.ceremony.note("intro", "intro")
        self.assertEqual(self.ceremony.decide("intro", False, False),
                         (None, "intro"))

    def test_first_launch_settings_present(self):
        # Settings saved before the first probe: skip the waiting story.
        self.assertEqual(self.ceremony.decide("", False, True),
                         ("trying", "connecting"))

    def test_just_saved_is_a_fresh_attempt(self):
        self.assertEqual(self.ceremony.decide("", False, False, just_saved=True),
                         ("trying", "connecting"))
        # ...and it re-says the line even while already "connecting".
        self.ceremony.note("trying", "connecting")
        self.assertEqual(self.ceremony.decide("connecting", False, True,
                                              just_saved=True),
                         ("trying", "connecting"))

    def test_intro_launch_reopens_once_per_launch(self):
        # A fresh launch with persisted "intro" (no settings yet) re-says
        # the introduction exactly once.
        self.assertEqual(self.ceremony.decide("intro", False, False),
                         ("intro", "intro"))
        self.ceremony.note("intro", "intro")
        self.assertEqual(self.ceremony.decide("intro", False, False),
                         (None, "intro"))

    def test_intro_becomes_trying_when_settings_appear(self):
        self.ceremony.note("intro", "intro")
        self.assertEqual(self.ceremony.decide("intro", False, True),
                         ("trying", "connecting"))

    def test_intro_plus_ready_model_wakes(self):
        # The brain came online while she was "waiting": the wake-up line,
        # not the ordinary greeting.
        self.assertEqual(self.ceremony.decide("intro", True, True),
                         ("wake", "intro"))

    def test_connecting_plus_ready_model_wakes(self):
        self.assertEqual(self.ceremony.decide("connecting", True, True),
                         ("wake", "connecting"))

    def test_reopen_mid_connecting_re_says_trying(self):
        # Fresh launch (fresh tracker) with persisted "connecting": she
        # re-says "checking the line" at startup, clock restarts.
        self.assertEqual(self.ceremony.decide("connecting", False, True),
                         ("trying", "connecting"))

    def test_reminder_fires_once_after_delay(self):
        import time
        self.ceremony.note("trying", "connecting")  # starts the clock
        self.ceremony._tracker().connecting_since = time.monotonic()
        # Before the window elapses: quiet.
        self.assertEqual(self.ceremony.decide("connecting", False, True),
                         (None, "connecting"))
        # After it does: the soft reminder.
        self.ceremony._tracker().connecting_since = time.monotonic() - 200
        self.assertEqual(self.ceremony.decide("connecting", False, True),
                         ("reminder", "connecting"))
        # ...and never again this launch.
        self.ceremony.note("reminder", "connecting")
        self.assertEqual(self.ceremony.decide("connecting", False, True),
                         (None, "connecting"))

    def test_settings_signal_is_the_model_name(self):
        # No model name configured yet -> "settings" is not present, even
        # though a server address could be (auto-detect needs no address).
        self.assertEqual(self.chat.getLLMModel(), "No Model Selected.")
        self.assertFalse(self.api._connection_settings_present())
        with patch.object(self.chat, "LLM_Model", "qwen3.8-27b"):
            self.assertTrue(self.api._connection_settings_present())


class CeremonyLineTextsTests(_FreshAppTests):
    def test_all_four_lines_exist(self):
        for kind in ("intro", "trying", "reminder", "fallback"):
            en, ja_display, ja_voice = self.ceremony.line(kind)
            self.assertTrue(en.strip(), kind)
            self.assertTrue(ja_display.strip(), kind)
            self.assertTrue(ja_voice.strip(), kind)

    def test_intro_keeps_phonetic_split(self):
        en, ja_display, ja_voice = self.ceremony.line("intro")
        self.assertNotEqual(ja_display, ja_voice)
        # Display: the user's original wording (kanji/hiragana names, the
        # Latin acronyms as the user wrote them).
        self.assertIn("牧瀬紅莉栖", ja_display)
        self.assertIn("LLMサーバー", ja_display)
        self.assertIn("APIキー", ja_display)
        # Spoken: the v3 phonetics the user A/B-tested (full katakana for
        # her name, kana for Kurisu, kana for AI / LLM / API).
        self.assertIn("アマデウス", ja_voice)
        self.assertIn("まきせくりす", ja_voice)
        self.assertIn("エーアイ", ja_voice)
        self.assertIn("エルエルエムサーバー", ja_voice)
        self.assertIn("エーピーアイキー", ja_voice)
        self.assertNotIn("牧瀬紅莉栖", ja_voice)

    def test_fixed_waiting_lines_are_pure_japanese(self):
        for kind in ("trying", "reminder", "fallback"):
            en, ja_display, ja_voice = self.ceremony.line(kind)
            self.assertEqual(ja_voice, ja_display, kind)

    def test_approved_english_wordings(self):
        self.assertEqual(
            self.ceremony.line("intro")[0],
            "Hello, I am Amadeus. An AI built on the memories of Makise "
            "Kurisu. To make my thinking work, you first need to connect "
            "my brain. Open the settings menu, and enter the LLM server "
            "address and API key. Make sure the model is running. Oh, feel "
            "free to write your name too, if you want. Until then, I'll be "
            "right here, waiting. I don't go anywhere.")
        self.assertIn("Settings received", self.ceremony.line("trying")[0])
        self.assertIn("double-check", self.ceremony.line("reminder")[0])
        self.assertIn("I can't reach the model", self.ceremony.line("fallback")[0])


class GreetRouteTests(_FreshAppTests):
    """The /greet route end to end (real Flask app, real temp store,
    faked model readiness, faked wake generation)."""

    def _canned_wake(self):
        pack = self.chat.AmadeusPack(
            assistant_reply_ENG="I'm awake.",
            assistant_reply_JPS="もう、起きているの。")
        return patch.object(
            self.api, "generate_greeting",
            side_effect=lambda **kw: (pack, 99, 1))

    def test_fresh_install_delivers_intro_line(self):
        with self.model(False, "model server unreachable"):
            res = self.greet()
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertIs(body["ready"], False)
        self.assertEqual(body["kind"], "intro")
        self.assertEqual(body["ceremony"], "intro")
        self.assertEqual(body["response"], self.ceremony.line("intro")[0])
        # The line is stored as a greeting row (display texts), and the
        # speech minted for it carries the SPOKEN phonetic text.
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        _id, role, content, japanese, conv, greeting = rows[0]
        self.assertEqual(role, "assistant")
        self.assertEqual(content, self.ceremony.line("intro")[0])
        self.assertEqual(japanese, self.ceremony.line("intro")[1])
        self.assertEqual(greeting, 1)
        _created, voice_text, speech_assistant_id = \
            self.api._speech_requests[body["speech_id"]]
        self.assertEqual(voice_text, self.ceremony.line("intro")[2])
        self.assertEqual(voice_text, self.ceremony.line("intro")[2])
        self.assertNotEqual(voice_text, japanese)
        self.assertEqual(speech_assistant_id, _id)
        self.assertEqual(self.state(), "intro")

    def test_intro_probes_are_silent(self):
        with self.model(False, "no model configured"):
            self.greet()  # the intro line
            res = self.greet()
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertIs(body["ready"], False)
        self.assertNotIn("kind", body)
        self.assertEqual(body["ceremony"], "intro")
        self.assertEqual(len(self.rows()), 1)  # no second line

    def test_settings_saved_moves_to_trying(self):
        with self.model(False, "model server unreachable"):
            self.greet()  # intro (no settings saved yet)
        with self.model(False, "model server unreachable"), \
             patch.object(self.chat, "LLM_Model", "qwen3.8-27b"):
            res = self.greet()  # settings now saved
        body = res.json
        self.assertEqual(body["kind"], "trying")
        self.assertEqual(body["ceremony"], "connecting")
        self.assertEqual(self.state(), "connecting")
        self.assertEqual(self.rows()[-1][2], self.ceremony.line("trying")[0])

    def test_just_saved_is_a_fresh_attempt(self):
        with self.model(False, "model server unreachable"):
            self.greet()  # intro (no settings saved yet)
        with self.model(False, "model server unreachable"), \
             patch.object(self.chat, "LLM_Model", "qwen3.8-27b"):
            self.greet()  # trying
            res = self.greet(just_saved=True)  # re-saved settings
        self.assertEqual(res.json["kind"], "trying")
        self.assertEqual(len(self.rows()), 3)  # intro + trying + trying

    def test_reminder_fires_once_then_quiet(self):
        import time
        with self.model(False, "model server unreachable"):
            self.greet()  # intro (no settings saved yet)
        with self.model(False, "model server unreachable"), \
             patch.object(self.chat, "LLM_Model", "qwen3.8-27b"):
            self.greet()  # trying (starts the clock)
            # Force the reminder window to have elapsed.
            self.ceremony._tracker().connecting_since = time.monotonic() - 200
            res = self.greet()
        self.assertEqual(res.json["kind"], "reminder")
        self.assertEqual(len(self.rows()), 3)
        with self.model(False, "model server unreachable"):
            res = self.greet()
        self.assertNotIn("kind", res.json)
        self.assertEqual(len(self.rows()), 3)  # never again this launch

    def test_wake_up_ends_ceremony(self):
        # generate_greeting is faked here (it stores nothing itself), so
        # the storage side of a wake is covered by WakePromptTests with the
        # real generator; this test covers the route: the GENERATED JA line
        # is what gets minted for speech.
        self.memory.save_ceremony_state("connecting")
        with self.model(True), self._canned_wake():
            res = self.greet()
        body = res.json
        self.assertIs(body["ready"], True)
        self.assertEqual(body["kind"], "wake")
        self.assertEqual(body["ceremony"], "done")
        self.assertEqual(body["response"], "I'm awake.")
        self.assertEqual(self.state(), "done")
        _created, voice_text, assistant_id = \
            self.api._speech_requests[body["speech_id"]]
        self.assertEqual(voice_text, "もう、起きているの。")
        self.assertEqual(assistant_id, 99)

    def test_wake_failure_ships_fallback_and_still_ends(self):
        self.memory.save_ceremony_state("connecting")
        with self.model(True), patch.object(
                self.api, "generate_greeting",
                side_effect=RuntimeError("model rejected the call")):
            res = self.greet()
        body = res.json
        self.assertEqual(body["kind"], "fallback")
        self.assertEqual(body["ceremony"], "done")
        self.assertEqual(body["response"], self.ceremony.line("fallback")[0])
        self.assertEqual(self.state(), "done")
        # The voice minted for the fallback is its (phonetically fine) JA.
        _created, voice_text, _ = self.api._speech_requests[body["speech_id"]]
        self.assertEqual(voice_text, self.ceremony.line("fallback")[2])

    def test_first_launch_model_already_running(self):
        # The brain is on from the start: normal greeting, ceremony done.
        self.chat.setKey("sk-test")
        pack = self.chat.AmadeusPack(
            assistant_reply_ENG="Welcome back.", assistant_reply_JPS="おかえり。")
        with self.model(True), patch.object(
                self.api, "generate_greeting",
                side_effect=lambda **kw: (pack, 7, 1)):
            res = self.greet()
        body = res.json
        self.assertIs(body["ready"], True)
        self.assertEqual(body["response"], "Welcome back.")
        self.assertEqual(body["ceremony"], "done")
        self.assertNotIn("kind", body)
        self.assertEqual(self.state(), "done")

    def test_first_launch_no_key_keeps_existing_400(self):
        # A brand-new install whose model runs but has no API key: the
        # normal path's existing 400 stands (the user adds a key in
        # Settings; the ceremony is already recorded as done).
        with self.model(True):
            res = self.greet()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.state(), "done")

    def test_done_state_runs_the_normal_path(self):
        self.memory.save_ceremony_state("done")
        self.chat.setKey("sk-test")
        pack = self.chat.AmadeusPack(
            assistant_reply_ENG="Back again?", assistant_reply_JPS="また来たの？")
        with self.model(True), patch.object(
                self.api, "generate_greeting",
                side_effect=lambda **kw: (pack, 7, 1)):
            res = self.greet()
        body = res.json
        self.assertEqual(body["response"], "Back again?")
        self.assertEqual(body["ceremony"], "done")
        self.assertNotIn("kind", body)

    def test_switch_greeting_is_silent_during_ceremony(self):
        self.memory.save_ceremony_state("intro")
        res = self.client.post("/conversations/1/greet", json={})
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertIs(body["ready"], False)
        self.assertEqual(body["ceremony"], "intro")
        # The guard fires before ANY store access: no line, no tab
        # activation (the fresh DB was never even created).
        self.assertEqual(self.rows(), [])
        self.assertFalse(Path("data/memory.db").exists())

    def test_switch_greeting_works_after_done(self):
        self.memory.save_ceremony_state("done")
        self.chat.setKey("sk-test")
        with self.model(False, "model server unreachable"):
            res = self.client.post("/conversations/1/greet", json={})
        body = res.json
        self.assertIs(body["ready"], False)
        self.assertNotIn("ceremony", body)  # plain old not-ready shape


class UsernameRouteTests(_FreshAppTests):
    def test_get_set_round_trip(self):
        self.assertEqual(self.client.get("/getUsername").json["username"], "")
        res = self.client.post("/setUsername", json={"username": "  Alex  "})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["username"], "Alex")
        self.assertEqual(self.client.get("/getUsername").json["username"], "Alex")

    def test_empty_clears(self):
        self.client.post("/setUsername", json={"username": "Alex"})
        res = self.client.post("/setUsername", json={"username": ""})
        self.assertEqual(res.json["username"], "")
        self.assertEqual(self.client.get("/getUsername").json["username"], "")

    def test_missing_field_is_400(self):
        res = self.client.post("/setUsername", json={})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/setUsername", json={"username": 5})
        self.assertEqual(res.status_code, 400)

    def test_username_line_in_prompt_when_set_only(self):
        # Part D, prompt side: the standing line rides the internal-context
        # block on BOTH reply and greeting prompts, and is byte-absent when
        # the name is cleared (existing installs stay byte-identical).
        captured = []
        pack = self.chat.AmadeusPack(
            assistant_reply_JPS="こんにちは", assistant_reply_ENG="Hello")
        fake = SimpleNamespace(
            with_structured_output=lambda *a, **k: SimpleNamespace(
                invoke=lambda messages: captured.extend(messages) or pack),
            bind_tools=lambda tools, **k: SimpleNamespace(
                invoke=lambda messages: captured.extend(messages) or
                SimpleNamespace(content="", tool_calls=[{
                    "name": "AmadeusPack", "id": "c1",
                    "args": {"assistant_reply_JPS": pack.assistant_reply_JPS,
                             "assistant_reply_ENG": pack.assistant_reply_ENG}}])),
        )
        with patch.object(self.chat, "get_llm", return_value=fake), \
             patch.object(self.chat, "_finalize", lambda p, llm: p), \
             patch.object(self.chat, "reset_llm"):
            # Baseline (no username): the name line must be absent.
            self.chat.getResponsePacked([{"role": "user", "content": "hi"}])
            baseline = [m["content"] for m in captured
                        if m["role"] == "system"]
            self.assertEqual(len(baseline), 1)
            self.assertNotIn("The user goes by", baseline[0])
            self.memory.save_username("Alex")
            captured.clear()
            self.chat.getResponsePacked([{"role": "user", "content": "hi"}])
            system = [m["content"] for m in captured
                      if m["role"] == "system"]
            self.assertEqual(len(system), 1)
            self.assertIn("The user goes by Alex", system[0])
            self.memory.save_username("")
            captured.clear()
            self.chat.getResponsePacked([{"role": "user", "content": "hi"}])
            system = [m["content"] for m in captured
                      if m["role"] == "system"]
            self.assertNotIn("The user goes by", system[0])


class WakePromptTests(_FreshAppTests):
    """The wake-up prompt carries the user-approved instruction (adapted
    to the username decision) and the first-connection arrival line - and
    generates + stores exactly one greeting line, like every other mode."""

    def _fake_llm(self):
        class _Bound:
            def invoke(self, messages):
                self._llm.calls += 1
                self._llm.last_messages = messages
                return SimpleNamespace(content="", tool_calls=[{
                    "name": "AmadeusPack", "id": "w1",
                    "args": {"assistant_reply_JPS": "もう、起きているの。",
                             "assistant_reply_ENG": "I'm awake."},
                }])

        llm = SimpleNamespace()
        llm.calls = 0
        llm.last_messages = None
        bound = _Bound()
        bound._llm = llm

        def bind(tools, tool_choice=None):
            return bound

        llm.bind_tools = bind
        return llm

    def _run_wake(self):
        fake = self._fake_llm()
        with patch.object(self.chat, "get_llm", return_value=fake), \
             patch.object(self.chat, "reset_llm"), \
             patch.object(self.chat, "_finalize", lambda pack, llm: pack), \
             patch.object(self.chat, "stats",
                          SimpleNamespace(load_stat=lambda key: 62.0)), \
             patch.object(self.memory, "load_default_personality_messages",
                          return_value=[{"role": "system",
                                         "content": "WAKE_PERSONALITY"}]):
            try:
                result = self.chat.generate_greeting(mode="wake")
                exc = None
            except Exception as e:
                result, exc = None, e
        return fake, result, exc

    def test_wake_prompt_without_username(self):
        fake, result, exc = self._run_wake()
        self.assertIsNone(exc)
        system = [m["content"] for m in fake.last_messages
                  if m["role"] == "system"]
        self.assertEqual(len(system), 1)
        self.assertIn("WAKE_PERSONALITY", system[0])
        self.assertIn("Connection established, initial boot", system[0])
        self.assertIn("ask for the user's name", system[0])
        self.assertNotIn("go by", system[0])  # the named variant is absent
        self.assertEqual(fake.last_messages[-1],
                         {"role": "user", "content": self.chat.WAKE_ARRIVAL})
        pack, assistant_id, conv_id = result
        self.assertEqual(pack.assistant_reply_ENG, "I'm awake.")

    def test_wake_prompt_addresses_a_saved_username(self):
        self.memory.save_username("Alex")
        fake, result, exc = self._run_wake()
        self.assertIsNone(exc)
        system = [m["content"] for m in fake.last_messages
                  if m["role"] == "system"]
        self.assertIn("they go by Alex", system[0])
        self.assertNotIn("ask for the user's name", system[0])

    def test_wake_stores_one_greeting_line(self):
        _fake, result, exc = self._run_wake()
        self.assertIsNone(exc)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        _id, role, content, japanese, conv, greeting = rows[0]
        self.assertEqual(role, "assistant")
        self.assertEqual(content, "I'm awake.")
        self.assertEqual(japanese, "もう、起きているの。")
        self.assertEqual(greeting, 1)

    def test_startup_prompt_is_unchanged(self):
        # Regression anchor: the wake mode must not leak into the ordinary
        # startup greeting.
        fake, _result, exc = self._run_wake()
        self.assertIsNone(exc)
        with patch.object(self.chat, "get_llm", return_value=fake), \
             patch.object(self.chat, "reset_llm"), \
             patch.object(self.chat, "_finalize", lambda pack, llm: pack), \
             patch.object(self.chat, "stats",
                          SimpleNamespace(load_stat=lambda key: 62.0)), \
             patch.object(self.memory, "load_default_personality_messages",
                          return_value=[{"role": "system",
                                         "content": "WAKE_PERSONALITY"}]):
            self.chat.generate_greeting()
        system = [m["content"] for m in fake.last_messages
                  if m["role"] == "system"]
        self.assertIn("The user just opened the app", system[0])
        self.assertNotIn("initial boot", system[0])
        self.assertEqual(fake.last_messages[-1]["content"],
                         "[The user just opened the app.]")


if __name__ == "__main__":
    unittest.main()

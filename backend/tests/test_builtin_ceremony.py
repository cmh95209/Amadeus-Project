# -*- coding: utf-8 -*-
"""Offline checks for the first-launch ceremony in built-in-model mode.

User-approved design (2026-10-17): NAME FIRST. She introduces herself while
her mind (the built-in model) loads and asks for the user's name; the
wake-up waits until a name is saved AND the model answers, so a fast model
never wakes her mid-intro. Covers:
  1. the approved line texts (EN / JA display / JA spoken with kana);
  2. the pure decision table (ceremony.decide_builtin);
  3. the /greet route end to end (temp data dir, faked readiness + wake):
     intro always first (no "normal" shortcut), silence until the name is
     saved, "almost awake", wake, the reminder, the missing-model line, the
     built-in fallback, and the wait flag the WebUI keys on;
  4. the wake prompt's built-in wording; custom mode is unchanged;
  5. the history budget cap, the brain-mode routes, and Settings' model
     field never showing (and so never saving back) the built-in name.
"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_builtin_llm import _config, _write  # noqa: E402
from test_ceremony import _FreshAppTests  # noqa: E402


class _BuiltinApp(_FreshAppTests):
    def setUp(self):
        super().setUp()
        self.cfg_path = Path("data") / "local_llm.json"
        self.base = Path(self.directory.name)
        _write(self.cfg_path, _config(self.base))
        # No real llama-server here: her model counts as "loading" unless a
        # test says otherwise (server_state).
        self.server_state = "loading"
        state = patch.object(self.api.builtin_server, "status",
                             side_effect=lambda: {"state": self.server_state, "file": "",
                                                  "error": "", "since": 0})
        state.start()
        self.addCleanup(state.stop)

    def name(self, value="Alex"):
        self.memory.save_username(value)

    def canned_wake(self):
        pack = self.chat.AmadeusPack(assistant_reply_ENG="I'm awake, Alex.",
                                     assistant_reply_JPS="起きたわ、アレックス。")
        return patch.object(self.api, "generate_greeting",
                            side_effect=lambda **kw: (pack, 99, 1))


class LineTests(_BuiltinApp):
    def test_approved_lines(self):
        en, ja, spoken = self.ceremony.line("builtin_intro")
        self.assertIn("tell me your name", en)
        self.assertIn("press Save", en)
        # Her creator's name in katakana, display and voice (2026-10-18).
        self.assertIn("マキセ・クリス", ja)
        self.assertIn("マキセ・クリス", spoken)
        for kind in ("builtin_intro", "builtin_missing"):
            for text in self.ceremony.line(kind)[1:]:
                self.assertNotIn("牧瀬紅莉栖", text)
                self.assertNotIn("まきせくりす", text)
        self.assertIn("エーアイ", spoken)
        self.assertIn("ピーシー", spoken)
        self.assertNotIn("PC", spoken)
        self.assertEqual(self.ceremony.line("builtin_waiting")[0],
                         "Thank you. My mind is almost awake - just a moment.")
        self.assertIn("Run the installer again", self.ceremony.line("builtin_missing")[0])
        self.assertIn("restarting the app", self.ceremony.line("builtin_reminder")[0])
        self.assertIn("restart the app", self.ceremony.line("builtin_fallback")[0])
        for kind in ("builtin_intro", "builtin_waiting", "builtin_missing", "builtin_reminder"):
            self.assertIn(kind, self.ceremony.FIXED_KINDS)


class DecideTests(_BuiltinApp):
    def decide(self, state, ready=False, missing=False, named=False, saved=False, now=None):
        return self.ceremony.decide_builtin(state, ready, missing, named, just_saved=saved, now=now)

    def test_intro_first_even_when_the_model_is_ready(self):
        self.assertEqual(self.decide("", ready=True), ("builtin_intro", "intro"))
        self.assertEqual(self.decide("", ready=True, named=True), ("builtin_intro", "intro"))

    def test_no_wake_without_a_name(self):
        self.ceremony.note("builtin_intro", "intro")
        self.assertEqual(self.decide("intro", ready=True), (None, "intro"))

    def test_name_then_wake_or_wait(self):
        self.ceremony.note("builtin_intro", "intro")
        self.assertEqual(self.decide("intro", ready=True, named=True), ("wake", "intro"))
        self.assertEqual(self.decide("intro", ready=False, named=True),
                         ("builtin_waiting", "connecting"))

    def test_connecting(self):
        self.assertEqual(self.decide("connecting", ready=True, named=True), ("wake", "connecting"))
        # A reopen re-says "almost awake" once, then waits; one reminder.
        self.assertEqual(self.decide("connecting", named=True), ("builtin_waiting", "connecting"))
        self.ceremony.note("builtin_waiting", "connecting")
        self.assertEqual(self.decide("connecting", named=True), (None, "connecting"))
        later = time.monotonic() + 200
        self.assertEqual(self.decide("connecting", named=True, now=later),
                         ("builtin_reminder", "connecting"))
        self.ceremony.note("builtin_reminder", "connecting")
        self.assertEqual(self.decide("connecting", named=True, now=later), (None, "connecting"))

    def test_reopen_with_name_already_saved_skips_the_intro(self):
        self.assertEqual(self.decide("intro", ready=True, named=True), ("wake", "intro"))

    def test_missing_model_once_per_launch(self):
        self.assertEqual(self.decide("", missing=True), ("builtin_missing", "intro"))
        self.ceremony.note("builtin_missing", "intro")
        self.assertEqual(self.decide("intro", missing=True), (None, "intro"))

    def test_done_is_done(self):
        self.assertEqual(self.decide("done", ready=True, named=True), (None, "done"))


class RouteTests(_BuiltinApp):
    def test_full_name_first_flow(self):
        # 1. First launch, model still loading: the built-in intro (a wait line).
        with self.model(False, "server loading"):
            body = self.greet().json
        self.assertEqual((body["kind"], body["ceremony"], body["wait"]),
                         ("builtin_intro", "intro", True))
        _c, voice, _a = self.api._speech_requests[body["speech_id"]]
        self.assertEqual(voice, self.ceremony.line("builtin_intro")[2])
        # 2. The model is ready, but no name yet: she stays quiet.
        with self.model(True):
            body = self.greet().json
        self.assertNotIn("kind", body)
        self.assertEqual(self.state(), "intro")
        # 3. The user saves a name while the model is still loading.
        self.name()
        with self.model(False, "server loading"):
            body = self.greet(just_saved=True).json
        self.assertEqual((body["kind"], body["ceremony"], body["wait"]),
                         ("builtin_waiting", "connecting", True))
        # 4. The model answers: the wake-up ends the ceremony. No API key needed.
        self.assertEqual(self.chat.API_KEY, "")
        with self.model(True), self.canned_wake():
            body = self.greet().json
        self.assertEqual((body["kind"], body["ceremony"]), ("wake", "done"))
        self.assertNotIn("wait", body)
        self.assertEqual(self.state(), "done")

    def test_no_false_waiting_lines_while_her_model_is_stopped(self):
        # Started without her model / unloaded / could not load: never
        # "almost awake" or "taking longer than usual" - and she wakes as
        # soon as it is loaded.
        with self.model(False, "server loading"):
            self.greet()                                   # intro
        self.name()
        for stopped in ("unloaded", "failed"):
            with self.subTest(state=stopped):
                self.server_state = stopped
                with self.model(False, "unreachable"):
                    self.assertNotIn("kind", self.greet(just_saved=True).json)
        self.server_state = "loaded"
        with self.model(True), self.canned_wake():
            self.assertEqual(self.greet().json["kind"], "wake")

    def test_hand_placed_standard_file_wakes_her(self):
        # 2026-10-18 test install: option B (model_ok false) + her standard
        # 12B downloaded by hand -> was "missing" forever, never woke.
        _write(self.cfg_path, _config(self.base, model_ok=False))
        import builtin_llm
        self.assertFalse(builtin_llm.model_missing())
        with self.model(False, "server loading"):
            self.assertEqual(self.greet().json["kind"], "builtin_intro")
        self.name()
        with self.model(True), self.canned_wake():
            self.assertEqual(self.greet(just_saved=True).json["kind"], "wake")

    def test_model_ready_at_first_probe_still_introduces(self):
        with self.model(True), self.canned_wake() as gen:
            body = self.greet().json
        self.assertEqual(body["kind"], "builtin_intro")
        gen.assert_not_called()

    def test_wake_failure_ships_the_builtin_fallback(self):
        self.memory.save_ceremony_state("connecting")
        self.name()
        with self.model(True), patch.object(self.api, "generate_greeting",
                                            side_effect=RuntimeError("boom")):
            body = self.greet().json
        self.assertEqual((body["kind"], body["ceremony"]), ("builtin_fallback", "done"))
        self.assertEqual(body["response"], self.ceremony.line("builtin_fallback")[0])

    def test_missing_model_line(self):
        _write(self.cfg_path, _config(self.base, model_file=str(self.base / "gone.gguf")))
        body = self.greet().json
        # llama.cpp is there, only the model file is not (2026-10-19:
        # say so honestly).
        self.assertEqual((body["kind"], body["wait"]), ("builtin_no_model", True))
        self.assertIn("model file isn't downloaded", body["response"])
        self.assertNotIn("kind", self.greet().json)   # once per launch

    def test_missing_program_line(self):
        _write(self.cfg_path, _config(self.base, server_ok=False))
        body = self.greet().json
        self.assertEqual((body["kind"], body["wait"]), ("builtin_missing", True))
        self.assertIn("Run the installer again", body["response"])

    def test_no_model_line_texts(self):
        en, ja, spoken = self.ceremony.line("builtin_no_model")
        self.assertIn("GGUF", en)
        self.assertIn("マキセ・クリス", ja)
        self.assertIn("ダウンロードされていない", ja)
        for latin in ("GGUF", "PC", "AI"):
            self.assertNotIn(latin, spoken)   # spelled in kana for her voice
        self.assertIn("ジージーユーエフ", spoken)

    def test_custom_mode_keeps_the_old_ceremony(self):
        _write(self.cfg_path, _config(self.base, mode="custom"))
        with self.model(False, "model server unreachable"):
            body = self.greet().json
        self.assertEqual(body["kind"], "intro")
        self.assertEqual(body["response"], self.ceremony.line("intro")[0])


class WakeWordingTests(_BuiltinApp):
    def run_wake(self):
        captured = {}

        class _Bound:
            def invoke(self, messages):
                captured["messages"] = messages
                return SimpleNamespace(content="", tool_calls=[{
                    "name": "AmadeusPack", "id": "w1",
                    "args": {"assistant_reply_JPS": "起きたわ。",
                             "assistant_reply_ENG": "I'm awake."}}])

        fake = SimpleNamespace(bind_tools=lambda tools, tool_choice=None: _Bound())
        with patch.object(self.chat, "get_llm", return_value=fake), \
             patch.object(self.chat, "reset_llm"), \
             patch.object(self.chat, "_finalize", lambda pack, llm: pack), \
             patch.object(self.chat, "stats", SimpleNamespace(load_stat=lambda key: 62.0)), \
             patch.object(self.memory, "load_default_personality_messages",
                          return_value=[{"role": "system", "content": "P"}]):
            self.chat.generate_greeting(mode="wake")
        return captured["messages"]

    def test_builtin_wake_wording(self):
        self.name()
        messages = self.run_wake()
        text = "\n".join(str(m.get("content", "")) for m in messages)
        self.assertIn("runs on the user's own PC", text)
        self.assertIn("Alex", text)
        self.assertNotIn("connected you", text)
        self.assertEqual(messages[-1]["content"], self.chat.WAKE_ARRIVAL_BUILTIN)

    def test_custom_wake_wording_unchanged(self):
        _write(self.cfg_path, _config(self.base, mode="custom"))
        self.name()
        messages = self.run_wake()
        self.assertEqual(messages[-1]["content"], self.chat.WAKE_ARRIVAL)


class SettingsTests(_BuiltinApp):
    def test_builtin_model_has_its_own_conversation_number(self):
        # No tier default in the file (an older install): all the context holds.
        self.assertEqual(self.memory.effective_context_budget(), 16384 - 6000 - 4096)
        self.assertEqual(self.client.get("/getContextBudget").json,
                         {"budget": 6288, "builtin_cap": 6288, "default": 6288, "mode": "builtin"})
        # The tier's default is where it starts.
        _write(self.cfg_path, _config(self.base, context=49152, history_default=32000, n=1))
        self.assertEqual(self.memory.effective_context_budget(), 32000)
        info = self.client.get("/getContextBudget").json
        self.assertEqual((info["budget"], info["builtin_cap"], info["default"]), (32000, 39056, 32000))
        # Settings saves the built-in number - clamped to what the context holds -
        # and leaves the custom model's number alone.
        out = self.client.post("/setContextBudget", json={"budget": 99999}).json
        self.assertEqual(out["budget"], 39056)
        self.assertEqual(self.client.post("/setContextBudget", json={"budget": 12000}).json["budget"], 12000)
        self.assertEqual(self.memory.effective_context_budget(), 12000)
        self.assertEqual(self.memory.load_context_budget(), 40000)
        # Custom mode: the custom number, untouched; switching back keeps 12000.
        self.client.post("/setBrainMode", json={"mode": "custom"})
        self.assertEqual(self.memory.effective_context_budget(), 40000)
        self.assertEqual(self.client.get("/getContextBudget").json,
                         {"budget": 40000, "builtin_cap": None, "default": 40000, "mode": "custom"})
        self.client.post("/setContextBudget", json={"budget": 50000})
        self.client.post("/setBrainMode", json={"mode": "builtin"})
        self.assertEqual(self.memory.effective_context_budget(), 12000)
        self.assertEqual(self.memory.load_context_budget(), 50000)

    def test_brain_mode_routes(self):
        info = self.client.get("/brainMode").json
        self.assertEqual((info["available"], info["mode"], info["model_label"], info["model_missing"]),
                         (True, "builtin", "Gemma 4 12B (QAT)", False))
        server = self.api.builtin_server
        idle = {"state": "unloaded", "file": "", "error": "", "since": 0}
        with patch.object(server, "load", return_value=idle) as load,              patch.object(server, "unload", return_value=idle) as unload,              patch.object(server, "status", return_value=idle):
            # Switching applies at once: custom unloads her model ...
            res = self.client.post("/setBrainMode", json={"mode": "custom"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual((res.json["mode"], res.json["restart_needed"]), ("custom", False))
            unload.assert_called_once()
            import builtin_llm
            self.assertFalse(builtin_llm.active())
            cfg = builtin_llm.load_config()
            self.assertTrue(cfg["model_ok"])          # the rest of the file is kept
            # ... and built-in loads it again.
            res = self.client.post("/setBrainMode", json={"mode": "builtin"})
            self.assertEqual(res.json["mode"], "builtin")
            load.assert_called_once()
        self.assertEqual(self.client.post("/setBrainMode", json={"mode": "x"}).status_code, 400)

    def test_no_builtin_model_to_switch_to(self):
        self.cfg_path.unlink()
        info = self.client.get("/brainMode").json
        self.assertEqual((info["available"], info["mode"]), (False, "custom"))
        res = self.client.post("/setBrainMode", json={"mode": "builtin"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("installer", res.json["message"])

    def test_model_field_shows_the_users_own_model(self):
        with patch.object(self.chat, "LLM_Model", "google/gemini-3.8-flash"):
            self.assertEqual(self.client.get("/getCurrLLMModel").json["message"],
                             "google/gemini-3.8-flash")
            # (the model actually used is the built-in one)
            self.assertEqual(self.chat.getLLMModel(), "amadeus-builtin")


if __name__ == "__main__":
    unittest.main()

"""Restart from Settings, backend side (2026-11-01).

Settings asks the backend; the backend leaves a request for the launcher
(only when a launcher started it) and reports the live state of her parts.
After the restart the page asks for a greeting with restarted=true: she
says she is back, stored as a "restart" line that never counts as an
ignored greeting.
"""

from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_greeting_regenerate import _Base, _FakeLLM  # noqa: E402

import restart_control  # noqa: E402


class RestartControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_launcher_no_restart(self):
        with patch.dict(os.environ, {"AMADEUS_LAUNCHER_DIR": ""}):
            self.assertEqual(restart_control.request_restart(),
                             {"ok": False, "reason": "no-launcher"})
            self.assertFalse(restart_control.system_status(probe=lambda p: True)["launcher"])

    def test_request_leaves_the_file_and_a_requested_status(self):
        result = restart_control.request_restart(self.folder, now=1000.0)
        self.assertTrue(result["ok"])
        self.assertTrue((self.folder / "restart.request").exists())
        status = restart_control.load_status(self.folder)
        self.assertEqual(status["state"], "requested")
        self.assertEqual(status["parts"]["brain"], "waiting")

    def test_a_running_restart_blocks_a_second_one(self):
        restart_control.request_restart(self.folder, now=1000.0)
        self.assertEqual(restart_control.request_restart(self.folder, now=1010.0),
                         {"ok": False, "reason": "busy"})

    def test_a_stale_running_status_does_not_block(self):
        restart_control.request_restart(self.folder, now=1000.0)
        later = 1000.0 + restart_control.STALE_SECONDS + 1
        self.assertTrue(restart_control.request_restart(self.folder, now=later)["ok"])

    def test_a_finished_restart_allows_the_next(self):
        (self.folder / "restart_status.json").write_text(
            json.dumps({"state": "done", "started": 1000.0}), encoding="utf-8")
        self.assertTrue(restart_control.request_restart(self.folder, now=1001.0)["ok"])

    def test_shutdown_needs_a_launcher_that_knows_it(self):
        with patch.dict(os.environ, {"AMADEUS_LAUNCHER_DIR": self.tmp.name,
                                     "AMADEUS_LAUNCHER_SHUTDOWN": ""}):
            self.assertEqual(restart_control.request_shutdown(),
                             {"ok": False, "reason": "no-launcher"})
            self.assertFalse(restart_control.system_status(probe=lambda p: True)["shutdown"])
        with patch.dict(os.environ, {"AMADEUS_LAUNCHER_DIR": self.tmp.name,
                                     "AMADEUS_LAUNCHER_SHUTDOWN": "1"}):
            self.assertTrue(restart_control.system_status(probe=lambda p: True)["shutdown"])
            self.assertEqual(restart_control.request_shutdown(), {"ok": True})
        self.assertTrue((self.folder / "shutdown.request").exists())

    def test_status_reports_each_part(self):
        restart_control.request_restart(self.folder, now=1000.0)
        status = restart_control.system_status(
            self.folder, probe=lambda port: port == 9880)
        self.assertTrue(status["launcher"])
        self.assertEqual(status["parts"], {"brain": "ready", "voice": "ready",
                                           "memory": "offline"})
        self.assertEqual(status["restart"]["state"], "requested")


class RestartRouteTests(_Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"AMADEUS_LAUNCHER_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        super().tearDown()

    def test_restart_route_leaves_a_request(self):
        res = self.client.post("/system/restart", json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["restart"]["state"], "requested")
        self.assertTrue((Path(self.tmp.name) / "restart.request").exists())
        again = self.client.post("/system/restart", json={})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json["reason"], "busy")

    def test_restart_route_without_launcher(self):
        with patch.dict(os.environ, {"AMADEUS_LAUNCHER_DIR": ""}):
            res = self.client.post("/system/restart", json={})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json["reason"], "no-launcher")

    def test_shutdown_route(self):
        with patch.dict(os.environ, {"AMADEUS_LAUNCHER_SHUTDOWN": "1"}):
            res = self.client.post("/system/shutdown", json={})
        self.assertEqual(res.status_code, 200)
        self.assertTrue((Path(self.tmp.name) / "shutdown.request").exists())

    def test_shutdown_route_with_an_older_launcher(self):
        with patch.dict(os.environ, {"AMADEUS_LAUNCHER_SHUTDOWN": ""}):
            res = self.client.post("/system/shutdown", json={})
        self.assertEqual(res.status_code, 409)
        self.assertFalse((Path(self.tmp.name) / "shutdown.request").exists())

    def test_status_route(self):
        with patch.object(restart_control, "port_open", lambda port, timeout=0.3: False):
            res = self.client.get("/system/status")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json["launcher"])
        self.assertEqual(res.json["parts"]["brain"], "ready")
        self.assertEqual(res.json["parts"]["voice"], "offline")
        self.assertIsNone(res.json["restart"])


class RestartGreetingTests(_Base):
    def greet_route(self, body, fake):
        with self.patched(fake), patch.object(
                self.api, "model_ready", return_value={"ready": True, "reason": ""}):
            return self.client.post("/greet", json=body)

    def test_restarted_greeting_says_she_is_back(self):
        fake = _FakeLLM(eng="I'm back.", jps="戻ったわ。")
        res = self.greet_route({"restarted": True}, fake)
        self.assertTrue(res.json["ready"])
        self.assertEqual(self.db_row(res.json["assistant_id"])[2], "restart")
        self.assertIn("You were just restarted", self.prompt_text(fake))

    def test_plain_greeting_is_unchanged(self):
        fake = _FakeLLM()
        res = self.greet_route({}, fake)
        self.assertEqual(self.db_row(res.json["assistant_id"])[2], "startup")
        self.assertNotIn("You were just restarted", self.prompt_text(fake))

    def test_restart_greeting_is_never_an_ignored_greeting(self):
        with patch.object(self.chat.feelings, "note_ignored_greeting") as note:
            self.greet_route({"restarted": True}, _FakeLLM())
        note.assert_not_called()
        conv = self.memory.load_active_conversation()
        self.assertEqual(self.memory.unanswered_greeting_count(conv), 0)

    def test_restart_greeting_regenerates_as_restart(self):
        res = self.greet_route({"restarted": True}, _FakeLLM())
        fake = _FakeLLM(eng="Back again.", jps="また戻った。")
        _p, nid, _c = self.regen(res.json["assistant_id"], fake)
        self.assertEqual(self.db_row(nid)[2], "restart")
        self.assertIn("You were just restarted", self.prompt_text(fake))


if __name__ == "__main__":
    unittest.main()

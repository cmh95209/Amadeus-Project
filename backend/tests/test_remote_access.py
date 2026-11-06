"""Remote access through Tailscale (remote_access.py, settings tab "Mobile and
remote access", docs/remote-access.md).

Covers: reading this PC's `tailscale serve` set-up (on / partly / off /
missing, Funnel spotted), the two fixed commands behind "Set it up for me"
and "Turn it off" (the first-time "allow Serve" page, a failure, Tailscale
missing) and the routes: only a request from this PC itself may change
anything - a phone's request through `tailscale serve` is refused.
Fakes only: no real tailscale command runs.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import api  # noqa: E402
import remote_access  # noqa: E402


class DescribeTests(unittest.TestCase):
    STATUS = {"BackendState": "Running", "Self": {"DNSName": "my-pc.tail123.ts.net."}}
    SERVE = {"Web": {"my-pc.tail123.ts.net:443": {"Handlers": {"/": {"Proxy": "http://localhost:5173"}}},
                     "my-pc.tail123.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5050"}}}}}

    def test_on(self):
        self.assertEqual(remote_access.describe(self.STATUS, self.SERVE),
                         {"state": "on", "address": "https://my-pc.tail123.ts.net",
                          "backend": "https://my-pc.tail123.ts.net:8443"})

    def test_partly_off_missing(self):
        only_page = {"Web": {k: v for k, v in self.SERVE["Web"].items() if k.endswith(":443")}}
        self.assertEqual(remote_access.describe(self.STATUS, only_page)["state"], "partly")
        self.assertEqual(remote_access.describe(self.STATUS, None)["state"], "partly")
        self.assertEqual(remote_access.describe({"BackendState": "Stopped"}, None), {"state": "off"})
        self.assertEqual(remote_access.describe(None, None), {"state": "missing"})

    def test_funnel_is_spotted(self):
        public = dict(self.SERVE, AllowFunnel={"my-pc.tail123.ts.net:443": True})
        self.assertTrue(remote_access.describe(self.STATUS, public)["funnel"])
        self.assertNotIn("funnel", remote_access.describe(self.STATUS, self.SERVE))
        closed = dict(self.SERVE, AllowFunnel={"my-pc.tail123.ts.net:443": False})
        self.assertNotIn("funnel", remote_access.describe(self.STATUS, closed))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.ran = []
        patchers = [patch.object(remote_access, "_exe", return_value="tailscale"),
                    patch.object(remote_access, "access", return_value={"state": "on"})]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def fake_run(self, outputs):
        outputs = list(outputs)

        def run(cmd, **kwargs):
            self.ran.append(cmd[1:])
            result = outputs.pop(0)
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(stdout=result[0].encode(), stderr=b"", returncode=result[1])
        return patch.object(remote_access.subprocess, "run", side_effect=run)

    def test_on_runs_exactly_the_two_serve_commands(self):
        with self.fake_run([("", 0), ("", 0)]):
            self.assertEqual(remote_access.turn_on(), {"ok": True, "access": {"state": "on"}})
        self.assertEqual(self.ran, [list(c) for c in remote_access.ON_COMMANDS])
        self.assertEqual(self.ran[0][-2:], ["--https=443", "http://localhost:5173"])
        self.assertEqual(self.ran[1][-2:], ["--https=8443", "http://127.0.0.1:5050"])
        self.assertTrue(all(c[0] == "serve" for c in self.ran))   # never funnel

    def test_off_runs_the_two_off_commands(self):
        with self.fake_run([("", 0), ("", 0)]):
            self.assertTrue(remote_access.turn_off()["ok"])
        self.assertEqual(self.ran, [list(c) for c in remote_access.OFF_COMMANDS])

    def test_first_time_allow_serve_link(self):
        waiting = subprocess.TimeoutExpired(
            ["tailscale"], 20,
            output=b"Serve is not enabled on your tailnet.\nTo enable, visit:\n\n"
                   b"         https://login.tailscale.com/f/serve?node=abc123\n")
        with self.fake_run([waiting]):
            self.assertEqual(remote_access.turn_on(),
                             {"ok": False, "reason": "enable",
                              "link": "https://login.tailscale.com/f/serve?node=abc123"})
        self.assertEqual(len(self.ran), 1)   # the second command waits for the first

    def test_failure_stops_and_says_why(self):
        with self.fake_run([("Access denied.", 1)]):
            self.assertEqual(remote_access.turn_on(),
                             {"ok": False, "reason": "failed", "message": "Access denied."})
        self.assertEqual(len(self.ran), 1)

    def test_missing(self):
        with patch.object(remote_access, "_exe", return_value=None):
            self.assertEqual(remote_access.turn_on(), {"ok": False, "reason": "missing"})


class FindTailscaleTests(unittest.TestCase):
    def test_path_first_then_the_usual_install_places(self):
        win = r"C:\Program Files\Tailscale\tailscale.exe"
        with patch.object(remote_access.shutil, "which", return_value="/usr/bin/tailscale"):
            self.assertEqual(remote_access._exe(), "/usr/bin/tailscale")
        with patch.object(remote_access.shutil, "which", return_value=None), \
             patch.object(remote_access.os.path, "isfile", side_effect=lambda p: p == win):
            self.assertEqual(remote_access._exe(), win)
        with patch.object(remote_access.shutil, "which", return_value=None), \
             patch.object(remote_access.os.path, "isfile", side_effect=lambda p: p == remote_access._MAC_CLI):
            self.assertEqual(remote_access._exe(), remote_access._MAC_CLI)
        with patch.object(remote_access.shutil, "which", return_value=None), \
             patch.object(remote_access.os.path, "isfile", return_value=False):
            self.assertIsNone(remote_access._exe())


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = api.application.test_client()

    def test_access_route_reads_only(self):
        with patch.object(remote_access, "access", return_value={"state": "off"}):
            self.assertEqual(self.client.get("/remote/access").get_json(), {"state": "off"})

    def test_setup_from_this_pc(self):
        with patch.object(remote_access, "turn_on", return_value={"ok": True}) as on, \
             patch.object(remote_access, "turn_off", return_value={"ok": True}) as off:
            self.assertEqual(self.client.post("/remote/setup", json={"action": "on"}).status_code, 200)
            self.assertEqual(self.client.post("/remote/setup", json={"action": "off"}).status_code, 200)
            self.assertEqual(self.client.post("/remote/setup", json={"action": "funnel"}).status_code, 400)
        on.assert_called_once()
        off.assert_called_once()

    def test_setup_refused_from_a_phone(self):
        with patch.object(remote_access, "turn_on") as on, patch.object(remote_access, "turn_off") as off:
            through_serve = self.client.post("/remote/setup", json={"action": "off"},
                                             headers={"X-Forwarded-For": "100.64.0.7"})
            other_device = self.client.post("/remote/setup", json={"action": "on"},
                                            environ_base={"REMOTE_ADDR": "100.64.0.7"})
        self.assertEqual(through_serve.status_code, 403)
        self.assertEqual(other_device.status_code, 403)
        on.assert_not_called()
        off.assert_not_called()


if __name__ == "__main__":
    unittest.main()

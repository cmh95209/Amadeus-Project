# -*- coding: utf-8 -*-
"""Offline checks for installer v4.13's speed + no-administrator changes
(2026-10-24), the launcher side:
  1. her memory's packages install with uv when it is there, and pip takes
     over when uv is missing or fails;
  2. the launcher finds Amadeus' own portable Node.js
     (%USERPROFILE%\\.amadeus-tools\\node\\npm.cmd) when none is on PATH.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND.parent / "scripts"


def _launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_speed_test", SCRIPTS / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MemorySetupTests(unittest.TestCase):
    def setUp(self):
        self.l = _launcher()
        self.calls = []

    def _run(self, codes, uv_present):
        codes = list(codes)

        def fake_run_tool(label, cmd, passthrough=False):
            self.calls.append(cmd)
            return codes.pop(0)
        real_find = importlib.util.find_spec
        with patch.object(self.l, "run_tool", side_effect=fake_run_tool), \
             patch.object(self.l, "say", lambda *a, **k: None), \
             patch.object(self.l, "note", lambda *a, **k: None), \
             patch.object(self.l.importlib.util, "find_spec",
                          side_effect=lambda name: (object() if uv_present else None) if name == "uv" else real_find(name)):
            return self.l._pip_install_sidecar()

    def test_uv_first(self):
        self.assertTrue(self._run([0], uv_present=True))
        self.assertEqual(self.calls[0][1:5], ["-m", "uv", "pip", "install"])
        self.assertEqual(len(self.calls), 1)

    def test_pip_when_uv_fails(self):
        self.assertTrue(self._run([1, 0], uv_present=True))
        self.assertIn("uv", self.calls[0])
        self.assertEqual(self.calls[1][1:4], ["-m", "pip", "install"])

    def test_pip_without_uv(self):
        self.assertTrue(self._run([0], uv_present=False))
        self.assertEqual(self.calls[0][1:4], ["-m", "pip", "install"])


class PortableNodeTests(unittest.TestCase):
    def test_finds_amadeus_own_node(self):
        l = _launcher()
        with tempfile.TemporaryDirectory() as home:
            npm = Path(home) / ".amadeus-tools" / "node" / "npm.cmd"
            npm.parent.mkdir(parents=True)
            npm.write_text("@echo off", encoding="utf-8")
            with patch.object(l.shutil, "which", return_value=None), \
                 patch.object(l.Path, "home", return_value=Path(home)):
                self.assertEqual(l.find_npm(), str(npm))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Offline checks for the launcher's per-install conda environment names.

An install made with the installer's -EnvSuffix option (a test install next
to the real one) records its suffix in .runtime/env_suffix.txt. Covers:
  1. no file = the plain names (every normal install is unchanged);
  2. a recorded suffix is appended to every environment name;
  3. a malformed file is ignored instead of producing a bad name;
  4. the memory sidecar's base Python and the voice engine's `conda run`
     both use the suffixed names.
"""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_LAUNCHER = Path(__file__).resolve().parents[2] / "scripts" / "launcher.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_under_test", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EnvSuffixTests(unittest.TestCase):
    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_envs_"))
        self.launcher.ENV_SUFFIX_FILE = self.tmp / "env_suffix.txt"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        self.launcher.ENV_SUFFIX_FILE.write_text(text, encoding="utf-8")

    def test_no_file_means_plain_names(self):
        self.assertEqual(self.launcher.env_suffix(), "")
        self.assertEqual(self.launcher.env_name("amadeus-cm"), "amadeus-cm")
        self.assertEqual(self.launcher.env_name("GPTSoVITS"), "GPTSoVITS")

    def test_recorded_suffix_is_appended(self):
        self._write("-test")
        self.assertEqual(self.launcher.env_name("amadeus-cm"), "amadeus-cm-test")
        self.assertEqual(self.launcher.env_name("GPTSoVITS"), "GPTSoVITS-test")

    def test_surrounding_whitespace_is_ignored(self):
        self._write("  -test\r\n")
        self.assertEqual(self.launcher.env_suffix(), "-test")

    def test_malformed_file_is_ignored(self):
        for bad in ("", "-", "test", "-te st", "-../x", "-a\\b"):
            with self.subTest(bad=bad):
                self._write(bad)
                self.assertEqual(self.launcher.env_suffix(), "")

    def test_sidecar_base_python_uses_suffixed_env(self):
        self._write("-test")
        conda_home = self.tmp / "miniconda3"
        scripts = conda_home / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "conda.exe").write_text("", encoding="utf-8")
        plain = conda_home / "envs" / "amadeus-cm"
        mine = conda_home / "envs" / "amadeus-cm-test"
        rel = "python.exe" if os.name == "nt" else os.path.join("bin", "python")
        for env in (plain, mine):
            (env / rel).parent.mkdir(parents=True, exist_ok=True)
            (env / rel).write_text("", encoding="utf-8")
        with patch.object(self.launcher, "find_conda", return_value=str(scripts / "conda.exe")):
            self.assertEqual(self.launcher._cm_base_python(), mine / rel)
            # The plain env is never used as a stand-in for a missing own env.
            shutil.rmtree(mine)
            self.assertIsNone(self.launcher._cm_base_python())

    def test_voice_engine_runs_in_suffixed_env(self):
        cmd = self.launcher.gptsovits_command("conda")
        self.assertEqual(cmd[1:4], ["run", "-n", "GPTSoVITS"])
        self._write("-test")
        cmd = self.launcher.gptsovits_command("conda")
        self.assertEqual(cmd[1:4], ["run", "-n", "GPTSoVITS-test"])


if __name__ == "__main__":
    unittest.main()

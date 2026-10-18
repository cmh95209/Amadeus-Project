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

    def test_conda_home_from_any_conda_on_path(self):
        home = self.tmp / "miniconda3"
        (home / "envs").mkdir(parents=True)
        (home / "conda-meta").mkdir()
        for rel in (("Scripts", "conda.exe"), ("Library", "bin", "conda.BAT"),
                    ("condabin", "conda.bat"), ("bin", "conda")):
            exe = home.joinpath(*rel)
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("", encoding="utf-8")
            with self.subTest(conda=str(Path(*rel))):
                self.assertEqual(self.launcher.conda_home(str(exe)), home.resolve())
        stray = self.tmp / "elsewhere" / "conda.exe"
        stray.parent.mkdir()
        stray.write_text("", encoding="utf-8")
        self.assertIsNone(self.launcher.conda_home(str(stray)))

    def test_sidecar_base_python_found_via_library_bin(self):
        # 2026-10-18: PATH found miniconda3\Library\bin\conda.BAT first.
        self._write("-test")
        home = self.tmp / "miniconda3"
        bat = home / "Library" / "bin" / "conda.BAT"
        bat.parent.mkdir(parents=True)
        bat.write_text("", encoding="utf-8")
        rel = "python.exe" if os.name == "nt" else os.path.join("bin", "python")
        py = home / "envs" / "amadeus-cm-test" / rel
        py.parent.mkdir(parents=True)
        py.write_text("", encoding="utf-8")
        with patch.object(self.launcher, "find_conda", return_value=str(bat)):
            self.assertEqual(self.launcher._cm_base_python(), py.resolve())

    def test_too_old_sidecar_venv_is_detected(self):
        self.launcher.CM_DIR = self.tmp / "memory_sidecar"
        cfg = self.launcher.CM_DIR / "venv" / "pyvenv.cfg"
        cfg.parent.mkdir(parents=True)
        for version, ok in (("3.10.22", False), ("3.11.9", False), ("3.12.0", True), ("3.13.5", True)):
            with self.subTest(version=version):
                cfg.write_text(f"home = x\nversion = {version}\n", encoding="utf-8")
                self.assertEqual(self.launcher._cm_venv_python_ok(), ok)
        cfg.unlink()
        self.assertTrue(self.launcher._cm_venv_python_ok())   # unreadable = as before

    def test_stale_old_venv_is_rebuilt_not_retried(self):
        sc = self.tmp / "memory_sidecar"
        (sc / "venv").mkdir(parents=True)
        (sc / "cm_sidecar.py").write_text("", encoding="utf-8")
        (sc / "requirements.txt").write_text("", encoding="utf-8")
        (sc / "venv" / "pyvenv.cfg").write_text("version = 3.10.22\n", encoding="utf-8")
        self.launcher.CM_DIR = sc
        fake_py = sc / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
        fake_py.parent.mkdir(parents=True)
        fake_py.write_text("", encoding="utf-8")
        good = self.tmp / "py313" / "python.exe"
        calls = []
        with patch.object(self.launcher, "_cm_base_python", return_value=good), \
             patch.object(self.launcher, "run_tool", side_effect=lambda label, cmd, **k: calls.append(cmd) or 0), \
             patch.object(self.launcher, "_pip_install_sidecar", return_value=True):
            self.assertTrue(self.launcher.ensure_cm_sidecar())
        self.assertEqual(calls[0][:3], [str(good), "-m", "venv"])   # rebuilt from the right Python

    def test_no_suitable_python_means_no_doomed_venv(self):
        sc = self.tmp / "memory_sidecar"
        sc.mkdir()
        (sc / "cm_sidecar.py").write_text("", encoding="utf-8")
        (sc / "requirements.txt").write_text("", encoding="utf-8")
        self.launcher.CM_DIR = sc
        with patch.object(self.launcher, "_cm_base_python", return_value=None), \
             patch.object(self.launcher.sys, "version_info", (3, 10, 22)), \
             patch.object(self.launcher, "run_tool") as run:
            self.assertFalse(self.launcher.ensure_cm_sidecar())
            run.assert_not_called()
        self.assertFalse((sc / "venv").exists())

    def test_voice_engine_runs_in_suffixed_env(self):
        cmd = self.launcher.gptsovits_command("conda")
        self.assertEqual(cmd[1:4], ["run", "-n", "GPTSoVITS"])
        self._write("-test")
        cmd = self.launcher.gptsovits_command("conda")
        self.assertEqual(cmd[1:4], ["run", "-n", "GPTSoVITS-test"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Offline checks for the launcher's per-install conda environment names.

An install made with the installer's -EnvSuffix option (a test install next
to the real one) records its suffix in .runtime/env_suffix.txt. Covers:
  1. no file = the plain names (every normal install is unchanged);
  2. a recorded suffix is appended to every environment name;
  3. a malformed file is ignored instead of producing a bad name;
  4. the memory sidecar's base Python and the voice engine's start command
     both use the suffixed names;
  5. the voice engine runs with its own environment's Python, by FULL path
     (a nested `conda run` once ran it with a different, newer Python,
     2026-11-10): the command uses the full path when the interpreter file
     exists (Windows and POSIX layouts), falls back to `conda run` when it
     does not, and the environment gets the PATH entries + CONDA_PREFIX that
     activation would set up.
"""
import importlib.util
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
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
        # Keep the lookup inside the pretend folders: since v4.13.3 it also
        # looks beside the running Python's environment and in ~/.conda/envs
        # (this PC's real ones may hold an "amadeus-cm-test").
        with patch.object(self.launcher, "find_conda", return_value=str(scripts / "conda.exe")), \
             patch.object(self.launcher.sys, "executable", str(self.tmp / "py" / "python.exe")), \
             patch.object(self.launcher.Path, "home", return_value=self.tmp / "home"):
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


class _OsLike:
    """The launcher's `os` with a pinned name/pathsep: the layout tests
    must hold on a Windows AND a POSIX runner alike (they pretend to be on
    the other side of the platform wall)."""

    def __init__(self, name, pathsep):
        self.name = name
        self.pathsep = pathsep

    def __getattr__(self, item):
        return getattr(os, item)


class VoicePythonTests(unittest.TestCase):
    """The voice engine runs with the voice environment's own interpreter,
    by FULL path (<conda home>/envs/<env>/bin/python or ...\\python.exe),
    with the PATH entries + CONDA_PREFIX that activation would set up.
    `conda run -n <env>` is only the fallback when that file does not exist
    (2026-11-10: a tester's PC ran the engine through the launcher's nested
    conda run, and a DIFFERENT, newer Python ran it - no soundfile/torch)."""

    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_voicepy_"))
        self.launcher.ENV_SUFFIX_FILE = self.tmp / "env_suffix.txt"
        self.home = self.tmp / "miniforge3"
        (self.home / "bin").mkdir(parents=True)
        (self.home / "bin" / "conda").write_text("", encoding="utf-8")
        (self.home / "envs").mkdir()
        self.conda = str(self.home / "bin" / "conda")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env_dir(self, spelling="GPTSoVITS"):
        env_dir = self.home / "envs" / spelling
        env_dir.mkdir(parents=True, exist_ok=True)
        return env_dir

    def _make_python(self, env_dir, posix):
        py = env_dir / (Path("bin", "python") if posix else "python.exe")
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("", encoding="utf-8")
        return py

    def test_command_uses_the_envs_own_python_windows_layout(self):
        env_dir = self._env_dir()
        py = self._make_python(env_dir, posix=False)
        with patch.object(self.launcher, "os", _OsLike("nt", ";")):
            self.assertEqual(self.launcher.voice_python(self.conda), py)
            cmd = self.launcher.gptsovits_command(self.conda)
        self.assertEqual(cmd, [str(py),
                               str(self.launcher.BACKEND_DIR / "start_gptsovits.py")])
        self.assertNotIn("run", cmd)

    def test_command_uses_the_envs_own_python_posix_layout(self):
        env_dir = self._env_dir()
        py = self._make_python(env_dir, posix=True)
        with patch.object(self.launcher, "os", _OsLike("posix", ":")):
            self.assertEqual(self.launcher.voice_python(self.conda), py)
            cmd = self.launcher.gptsovits_command(self.conda)
        self.assertEqual(cmd, [str(py),
                               str(self.launcher.BACKEND_DIR / "start_gptsovits.py")])

    def test_missing_python_falls_back_to_conda_run(self):
        self._env_dir()                      # the env exists, its Python does not
        for os_like in (_OsLike("nt", ";"), _OsLike("posix", ":")):
            with self.subTest(layout=os_like.name), \
                 patch.object(self.launcher, "os", os_like):
                self.assertIsNone(self.launcher.voice_python(self.conda))
                cmd = self.launcher.gptsovits_command(self.conda)
                self.assertEqual(cmd[1:4], ["run", "-n", "GPTSoVITS"])
                self.assertEqual(cmd[0], self.conda)
                self.assertEqual(cmd[4:], ["--no-capture-output", "python",
                                            str(self.launcher.BACKEND_DIR / "start_gptsovits.py")])
                self.assertEqual(self.launcher.gptsovits_env(self.conda), {})
        # No conda at all: the old command shape, with nothing to find.
        cmd = self.launcher.gptsovits_command("")
        self.assertEqual(cmd[1:4], ["run", "-n", "GPTSoVITS"])

    def test_env_sets_path_and_conda_prefix_windows_layout(self):
        env_dir = self._env_dir()
        self._make_python(env_dir, posix=False)
        rest = os.environ.get("PATH", "")
        with patch.object(self.launcher, "os", _OsLike("nt", ";")):
            env = self.launcher.gptsovits_env(self.conda)
        self.assertEqual(env.get("CONDA_PREFIX"), str(env_dir))
        expected = [str(env_dir), os.path.join(str(env_dir), "Library", "bin"),
                    os.path.join(str(env_dir), "Scripts")]
        self.assertEqual(env.get("PATH", ""),
                         ";".join(expected + [rest]))   # rest may be ""

    def test_env_sets_path_and_conda_prefix_posix_layout(self):
        env_dir = self._env_dir()
        self._make_python(env_dir, posix=True)
        rest = os.environ.get("PATH", "")
        with patch.object(self.launcher, "os", _OsLike("posix", ":")):
            env = self.launcher.gptsovits_env(self.conda)
        self.assertEqual(env.get("CONDA_PREFIX"), str(env_dir))
        expected = [os.path.join(str(env_dir), "bin")]
        self.assertEqual(env.get("PATH", ""),
                         ":".join(expected + [rest]))   # rest may be ""

    def test_env_check_asks_the_envs_own_python(self):
        env_dir = self._env_dir()
        py = self._make_python(env_dir, posix=False)
        run = mock.Mock(return_value=types.SimpleNamespace(
            stdout="OK\n", stderr=""))
        with patch.object(self.launcher, "os", _OsLike("nt", ";")), \
             mock.patch("subprocess.run", run):
            self.assertEqual(self.launcher.voice_env_check(self.conda), "")
        run.assert_called_once()
        command = run.call_args[0][0]
        self.assertEqual(command[0], str(py))
        self.assertEqual(command[1], "-c")
        self.assertNotIn("run", command)
        self.assertNotIn("-n", command)
        env = run.call_args[1].get("env")
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("CONDA_PREFIX"), str(env_dir))
        self.assertIn("GPTSoVITS", env.get("PATH", ""))
        self.assertEqual(run.call_args[1].get("timeout"), 30)

    def test_env_check_falls_back_to_conda_run_without_extra_env(self):
        self._env_dir()                      # no interpreter file in the env
        run = mock.Mock(return_value=types.SimpleNamespace(
            stdout="OK\n", stderr=""))
        with patch.object(self.launcher, "os", _OsLike("nt", ";")), \
             mock.patch("subprocess.run", run):
            self.assertEqual(self.launcher.voice_env_check(self.conda), "")
        command = run.call_args[0][0]
        self.assertEqual(command[0], self.conda)
        self.assertEqual(command[1:4], ["run", "-n", "GPTSoVITS"])
        self.assertNotIn("env", run.call_args[1])


if __name__ == "__main__":
    unittest.main()

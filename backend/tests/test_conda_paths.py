# -*- coding: utf-8 -*-
"""Windows installer v4.13.3: the start script, health check and launcher
find this install's Python and conda wherever the installer put them.

2026-11-08, a tester: "Amadeus's Python (conda env "amadeus") could not be
found". The installer accepts a Miniconda in more places (an all-users
C:\\ProgramData\\miniconda3, whose environments may land in
%USERPROFILE%\\.conda\\envs) than start_windows.bat looked in. Covers:
  1. the installer's Save-RuntimePaths writes .runtime\\app_python.txt in
     the console code page (OEM) and conda_exe.txt in UTF-8;
  2. the REAL lookup lines of start_windows.bat and check_amadeus.bat (run
     in cmd with pretend user folders - nothing of this PC is used): the
     recorded path wins, even with an accented user name; without it the
     ProgramData, Continuum and .conda\\envs places are found; a recorded
     path that no longer exists is skipped;
  3. the launcher: the recorded conda comes first; her memory's environment
     is found beside the app's own environment and in ~/.conda/envs.
"""
import ctypes
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _ROOT / "scripts" / "install_windows.ps1"
_LAUNCHER = _ROOT / "scripts" / "launcher.py"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
_WINDOWS = os.name == "nt"

_EXTRACT = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('Save-RuntimePaths')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
Set-Content -Path $Out -Value ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n") -Encoding utf8
"""


def _oem() -> str:
    return f"cp{ctypes.windll.kernel32.GetOEMCP()}"


def _ps(args, timeout=120):
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    return subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
                          capture_output=True, text=True, timeout=timeout, env=env,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)


def _lookup_block(bat: Path, start: str, end: str) -> str:
    """The lines of a .bat that find the Python (start..end, inclusive)."""
    lines = bat.read_text(encoding="utf-8").splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith(start))
    j = next(n for n, l in enumerate(lines) if n > i and l.startswith(end))
    return "\r\n".join(lines[i:j + 1])


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class SaveRuntimePathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_paths_"))
        (self.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        out = _ps(["-File", str(self.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(self.tmp / "defs.ps1")])
        self.assertEqual(out.returncode, 0, out.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_paths_are_recorded_in_the_right_encodings(self):
        env = self.tmp / "Jos\u00e9" / "envs" / "amadeus"
        env.mkdir(parents=True)
        py = env / "python.exe"
        py.write_text("", encoding="ascii")
        conda = self.tmp / "Jos\u00e9" / "Scripts" / "conda.exe"
        conda.parent.mkdir(parents=True)
        conda.write_text("", encoding="ascii")
        proj = self.tmp / "proj"
        proj.mkdir()
        script = self.tmp / "run.ps1"
        script.write_text(
            "function Write-LogOnly($m){ }\n"
            f". '{self.tmp / 'defs.ps1'}'\n"
            f"Save-RuntimePaths '{proj}' '{py}' '{conda}'\n", encoding="utf-8-sig")
        out = _ps(["-File", str(script)])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((proj / ".runtime" / "app_python.txt").read_bytes(), str(py).encode(_oem()))
        self.assertEqual((proj / ".runtime" / "conda_exe.txt").read_bytes(), str(conda).encode("utf-8"))

    def test_missing_python_is_not_recorded(self):
        proj = self.tmp / "proj2"
        proj.mkdir()
        script = self.tmp / "run2.ps1"
        script.write_text(
            "function Write-LogOnly($m){ }\n"
            f". '{self.tmp / 'defs.ps1'}'\n"
            f"Save-RuntimePaths '{proj}' $null '{self.tmp / 'nope.exe'}'\n", encoding="utf-8-sig")
        out = _ps(["-File", str(script)])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse((proj / ".runtime" / "app_python.txt").exists())
        self.assertFalse((proj / ".runtime" / "conda_exe.txt").exists())

    def test_installer_calls_it_after_the_smoke_test(self):
        text = _INSTALLER.read_text(encoding="utf-8")
        self.assertIn("Save-RuntimePaths $proj $amPy $conda", text)
        self.assertRegex(text, r'\$InstallerVersion = "v4\.13\.3"')


@unittest.skipUnless(_WINDOWS, "Windows only")
class BatLookupTests(unittest.TestCase):
    """Runs the lookup lines of the real .bat files in cmd, with USERPROFILE,
    LOCALAPPDATA and ProgramData pointing at empty pretend folders."""

    CASES = [
        (_ROOT / "start_windows.bat", 'set "ENV_SUFFIX="',
         "if not defined ENV_SUFFIX if not defined AMADEUS_PY if defined CONDA_PREFIX", "AMADEUS_PY", ""),
        (_ROOT / "check_amadeus.bat", 'set "ENV_SUFFIX="',
         "if not defined ENV_SUFFIX if not defined PY if defined CONDA_PREFIX", "PY", "%~dp0"),
    ]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_bat_"))
        self.home = self.tmp / "users" / "Jos\u00e9"
        self.local = self.home / "AppData" / "Local"
        self.pdata = self.tmp / "ProgramData"
        for d in (self.home, self.local, self.pdata):
            d.mkdir(parents=True, exist_ok=True)
        self.proj = self.tmp / "proj"
        (self.proj / ".runtime").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_python(self, root: Path) -> Path:
        py = root / "amadeus" / "python.exe"
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("", encoding="ascii")
        return py

    def found(self, bat: Path, start: str, end: str, var: str) -> dict:
        """The Python each .bat would pick (one run per .bat)."""
        block = _lookup_block(bat, start, end)
        test_bat = self.proj / "lookup.bat"
        test_bat.write_bytes(("@echo off\r\nsetlocal EnableExtensions DisableDelayedExpansion\r\n"
                              'cd /d "%~dp0"\r\n' + block + f"\r\necho FOUND=[%{var}%]\r\n").encode(_oem()))
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("CONDA_PREFIX", "USERPROFILE", "LOCALAPPDATA", "PROGRAMDATA")}
        env.update(USERPROFILE=str(self.home), LOCALAPPDATA=str(self.local), ProgramData=str(self.pdata))
        out = subprocess.run(["cmd.exe", "/d", "/c", str(test_bat)], capture_output=True, env=env,
                             timeout=60, stdin=subprocess.DEVNULL)
        text = out.stdout.decode(_oem(), errors="replace")
        m = re.search(r"FOUND=\[(.*)\]", text)
        self.assertIsNotNone(m, text + out.stderr.decode(_oem(), errors="replace"))
        return m.group(1)

    def check_all(self, expected: Path):
        for bat, start, end, var, _ in self.CASES:
            with self.subTest(bat=bat.name):
                self.assertEqual(self.found(bat, start, end, var), str(expected))

    def test_recorded_path_wins_with_an_accented_user_name(self):
        recorded = self.fake_python(self.home / "somewhere" / "odd")
        self.fake_python(self.home / "miniconda3" / "envs")  # would be found otherwise
        (self.proj / ".runtime" / "app_python.txt").write_bytes(str(recorded).encode(_oem()))
        self.check_all(recorded)

    def test_all_users_programdata_miniconda(self):
        self.check_all(self.fake_python(self.pdata / "miniconda3" / "envs"))

    def test_user_conda_envs_folder(self):
        self.check_all(self.fake_python(self.home / ".conda" / "envs"))

    def test_old_continuum_folder(self):
        self.check_all(self.fake_python(self.local / "Continuum" / "miniconda3" / "envs"))

    def test_stale_recorded_path_is_skipped(self):
        (self.proj / ".runtime" / "app_python.txt").write_bytes(str(self.tmp / "gone" / "python.exe").encode(_oem()))
        self.check_all(self.fake_python(self.home / "miniconda3" / "envs"))

    def test_nothing_found_stays_empty(self):
        for bat, start, end, var, _ in self.CASES:
            with self.subTest(bat=bat.name):
                self.assertEqual(self.found(bat, start, end, var), "")

    def test_start_script_error_points_to_the_log(self):
        text = (_ROOT / "start_windows.bat").read_text(encoding="utf-8")
        self.assertIn("install_log.txt", text)
        self.assertIn("Install Amadeus.bat", text)


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_paths", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherCondaTests(unittest.TestCase):
    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_lconda_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recorded_conda_comes_first(self):
        conda = self.tmp / "conda.exe"
        conda.write_text("", encoding="ascii")
        rec = self.tmp / "conda_exe.txt"
        rec.write_text(str(conda), encoding="utf-8")
        self.launcher.RECORDED_CONDA_FILE = rec
        with mock.patch.object(self.launcher.shutil, "which", return_value=r"C:\other\conda.exe"):
            self.assertEqual(self.launcher.find_conda(), str(conda))

    def test_missing_record_changes_nothing(self):
        self.launcher.RECORDED_CONDA_FILE = self.tmp / "none.txt"
        self.assertIsNone(self.launcher._recorded_conda())

    def test_memory_env_found_beside_the_app_env(self):
        envs = self.tmp / ".conda" / "envs"
        exe_name = "python.exe" if _WINDOWS else "bin/python"
        app = envs / "amadeus" / exe_name
        cm = envs / "amadeus-cm" / exe_name
        for p in (app, cm):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("", encoding="ascii")
        with mock.patch.object(self.launcher, "find_conda", return_value=None), \
             mock.patch.object(self.launcher.sys, "executable", str(app)), \
             mock.patch.object(self.launcher, "env_name", side_effect=lambda b: b):
            self.assertEqual(self.launcher._cm_base_python(), cm)

    def test_memory_env_found_in_home_conda_envs(self):
        home = self.tmp / "home"
        exe_name = "python.exe" if _WINDOWS else "bin/python"
        cm = home / ".conda" / "envs" / "amadeus-cm" / exe_name
        cm.parent.mkdir(parents=True, exist_ok=True)
        cm.write_text("", encoding="ascii")
        with mock.patch.object(self.launcher, "find_conda", return_value=None), \
             mock.patch.object(self.launcher.sys, "executable", str(self.tmp / "py" / "python.exe")), \
             mock.patch.object(self.launcher.Path, "home", return_value=home), \
             mock.patch.object(self.launcher, "env_name", side_effect=lambda b: b):
            self.assertEqual(self.launcher._cm_base_python(), cm)


if __name__ == "__main__":
    unittest.main()

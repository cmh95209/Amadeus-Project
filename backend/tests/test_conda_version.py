# -*- coding: utf-8 -*-
"""Windows installer v4.13.4: a Miniconda that is too old is not used.

Same idea as the Node.js fix (v4.13.1): an existing conda older than 4.9
(no `conda run --no-capture-output`, which starts her voice engine) is never
used; one older than 23.10 only by an install that already has its Python
environments there. Otherwise Amadeus gets her own Miniconda in
%USERPROFILE%\\.amadeus-tools\\miniconda3. Windows only (skipped elsewhere):
the real installer functions run in Windows PowerShell with pretend conda
programs - nothing is installed or downloaded. Covers:
  1. Get-CondaVersion reads "conda X.Y.Z"; a conda that prints nothing -> $null;
  2. Get-CondaProblem: the version rules above;
  3. Test-CondaHasAmadeusEnvs: an amadeus* environment with a Python inside;
  4. the Miniconda silent-install arguments: /D= last, no quotes (spaces too);
  5. the wiring: own copy first, the check, Ensure-Env ignores another
     conda's environment, the .bat files and the launcher know the folder.
"""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _ROOT / "scripts" / "install_windows.ps1"
_LAUNCHER = _ROOT / "scripts" / "launcher.py"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None

_EXTRACT = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('Get-CondaVersion', 'Get-CondaProblem', 'Test-CondaHasAmadeusEnvs', 'Get-MinicondaArgs')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
$vars = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and @('$CondaMinWorking', '$CondaMinNew') -contains $n.Left.Extent.Text }, $true)
if (@($vars).Count -ne 2) { throw 'expected the two conda version limits' }
Set-Content -Path $Out -Value (((@($vars) | ForEach-Object { $_.Extent.Text }) -join "`n") + "`n`n" + ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n")) -Encoding utf8
"""


def _ps(args, timeout=120):
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    return subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
                          capture_output=True, text=True, timeout=timeout, env=env,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class CondaVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="amadeus_condaver_"))
        (cls.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        out = _ps(["-File", str(cls.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(cls.tmp / "defs.ps1")])
        if out.returncode != 0:
            raise RuntimeError(out.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_ps(self, body: str) -> str:
        script = self.tmp / "run.ps1"
        script.write_text(f". '{self.tmp / 'defs.ps1'}'\n" + body, encoding="utf-8-sig")
        out = _ps(["-File", str(script)])
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def fake_conda(self, name: str, prints: str) -> Path:
        path = self.tmp / f"{name}.cmd"
        path.write_text(f"@echo off\r\n{('echo ' + prints) if prints else 'exit /b 1'}\r\n", encoding="ascii")
        return path

    def test_version_is_read(self):
        for prints, want in (("conda 24.11.3", "24.11"), ("conda 4.8.3", "4.8"), ("conda 23.10.0", "23.10")):
            with self.subTest(prints=prints):
                exe = self.fake_conda("c" + want.replace(".", "_"), prints)
                self.assertEqual(self.run_ps(f"$v = Get-CondaVersion '{exe}'; [string]$v"), want)

    def test_silent_conda_has_no_version(self):
        exe = self.fake_conda("dead", "")
        self.assertEqual(self.run_ps(f"if ($null -eq (Get-CondaVersion '{exe}')) {{ 'NULL' }}"), "NULL")

    def problem(self, ver: str | None, has_envs: bool) -> str:
        v = f"([version]'{ver}')" if ver else "$null"
        flag = "$true" if has_envs else "$false"
        return self.run_ps(f"$r = Get-CondaProblem {v} {flag}; if ($null -eq $r) {{ 'NULL' }} else {{ $r }}")

    def test_version_rules(self):
        cases = [
            ("25.1", False, None), ("23.10", False, None), ("24.1", True, None),
            ("23.7", True, None),             # an earlier install that works: kept
            ("23.7", False, "too old for a new install"),
            ("4.8", True, "too old"),          # never works (no --no-capture-output)
            ("4.8", False, "too old"),
            (None, False, "does not run"),
        ]
        for ver, has, want in cases:
            with self.subTest(ver=ver, has_envs=has):
                got = self.problem(ver, has)
                if want is None:
                    self.assertEqual(got, "NULL")
                else:
                    self.assertIn(want, got)

    def test_amadeus_envs_are_recognised(self):
        root = self.tmp / "mc"
        (root / "envs" / "other").mkdir(parents=True)
        (root / "envs" / "other" / "python.exe").write_text("", encoding="ascii")
        (root / "envs" / "amadeus-cm").mkdir(parents=True)  # empty: half-made
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        body = (f"$env:USERPROFILE = '{home}'\n"
                f"'A=' + (Test-CondaHasAmadeusEnvs '{root}')\n")
        self.assertEqual(self.run_ps(body), "A=False")
        (root / "envs" / "amadeus" ).mkdir()
        (root / "envs" / "amadeus" / "python.exe").write_text("", encoding="ascii")
        self.assertEqual(self.run_ps(body), "A=True")

    def test_amadeus_envs_in_the_user_conda_folder(self):
        root = self.tmp / "mc2"
        root.mkdir()
        home = self.tmp / "home2"
        env = home / ".conda" / "envs" / "amadeus-test"
        env.mkdir(parents=True)
        (env / "python.exe").write_text("", encoding="ascii")
        self.assertEqual(self.run_ps(f"$env:USERPROFILE = '{home}'\n'A=' + (Test-CondaHasAmadeusEnvs '{root}')"),
                         "A=True")

    def test_silent_install_arguments(self):
        out = self.run_ps("Get-MinicondaArgs 'C:\\Users\\Jo Smith\\.amadeus-tools\\miniconda3'")
        self.assertTrue(out.endswith("/D=C:\\Users\\Jo Smith\\.amadeus-tools\\miniconda3"), out)
        self.assertNotIn('"', out)
        for flag in ("/InstallationType=JustMe", "/AddToPath=0", "/RegisterPython=0", "/S "):
            self.assertIn(flag, out)


class CondaWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _INSTALLER.read_text(encoding="utf-8")

    def test_version(self):
        self.assertRegex(self.text, r'\$InstallerVersion = "v4\.13\.\d+"')

    def test_no_control_characters(self):
        # 2026-11-08: a header note once held a BEL character where "\a" was
        # meant (".runtime\app_python.txt"). Tabs and line ends only.
        for path in (_INSTALLER, _ROOT / "docs" / "changelog.md", _ROOT / "start_windows.bat",
                     _ROOT / "check_amadeus.bat", _LAUNCHER):
            with self.subTest(file=path.name):
                bad = [b for b in path.read_bytes() if b < 32 and b not in (9, 10, 13)]
                self.assertEqual(bad, [])

    def test_own_copy_is_looked_for_first(self):
        self.assertIn('$spots = @($OwnCondaDir,', self.text)
        self.assertIn('$OwnCondaDir = Join-Path $ToolsDir "miniconda3"', self.text)

    def test_existing_conda_is_checked(self):
        self.assertIn("$condaProblem = Get-CondaProblem $foundVer (Test-CondaHasAmadeusEnvs $found)", self.text)
        self.assertIn("Install-MinicondaDirect $OwnCondaDir", self.text)
        self.assertIn("$script:OwnConda = ($found -eq $OwnCondaDir)", self.text)

    def test_quoted_target_folder_is_gone(self):
        self.assertNotIn('/D=`"', self.text)

    def test_own_conda_does_not_reuse_another_condas_env(self):
        self.assertIn("if ($script:OwnConda -and $path -and -not ([string]$path).StartsWith(", self.text)

    def test_bat_files_know_the_own_copy(self):
        for name in ("start_windows.bat", "check_amadeus.bat"):
            with self.subTest(bat=name):
                self.assertIn(r"%USERPROFILE%\.amadeus-tools\miniconda3\envs\%AMADEUS_ENV%\python.exe",
                              (_ROOT / name).read_text(encoding="utf-8"))


@unittest.skipUnless(os.name == "nt", "Windows only")
class LauncherOwnCondaTests(unittest.TestCase):
    def test_own_copy_before_path(self):
        spec = importlib.util.spec_from_file_location("amadeus_launcher_owncond", _LAUNCHER)
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        tmp = Path(tempfile.mkdtemp(prefix="amadeus_owncond_"))
        try:
            exe = tmp / ".amadeus-tools" / "miniconda3" / "Scripts" / "conda.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="ascii")
            launcher.RECORDED_CONDA_FILE = tmp / "none.txt"
            with mock.patch.object(launcher.Path, "home", return_value=tmp), \
                 mock.patch.object(launcher.shutil, "which", return_value=r"C:\old\conda.exe"):
                self.assertEqual(launcher.find_conda(), str(exe))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Windows installer v4.12.2 hotfixes.

Windows only (skipped elsewhere). The functions are taken out of the real
installer and run in Windows PowerShell on pretend inputs - nothing is
installed. Covers:
  1. the install folder must be a full path ("Yes" typed after the command
     is refused - the REAL installer is run and must stop before doing
     anything);
  2. `conda env list` lines are read into an environment's folder (paths
     with spaces, the active-environment star), and an environment without
     a working Python counts as broken;
  3. nvidia-smi's card line is read, and the voice PyTorch choice: no card or
     a card older than generation 7.5 (GTX 10 series and before) -> processor
     version; 7.5 and newer or unknown -> graphics-card version;
  4. (v4.12.3) conda's "package ... appears to be corrupted" errors are read
     into the damaged cache folders, and only those (inside "pkgs") are
     deleted before one retry.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_INSTALLER = Path(__file__).resolve().parents[2] / "scripts" / "install_windows.ps1"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None

_EXTRACT = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('Test-InstallDirOk', 'Find-EnvPathIn', 'Test-EnvWorks', 'ConvertFrom-NvidiaQuery', 'Select-VoiceTorchBuild', 'Find-CorruptPackageDirs', 'Repair-CondaCache')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
$cap = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$VoiceMinComputeCap' }, $true)
if (@($cap).Count -ne 1) { throw 'expected one $VoiceMinComputeCap' }
Set-Content -Path $Out -Value ((@($cap)[0].Extent.Text) + "`n`n" + ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n")) -Encoding utf8
"""


def _ps(args, timeout=120):
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    return subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
                          capture_output=True, text=True, timeout=timeout, env=env,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)


def _ps_str(s):
    return "'" + s.replace("'", "''") + "'"


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class InstallerHotfixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="amadeus_hotfix_"))
        (cls.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        out = _ps(["-File", str(cls.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(cls.tmp / "defs.ps1")])
        assert out.returncode == 0, out.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_json(self, body: str):
        script = self.tmp / "run.ps1"
        script.write_text(f". '{self.tmp / 'defs.ps1'}'\n" + body, encoding="utf-8-sig")
        out = _ps(["-File", str(script)])
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    # 1. install folder -------------------------------------------------
    def test_install_folder_must_be_full_path(self):
        cases = {"D:\\Amadeus": True, "C:\\Users\\Jo Doe\\Amadeus": True, "D:\\": True,
                 "\\\\server\\share\\Amadeus": True, "Yes": False, "yes": False,
                 "Amadeus": False, ".\\Amadeus": False, "D:Amadeus": False, "\\Amadeus": False}
        body = "@(" + ", ".join(f"(Test-InstallDirOk {_ps_str(k)})" for k in cases) + ") | ConvertTo-Json -Compress"
        self.assertEqual(self.run_json(body), list(cases.values()))

    def test_real_installer_stops_on_yes_before_doing_anything(self):
        work = self.tmp / "cwd"
        work.mkdir(exist_ok=True)
        out = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(_INSTALLER), "Yes"], capture_output=True, text=True, timeout=60,
                             cwd=work, stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("'Yes' is not a full folder path", out.stdout)
        self.assertEqual(list(work.iterdir()), [])          # no "Yes" folder, no log

    # 2. conda environments ---------------------------------------------
    def test_env_folder_from_conda_list(self):
        lines = ["# conda environments:", "#",
                 "base                  *  C:\\Users\\Jo Doe\\miniconda3",
                 "amadeus                  C:\\Users\\Jo Doe\\miniconda3\\envs\\amadeus",
                 "amadeus-cm               C:\\Users\\Jo Doe\\.conda\\envs\\amadeus-cm",
                 "                         D:\\other\\nameless-env", ""]
        arr = "@(" + ", ".join(_ps_str(l) for l in lines) + ")"
        # "" + $null = "" (a bare $null would drop out of the list)
        names = ["amadeus", "amadeus-cm", "base", "GPTSoVits", "amadeus-c"]
        body = "@(" + ", ".join(f"(\"\" + (Find-EnvPathIn {arr} '{n}'))" for n in names) + \
               ") | ConvertTo-Json -Compress"
        self.assertEqual(self.run_json(body),
                         ["C:\\Users\\Jo Doe\\miniconda3\\envs\\amadeus",
                          "C:\\Users\\Jo Doe\\.conda\\envs\\amadeus-cm",
                          "C:\\Users\\Jo Doe\\miniconda3", "", ""])

    def test_broken_env_is_not_working(self):
        empty = self.tmp / "envs" / "amadeus"
        empty.mkdir(parents=True, exist_ok=True)
        real = Path(sys.executable).parent              # this test's own working environment
        body = (f"@((Test-EnvWorks {_ps_str(str(empty))}), (Test-EnvWorks {_ps_str(str(self.tmp / 'missing'))}), "
                f"(Test-EnvWorks {_ps_str(str(real))})) | ConvertTo-Json -Compress")
        self.assertEqual(self.run_json(body), [False, False, True])

    # 3. graphics card --------------------------------------------------
    def parse(self, lines):
        arr = "@(" + ", ".join(_ps_str(l) for l in lines) + ")"
        return self.run_json(f"$c = ConvertFrom-NvidiaQuery {arr}\n"
                             "if ($c) { $c | ConvertTo-Json -Compress } else { 'null' }")

    def test_reads_the_card_line(self):
        self.assertEqual(self.parse(["NVIDIA GeForce RTX 3050 Laptop GPU, 6144, 581.57, 8.6"]),
                         {"name": "NVIDIA GeForce RTX 3050 Laptop GPU", "vram_mb": 6144,
                          "driver": "581.57", "compute_cap": 8.6})
        self.assertIsNone(self.parse(["NVIDIA GeForce GTX 970, 4096, 456.71"])["compute_cap"])
        self.assertIsNone(self.parse(['Field "compute_cap" is not a valid field to query.']))

    def test_voice_build_choice(self):
        cases = [("$false", "$null", "cpu"), ("$true", "6.1", "cpu"), ("$true", "7.0", "cpu"),
                 ("$true", "7.5", "cuda"), ("$true", "8.6", "cuda"), ("$true", "$null", "cuda")]
        body = "@(" + ", ".join(f"(Select-VoiceTorchBuild {g} {c})" for g, c, _ in cases) + \
               ") | ConvertTo-Json -Compress"
        self.assertEqual(self.run_json(body), [want for _, _, want in cases])

    # 4. damaged conda download cache -----------------------------------
    @staticmethod
    def corrupt_lines(pkgs):
        # conda's real wording (seen on a tester's PC, 2026-11-02)
        return ["Preparing transaction: done", "Verifying transaction: failed", "",
                f"CondaVerificationError: The package for tzdata located at {pkgs}\\tzdata-2026c-he532380_0",
                "appears to be corrupted. The path 'share/zoneinfo/zone.tab'",
                "specified in the package manifest cannot be found.", "",
                f"CondaVerificationError: The package for tzdata located at {pkgs}\\tzdata-2026c-he532380_0",
                "appears to be corrupted. The path 'share/zoneinfo/zone1970.tab'",
                "specified in the package manifest cannot be found.", "",
                f"CondaVerificationError: The package for pip located at {pkgs}\\pip-26.2.2-pyhc872135_0",
                "appears to be corrupted. The path 'site-packages/pip-26.2.2.dist-info/licenses/AUTHORS.txt'",
                "specified in the package manifest cannot be found.",
                "CondaVerificationError: The package for evil located at C:\\Users\\Jo\\Documents",
                "appears to be corrupted."]

    def test_finds_damaged_package_folders(self):
        pkgs = "C:\\Users\\Jo Doe\\miniconda3\\pkgs"
        arr = "@(" + ", ".join(_ps_str(l) for l in self.corrupt_lines(pkgs)) + ")"
        body = (f"@{{ hit = @(Find-CorruptPackageDirs {arr}); "
                "none = @(Find-CorruptPackageDirs @('PackagesNotFoundError: x', '')) } | ConvertTo-Json -Compress")
        self.assertEqual(self.run_json(body),
                         {"hit": [pkgs + "\\tzdata-2026c-he532380_0", pkgs + "\\pip-26.2.2-pyhc872135_0"],
                          "none": []})

    def test_repair_deletes_only_named_packages_then_cleans(self):
        pkgs = self.tmp / "mc3" / "pkgs"
        for d in ("tzdata-2026c-he532380_0", "pip-26.2.2-pyhc872135_0", "python-3.10.16-h_0"):
            (pkgs / d).mkdir(parents=True, exist_ok=True)
            (pkgs / d / "info.json").write_text("{}")
        (pkgs / "tzdata-2026c-he532380_0.conda").write_text("x")
        outside = self.tmp / "Documents"
        outside.mkdir(exist_ok=True)
        lines = self.corrupt_lines(str(pkgs))
        lines[-2] = f"CondaVerificationError: The package for evil located at {outside}"
        arr = "@(" + ", ".join(_ps_str(l) for l in lines) + ")"
        body = ("function Log($m, $c) {}\n$script:calls = @()\n"
                "$conda = { $script:calls += ($args -join ' ') }\n"
                f"$fixed = Repair-CondaCache {arr}\n"
                "$none = Repair-CondaCache @('Solving environment: failed')\n"
                "@{ fixed = $fixed; none = $none; calls = @($script:calls) } | ConvertTo-Json -Compress")
        got = self.run_json(body)
        self.assertEqual(got, {"fixed": True, "none": False, "calls": ["clean --all -y"]})
        self.assertEqual(sorted(p.name for p in pkgs.iterdir()), ["python-3.10.16-h_0"])
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()

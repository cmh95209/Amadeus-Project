# -*- coding: utf-8 -*-
"""The installer's voice-engine graphics-card decision.

Windows only (skipped elsewhere). The functions are taken out of the real
installer and run in Windows PowerShell on pretend nvidia-smi answers -
nothing is installed. Covers:
  1. nvidia-smi's card line is read (name, memory, driver, generation); a
     driver too old for the generation field, "[N/A]" or an error line give
     no generation / no card instead of a crash;
  2. the voice PyTorch choice: no card or a card older than generation 7.5
     (GTX 10 series and before) -> processor version; 7.5 and newer, or an
     unknown generation (step 7's real test decides) -> graphics-card version.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_INSTALLER = Path(__file__).resolve().parents[2] / "scripts" / "install_windows.ps1"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None

_EXTRACT = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('ConvertFrom-NvidiaQuery', 'Select-VoiceTorchBuild')
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
                          encoding="utf-8", errors="replace")


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class VoiceGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="amadeus_voicegpu_"))
        (cls.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        out = _ps(["-File", str(cls.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(cls.tmp / "defs.ps1")])
        assert out.returncode == 0, out.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_json(self, body: str):
        """Run `body` with the installer's functions loaded; it prints JSON."""
        script = self.tmp / "run.ps1"
        script.write_text(f". '{self.tmp / 'defs.ps1'}'\n" + body, encoding="utf-8-sig")
        out = _ps(["-File", str(script)])
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def parse(self, lines):
        arr = "@(" + ", ".join("'" + l.replace("'", "''") + "'" for l in lines) + ")"
        return self.run_json(f"$c = ConvertFrom-NvidiaQuery {arr}\n"
                             "if ($c) { $c | ConvertTo-Json -Compress } else { 'null' }")

    def test_reads_the_card_line(self):
        self.assertEqual(self.parse(["NVIDIA GeForce RTX 5090, 32607, 581.57, 12.0"]),
                         {"name": "NVIDIA GeForce RTX 5090", "vram_mb": 32607,
                          "driver": "581.57", "compute_cap": 12.0})
        self.assertEqual(self.parse(["NVIDIA GeForce GTX 1060 6GB, 6144, 472.12, 6.1"])["compute_cap"], 6.1)

    def test_first_card_counts(self):
        card = self.parse(["NVIDIA GeForce GTX 1080, 8192, 560.94, 6.1",
                           "NVIDIA GeForce RTX 3060, 12288, 560.94, 8.6"])
        self.assertEqual(card["name"], "NVIDIA GeForce GTX 1080")

    def test_no_generation_when_driver_cannot_say(self):
        self.assertIsNone(self.parse(["NVIDIA GeForce GTX 970, 4096, 456.71"])["compute_cap"])
        self.assertIsNone(self.parse(["NVIDIA GeForce GTX 970, 4096, 456.71, [N/A]"])["compute_cap"])

    def test_error_answer_is_no_card(self):
        self.assertIsNone(self.parse(['Field "compute_cap" is not a valid field to query.']))
        self.assertIsNone(self.parse([]))

    def test_voice_build_choice(self):
        cases = [("$false", "$null", "cpu"), ("$false", "8.6", "cpu"),
                 ("$true", "5.2", "cpu"), ("$true", "6.1", "cpu"), ("$true", "7.0", "cpu"),
                 ("$true", "7.5", "cuda"), ("$true", "8.6", "cuda"), ("$true", "12.0", "cuda"),
                 ("$true", "$null", "cuda")]
        body = "@(" + ", ".join(f"(Select-VoiceTorchBuild {g} {c})" for g, c, _ in cases) + \
               ") | ConvertTo-Json -Compress"
        self.assertEqual(self.run_json(body), [want for _, _, want in cases])


if __name__ == "__main__":
    unittest.main()

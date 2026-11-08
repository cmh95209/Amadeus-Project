# -*- coding: utf-8 -*-
"""Windows installer v4.13.2: an NVIDIA card keeps the CUDA llama.cpp build
when it only failed to DOWNLOAD.

2026-11-08, a tester with an RTX 3080 Ti during a GitHub outage: every
llama.cpp download returned 504 and the installer went on to the Vulkan
build. Windows only (skipped elsewhere): the real installer functions run in
Windows PowerShell with pretend downloads - nothing is downloaded. Covers:
  1. a failed CUDA download -> no Vulkan attempt, the choice stays CUDA,
     no helper program ("" - the summary says to run the installer again);
  2. a CUDA build that downloaded but cannot start -> Vulkan as before;
  3. the right build installed fine -> no fallback;
  4. a re-run takes the build from the graphics card again (a kept choice
     whose saved build is Vulkan gets this PC's CUDA build), and the saved
     choice is only written at step 0, never after a fallback.
"""
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
$names = @('Install-LlamaBuild', 'Install-LlamaWithFallback', 'Update-KeptChoice')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
Set-Content -Path $Out -Value ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n") -Encoding utf8
"""

# Pretend surroundings: a tier table with two builds, a log that collects
# lines, and downloads that fail (504) or succeed per build.
_STUBS = r"""
$ErrorActionPreference = 'Continue'
$script:Logged = @()
function Log($msg, $color){ $script:Logged += [string]$msg }
function Write-LogOnly($msg){ }
$script:Fetched = @()
function Get-VerifiedFile($url, $dest, $bytes, $sha, $label){
    $script:Fetched += $url
    if ($script:FailDownload) { throw ("Could not download " + $label + " right now. Check your internet connection, then run the installer again - it continues where it stopped.") }
}
function Expand-ZipFile($zip, $dir){ }
$TierTable = [pscustomobject]@{ llama_cpp = [pscustomobject]@{ release = "b1"; builds = [pscustomobject]@{
    "cuda-13.4" = [pscustomobject]@{ files = @([pscustomobject]@{ name = "cuda.zip"; bytes = 1; sha256 = "x" }) }
    "vulkan"    = [pscustomobject]@{ files = @([pscustomobject]@{ name = "vk.zip";   bytes = 1; sha256 = "y" }) } } } }
"""


def _ps(args, timeout=120):
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    return subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
                          capture_output=True, text=True, timeout=timeout, env=env,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class LlamaFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="amadeus_llama_"))
        (cls.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        out = _ps(["-File", str(cls.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(cls.tmp / "defs.ps1")])
        if out.returncode != 0:
            raise RuntimeError(out.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_ps(self, body: str) -> str:
        models = self.tmp / "models"
        shutil.rmtree(models, ignore_errors=True)
        script = self.tmp / "run.ps1"
        script.write_text(_STUBS + f"\n$ModelsDir = '{models}'\n. '{self.tmp / 'defs.ps1'}'\n" + body,
                          encoding="utf-8-sig")
        out = _ps(["-File", str(script)])
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        return out.stdout

    def test_failed_download_keeps_cuda_and_tries_no_vulkan(self):
        out = self.run_ps(
            "$script:FailDownload = $true\n"
            "$c = [pscustomobject]@{ build = 'cuda-13.4' }\n"
            "$exe = Install-LlamaWithFallback $c\n"
            "'EXE=[' + $exe + ']'\n"
            "'BUILD=' + $c.build\n"
            "'FETCHED=' + ($script:Fetched -join ',')\n"
            "'LOG=' + ($script:Logged -join ' | ')\n")
        self.assertIn("EXE=[]", out)
        self.assertIn("BUILD=cuda-13.4", out)
        self.assertNotIn("vk.zip", out.split("FETCHED=")[1].splitlines()[0])
        self.assertNotIn("Vulkan) version of llama.cpp instead", out)
        self.assertIn("could not be downloaded right now", out)
        self.assertIn("Run the installer again later", out)

    def test_cuda_that_cannot_start_falls_back_to_vulkan(self):
        # The real download part is not needed here: a pretend build step
        # where CUDA "would not start" (an ordinary error, no download mark).
        out = self.run_ps(
            "function Install-LlamaBuild($build){\n"
            "    if ($build -ne 'vulkan') { throw ('llama.cpp (' + $build + ') would not start on this PC: driver too old') }\n"
            "    return 'C:\\vk\\llama-server.exe'\n"
            "}\n"
            "$c = [pscustomobject]@{ build = 'cuda-13.4' }\n"
            "$exe = Install-LlamaWithFallback $c\n"
            "'EXE=' + $exe\n"
            "'BUILD=' + $c.build\n")
        self.assertIn("EXE=C:\\vk\\llama-server.exe", out)
        self.assertIn("BUILD=vulkan", out)

    def test_working_build_has_no_fallback(self):
        out = self.run_ps(
            "function Install-LlamaBuild($build){ return ('C:\\' + $build + '\\llama-server.exe') }\n"
            "$c = [pscustomobject]@{ build = 'cuda-13.4' }\n"
            "'EXE=' + (Install-LlamaWithFallback $c)\n"
            "'BUILD=' + $c.build\n")
        self.assertIn("EXE=C:\\cuda-13.4\\llama-server.exe", out)
        self.assertIn("BUILD=cuda-13.4", out)

    def test_vulkan_download_failure_is_reported_too(self):
        # An AMD/Intel PC (build already Vulkan) with a failed download:
        # nothing installed, a clear note, no crash.
        out = self.run_ps(
            "$script:FailDownload = $true\n"
            "$c = [pscustomobject]@{ build = 'vulkan' }\n"
            "'EXE=[' + (Install-LlamaWithFallback $c) + ']'\n"
            "'BUILD=' + $c.build\n"
            "'LOG=' + ($script:Logged -join ' | ')\n")
        self.assertIn("EXE=[]", out)
        self.assertIn("BUILD=vulkan", out)
        self.assertIn("could not be downloaded right now", out)

    def test_rerun_takes_the_build_from_the_card_again(self):
        out = self.run_ps(
            "$prev = [pscustomobject]@{ mode = 'builtin'; model = '12B'; build = 'vulkan'; chosen = 'then' }\n"
            "$hw = [pscustomobject]@{ simulated = $false; choice = [pscustomobject]@{ offered = $true; model = '12B'; tier = '12-15'; build = 'cuda-13.4';\n"
            "    context = 24576; kv_cache = 'q8_0'; voice_device = 'cuda'; history_default = 14000; vram_need_gb = 9.6 } }\n"
            "'BUILD=' + (Update-KeptChoice $prev $hw).build\n")
        self.assertIn("BUILD=cuda-13.4", out)


class SavedChoiceTests(unittest.TestCase):
    def test_choice_file_is_written_only_at_step_0(self):
        # A fallback changes the build only in memory for this run: the saved
        # answer (local_llm_choice.json) is written before any download.
        text = _INSTALLER.read_text(encoding="utf-8")
        writes = [l for l in text.splitlines() if "Write-Utf8File $LlmChoiceFile" in l]
        self.assertEqual(len(writes), 2, writes)
        start = text.index("function Invoke-LlmChoice")
        end = text.index("\nfunction ", start + 10)
        for w in writes:
            self.assertIn(w.strip(), text[start:end])


if __name__ == "__main__":
    unittest.main()

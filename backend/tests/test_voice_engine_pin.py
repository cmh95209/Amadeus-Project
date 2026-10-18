# -*- coding: utf-8 -*-
"""The installers pin the voice engine (GPT-SoVITS) to its tested version.

GPT-SoVITS's October 2026 update made her voice take minutes per line, and
the installers used to take whatever upstream had published that day.
Offline: a throwaway git repo stands in for GitHub, with a "tested" and a
"newer" commit. The real pin code is taken out of each installer and run
(Windows: PowerShell, Mac: bash from Git for Windows). Covers:
  1. both installers carry the same pinned commit;
  2. an install on the newer version is switched to the tested one; the
     voice-patched file and the engine's own settings file go back to the
     tested originals; untracked model files stay;
  3. a second run is a no-op; a copy that lacks the commit (shallow clone)
     fetches it first.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_WIN = _SCRIPTS / "install_windows.ps1"
_MAC = _SCRIPTS / "install_macos.sh"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
_GIT = shutil.which("git")
_BASH = None
if _GIT and os.name == "nt":
    candidate = Path(_GIT).resolve().parents[1] / "bin" / "bash.exe"
    _BASH = str(candidate) if candidate.exists() else None
elif _GIT:
    _BASH = shutil.which("bash")


def _git(*args, cwd=None):
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {out.stderr}")
    return out.stdout.strip()


def _win_commit():
    m = re.search(r'^\$GptSovitsCommit\s*=\s*"([0-9a-f]{40})"', _WIN.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def _mac_commit():
    m = re.search(r'^GPT_SOVITS_COMMIT="([0-9a-f]{40})"', _MAC.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


class PinnedCommitTests(unittest.TestCase):
    def test_both_installers_pin_the_same_tested_commit(self):
        self.assertEqual(_win_commit(), "48b1a0169a28582a8984402f82cf438d3bfa6aca")
        self.assertEqual(_mac_commit(), _win_commit())


@unittest.skipUnless(_GIT, "git not available")
class _FakeUpstream(unittest.TestCase):
    """A local 'GPT-SoVITS' with a tested commit and a newer one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_pin_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        up = self.tmp / "upstream"
        (up / "GPT_SoVITS" / "configs").mkdir(parents=True)
        (up / "GPT_SoVITS" / "text").mkdir(parents=True)
        _git("init", "-q", "-b", "main", str(up))
        _git("config", "user.email", "t@example.invalid", cwd=up)
        _git("config", "user.name", "test", cwd=up)
        _git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=up)
        self.yaml_tested = "custom:\n  device: cuda\n  version: v2\n"
        (up / "GPT_SoVITS" / "configs" / "tts_infer.yaml").write_text(self.yaml_tested, encoding="utf-8")
        (up / "GPT_SoVITS" / "text" / "japanese.py").write_text("# tested\n", encoding="utf-8")
        (up / "TTS.py").write_text("# august\n", encoding="utf-8")
        _git("add", "-A", cwd=up)
        _git("commit", "-q", "-m", "tested", cwd=up)
        self.tested = _git("rev-parse", "HEAD", cwd=up)
        (up / "TTS.py").write_text("# october rework\n", encoding="utf-8")
        _git("commit", "-q", "-am", "newer", cwd=up)
        self.newer = _git("rev-parse", "HEAD", cwd=up)
        self.upstream = up

    def make_install(self, shallow=False):
        dest = self.tmp / "install" / "GPT-SoVITS"
        args = ["clone", "-q"] + (["--depth", "1", "--no-local"] if shallow else [])
        src = self.upstream.as_uri() if shallow else str(self.upstream)
        _git(*args, src, str(dest))
        # What an older install looks like: voice patch applied, the newer
        # engine's extra settings written, model files downloaded.
        (dest / "GPT_SoVITS" / "text" / "japanese.py").write_text("# our voice patch\n", encoding="utf-8")
        with (dest / "GPT_SoVITS" / "configs" / "tts_infer.yaml").open("a", encoding="utf-8") as f:
            f.write("v5dev:\n  version: v5dev\n")
        models = dest / "GPT_SoVITS" / "pretrained_models"
        models.mkdir()
        (models / "s2G2333k.pth").write_bytes(b"model")
        return dest

    def check_switched(self, dest):
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), self.tested)
        self.assertEqual((dest / "TTS.py").read_text(encoding="utf-8").replace("\r\n", "\n"), "# august\n")
        self.assertEqual((dest / "GPT_SoVITS" / "configs" / "tts_infer.yaml").read_text(encoding="utf-8").replace("\r\n", "\n"),
                         self.yaml_tested)
        self.assertEqual((dest / "GPT_SoVITS" / "text" / "japanese.py").read_text(encoding="utf-8").replace("\r\n", "\n"),
                         "# tested\n")   # the installer re-applies the patch right after
        self.assertEqual((dest / "GPT_SoVITS" / "pretrained_models" / "s2G2333k.pth").read_bytes(), b"model")


_EXTRACT = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('Log', 'Set-GptSovitsVersion')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
Set-Content -Path $Out -Value ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n") -Encoding utf8
"""


@unittest.skipUnless(_POWERSHELL and _GIT, "Windows PowerShell + git needed")
class WindowsPinTests(_FakeUpstream):
    def run_pin(self, dest):
        (self.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        ps = [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        out = subprocess.run(ps + [str(self.tmp / "extract.ps1"), "-Installer", str(_WIN),
                                   "-Out", str(self.tmp / "defs.ps1")], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        run = self.tmp / "run.ps1"
        run.write_text(
            f"$LogPath = '{self.tmp / 'log.txt'}'\n$Utf8NoBom = New-Object Text.UTF8Encoding $false\n"
            f". '{self.tmp / 'defs.ps1'}'\n"
            f"try {{ Set-GptSovitsVersion '{dest}' '{self.tested}'; 'RESULT=OK' }} catch {{ 'RESULT=ERR ' + $_.Exception.Message }}\n",
            encoding="utf-8-sig")
        out = subprocess.run(ps + [str(run)], capture_output=True, text=True, env=env)
        return out.stdout

    def test_newer_install_is_switched_and_rerun_is_a_noop(self):
        dest = self.make_install()
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), self.newer)
        stdout = self.run_pin(dest)
        self.assertIn("RESULT=OK", stdout)
        self.assertIn("Switching the voice engine", stdout)
        self.check_switched(dest)
        stdout = self.run_pin(dest)
        self.assertIn("RESULT=OK", stdout)
        self.assertIn("is on the tested version", stdout)

    def test_shallow_copy_fetches_the_tested_commit(self):
        dest = self.make_install(shallow=True)
        stdout = self.run_pin(dest)
        self.assertIn("RESULT=OK", stdout)
        self.check_switched(dest)


@unittest.skipUnless(_BASH and _GIT, "bash + git needed")
class MacPinTests(_FakeUpstream):
    def run_pin(self, dest):
        text = _MAC.read_text(encoding="utf-8").replace("\r\n", "\n")
        start = text.index('GPT_SOVITS_COMMIT="')
        end = text.index('log "  Voice engine is on the tested version."\n') + len('log "  Voice engine is on the tested version."\n')
        block = text[start:end].replace('GPT_SOVITS_COMMIT="48b1a0169a28582a8984402f82cf438d3bfa6aca"',
                                        f'GPT_SOVITS_COMMIT="{self.tested}"')
        script = self.tmp / "pin.sh"
        script.write_bytes((
            "log() { echo \"$*\"; }\ndie() { echo \"DIE: $*\"; exit 1; }\n"
            f"GPT='{dest.as_posix()}'\n" + block + "echo RESULT=OK\n").encode("utf-8"))
        out = subprocess.run([_BASH, str(script)], capture_output=True, text=True)
        return out.stdout + out.stderr

    def test_newer_install_is_switched(self):
        dest = self.make_install()
        output = self.run_pin(dest)
        self.assertIn("RESULT=OK", output)
        self.check_switched(dest)

    def test_shallow_copy_fetches_the_tested_commit(self):
        dest = self.make_install(shallow=True)
        output = self.run_pin(dest)
        self.assertIn("RESULT=OK", output, output)
        self.check_switched(dest)


if __name__ == "__main__":
    unittest.main()

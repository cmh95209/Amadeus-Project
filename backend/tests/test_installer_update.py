# -*- coding: utf-8 -*-
"""Re-running an installer updates an existing install (both installers).

The installers update an existing Amadeus folder to the newest release. The
user's own files must survive: her personality (backend/data/personality.txt,
a tracked file the app writes when the user edits it) and the saved
connection (llm_server.txt, llm_model.txt). A release can change the
shipped personality.txt too (v3.0 did): then the user's version stays as it
is - no conflict markers, nothing left shelved in git.

Offline: a throwaway git repo stands in for GitHub with an "old" and a "new"
release. The real Step 3 update code is taken out of each installer and run
(Windows: PowerShell, Mac: bash from Git for Windows).
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

PERSONALITY = "backend/data/personality.txt"
SERVER = "backend/llm_server.txt"
MODEL = "backend/data/llm_model.txt"
_MARKERS = ("<<<<<<<", ">>>>>>>", "=======")


def _git(*args, cwd=None):
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {out.stderr}")
    return out.stdout.strip()


def _write(root: Path, rel: str, text: str):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(_GIT, "git not available")
class _FakeRelease(unittest.TestCase):
    """A local 'fork' with an old release and a new one that changes the
    shipped personality.txt; an install of the old one that the user used."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_update_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        fork = self.tmp / "fork"
        _git("init", "-q", "-b", "main", str(fork))
        _git("config", "user.email", "t@example.invalid", cwd=fork)
        _git("config", "user.name", "cmh95209", cwd=fork)
        _git("config", "core.autocrlf", "false", cwd=fork)
        _write(fork, "backend/main.py", "# app\n")
        _write(fork, "start_windows.bat", "@echo off\n")
        _write(fork, PERSONALITY, "old default personality\n")
        _write(fork, SERVER, "")
        _write(fork, MODEL, "")
        _git("add", "-A", cwd=fork)
        _git("commit", "-q", "-m", "old release", cwd=fork)
        self.old = _git("rev-parse", "HEAD", cwd=fork)
        _write(fork, PERSONALITY, "new default personality\n")
        _write(fork, "backend/new_feature.py", "# v3\n")
        _git("add", "-A", cwd=fork)
        _git("commit", "-q", "-m", "new release", cwd=fork)
        self.new = _git("rev-parse", "HEAD", cwd=fork)
        _git("branch", "-q", "old", self.old, cwd=fork)
        _git("checkout", "-q", "--detach", cwd=fork)   # so 'main' can be moved
        self.fork = fork

    def make_install(self, shallow: bool, edited: bool = True) -> Path:
        dest = self.tmp / "install" / "Amadeus-Project"
        args = ["clone", "-q", "-b", "old"]
        args += ["--depth", "1", "--no-local"] if shallow else []
        src = self.fork.as_uri() if shallow else str(self.fork)
        _git(*args, src, str(dest))
        # An install follows main (the old release was main back then).
        _git("checkout", "-q", "-B", "main", cwd=dest)
        _git("branch", "-q", "--set-upstream-to", "origin/old", "main", cwd=dest)
        _git("config", "user.email", "t@example.invalid", cwd=dest)
        _git("config", "user.name", "someone", cwd=dest)
        _write(dest, SERVER, "http://127.0.0.1:8080/v1\n")
        _write(dest, MODEL, "my-model\n")
        if edited:
            _write(dest, PERSONALITY, "MY OWN personality\n")
        return dest

    def check_updated(self, dest: Path, edited: bool = True):
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), self.new)
        self.assertEqual(_read(dest, "backend/new_feature.py"), "# v3\n")
        personality = _read(dest, PERSONALITY)
        for marker in _MARKERS:
            self.assertNotIn(marker, personality)
        self.assertEqual(personality, "MY OWN personality\n" if edited else "new default personality\n")
        self.assertEqual(_read(dest, SERVER), "http://127.0.0.1:8080/v1\n")
        self.assertEqual(_read(dest, MODEL), "my-model\n")
        self.assertEqual(_git("stash", "list", cwd=dest), "")       # nothing left shelved
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=dest,
                                capture_output=True, text=True).stdout
        for line in status.splitlines():
            self.assertTrue(line.startswith(" M "), status)         # only unstaged own edits, no conflict


_EXTRACT = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('Write-LogLines', 'Log', 'Write-LogOnly', 'Test-Complete', 'Move-ToRewrittenHistory')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
Set-Content -Path $Out -Value ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n") -Encoding utf8
"""


def _win_update_block() -> str:
    """Step 3's 'Already have Amadeus-Project - checking for updates' part."""
    text = _WIN.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index('    if (Test-Complete $proj @("backend\\main.py","start_windows.bat")) {')
    end = text.index('    } else {\n        Ensure-Clone $ForkUrl $proj', start)
    return text[start:end] + "    }\n"


@unittest.skipUnless(_POWERSHELL and _GIT, "Windows PowerShell + git needed")
class WindowsUpdateTests(_FakeRelease):
    def run_update(self, dest: Path) -> str:
        (self.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        ps = [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        out = subprocess.run(ps + [str(self.tmp / "extract.ps1"), "-Installer", str(_WIN),
                                   "-Out", str(self.tmp / "defs.ps1")], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        run = self.tmp / "run.ps1"
        run.write_text(
            f"$LogPath = '{self.tmp / 'log.txt'}'\n"
            f". '{self.tmp / 'defs.ps1'}'\n"
            f"$proj = '{dest}'\n$ForkUrl = '{self.fork}'\n$Branch = 'main'\n"
            "$LegacyBranch = 'feature/character-memory-spike'\n"
            # The fake fork publishes the new release as 'main'.
            f"git -C '{self.fork}' branch -q -f main '{self.new}'\n"
            f"try {{\n{_win_update_block()}    'RESULT=OK'\n}} catch {{ 'RESULT=ERR ' + $_.Exception.Message }}\n",
            encoding="utf-8-sig")
        out = subprocess.run(ps + [str(run)], capture_output=True, text=True, env=env)
        return out.stdout + out.stderr

    def test_full_clone_with_edited_personality(self):
        dest = self.make_install(shallow=False)
        output = self.run_update(dest)
        self.assertIn("RESULT=OK", output, output)
        self.assertIn("Updated to the latest version", output, output)
        self.check_updated(dest)

    def test_shallow_clone_with_edited_personality(self):
        dest = self.make_install(shallow=True)
        output = self.run_update(dest)
        self.assertIn("Updated to the latest version", output, output)
        self.check_updated(dest)

    def test_untouched_personality_gets_the_new_default(self):
        dest = self.make_install(shallow=True, edited=False)
        output = self.run_update(dest)
        self.assertIn("Updated to the latest version", output, output)
        self.check_updated(dest, edited=False)


def _mac_update_block() -> str:
    """Step 3 of the Mac installer, from 'Found an existing Amadeus' to
    the end of the update branch."""
    text = _MAC.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index('if [ -d "$PROJ/.git" ]; then\n    log "  Found an existing Amadeus')
    end = text.index('    log "  Updated to the latest version on the $BRANCH branch."\n', start)
    return text[start:end] + '    log "  Updated to the latest version on the $BRANCH branch."\nfi\n'


@unittest.skipUnless(_BASH and _GIT, "bash + git needed")
class MacUpdateTests(_FakeRelease):
    def run_update(self, dest: Path) -> str:
        # The Mac installer updates from the clone's own 'origin' remote.
        _git("branch", "-q", "-f", "main", self.new, cwd=self.fork)
        _git("fetch", "-q", "origin", "main", cwd=dest)
        script = self.tmp / "update.sh"
        script.write_bytes((
            "log() { echo \"$*\"; }\ndie() { echo \"DIE: $*\"; exit 1; }\n"
            f"PROJ='{dest.as_posix()}'\nBRANCH=main\nLEGACY_BRANCH=feature/character-memory-spike\n"
            + _mac_update_block() + "echo RESULT=OK\n").encode("utf-8"))
        out = subprocess.run([_BASH, str(script)], capture_output=True, text=True)
        return out.stdout + out.stderr

    # The Mac installer makes full clones (no --depth).
    def test_edited_personality_stays_without_conflict_markers(self):
        dest = self.make_install(shallow=False)
        output = self.run_update(dest)
        self.assertIn("RESULT=OK", output, output)
        self.check_updated(dest)

    def test_untouched_personality_gets_the_new_default(self):
        dest = self.make_install(shallow=False, edited=False)
        output = self.run_update(dest)
        self.assertIn("RESULT=OK", output, output)
        self.check_updated(dest, edited=False)

    def test_no_personal_changes_at_all(self):
        dest = self.make_install(shallow=False, edited=False)
        _git("checkout", "-q", "--", SERVER, MODEL, cwd=dest)
        output = self.run_update(dest)
        self.assertIn("RESULT=OK", output, output)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), self.new)
        self.assertEqual(_read(dest, PERSONALITY), "new default personality\n")

    def test_another_shelved_change_is_left_alone(self):
        dest = self.make_install(shallow=False)
        _write(dest, "backend/main.py", "# someone's own experiment\n")
        _git("stash", "push", "-q", "-m", "users-own", "--", "backend/main.py", cwd=dest)
        output = self.run_update(dest)
        self.assertIn("RESULT=OK", output, output)
        self.assertEqual(_read(dest, PERSONALITY), "MY OWN personality\n")
        self.assertIn("users-own", _git("stash", "list", cwd=dest))


class SameRuleTests(unittest.TestCase):
    def test_both_installers_keep_the_same_personal_files(self):
        win = re.search(r'\$personal = "([^"]+)"', _WIN.read_text(encoding="utf-8")).group(1)
        for name in ("llm_server", "llm_model", "personality"):
            self.assertIn(name, win)
            self.assertIn(name, _MAC.read_text(encoding="utf-8").split("STEP 3")[1][:2500])


if __name__ == "__main__":
    unittest.main()

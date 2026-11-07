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
PERSONALITY_DEFAULT = "backend/data/personality.default.txt"
VOICE = "backend/data/voice_ja.txt"
JA_VOICE = "backend/ja_voice.py"
SERVER = "backend/llm_server.txt"
MODEL = "backend/data/llm_model.txt"
_MARKERS = ("<<<<<<<", ">>>>>>>", "=======")
OLD_VOICE = "【声のルール】\n古いルール。\n"
NEW_VOICE = "【声のルール / Voice rules】\n新しいルール、たくさん良くなった。\n"


def _ja_voice(block: str) -> str:
    """A ja_voice.py with its default the way the real one holds it."""
    return f'import os\n\nDEFAULT_VOICE_BLOCK = r"""\n{block}"""\n\n\ndef load_voice_block():\n    return ""\n'


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
        _write(fork, PERSONALITY_DEFAULT, "old default personality\n")
        _write(fork, JA_VOICE, _ja_voice(OLD_VOICE))
        _write(fork, SERVER, "")
        _write(fork, MODEL, "")
        _git("add", "-A", cwd=fork)
        _git("commit", "-q", "-m", "old release", cwd=fork)
        self.old = _git("rev-parse", "HEAD", cwd=fork)
        _write(fork, PERSONALITY, "new default personality\n")
        _write(fork, PERSONALITY_DEFAULT, "new default personality\n")
        _write(fork, JA_VOICE, _ja_voice(NEW_VOICE))
        _write(fork, "backend/new_feature.py", "# v3\n")
        _git("add", "-A", cwd=fork)
        _git("commit", "-q", "-m", "new release", cwd=fork)
        self.new = _git("rev-parse", "HEAD", cwd=fork)
        _git("branch", "-q", "old", self.old, cwd=fork)
        _git("checkout", "-q", "--detach", cwd=fork)   # so 'main' can be moved
        self.fork = fork

    def make_install(self, shallow: bool, edited: bool = True, voice: str | None = None) -> Path:
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
        if voice is not None:
            _write(dest, VOICE, voice)       # written by the app (not tracked)
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


_EXTRACT_DEFAULTS = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('Write-LogLines', 'Log', 'Write-LogOnly', 'Test-Complete', 'Move-ToRewrittenHistory',
           'Join-ProcessArgs', 'Resolve-Exe', 'Read-Answer', 'Die', 'Write-Utf8File',
           'ConvertTo-NormalText', 'Get-GitFileText', 'Get-NewDefaultText', 'Get-DefaultsToOffer',
           'Confirm-NewDefaults', 'Apply-NewDefaults')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
$table = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$DefaultsFiles' }, $true) | Select-Object -First 1
$parts = @($defs | ForEach-Object { $_.Extent.Text }) + $table.Extent.Text
Set-Content -Path $Out -Value ($parts -join "`n`n") -Encoding utf8
"""


@unittest.skipUnless(_POWERSHELL and _GIT, "Windows PowerShell + git needed")
class WindowsNewDefaultsTests(_FakeRelease):
    """The question after A/B (Confirm-NewDefaults) + step 3 (Apply-NewDefaults)."""

    MY_VOICE = "MY OWN voice rules\n"

    def defs(self) -> Path:
        out = self.tmp / "defaults_defs.ps1"
        if not out.exists():
            (self.tmp / "extract_defaults.ps1").write_text(_EXTRACT_DEFAULTS, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
            ran = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                  str(self.tmp / "extract_defaults.ps1"), "-Installer", str(_WIN),
                                  "-Out", str(out)], capture_output=True, text=True, env=env)
            self.assertEqual(ran.returncode, 0, ran.stderr)
        return out

    def run_installer(self, dest: Path, answer: str, publish_new: bool = True) -> str:
        fork_main = self.new if publish_new else self.old
        run = self.tmp / "run_defaults.ps1"
        run.write_text(
            f"$LogPath = '{self.tmp / 'log.txt'}'\n"
            "Set-Content -Path $LogPath -Value 'test log'\n"   # the installer creates it at its start
            f". '{self.defs()}'\n"
            "function Refresh-Path { }\n"          # the test's PATH stays as it is
            f"$proj = '{dest}'\n$ForkUrl = '{self.fork}'\n$Branch = 'main'\n"
            "$LegacyBranch = 'feature/character-memory-spike'\n"
            f"git -C '{self.fork}' branch -q -f main '{fork_main}'\n"
            "$ReplaceDefaults = Confirm-NewDefaults $proj\n"
            f"try {{\n{_win_update_block()}    'RESULT=OK'\n}} catch {{ 'RESULT=ERR ' + $_.Exception.Message }}\n",
            encoding="utf-8-sig")
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        out = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(run)],
                             input=answer + "\n", capture_output=True, text=True, env=env, encoding="utf-8")
        text = out.stdout + out.stderr
        return ("ASKED=True" if "IMPORTANT: this update" in text else "ASKED=False") + "\n" + text

    def backups(self, dest: Path) -> list:
        root = dest / "backups"
        return sorted(root.iterdir()) if root.is_dir() else []

    def test_yes_saves_then_puts_the_new_defaults_in_place(self):
        dest = self.make_install(shallow=True, voice=self.MY_VOICE)
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=True", out, out)
        self.assertIn("RESULT=OK", out, out)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), self.new)
        self.assertEqual(_read(dest, PERSONALITY), "new default personality\n")
        self.assertFalse((dest / VOICE).exists())                  # the app rebuilds it from the new default
        [folder] = self.backups(dest)
        self.assertEqual((folder / "personality.txt").read_bytes().decode("utf-8"), "MY OWN personality\n")
        self.assertEqual((folder / "voice_ja.txt").read_bytes().decode("utf-8"), self.MY_VOICE)
        self.assertTrue((folder / "README.txt").is_file())
        # Her saved connection is not part of it.
        self.assertEqual(_read(dest, SERVER), "http://127.0.0.1:8080/v1\n")
        self.assertEqual(_read(dest, MODEL), "my-model\n")

    def test_no_keeps_everything(self):
        dest = self.make_install(shallow=True, voice=self.MY_VOICE)
        out = self.run_installer(dest, "N")
        self.assertIn("ASKED=True", out, out)
        self.check_updated(dest)                                    # updated, own personality kept
        self.assertEqual(_read(dest, VOICE), self.MY_VOICE)
        self.assertEqual(self.backups(dest), [])

    def test_untouched_old_defaults_are_offered_too(self):
        dest = self.make_install(shallow=True, edited=False, voice=OLD_VOICE.strip())
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=True", out, out)
        self.assertEqual(_read(dest, PERSONALITY), "new default personality\n")
        self.assertFalse((dest / VOICE).exists())

    def test_nothing_new_means_no_question(self):
        dest = self.make_install(shallow=True, voice=self.MY_VOICE)
        out = self.run_installer(dest, "Y", publish_new=False)    # GitHub still has her version
        self.assertIn("ASKED=False", out, out)
        self.assertEqual(_read(dest, PERSONALITY), "MY OWN personality\n")
        self.assertEqual(_read(dest, VOICE), self.MY_VOICE)
        self.assertEqual(self.backups(dest), [])

    def test_already_the_new_defaults_means_no_question(self):
        dest = self.make_install(shallow=True, edited=False, voice=NEW_VOICE.strip() + "\r\n")
        _write(dest, PERSONALITY, "new default personality\r\n")  # line ends do not count
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=False", out, out)
        self.assertEqual(self.backups(dest), [])

    def test_a_failed_update_replaces_nothing(self):
        dest = self.make_install(shallow=True, voice=self.MY_VOICE)
        _write(dest, "backend/main.py", "# someone's own change\n")  # blocks the update
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=True", out, out)
        self.assertIn("were NOT replaced", out, out)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), self.old)
        self.assertEqual(_read(dest, PERSONALITY), "MY OWN personality\n")
        self.assertEqual(_read(dest, VOICE), self.MY_VOICE)
        self.assertEqual(self.backups(dest), [])

    def test_fresh_install_is_not_asked(self):
        run = self.tmp / "fresh.ps1"
        run.write_text(f". '{self.defs()}'\nfunction Refresh-Path {{ }}\n$ForkUrl = '{self.fork}'\n$Branch = 'main'\n"
                       f"'OFFER=' + (@(Get-DefaultsToOffer '{self.tmp / 'nothing-here'}').Count)\n",
                       encoding="utf-8-sig")
        out = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(run)],
                             capture_output=True, text=True)
        self.assertIn("OFFER=0", out.stdout, out.stdout + out.stderr)

    def test_reads_the_real_defaults_like_the_app(self):
        """The installer's reading of THIS repo's defaults = the app's own."""
        import sys
        backend = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(backend))
        import ja_voice
        import defaults_refresh
        repo = backend.parent
        run = self.tmp / "real.ps1"
        out_file = self.tmp / "real_out.txt"
        run.write_text(
            f". '{self.defs()}'\n"
            f"$v = Get-NewDefaultText '{repo}' 'HEAD' 'voice'\n"
            f"$p = Get-NewDefaultText '{repo}' 'HEAD' 'personality'\n"
            f"[IO.File]::WriteAllText('{out_file}', (ConvertTo-NormalText $v) + \"`n=====SPLIT=====`n\" + (ConvertTo-NormalText $p), (New-Object Text.UTF8Encoding $false))\n",
            encoding="utf-8-sig")
        ran = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(run)],
                             capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        voice, personality = out_file.read_bytes().decode("utf-8").split("\n=====SPLIT=====\n")
        self.assertEqual(voice, defaults_refresh.normalize(ja_voice.DEFAULT_VOICE_BLOCK))
        committed = subprocess.run(["git", "show", "HEAD:backend/data/personality.default.txt"], cwd=repo,
                                   capture_output=True).stdout.decode("utf-8")
        self.assertEqual(personality, defaults_refresh.normalize(committed))
        self.assertTrue(len(voice) > 500 and "Amadeus" in voice)


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


def _mac_defaults_functions() -> str:
    """The Mac installer's built-in-model functions (read_answer lives there)
    and its new-defaults functions."""
    text = _MAC.read_text(encoding="utf-8").replace("\r\n", "\n")
    parts = []
    for name in ("BUILT-IN MODEL FUNCTIONS", "NEW DEFAULTS FUNCTIONS"):
        start = text.index("# >>> " + name)
        parts.append(text[start:text.index("# <<< " + name, start)])
    return "\n".join(parts)


@unittest.skipUnless(_BASH and _GIT, "bash + git needed")
class MacNewDefaultsTests(_FakeRelease):
    """The Mac installer's question after A/B (confirm_new_defaults) + step 3
    (apply_new_defaults) - the same cases as WindowsNewDefaultsTests."""

    MY_VOICE = "MY OWN voice rules\n"

    def script(self, dest: Path, body: str) -> str:
        defs = self.tmp / "mac_defaults_defs.sh"
        defs.write_bytes(_mac_defaults_functions().encode("utf-8"))
        script = self.tmp / "mac_defaults.sh"
        script.write_bytes((
            "set -uo pipefail\n"
            f"TMP='{self.tmp.as_posix()}'\nINSTALL_DIR='{(self.tmp / 'inst').as_posix()}'\nINSTALL_LOG=''\n"
            "tee_to() { cat; }\nlog() { echo \"$*\"; }\ndie() { echo \"DIE: $*\"; exit 1; }\n"
            f"PROJ='{dest.as_posix()}'\nBRANCH=main\nLEGACY_BRANCH=feature/character-memory-spike\n"
            f". '{defs.as_posix()}'\n" + body).encode("utf-8"))
        out = subprocess.run([_BASH, str(script)], capture_output=True, input=b"")
        return (out.stdout + out.stderr).decode("utf-8", "replace")

    def run_installer(self, dest: Path, answer: str, publish_new: bool = True) -> str:
        _git("branch", "-q", "-f", "main", self.new if publish_new else self.old, cwd=self.fork)
        body = ("confirm_new_defaults < '" + (self.tmp / "answer.txt").as_posix() + "'\n"
                "if [ -n \"$DEFAULTS_OFFER\" ]; then echo ASKED=True; else echo ASKED=False; fi\n"
                + _mac_update_block() + "echo RESULT=OK\n")
        (self.tmp / "answer.txt").write_bytes((answer + "\n").encode("utf-8"))
        return self.script(dest, body)

    def backups(self, dest: Path) -> list:
        root = dest / "backups"
        return sorted(root.iterdir()) if root.is_dir() else []

    # The Mac installer makes full clones (no --depth).
    def test_yes_saves_then_puts_the_new_defaults_in_place(self):
        dest = self.make_install(shallow=False, voice=self.MY_VOICE)
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=True", out, out)
        self.assertIn("her personality and her voice rules", out)
        self.assertIn("RESULT=OK", out, out)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), self.new)
        self.assertEqual(_read(dest, PERSONALITY), "new default personality\n")
        self.assertFalse((dest / VOICE).exists())                  # the app rebuilds it from the new default
        [folder] = self.backups(dest)
        self.assertEqual((folder / "personality.txt").read_bytes().decode("utf-8"), "MY OWN personality\n")
        self.assertEqual((folder / "voice_ja.txt").read_bytes().decode("utf-8"), self.MY_VOICE)
        self.assertIn("To go back to your version", (folder / "README.txt").read_text(encoding="utf-8"))
        # Her saved connection is not part of it.
        self.assertEqual(_read(dest, SERVER), "http://127.0.0.1:8080/v1\n")
        self.assertEqual(_read(dest, MODEL), "my-model\n")
        # The real project keeps the backups folder out of git.
        repo = Path(__file__).resolve().parents[2]
        self.assertIn("/backups/", (repo / ".gitignore").read_text(encoding="utf-8").splitlines())

    def test_no_keeps_everything(self):
        dest = self.make_install(shallow=False, voice=self.MY_VOICE)
        out = self.run_installer(dest, "N")
        self.assertIn("ASKED=True", out, out)
        self.assertIn("RESULT=OK", out, out)
        self.check_updated(dest)                                    # updated, own personality kept
        self.assertEqual(_read(dest, VOICE), self.MY_VOICE)
        self.assertEqual(self.backups(dest), [])

    def test_untouched_old_defaults_are_offered_too(self):
        dest = self.make_install(shallow=False, edited=False, voice=OLD_VOICE.strip())
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=True", out, out)
        self.assertEqual(_read(dest, PERSONALITY), "new default personality\n")
        self.assertFalse((dest / VOICE).exists())

    def test_only_the_file_that_differs_is_named(self):
        dest = self.make_install(shallow=False, edited=False, voice=self.MY_VOICE)
        _write(dest, PERSONALITY, "new default personality\n")   # already the new one
        out = self.run_installer(dest, "N")
        self.assertIn("new versions of her voice rules\n", out, out)

    def test_nothing_new_means_no_question(self):
        dest = self.make_install(shallow=False, voice=self.MY_VOICE)
        out = self.run_installer(dest, "Y", publish_new=False)    # GitHub still has her version
        self.assertIn("ASKED=False", out, out)
        self.assertEqual(_read(dest, PERSONALITY), "MY OWN personality\n")
        self.assertEqual(_read(dest, VOICE), self.MY_VOICE)
        self.assertEqual(self.backups(dest), [])

    def test_already_the_new_defaults_means_no_question(self):
        dest = self.make_install(shallow=False, edited=False, voice=NEW_VOICE.strip() + "\r\n")
        _write(dest, PERSONALITY, "new default personality  \r\n\r\n")   # line ends / spaces do not count
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=False", out, out)
        self.assertEqual(self.backups(dest), [])

    def test_a_failed_update_replaces_nothing(self):
        dest = self.make_install(shallow=False, voice=self.MY_VOICE)
        # A commit of someone's own: the copy can't be moved automatically.
        _write(dest, "backend/main.py", "# someone's own change\n")
        _git("-c", "user.name=Someone Else", "-c", "user.email=else@example.invalid",
             "commit", "-q", "-m", "own change", "--", "backend/main.py", cwd=dest)
        own = _git("rev-parse", "HEAD", cwd=dest)
        out = self.run_installer(dest, "Y")
        self.assertIn("ASKED=True", out, out)
        self.assertIn("were NOT replaced", out, out)
        self.assertIn("DIE: Your existing Amadeus copy has diverged", out, out)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=dest), own)
        self.assertEqual(_read(dest, PERSONALITY), "MY OWN personality\n")
        self.assertEqual(_read(dest, VOICE), self.MY_VOICE)
        self.assertEqual(self.backups(dest), [])

    def test_offline_means_no_question(self):
        dest = self.make_install(shallow=False, voice=self.MY_VOICE)
        _git("remote", "set-url", "origin", (self.tmp / "no-such-fork").as_posix(), cwd=dest)
        out = self.script(dest, "confirm_new_defaults </dev/null\necho \"OFFER=[$DEFAULTS_OFFER]\"\n")
        self.assertIn("OFFER=[]", out, out)

    def test_fresh_install_is_not_asked(self):
        out = self.script(self.tmp / "nothing-here",
                          "confirm_new_defaults </dev/null\necho \"OFFER=[$DEFAULTS_OFFER] R=$REPLACE_DEFAULTS\"\n")
        self.assertIn("OFFER=[] R=0", out, out)

    def test_reads_the_real_defaults_like_the_app(self):
        """The installer's reading of THIS repo's defaults = the app's own."""
        import sys
        backend = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(backend))
        import ja_voice
        import defaults_refresh
        repo = backend.parent
        out_v, out_p = self.tmp / "v.txt", self.tmp / "p.txt"
        self.script(repo, f"new_default_text HEAD voice | normal_text > '{out_v.as_posix()}'\n"
                          f"new_default_text HEAD personality | normal_text > '{out_p.as_posix()}'\n")
        voice = out_v.read_bytes().decode("utf-8").rstrip("\n")
        personality = out_p.read_bytes().decode("utf-8").rstrip("\n")
        self.assertEqual(voice, defaults_refresh.normalize(ja_voice.DEFAULT_VOICE_BLOCK))
        committed = subprocess.run(["git", "show", "HEAD:backend/data/personality.default.txt"], cwd=repo,
                                   capture_output=True).stdout.decode("utf-8")
        self.assertEqual(personality, defaults_refresh.normalize(committed))
        self.assertTrue(len(voice) > 500 and "Amadeus" in voice)

    def test_normalizing_matches_the_app(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import defaults_refresh
        sample = "\r\n\n  first line  \r\nmiddle\t \n\n\nlast 　\n\n"
        (self.tmp / "sample.txt").write_bytes(sample.encode("utf-8"))
        (self.tmp / "norm.txt").write_bytes(b"")
        self.script(self.tmp, f"normal_text < '{(self.tmp / 'sample.txt').as_posix()}' > "
                              f"'{(self.tmp / 'norm.txt').as_posix()}'\n")
        got = (self.tmp / "norm.txt").read_bytes().decode("utf-8").rstrip("\n")
        # The app also trims the full-width space at the very end; the
        # installer keeps it (it then only asks once more) - everything
        # else must be the same.
        self.assertEqual(got.rstrip("\u3000 "), defaults_refresh.normalize(sample).rstrip("\u3000 "))


class SameRuleTests(unittest.TestCase):
    def test_both_installers_keep_the_same_personal_files(self):
        win = re.search(r'\$personal = "([^"]+)"', _WIN.read_text(encoding="utf-8")).group(1)
        for name in ("llm_server", "llm_model", "personality"):
            self.assertIn(name, win)
            self.assertIn(name, _MAC.read_text(encoding="utf-8").split("STEP 3")[1][:2500])


if __name__ == "__main__":
    unittest.main()

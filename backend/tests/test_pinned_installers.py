"""scripts/make_pinned_installers.py (v3.1.1): pinned installers for releases.

The maker patches each release's OWN installer. These tests build the newest
release tag that is present locally (skipped in a copy without tags) and the
patch functions on small synthetic installers, so a changed installer that
loses one of the patch points fails here instead of at release time.
"""
import importlib.util
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MAKER = _ROOT / "scripts" / "make_pinned_installers.py"


def _load():
    spec = importlib.util.spec_from_file_location("make_pinned_installers", _MAKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


maker = _load()

SHA = "0123456789abcdef0123456789abcdef01234567"

WIN_SAMPLE = """#  AMADEUS - ONE-SHOT INSTALLER FOR WINDOWS   v4.13.4  (resumable / safe to re-run)
param(
    [string]$Branch = "main",
    [string]$EnvSuffix = ""
)
$InstallerVersion = "v4.13.4"
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE "Amadeus" }
$InstallDir = $InstallDir.TrimEnd('\\')
if ($Branch -ne "main") { Log "test" }
    if (Test-Complete $proj @("backend\\main.py","start_windows.bat")) {
        Log "  Already have Amadeus-Project - checking for updates." "Yellow"
    } else {
        Ensure-Clone $ForkUrl $proj $Branch @("backend\\main.py","start_windows.bat") $true
    }
"""

SH_SAMPLE = """#  AMADEUS - ONE-SHOT INSTALLER FOR LINUX   v1.1  (resumable / safe to re-run)
BRANCH="main"
INSTALLER_VERSION="v1.1"
INSTALL_DIR="${INSTALL_DIR:-$HOME/Amadeus}"
PROJ="$INSTALL_DIR/Amadeus-Project"
if [ "$BRANCH" != "main" ]; then log "  branch            : $BRANCH"; fi
if [ -d "$PROJ/.git" ]; then
    log "  Found an existing Amadeus at $PROJ - updating it..."
else
    clone_or_update "$REPO_URL" "$PROJ" "$BRANCH"
fi
"""


class PatchTests(unittest.TestCase):
    def test_windows_patch(self):
        out = maker.pin_windows(WIN_SAMPLE, "v3.0.4", SHA)
        self.assertIn('[string]$Branch = "v3.0.4",', out)
        self.assertIn('$InstallerVersion = "v3.0.4-pinned"', out)
        self.assertIn('$Branch -ne "v3.0.4"', out)
        self.assertNotIn('"main"', out.split("PINNED INSTALLER")[0].replace('[string]$Branch = "v3.0.4"', ""))
        self.assertIn(f'$PinnedCommit  = "{SHA}"', out)
        self.assertIn("v3.0.4-pinned (installs exactly Amadeus v3.0.4)", out)
        # the guard runs right after the install folder is known, before any step
        self.assertLess(out.index("$InstallDir = $InstallDir.TrimEnd"), out.index("Get-PinnedState $pinnedProj"))
        self.assertIn("-eq $PinnedCommit)) {", out)
        self.assertIn("} elseif (Test-Complete $proj", out)

    def test_sh_patch(self):
        out = maker.pin_sh(SH_SAMPLE, "v3.0.4", SHA, "LINUX")
        self.assertIn('BRANCH="v3.0.4"', out)
        self.assertIn('INSTALLER_VERSION="v3.0.4-pinned"', out)
        self.assertIn('[ "$BRANCH" != "v3.0.4" ]', out)
        self.assertIn(f'PINNED_COMMIT="{SHA}"', out)
        self.assertLess(out.index('PROJ="$INSTALL_DIR/Amadeus-Project"'), out.index('_pin_state="$(pinned_state "$PROJ")"'))
        self.assertIn('elif [ -d "$PROJ/.git" ]; then', out)

    def test_missing_patch_point_fails_loudly(self):
        with self.assertRaises(maker.PatchError):
            maker.pin_windows(WIN_SAMPLE.replace('[string]$Branch = "main",', ""), "v3.0.4", SHA)
        with self.assertRaises(maker.PatchError):
            maker.pin_sh(SH_SAMPLE.replace('PROJ="$INSTALL_DIR/Amadeus-Project"\n', ""), "v3.0.4", SHA, "LINUX")

    def test_bat_downloads_the_release_copy(self):
        bat = maker.windows_bat("v3.0.4")
        self.assertIn("/releases/download/v3.0.4/install_windows-v3.0.4.ps1", bat)
        self.assertIn('-File "%AMADEUS_PS1%" %*', bat)
        self.assertNotIn("/main/", bat)

    def test_release_tag_filter(self):
        self.assertIsNone(maker.tag_key("v3.1.0-rc1"))
        self.assertGreaterEqual(maker.tag_key("v3.0"), maker.FIRST_PINNED)
        self.assertLess(maker.tag_key("v2.1.1"), maker.FIRST_PINNED)


class SharedGuardTests(unittest.TestCase):
    """The shell guard, run for real on fake install folders (needs bash)."""

    @classmethod
    def setUpClass(cls):
        # On Windows prefer Git's own bash: "bash" on PATH may be the WSL
        # launcher in System32, which cannot see these temp paths.
        cls.bash = None
        git = shutil.which("git")
        if git:
            for up in (1, 2):
                cand = pathlib.Path(git).resolve().parents[up] / "bin" / "bash.exe"
                if cand.exists():
                    cls.bash = str(cand)
                    break
        if not cls.bash:
            found = shutil.which("bash")
            if found and "system32" not in found.lower():
                cls.bash = found

    def run_guard(self, head_text):
        if not self.bash:
            self.skipTest("bash not available")
        guard = maker.SH_GUARD.replace("__TAG__", "v3.0.4").replace("__SHA__", SHA)
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            proj = home / "Amadeus" / "Amadeus-Project"
            if head_text is not None:
                (proj / ".git").mkdir(parents=True)
                (proj / ".git" / "HEAD").write_text(head_text, encoding="utf-8")
            script = home / "g.sh"
            script.write_text('PROJ="$HOME/Amadeus/Amadeus-Project"\n' + guard + 'echo PASSED\n', encoding="utf-8", newline="\n")
            r = subprocess.run([self.bash, "-c", 'HOME="$1"; . "$2"', "x", home.as_posix(), script.as_posix()],
                               capture_output=True, text=True)
            return r.returncode, r.stdout

    def test_empty_folder_continues(self):
        code, out = self.run_guard(None)
        self.assertEqual(code, 0)
        self.assertIn("PASSED", out)

    def test_same_version_continues(self):
        code, out = self.run_guard(SHA + "\n")
        self.assertEqual(code, 0)
        self.assertIn("already installed here", out)

    def test_latest_install_stops(self):
        code, out = self.run_guard("ref: refs/heads/main\n")
        self.assertEqual(code, 1)
        self.assertIn("follows the 'main' branch", out)
        self.assertNotIn("PASSED", out)

    def test_other_version_stops(self):
        code, out = self.run_guard("fedcba9876543210fedcba9876543210fedcba98\n")
        self.assertEqual(code, 1)
        self.assertIn("a different version (commit fedcba9)", out)


class RealReleaseTests(unittest.TestCase):
    def test_newest_release_builds(self):
        try:
            tags = maker.release_tags()
        except Exception:
            tags = []
        if not tags:
            self.skipTest("no release tags in this copy")
        tag = tags[-1]
        with tempfile.TemporaryDirectory() as tmp:
            files = maker.build(tag, pathlib.Path(tmp))
            names = {f.name for f in files}
            self.assertIn(f"install_windows-{tag}.ps1", names)
            self.assertIn(f"Install-Amadeus-{tag}.bat", names)
            self.assertIn(f"install_macos-{tag}.sh", names)
            ps1 = (pathlib.Path(tmp) / tag / f"install_windows-{tag}.ps1").read_text(encoding="utf-8")
            self.assertRegex(ps1, r'\$PinnedCommit  = "[0-9a-f]{40}"')
            self.assertTrue(re.search(rf'\[string\]\$Branch = "{re.escape(tag)}"', ps1))


if __name__ == "__main__":
    unittest.main()

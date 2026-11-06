# -*- coding: utf-8 -*-
"""The installer's "safe to update?" check (Step 3, existing install).

Windows only (skipped elsewhere). The real check lines are taken out of
install_windows.ps1 and run in Windows PowerShell inside a throwaway git
copy - nothing is downloaded. Covers (VM test 2026-10-25: the .bat saved
install_windows.ps1 into the project folder and every update was refused):
  1. extra files nobody committed (the downloaded installer, test tools) do
     NOT block an update, and they survive it;
  2. the personal settings files still do not block;
  3. a changed project file still blocks;
  4. an extra file the update would overwrite makes git refuse - the
     user's file stays as it was.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_INSTALLER = Path(__file__).resolve().parents[2] / "scripts" / "install_windows.ps1"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
_GIT = shutil.which("git")


def _check_lines() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    personal = re.search(r"^\s*\$personal\s*=.*$", text, re.M)
    status = re.search(r"^\s*\$status\s*=\s*& git status.*$", text, re.M)
    if not personal or not status:
        raise AssertionError("update check lines not found in install_windows.ps1")
    return personal.group(0).strip() + "\n" + status.group(0).strip() + "\n"


@unittest.skipUnless(_POWERSHELL and _GIT, "Windows PowerShell or git not available")
class InstallerUpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_upd_"))
        self.src = self.tmp / "src"
        self.proj = self.tmp / "proj"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.src))
        (self.src / "backend").mkdir()
        (self.src / "backend" / "main.py").write_text("v1\n", encoding="utf-8")
        (self.src / "backend" / "llm_server.txt").write_text("\n", encoding="utf-8")
        self.git(self.src, "add", ".")
        self.commit(self.src, "v1")
        self.git(self.tmp, "clone", "-q", str(self.src), str(self.proj))
        (self.src / "backend" / "main.py").write_text("v2\n", encoding="utf-8")
        self.git(self.src, "add", ".")
        self.commit(self.src, "v2")
        self.git(self.proj, "fetch", "-q", "origin")
        (self.tmp / "check.ps1").write_text(
            _check_lines() + "if ($status) { 'BLOCK' } else { 'OK' }\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        out = subprocess.run([_GIT, *args], cwd=cwd, capture_output=True, text=True)
        return out

    def commit(self, cwd, msg):
        out = self.git(cwd, "-c", "user.name=t", "-c", "user.email=t@example.com",
                       "commit", "-q", "-m", msg)
        self.assertEqual(out.returncode, 0, out.stderr)

    def check(self) -> str:
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        out = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-File", str(self.tmp / "check.ps1")],
                             cwd=self.proj, capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_extra_files_do_not_block_and_survive(self):
        (self.proj / "install_windows.ps1").write_text("# downloaded\n", encoding="utf-8")
        (self.proj / "Reset Amadeus (test).bat").write_text("rem tool\n", encoding="utf-8")
        (self.proj / "backend" / "llm_server.txt").write_text("http://x\n", encoding="utf-8")
        self.assertEqual(self.check(), "OK")
        merge = self.git(self.proj, "merge", "--ff-only", "origin/main")
        self.assertEqual(merge.returncode, 0, merge.stderr)
        self.assertEqual((self.proj / "backend" / "main.py").read_text(encoding="utf-8"), "v2\n")
        self.assertTrue((self.proj / "install_windows.ps1").exists())
        self.assertTrue((self.proj / "Reset Amadeus (test).bat").exists())

    def test_changed_project_file_still_blocks(self):
        (self.proj / "backend" / "main.py").write_text("my edit\n", encoding="utf-8")
        self.assertEqual(self.check(), "BLOCK")

    def test_update_never_overwrites_an_extra_file(self):
        (self.src / "install_windows.ps1").write_text("# from the project\n", encoding="utf-8")
        self.git(self.src, "add", ".")
        self.commit(self.src, "v3")
        self.git(self.proj, "fetch", "-q", "origin")
        (self.proj / "install_windows.ps1").write_text("# mine\n", encoding="utf-8")
        self.assertEqual(self.check(), "OK")
        merge = self.git(self.proj, "merge", "--ff-only", "origin/main")
        self.assertNotEqual(merge.returncode, 0)
        keep = self.git(self.proj, "reset", "--keep", "origin/main")  # the installer's fallback
        self.assertNotEqual(keep.returncode, 0)
        self.assertEqual((self.proj / "install_windows.ps1").read_text(encoding="utf-8"), "# mine\n")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Windows installer v4.13.1 + launcher: a Node.js that cannot run her web page.

2026-11-08, a tester: an old 32-bit Node.js (npm 6) in "Program Files (x86)"
was accepted by the installer; npm 6 opened a bare command prompt instead of
starting Vite, so every start ended with "WebUI did not become ready".
Covers:
  1. the installer's Get-NodeProblem: old (< 20) or 32-bit -> a reason;
     20+ 64-bit (and arm64) -> fine; a node that prints nothing -> "does
     not run" (Windows PowerShell, pretend node programs, nothing installed);
  2. Get-NodeId reads "node<major> <arch>";
  3. the installer's step 2 / step 9 wiring: Amadeus' own copy when the PC's
     Node.js has a problem, the web page's parts set up again on a change;
  4. the launcher: its own Node.js goes first on PATH (Windows), an old
     Node.js stops the start with a plain message, a fine/unknown one not.
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
$names = @('Get-NodeProblem', 'Get-NodeId')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
Set-Content -Path $Out -Value ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n") -Encoding utf8
"""


def _ps(args, timeout=120):
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    return subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
                          capture_output=True, text=True, timeout=timeout, env=env,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_node", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class InstallerNodeCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="amadeus_node_"))
        (cls.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        out = _ps(["-File", str(cls.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(cls.tmp / "defs.ps1")])
        if out.returncode != 0:
            raise RuntimeError(out.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fake_node(self, name: str, prints: str) -> Path:
        """A pretend node: a .cmd that prints a fixed line whatever it is asked."""
        path = self.tmp / f"{name}.cmd"
        path.write_text(f"@echo off\r\n{('echo ' + prints) if prints else 'exit /b 1'}\r\n",
                        encoding="ascii")
        return path

    def call(self, func: str, exe: Path) -> str:
        script = self.tmp / "run.ps1"
        script.write_text(
            f". '{self.tmp / 'defs.ps1'}'\n"
            f"$r = {func} '{exe}'\n"
            "if ($null -eq $r) { 'NULL' } else { $r }\n", encoding="utf-8-sig")
        out = _ps(["-File", str(script)])
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_old_node_is_refused(self):
        r = self.call("Get-NodeProblem", self.fake_node("old", "14.21.3 ia32"))
        self.assertIn("14.21.3", r)
        self.assertIn("too old", r)

    def test_32bit_node_is_refused(self):
        r = self.call("Get-NodeProblem", self.fake_node("x86", "22.11.0 ia32"))
        self.assertIn("32-bit", r)

    def test_new_64bit_node_is_fine(self):
        self.assertEqual(self.call("Get-NodeProblem", self.fake_node("ok", "24.22.1 x64")), "NULL")
        self.assertEqual(self.call("Get-NodeProblem", self.fake_node("arm", "20.0.0 arm64")), "NULL")

    def test_node_19_is_too_old_for_the_installer(self):
        self.assertIn("too old", self.call("Get-NodeProblem", self.fake_node("n19", "19.9.0 x64")))

    def test_silent_node_does_not_run(self):
        self.assertIn("does not run", self.call("Get-NodeProblem", self.fake_node("dead", "")))

    def test_node_id(self):
        self.assertEqual(self.call("Get-NodeId", self.fake_node("id", "node24 x64")), "node24 x64")
        self.assertEqual(self.call("Get-NodeId", self.fake_node("bad", "")), "NULL")


class InstallerNodeWiringTests(unittest.TestCase):
    """The step 2 / step 9 code around the two functions (read as text)."""

    @classmethod
    def setUpClass(cls):
        cls.text = _INSTALLER.read_text(encoding="utf-8")

    def test_installer_is_ascii(self):
        # Windows PowerShell 5.1 reads a file without a byte-order mark in the
        # PC's own code page - the installer stays plain ASCII, apart from the
        # one known Japanese test character of the voice dictionary check.
        for line in _INSTALLER.read_bytes().decode("utf-8").splitlines():
            if not line.isascii():
                self.assertIn("pyopenjtalk.g2p(", line)

    def test_version(self):
        # v4.13.1 brought the Node.js check; later hotfixes keep it.
        self.assertRegex(self.text, r'\$InstallerVersion = "v4\.13\.\d+"')

    def test_step2_uses_own_copy_when_the_pc_node_has_a_problem(self):
        self.assertIn("$sysNodeProblem = Get-NodeProblem $sysNode.Source", self.text)
        self.assertIn("if ($sysNode -and -not $sysNodeProblem)", self.text)
        self.assertIn("$script:NodeSwitched = $true", self.text)
        self.assertIn("$nowProblem = Get-NodeProblem $nowNode.Source", self.text)

    def test_step9_sets_parts_up_again_on_a_change(self):
        self.assertIn('".amadeus-node.txt"', self.text)
        self.assertIn("-or (-not $nodeBefore -and $NodeSwitched)", self.text)
        self.assertIn('rmdir /s /q "$nodeModules"', self.text)
        self.assertIn("[IO.File]::WriteAllText($nodeRecord, $nodeNow)", self.text)

    def test_own_node_goes_first_on_path(self):
        self.assertIn('foreach ($sub in @("node", "git\\cmd", "git-lfs", "ffmpeg\\bin"))', self.text)


class LauncherNodeTests(unittest.TestCase):
    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_lnode_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_node_stops_with_a_plain_message(self):
        msg = self.launcher.node_problem((r"C:\Program Files (x86)\nodejs\node.exe", "14.21.3"))
        self.assertIn("14.21.3", msg)
        self.assertIn("installer again", msg)

    def test_fine_or_unknown_node_does_not_stop(self):
        self.assertIsNone(self.launcher.node_problem(("node", "18.20.0")))
        self.assertIsNone(self.launcher.node_problem(("node", "24.22.1")))
        self.assertIsNone(self.launcher.node_problem(None))

    def test_node_version_reads_the_node_beside_npm(self):
        exe = "node.exe" if os.name == "nt" else "node"
        (self.tmp / exe).write_text("", encoding="ascii")
        npm = self.tmp / ("npm.cmd" if os.name == "nt" else "npm")
        done = subprocess.CompletedProcess([], 0, stdout="14.21.3\n", stderr="")
        with mock.patch.object(self.launcher.subprocess, "run", return_value=done) as run:
            found = self.launcher.node_version(str(npm))
        self.assertEqual(found, (str(self.tmp / exe), "14.21.3"))
        self.assertEqual(run.call_args[0][0][0], str(self.tmp / exe))

    def test_node_version_unknown_when_it_prints_nonsense(self):
        (self.tmp / ("node.exe" if os.name == "nt" else "node")).write_text("", encoding="ascii")
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(self.launcher.subprocess, "run", return_value=done):
            self.assertIsNone(self.launcher.node_version(str(self.tmp / "npm.cmd")))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_own_node_goes_first_on_path(self):
        own = self.tmp / "node"
        own.mkdir()
        (own / "node.exe").write_text("", encoding="ascii")
        self.launcher.OWN_NODE_DIR = own
        with mock.patch.dict(os.environ, {"PATH": r"C:\Program Files (x86)\nodejs;C:\Windows"}):
            self.launcher.use_own_node()
            self.launcher.use_own_node()  # a second call adds nothing
            parts = os.environ["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], str(own))
        self.assertEqual(parts.count(str(own)), 1)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_no_own_node_leaves_path_alone(self):
        self.launcher.OWN_NODE_DIR = self.tmp / "missing"
        with mock.patch.dict(os.environ, {"PATH": r"C:\Windows"}):
            self.launcher.use_own_node()
            self.assertEqual(os.environ["PATH"], r"C:\Windows")

    def test_start_uses_own_node_before_looking_for_npm(self):
        text = _LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("    use_own_node()\n    npm = find_npm()", text)
        self.assertIn("problem = node_problem(found)", text)


if __name__ == "__main__":
    unittest.main()

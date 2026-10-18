# -*- coding: utf-8 -*-
"""Offline checks for the launcher's quiet window and the memory setup step.

2026-10-18: the launcher window was a wall of "[Launcher]" lines and raw pip
output on first start; it now shows a few status lines, every detail goes to
logs/launcher.log, and the installer sets her memory up (by running the
launcher's own setup code). Covers:
  1. the logs live in a visible logs/ folder, not under .runtime/;
  2. say() reaches the window and the log, note() only the log;
  3. run_tool keeps a program's output out of the window (all of it in the
     log, pip's progress lines excepted), returns its exit code, and prints
     it unchanged in passthrough mode (the installer's progress bar reads it);
  4. a program that cannot start returns 127 instead of raising;
  5. pip is asked again without its progress option only when it is too old;
  6. ensure_cm_sidecar builds from the Python the installer names;
  7. --setup-memory refuses a missing Python;
  8. both installers run the launcher's setup step with their memory Python.
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER = _ROOT / "scripts" / "launcher.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_console", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_console_"))
        self.launcher.LOG_DIR = self.tmp / "logs"
        self.launcher.LAUNCHER_LOG = self.launcher.LOG_DIR / "launcher.log"
        self.launcher.open_launcher_log()

    def tearDown(self):
        if self.launcher._launcher_log is not None:
            self.launcher._launcher_log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _log(self):
        self.launcher._launcher_log.flush()
        return self.launcher.LAUNCHER_LOG.read_text(encoding="utf-8")

    def _child(self, code):
        return [sys.executable, "-c", code]

    def test_logs_folder_is_visible(self):
        fresh = _load_launcher()
        self.assertEqual(fresh.LOG_DIR, fresh.PROJECT_ROOT / "logs")
        self.assertNotIn(".runtime", fresh.LOG_DIR.parts)
        self.assertEqual(fresh.LAUNCHER_LOG.parent, fresh.LOG_DIR)

    def test_say_and_note(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.launcher.say("  Her voice is ready")
            self.launcher.note("Conda   : C:\\somewhere")
        self.assertIn("Her voice is ready", out.getvalue())
        self.assertNotIn("Conda", out.getvalue())
        log = self._log()
        self.assertIn("Her voice is ready", log)
        self.assertIn("Conda   : C:\\somewhere", log)

    def test_begin_end_share_one_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.launcher.begin("Her voice")
            self.launcher.end("ready (3 s)")
        self.assertEqual(out.getvalue(), "  Her voice ... ready (3 s)\n")

    def test_run_tool_keeps_output_out_of_the_window(self):
        code = ("print('Collecting numpy==2.5.3'); print('Progress 5 of 10'); "
                "print('Successfully installed numpy-2.5.3'); raise SystemExit(3)")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.launcher.run_tool("Setting up her memory", self._child(code))
        self.assertEqual(rc, 3)
        self.assertNotIn("Successfully installed", out.getvalue())
        log = self._log()
        self.assertIn("Collecting numpy==2.5.3", log)
        self.assertIn("Successfully installed numpy-2.5.3", log)
        self.assertNotIn("| Progress 5 of 10", log)   # progress ticks are noise
        self.assertIn("exit 3", log)
        self.assertEqual(self.launcher._last_tail[-1], "Successfully installed numpy-2.5.3")

    def test_run_tool_passthrough_prints_everything(self):
        code = "print('Collecting faiss-cpu'); print('Progress 1 of 2')"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.launcher.run_tool("x", self._child(code), passthrough=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines(), ["Collecting faiss-cpu", "Progress 1 of 2"])

    def test_run_tool_missing_program(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.launcher.run_tool("x", [str(self.tmp / "no_such_program.exe")])
        self.assertEqual(rc, 127)

    def _sidecar(self):
        sc = self.tmp / "memory_sidecar"
        sc.mkdir()
        (sc / "cm_sidecar.py").write_text("", encoding="utf-8")
        (sc / "requirements.txt").write_text("", encoding="utf-8")
        self.launcher.CM_DIR = sc
        return sc

    def test_pip_retried_only_for_an_old_pip(self):
        self._sidecar()
        for tail, expected_calls in ((["ERROR: no such option: --progress-bar"], 2),
                                     (["ERROR: Could not find a version"], 1)):
            calls = []

            def fake(label, cmd, passthrough=False, _tail=tail):
                calls.append(cmd)
                self.launcher._last_tail[:] = _tail
                return 1
            with self.subTest(tail=tail[0]), patch.object(self.launcher, "run_tool", side_effect=fake), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(self.launcher._pip_install_sidecar())
                self.assertEqual(len(calls), expected_calls)
                self.assertIn("raw", calls[0])
                if expected_calls == 2:
                    self.assertNotIn("--progress-bar", calls[1])

    def test_sidecar_built_from_the_installers_python(self):
        sc = self._sidecar()
        given = self.tmp / "envs" / "amadeus-cm-test" / "python.exe"
        calls = []
        with patch.object(self.launcher, "_cm_base_python") as lookup, \
                patch.object(self.launcher, "run_tool",
                             side_effect=lambda label, cmd, passthrough=False: calls.append((cmd, passthrough)) or 0), \
                patch.object(self.launcher, "_pip_install_sidecar", return_value=True):
            self.assertTrue(self.launcher.ensure_cm_sidecar(base_python=given, passthrough=True))
            lookup.assert_not_called()
        self.assertEqual(calls[0][0][:3], [str(given), "-m", "venv"])
        self.assertTrue(calls[0][1])
        self.assertTrue((sc / ".venv_installed").exists())

    def test_setup_memory_refuses_a_missing_python(self):
        self._sidecar()
        with patch.object(self.launcher, "ensure_cm_sidecar") as ensure, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.launcher.setup_memory(str(self.tmp / "nope.exe")), 1)
            ensure.assert_not_called()
        with patch.object(self.launcher, "ensure_cm_sidecar", return_value=True) as ensure:
            self.assertEqual(self.launcher.setup_memory(None), 0)
            ensure.assert_called_once_with(base_python=None, passthrough=True)


class AppWindowTests(unittest.TestCase):
    """Her own app window (2026-10-18): a Chromium browser with --app and its
    own profile, so the autoplay flag applies even with Chrome already open."""

    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_appwin_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launcher.APP_PROFILE_DIR = self.tmp / "app-window"
        self.launcher.open_launcher_log = lambda: None

    def test_flags(self):
        args = self.launcher.app_window_args("http://127.0.0.1:5173/")
        self.assertIn("--app=http://127.0.0.1:5173/", args)
        self.assertIn(f"--user-data-dir={self.tmp / 'app-window'}", args)
        self.assertIn("--autoplay-policy=no-user-gesture-required", args)
        self.assertTrue((self.tmp / "app-window").is_dir())
        self.assertIn(".runtime", _load_launcher().APP_PROFILE_DIR.parts)

    @unittest.skipUnless(os.name == "nt", "Windows browser lookup")
    def test_windows_picks_chromium_then_edge(self):
        chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        firefox = r"C:\Program Files\Mozilla Firefox\firefox.exe"
        for default, edge_found, expected in ((chrome, edge, chrome), (firefox, edge, edge),
                                              (firefox, None, None), (None, edge, edge)):
            with self.subTest(default=default, edge=edge_found), \
                    patch.object(self.launcher, "_windows_default_browser_exe", return_value=default), \
                    patch.object(self.launcher, "_windows_edge_exe", return_value=edge_found), \
                    patch.object(self.launcher.subprocess, "Popen") as popen:
                opened = self.launcher._open_browser_windows("http://127.0.0.1:5173/")
                self.assertEqual(opened, expected is not None)
                if expected:
                    cmd = popen.call_args[0][0]
                    self.assertEqual(cmd[0], expected)
                    self.assertIn("--app=http://127.0.0.1:5173/", cmd)
                else:
                    popen.assert_not_called()   # -> plain webbrowser.open()


class InstallerMemoryStepTests(unittest.TestCase):
    def test_windows_installer_runs_the_launchers_setup(self):
        text = (_ROOT / "scripts" / "install_windows.ps1").read_text(encoding="utf-8")
        step5 = text[text.index('Step 5 "'):text.index('Step 6 "')]
        self.assertIn('"--setup-memory"', step5)
        self.assertIn('"--cm-python", $cmPy', step5)
        self.assertIn("$cmPy = Env-Python $EnvCm", step5)
        self.assertIn('"scripts\\launcher.py"', step5)

    def test_mac_installer_runs_the_launchers_setup(self):
        text = (_ROOT / "scripts" / "install_macos.sh").read_text(encoding="utf-8")
        step5 = text[text.index('step 5 "'):text.index('step 6 "')]
        self.assertIn("--setup-memory", step5)
        self.assertIn('--cm-python "$cm_py"', step5)
        self.assertIn('cm_py="$CONDA_HOME/envs/amadeus-cm/bin/python"', step5)

    def test_start_scripts_point_to_the_visible_logs(self):
        for name in ("start_windows.bat", "start_macos.command"):
            with self.subTest(script=name):
                text = (_ROOT / name).read_text(encoding="utf-8")
                self.assertNotIn(".runtime\\logs", text)
                self.assertNotIn(".runtime/logs", text)
                self.assertIn("logs", text)


if __name__ == "__main__":
    unittest.main()

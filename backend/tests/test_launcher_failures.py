# -*- coding: utf-8 -*-
"""Clear failure messages for the launcher and her built-in model
(2026-11-10: a Linux tester's GPT-SoVITS crashed on a missing package and
the launcher only said "exited during startup").

Every failure message must say (1) WHICH part of Amadeus failed, in plain
words, (2) the most likely reason in one sentence - only when the log
actually shows it, never a guess, (3) what to try, (4) where to get help:
the Discord + the exact file to send (for the app, her whole logs folder).

Covers:
  * the shared failure helper: plain words per part, the known causes
    (a missing package names the package; a busy port names the port), no
    invented reason for an unrecognized log, the help line always present;
  * wait_for_port: a crashed part and a part that never becomes ready give
    that message (run for real - a tiny Python process stands in for the
    part);
  * the voice preflight (its conda call mocked): missing packages, a clean
    toolbox, a missing conda environment, and a silent failure (the start
    must never be blocked);
  * the graphics-memory line: a card whose memory cannot be read says so
    instead of the old "None GB free";
  * her built-in model's failure messages end with the help line (the
    Discord + the logs folder to send), and the "often not enough memory"
    words only when her log shows a memory shortage.
"""
import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_BACKEND = _TESTS.parent
for _p in (_BACKEND, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_DISCORD = "https://discord.gg/YxYkZPSVf3"


def _load_launcher():
    spec = importlib.util.spec_from_file_location(
        "amadeus_launcher_failures_test", _BACKEND.parent / "scripts" / "launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


L = _load_launcher()


def _fixture(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class KnownFailureReasonTests(unittest.TestCase):
    """The most likely reason - only for causes the log really shows."""

    def test_missing_package_names_the_package(self):
        tail = ("Traceback (most recent call last):\n"
                "  File \"start_gptsovits.py\", line 12, in <module>\n"
                "ModuleNotFoundError: No module named 'soundfile'\n")
        reason = L.known_failure_reason("GPT-SoVITS", tail)
        self.assertIn("soundfile", reason)
        self.assertIn("Her voice toolbox", reason)
        self.assertIn("run the installer again", reason)

    def test_missing_package_double_quoted(self):
        reason = L.known_failure_reason("backend",
                                        'ModuleNotFoundError: No module named "torch"')
        self.assertIn("torch", reason)
        self.assertIn("The app's toolbox", reason)

    def test_port_in_use_names_the_port(self):
        reason = L.known_failure_reason("backend",
                                        "OSError: [Errno 98] Address already in use")
        self.assertIn("Port 5050", reason)
        self.assertIn("close that program", reason)

    def test_unrecognized_tail_invents_nothing(self):
        for tail in ("", "some weird error: kaboom\n", "line one\nline two\n"):
            with self.subTest(tail=tail):
                self.assertEqual(L.known_failure_reason("GPT-SoVITS", tail), "")

    def test_unknown_part_with_port_cause_stays_plain(self):
        reason = L.known_failure_reason("built-in model", "address already in use")
        self.assertIn("port", reason.lower())
        self.assertNotIn("Port 9880", reason)


class FailureMessageTests(unittest.TestCase):
    def test_full_message_says_part_reason_try_and_help(self):
        # Her whole logs folder is the thing to send: never called "the file".
        m = L.failure_message("GPT-SoVITS", "stopped while Amadeus was starting it",
                              reason="Her voice toolbox is incomplete (the package "
                                     "'soundfile' is missing) - run the installer again.",
                              file_hint="C:\\x\\Amadeus\\logs", logs_folder=True)
        self.assertIn("Her voice stopped while Amadeus was starting it.", m)
        self.assertIn("soundfile", m)
        self.assertIn("What to try: start Amadeus again", m)
        self.assertIn(_DISCORD, m)
        self.assertIn("and send your logs folder: C:\\x\\Amadeus\\logs.", m)
        self.assertNotIn("send the file", m)

    def test_plain_words_per_part(self):
        for part, word in (("GPT-SoVITS", "Her voice"), ("backend", "The app"),
                           ("WebUI", "The web page"), ("cm-sidecar", "Her memory"),
                           ("built-in model", "Her built-in model")):
            with self.subTest(part=part):
                m = L.failure_message(part, "did not start")
                self.assertTrue(m.startswith(f"{word} did not start."), m)

    def test_unknown_cause_has_no_invented_reason(self):
        m = L.failure_message("cm-sidecar", "did not become ready after 600 seconds")
        low = m.lower()
        for word in ("incomplete", "package", "port", "driver", "not enough",
                     "missing", "memory card"):
            self.assertNotIn(word, low)

    def test_help_line_always_present(self):
        self.assertEqual(L.help_line(), f"Need help? Ask on our Discord: {_DISCORD}.")
        # A logs folder is named a folder; a single file stays "the file".
        self.assertEqual(L.help_line("/a/b/logs", logs_folder=True),
                         f"Need help? Ask on our Discord: {_DISCORD} "
                         f"- and send your logs folder: /a/b/logs.")
        self.assertEqual(L.help_line("/a/b/logs/llm.log"),
                         f"Need help? Ask on our Discord: {_DISCORD} - and send the file /a/b/logs/llm.log.")


class ServiceFailureTests(unittest.TestCase):
    """service_failure(): the message a part that did not start raises."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_fail_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.logs = self.tmp / "logs"
        self.logs.mkdir()
        self._orig_logs = L.LOG_DIR
        L.LOG_DIR = self.logs
        self.addCleanup(setattr, L, "LOG_DIR", self._orig_logs)

    def _write_log(self, name, text):
        (self.logs / name).write_text(text, encoding="utf-8")

    def test_crash_with_known_cause(self):
        self._write_log("gptsovits.log",
                        "starting her voice...\n"
                        "ModuleNotFoundError: No module named 'soundfile'\n")
        m = L.service_failure("GPT-SoVITS", "stopped while Amadeus was starting it")
        self.assertIn("Her voice stopped while Amadeus was starting it.", m)
        self.assertIn("soundfile", m)
        self.assertIn("run the installer again", m)
        self.assertIn(_DISCORD, m)
        self.assertIn(str(self.logs), m)

    def test_crash_with_unknown_cause_invents_nothing(self):
        self._write_log("backend.log", "Traceback...\nRuntimeError: kaboom\n")
        m = L.service_failure("backend", "stopped while Amadeus was starting it")
        self.assertIn("The app stopped while Amadeus was starting it.", m)
        low = m.lower()
        for word in ("incomplete", "package", "port", "driver", "not enough"):
            self.assertNotIn(word, low)
        self.assertIn(_DISCORD, m)
        self.assertIn(str(self.logs), m)

    def test_missing_log_gives_no_reason(self):
        m = L.service_failure("cm-sidecar", "did not become ready after 600 seconds")
        self.assertIn("Her memory did not become ready after 600 seconds.", m)
        self.assertNotIn("incomplete", m.lower())
        self.assertIn(_DISCORD, m)
        self.assertIn(str(self.logs), m)


class WaitForPortTests(unittest.TestCase):
    """The real wait, with a stand-in process: the raised message is the
    clear one (plain words + known cause from the part's log + help line)."""

    PORT = 19880

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_wait_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.logs = self.tmp / "logs"
        self.logs.mkdir()
        self._orig_logs = L.LOG_DIR
        L.LOG_DIR = self.logs
        self.addCleanup(setattr, L, "LOG_DIR", self._orig_logs)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try:
                p.kill()
            except OSError:
                pass
            try:
                p.wait(timeout=5)
            except Exception:
                pass
        super().tearDown()

    def _spawn(self, code):
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        p = subprocess.Popen([sys.executable, "-c", code], **kwargs)
        self.procs.append(p)
        return p

    def test_crash_reports_the_part_and_the_known_cause(self):
        (self.logs / "gptsovits.log").write_text(
            "ModuleNotFoundError: No module named 'soundfile'\n", encoding="utf-8")
        p = self._spawn("import sys; sys.exit(7)")
        with self.assertRaises(RuntimeError) as ctx:
            L.wait_for_port("GPT-SoVITS", self.PORT, p, timeout=15)
        msg = str(ctx.exception)
        self.assertIn("Her voice stopped while Amadeus was starting it.", msg)
        self.assertIn("soundfile", msg)
        self.assertIn("run the installer again", msg)
        self.assertIn(_DISCORD, msg)
        self.assertIn(str(self.logs), msg)
        self.assertNotIn("not enough", msg.lower())

    def test_timeout_reports_the_part_without_a_guessed_reason(self):
        p = self._spawn("import time; time.sleep(30)")
        with self.assertRaises(TimeoutError) as ctx:
            L.wait_for_port("cm-sidecar", self.PORT + 1, p, timeout=1)
        msg = str(ctx.exception)
        self.assertIn("Her memory did not become ready after 1 seconds.", msg)
        self.assertIn(_DISCORD, msg)
        self.assertIn(str(self.logs), msg)
        self.assertNotIn("incomplete", msg.lower())
        self.assertNotIn("not enough", msg.lower())


class VoicePreflightTests(unittest.TestCase):
    """voice_env_check(): the step-1 message before her voice even starts
    (its conda call mocked - nothing is really run)."""

    def setUp(self):
        self.env_p = mock.patch.object(L, "voice_env_name", return_value="GPTSoVITS")
        self.env_p.start()
        self.addCleanup(self.env_p.stop)

    def _check(self, run_mock):
        with mock.patch("subprocess.run", run_mock):
            return L.voice_env_check("C:/miniconda3/conda.exe")

    def test_missing_package_is_said_at_once(self):
        issue = self._check(mock.Mock(return_value=_fixture(stdout="MISSING soundfile\n")))
        self.assertIn("the package 'soundfile' is missing", issue)
        self.assertIn("run the installer again", issue)

    def test_missing_packages_are_named_together(self):
        issue = self._check(mock.Mock(return_value=_fixture(
            stdout="MISSING torch torchaudio soundfile\n")))
        self.assertIn("the packages 'torch' and 'torchaudio' and 'soundfile' are missing",
                      issue)

    def test_clean_toolbox_says_nothing(self):
        self.assertEqual(self._check(mock.Mock(return_value=_fixture(stdout="OK\n"))), "")

    def test_missing_conda_environment_is_named(self):
        issue = self._check(mock.Mock(return_value=_fixture(
            returncode=1, stderr="EnvironmentLocationNotFound: Not a conda environment: "
                                 "GPTSoVITS\n")))
        self.assertIn("GPTSoVITS", issue)
        self.assertIn("not set up", issue)
        self.assertIn("run the installer again", issue)

    def test_silent_failure_never_blocks_the_start(self):
        for run_mock in (mock.Mock(return_value=_fixture(returncode=1, stderr="weird")),
                         mock.Mock(side_effect=subprocess.SubprocessError("timeout"))):
            with self.subTest(run=run_mock):
                self.assertEqual(self._check(run_mock), "")

    def test_it_asks_the_voice_environment_with_a_timeout(self):
        run = mock.Mock(return_value=_fixture(stdout="OK\n"))
        self._check(run)
        run.assert_called_once()
        command = run.call_args[0][0]
        self.assertIn("run", command)
        self.assertIn("-n", command)
        self.assertIn("GPTSoVITS", command)
        self.assertEqual(run.call_args[1].get("timeout"), 30)


class GraphicsMemoryLineTests(unittest.TestCase):
    """The launcher log's graphics-memory line: a card whose memory cannot
    be read says so (the old log read 'None GB free')."""

    def _ask(self, read_free, answers=()):
        notes, says = [], []
        stdin = type("S", (), {"isatty": lambda self: True})()
        with mock.patch.object(L, "note", side_effect=notes.append), \
                mock.patch.object(L, "say", side_effect=says.append), \
                mock.patch.object(L.sys, "stdin", stdin):
            result = L.ask_about_graphics_memory(
                9.6, read_free=read_free,
                ask=lambda prompt: next(iter(answers), "s"))
        return result, "\n".join(notes), "\n".join(says)

    def test_unreadable_card_says_so(self):
        result, notes, _ = self._ask(lambda: None)
        self.assertTrue(result)
        self.assertIn("could not be read on this card", notes)
        self.assertIn("her model needs about 9.6 GB", notes)
        self.assertNotIn("None GB", notes)

    def test_readable_card_keeps_its_line(self):
        result, notes, _ = self._ask(lambda: 24)
        self.assertTrue(result)
        self.assertIn("24 GB free, her model needs about 9.6 GB", notes)

    def test_card_that_becomes_unreadable_while_waiting(self):
        frees = iter([8.0, None])
        result, _, says = self._ask(lambda: next(frees), answers=("",))
        self.assertTrue(result)
        self.assertIn("The card has room now - starting her model.", says)
        self.assertNotIn("None GB", says)


class BuiltinServerFooterTests(unittest.TestCase):
    """Her built-in model's failure messages (backend/builtin_server.py):
    they end with the help line, and the 'often not enough memory' words
    only when her log shows a memory shortage."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_footer_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prev = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.prev)
        for n in ("builtin_server", "builtin_llm", "memory", "timing"):
            sys.modules.pop(n, None)
        self.s = importlib.import_module("builtin_server")
        self.s.LOG_FILE = self.tmp / "logs" / "llm.log"
        self.s.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, text):
        self.s.LOG_FILE.write_text(text, encoding="utf-8")

    def test_driver_message_ends_with_the_help_line(self):
        # Her whole logs folder is the thing to send: named a folder.
        msg = self.s.driver_error_message()
        self.assertIn(_DISCORD, msg)
        self.assertIn(f"and send your logs folder: {self.tmp / 'logs'}.", msg)
        self.assertNotIn("send the file", msg)

    def test_memory_words_need_log_evidence(self):
        self._log("fake llama-server starting\n")
        self.assertEqual(self.s._memory_shortage(), "")
        self._log("fake llama-server starting\nerror: CUDA error: out of memory\n")
        self.assertIn("not enough", self.s._memory_shortage())


if __name__ == "__main__":
    unittest.main()

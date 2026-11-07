# -*- coding: utf-8 -*-
"""The Mac installer's built-in-model screens and downloads, run for real.

The same checks as tests/test_installer_llm.py does for Windows, with the
Mac installer (scripts/install_macos.sh) run by bash - Git Bash on Windows,
the system bash elsewhere. Nothing is installed and nothing leaves this PC:
  1. it is written for the bash every Mac has (3.2): no newer-only features;
  2. the A/B screens, through the installer's --llm-screens-only test mode
     (answers typed in automatically; pretend Macs; a temp folder): choice +
     settings files, re-run "keep it?", switching to B, Intel Macs and Macs
     with too little memory, what the user changed in the app survives;
  3. the checked download (get_verified_file, taken out of the installer)
     against a tiny web server on 127.0.0.1: fresh download, re-run skip,
     resuming, a dropped connection, a damaged file;
  4. llama.cpp's download unpacked and its start test (a stand-in program),
     an unsafe archive refused, a program that will not start;
  5. a whole option-A model step: background download, then every file
     checked and the settings file written, as the app reads it.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_MAC = Path(__file__).resolve().parents[2] / "scripts" / "install_macos.sh"


def _find_bash():
    git = shutil.which("git")
    if git and os.name == "nt":
        # git.exe is in Git\cmd (or Git\mingw64in): bash.exe is in Gitin.
        for up in (1, 2):
            candidate = Path(git).resolve().parents[up] / "bin" / "bash.exe"
            if candidate.exists():
                return str(candidate)
        return None
    return shutil.which("bash")


_BASH = _find_bash()


def _posix(path) -> str:
    """A path bash (and GNU tar in Git Bash) understands: C:\\x -> /c/x."""
    p = str(path)
    if os.name == "nt" and re.match(r"^[A-Za-z]:", p):
        p = "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def _script_text() -> str:
    return _MAC.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _functions() -> str:
    text = _script_text()
    start = text.index("# >>> BUILT-IN MODEL FUNCTIONS")
    end = text.index("# <<< BUILT-IN MODEL FUNCTIONS")
    return text[start:end]


class Bash32Tests(unittest.TestCase):
    """macOS still ships bash 3.2: features of bash 4+ would break there
    (Git Bash, where the other tests run, is bash 5 and would not notice)."""

    def test_no_newer_bash_features(self):
        code = "\n".join(line for line in _script_text().splitlines()
                         if not line.lstrip().startswith("#"))
        for pattern, what in [
            (r"\bdeclare\s+-[aA-Za-z]*A", "associative arrays"),
            (r"\bmapfile\b|\breadarray\b", "mapfile / readarray"),
            (r"\$\{[A-Za-z_][A-Za-z_0-9]*(,,|\^\^|,|\^)\}", "case changes ${x,,}"),
            (r"&>>", "&>>"),
            (r"\|&", "|&"),
            (r"\b(local|declare)\s+-n\b", "name references"),
            (r"\$\{[A-Za-z_][A-Za-z_0-9]*@[QEPAa]\}", "${x@Q}"),
            (r"\bwait\s+-n\b", "wait -n"),
            (r"\bcoproc\b", "coproc"),
        ]:
            with self.subTest(feature=what):
                self.assertIsNone(re.search(pattern, code), what)

    def test_functions_block_is_marked(self):
        block = _functions()
        for name in ("mac_choose", "get_verified_file", "install_llama_build",
                     "write_local_llm_config", "install_local_llm"):
            self.assertIn(name + "()", block)


@unittest.skipUnless(_BASH, "bash not available")
class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_mac_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = self.tmp / "install_macos.sh"
        self.script.write_bytes(_script_text().encode("utf-8"))
        self.inst = self.tmp / "inst"


class ScreenTests(_Base):
    def screens(self, answers, ram=16, chip="apple", extra=()):
        return subprocess.run(
            [_BASH, str(self.script), _posix(self.inst), "--llm-screens-only",
             "--simulate-chip", chip, "--simulate-ram-gb", str(ram), *extra],
            input="\n".join(answers) + "\n", capture_output=True, text=True, timeout=120)

    def choice(self):
        return json.loads((self.inst / "models" / "local_llm_choice.json").read_text(encoding="utf-8"))

    def settings_path(self):
        return self.inst / "Amadeus-Project" / "backend" / "data" / "local_llm.json"

    def settings(self):
        return json.loads(self.settings_path().read_text(encoding="utf-8"))

    def test_option_a_writes_choice_and_settings(self):
        out = self.screens(["A", "Y"], ram=16)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("STEP 0: How should Amadeus think?", out.stdout)
        self.assertIn("Memory        : 16 GB", out.stdout)
        c = self.choice()
        self.assertEqual((c["mode"], c["model"], c["tier"], c["build"], c["voice_device"]),
                         ("builtin", "12B-Q3", "mac-16", "metal-arm64", "mps"))
        s = self.settings()
        self.assertEqual((s["mode"], s["model"], s["tier"], s["context"], s["kv_cache"]),
                         ("builtin", "12B-Q3", "mac-16", 16384, "q4_0"))
        self.assertEqual(s["model_label"], "Gemma 4 12B (3-bit)")
        self.assertEqual((s["port"], s["gpu_layers"], s["history_default"]), (8091, 999, 6000))
        self.assertFalse(s["model_ok"])                 # --llm-screens-only downloads nothing
        self.assertFalse(s["server_ok"])
        self.assertTrue(s["model_file"].endswith("/models/gemma-4-12b-it-Q3_K_M.gguf"))
        self.assertTrue(s["simulated"])
        self.assertEqual(s["installer"], "mac-v1.6")
        # The log holds the whole conversation.
        self.assertIn("(answer: Y)", (self.inst / "install_macos.log").read_text(encoding="utf-8"))

    def test_bigger_mac_gets_the_qat_model(self):
        self.screens(["A", "Y"], ram=36)
        s = self.settings()
        self.assertEqual((s["model"], s["tier"], s["context"], s["kv_cache"]),
                         ("12B", "mac-32", 32768, "f16"))
        self.assertEqual(s["model_label"], "Gemma 4 12B (QAT)")

    def test_rerun_keeps_choice_or_switches_to_b(self):
        self.screens(["A", "Y"], ram=24)
        out = self.screens(["Y"], ram=24)
        self.assertIn("Last time you chose: A)", out.stdout)
        self.assertEqual(self.choice()["mode"], "builtin")
        out = self.screens(["N", "B"], ram=24)
        self.assertEqual(self.choice()["mode"], "custom")
        # Option B on an Apple-chip Mac: llama.cpp stays ready for a GGUF of
        # the user's own (here not downloaded: --llm-screens-only).
        s = self.settings()
        self.assertEqual((s["mode"], s["model"], s["tier"]), ("custom", "12B", "mac-24"))

    def test_kept_choice_takes_the_new_tier_settings(self):
        self.screens(["A", "Y"], ram=24)
        self.screens(["Y"], ram=48)                      # same model, a newer / bigger tier
        s = self.settings()
        self.assertEqual((s["tier"], s["context"]), ("mac-48", 49152))
        self.screens(["Y"], ram=16)                      # a different model: the old choice stays
        self.assertEqual(self.choice()["model"], "12B")

    def test_intel_mac_gets_option_b(self):
        out = self.screens(["A", ""], ram=64, chip="intel")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("needs a Mac with an Apple chip", out.stdout)
        self.assertEqual(self.choice()["mode"], "custom")
        self.assertFalse(self.settings_path().exists())

    def test_small_mac_gets_option_b(self):
        out = self.screens(["A", ""], ram=8)
        self.assertIn("at least 16 GB of memory (this Mac has 8 GB)", out.stdout)
        self.assertEqual(self.choice()["mode"], "custom")
        self.assertFalse(self.settings_path().exists())

    def test_option_b_on_an_intel_mac_switches_an_old_built_in_off(self):
        self.screens(["A", "Y"], ram=24)
        self.screens(["N", "B"], ram=24, chip="intel")
        self.assertEqual(self.settings()["mode"], "custom")
        self.assertEqual(self.settings()["model"], "12B")     # the rest kept

    def test_what_the_user_changed_in_the_app_survives(self):
        self.screens(["A", "Y"], ram=24)
        own = self.inst / "models" / "my model's v2 (copy).gguf"   # spaces, ' and ()
        own.write_bytes(b"x")
        s = self.settings()
        s.update({"selected_file": _posix(own), "history_budget": 9000})
        # The app writes it with json.dump(indent=4, ensure_ascii=False).
        self.settings_path().write_text(json.dumps(s, indent=4, ensure_ascii=False), encoding="utf-8")
        self.screens(["Y"], ram=24)
        s = self.settings()
        self.assertEqual(s["selected_file"], _posix(own))
        self.assertEqual(s["history_budget"], 9000)
        own.unlink()                                          # the file is gone: not kept
        self.screens(["Y"], ram=24)
        self.assertNotIn("selected_file", self.settings())
        self.assertEqual(self.settings()["history_budget"], 9000)

    def test_no_answer_stops_instead_of_looping(self):
        out = subprocess.run([_BASH, str(self.script), _posix(self.inst), "--llm-screens-only",
                              "--simulate-chip", "apple", "--simulate-ram-gb", "16"],
                             input="", capture_output=True, text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("No answer was given", out.stdout)

    def test_only_runs_on_a_mac(self):
        if os.uname().sysname == "Darwin" if hasattr(os, "uname") else False:
            self.skipTest("this is a Mac")
        out = subprocess.run([_BASH, str(self.script), _posix(self.inst)],
                             input="", capture_output=True, text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("This installer is for Mac OS", out.stdout)
        self.assertFalse((self.inst / "models").exists())

    def test_bad_test_options_are_refused(self):
        for args in (["--simulate-chip", "arm"], ["--simulate-ram-gb", "lots"], ["--branch", "x;y"],
                     ["--nonsense"]):
            with self.subTest(args=args):
                out = subprocess.run([_BASH, str(self.script), *args, "--hardware-check-only"],
                                     capture_output=True, text=True, timeout=60)
                self.assertEqual(out.returncode, 2, out.stdout)


class _Files:
    """A tiny HTTP server for several files, with Range support and faults."""

    def __init__(self, files):
        self.files = dict(files)
        self.mode = "ok"            # "ok" | "drop_first" | "wrong"
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                name = self.path.lstrip("/").split("?")[0]
                rng = self.headers.get("Range", "")
                server.requests.append((name, rng))
                if name not in server.files:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = server.files[name]
                body = data if server.mode != "wrong" else bytes(len(data))
                start = int(rng.split("=")[1].split("-")[0]) if rng.startswith("bytes=") else 0
                if start >= len(body):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(body)}")
                    self.end_headers()
                    return
                chunk = body[start:]
                self.send_response(206 if start else 200)
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                if server.mode == "drop_first" and len(server.requests) == 1:
                    self.wfile.write(chunk[: len(chunk) // 3])
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(chunk)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _llama_tar(version_exit=0, unsafe=False) -> bytes:
    """A stand-in for llama.cpp's macOS download (a .tar.gz)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, text, mode=0o755):
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        add("build/bin/llama-server",
            f"#!/bin/sh\necho 'version: 11400 (stand-in)'\nexit {version_exit}\n")
        add("build/bin/libllama.dylib", "x", 0o644)
        if unsafe:
            add("../outside.txt", "x", 0o644)
    return buf.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _FunctionBase(_Base):
    """Runs the installer's built-in-model functions with test values."""

    def setUp(self):
        super().setUp()
        (self.tmp / "defs.sh").write_text(_functions(), encoding="utf-8")
        self.log = self.tmp / "install.log"

    def run_bash(self, body, extra_setup=""):
        script = "\n".join([
            "set -uo pipefail",
            f"INSTALL_DIR='{_posix(self.inst)}'",
            f"INSTALL_LOG='{_posix(self.log)}'",
            "PROJ=\"$INSTALL_DIR/Amadeus-Project\"",
            "IS_SILICON=1; SIM_RAM_GB=-1; SIM_CHIP=''; SKIP_LLM_DOWNLOAD=0; INSTALLER_VERSION=v1.6",
            "log() { printf '%s\\n' \"$1\"; printf '%s\\n' \"$1\" >> \"$INSTALL_LOG\"; }",
            "step() { printf 'STEP %s: %s\\n' \"$1\" \"$2\"; }",
            "die() { printf 'DIE %s\\n' \"$1\"; exit 1; }",
            f". '{_posix(self.tmp / 'defs.sh')}'",
            "DOWNLOAD_RETRY_DELAY=0",
            extra_setup,
            body,
        ])
        (self.tmp / "run.sh").write_text(script, encoding="utf-8")
        return subprocess.run([_BASH, str(self.tmp / "run.sh")], capture_output=True, text=True,
                              timeout=180)


class JsonTests(_FunctionBase):
    def test_strings_are_escaped_and_read_back(self):
        tricky = r'C:\odd "name"\x.gguf'
        cfg = _posix(self.tmp / "t.json")
        body = "\n".join([
            "v='" + tricky + "'",
            "{ printf '{\\n    \"a\": %s,\\n' \"$(json_str \"$v\")\"; printf '    \"n\": 5\\n}\\n'; } > '" + cfg + "'",
            "printf 'GOT=%s|\\n' \"$(json_get '" + cfg + "' a)\"",
            "printf 'NUM=%s|\\n' \"$(json_get '" + cfg + "' n)\"",
            "printf 'NONE=%s|\\n' \"$(json_get '" + cfg + "' missing)\"",
        ])
        out = self.run_bash(body)
        self.assertEqual(json.loads((self.tmp / "t.json").read_text(encoding="utf-8"))["a"], tricky,
                         out.stdout + out.stderr)
        self.assertIn("GOT=" + tricky + "|", out.stdout)
        self.assertIn("NUM=5|", out.stdout)
        self.assertIn("NONE=|", out.stdout)


class DownloadTests(_FunctionBase):
    def setUp(self):
        super().setUp()
        self.data = os.urandom(1_000_000)
        self.server = _Files({"model.gguf": self.data})
        self.addCleanup(self.server.close)
        self.dest = self.inst / "models" / "model.gguf"

    def fetch(self, sha=None):
        out = self.run_bash(
            f"if get_verified_file '{self.server.base}/model.gguf' '{_posix(self.dest)}' "
            f"{len(self.data)} {sha or _sha(self.data)} 'test file'; then echo RESULT=OK; "
            "else echo \"RESULT=ERR $VF_ERROR\"; fi")
        lines = [l for l in out.stdout.splitlines() if l.startswith("RESULT=")]
        self.assertTrue(lines, out.stdout + out.stderr)
        return lines[-1], out.stdout

    def test_fresh_download_then_rerun_skips(self):
        result, _ = self.fetch()
        self.assertEqual(result, "RESULT=OK")
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual((self.inst / "models" / "model.gguf.verified").read_text(),
                         f"{_sha(self.data)} {len(self.data)}")
        asked = len(self.server.requests)
        result, out = self.fetch()
        self.assertEqual(result, "RESULT=OK")
        self.assertIn("(checked) - skipping", out)
        self.assertEqual(len(self.server.requests), asked)      # nothing downloaded again

    def test_half_finished_file_resumes(self):
        part = Path(str(self.dest) + ".part")
        part.parent.mkdir(parents=True)
        part.write_bytes(self.data[:400_000])
        self.assertEqual(self.fetch()[0], "RESULT=OK")
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual(self.server.requests[0][1], "bytes=400000-")

    def test_dropped_connection_retries_and_resumes(self):
        self.server.mode = "drop_first"
        self.assertEqual(self.fetch()[0], "RESULT=OK")
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_damaged_download_is_deleted_with_clear_error(self):
        self.server.mode = "wrong"
        result, _ = self.fetch()
        self.assertIn("RESULT=ERR test file was downloaded but is damaged", result)
        self.assertFalse(self.dest.exists())
        self.assertFalse(Path(str(self.dest) + ".part").exists())

    def test_unchecked_existing_file_is_checked_not_redownloaded(self):
        self.dest.parent.mkdir(parents=True)
        self.dest.write_bytes(self.data)
        self.assertEqual(self.fetch()[0], "RESULT=OK")
        self.assertEqual(self.server.requests, [])


class LlamaBuildTests(_FunctionBase):
    def install(self, tar_bytes):
        name = "llama-b11400-bin-macos-arm64.tar.gz"
        server = _Files({name: tar_bytes})
        self.addCleanup(server.close)
        setup = (f"MAC_BUILD_FILES='{name} {len(tar_bytes)} {_sha(tar_bytes)}'\n"
                 f"llama_url() {{ printf '%s/%s' '{server.base}' \"$1\"; }}")
        return self.run_bash("if install_llama_build; then echo \"RESULT=OK $LLAMA_EXE\"; "
                             "else echo \"RESULT=ERR $VF_ERROR\"; fi", setup), server

    def result(self, out):
        lines = [l for l in out.stdout.splitlines() if l.startswith("RESULT=")]
        self.assertTrue(lines, out.stdout + out.stderr)
        return lines[-1]

    def test_unpacks_and_checks_it_starts(self):
        out, server = self.install(_llama_tar())
        result = self.result(out)
        self.assertTrue(result.startswith("RESULT=OK "), result)
        build = self.inst / "models" / "llama.cpp" / "b11400-metal-arm64"
        self.assertTrue((build / "build" / "bin" / "llama-server").is_file())
        self.assertTrue((build / "build" / "bin" / "libllama.dylib").is_file())
        self.assertTrue(result.endswith("/build/bin/llama-server"))
        self.assertTrue((build / "install_complete.txt").is_file())
        self.assertIn("version: 11400 (stand-in)", self.log.read_text(encoding="utf-8"))
        # The download itself is cleaned up; a re-run skips everything.
        self.assertEqual(list((self.inst / "models" / "downloads").iterdir()), [])
        asked = len(server.requests)
        out = self.run_bash("install_llama_build && echo \"RESULT=OK $LLAMA_EXE\"")
        self.assertIn("Already have llama.cpp b11400 (metal-arm64) - skipping.", out.stdout)
        self.assertEqual(len(server.requests), asked)

    def test_unsafe_archive_is_refused(self):
        out, _ = self.install(_llama_tar(unsafe=True))
        self.assertIn("RESULT=ERR Unsafe path", self.result(out))
        self.assertFalse((self.tmp / "inst" / "models" / "outside.txt").exists())
        self.assertFalse((self.inst / "models" / "llama.cpp" / "b11400-metal-arm64" / "install_complete.txt").exists())

    def test_program_that_will_not_start(self):
        out, _ = self.install(_llama_tar(version_exit=3))
        self.assertIn("RESULT=ERR llama.cpp (metal-arm64) would not start on this Mac", self.result(out))
        self.assertFalse((self.inst / "models" / "llama.cpp" / "b11400-metal-arm64" / "install_complete.txt").exists())


class OptionAStepTests(_FunctionBase):
    """The whole model step of option A, with small stand-in files."""

    def setUp(self):
        super().setUp()
        self.tar = _llama_tar()
        self.model = os.urandom(700_000)
        self.vision = os.urandom(200_000)
        self.server = _Files({"llama-b11400-bin-macos-arm64.tar.gz": self.tar,
                              "gemma-4-12b-it-Q3_K_M.gguf": self.model,
                              "mmproj-F16.gguf": self.vision})
        self.addCleanup(self.server.close)
        self.setup = "\n".join([
            f"MAC_BUILD_FILES='llama-b11400-bin-macos-arm64.tar.gz {len(self.tar)} {_sha(self.tar)}'",
            "MAC_MODELS='12B-Q3|Gemma 4 12B (3-bit)|unsloth/x|rev|gemma-4-12b-it-Q3_K_M.gguf|"
            f"{len(self.model)}|{_sha(self.model)}|mmproj-F16.gguf|gemma-4-12b-it-mmproj-F16.gguf|"
            f"{len(self.vision)}|{_sha(self.vision)}'",
            f"llama_url() {{ printf '%s/%s' '{self.server.base}' \"$1\"; }}",
            f"hf_url() {{ printf '%s/%s' '{self.server.base}' \"$3\"; }}",
            # Git Bash's ps cannot name a process ("-o comm="); macOS's can.
            "bg_alive() { [ -n \"$1\" ] && kill -0 \"$1\" 2>/dev/null; }",
            "mac_choose 1 16; HW_SIMULATED=0; set_choice_from_check builtin ''",
        ])

    def settings(self):
        return json.loads((self.inst / "Amadeus-Project" / "backend" / "data" / "local_llm.json")
                          .read_text(encoding="utf-8"))

    def test_background_download_then_checked_install(self):
        out = self.run_bash("start_background_downloads\n"
                            "if install_local_llm \"$PROJ\"; then echo RESULT=READY; else echo RESULT=MISSING; fi",
                            self.setup)
        self.assertIn("RESULT=READY", out.stdout, out.stdout + out.stderr)
        self.assertIn("start downloading in the background now", out.stdout)
        models = self.inst / "models"
        self.assertEqual((models / "gemma-4-12b-it-Q3_K_M.gguf").read_bytes(), self.model)
        self.assertEqual((models / "gemma-4-12b-it-mmproj-F16.gguf").read_bytes(), self.vision)
        s = self.settings()
        self.assertTrue(s["model_ok"] and s["server_ok"])
        self.assertEqual(s["mode"], "builtin")
        self.assertTrue(s["server_exe"].endswith("/llama.cpp/b11400-metal-arm64/build/bin/llama-server"))
        self.assertTrue(s["model_file"].endswith("/models/gemma-4-12b-it-Q3_K_M.gguf"))
        self.assertFalse(list(models.glob("downloads/background_*.pid")))
        # The app reads this file as written (its own reader, its own rules).
        self.assertEqual(s["build"], "metal-arm64")
        self.assertEqual(s["voice_device"], "mps")

    def test_failed_downloads_never_stop_the_install(self):
        self.server.files.pop("gemma-4-12b-it-Q3_K_M.gguf")
        self.server.files.pop("mmproj-F16.gguf")
        out = self.run_bash("if install_local_llm \"$PROJ\"; then echo RESULT=READY; else echo RESULT=MISSING; fi",
                            self.setup)
        self.assertIn("RESULT=MISSING", out.stdout, out.stdout + out.stderr)
        self.assertIn("Could not download Gemma 4 12B (3-bit) right now", out.stdout)
        self.assertIn("Vision file not downloaded - she works", out.stdout)
        s = self.settings()
        self.assertFalse(s["model_ok"])
        self.assertTrue(s["server_ok"])                  # llama.cpp itself is fine

    def test_rerun_after_success_downloads_nothing(self):
        self.run_bash("install_local_llm \"$PROJ\"", self.setup)
        asked = len(self.server.requests)
        out = self.run_bash("start_background_downloads\ninstall_local_llm \"$PROJ\" && echo RESULT=READY",
                            self.setup)
        self.assertIn("RESULT=READY", out.stdout)
        self.assertEqual(len(self.server.requests), asked)
        self.assertNotIn("in the background now", out.stdout)


if __name__ == "__main__":
    unittest.main()

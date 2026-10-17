# -*- coding: utf-8 -*-
"""The Windows installer's built-in-model screens and downloads, run for real.

Windows only (skipped elsewhere). Nothing is installed and nothing leaves
this PC:
  1. the A/B screens, through the installer's -LlmScreensOnly test mode
     (answers typed in automatically; pretend hardware; a temp folder):
     choice + settings files, re-run "keep it?", switching back to B, the
     under-8 GB warning, the not-offered cases;
  2. the checked download (Get-VerifiedFile, taken out of the installer)
     against a tiny web server on 127.0.0.1: fresh download, re-run skip,
     resuming a half-finished file, a connection that drops mid-way, and a
     damaged file (deleted, clear error).
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_INSTALLER = Path(__file__).resolve().parents[2] / "scripts" / "install_windows.ps1"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def _ps(args, stdin_text="", timeout=180):
    # A clean module path, as when a user opens Windows PowerShell: when the
    # tests themselves run from PowerShell 7, its module path would break
    # Windows PowerShell's own modules (e.g. Expand-Archive).
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    return subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
                          input=stdin_text, capture_output=True, text=True, timeout=timeout, env=env)


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="amadeus_screens_"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def screens(self, answers, *extra):
        out = _ps(["-File", str(_INSTALLER), "-InstallDir", str(self.dir), "-LlmScreensOnly", *extra],
                  "\n".join(answers) + "\n")
        return out

    def choice(self):
        return json.loads((self.dir / "models" / "local_llm_choice.json").read_text(encoding="utf-8"))

    def settings_path(self):
        return self.dir / "Amadeus-Project" / "backend" / "data" / "local_llm.json"

    def settings(self):
        return json.loads(self.settings_path().read_text(encoding="utf-8"))

    def test_option_a_writes_choice_and_settings(self):
        out = self.screens(["A", "Y"], "-SimulateVramGB", "12", "-SimulateRamGB", "32")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        c = self.choice()
        self.assertEqual((c["mode"], c["model"], c["tier"]), ("builtin", "12B", "8-15"))
        s = self.settings()
        self.assertEqual(s["mode"], "builtin")
        self.assertEqual((s["context"], s["kv_cache"], s["voice_device"], s["port"]),
                         (16384, "q8_0", "cpu", 8091))
        # Screens-only mode downloads nothing, so the model is recorded missing.
        self.assertFalse(s["model_ok"])
        self.assertFalse(s["server_ok"])
        self.assertTrue(s["model_file"].endswith("gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"))
        self.assertTrue(s["simulated"])
        self.assertFalse(s["use_drafter"])
        # The model lives next to the project folder, never inside it.
        self.assertEqual(Path(s["model_file"]).parent, self.dir / "models")

    def test_rerun_keeps_choice_or_switches_to_b(self):
        self.screens(["A", "Y"], "-SimulateVramGB", "12", "-SimulateRamGB", "32")
        out = self.screens(["Y"])
        self.assertIn("Last time you chose: A)", out.stdout)
        self.assertEqual(self.choice()["model"], "12B")
        out = self.screens(["N", "B"])
        self.assertEqual(self.choice()["mode"], "custom")
        self.assertEqual(self.settings()["mode"], "custom")

    def test_option_b_writes_no_settings(self):
        out = self.screens(["B"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.choice()["mode"], "custom")
        self.assertFalse(self.settings_path().exists())

    def test_small_card_warns_and_can_switch_to_b(self):
        out = self.screens(["A", "B"], "-SimulateVramGB", "6", "-SimulateRamGB", "32")
        self.assertIn("not recommended on PCs with less than 8 GB of graphics memory", out.stdout)
        self.assertEqual(self.choice()["mode"], "custom")
        out = self.screens(["N", "A", "A", "Y"], "-SimulateVramGB", "6", "-SimulateRamGB", "32")
        self.assertEqual(self.choice()["model"], "E4B")

    def test_not_offered_cases_explain_and_use_b(self):
        for extra, words in ((("-SimulateVramGB", "0"), "needs a separate graphics card"),
                             (("-SimulateVramGB", "8", "-SimulateRamGB", "8"), "at least 16 GB of normal memory")):
            with self.subTest(extra=extra):
                shutil.rmtree(self.dir / "models", ignore_errors=True)
                out = self.screens(["A", ""], *extra)
                self.assertIn(words, out.stdout)
                self.assertEqual(self.choice()["mode"], "custom")

    def test_no_answer_stops_instead_of_looping(self):
        out = self.screens([])
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("No answer was given", out.stdout)


class _Server:
    """A tiny HTTP file server with Range support and fault modes."""

    def __init__(self, data):
        self.data = data
        self.mode = "ok"          # "ok" | "drop_first" | "wrong"
        self.requests = []        # Range header of each request ("" = none)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                rng = self.headers.get("Range", "")
                server.requests.append(rng)
                body = server.data if server.mode != "wrong" else bytes(len(server.data))
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
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if server.mode == "drop_first" and len(server.requests) == 1:
                    self.wfile.write(chunk[: len(chunk) // 3])
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(chunk)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/model.gguf"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


_EXTRACT = r"""
param($Installer, $Out)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
$names = @('Log', 'Get-Sha256', 'Get-VerifiedFile', 'Expand-ZipFile')
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $true)
if (@($defs).Count -ne $names.Count) { throw ('expected ' + $names.Count + ' functions, found ' + @($defs).Count) }
Set-Content -Path $Out -Value ((@($defs) | ForEach-Object { $_.Extent.Text }) -join "`n`n") -Encoding utf8
"""

_RUN = r"""
param($Defs, $Url, $Dest, [double]$Bytes, $Sha, $LogFile)
$LogPath = $LogFile
$DownloadRetryDelaySeconds = 0
. $Defs
try { Get-VerifiedFile $Url $Dest $Bytes $Sha "test file"; "RESULT=OK" }
catch { "RESULT=ERR " + $_.Exception.Message }
"""


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_dl_"))
        (self.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        (self.tmp / "run.ps1").write_text(_RUN, encoding="utf-8")
        out = _ps(["-File", str(self.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(self.tmp / "defs.ps1")])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.data = os.urandom(1_000_000)
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.server = _Server(self.data)
        self.dest = self.tmp / "models" / "model.gguf"

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fetch(self, sha=None):
        out = _ps(["-File", str(self.tmp / "run.ps1"), "-Defs", str(self.tmp / "defs.ps1"),
                   "-Url", self.server.url, "-Dest", str(self.dest), "-Bytes", str(len(self.data)),
                   "-Sha", sha or self.sha, "-LogFile", str(self.tmp / "log.txt")])
        lines = [l for l in out.stdout.splitlines() if l.startswith("RESULT=")]
        self.assertTrue(lines, out.stdout + out.stderr)
        return lines[-1], out.stdout

    def test_fresh_download_then_rerun_skips(self):
        result, _ = self.fetch()
        self.assertEqual(result, "RESULT=OK")
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertTrue(Path(str(self.dest) + ".verified").exists())
        self.assertFalse(Path(str(self.dest) + ".part").exists())
        before = len(self.server.requests)
        result, stdout = self.fetch()
        self.assertEqual(result, "RESULT=OK")
        self.assertIn("Already have test file", stdout)
        self.assertEqual(len(self.server.requests), before)   # no new request

    def test_half_finished_file_resumes(self):
        self.dest.parent.mkdir(parents=True)
        Path(str(self.dest) + ".part").write_bytes(self.data[:400_000])
        result, _ = self.fetch()
        self.assertEqual(result, "RESULT=OK")
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual(self.server.requests[0], "bytes=400000-")

    def test_dropped_connection_retries_and_resumes(self):
        self.server.mode = "drop_first"
        result, _ = self.fetch()
        self.assertEqual(result, "RESULT=OK")
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertGreaterEqual(len(self.server.requests), 2)
        self.assertTrue(self.server.requests[-1].startswith("bytes="))

    def test_damaged_download_is_deleted_with_clear_error(self):
        self.server.mode = "wrong"
        result, _ = self.fetch()
        self.assertTrue(result.startswith("RESULT=ERR"))
        self.assertIn("damaged", result)
        self.assertFalse(self.dest.exists())
        self.assertFalse(Path(str(self.dest) + ".part").exists())

    def test_unchecked_existing_file_is_checked_not_redownloaded(self):
        self.dest.parent.mkdir(parents=True)
        self.dest.write_bytes(self.data)
        result, _ = self.fetch()
        self.assertEqual(result, "RESULT=OK")
        self.assertEqual(self.server.requests, [])


_UNZIP = r"""
param($Defs, $Zip, $Dir, $LogFile)
$LogPath = $LogFile
. $Defs
try { Expand-ZipFile $Zip $Dir; "RESULT=OK" }
catch { "RESULT=ERR " + $_.Exception.Message }
"""


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class UnzipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_zip_"))
        (self.tmp / "extract.ps1").write_text(_EXTRACT, encoding="utf-8")
        (self.tmp / "unzip.ps1").write_text(_UNZIP, encoding="utf-8")
        out = _ps(["-File", str(self.tmp / "extract.ps1"), "-Installer", str(_INSTALLER),
                   "-Out", str(self.tmp / "defs.ps1")])
        self.assertEqual(out.returncode, 0, out.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def unzip(self, entries, into):
        import zipfile
        z = self.tmp / "t.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for name, data in entries:
                zf.writestr(name, data)
        out = _ps(["-File", str(self.tmp / "unzip.ps1"), "-Defs", str(self.tmp / "defs.ps1"),
                   "-Zip", str(z), "-Dir", str(into), "-LogFile", str(self.tmp / "log.txt")])
        return [l for l in out.stdout.splitlines() if l.startswith("RESULT=")][-1]

    def test_unpacks_files_and_folders_overwriting(self):
        d = self.tmp / "out"
        d.mkdir()
        (d / "llama-server.exe").write_bytes(b"old")
        res = self.unzip([("llama-server.exe", b"new"), ("lib/ggml.dll", b"dll")], d)
        self.assertEqual(res, "RESULT=OK")
        self.assertEqual((d / "llama-server.exe").read_bytes(), b"new")
        self.assertEqual((d / "lib" / "ggml.dll").read_bytes(), b"dll")

    def test_refuses_entries_outside_the_folder(self):
        d = self.tmp / "out"
        res = self.unzip([("../evil.txt", b"x")], d)
        self.assertTrue(res.startswith("RESULT=ERR"))
        self.assertFalse((self.tmp / "evil.txt").exists())


if __name__ == "__main__":
    unittest.main()

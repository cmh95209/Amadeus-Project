# -*- coding: utf-8 -*-
"""Offline checks for her built-in model's server, run by the app
(backend/builtin_server.py), and the Settings controls behind it.

2026-10-18: users can unload her model (to free graphics memory),
load it again, and pick another GGUF from the models folder; the launcher
asks first when the graphics card is too full, and can start without her
model. Covers:
  1. the models folder list: chat models only, no speed-up / vision files;
  2. Settings can only point at files directly inside the models folder;
  3. load -> loading -> loaded with a stand-in server (a tiny Python HTTP
     server playing llama-server's /health), and the file that loaded is
     remembered; unload stops it;
  4. a server that dies while loading -> "failed" with the log's last lines;
  5. no load when built-in mode is off or llama.cpp is missing;
  6. the routes: /builtinModel, /builtinModel/load, /builtinModel/unload;
  7. the launcher: one fixed model name shared with the backend, the
     memory estimate for a bigger own file, and the graphics-memory question.
"""
import importlib
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND.parent / "scripts"
for _p in (BACKEND, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from test_builtin_llm import _config, _write  # noqa: E402
from test_ceremony import _FreshAppTests  # noqa: E402

# A stand-in for llama-server: answers /health with 503 ("loading") for a
# moment, then 200; prints a line to its log. argv: port, delay, exit_code
# (exit_code >= 0 = die at once with that code after printing).
_FAKE_SERVER = r'''
import sys, time, http.server
port, delay, die = int(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
print("fake llama-server starting", flush=True)
if die >= 0:
    print("error: out of graphics memory", flush=True)
    sys.exit(die)
start = time.time()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if time.time() - start >= delay else 503)
        self.end_headers()
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_server_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prev = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.prev)
        for n in ("builtin_server", "builtin_llm", "memory"):
            sys.modules.pop(n, None)
        self.b = importlib.import_module("builtin_llm")
        self.s = importlib.import_module("builtin_server")
        self.addCleanup(self.s.unload)
        self.s.LOG_FILE = self.tmp / "logs" / "llm.log"
        self.port = _free_port()
        self.cfg_path = self.tmp / "data" / "local_llm.json"
        _write(self.cfg_path, _config(self.tmp, port=self.port))
        self.fake = self.tmp / "fake_llama_server.py"
        self.fake.write_text(_FAKE_SERVER, encoding="utf-8")
        self.models = self.tmp / "models"

    def use_fake(self, delay=0.5, die=-1):
        cmd = lambda cfg, model: [sys.executable, str(self.fake), str(self.port), str(delay), str(die)]
        p = patch.object(self.s, "server_command", side_effect=cmd)
        p.start()
        self.addCleanup(p.stop)

    def wait_for(self, state, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.s.status()
            if st["state"] == state:
                return st
            time.sleep(0.2)
        self.fail(f"state never became {state}: {self.s.status()}")


class ModelListTests(_ServerBase):
    def test_list_has_chat_models_only(self):
        for name in ("my-own-model.gguf", "mtp-gemma-4-12B-it.gguf",
                     "mmproj-gemma-4.gguf", "half.gguf.part", "notes.txt"):
            (self.models / name).write_bytes(b"x")
        names = [m["name"] for m in self.b.list_models()]
        self.assertEqual(names, ["gemma-4-12B-it-qat-UD-Q4_K_XL.gguf", "my-own-model.gguf"])

    def test_only_files_inside_the_models_folder(self):
        (self.models / "my-own-model.gguf").write_bytes(b"x")
        outside = self.tmp / "elsewhere.gguf"
        outside.write_bytes(b"x")
        self.assertEqual(self.b.resolve_model_file("my-own-model.gguf"),
                         (self.models / "my-own-model.gguf").resolve())
        for bad in (str(outside), "../elsewhere.gguf", "mtp-gemma-4-12B-it.gguf",
                    "missing.gguf", ""):
            with self.subTest(file=bad):
                self.assertIsNone(self.b.resolve_model_file(bad))

    def test_selected_file_and_label(self):
        own = self.models / "my-own-model.gguf"
        own.write_bytes(b"x")
        self.assertEqual(self.b.model_label(), "Gemma 4 12B (QAT)")
        self.b.save_selected_file(str(own))
        self.assertEqual(self.b.selected_file(), str(own))
        self.assertEqual(self.b.model_label(), "my-own-model")
        self.assertFalse(self.b.model_missing())
        own.unlink()   # gone -> back to the installer's model
        self.assertEqual(self.b.selected_file(), self.b.load_config()["model_file"])


class LoadTests(_ServerBase):
    def test_load_then_unload(self):
        self.use_fake(delay=0.6)
        own = self.models / "my-own-model.gguf"
        own.write_bytes(b"x")
        st = self.s.load("my-own-model.gguf")
        self.assertEqual(st["state"], "loading")
        st = self.wait_for("loaded")
        self.assertEqual(st["file"], str(own.resolve()))
        self.assertEqual(self.b.selected_file(), str(own.resolve()))   # remembered
        proc = self.s._proc
        self.assertEqual(self.s.unload()["state"], "unloaded")
        self.assertIsNotNone(proc.poll())   # really stopped

    def test_crash_while_loading_is_reported(self):
        self.use_fake(die=3)
        self.s.load()
        st = self.wait_for("failed")
        self.assertIn("exit 3", st["error"])
        self.assertIn("out of graphics memory", st["error"])   # the log's last lines
        self.assertEqual(self.b.load_config().get("selected_file"), None)

    def test_no_load_when_off_or_missing(self):
        _write(self.cfg_path, _config(self.tmp, mode="custom", port=self.port))
        with self.assertRaises(LookupError):
            self.s.load()
        _write(self.cfg_path, _config(self.tmp, server_ok=False, port=self.port, n=1))
        with self.assertRaises(LookupError):
            self.s.load()
        _write(self.cfg_path, _config(self.tmp, port=self.port, n=22))
        with self.assertRaises(ValueError):
            self.s.load("../outside.gguf")

    def test_start_at_launch_respects_the_launcher(self):
        with patch.object(self.s, "load") as load, \
                patch.dict(os.environ, {"AMADEUS_LOAD_MODEL": "0"}):
            self.s.start_at_launch()
            load.assert_not_called()
        with patch.object(self.s, "load") as load, \
                patch.dict(os.environ, {"AMADEUS_LOAD_MODEL": "1"}):
            self.s.start_at_launch()
            load.assert_called_once()


class RouteTests(_FreshAppTests):
    def setUp(self):
        super().setUp()
        _write(Path("data") / "local_llm.json", _config(Path(self.directory.name)))

    def test_status_and_controls(self):
        server = self.api.builtin_server
        info = self.client.get("/builtinModel").json
        self.assertEqual(info["server"]["state"], "unloaded")
        self.assertEqual([f["name"] for f in info["files"]], ["gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"])
        self.assertTrue(info["selected"].endswith("gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"))
        fake = {"state": "loading", "file": "x", "error": "", "since": 0}
        with patch.object(server, "load", return_value=fake) as load:
            res = self.client.post("/builtinModel/load", json={"file": "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"})
            self.assertEqual(res.status_code, 200)
            load.assert_called_once_with("gemma-4-12B-it-qat-UD-Q4_K_XL.gguf")
        res = self.client.post("/builtinModel/load", json={"file": "../../evil.gguf"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.post("/builtinModel/load", json={"file": 5}).status_code, 400)
        self.assertEqual(self.client.post("/builtinModel/unload").json["server"]["state"], "unloaded")

    def test_unloaded_model_message(self):
        import openai
        exc = openai.APIConnectionError(request=__import__("httpx").Request("GET", "http://x"))
        self.assertIn("not loaded", self.api._llm_error_message(exc))


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_server_test",
                                                  SCRIPTS / "launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherModelTests(unittest.TestCase):
    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_launch_model_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_one_name_shared_with_the_backend(self):
        import builtin_llm
        self.assertEqual(self.launcher.BUILTIN_ALIAS, builtin_llm.ALIAS)

    def test_memory_need_follows_a_bigger_own_file(self):
        cfg = _config(self.tmp, vram_need_gb=11.0)
        Path(cfg["model_file"]).write_bytes(b"x" * 1024)
        self.assertEqual(self.launcher.builtin_vram_need_gb(cfg), 11.0)
        own = self.tmp / "models" / "big.gguf"
        with open(own, "wb") as f:
            f.truncate(1024 + 2 * 1024 ** 3)   # 2 GB bigger (sparse)
        cfg["selected_file"] = str(own)
        self.assertEqual(self.launcher.builtin_vram_need_gb(cfg), 13.0)
        self.assertIsNone(self.launcher.builtin_vram_need_gb(_config(self.tmp)))   # old file: no estimate

    def test_files_ready_with_an_own_file(self):
        cfg = _config(self.tmp, model_ok=False)
        own = self.tmp / "models" / "mine.gguf"
        own.write_bytes(b"x")
        self.assertFalse(self.launcher.builtin_files_ready(cfg)[0])
        cfg["selected_file"] = str(own)
        self.assertEqual(self.launcher.builtin_files_ready(cfg), (True, ""))

    def ask(self, free_values, answers, tty=True):
        frees = iter(free_values)
        given = iter(answers)
        stdin = type("S", (), {"isatty": lambda self: tty})()
        with patch.object(self.launcher.sys, "stdin", stdin), \
                patch.object(self.launcher, "say"):
            return self.launcher.ask_about_graphics_memory(
                11.0, read_free=lambda: next(frees), ask=lambda prompt: next(given))

    def test_free_card_asks_nothing(self):
        self.assertTrue(self.ask([20.0], []))
        self.assertTrue(self.ask([None], []))   # not readable (AMD / Intel)

    def test_full_card_asks(self):
        self.assertFalse(self.ask([4.0], ["N"]))
        self.assertTrue(self.ask([4.0], ["s"]))
        self.assertTrue(self.ask([4.0, 5.0, 18.0], ["", ""]))   # Enter until it fits
        self.assertTrue(self.ask([4.0], [], tty=False))         # nobody to ask


if __name__ == "__main__":
    unittest.main()

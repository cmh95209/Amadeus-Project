# -*- coding: utf-8 -*-
"""Offline checks for built-in-model mode (installer option A).

No server, no model, no network. Covers:
  1. backend/builtin_llm.py: the settings file (absent / custom / builtin /
     unreadable / byte-order mark / changed on disk), the server address,
     the served model name, the "model missing" check;
  2. the thought filter (inline Gemma 4 thought blocks never reach her text);
  3. llm.py in built-in mode: only port 8091 - even when other local ports
     answer - the served model name, thinking off, the filtering client;
     custom mode unchanged;
  4. the app (real Flask routes, temp data dir): no API-key gate, the
     model name, /testConnection + readiness use the built-in server, a
     missing model is reported;
  5. the launcher: llama-server's command line per tier, the drafter
     switch, files-ready reasons, the pretend-VRAM tier, the sidecar and
     voice settings - and the same model name as the backend;
  6. start_gptsovits: the processor-only copy of the voice settings.
"""
import ast
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND.parent / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402


def _config(base: Path, **over) -> dict:
    """A complete built-in settings file whose files really exist."""
    models = base / "models"
    models.mkdir(parents=True, exist_ok=True)
    exe = models / "llama.cpp" / "llama-server.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    model = models / "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"
    model.write_bytes(b"")
    cfg = {
        "mode": "builtin", "model": "12B", "model_label": "Gemma 4 12B (QAT)",
        "model_file": str(model), "model_ok": True,
        "server_exe": str(exe), "server_ok": True,
        "llama_release": "b11400", "build": "cuda-13.4", "tier": "8-11",
        "context": 16384, "kv_cache": "q8_0", "voice_device": "cpu",
        "gpu_layers": 999, "port": 8091, "simulated": False,
    }
    cfg.update(over)
    return cfg


def _write(path: Path, cfg, bom=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cfg) if not isinstance(cfg, str) else cfg
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


class _TempBackendDir(unittest.TestCase):
    """chdir into a temp folder (memory.DATA_DIR is relative) and import
    fresh copies of the modules under test."""
    modules = ("builtin_llm", "memory")

    def setUp(self):
        if str(BACKEND) not in sys.path:
            sys.path.insert(0, str(BACKEND))
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_builtin_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prev = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.prev)
        saved = {n: sys.modules.get(n) for n in self.modules}
        for n in self.modules:
            sys.modules.pop(n, None)

        def restore():
            for n, m in saved.items():
                if m is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = m
        self.addCleanup(restore)
        self.mods = {n: importlib.import_module(n) for n in self.modules}
        self.b = self.mods["builtin_llm"]
        self.cfg_path = self.tmp / "data" / "local_llm.json"


class ConfigTests(_TempBackendDir):
    def test_no_file_means_off(self):
        self.assertFalse(self.b.active())
        self.assertIsNone(self.b.load_config())

    def test_custom_mode_means_off(self):
        _write(self.cfg_path, _config(self.tmp, mode="custom"))
        self.assertFalse(self.b.active())

    def test_builtin_mode(self):
        _write(self.cfg_path, _config(self.tmp))
        self.assertTrue(self.b.active())
        self.assertEqual(self.b.server_url(), "http://127.0.0.1:8091/v1")
        self.assertEqual(self.b.model_name(), "gemma-4-12B-it-qat-UD-Q4_K_XL")
        self.assertFalse(self.b.model_missing())

    def test_unreadable_file_means_off(self):
        _write(self.cfg_path, "{not json")
        self.assertFalse(self.b.active())
        _write(self.cfg_path, "[1, 2]")
        self.assertFalse(self.b.active())

    def test_byte_order_mark_is_tolerated(self):
        _write(self.cfg_path, _config(self.tmp), bom=True)
        self.assertTrue(self.b.active())

    def test_change_on_disk_is_picked_up(self):
        _write(self.cfg_path, _config(self.tmp))
        self.assertTrue(self.b.active())
        _write(self.cfg_path, _config(self.tmp, mode="custom", padding="x" * 10))
        self.assertFalse(self.b.active())

    def test_missing_model_or_program(self):
        cases = [
            {"model_ok": False},
            {"server_ok": False},
            {"model_file": str(self.tmp / "gone.gguf")},
            {"server_exe": str(self.tmp / "gone.exe")},
        ]
        for over in cases:
            with self.subTest(**over):
                _write(self.cfg_path, _config(self.tmp, **over, n=len(str(over))))
                self.assertTrue(self.b.active())
                self.assertTrue(self.b.model_missing())


class ThoughtFilterTests(_TempBackendDir):
    def test_closed_block_is_removed(self):
        text = "<|channel>thought\nThe user greets me.<channel|>おはよう。"
        self.assertEqual(self.b.strip_thoughts(text), "おはよう。")

    def test_unclosed_block_drops_the_rest(self):
        self.assertEqual(self.b.strip_thoughts("Hello<|channel>thought I should"), "Hello")

    def test_several_blocks(self):
        text = "<|channel>thought a<channel|>A<|channel>thought b<channel|>B"
        self.assertEqual(self.b.strip_thoughts(text), "AB")

    def test_plain_text_is_untouched(self):
        for text in ("  plain reply  ", "", "a < b | c > d", None, 5):
            with self.subTest(text=text):
                self.assertEqual(self.b.strip_thoughts(text), text)


class LlmClientTests(_TempBackendDir):
    modules = ("builtin_llm", "memory", "llm")

    def setUp(self):
        super().setUp()
        self.llm = self.mods["llm"]
        self.llm.reset_llm()

    def test_builtin_never_probes_other_ports(self):
        _write(self.cfg_path, _config(self.tmp))
        with patch.object(self.llm, "_port_alive", return_value=True) as alive:
            self.assertEqual(self.llm._server_url(), "http://127.0.0.1:8091/v1")
            alive.assert_not_called()

    def test_custom_mode_still_probes(self):
        with patch.object(self.llm, "_SERVER_FILE", self.tmp / "no_llm_server.txt"), \
             patch.object(self.llm, "_port_alive", side_effect=lambda h, p, **k: p == 8888):
            self.assertEqual(self.llm._server_url(), "http://localhost:8888/v1")

    def test_builtin_client(self):
        _write(self.cfg_path, _config(self.tmp))
        client = self.llm.get_llm("", "some/cloud-model")
        self.assertIsInstance(client, self.llm._ThoughtFreeChatOpenAI)
        self.assertEqual(client.model_name, "gemma-4-12B-it-qat-UD-Q4_K_XL")
        self.assertEqual(str(client.openai_api_base), "http://127.0.0.1:8091/v1")
        self.assertEqual(client.extra_body["chat_template_kwargs"], {"enable_thinking": False})

    def test_reply_thoughts_are_filtered(self):
        client = self.llm._ThoughtFreeChatOpenAI(model="m", api_key="k",
                                                  base_url="http://127.0.0.1:8091/v1")
        response = {
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant",
                "content": "<|channel>thought\nplan<channel|>こんにちは"}}],
        }
        result = client._create_chat_result(response)
        self.assertEqual(result.generations[0].message.content, "こんにちは")
        self.assertEqual(result.generations[0].text, "こんにちは")


class AppTests(_FreshAppTests):
    def setUp(self):
        super().setUp()
        self.cfg_path = Path("data") / "local_llm.json"
        self.base = Path(self.directory.name)

    def builtin_on(self, **over):
        _write(self.cfg_path, _config(self.base, **over))

    def test_no_key_gate_in_builtin_mode(self):
        self.assertFalse(self.chat.has_api_key())
        self.builtin_on()
        self.assertTrue(self.chat.has_api_key())
        self.assertEqual(self.chat.API_KEY, "")   # no fake key was stored

    def test_model_name_comes_from_builtin(self):
        self.builtin_on()
        self.assertEqual(self.chat.getLLMModel(), "gemma-4-12B-it-qat-UD-Q4_K_XL")

    def test_test_connection_probes_builtin_server(self):
        self.builtin_on()
        seen = []

        def probe(address, api_key="", timeout=5.0):
            seen.append(address)
            return {"reachable": True, "models": ["gemma-4-12B-it-qat-UD-Q4_K_XL"],
                    "error": None, "loading": False}
        with patch.object(self.api.llm, "test_server", probe, create=True):
            data = self.client.get("/testConnection").get_json()
        self.assertEqual(seen, ["http://127.0.0.1:8091/v1"])
        self.assertTrue(data["builtin"])
        self.assertTrue(data["model_found"])
        self.assertFalse(data["builtin_model_missing"])
        with patch.object(self.api.llm, "test_server", probe, create=True):
            self.assertEqual(self.api.model_ready(), {"ready": True, "reason": ""})

    def test_missing_model_is_reported(self):
        self.builtin_on(model_ok=False)
        with patch.object(self.api.llm, "test_server",
                          lambda *a, **k: {"reachable": False, "models": [], "error": "x"},
                          create=True):
            data = self.client.get("/testConnection").get_json()
            self.assertTrue(data["builtin_model_missing"])
            self.assertEqual(self.api.model_ready()["reason"], "built-in model not installed")


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_builtin_test",
                                                  SCRIPTS / "launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.launcher = _load_launcher()
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_launch_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launcher.BUILTIN_CONFIG = self.tmp / "local_llm.json"
        self.launcher.RUNTIME_DIR = self.tmp / ".runtime"

    def test_load_config_only_in_builtin_mode(self):
        self.assertIsNone(self.launcher.load_builtin_config())
        _write(self.launcher.BUILTIN_CONFIG, _config(self.tmp, mode="custom"))
        self.assertIsNone(self.launcher.load_builtin_config())
        _write(self.launcher.BUILTIN_CONFIG, _config(self.tmp), bom=True)
        self.assertEqual(self.launcher.load_builtin_config()["mode"], "builtin")

    def test_command_for_a_small_tier(self):
        cfg = _config(self.tmp)
        cmd = self.launcher.llama_server_command(cfg)
        self.assertEqual(cmd[0], cfg["server_exe"])
        joined = " ".join(cmd)
        for part in ("-m " + cfg["model_file"], "--host 127.0.0.1", "--port 8091",
                     "-c 16384", "-ngl 999", "--jinja", "-fa on -ctk q8_0 -ctv q8_0",
                     "--temp 1.0 --top-p 0.95 --top-k 64",
                     "--alias gemma-4-12B-it-qat-UD-Q4_K_XL"):
            self.assertIn(part, joined)
        # Thinking is set per request (backend + sidecar), not server-wide:
        # b11400 deprecates --chat-template-kwargs, and --reasoning off might
        # refuse Deep thinking's per-request "on".
        self.assertNotIn("--chat-template-kwargs", cmd)
        self.assertNotIn("--reasoning", cmd)
        self.assertNotIn("-md", cmd)

    def test_command_for_a_big_tier_keeps_normal_memory(self):
        cmd = self.launcher.llama_server_command(_config(self.tmp, context=32768, kv_cache="f16"))
        self.assertIn("32768", cmd)
        self.assertNotIn("-ctk", cmd)
        self.assertNotIn("-fa", cmd)

    def test_no_speed_up_drafter(self):
        # Never used (2026-10-18), so never passed - even from an old settings file.
        cfg = _config(self.tmp, use_drafter=True, drafter_ok=True,
                      drafter_file=str(self.tmp / "mtp-gemma-4-12B-it.gguf"))
        Path(cfg["drafter_file"]).write_bytes(b"")
        self.assertNotIn("-md", self.launcher.llama_server_command(cfg))

    def test_files_ready(self):
        cfg = _config(self.tmp)
        self.assertEqual(self.launcher.builtin_files_ready(cfg), (True, ""))
        ok, why = self.launcher.builtin_files_ready(dict(cfg, server_ok=False))
        self.assertFalse(ok)
        self.assertIn("llama.cpp", why)
        ok, why = self.launcher.builtin_files_ready(dict(cfg, model_file=str(self.tmp / "x.gguf")))
        self.assertFalse(ok)
        self.assertIn("x.gguf", why)

    def test_same_model_name_as_backend(self):
        if str(BACKEND) not in sys.path:
            sys.path.insert(0, str(BACKEND))
        import builtin_llm
        cfg = _config(self.tmp)
        self.assertEqual(self.launcher.builtin_model_name(cfg), builtin_llm.model_name(cfg))

    def test_pretend_vram_uses_that_tiers_settings(self):
        cfg = _config(self.tmp, model="26B-A4B", tier="32-plus", context=49152,
                      kv_cache="f16", voice_device="cuda")
        sim = self.launcher.apply_simulated_vram(cfg, 12)
        self.assertEqual((sim["model"], sim["tier"], sim["context"], sim["kv_cache"],
                          sim["voice_device"]), ("12B", "12-15", 24576, "q8_0", "cpu"))
        self.assertTrue(sim["model_ok"])   # the 12B file exists in this models folder
        self.assertTrue(sim["simulated"])
        sim = self.launcher.apply_simulated_vram(cfg, 6)
        self.assertEqual(sim["model"], "E4B")
        self.assertFalse(sim["model_ok"])  # not downloaded -> reported, not started
        self.assertIsNone(self.launcher.apply_simulated_vram(cfg, 0))

    def test_sidecar_follows_builtin_without_touching_user_files(self):
        env = self.launcher.sidecar_builtin_env(_config(self.tmp))
        self.assertEqual(Path(env["CM_SERVER_FILE"]).read_text(encoding="utf-8"),
                         "http://127.0.0.1:8091/v1")
        self.assertEqual(Path(env["CM_MODEL_FILE"]).read_text(encoding="utf-8"),
                         "gemma-4-12B-it-qat-UD-Q4_K_XL")
        self.assertTrue(env["CM_SERVER_FILE"].startswith(str(self.tmp / ".runtime")))

    def test_voice_on_processor_only_when_asked(self):
        self.assertEqual(self.launcher.voice_env(_config(self.tmp)), {"AMADEUS_VOICE_DEVICE": "cpu"})
        self.assertEqual(self.launcher.voice_env(_config(self.tmp, voice_device="cuda")), {})
        self.assertEqual(self.launcher.voice_env(None), {})


class VoiceConfigTests(unittest.TestCase):
    """start_gptsovits imports torch at the top, so only the helper is run."""

    def setUp(self):
        src = (BACKEND / "start_gptsovits.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.FunctionDef) and n.name == "_voice_config_args")
        self.ns = {"os": os, "Path": Path}
        exec(compile(ast.Module([fn], []), "start_gptsovits", "exec"), self.ns)
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_voicecfg_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gpt = self.tmp / "GPT-SoVITS"
        cfg = self.gpt / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        cfg.parent.mkdir(parents=True)
        self.original = "custom:\n  device: cuda\n  is_half: true\n  version: v2\n"
        cfg.write_text(self.original, encoding="utf-8")
        self.cfg = cfg

    def call(self, value):
        with patch.dict(os.environ, {"AMADEUS_VOICE_DEVICE": value} if value is not None else {}, clear=False):
            if value is None:
                os.environ.pop("AMADEUS_VOICE_DEVICE", None)
            return self.ns["_voice_config_args"](self.tmp, self.gpt)

    def test_default_keeps_the_engines_own_settings(self):
        self.assertEqual(self.call(None), [])
        self.assertEqual(self.call("cuda"), [])

    def test_processor_copy(self):
        import yaml
        args = self.call("cpu")
        self.assertEqual(args[0], "-c")
        data = yaml.safe_load(Path(args[1]).read_text(encoding="utf-8"))
        self.assertEqual(data["custom"], {"device": "cpu", "is_half": False, "version": "v2"})
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), self.original)  # untouched
        self.assertEqual(Path(args[1]).parent, self.tmp / ".runtime")


if __name__ == "__main__":
    unittest.main()

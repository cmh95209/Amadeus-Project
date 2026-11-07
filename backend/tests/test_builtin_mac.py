# -*- coding: utf-8 -*-
"""Her built-in model on a Mac with an Apple chip (2026-11-07).

The Mac installer writes the same settings file as the Windows one, with
build "metal-arm64" (llama.cpp's Metal build) and voice_device "mps". On a
Mac the processor and graphics share one memory:
  - her pictures are always read on the chip's graphics side (no card
    memory to measure, no --no-mmproj-offload);
  - the launcher asks nothing about graphics memory at start;
  - messages and Settings say "memory" / "Apple chip", not "graphics card";
  - the launcher's --simulate-vram-gb test option follows the Mac tiers.
Everything runs in a temp folder with stand-in files - no user data.
"""
import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
_LAUNCHER = BACKEND.parent / "scripts" / "launcher.py"
for _p in (BACKEND, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from test_builtin_llm import _config, _write  # noqa: E402
from test_builtin_server import _ServerBase  # noqa: E402

Q3 = "gemma-4-12b-it-Q3_K_M.gguf"
Q3_VISION = "gemma-4-12b-it-mmproj-F16.gguf"


def _mac(base, **over):
    """A settings file as the Mac installer writes it (16 GB Mac)."""
    cfg = _config(base, build="metal-arm64", tier="mac-16", model="12B-Q3",
                  model_label="Gemma 4 12B (3-bit)", context=16384, kv_cache="q4_0",
                  voice_device="mps", vram_need_gb=8.5, history_default=6000)
    exe = base / "models" / "llama.cpp" / "llama-server"
    exe.write_bytes(b"")
    cfg["server_exe"] = str(exe)
    cfg.update(over)
    return cfg


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_mac_test", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacServerTests(_ServerBase):
    def setUp(self):
        super().setUp()
        self.cfg = _mac(self.tmp, port=self.port)
        _write(self.cfg_path, self.cfg)

    def test_shared_memory_follows_the_build(self):
        self.assertTrue(self.b.shared_memory(self.cfg))
        self.assertTrue(self.b.shared_memory())                       # from the settings file
        self.assertFalse(self.b.shared_memory(_config(self.tmp)))     # Windows CUDA
        self.assertFalse(self.b.shared_memory(_config(self.tmp, build="vulkan")))
        self.assertFalse(self.b.shared_memory({}))

    def test_settings_learns_it_is_a_mac(self):
        self.assertTrue(self.b.describe()["shared_memory"])
        _write(self.cfg_path, _config(self.tmp))
        self.assertFalse(self.b.describe()["shared_memory"])

    def test_get_more_models_uses_the_mac_labels(self):
        import json as _json
        table = _json.loads((BACKEND.parent / "scripts" / "local_llm_tiers.json").read_text(encoding="utf-8"))
        self.assertEqual(self.b.MAC_LABELS, table["mac"]["labels"])
        labels = {m["key"]: m["label"] for m in self.b.catalog(self.cfg)}
        self.assertEqual(labels["12B-Q3"], "Gemma 4 12B (3-bit)")
        labels = {m["key"]: m["label"] for m in self.b.catalog(_config(self.tmp, model="12B-Q3"))}
        self.assertEqual(labels["12B-Q3"], "Gemma 4 12B (3-bit, for 8 GB cards)")   # Windows

    def test_pictures_always_on_the_chip(self):
        # Nothing is measured (no NVIDIA tool on a Mac) - even a "full"
        # reading would not move them to the processor.
        with patch.object(self.s.timing, "gpu_memory", side_effect=AssertionError("measured")):
            on, why = self.s.vision_on_card(self.cfg)
        self.assertTrue(on)
        self.assertIn("Apple chip", why)
        self.assertTrue(self.s.vision_on_card(self.cfg, (15.9, 16.0))[0])

    def test_server_command_on_a_mac(self):
        model = self.models / Q3
        model.write_bytes(b"")
        (self.models / Q3_VISION).write_bytes(b"")
        cmd = self.s.server_command(self.cfg, str(model),
                                    vision_on_card=self.s.vision_on_card(self.cfg)[0])
        self.assertEqual(cmd[0], self.cfg["server_exe"])               # no ".exe"
        self.assertIn("--mmproj", cmd)
        self.assertNotIn("--no-mmproj-offload", cmd)
        self.assertEqual(cmd[cmd.index("-ngl") + 1], "999")             # every layer on Metal
        self.assertEqual(cmd[cmd.index("-ctk") + 1], "q4_0")
        self.assertIn("-fa", cmd)

    def test_words_for_a_mac(self):
        self.assertEqual(self.s._place(self.cfg, True), "Apple chip")
        self.assertEqual(self.s._place(_config(self.tmp), True), "graphics card")
        self.assertEqual(self.s._place(self.cfg, False), "processor")

    def test_a_crash_says_memory_not_graphics_memory(self):
        class Dead:
            returncode = 3

            def poll(self):
                return 3
        self.s._proc = Dead()
        self.s._set("loaded", "x.gguf")
        self.s._poll()
        self.assertEqual(self.s.status()["state"], "failed")
        self.assertIn("not enough memory", self.s.status()["error"])
        self.assertNotIn("graphics", self.s.status()["error"])


class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_mac_launcher_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launcher = _load_launcher()

    def test_shared_memory_rule_matches_the_backend(self):
        self.assertTrue(self.launcher.is_shared_memory(_mac(self.tmp)))
        self.assertFalse(self.launcher.is_shared_memory(_config(self.tmp)))
        self.assertFalse(self.launcher.is_shared_memory(None))

    def test_voice_place(self):
        self.assertEqual(self.launcher.voice_place({"voice_device": "mps"}), "Apple chip's accelerator")
        self.assertEqual(self.launcher.voice_place({"voice_device": "cpu"}), "processor")
        self.assertEqual(self.launcher.voice_place({"voice_device": "cuda"}), "graphics card")

    def test_mps_voice_is_not_moved_to_the_processor(self):
        self.assertEqual(self.launcher.voice_env(_mac(self.tmp)), {})

    def test_simulate_follows_the_mac_tiers(self):
        cfg = _mac(self.tmp)
        out = self.launcher.apply_simulated_vram(cfg, 24)
        self.assertEqual((out["tier"], out["model"], out["context"], out["kv_cache"]),
                         ("mac-24", "12B", 24576, "q8_0"))
        self.assertEqual(out["voice_device"], "mps")
        self.assertEqual(out["model_label"], "Gemma 4 12B (QAT)")
        out16 = self.launcher.apply_simulated_vram(cfg, 16)
        self.assertEqual(out16["model_label"], "Gemma 4 12B (3-bit)")   # the Mac's own label
        self.assertIsNone(self.launcher.apply_simulated_vram(cfg, 8))    # option B on 8 GB


if __name__ == "__main__":
    unittest.main()

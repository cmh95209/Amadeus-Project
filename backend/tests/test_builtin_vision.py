# -*- coding: utf-8 -*-
"""Her built-in model sees pictures (attachments Phase 3, 2026-10-26).

Covers: finding the model's vision file (the installer's name, a file the
user added next to their own model, the only one in the folder, none), the
start options (--mmproj, the picture reader on the processor under 16 GB,
the bigger working space with pictures), the retries when a start with the
vision file fails (she then runs without it and Settings says why), and the
tier table's memory needs.
"""
import json
import sys
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
from test_builtin_server import _ServerBase  # noqa: E402

QAT = "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"
QAT_VISION = "gemma-4-12B-it-qat-mmproj-F16.gguf"


class VisionFileTests(_ServerBase):
    def test_installers_vision_file(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        (self.models / "some-other-mmproj.gguf").write_bytes(b"x")
        self.assertEqual(self.b.vision_file(self.models / QAT), str(self.models / QAT_VISION))

    def test_own_model_with_its_own_vision_file(self):
        own = self.models / "Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
        own.write_bytes(b"x")
        (self.models / "qwen3-vl-8b-instruct-mmproj-F16.gguf").write_bytes(b"x")
        (self.models / QAT_VISION).write_bytes(b"x")          # Gemma's: not this model's
        self.assertEqual(Path(self.b.vision_file(own)).name, "qwen3-vl-8b-instruct-mmproj-F16.gguf")

    def test_mmproj_named_after_the_model_family(self):
        own = self.models / "gemma-4-12b-it-Q5_K_M.gguf"
        own.write_bytes(b"x")
        (self.models / "gemma-4-12b-it-mmproj-F16.gguf").write_bytes(b"x")
        (self.models / "gemma-4-mmproj-F32.gguf").write_bytes(b"x")
        self.assertEqual(Path(self.b.vision_file(own)).name, "gemma-4-12b-it-mmproj-F16.gguf")

    def test_the_only_vision_file_in_the_folder(self):
        own = self.models / "my-model.gguf"
        own.write_bytes(b"x")
        (self.models / "mmproj-F16.gguf").write_bytes(b"x")
        self.assertEqual(Path(self.b.vision_file(own)).name, "mmproj-F16.gguf")

    def test_none_or_unclear(self):
        own = self.models / "my-model.gguf"
        own.write_bytes(b"x")
        self.assertEqual(self.b.vision_file(own), "")
        (self.models / "a-mmproj.gguf").write_bytes(b"x")
        (self.models / "b-mmproj.gguf").write_bytes(b"x")
        self.assertEqual(self.b.vision_file(own), "")           # two strangers: not a guess
        self.assertEqual(self.b.vision_file(""), "")

    def test_settings_name_the_vision_file(self):
        self.assertEqual(self.b.describe()["vision_file"], "")
        (self.models / QAT_VISION).write_bytes(b"x")
        self.assertEqual(self.b.describe()["vision_file"], QAT_VISION)

    def test_installer_names_match_the_tier_table(self):
        table = json.loads((SCRIPTS / "local_llm_tiers.json").read_text(encoding="utf-8"))
        names = {m["file"]: m["mmproj"]["save_as"] for m in table["models"].values()}
        self.assertEqual(names, self.b.VISION_FILES)


class CommandTests(_ServerBase):
    def command(self, vision=True, **over):
        cfg = _config(self.tmp, **over)
        if vision:
            (self.models / QAT_VISION).write_bytes(b"x")
        return self.s.server_command(cfg, cfg["model_file"])

    def test_vision_file_is_loaded(self):
        cmd = self.command(tier="24-plus", context=49152, kv_cache="f16")
        self.assertEqual(cmd[cmd.index("--mmproj") + 1], str(self.models / QAT_VISION))
        self.assertNotIn("--no-mmproj-offload", cmd)          # 16 GB+: on the card

    def test_picture_reader_placement(self):
        # On the card by default (MH 2026-10-26: 8 GB and 12 GB cards too);
        # 6-7 GB cards and a card measured too full: on the processor.
        self.assertIn("--no-mmproj-offload", self.command(tier="6-7"))
        for tier in ("8-11", "12-15", "16-23"):
            with self.subTest(tier=tier):
                self.assertNotIn("--no-mmproj-offload", self.command(tier=tier))
        cfg = _config(self.tmp, tier="12-15")
        self.assertIn("--no-mmproj-offload",
                      self.s.server_command(cfg, cfg["model_file"], vision_on_card=False))

    def test_bigger_working_space_with_pictures(self):
        cmd = self.command(tier="8-11", context=16384, kv_cache="q4_0")
        self.assertEqual(cmd[cmd.index("-ub") + 1], "512")
        (self.models / QAT_VISION).unlink()
        cmd = self.command(vision=False, tier="8-11", context=16384, kv_cache="q4_0")
        self.assertEqual(cmd[cmd.index("-ub") + 1], "256")
        self.assertNotIn("--mmproj", cmd)

    def test_retry_commands(self):
        cfg = _config(self.tmp)
        (self.models / QAT_VISION).write_bytes(b"x")
        safe = self.s.server_command(cfg, cfg["model_file"], safe=True)
        self.assertIn("--mmproj", safe)                       # safe defaults, still pictures
        self.assertNotIn("-ub", safe)
        bare = self.s.server_command(cfg, cfg["model_file"], vision=False)
        self.assertNotIn("--mmproj", bare)
        self.assertNotIn("--no-mmproj-offload", bare)


class CardMemoryTests(_ServerBase):
    """Where her pictures are read, from the free card memory (MH 2026-10-26)."""

    def place(self, used, total, voice_up=False, **over):
        cfg = _config(self.tmp, **over)
        with patch.object(self.s, "_voice_running", return_value=voice_up):
            return self.s.vision_on_card(cfg, (used, total))

    def test_8gb_card_with_room(self):
        on, why = self.place(0.6, 8.0, tier="8-11", vram_need_gb=6.5)
        self.assertTrue(on)                                   # 7.4 free >= 6.5 + 0.6
        self.assertIn("7.4 GB free", why)

    def test_8gb_card_too_full_goes_to_the_processor(self):
        on, _ = self.place(1.2, 8.0, tier="8-11", vram_need_gb=6.5)   # a browser / game open
        self.assertFalse(on)

    def test_12gb_card_and_her_voice(self):
        # Her voice on the card is part of the tier's need; once it runs it
        # is already in "used" - not counted twice.
        cfg = dict(tier="12-15", vram_need_gb=9.6, voice_device="cuda")
        self.assertTrue(self.place(1.0, 12.0, **cfg)[0])                    # 11.0 >= 10.2
        self.assertTrue(self.place(3.0, 12.0, voice_up=True, **cfg)[0])     # 9.0 >= 7.6 + 0.6
        self.assertFalse(self.place(3.0, 12.0, voice_up=False, **cfg)[0])   # voice still to come

    def test_a_bigger_own_model_needs_more_room(self):
        # 8-11 GB tier with the QAT file instead of its own 3-bit one (the
        # 2026-10-26 live check: 7.3 GB free looked enough, her model spilled).
        cfg = _config(self.tmp, tier="8-11", vram_need_gb=6.5)
        big = self.models / "my-bigger-model.gguf"
        with open(big, "wb") as f:
            f.truncate(5693872960 + 1024 ** 3)            # the tier's 3-bit file + 1 GB
        self.assertAlmostEqual(self.s.model_need_gb(cfg, big), 7.5, places=2)
        with patch.object(self.s, "_voice_running", return_value=False):
            self.assertFalse(self.s.vision_on_card(cfg, (0.7, 8.0), big)[0])        # 7.3 < 8.1
            self.assertTrue(self.s.vision_on_card(cfg, (0.7, 8.0))[0])              # its own model: fits

    def test_6gb_card_always_on_the_processor(self):
        self.assertFalse(self.place(0.0, 32.0, tier="6-7")[0])

    def test_no_nvidia_card(self):
        cfg = _config(self.tmp, tier="8-11")
        with patch.object(self.s.timing, "gpu_memory", return_value=None):
            self.assertEqual(self.s.vision_on_card(cfg)[0], False)
            self.assertEqual(self.s.vision_on_card(_config(self.tmp, tier="24-plus"))[0], True)


class RetryTests(_ServerBase):
    """Stand-in servers: dying ones while the vision file is in the command."""

    def setUp(self):
        super().setUp()
        p = patch.object(self.s, "vision_on_card", return_value=(True, "test: room on the card"))
        p.start()
        self.addCleanup(p.stop)

    def use_vision_fake(self, die_with_vision: bool, die_safe: bool = True, die_on_card=False):
        seen = []

        def cmd(cfg, model, safe=False, vision=True, vision_on_card=None, vision_path=None):
            seen.append((safe, vision, vision_on_card if vision else None))
            die = 3 if ((vision and die_with_vision) or (safe and die_safe and vision)
                        or (vision and vision_on_card and die_on_card)) else -1
            return [sys.executable, str(self.fake), str(self.port), "0.3", str(die)]
        p = patch.object(self.s, "server_command", side_effect=cmd)
        p.start()
        self.addCleanup(p.stop)
        return seen

    def test_runs_with_pictures(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        seen = self.use_vision_fake(die_with_vision=False)
        self.s.load()
        st = self.wait_for("loaded")
        self.assertTrue(st["pictures"])
        self.assertEqual(st["pictures_where"], "card")
        self.assertEqual(st["pictures_note"], "")
        self.assertEqual(seen, [(False, True, True)])

    def test_card_too_full_at_start_moves_pictures_to_the_processor(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        seen = self.use_vision_fake(die_with_vision=False, die_on_card=True)
        self.s.load()
        st = self.wait_for("loaded")
        self.assertTrue(st["pictures"])
        self.assertEqual(st["pictures_where"], "processor")
        # card -> card, safe defaults -> processor
        self.assertEqual(seen, [(False, True, True), (True, True, True), (False, True, False)])

    def test_broken_vision_file_starts_without_it(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        seen = self.use_vision_fake(die_with_vision=True)
        self.s.load()
        st = self.wait_for("loaded")
        self.assertFalse(st["pictures"])
        self.assertEqual(st["pictures_where"], "")
        self.assertIn("would not start with its vision file", st["pictures_note"])
        self.assertIn(QAT_VISION, st["pictures_note"])
        # card -> card, safe defaults -> processor -> without the vision file
        self.assertEqual(seen, [(False, True, True), (True, True, True), (False, True, False),
                                (False, False, None)])

    def test_no_vision_file_says_so(self):
        seen = self.use_vision_fake(die_with_vision=False)
        self.s.load()
        st = self.wait_for("loaded")
        self.assertFalse(st["pictures"])
        self.assertIn("no vision file", st["pictures_note"])
        self.assertEqual(seen, [(False, True, False)])        # (nothing to place)

    def test_unload_forgets_pictures(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        self.use_vision_fake(die_with_vision=False)
        self.s.load()
        self.wait_for("loaded")
        with patch("attachments.reset_capabilities") as reset:
            st = self.s.unload()
        self.assertFalse(st["pictures"])
        reset.assert_called_once()

    def test_crash_after_loading_is_still_noticed(self):
        self.use_vision_fake(die_with_vision=False)
        self.s.load()
        self.wait_for("loaded")
        self.s._proc.kill()
        self.s._proc.wait(timeout=10)
        st = self.s.status()
        self.assertEqual(st["state"], "failed")
        self.assertIn("stopped", st["error"])

    def test_everything_failing_still_reports_failed(self):
        (self.models / QAT_VISION).write_bytes(b"x")

        def cmd(cfg, model, safe=False, vision=True, vision_on_card=None, vision_path=None):
            return [sys.executable, str(self.fake), str(self.port), "0.3", "4"]
        p = patch.object(self.s, "server_command", side_effect=cmd)
        p.start()
        self.addCleanup(p.stop)
        self.s.load()
        st = self.wait_for("failed")
        self.assertIn("exit 4", st["error"])


class VisionDetectionTests(_ServerBase):
    """Finding the vision file by name markers (2026-11-11: the tester's
    folder had "mmproj-Gemma4-12B-....gguf" - marker in FRONT of the model
    name - plus the installer's own Gemma files and an MTP file: the old
    code found nothing)."""

    HAUHAU = "Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M.gguf"
    HAUHAU_VISION = "mmproj-Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-F16.gguf"

    def test_a_vision_file_named_after_its_model(self):
        # Her real folder: the model, its "mmproj-..." file, an MTP speed-up
        # file, and the installer's own Gemma 4 files.
        model = self.models / self.HAUHAU
        model.write_bytes(b"x")
        (self.models / self.HAUHAU_VISION).write_bytes(b"x")
        (self.models / "mtp-gemma-4-12b-it.gguf").write_bytes(b"x")
        (self.models / QAT).write_bytes(b"x")
        (self.models / QAT_VISION).write_bytes(b"x")
        (self.models / "gemma-4-12b-it-Q3_K_M.gguf").write_bytes(b"x")
        (self.models / "gemma-4-12b-it-mmproj-F16.gguf").write_bytes(b"x")
        self.assertEqual(self.b.vision_file(model), str(self.models / self.HAUHAU_VISION))

    def test_the_markers_are_found_as_whole_parts(self):
        for name, marker in (
            ("MyModel-7B-mmproj-F16.gguf", "mmproj"),
            ("MyModel-7B.mmproj.gguf", "mmproj"),
            ("MyModel-7B-mm-proj-F16.gguf", "mm-proj"),
            ("MyModel-7B-vision.gguf", "vision"),
            ("MyModel-7B-clip.gguf", "clip"),
            ("MyModel-7B-projector.gguf", "projector"),
        ):
            with self.subTest(marker=marker):
                own = self.models / "MyModel-7B-Q8_0.gguf"
                own.write_bytes(b"x")
                vision = self.models / name
                vision.write_bytes(b"x")
                self.assertEqual(self.b.vision_file(own), str(vision))
                own.unlink()
                vision.unlink()

    def test_envision_is_a_chat_model_not_a_vision_file(self):
        own = self.models / "envision-7b-instruct-Q4_K_M.gguf"
        own.write_bytes(b"x")
        self.assertFalse(self.b._is_vision_name(own.name))
        self.assertIn(own.name, [f["name"] for f in self.b.list_models()])

    def test_quant_and_precision_tags_do_not_break_the_match(self):
        # The model is 4-bit, its vision file is F16: the tags differ, the
        # model name must still match (and so does a bf16/q8_0 vision file).
        own = self.models / "MyModel-7B-Q4_K_M.gguf"
        own.write_bytes(b"x")
        (self.models / "MyModel-7B-mmproj-F16.gguf").write_bytes(b"x")
        self.assertEqual(self.b.vision_file(own),
                         str(self.models / "MyModel-7B-mmproj-F16.gguf"))
        own2 = self.models / "MyModel-7B-IQ4_XS.gguf"
        own2.write_bytes(b"x")
        (self.models / "mymodel-7b-mmproj-bf16.gguf").write_bytes(b"x")
        self.assertEqual(self.b.vision_file(own2),
                         str(self.models / "mymodel-7b-mmproj-bf16.gguf"))

    def test_the_vision_files_are_never_chat_models(self):
        (self.models / self.HAUHAU_VISION).write_bytes(b"x")
        (self.models / "MyModel-7B-vision.gguf").write_bytes(b"x")
        names = [f["name"] for f in self.b.list_models()]
        self.assertNotIn(self.HAUHAU_VISION, names)
        self.assertNotIn("MyModel-7B-vision.gguf", names)
        self.assertIn(QAT, names)                      # the config's model
        for f in self.b.vision_files():
            self.assertIn(f["name"], (self.HAUHAU_VISION, "MyModel-7B-vision.gguf"))


class VisionChoiceTests(_ServerBase):
    """The Settings -> Connection "Vision file" choice ("" = Automatic,
    "none" = no pictures, else a file from her models folder), kept like the
    chosen model file; the server loads it on the next Load."""

    OTHER = "gemma-4-12b-it-mmproj-F16.gguf"

    def test_a_picked_file_is_saved_and_used(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        (self.models / self.OTHER).write_bytes(b"x")
        self.b.save_selected_vision_file(self.OTHER)
        cfg = self.b.load_config()
        self.assertEqual(cfg.get("selected_vision_file"), str(self.models / self.OTHER))
        self.assertEqual(self.b.vision_choice(), self.OTHER)
        self.assertEqual(self.b.selected_vision_file(), str(self.models / self.OTHER))
        self.assertEqual(self.b.describe()["vision_file"], self.OTHER)
        self.b.save_selected_vision_file("")
        self.assertEqual(self.b.vision_choice(), "")     # Automatic again
        self.assertEqual(self.b.load_config().get("selected_vision_file"), None)

    def test_none_turns_pictures_off(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        self.b.save_selected_vision_file("none")
        self.assertEqual(self.b.vision_choice(), "none")
        self.assertEqual(self.b.selected_vision_file(), "")
        self.assertEqual(self.b.vision_file(self.models / QAT), str(self.models / QAT_VISION))

    def test_only_vision_files_in_the_folder_are_accepted(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        (self.models / "mtp-gemma-4-12b-it.gguf").write_bytes(b"x")
        outside = self.tmp / "elsewhere-vision.gguf"
        outside.write_bytes(b"x")
        for bad in (str(outside), "../elsewhere-vision.gguf", "mtp-gemma-4-12b-it.gguf",
                    QAT, "missing-vision.gguf"):
            with self.subTest(file=bad):
                with self.assertRaises(ValueError):
                    self.b.save_selected_vision_file(bad)
        self.assertIsNone(self.b.resolve_vision_file(str(outside)))
        self.b.save_selected_vision_file(QAT_VISION)     # a valid one
        self.assertEqual(self.b.vision_choice(), QAT_VISION)

    def test_a_picked_file_that_is_gone_is_automatic_again(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        self.b.save_selected_vision_file(QAT_VISION)
        (self.models / QAT_VISION).unlink()
        self.assertEqual(self.b.vision_choice(), "")
        self.assertEqual(self.b.selected_vision_file(), "")

    def test_load_passes_the_picked_file_to_llama_server(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        (self.models / self.OTHER).write_bytes(b"x")
        self.b.save_selected_vision_file(self.OTHER)
        p = patch.object(self.s, "vision_on_card", return_value=(True, "test: room on the card"))
        p.start()
        self.addCleanup(p.stop)
        seen = []

        def cmd(cfg, model, safe=False, vision=True, vision_on_card=None, vision_path=None):
            seen.append(vision_path if vision else None)
            return [sys.executable, str(self.fake), str(self.port), "0.3", "-1"]
        p = patch.object(self.s, "server_command", side_effect=cmd)
        p.start()
        self.addCleanup(p.stop)
        self.s.load()
        st = self.wait_for("loaded")
        self.assertTrue(st["pictures"])
        self.assertEqual(st["pictures_file"], self.OTHER)
        self.assertEqual(seen, [str(self.models / self.OTHER)])

    def test_load_with_none_runs_without_pictures(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        self.b.save_selected_vision_file("none")
        seen = []

        def cmd(cfg, model, safe=False, vision=True, vision_on_card=None, vision_path=None):
            seen.append(vision_path)
            return [sys.executable, str(self.fake), str(self.port), "0.3", "-1"]
        p = patch.object(self.s, "server_command", side_effect=cmd)
        p.start()
        self.addCleanup(p.stop)
        self.s.load()
        st = self.wait_for("loaded")
        self.assertFalse(st["pictures"])
        self.assertEqual(st["pictures_file"], "")
        self.assertIn("turned pictures off", st["pictures_note"])
        self.assertEqual(seen, [""])

    def test_unload_forgets_the_pictures_file(self):
        (self.models / QAT_VISION).write_bytes(b"x")
        self.use_fake(delay=0.4)
        self.s.load()
        st = self.wait_for("loaded")
        self.assertEqual(st["pictures_file"], QAT_VISION)
        st = self.s.unload()
        self.assertEqual(st["pictures_file"], "")
        self.assertFalse(st["pictures"])


class TierMemoryTests(unittest.TestCase):
    def test_memory_needs_include_pictures(self):
        table = json.loads((SCRIPTS / "local_llm_tiers.json").read_text(encoding="utf-8"))
        needs = {t["id"]: t["vram_need_gb"] for t in table["tiers"]}
        self.assertEqual(needs, {"6-7": 4.6, "8-11": 6.5, "12-15": 9.6, "16-23": 11.0, "24-plus": 11.5})
        self.assertIn("loaded with the model", table["about"])


if __name__ == "__main__":
    unittest.main()

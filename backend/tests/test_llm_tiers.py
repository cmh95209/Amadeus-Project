# -*- coding: utf-8 -*-
"""Offline checks for the built-in local model's tier table and choice rules.

Covers:
  1. the installer's embedded copy of scripts/local_llm_tiers.json is
     identical to the file (the installer runs before the project exists, so
     it carries its own copy);
  2. the table says what the user decided;
  3. the Python rules (scripts/llm_tiers.py, used by the launcher);
  4. the installer's PowerShell rules give the SAME answer as the Python
     ones for a grid of pretend hardware (Windows only - runs the real
     installer with -HardwareCheckOnly, which installs nothing).
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_INSTALLER = _SCRIPTS / "install_windows.ps1"


def _load_tiers():
    spec = importlib.util.spec_from_file_location("amadeus_llm_tiers_under_test",
                                                  _SCRIPTS / "llm_tiers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tiers = _load_tiers()
TABLE = tiers.load_table()


def _embedded_table() -> dict:
    text = _INSTALLER.read_text(encoding="utf-8")
    m = re.search(r"# >>> TIER TABLE.*?\n\$TierTableJson = @'\n(.*?)\n'@\n# <<< TIER TABLE",
                  text.replace("\r\n", "\n"), re.S)
    if not m:
        raise AssertionError("TIER TABLE block not found in install_windows.ps1")
    return json.loads(m.group(1))


class TableCopyTests(unittest.TestCase):
    def test_installer_copy_is_identical(self):
        self.assertEqual(_embedded_table(), TABLE)


class TableDecisionTests(unittest.TestCase):
    """The user's decisions (the approved tier plan)."""

    def tier(self, tier_id):
        return next(t for t in TABLE["tiers"] if t["id"] == tier_id)

    def test_one_model_per_tier(self):
        # 2026-10-18: the 31B (~31 GB at peak on a 32 GB card) and then
        # the 26B-A4B (31.5 GB) were too heavy -> every card of 24 GB+ runs
        # the 12B with a longer conversation.
        # 2026-10-21 (tiers v5): 8-11 GB cards get the 3-bit 12B (the 4-bit
        # needs 8.1 GB); under 8 GB no built-in model (the smaller files
        # did not sound like her).
        # 6-7 GB cards may choose the same 3-bit 12B after a NOTICE.
        expected = {"6-7": "12B-Q3", "8-11": "12B-Q3", "12-15": "12B", "16-23": "12B",
                    "24-plus": "12B"}
        self.assertEqual({t["id"]: t["model"] for t in TABLE["tiers"]}, expected)
        self.assertEqual([t["min_vram_gb"] for t in TABLE["tiers"]], [6, 8, 12, 16, 24])

    def test_tier_settings(self):
        expected = {   # context, kv cache, voice, conversation memory default
            "6-7": (16384, "q4_0", "cpu", 6000),
            "8-11": (16384, "q4_0", "cpu", 6000),
            "12-15": (24576, "q8_0", "cuda", 14000),
            "16-23": (32768, "f16", "cuda", 22000),
            "24-plus": (49152, "f16", "cuda", 32000),
        }
        for tier_id, settings in expected.items():
            with self.subTest(tier=tier_id):
                t = self.tier(tier_id)
                self.assertEqual((t["context"], t["kv_cache"], t["voice_device"],
                                  t["history_default"]), settings)

    def test_conversation_default_fits_the_context(self):
        # The context holds the conversation + ~6,000 (her character) +
        # 4,096 (her reply) - backend/builtin_llm.py's cap.
        for t in TABLE["tiers"]:
            with self.subTest(tier=t["id"]):
                self.assertLessEqual(t["history_default"] + 6000 + 4096, t["context"])

    def test_graphics_memory_estimates(self):
        needs = [t["vram_need_gb"] for t in TABLE["tiers"]]
        self.assertEqual(needs, sorted(needs))
        for t in TABLE["tiers"]:
            with self.subTest(tier=t["id"]):
                self.assertLessEqual(t["vram_need_gb"], t["min_vram_gb"])

    def test_only_the_6gb_tier_has_a_notice(self):
        self.assertEqual([t["id"] for t in TABLE["tiers"] if t["warning"]], ["6-7"])

    def test_official_unsloth_files_only(self):
        self.assertEqual(set(TABLE["models"]), {"12B", "12B-Q3"})
        self.assertTrue(TABLE["models"]["12B"]["file"].endswith("-it-qat-UD-Q4_K_XL.gguf"))
        self.assertEqual(TABLE["models"]["12B-Q3"]["file"], "gemma-4-12b-it-Q3_K_M.gguf")
        for key, model in TABLE["models"].items():
            with self.subTest(model=key):
                self.assertTrue(model["repo"].startswith("unsloth/gemma-4-"))
                self.assertTrue(model["repo"].endswith("-GGUF"))
                self.assertGreater(model["bytes"], 0)
                self.assertRegex(model["sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(model["revision"], r"^[0-9a-f]{40}$")
                # The speed-up (MTP drafter) files were downloaded but never
                # used (2026-10-18) - no longer part of the table.
                self.assertNotIn("drafter", model)
                # Each model's OWN vision file (the two differ), saved under
                # a model-specific name, for the coming picture support.
                v = model["mmproj"]
                self.assertEqual(v["file"], "mmproj-F16.gguf")
                self.assertIn("mmproj", v["save_as"])
                self.assertGreater(v["bytes"], 0)
                self.assertRegex(v["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(TABLE["models"]["12B"]["mmproj"]["save_as"],
                            TABLE["models"]["12B-Q3"]["mmproj"]["save_as"])

    def test_memory_rule(self):
        self.assertEqual(TABLE["voice_on_cpu_min_ram_gb"], 16)


class RoundingTests(unittest.TestCase):
    def test_cards_reporting_a_little_under_their_label(self):
        self.assertEqual(tiers.round_gb(32607 / 1024), 32)   # RTX 5090
        self.assertEqual(tiers.round_gb(8188 / 1024), 8)     # a typical "8 GB" card
        self.assertEqual(tiers.round_gb(34190917632 / 2**30), 32)  # registry bytes

    def test_halves_round_up(self):
        self.assertEqual(tiers.round_gb(7.5), 8)
        self.assertEqual(tiers.round_gb(8.5), 9)


# (vendor, vram, ram) -> expected (offered, reason or model, voice, build, warning)
GRID = [
    (None, 0, 32, (False, "no_card")),
    ("nvidia", 1, 32, (False, "no_card")),
    ("nvidia", 5, 32, (False, "small_card")),
    ("nvidia", 6, 32, (True, "12B-Q3", "cpu", "cuda-13.4", True)),
    ("nvidia", 6, 8, (False, "low_ram")),
    ("nvidia", 8, 16, (True, "12B-Q3", "cpu", "cuda-13.4", False)),
    ("nvidia", 10, 32, (True, "12B-Q3", "cpu", "cuda-13.4", False)),
    ("nvidia", 8, 8, (False, "low_ram")),
    ("nvidia", 12, 32, (True, "12B", "cuda", "cuda-13.4", False)),
    ("nvidia", 12, 8, (True, "12B", "cuda", "cuda-13.4", False)),   # voice on the card: no memory rule
    ("nvidia", 16, 8, (True, "12B", "cuda", "cuda-13.4", False)),
    ("nvidia", 24, 32, (True, "12B", "cuda", "cuda-13.4", False)),
    ("nvidia", 32, 32, (True, "12B", "cuda", "cuda-13.4", False)),
    ("nvidia", 48, 64, (True, "12B", "cuda", "cuda-13.4", False)),
    ("amd", 16, 8, (False, "low_ram")),
    ("amd", 32, 32, (True, "12B", "cpu", "vulkan", False)),
    ("intel", 12, 16, (True, "12B", "cpu", "vulkan", False)),
    ("nvidia-unverified", 24, 32, (True, "12B", "cpu", "vulkan", False)),
    ("nvidia", 8, -1, (True, "12B-Q3", "cpu", "cuda-13.4", False)),   # memory unknown
]


def _summary(choice):
    if not choice["offered"]:
        return (False, choice["reason"])
    return (True, choice["model"], choice["voice_device"], choice["build"], choice["warning"])


class PythonRuleTests(unittest.TestCase):
    def test_grid(self):
        for vendor, vram, ram, expected in GRID:
            with self.subTest(vendor=vendor, vram=vram, ram=ram):
                self.assertEqual(_summary(tiers.choose(TABLE, vendor, vram, ram, "610.88")), expected)

    def test_offered_choice_carries_tier_settings(self):
        c = tiers.choose(TABLE, "nvidia", 32, 32, "610.88")
        self.assertEqual((c["tier"], c["context"], c["kv_cache"], c["history_default"], c["vram_need_gb"]),
                         ("24-plus", 49152, "f16", 32000, 11.0))


class BuildTests(unittest.TestCase):
    def test_build_follows_the_nvidia_driver(self):
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88"), "cuda-13.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "580.10"), "cuda-13.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "576.02"), "cuda-12.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "516.94"), "vulkan")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", ""), "vulkan")
        for vendor in ("amd", "intel", "nvidia-unverified"):
            self.assertEqual(tiers.pick_build(TABLE, vendor, "610.88"), "vulkan")

    def test_builds_are_pinned_and_checked(self):
        lc = TABLE["llama_cpp"]
        self.assertRegex(lc["release"], r"^b\d+$")
        for name, build in lc["builds"].items():
            with self.subTest(build=name):
                self.assertTrue(any(f["name"].startswith("llama-" + lc["release"] + "-bin-win-")
                                    for f in build["files"]))
                for f in build["files"]:
                    self.assertTrue(f["name"].endswith("-x64.zip"))
                    self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
                    self.assertGreater(f["bytes"], 0)

    def test_download_size_counts_everything(self):
        m = TABLE["models"]["12B"]
        files = TABLE["llama_cpp"]["builds"]["cuda-13.4"]["files"]
        self.assertEqual(tiers.download_bytes(TABLE, "12B", "cuda-13.4"),
                         m["bytes"] + m["mmproj"]["bytes"] + sum(f["bytes"] for f in files))


_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell not available")
class PowerShellAgreesTests(unittest.TestCase):
    """The installer, run for real with pretend hardware, agrees with Python.
    The pretend card keeps this PC's card maker (or NVIDIA if it has none)."""

    def run_check(self, vram, ram):
        out = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(_INSTALLER), "-HardwareCheckOnly", "-Json",
             "-SimulateVramGB", str(vram), "-SimulateRamGB", str(ram)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_grid_matches_python(self):
        for vram, ram in [(0, 32), (5, 32), (6, 32), (6, 8), (8, 16), (8, 8), (12, 32),
                          (16, 8), (24, 32), (32, 32), (48, 64)]:
            with self.subTest(vram=vram, ram=ram):
                hw = self.run_check(vram, ram)
                vendor = hw["card"]["vendor"] if hw["card"] else None
                driver = hw["card"]["driver"] if hw["card"] else ""
                expected = tiers.choose(TABLE, vendor, vram, ram, driver)
                self.assertEqual(_summary(hw["choice"]), _summary(expected))
                if expected["offered"]:
                    self.assertEqual(hw["choice"]["tier"], expected["tier"])
                    self.assertEqual(hw["choice"]["context"], expected["context"])
                    self.assertEqual(hw["choice"]["kv_cache"], expected["kv_cache"])
                    self.assertEqual(hw["choice"]["history_default"], expected["history_default"])
                    self.assertEqual(hw["choice"]["vram_need_gb"], expected["vram_need_gb"])
                    self.assertEqual(hw["model"], TABLE["models"][expected["model"]])


if __name__ == "__main__":
    unittest.main()

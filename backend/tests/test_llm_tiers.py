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
     installer with -HardwareCheckOnly, which installs nothing);
  5. Macs (2026-11-07): the Mac installer's copy of the Mac table, the Mac
     decisions, and the Mac installer's bash rules agree with Python (run
     with --hardware-check-only: Git Bash on Windows, bash elsewhere);
  6. Linux (2026-11-08): the Linux installer's copy of the PC tiers, models
     and Linux builds, the Linux decisions, and its bash rules agree with
     Python.
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
_MAC_INSTALLER = _SCRIPTS / "install_macos.sh"
_LINUX_INSTALLER = _SCRIPTS / "install_linux.sh"


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


# (vendor, vram, ram, driver, cuda, cc) -> expected (offered, reason or model,
# voice, build, warning). cuda = the CUDA version nvidia-smi's header
# reports (the newest CUDA the driver can run; "" = unreadable). cc = the
# card's compute capability ("7.5"; "" = unknown) - a build holding native
# code for it runs even on a driver whose CUDA version is below the build's.
GRID = [
    (None, 0, 32, "", "", "", (False, "no_card")),
    ("nvidia", 1, 32, "610.88", "", "", (False, "no_card")),
    ("nvidia", 5, 32, "610.88", "", "", (False, "small_card")),
    ("nvidia", 6, 32, "610.88", "", "", (True, "12B-Q3", "cpu", "cuda-12.4", True)),
    ("nvidia", 6, 8, "610.88", "", "", (False, "low_ram")),
    ("nvidia", 8, 16, "610.88", "", "", (True, "12B-Q3", "cpu", "cuda-12.4", False)),
    ("nvidia", 10, 32, "610.88", "", "", (True, "12B-Q3", "cpu", "cuda-12.4", False)),
    ("nvidia", 8, 8, "610.88", "", "", (False, "low_ram")),
    ("nvidia", 12, 32, "610.88", "", "", (True, "12B", "cuda", "cuda-12.4", False)),
    ("nvidia", 12, 8, "610.88", "", "", (True, "12B", "cuda", "cuda-12.4", False)),   # voice on the card: no memory rule
    ("nvidia", 16, 8, "610.88", "", "", (True, "12B", "cuda", "cuda-12.4", False)),
    ("nvidia", 24, 32, "610.88", "", "", (True, "12B", "cuda", "cuda-12.4", False)),
    ("nvidia", 32, 32, "610.88", "", "", (True, "12B", "cuda", "cuda-12.4", False)),
    ("nvidia", 48, 64, "610.88", "", "", (True, "12B", "cuda", "cuda-12.4", False)),
    # The tester's PC (2026-11-10): a driver 58x reports CUDA 13.0, which
    # cannot run a 13.4 build - the newest build it CAN run is 12.4.
    ("nvidia", 6, 32, "581.0", "13.0", "", (True, "12B-Q3", "cpu", "cuda-12.4", True)),
    ("nvidia", 12, 32, "581.0", "13.0", "", (True, "12B", "cuda", "cuda-12.4", False)),
    # A driver that reports CUDA 13.4 runs the 13.4 build.
    ("nvidia", 32, 32, "580.10", "13.4", "", (True, "12B", "cuda", "cuda-13.4", False)),
    # CUDA version unreadable: the driver-number rule, minus the 13.x builds.
    ("nvidia", 32, 32, "590.0", "", "", (True, "12B", "cuda", "cuda-12.4", False)),
    ("nvidia", 32, 32, "576.02", "", "", (True, "12B", "cuda", "cuda-12.4", False)),
    ("nvidia", 32, 32, "516.94", "", "", (True, "12B", "cuda", "vulkan", False)),
    ("nvidia", 32, 32, "", "", "", (True, "12B", "cuda", "vulkan", False)),
    ("amd", 16, 8, "", "", "", (False, "low_ram")),
    ("amd", 32, 32, "", "", "", (True, "12B", "cpu", "vulkan", False)),
    ("intel", 12, 16, "", "", "", (True, "12B", "cpu", "vulkan", False)),
    ("nvidia-unverified", 24, 32, "", "", "", (True, "12B", "cpu", "vulkan", False)),
    ("nvidia", 8, -1, "610.88", "", "", (True, "12B-Q3", "cpu", "cuda-12.4", False)),   # memory unknown
    # CUDA minor-version compatibility via the build's NATIVE code (the 13.4
    # build holds native sm_86/sm_89/sm_120/sm_121 code; 12.4 holds
    # sm_50..sm_90). A build runs when the card's generation is in its list
    # even if the driver's CUDA version is below the build's.
    ("nvidia", 32, 32, "610.88", "13.3", "12.0", (True, "12B", "cuda", "cuda-13.4", False)),   # RTX 5090: 13.3 < 13.4 but sm_120 is native in 13.4
    ("nvidia", 6, 32, "581.0", "13.0", "7.5", (True, "12B-Q3", "cpu", "cuda-12.4", True)),     # RTX 2060: sm_75 not native in 13.4, 13.0 >= 12.4
    ("nvidia", 12, 32, "581.0", "13.0", "8.6", (True, "12B", "cuda", "cuda-13.4", False)),     # RTX 3060: sm_86 is native in 13.4
    ("nvidia", 32, 32, "580.10", "13.3", "", (True, "12B", "cuda", "cuda-12.4", False)),       # unknown generation: only the driver's CUDA decides
    ("nvidia", 32, 32, "580.10", "13.4", "7.5", (True, "12B", "cuda", "cuda-13.4", False)),    # driver CUDA >= build: native list not needed
    ("nvidia", 32, 32, "610.88", "12.4", "7.5", (True, "12B", "cuda", "cuda-12.4", False)),    # 12.4 native for sm_75, 13.4 is not
]


def _summary(choice):
    if not choice["offered"]:
        return (False, choice["reason"])
    return (True, choice["model"], choice["voice_device"], choice["build"], choice["warning"])


class PythonRuleTests(unittest.TestCase):
    def test_grid(self):
        for vendor, vram, ram, driver, cuda, cc, expected in GRID:
            with self.subTest(vendor=vendor, vram=vram, ram=ram, driver=driver, cuda=cuda, cc=cc):
                self.assertEqual(_summary(tiers.choose(TABLE, vendor, vram, ram, driver, cuda, cc)), expected)

    def test_offered_choice_carries_tier_settings(self):
        c = tiers.choose(TABLE, "nvidia", 32, 32, "610.88")
        self.assertEqual((c["tier"], c["context"], c["kv_cache"], c["history_default"], c["vram_need_gb"]),
                         ("24-plus", 49152, "f16", 32000, 11.5))   # + pictures (2026-10-26)


class BuildTests(unittest.TestCase):
    def test_build_follows_the_nvidia_driver(self):
        # The CUDA version the driver supports decides first (nvidia-smi's
        # header): a build is only used when the driver runs its CUDA version
        # OR the build holds native code for the card's generation.
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "581.0", None, "13.0"), "cuda-12.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "580.10", None, "13.4"), "cuda-13.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88", None, "13.4"), "cuda-13.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88", None, "13.3"), "cuda-12.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "576.02", None, "13.4"), "cuda-12.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "516.94", None, "13.4"), "vulkan")
        # Native code: a build compiled for the card's generation runs on a
        # driver whose CUDA version is below the build's (minor-version
        # compatibility; the native lists are measured from the b11400
        # archives - see the tier table's native_cc).
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88", None, "13.3", "12.0"), "cuda-13.4")   # RTX 5090
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88", None, "13.3", "12.1"), "cuda-13.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "581.0", None, "13.0", "8.6"), "cuda-13.4")     # RTX 3060
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "581.0", None, "13.0", "8.9"), "cuda-13.4")     # RTX 4060
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "581.0", None, "13.0", "7.5"), "cuda-12.4")     # RTX 2060: not in 13.4's native list
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "581.0", None, "13.0", "9.0"), "cuda-12.4")     # sm_90 not in 13.4's native list
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "580.10", None, "13.4", "7.5"), "cuda-13.4")    # driver CUDA >= build: native list not needed
        # A card whose generation we cannot read is never "native".
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88", None, "13.3", ""), "cuda-12.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88", None, "13.3", "99.9"), "cuda-12.4")
        # CUDA version unreadable: the driver-number rule stays, minus 13.x.
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "610.88"), "cuda-12.4")
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "590.0"), "cuda-12.4")
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
                prefix = "-bin-macos-" if name.startswith("metal") else "-bin-win-"
                self.assertTrue(any(f["name"].startswith("llama-" + lc["release"] + prefix)
                                    for f in build["files"]))
                for f in build["files"]:
                    if name.startswith("metal"):
                        self.assertTrue(f["name"].endswith("-arm64.tar.gz"))
                    else:
                        self.assertTrue(f["name"].endswith("-x64.zip"))
                    self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
                    self.assertGreater(f["bytes"], 0)

    def test_native_cc_lists_are_the_measured_b11400_sets(self):
        # Measured from the official b11400 archives (2026-11-10): the CUDA
        # 13.4 builds hold native sm_86/sm_89/sm_120/sm_121 code (no PTX),
        # the Windows CUDA 12.4 build sm_50..sm_90 (SASS + PTX), and the
        # Linux CUDA 12.8 build sm_120 only.
        win = TABLE["llama_cpp"]["builds"]
        self.assertEqual(win["cuda-13.4"]["native_cc"], ["8.6", "8.9", "12.0", "12.1"])
        self.assertEqual(win["cuda-12.4"]["native_cc"], ["5.0", "6.1", "7.0", "7.5", "8.0", "9.0"])
        lin = TABLE["linux"]["builds"]
        self.assertEqual(lin["cuda-13.4"]["native_cc"], ["8.6", "8.9", "12.0", "12.1"])
        self.assertEqual(lin["cuda-12.8"]["native_cc"], ["12.0"])
        for builds in (win, lin):
            for name, b in builds.items():
                if not name.startswith("cuda-"):
                    self.assertNotIn("native_cc", b)   # vulkan / metal: no CUDA code

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
                card = hw["card"] or {}
                vendor = card.get("vendor")
                driver = card.get("driver", "")
                cuda = card.get("cuda", "")
                cc = card.get("cc", "")
                expected = tiers.choose(TABLE, vendor, vram, ram, driver, cuda, cc)
                self.assertEqual(_summary(hw["choice"]), _summary(expected))
                if expected["offered"]:
                    self.assertEqual(hw["choice"]["tier"], expected["tier"])
                    self.assertEqual(hw["choice"]["context"], expected["context"])
                    self.assertEqual(hw["choice"]["kv_cache"], expected["kv_cache"])
                    self.assertEqual(hw["choice"]["history_default"], expected["history_default"])
                    self.assertEqual(hw["choice"]["vram_need_gb"], expected["vram_need_gb"])
                    self.assertEqual(hw["model"], TABLE["models"][expected["model"]])


# ---------------------------------------------------------------------------
# Macs
# ---------------------------------------------------------------------------
def _mac_text() -> str:
    return _MAC_INSTALLER.read_text(encoding="utf-8").replace("\r\n", "\n")


def _mac_shell_table() -> dict:
    """The MAC TIER TABLE block of install_macos.sh, read back into Python."""
    m = re.search(r"# >>> MAC TIER TABLE.*?\n(.*?)# <<< MAC TIER TABLE", _mac_text(), re.S)
    if not m:
        raise AssertionError("MAC TIER TABLE block not found in install_macos.sh")
    values = dict(re.findall(r'^([A-Z_]+)="(.*?)"$', m.group(1), re.S | re.M))
    values.update(re.findall(r'^([A-Z_]+)=(\d+)$', m.group(1), re.M))
    return values


class MacTableCopyTests(unittest.TestCase):
    def setUp(self):
        self.sh = _mac_shell_table()
        self.mac = TABLE["mac"]

    def test_release_build_and_limits(self):
        self.assertEqual(self.sh["LLAMA_RELEASE"], TABLE["llama_cpp"]["release"])
        self.assertEqual(self.sh["MAC_BUILD"], self.mac["build"])
        self.assertEqual(int(self.sh["MAC_MIN_RAM_GB"]), self.mac["min_ram_gb"])
        self.assertEqual(int(self.sh["DISK_HEADROOM_GB"]), TABLE["disk_headroom_gb"])

    def test_build_files(self):
        files = [line.split() for line in self.sh["MAC_BUILD_FILES"].splitlines()]
        expected = [[f["name"], str(f["bytes"]), f["sha256"]]
                    for f in TABLE["llama_cpp"]["builds"][self.mac["build"]]["files"]]
        self.assertEqual(files, expected)

    def test_tiers(self):
        rows = [line.split() for line in self.sh["MAC_TIERS"].splitlines()]
        expected = [[t["id"], str(t["min_ram_gb"]), t["model"], str(t["context"]), t["kv_cache"],
                     str(t["history_default"]), str(t["memory_need_gb"])] for t in self.mac["tiers"]]
        self.assertEqual(rows, expected)

    def test_models(self):
        labels = self.mac.get("labels", {})
        rows = {line.split("|")[0]: line.split("|") for line in self.sh["MAC_MODELS"].splitlines()}
        self.assertEqual(set(rows), set(TABLE["models"]))
        for key, m in TABLE["models"].items():
            with self.subTest(model=key):
                v = m["mmproj"]
                self.assertEqual(rows[key], [key, labels.get(key, m["label"]), m["repo"], m["revision"],
                                             m["file"], str(m["bytes"]), m["sha256"], v["file"],
                                             v["save_as"], str(v["bytes"]), v["sha256"]])


class MacDecisionTests(unittest.TestCase):
    """MH's decision 2026-11-07 (plan D1 a): Apple chip and 16 GB or more."""

    def test_tiers(self):
        mac = TABLE["mac"]
        self.assertEqual(mac["min_ram_gb"], 16)
        self.assertEqual([(t["id"], t["min_ram_gb"], t["model"]) for t in mac["tiers"]],
                         [("mac-16", 16, "12B-Q3"), ("mac-24", 24, "12B"),
                          ("mac-32", 32, "12B"), ("mac-48", 48, "12B")])
        for t in mac["tiers"]:
            with self.subTest(tier=t["id"]):
                self.assertLessEqual(t["history_default"] + 6000 + 4096, t["context"])
                self.assertIn(t["kv_cache"], ("q4_0", "q8_0", "f16"))
                self.assertIn(t["model"], TABLE["models"])
                # About two thirds of a Mac's memory may go to graphics.
                self.assertLessEqual(t["memory_need_gb"], t["min_ram_gb"] * 2 / 3)

    def test_build_is_pinned(self):
        files = TABLE["llama_cpp"]["builds"]["metal-arm64"]["files"]
        self.assertEqual([f["name"] for f in files], ["llama-b11400-bin-macos-arm64.tar.gz"])
        self.assertRegex(files[0]["sha256"], r"^[0-9a-f]{64}$")

    def test_windows_never_picks_the_mac_build(self):
        for vendor in ("nvidia", "amd", "intel", "nvidia-unverified", None):
            self.assertNotEqual(tiers.pick_build(TABLE, vendor, "610.88"), "metal-arm64")


MAC_GRID = [   # (apple chip, memory GB) -> expected
    (False, 64, (False, "intel_mac")),
    (False, 16, (False, "intel_mac")),
    (True, -1, (False, "low_ram")),
    (True, 8, (False, "low_ram")),
    (True, 15, (False, "low_ram")),
    (True, 16, (True, "12B-Q3", "mps", "metal-arm64", False)),
    (True, 18, (True, "12B-Q3", "mps", "metal-arm64", False)),
    (True, 24, (True, "12B", "mps", "metal-arm64", False)),
    (True, 36, (True, "12B", "mps", "metal-arm64", False)),
    (True, 48, (True, "12B", "mps", "metal-arm64", False)),
    (True, 128, (True, "12B", "mps", "metal-arm64", False)),
]


class MacPythonRuleTests(unittest.TestCase):
    def test_grid(self):
        for apple, ram, expected in MAC_GRID:
            with self.subTest(apple=apple, ram=ram):
                self.assertEqual(_summary(tiers.choose_mac(TABLE, apple, ram)), expected)

    def test_offered_choice_carries_tier_settings(self):
        c = tiers.choose_mac(TABLE, True, 36)
        self.assertEqual((c["tier"], c["context"], c["kv_cache"], c["history_default"], c["vram_need_gb"]),
                         ("mac-32", 32768, "f16", 22000, 13.0))


def _bash():
    git = shutil.which("git")
    if git and os.name == "nt":
        # git.exe is in Git\cmd (or Git\mingw64in): bash.exe is in Gitin.
        for up in (1, 2):
            candidate = Path(git).resolve().parents[up] / "bin" / "bash.exe"
            if candidate.exists():
                return str(candidate)
        return None
    return shutil.which("bash")


_BASH = _bash()


@unittest.skipUnless(_BASH, "bash not available")
class MacShellAgreesTests(unittest.TestCase):
    """The Mac installer, run for real with pretend Macs, agrees with Python."""

    def test_grid_matches_python(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="amadeus_mac_hw_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        script = tmp / "install_macos.sh"
        script.write_bytes(_MAC_INSTALLER.read_bytes().replace(b"\r\n", b"\n"))
        for apple, ram, _ in MAC_GRID:
            with self.subTest(apple=apple, ram=ram):
                out = subprocess.run(
                    [_BASH, str(script), str(tmp / "inst"), "--hardware-check-only", "--json",
                     "--simulate-chip", "apple" if apple else "intel", "--simulate-ram-gb", str(ram)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                hw = json.loads(out.stdout.strip().splitlines()[-1])
                expected = tiers.choose_mac(TABLE, apple, ram)
                self.assertEqual(hw["choice"], expected)
                self.assertFalse((tmp / "inst").exists())      # installs nothing
                if expected["offered"]:
                    gb = tiers.download_bytes(TABLE, expected["model"], "metal-arm64") / 2 ** 30
                    self.assertAlmostEqual(hw["download_gb"], gb, delta=0.11)


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------
def _linux_shell_table() -> dict:
    """The LINUX TIER TABLE block of install_linux.sh, read back into Python."""
    text = _LINUX_INSTALLER.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"# >>> LINUX TIER TABLE.*?\n(.*?)# <<< LINUX TIER TABLE", text, re.S)
    if not m:
        raise AssertionError("LINUX TIER TABLE block not found in install_linux.sh")
    values = dict(re.findall(r'^([A-Z_]+)="(.*?)"$', m.group(1), re.S | re.M))
    values.update(re.findall(r'^([A-Z_]+)=(\d+)$', m.group(1), re.M))
    return values


class LinuxTableCopyTests(unittest.TestCase):
    def setUp(self):
        self.sh = _linux_shell_table()

    def test_release_and_limits(self):
        self.assertEqual(self.sh["LLAMA_RELEASE"], TABLE["llama_cpp"]["release"])
        self.assertEqual(int(self.sh["DEDICATED_CARD_MIN_GB"]), TABLE["dedicated_card_min_gb"])
        self.assertEqual(int(self.sh["VOICE_ON_CPU_MIN_RAM_GB"]), TABLE["voice_on_cpu_min_ram_gb"])
        self.assertEqual(int(self.sh["DISK_HEADROOM_GB"]), TABLE["disk_headroom_gb"])

    def test_builds(self):
        rows = [line.split() for line in self.sh["LINUX_BUILDS"].splitlines()]
        expected = []
        for name, b in TABLE["linux"]["builds"].items():
            row = [name, str(b.get("min_nvidia_driver", 0)),
                   ",".join(b.get("native_cc") or []) or "-"]
            for f in b["files"]:
                row += [f["name"], str(f["bytes"]), f["sha256"]]
            expected.append(row)
        self.assertEqual(rows, expected)

    def test_tiers(self):
        rows = [line.split() for line in self.sh["PC_TIERS"].splitlines()]
        expected = [[t["id"], str(t["min_vram_gb"]), t["model"], str(t["context"]), t["kv_cache"],
                     t["voice_device"], str(t["history_default"]), str(t["vram_need_gb"]),
                     "1" if t["warning"] else "0"] for t in TABLE["tiers"]]
        self.assertEqual(rows, expected)

    def test_models(self):
        rows = {line.split("|")[0]: line.split("|") for line in self.sh["PC_MODELS"].splitlines()}
        self.assertEqual(set(rows), set(TABLE["models"]))
        for key, m in TABLE["models"].items():
            with self.subTest(model=key):
                v = m["mmproj"]
                self.assertEqual(rows[key], [key, m["label"], m["repo"], m["revision"], m["file"],
                                             str(m["bytes"]), m["sha256"], v["file"], v["save_as"],
                                             str(v["bytes"]), v["sha256"]])


class LinuxDecisionTests(unittest.TestCase):
    """MH's picks 2026-11-08 (plan 3a): the Windows tiers, Linux builds."""

    def test_builds_are_pinned_linux_files(self):
        builds = TABLE["linux"]["builds"]
        self.assertEqual(list(builds), ["cuda-13.4", "cuda-12.8", "vulkan"])
        self.assertEqual((builds["cuda-13.4"]["min_nvidia_driver"], builds["cuda-12.8"]["min_nvidia_driver"]),
                         (580, 570))
        for name, b in builds.items():
            for f in b["files"]:
                with self.subTest(file=f["name"]):
                    self.assertIn("-b11400-bin-ubuntu-", f["name"])
                    self.assertTrue(f["name"].endswith("-x64.tar.gz"))
                    self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
            # An NVIDIA build = llama.cpp + NVIDIA's runtime files.
            self.assertEqual(len(b["files"]), 2 if name.startswith("cuda") else 1)

    def test_windows_never_picks_a_linux_build(self):
        # The Linux builds share names with Windows ones on purpose (the app
        # reads "cuda-..." / "vulkan" the same way), but live in their own list.
        win_files = [g["name"] for b in TABLE["llama_cpp"]["builds"].values() for g in b["files"]]
        for b in TABLE["linux"]["builds"].values():
            for f in b["files"]:
                self.assertNotIn(f["name"], win_files)
        self.assertEqual(tiers.pick_build(TABLE, "nvidia", "575.64"), "cuda-12.4")   # Windows unchanged


LINUX_GRID = [   # (vendor, vram, ram, driver, x64, cuda, cc) -> summary
    (None, 0, 32, "", True, "", "", (False, "no_card")),
    ("nvidia-nodriver", 0, 32, "", True, "", "", (False, "nvidia_no_driver")),
    ("nvidia", 16, 32, "610.88", False, "", "", (False, "not_x64")),
    ("nvidia", 5, 32, "610.88", True, "", "", (False, "small_card")),
    ("nvidia", 6, 32, "610.88", True, "", "", (True, "12B-Q3", "cpu", "cuda-12.8", True)),
    ("nvidia", 8, 8, "610.88", True, "", "", (False, "low_ram")),
    ("nvidia", 12, 8, "575.64", True, "", "", (True, "12B", "cuda", "cuda-12.8", False)),
    ("nvidia", 16, 32, "570.0", True, "", "", (True, "12B", "cuda", "cuda-12.8", False)),
    ("nvidia", 24, 32, "550.120", True, "", "", (True, "12B", "cuda", "vulkan", False)),
    # The CUDA version the driver reports decides first (2026-11-10).
    ("nvidia", 32, 64, "580.10", True, "13.4", "", (True, "12B", "cuda", "cuda-13.4", False)),
    ("nvidia", 32, 64, "581.0", True, "13.0", "", (True, "12B", "cuda", "cuda-12.8", False)),
    # CUDA version unreadable: the driver-number rule, minus the 13.x builds.
    ("nvidia", 32, 64, "580.10", True, "", "", (True, "12B", "cuda", "cuda-12.8", False)),
    ("nvidia", 32, 64, "590.0", True, "", "", (True, "12B", "cuda", "cuda-12.8", False)),
    ("amd", 16, 8, "", True, "", "", (False, "low_ram")),
    ("amd", 16, 32, "", True, "", "", (True, "12B", "cpu", "vulkan", False)),
    ("intel", 12, 16, "", True, "", "", (True, "12B", "cpu", "vulkan", False)),
    ("amd", 8, -1, "", True, "", "", (True, "12B-Q3", "cpu", "vulkan", False)),   # memory unknown
    # CUDA minor-version compatibility via the builds' NATIVE code (13.4:
    # sm_86/sm_89/sm_120/sm_121; 12.8: sm_120 only - measured from the
    # b11400 archives).
    ("nvidia", 32, 64, "610.88", True, "13.3", "12.0", (True, "12B", "cuda", "cuda-13.4", False)),   # RTX 5090: 13.3 < 13.4 but sm_120 is native in both CUDA builds
    ("nvidia", 6, 32, "581.0", True, "13.0", "7.5", (True, "12B-Q3", "cpu", "cuda-12.8", True)),     # RTX 2060: sm_75 is native in neither build, 13.0 >= 12.8
    ("nvidia", 12, 32, "581.0", True, "13.0", "8.6", (True, "12B", "cuda", "cuda-13.4", False)),     # RTX 3060: sm_86 is native in 13.4
    ("nvidia", 32, 64, "610.88", True, "13.3", "", (True, "12B", "cuda", "cuda-12.8", False)),       # unknown generation: only the driver's CUDA decides
]


class LinuxPythonRuleTests(unittest.TestCase):
    def test_grid(self):
        for vendor, vram, ram, driver, x64, cuda, cc, expected in LINUX_GRID:
            with self.subTest(vendor=vendor, vram=vram, ram=ram, driver=driver, cuda=cuda, cc=cc):
                self.assertEqual(_summary(tiers.choose_linux(TABLE, vendor, vram, ram, driver, x64, cuda, cc)), expected)

    def test_download_counts_both_nvidia_archives(self):
        b = TABLE["linux"]["builds"]["cuda-13.4"]
        self.assertEqual(tiers.download_bytes(TABLE, "12B", "cuda-13.4", TABLE["linux"]["builds"]),
                         TABLE["models"]["12B"]["bytes"] + TABLE["models"]["12B"]["mmproj"]["bytes"]
                         + sum(f["bytes"] for f in b["files"]))


@unittest.skipUnless(_BASH, "bash not available")
class LinuxShellAgreesTests(unittest.TestCase):
    """The Linux installer, run for real with pretend PCs, agrees with Python."""

    def test_grid_matches_python(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="amadeus_linux_hw_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        script = tmp / "install_linux.sh"
        script.write_bytes(_LINUX_INSTALLER.read_bytes().replace(b"\r\n", b"\n"))
        for vendor, vram, ram, driver, x64, _cuda, cc, _ in LINUX_GRID:
            if not x64:
                continue        # the processor type is read from the PC (not pretendable)
            with self.subTest(vendor=vendor, vram=vram, ram=ram, driver=driver, cc=cc):
                args = [_BASH, str(script), str(tmp / "inst"), "--hardware-check-only", "--json",
                        "--simulate-gpu", vendor or "none", "--simulate-vram-gb", str(vram),
                        "--simulate-ram-gb", str(ram)]
                if driver:
                    args += ["--simulate-driver", driver]
                if cc:
                    args += ["--simulate-cc", cc]
                out = subprocess.run(args, capture_output=True, text=True, timeout=60)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                hw = json.loads(out.stdout.strip().splitlines()[-1])
                expected = tiers.choose_linux(TABLE, vendor, vram, ram, driver, True,
                                              hw.get("cuda", ""), hw.get("cc", ""))
                self.assertEqual(hw["choice"], expected)
                self.assertFalse((tmp / "inst").exists())      # installs nothing
                if expected["offered"]:
                    gb = tiers.download_bytes(TABLE, expected["model"], expected["build"],
                                              TABLE["linux"]["builds"]) / 2 ** 30
                    self.assertAlmostEqual(hw["download_gb"], gb, delta=0.11)


if __name__ == "__main__":
    unittest.main()

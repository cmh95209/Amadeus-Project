"""Which built-in local model (and settings) a PC gets.

The table lives in scripts/local_llm_tiers.json. The installer picks a tier
with the same rules in PowerShell (Select-LlmTier in install_windows.ps1);
tests/test_llm_tiers.py runs both on the same inputs so they never disagree.
The launcher uses this module for its -SimulateVramGB test option.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

TABLE_FILE = Path(__file__).resolve().parent / "local_llm_tiers.json"


def load_table(path: Path | None = None) -> dict:
    return json.loads(Path(path or TABLE_FILE).read_text(encoding="utf-8"))


def round_gb(value: float) -> int:
    """Nearest whole GB, halves rounded up. Cards report a little under their
    label (an RTX 5090 says 32607 MiB = 31.8 GB), so a plain comparison would
    drop every card one tier."""
    return int(math.floor(value + 0.5))


def driver_major(driver: str) -> int:
    """'610.88' -> 610; anything unreadable -> 0."""
    head = (driver or "").strip().split(".")[0]
    return int(head) if head.isdigit() else 0


def build_cuda(name: str) -> tuple[int, int] | None:
    """The CUDA version a build needs ('cuda-13.4' -> (13, 4)); None for
    non-CUDA builds (vulkan, metal)."""
    m = re.fullmatch(r"cuda-(\d+)(?:\.(\d+))?", name or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0))


def parse_cuda_version(value: str) -> tuple[int, int] | None:
    """The CUDA version a driver supports ('13.4' -> (13, 4)); None when the
    value is missing or unreadable."""
    v = (value or "").strip()
    if not v:
        return None
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?", v)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0))


def pick_build(table: dict, vendor: str | None, driver: str = "",
               builds: dict | None = None, cuda: str = "", cc: str = "") -> str:
    """Which llama.cpp build: the newest CUDA build this card can run.
    A CUDA build is picked only when the driver runs it: the driver number
    check (min_nvidia_driver) applies, and EITHER
      (a) the CUDA version the driver supports - the "CUDA Version" line of
          nvidia-smi's header (a CUDA 13.4 build on a driver that stops at
          13.0 crashes at first GPU use) - is >= the build's CUDA version,
      or (b) the card's compute capability is one of the build's NATIVE
          architectures (builds["<name>"]["native_cc"]: real GPU code for
          that card, which CUDA's minor-version compatibility runs even on
          a driver that stops at an older 13.x).
    A card whose compute capability we cannot read counts as not native.
    When the CUDA version cannot be read AND no build has the card natively,
    the old driver-number rule still applies, but 13.x builds are left out
    (a driver we cannot verify). Else Vulkan (AMD / Intel / old or
    unreadable NVIDIA driver).
    cuda: the CUDA version nvidia-smi's header reported ("" = unreadable).
    cc: the card's compute capability ("7.5"; "" = unknown).
    builds: another build list (Linux: table["linux"]["builds"])."""
    if builds is None:
        builds = table["llama_cpp"]["builds"]
    if vendor == "nvidia":
        major = driver_major(driver)
        have = parse_cuda_version(cuda)
        eligible = []
        for name, b in builds.items():
            need = build_cuda(name)
            if need is None:
                continue
            if major < int(b.get("min_nvidia_driver", 0)):
                continue
            runs = have is not None and have >= need            # (a)
            if not runs and cc:
                runs = cc in (b.get("native_cc") or [])         # (b) native code
            if runs:
                eligible.append((need, name))
        if have is None:
            # CUDA version unreadable: the old driver-number rule, minus
            # 13.x builds (a driver we cannot verify).
            for name, b in builds.items():
                need = build_cuda(name)
                if need is None or need[0] >= 13:
                    continue
                if major >= int(b.get("min_nvidia_driver", 0)):
                    eligible.append((need, name))
        if eligible:
            return max(eligible, key=lambda c: c[0])[1]
    return "vulkan"


def download_bytes(table: dict, model_key: str, build: str,
                   builds: dict | None = None) -> int:
    """Everything option A downloads: model + its vision file + llama.cpp files."""
    model = table["models"][model_key]
    files = (builds if builds is not None else table["llama_cpp"]["builds"])[build]["files"]
    vision = (model.get("mmproj") or {}).get("bytes", 0)
    return model["bytes"] + vision + sum(f["bytes"] for f in files)


def choose_mac(table: dict, apple_chip: bool, ram_gb: int) -> dict:
    """The Mac installer's decision (scripts/install_macos.sh mac_choose
    follows the same rules; the tests run both on the same inputs).

    apple_chip: an Apple chip (M1 and later); an Intel Mac gets option B
    (llama.cpp has no graphics acceleration there - far too slow for her).
    ram_gb: the Mac's memory in whole GB, shared by processor and graphics
    (negative = unknown: not offered, the memory decides everything here).
    Returns the same keys as choose(); vram_need_gb = memory_need_gb.
    """
    mac = table["mac"]
    if not apple_chip:
        return {"offered": False, "reason": "intel_mac"}
    tier = None
    for candidate in sorted(mac["tiers"], key=lambda t: t["min_ram_gb"]):
        if ram_gb >= candidate["min_ram_gb"]:
            tier = candidate
    if tier is None:
        return {"offered": False, "reason": "low_ram"}
    return {
        "offered": True,
        "reason": "",
        "tier": tier["id"],
        "model": tier["model"],
        "context": tier["context"],
        "kv_cache": tier["kv_cache"],
        "voice_device": "mps",
        "history_default": int(tier["history_default"]),
        "vram_need_gb": float(tier["memory_need_gb"]),
        "warning": False,
        "build": mac["build"],
    }


def choose(table: dict, vendor: str | None, vram_gb: int, ram_gb: int,
           driver: str = "", cuda: str = "", cc: str = "") -> dict:
    """The installer's decision for this hardware.

    vendor: "nvidia" (driver tool working) / "amd" / "intel" /
    "nvidia-unverified" (registry only - treated like amd/intel) / None (no
    separate graphics card).
    ram_gb: normal memory in whole GB, or a negative number when unknown
    (then the memory rule is not applied).
    driver: the NVIDIA driver version nvidia-smi reported ("610.88").
    cuda: the CUDA version nvidia-smi's header reported ("13.4" = the newest
    CUDA the driver can run, "" = unreadable).
    cc: the card's compute capability ("7.5"; "" = unknown) - a build with
    native code for it can run even on a driver with an older CUDA version.
    Returns {"offered": bool, "reason": str, and when offered: tier, model,
    context, kv_cache, voice_device, history_default, vram_need_gb, warning,
    build}.
    """
    if not vendor or vram_gb < table["dedicated_card_min_gb"]:
        return {"offered": False, "reason": "no_card"}
    tier = None
    for candidate in sorted(table["tiers"], key=lambda t: t["min_vram_gb"]):
        if vram_gb >= candidate["min_vram_gb"]:
            tier = candidate
    if tier is None:
        # A separate card, but smaller than the smallest tier (8 GB since
        # tiers v5): the models that fit did not sound like her.
        return {"offered": False, "reason": "small_card"}
    # Her voice engine can use only NVIDIA cards; on any other card it runs
    # on the processor, which needs plenty of normal memory.
    voice = tier["voice_device"] if vendor == "nvidia" else "cpu"
    if voice == "cpu" and 0 <= ram_gb < table["voice_on_cpu_min_ram_gb"]:
        return {"offered": False, "reason": "low_ram"}
    return {
        "offered": True,
        "reason": "",
        "tier": tier["id"],
        "model": tier["model"],
        "context": tier["context"],
        "kv_cache": tier["kv_cache"],
        "voice_device": voice,
        "history_default": int(tier["history_default"]),
        "vram_need_gb": float(tier["vram_need_gb"]),
        "warning": bool(tier["warning"]),
        "build": pick_build(table, vendor, driver, None, cuda, cc),
    }


def choose_linux(table: dict, vendor: str | None, vram_gb: int, ram_gb: int,
                 driver: str = "", x64: bool = True, cuda: str = "",
                 cc: str = "") -> dict:
    """The Linux installer's decision (scripts/install_linux.sh pc_choose
    follows the same rules; the tests run both on the same inputs).

    The Windows rules (choose) with Linux's llama.cpp builds, plus two Linux
    cases: "nvidia-nodriver" (the kernel lists an NVIDIA card but NVIDIA's
    driver is missing, so nothing can use it yet) and a processor that is
    not 64-bit Intel/AMD (x64 False: no llama.cpp graphics builds).
    cuda: the CUDA version nvidia-smi's header reported ("" = unreadable).
    cc: the card's compute capability ("7.5"; "" = unknown).
    """
    if not x64:
        return {"offered": False, "reason": "not_x64"}
    if vendor == "nvidia-nodriver":
        return {"offered": False, "reason": "nvidia_no_driver"}
    choice = choose(table, vendor, vram_gb, ram_gb, driver, cuda, cc)
    if choice["offered"]:
        choice["build"] = pick_build(table, vendor, driver, table["linux"]["builds"], cuda, cc)
    return choice

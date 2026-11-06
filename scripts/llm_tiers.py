"""Which built-in local model (and settings) a PC gets.

The table lives in scripts/local_llm_tiers.json. The installer picks a tier
with the same rules in PowerShell (Select-LlmTier in install_windows.ps1);
tests/test_llm_tiers.py runs both on the same inputs so they never disagree.
The launcher uses this module for its -SimulateVramGB test option.
"""
from __future__ import annotations

import json
import math
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


def pick_build(table: dict, vendor: str | None, driver: str = "") -> str:
    """Which llama.cpp build: the newest CUDA build the NVIDIA driver can
    run, else Vulkan (AMD / Intel / old or unreadable NVIDIA driver)."""
    if vendor == "nvidia":
        major = driver_major(driver)
        cuda = [(b["min_nvidia_driver"], name)
                for name, b in table["llama_cpp"]["builds"].items()
                if "min_nvidia_driver" in b]
        for need, name in sorted(cuda, reverse=True):
            if major >= need:
                return name
    return "vulkan"


def download_bytes(table: dict, model_key: str, build: str) -> int:
    """Everything option A downloads: model + its vision file + llama.cpp files."""
    model = table["models"][model_key]
    files = table["llama_cpp"]["builds"][build]["files"]
    vision = (model.get("mmproj") or {}).get("bytes", 0)
    return model["bytes"] + vision + sum(f["bytes"] for f in files)


def choose(table: dict, vendor: str | None, vram_gb: int, ram_gb: int,
           driver: str = "") -> dict:
    """The installer's decision for this hardware.

    vendor: "nvidia" (driver tool working) / "amd" / "intel" /
    "nvidia-unverified" (registry only - treated like amd/intel) / None (no
    separate graphics card).
    ram_gb: normal memory in whole GB, or a negative number when unknown
    (then the memory rule is not applied).
    driver: the NVIDIA driver version nvidia-smi reported ("610.88").
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
        "build": pick_build(table, vendor, driver),
    }

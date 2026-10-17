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


def choose(table: dict, vendor: str | None, vram_gb: int, ram_gb: int) -> dict:
    """The installer's decision for this hardware.

    vendor: "nvidia" (driver tool working) / "amd" / "intel" /
    "nvidia-unverified" (registry only - treated like amd/intel) / None (no
    separate graphics card).
    ram_gb: normal memory in whole GB, or a negative number when unknown
    (then the memory rule is not applied).
    Returns {"offered": bool, "reason": str, and when offered: tier, model,
    context, kv_cache, voice_device, warning, build}.
    """
    if not vendor or vram_gb < table["dedicated_card_min_gb"]:
        return {"offered": False, "reason": "no_card"}
    tier = None
    for candidate in sorted(table["tiers"], key=lambda t: t["min_vram_gb"]):
        if vram_gb >= candidate["min_vram_gb"]:
            tier = candidate
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
        "warning": bool(tier["warning"]),
        "build": "cuda" if vendor == "nvidia" else "vulkan",
    }

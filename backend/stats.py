"""Extensible key-value stat store for Amadeus.

Adding a new stat in the future = add ONE dict to STAT_DEFS below. Persistence,
clamping, the settings display, and the tier mapping all become automatic.

- Stats are GLOBAL (shared across every chat session) and live in data/stats.db.
- A stat flagged hidden=True is internal (e.g. counters) and is not shown in Settings.
- A stat may define "tiers": a list of (lo, hi, label) used for display and for
  steering her speech register (see ja_voice.py).
"""
import os
import sqlite3
from datetime import datetime, timezone

DATA_DIR = "data"
PATH_TO_STATS = os.path.join(DATA_DIR, "stats.db")

STAT_DEFS = [
    {
        "key": "trust",
        "label": "Trust",
        "min": 0,
        "max": 100,
        "default": 10,
        "hidden": False,
        "description": (
            "How close and comfortable Amadeus feels with you. Shared across all chats. "
            "It changes only a little at a time: warm, friendly conversations slowly raise it "
            "a few points, tense or rude ones lower it. Real closeness builds over many messages. "
            "Levels: Distant > Cautious > Warming up > Familiar > Close. "
            "Higher levels unlock warmer, more playful conversation."
        ),
        "tiers": [
            (0, 19, "Distant"),
            (20, 39, "Cautious"),
            (40, 59, "Warming up"),
            (60, 79, "Familiar"),
            (80, 100, "Close"),
        ],
    },
    # Internal: user messages since the last trust re-score. Not shown in Settings.
    {
        "key": "_user_msg_counter",
        "label": "Messages since last re-score",
        "min": 0,
        "max": 10 ** 9,
        "default": 0,
        "hidden": True,
        "description": "Internal bookkeeping; not shown.",
    },
    # Future stats go here, e.g.
    # {"key": "familiarity", "label": "Familiarity", "min": 0, "max": 100, "default": 0, "hidden": False, "description": "..."},
]

_BY_KEY = {d["key"]: d for d in STAT_DEFS}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _conn():
    os.makedirs(os.path.dirname(PATH_TO_STATS), exist_ok=True)
    conn = sqlite3.connect(PATH_TO_STATS)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS stats (" "key TEXT PRIMARY KEY, value REAL NOT NULL, updated_at TEXT)"
    )
    conn.commit()
    return conn


def load_stat(key: float):
    d = _BY_KEY.get(key)
    if d is None:
        raise KeyError(key)
    conn = _conn()
    row = conn.execute("SELECT value FROM stats WHERE key = ?", (key,)).fetchone()
    conn.close()
    return float(row[0]) if row else float(d["default"])


def set_stat(key, value) -> float:
    """Store a stat, clamped to its [min, max]."""
    d = _BY_KEY.get(key)
    if d is None:
        raise KeyError(key)
    v = max(float(d["min"]), min(float(d["max"]), float(value)))
    conn = _conn()
    conn.execute(
        "INSERT INTO stats (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, v, _now()),
    )
    conn.commit()
    conn.close()
    return v


def adjust_stat(key, delta) -> float:
    """Move a stat by delta, clamped to its [min, max]."""
    return set_stat(key, load_stat(key) + delta)


def tier_label(key, value=None):
    d = _BY_KEY.get(key)
    if not d or "tiers" not in d:
        return None
    if value is None:
        value = load_stat(key)
    for lo, hi, name in d["tiers"]:
        if lo <= value <= hi:
            return name
    return d["tiers"][-1][2]


def all_stats(include_hidden: bool = False):
    out = []
    for d in STAT_DEFS:
        if d.get("hidden") and not include_hidden:
            continue
        v = load_stat(d["key"])
        item = {
            "key": d["key"],
            "label": d["label"],
            "value": v,
            "min": d["min"],
            "max": d["max"],
            "description": d.get("description", ""),
        }
        t = tier_label(d["key"], v)
        if t:
            item["tier"] = t
        out.append(item)
    return out

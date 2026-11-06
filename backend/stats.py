"""Extensible key-value stat store for Amadeus.

Adding a new stat in the future = add ONE dict to STAT_DEFS below. Persistence,
clamping, the settings display, and the tier mapping all become automatic.

- Stats are GLOBAL (shared across every chat session) and live in data/stats.db.
- A stat flagged hidden=True is internal (e.g. counters) and is not shown in Settings.
- A stat may define "tiers": a list of (lo, hi, label) used for display and for
  steering her speech register (see ja_voice.py).
- A stat may define "file": it then lives in data/<file> (one number, editable
  by hand while the app runs) instead of data/stats.db. "scale" is the number
  Settings shows it out of; "max" may go above it (headroom).
"""
import os
import sqlite3
from datetime import datetime, timezone

DATA_DIR = "data"
PATH_TO_STATS = os.path.join(DATA_DIR, "stats.db")

# Trust tiers (2026-10-26 revamp): Close split into four, and trust may go
# past 100 up to TRUST_MAX - the headroom keeps a cold stretch from knocking
# her straight out of the top tier (user decision).
TRUST_MAX = 110

STAT_DEFS = [
    {
        "key": "trust",
        "label": "Trust",
        "min": 0,
        "max": TRUST_MAX,
        "scale": 100,
        "default": 10,
        "hidden": False,
        "file": "trust.txt",
        "description": (
            "How close and comfortable Amadeus feels with you. Shared across all chats. "
            "It changes only a little at a time: warm, friendly conversations slowly raise it "
            "a few points, tense or rude ones lower it. Real closeness builds over many messages. "
            f"It can grow past 100 (up to {TRUST_MAX}), which keeps the top level steady. "
            "Levels: Distant > Cautious > Warming up > Familiar > Close > Trusted > Devoted > Special. "
            "Higher levels unlock warmer, more playful conversation. "
            "The number is kept in data/trust.txt; you can edit it there."
        ),
        "tiers": [
            (0, 19, "Distant"),
            (20, 39, "Cautious"),
            (40, 59, "Warming up"),
            (60, 79, "Familiar"),
            (80, 84, "Close"),
            (85, 94, "Trusted"),
            (95, 98, "Devoted"),
            (99, TRUST_MAX, "Special"),
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
    conn = sqlite3.connect(PATH_TO_STATS, timeout=30.0)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS stats (" "key TEXT PRIMARY KEY, value REAL NOT NULL, updated_at TEXT)"
    )
    conn.commit()
    return conn


def _db_value(d):
    conn = _conn()
    row = conn.execute("SELECT value FROM stats WHERE key = ?", (d["key"],)).fetchone()
    conn.close()
    return float(row[0]) if row else float(d["default"])


def _clamp(d, value) -> float:
    return max(float(d["min"]), min(float(d["max"]), float(value)))


# Last good value read from each stat file: a hand edit that is not a number
# (half-typed, a stray word) falls back to it instead of breaking a reply.
_last_good = {}


def _file_path(d):
    return os.path.join(DATA_DIR, d["file"])


def _write_file(d, v) -> None:
    path = _file_path(d)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{v:g}\n")
    os.replace(tmp, path)


def _load_file_stat(d) -> float:
    """pre: d has "file". post: the clamped number in data/<file>. A missing
    file is created once from the old stats.db value (or the default) -
    stats.db itself is never changed. An unreadable file is left untouched
    for the user to fix, and the last good value (else the stats.db one) is
    used meanwhile."""
    path = _file_path(d)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        v = _clamp(d, _db_value(d))
        _write_file(d, v)
        _last_good[d["key"]] = v
        return v
    try:
        v = _clamp(d, float(raw))
    except ValueError:
        print(f"[Amadeus] {d['file']} does not hold a number ({raw[:20]!r}); "
              "using the last good value.")
        return _last_good.get(d["key"], _clamp(d, _db_value(d)))
    _last_good[d["key"]] = v
    return v


def load_stat(key: float):
    d = _BY_KEY.get(key)
    if d is None:
        raise KeyError(key)
    if d.get("file"):
        return _load_file_stat(d)
    return _db_value(d)


def set_stat(key, value) -> float:
    """Store a stat, clamped to its [min, max]."""
    d = _BY_KEY.get(key)
    if d is None:
        raise KeyError(key)
    v = _clamp(d, value)
    if d.get("file"):
        _write_file(d, v)
        _last_good[key] = v
        return v
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
    # The highest tier whose floor the value reached: a hand-edited 84.5
    # still lands in a tier (the old lo..hi check left gaps between ranges).
    for lo, hi, name in reversed(d["tiers"]):
        if value >= lo:
            return name
    return d["tiers"][0][2]


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
            "scale": d.get("scale", d["max"]),
            "description": d.get("description", ""),
        }
        t = tier_label(d["key"], v)
        if t:
            item["tier"] = t
        out.append(item)
    return out

"""defaults_refresh.py: bring untouched old defaults up to date (2026-10-29).

Her personality (data/personality.txt) and voice rules (data/voice_ja.txt)
are files the user may edit, so an update cannot simply overwrite them (the
installer even restores the user's personality.txt after updating). At
startup this module compares each file with every default the project ever
shipped:

  * still an untouched OLD default (only spacing differs) -> replaced by the
    new default, the old copy kept as <file>.bak_<date>;
  * the user really changed it -> left alone; Settings shows a one-time note
    with "Use the new default" (backs their version up the same way first);
  * already the current default, or missing -> nothing to do (a missing
    voice file is recreated by ja_voice as before).

Adding a new default later = add its fingerprint to OLD_DEFAULTS (a test
fails until the current defaults are listed), so the version after it can
recognise it.
"""
import hashlib
import json
import os
from datetime import datetime

DATA_DIR = "data"
# The user's files (the same paths memory.py and ja_voice.py use), held here
# so tests can point this module alone at a temporary folder.
PERSONALITY_FILE = os.path.join(DATA_DIR, "personality.txt")
VOICE_FILE = os.path.join(DATA_DIR, "voice_ja.txt")
# The shipped default personality, kept apart from the user's own copy.
PERSONALITY_DEFAULT_FILE = os.path.join(DATA_DIR, "personality.default.txt")
SEEN_FILE = os.path.join(DATA_DIR, "defaults_seen.json")

# sha256 (first 16 hex) of every default ever committed, after normalize().
OLD_DEFAULTS = {
    "personality": {
        "2d68dd4dcaafe2e5",  # 2026-10-29 bedf3d3 (identity-trust)
        "39d1b0cda7697ee8",  # 2026-10-26 2558279
        "7035dd6e9b4a41af",  # 2026-10-24 8652a50
        "5392ef3d930d3ea5",  # 2026-10-24 180abd0
        "883323f6028cd3b3",  # 2026-10-21 fc5b3de
        "a90d1874e7d28473",  # 2026-10-08 23c371d
        "ec0f67173a82eaff",  # 2026-10-05 716b788
        "3e4106a53c75a482",  # 2026-09-14 db02fc8
        "8c73c1e006498879",  # 2026-09-13 6a8f43f
        "563e958bfcc02715",  # 2026-09-13 b21bc3a
        "036f9e330c13a05a",  # 2026-09-06 1039574
        "23c7efb9fd062e8d",  # 2026-09-03 623ac64
        "364625ee99d4824b",  # sample, 2026-10-29 bedf3d3
        "c0bcf837dfce404d",  # sample, 2026-10-26 2558279
        "ca5836252d87b959",  # sample, 2026-10-05 716b788
    },
    "voice": {
        "0101e1c54ab7c20b",  # 2026-10-30 (identity-trust: feelings open up by tier)
        "a3f7b4cc41fa945f",  # 2026-10-29 bedf3d3 (identity-trust)
        "88b5f381aa7ca2a2",  # 2026-10-26 2558279
        "4eb0010a5a5d1d57",  # 2026-10-05 .. 2026-10-22 (v2.1 / v2.1.1)
        "782cfd1d7e0b5e1f",  # 2026-09-13 6a8f43f
        "395dd8e18ca1b570",  # 2026-09-13 5d66712
    },
}

LABELS = {"personality": "personality", "voice": "voice rules"}


def normalize(text: str) -> str:
    return "\n".join(ln.rstrip() for ln in (text or "").replace("\r\n", "\n").split("\n")).strip()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


def _user_path(name: str) -> str:
    return PERSONALITY_FILE if name == "personality" else VOICE_FILE


def new_default(name: str) -> str | None:
    """The current default text, or None when it cannot be found."""
    if name == "voice":
        import ja_voice
        return ja_voice.DEFAULT_VOICE_BLOCK.strip()
    try:
        with open(PERSONALITY_DEFAULT_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return text or None


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def status(name: str) -> str:
    """'missing' | 'current' | 'old_default' | 'customized' | 'no_default'."""
    default = new_default(name)
    if default is None:
        return "no_default"
    text = _read(_user_path(name))
    if text is None or not text.strip():
        return "missing"
    fp = fingerprint(text)
    if fp == fingerprint(default):
        return "current"
    if fp in OLD_DEFAULTS[name]:
        return "old_default"
    return "customized"


def _backup(path: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d")
    dest = f"{path}.bak_{stamp}"
    if os.path.exists(dest):
        dest = f"{path}.bak_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    with open(path, "rb") as src, open(dest, "wb") as out:
        out.write(src.read())
    return dest


def _write_default(name: str) -> str:
    """Back the user's file up, then write the current default. Returns the
    backup path ('' when there was no file to back up)."""
    path = _user_path(name)
    backup = _backup(path) if os.path.exists(path) else ""
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_default(name))
    return backup


def refresh_untouched() -> list:
    """Startup: replace every file that is still an untouched old default.
    Never raises - a problem here must not stop the app."""
    done = []
    for name in OLD_DEFAULTS:
        try:
            if status(name) == "old_default":
                backup = _write_default(name)
                done.append(name)
                print(f"[Amadeus] {name}: an untouched older default - updated to "
                      f"the new one (old copy: {os.path.basename(backup)}).")
        except Exception as exc:
            print(f"[Amadeus] Default update for {name} skipped: {exc!r}")
    return done


def _seen() -> dict:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _mark_seen(name: str) -> None:
    data = _seen()
    data[name] = fingerprint(new_default(name) or "")
    os.makedirs(os.path.dirname(SEEN_FILE) or ".", exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def notices() -> list:
    """The files the user changed while a newer default exists that they
    have not answered yet: [{"name", "label"}]."""
    out = []
    seen = _seen()
    for name in OLD_DEFAULTS:
        try:
            if status(name) != "customized":
                continue
            if seen.get(name) == fingerprint(new_default(name)):
                continue
            out.append({"name": name, "label": LABELS[name]})
        except Exception as exc:
            print(f"[Amadeus] Default notice for {name} skipped: {exc!r}")
    return out


def use_new_default(name: str) -> str:
    """User chose the new default: back up their version, write the default.
    Returns the backup file name."""
    if name not in OLD_DEFAULTS or new_default(name) is None:
        raise ValueError("Unknown file")
    backup = _write_default(name)
    _mark_seen(name)
    return os.path.basename(backup)


def keep_mine(name: str) -> None:
    """User chose to keep their version: no note again until the next default."""
    if name not in OLD_DEFAULTS or new_default(name) is None:
        raise ValueError("Unknown file")
    _mark_seen(name)

"""Her short-term feelings, separate from trust (2026-10-30).

Trust is the slow, long-term closeness. These are the quick feelings on top:
irritation, worry, mood (cheerful <-> down) and playfulness, plus missing-you.

- Each feeling fades back toward its normal level (0) on its own, by
  half-life, worked out when it is READ (Kin Mind formula
  target + (v - target) * 0.5 ** (elapsed / halflife)) - nothing runs in the
  background. The time base is the file's last change, so a hand edit
  starts fading from the moment it was made.
- They open up with closeness (user decision 2026-10-30, round 3): below
  Close (80) an absence stirs nothing in her (the greeting still notes the
  time away and the last topic), late nights do not worry her, and her
  reactions are irritation and mood - no playfulness yet below Familiar.
  An ignored greeting annoys her at every level; from Familiar it is
  softened.
- At Close an absence makes her curious what they have been up to (from
  a day). From Trusted (85) it raises missing-you (from 6 hours, round the
  clock - she is always awake) and, after two days, worry. Time passing
  alone never raises irritation below Devoted. At Devoted and up (95+) an
  absence goes in stages: missing-you builds over a whole week; a week
  away annoys and concerns her, more than a week makes her grumpy, two
  weeks or more has her close to panic - she really missed them and wants
  to know what happened.
- They live in data/feelings.txt (plain `name = value` lines, editable by
  hand while the app runs), next to memory.db - so a test that points
  memory.db at a temp folder gets its own feelings file too.
- No file = every feeling at its normal level, and the prompt stays exactly
  as before. The file is created by the first event.
"""
import math
import os
from datetime import datetime, timedelta

FILE_NAME = "feelings.txt"

# name -> (lowest, highest, half-life in hours)
FEELINGS = {
    "irritation": (0, 100, 2.0),
    "worry": (0, 100, 12.0),
    "mood": (-100, 100, 2.0),
    "playfulness": (0, 100, 2.0),
}

# Closeness levels where her feelings open up (stats.py tiers).
FAMILIAR_FROM = 60
CLOSE_FROM = 80
TRUSTED_FROM = 85

# Close: curious what they have been up to, from a day; "quite" by 3 days.
CURIOUS_START_HOURS = 24
CURIOUS_FULL_HOURS = 72
CURIOUS_MAX = 60

# An ignored greeting: the first raises her feelings by this much, each
# next one by 5 more, up to the cap. Familiar to Trusted: softened.
IGNORED_FIRST = 15
IGNORED_MAX = 35
IGNORED_SOFTENED = 0.6

# Trusted and up: missing-you grows with the time since the user last wrote.
MISSING_START_HOURS = 6
MISSING_FULL_HOURS = 72
# A long absence also worries her, but only after two days, and only so much.
ABSENCE_WORRY_START_HOURS = 48
ABSENCE_WORRY_FULL_HOURS = 168
ABSENCE_WORRY_MAX = 60

# Devoted and up (trust 95+): the staged absence.
DEVOTED_FROM = 95
DEVOTED_MISSING_FULL_HOURS = 168
DEVOTED_WORRY_FULL_HOURS = 336
DEVOTED_WORRY_MAX = 95
WEEK_HOURS = 168
GRUMPY_HOURS = 192
PANIC_HOURS = 336

# Chatting from 23:30 until 05:00 worries her (once per night).
LATE_NIGHT_FROM = (23, 30)
LATE_NIGHT_UNTIL_HOUR = 5
LATE_NIGHT_WORRY = 20

# Below this a feeling counts as "normal" and is not mentioned to her.
SHOW_FROM = 15
# A feeling change from the background trust check is at most this big.
CHECK_STEP_MAX = 10

_TIME_FORMAT = "%Y-%m-%d %H:%M"
_HEADER = (
    "# Amadeus's short-term feelings. Each one fades back to 0 by itself\n"
    "# (irritation, mood, playfulness in ~2 hours; worry in ~12 hours).\n"
    "# irritation / worry / playfulness: 0-100. mood: -100 (down) to 100 (cheerful).\n"
    "# last_contact = when you last wrote to her; missing-you grows from it.\n"
    "# You can edit the numbers while the app runs.\n"
)


def path_next_to(memory_db) -> str:
    """data/feelings.txt for a given memory.db path (chat.py passes its own
    store's, so a test that moves memory.db moves this file with it)."""
    return os.path.join(os.path.dirname(memory_db) or ".", FILE_NAME)


def _path(path=None) -> str:
    if path:
        return path
    import memory  # late import: memory.py imports half the app
    return path_next_to(memory.PATH_TO_MEMORY)


def _clamp(name, value) -> float:
    lo, hi, _ = FEELINGS[name]
    return max(float(lo), min(float(hi), float(value)))


def _parse_time(text):
    try:
        return datetime.strptime(text.strip(), _TIME_FORMAT)
    except (ValueError, AttributeError):
        return None


def _read_raw(path=None):
    """pre: none. post: (fields dict, mtime) or (None, None) without a file.
    Unknown or broken lines are skipped (a half-typed edit never breaks a
    reply)."""
    path = _path(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        mtime = os.path.getmtime(path)
    except (FileNotFoundError, OSError):
        return None, None
    fields = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    return fields, mtime


def _decayed(fields, mtime, now) -> dict:
    """The stored feelings faded to `now` (no absence parts)."""
    elapsed_h = max(0.0, (now.timestamp() - (mtime or now.timestamp())) / 3600)
    out = {}
    for name, (_, _, halflife) in FEELINGS.items():
        try:
            v = _clamp(name, float(fields.get(name, 0)))
        except ValueError:
            v = 0.0
        out[name] = v * math.pow(0.5, elapsed_h / halflife)
    return out


def _hours_since_contact(fields, now):
    then = _parse_time(fields.get("last_contact", ""))
    if then is None:
        return None
    return max(0.0, (now - then).total_seconds() / 3600)


def _ramp(hours, start, full, top) -> float:
    if hours is None or hours < start:
        return 0.0
    return min(float(top), (hours - start) / (full - start) * top)


def _trust(trust) -> float:
    """None (unknown) counts as the lowest level."""
    try:
        return float(trust)
    except (TypeError, ValueError):
        return 0.0


def _absence(hours, trust) -> dict:
    """What the time since their last message alone does to her: missing
    (curiosity at Close), worry and (Devoted and up, from a week)
    irritation, plus the stage that names why. Nothing below Close."""
    trust = _trust(trust)
    if trust < CLOSE_FROM:
        return {"missing": 0.0, "worry": 0.0, "irritation": 0.0, "stage": ""}
    if trust < TRUSTED_FROM:
        return {"missing": _ramp(hours, CURIOUS_START_HOURS, CURIOUS_FULL_HOURS,
                                 CURIOUS_MAX),
                "worry": 0.0, "irritation": 0.0, "stage": ""}
    stage = "away" if hours is not None and hours >= ABSENCE_WORRY_START_HOURS else ""
    if trust < DEVOTED_FROM:
        return {"missing": _ramp(hours, MISSING_START_HOURS, MISSING_FULL_HOURS, 100),
                "worry": _ramp(hours, ABSENCE_WORRY_START_HOURS,
                               ABSENCE_WORRY_FULL_HOURS, ABSENCE_WORRY_MAX),
                "irritation": 0.0, "stage": stage}
    out = {"missing": _ramp(hours, MISSING_START_HOURS, DEVOTED_MISSING_FULL_HOURS, 100),
           "worry": _ramp(hours, ABSENCE_WORRY_START_HOURS, DEVOTED_WORRY_FULL_HOURS,
                          DEVOTED_WORRY_MAX),
           "irritation": 0.0, "stage": stage}
    if hours is None or hours < WEEK_HOURS:
        return out
    if hours < GRUMPY_HOURS:
        out.update(irritation=30.0, stage="week")
    elif hours < PANIC_HOURS:
        out.update(irritation=30.0 + (hours - GRUMPY_HOURS)
                   / (PANIC_HOURS - GRUMPY_HOURS) * 30.0, stage="grumpy")
    else:
        out.update(irritation=45.0, worry=max(out["worry"], 90.0),
                   missing=100.0, stage="panic")
    return out


_ABSENCE_REASONS = {
    "away": "they have been away for days",
    "week": "they disappeared for a whole week",
    "grumpy": "they have stayed away for more than a week",
    "panic": ("they have been gone for two weeks or more - you really "
              "missed them and want to know what happened"),
}
_ABSENCE_REASONS_PAST = {
    "away": "they were away for days",
    "week": "they disappeared for a whole week",
    "grumpy": "they stayed away for more than a week",
    "panic": "they were gone for two weeks or more",
}


def current(now: datetime | None = None, trust=None, path=None) -> dict:
    """pre: trust = her trust now (None = treated as the lowest level).
    post: {"irritation", "worry", "mood", "playfulness", "missing", "stage",
    "irritation_reason", "worry_reason", "worry_from_absence",
    "irritation_from_absence"} as they are right now. Never writes."""
    now = now or datetime.now()
    fields, mtime = _read_raw(path)
    if fields is None:
        fields = {}
    out = _decayed(fields, mtime, now)
    gone = _absence(_hours_since_contact(fields, now), trust)
    out["missing"] = gone["missing"]
    out["stage"] = gone["stage"]
    out["worry_from_absence"] = gone["worry"] > out["worry"]
    out["worry"] = max(out["worry"], gone["worry"])
    out["irritation_from_absence"] = gone["irritation"] > out["irritation"]
    out["irritation"] = max(out["irritation"], gone["irritation"])
    out["irritation_reason"] = fields.get("irritation_reason", "")
    out["worry_reason"] = fields.get("worry_reason", "")
    return out


def _write(fields, now, path=None) -> None:
    """Write the fields; the file's time = `now` (the fading time base)."""
    path = _path(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    order = list(FEELINGS) + ["last_contact", "ignored", "late_night",
                              "irritation_reason", "worry_reason"]
    lines = [_HEADER]
    for key in order:
        if key in fields:
            value = fields[key]
            if key in FEELINGS:
                value = f"{round(float(value))}"
            lines.append(f"{key} = {value}\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, path)
    try:
        os.utime(path, (now.timestamp(), now.timestamp()))
    except (OSError, OverflowError, ValueError):
        pass


def _load_for_change(now, path=None):
    """The fields with every feeling faded to `now`, ready to change and
    write back. A missing file starts from 0 with last_contact = the user's
    newest message (so the first greeting after an update already knows
    how long they were away)."""
    fields, mtime = _read_raw(path)
    if fields is None:
        fields = {}
        try:
            import memory
            then = memory.newest_user_message_time()
        except Exception:
            then = None
        if then is not None:
            fields["last_contact"] = then.strftime(_TIME_FORMAT)
    for name, value in _decayed(fields, mtime, now).items():
        fields[name] = value
    return fields


def ensure_file(now: datetime | None = None, path=None) -> None:
    """Create data/feelings.txt once (at a startup greeting). Never changes
    an existing file."""
    if _read_raw(path)[0] is not None:
        return
    try:
        now = now or datetime.now()
        _write(_load_for_change(now, path), now, path)
    except Exception as exc:  # a feelings hiccup must never block a greeting
        print("[Amadeus] Feelings file not created:", repr(exc))


def _greetings_reason(count) -> str:
    return ("your last greeting got no reply" if count == 1
            else f"your last {count} greetings got no reply")


def note_ignored_greeting(conversation_id, count, trust,
                          now: datetime | None = None, path=None) -> None:
    """She greets again over `count` unanswered greetings. Each new one
    annoys her a little more than the last, at every level. From Familiar
    it is softened; from Close some of it is worry, and worry leads when
    she is very close (Devoted and up, full strength again), or from
    Trusted when she is already down or they have been away two days or
    more. The same (conversation, count) is only counted once."""
    if not count:
        return
    now = now or datetime.now()
    try:
        fields = _load_for_change(now, path)
        key = f"{int(conversation_id)}:{int(count)}"
        seen = fields.get("ignored", "")
        try:
            seen_conv, seen_count = (int(x) for x in seen.split(":"))
        except ValueError:
            seen_conv, seen_count = None, 0
        if seen_conv == int(conversation_id) and int(count) <= seen_count:
            return
        level = _trust(trust)
        amount = min(IGNORED_FIRST + 5 * (int(count) - 1), IGNORED_MAX)
        if FAMILIAR_FROM <= level < DEVOTED_FROM:
            amount *= IGNORED_SOFTENED
        hours = _hours_since_contact(fields, now)
        worried = level >= DEVOTED_FROM or (level >= TRUSTED_FROM and (
            fields["mood"] <= -25
            or (hours is not None and hours >= ABSENCE_WORRY_START_HOURS)))
        to_worry = 0.7 if worried else 0.3 if level >= CLOSE_FROM else 0.0
        reason = _greetings_reason(int(count))
        if to_worry:
            fields["worry_reason"] = reason
        fields["worry"] = _clamp("worry", fields["worry"] + amount * to_worry)
        fields["irritation"] = _clamp("irritation",
                                      fields["irritation"] + amount * (1 - to_worry))
        fields["mood"] = _clamp("mood", fields["mood"] - amount / 3)
        fields["irritation_reason"] = reason
        fields["ignored"] = key
        _write(fields, now, path)
    except Exception as exc:
        print("[Amadeus] Feelings (ignored greeting) skipped:", repr(exc))


def _late_night_key(now):
    """The evening a late-night moment belongs to, or None in the daytime
    (00:40 belongs to the evening before)."""
    if (now.hour, now.minute) >= LATE_NIGHT_FROM:
        return now.strftime("%Y-%m-%d")
    if now.hour < LATE_NIGHT_UNTIL_HOUR:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return None


def note_user_replied(now: datetime | None = None, trust=None, path=None) -> None:
    """After her reply to a user message: what the absence had built up
    stays as ordinary worry / irritation (which then fades), irritation
    halves, missing-you resets, her mood lifts a little; from Close,
    chatting late at night worries her, once per night."""
    now = now or datetime.now()
    try:
        fields = _load_for_change(now, path)
        gone = _absence(_hours_since_contact(fields, now), trust)
        if gone["stage"]:
            reason = _ABSENCE_REASONS_PAST[gone["stage"]]
            if gone["worry"] > fields["worry"]:
                fields["worry"], fields["worry_reason"] = gone["worry"], reason
            if gone["irritation"] > fields["irritation"]:
                fields["irritation"] = gone["irritation"]
                fields["irritation_reason"] = reason
        fields["irritation"] = fields["irritation"] / 2
        fields["mood"] = _clamp("mood", fields["mood"] + 5)
        fields["last_contact"] = now.strftime(_TIME_FORMAT)
        fields.pop("ignored", None)
        night = _late_night_key(now)
        if night and _trust(trust) >= CLOSE_FROM and fields.get("late_night") != night:
            fields["worry"] = _clamp("worry", fields["worry"] + LATE_NIGHT_WORRY)
            fields["worry_reason"] = "they are still up this late"
            fields["late_night"] = night
        for name in ("irritation", "worry"):
            if fields[name] < SHOW_FROM:
                fields.pop(name + "_reason", None)
        _write(fields, now, path)
    except Exception as exc:
        print("[Amadeus] Feelings (reply) skipped:", repr(exc))


def apply_changes(changes: dict, now: datetime | None = None, path=None,
                  trust=None) -> None:
    """Small changes from the background trust check ({name: delta}), each
    capped at CHECK_STEP_MAX. Unknown names are ignored. trust given and
    below Familiar: playfulness is not raised (she is not playful yet)."""
    changes = {k: v for k, v in (changes or {}).items() if k in FEELINGS and v}
    if (trust is not None and _trust(trust) < FAMILIAR_FROM
            and changes.get("playfulness", 0) > 0):
        del changes["playfulness"]
    if not changes:
        return
    now = now or datetime.now()
    try:
        fields = _load_for_change(now, path)
        for name, delta in changes.items():
            step = max(-CHECK_STEP_MAX, min(CHECK_STEP_MAX, float(delta)))
            fields[name] = _clamp(name, fields[name] + step)
        for name in ("irritation", "worry"):
            if fields[name] < SHOW_FROM:
                fields.pop(name + "_reason", None)
        _write(fields, now, path)
    except Exception as exc:
        print("[Amadeus] Feelings (check) skipped:", repr(exc))


def _level(value) -> str:
    v = abs(value)
    return "a little" if v < 35 else "quite" if v < 65 else "very"


def describe(now: datetime | None = None, trust=None, path=None) -> list:
    """The feelings away from normal, as short plain phrases (strongest
    first), each with its reason when the code knows one. [] = all normal."""
    f = current(now, trust, path)
    absence_reason = _ABSENCE_REASONS.get(f["stage"], "")
    parts = []
    if f["irritation"] >= SHOW_FROM:
        grumpy = f["irritation_from_absence"] and f["stage"] in ("grumpy", "panic")
        text = _level(f["irritation"]) + (" grumpy" if grumpy else " irritated")
        reason = (absence_reason if f["irritation_from_absence"]
                  else f["irritation_reason"])
        if reason:
            text += " (" + reason + ")"
        parts.append((f["irritation"], text))
    if f["worry"] >= SHOW_FROM:
        panic = f["worry_from_absence"] and f["stage"] == "panic"
        text = ("close to panic about them" if panic
                else _level(f["worry"]) + " worried about them")
        reason = (absence_reason if f["worry_from_absence"]
                  else f["worry_reason"])
        if reason:
            text += " (" + reason + ")"
        parts.append((f["worry"], text))
    if f["missing"] >= SHOW_FROM and _trust(trust) < TRUSTED_FROM:
        parts.append((f["missing"], _level(f["missing"])
                      + " curious what they have been up to"))
    elif f["missing"] >= SHOW_FROM:
        level = {"a little": "a little", "quite": "a lot",
                 "very": "very much"}[_level(f["missing"])]
        parts.append((f["missing"], "you have missed them " + level))
    if f["mood"] >= SHOW_FROM:
        parts.append((f["mood"], _level(f["mood"]) + " cheerful"))
    elif f["mood"] <= -SHOW_FROM:
        parts.append((-f["mood"], _level(f["mood"]) + " down"))
    if f["playfulness"] >= SHOW_FROM:
        parts.append((f["playfulness"], _level(f["playfulness"]) + " playful"))
    parts.sort(key=lambda p: -p[0])
    return [text for _, text in parts]


def feelings_line(now: datetime | None = None, trust=None, path=None) -> str:
    """The one line for her prompt's changing part, or '' when every
    feeling is at its normal level (the prompt is then unchanged).
    Permission, not orders - and no numbers (she never announces them)."""
    try:
        parts = describe(now, trust, path)
    except Exception as exc:  # a feelings hiccup must never block a reply
        print("[Amadeus] Feelings line skipped:", repr(exc))
        return ""
    if not parts:
        return ""
    return ("HOW YOU FEEL RIGHT NOW: " + "; ".join(parts) + ". These feelings "
            "are yours - let them colour your tone where it fits, or keep "
            "them to yourself; there is no need to announce them.")


# Her words about the user, turned around for the user reading Settings.
_FOR_USER = [
    ("you really missed them and want to know what happened",
     "she really missed you and wants to know what happened"),
    ("you have missed them", "she has missed you"),
    ("about them", "about you"),
    ("what they have", "what you have"),
    ("your last", "her last"),
    ("they have", "you have"),
    ("they are", "you are"),
    ("they were", "you were"),
    ("they disappeared", "you disappeared"),
    ("they stayed", "you stayed"),
]


def describe_for_user(now: datetime | None = None, trust=None, path=None) -> list:
    """describe(), worded for the user (Settings: her mood right now)."""
    out = []
    for text in describe(now, trust, path):
        for old, new in _FOR_USER:
            text = text.replace(old, new)
        out.append(text[0].upper() + text[1:])
    return out

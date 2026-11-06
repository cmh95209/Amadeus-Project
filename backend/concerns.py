"""Things on her mind about the user (Kin Mind's 心事, 2026-10-30).

Things the user mentioned that a caring friend would follow up on - an
exam, being ill, skipped meals, an interview. She keeps them until they are
settled (an AI remembers; user decision 2026-10-30):

- open: at most OPEN_SHOWN are put in her prompt (the soonest due / most
  recent first) at every level, so she remembers what they told her
  (user decision 2026-10-30, round 3). Below Close (80) they are only
  background - she uses them when the user brings the subject up or asks;
  from Close she may ask about one herself; at Devoted (95) and up she
  makes a point of asking.
- quiet: she asked twice and the user did not pick it up - kept, but she no
  longer raises it herself. It wakes up when the user mentions it again.
- done: the user answered it, or it is clearly over (the background trust
  check decides) - kept with the date, never deleted by the app.

A concern with a day ("exam on Friday") carries the real date, worked out
by the code, so she knows when it has passed and can ask how it went.
From Close, a startup greeting is shown the open ones and may open with
one if it fits the moment - she chooses, nothing is locked (user decision
2026-10-31: no cooldown; a regenerated greeting may reuse the topic).
Repeats are kept down by the unanswered-greeting note ("start something
different") and by quiet-after-two-asks. Older files may still carry a
"greeted" date; it is read and kept, no longer used.

The list lives in data/concerns.txt next to memory.db: plain lines the user
can edit or delete by hand; Settings shows it with delete buttons.
"""
import difflib
import os
import re
from datetime import date, datetime, timedelta

FILE_NAME = "concerns.txt"
SECTIONS = ("open", "quiet", "done")
OPEN_SHOWN = 3
STORE_MAX = 10           # open + quiet; the oldest quiet one makes room first
QUIET_AFTER_ASKS = 2
ASK_FROM_TRUST = 80      # Close: she may bring them up herself
CARE_FROM_TRUST = 95
DONE_KEPT = 30           # done lines kept in the file (the oldest go first)

_HEADER = (
    "# Things on Amadeus's mind about you - she follows up on them.\n"
    "# One per line: what | added YYYY-MM-DD | due YYYY-MM-DD | asked N\n"
    "# [open] = she may ask; [quiet] = she asked twice, waits for you to bring it up;\n"
    "# [done] = settled. Delete a line, or move it under [done], to settle it yourself.\n"
)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def path_next_to(memory_db) -> str:
    return os.path.join(os.path.dirname(memory_db) or ".", FILE_NAME)


def _path(path=None) -> str:
    if path:
        return path
    import memory  # late import: memory.py imports half the app
    return path_next_to(memory.PATH_TO_MEMORY)


def _parse_date(text):
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _parse_line(line, section) -> dict | None:
    parts = [p.strip() for p in line.split("|")]
    text = parts[0].lstrip("-* ").strip()
    if not text:
        return None
    item = {"text": text, "status": section, "added": None, "due": None,
            "asked": 0, "greeted": None, "done": None}
    for part in parts[1:]:
        key, _, value = part.partition(" ")
        key = key.lower()
        if key in ("added", "due", "greeted", "done"):
            item[key] = _parse_date(value)
        elif key == "asked":
            try:
                item["asked"] = max(0, int(value))
            except ValueError:
                pass
    return item


def load(path=None) -> list:
    """pre: none. post: every concern in file order, as dicts {"text",
    "status", "added", "due", "asked", "greeted", "done"}; [] without a
    file. Lines before any [section] count as open."""
    try:
        with open(_path(path), "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (FileNotFoundError, OSError):
        return []
    section, items = "open", []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.fullmatch(r"\[(\w+)\]", s)
        if m:
            section = m.group(1).lower() if m.group(1).lower() in SECTIONS else "open"
            continue
        item = _parse_line(s, section)
        if item:
            items.append(item)
    return items


def _format(item) -> str:
    out = "- " + item["text"].replace("|", "/")
    for key in ("added", "due"):
        if item.get(key):
            out += f" | {key} {item[key]:%Y-%m-%d}"
    if item.get("asked"):
        out += f" | asked {item['asked']}"
    for key in ("greeted", "done"):
        if item.get(key):
            out += f" | {key} {item[key]:%Y-%m-%d}"
    return out


def save(items, path=None) -> None:
    p = _path(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    done = [i for i in items if i["status"] == "done"][-DONE_KEPT:]
    lines = [_HEADER]
    for section in SECTIONS:
        lines.append(f"\n[{section}]\n")
        group = done if section == "done" else [i for i in items if i["status"] == section]
        lines.extend(_format(i) + "\n" for i in group)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, p)


def active(items) -> list:
    """Open and quiet concerns (the ones the trust check sees, numbered)."""
    return [i for i in items if i["status"] in ("open", "quiet")]


def _similar(a, b) -> bool:
    a, b = a.lower().strip(" ."), b.lower().strip(" .")
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def resolve_day(text, when, today: date) -> date | None:
    """The concern's real date: the check's YYYY-MM-DD when it is sane
    (a week back to a year ahead), else a weekday / today / tomorrow named
    in the text, else None."""
    d = _parse_date(when or "")
    if d and today - timedelta(days=7) <= d <= today + timedelta(days=365):
        return d
    low = (text or "").lower()
    if "tomorrow" in low:
        return today + timedelta(days=1)
    if "today" in low or "tonight" in low:
        return today
    for n, name in enumerate(_WEEKDAYS):
        if re.search(r"\b" + name + r"\b", low):
            ahead = (n - today.weekday()) % 7
            return today + timedelta(days=ahead or 7) if "next" in low else \
                today + timedelta(days=ahead)
    return None


def add(text, when=None, today: date | None = None, path=None) -> bool:
    """Add a concern the check found. A near-duplicate of an open one is
    skipped; of a quiet one wakes it up (the user brought it up again).
    Returns True when the list changed."""
    text = " ".join((text or "").split()).strip(" .")[:120]
    if not text or text.lower() in ("none", "nothing", "-", "n/a"):
        return False
    today = today or date.today()
    items = load(path)
    for item in active(items):
        if _similar(item["text"], text):
            if item["status"] == "quiet":
                item["status"], item["asked"] = "open", 0
                save(items, path)
                return True
            return False
    items.append({"text": text, "status": "open", "added": today,
                  "due": resolve_day(text, when, today), "asked": 0,
                  "greeted": None, "done": None})
    live = active(items)
    while len(live) > STORE_MAX:
        quiet = [i for i in live if i["status"] == "quiet"]
        drop = quiet[0] if quiet else live[0]
        drop["status"], drop["done"] = "done", today
        live = active(items)
    save(items, path)
    return True


def mark(numbers, what, today: date | None = None, path=None) -> None:
    """numbers = 1-based positions in active(); what = "done" or "asked".
    Two asks without an answer make a concern quiet."""
    items = load(path)
    live = active(items)
    today = today or date.today()
    changed = False
    for n in set(numbers or []):
        if not 1 <= n <= len(live):
            continue
        item = live[n - 1]
        if what == "done":
            item["status"], item["done"] = "done", today
        elif item["status"] == "open":
            item["asked"] += 1
            if item["asked"] >= QUIET_AFTER_ASKS:
                item["status"] = "quiet"
        changed = True
    if changed:
        save(items, path)


def remove(index, path=None) -> bool:
    """Delete one concern by its position in load() (Settings' delete
    button). Returns True when one was removed."""
    items = load(path)
    if not 0 <= index < len(items):
        return False
    del items[index]
    save(items, path)
    return True


def _rank(items, today):
    """The open ones most worth carrying: passed or soonest due first, then
    the most recently added."""
    def key(i):
        due = i["due"]
        return (0 if due and due <= today + timedelta(days=2) else 1,
                due or date.max, -(i["added"] or date.min).toordinal())
    return sorted([i for i in items if i["status"] == "open"], key=key)


def _when_phrase(item, today) -> str:
    bits = []
    if item["added"]:
        days = (today - item["added"]).days
        bits.append("they told you today" if days <= 0 else
                    "they told you yesterday" if days == 1 else
                    f"they told you {days} days ago")
    due = item["due"]
    if due:
        delta = (due - today).days
        if delta < 0:
            bits.append(f"it was on {due:%A %Y-%m-%d} - that has passed, "
                        "so you could ask how it went")
        elif delta == 0:
            bits.append("it is today")
        elif delta == 1:
            bits.append("it is tomorrow")
        else:
            bits.append(f"it is on {due:%A %Y-%m-%d}")
    return "; ".join(bits)


def prompt_line(trust, today: date | None = None, path=None) -> str:
    """Her prompt's line about them, or '' (nothing open). Below Close it
    is background only; from Close, permission to ask - never a list to
    recite."""
    try:
        trust = float(trust)
    except (TypeError, ValueError):
        trust = 0.0
    today = today or date.today()
    try:
        shown = _rank(load(path), today)[:OPEN_SHOWN]
    except Exception as exc:  # a concerns hiccup must never block a reply
        print("[Amadeus] Concerns line skipped:", repr(exc))
        return ""
    if not shown:
        return ""
    parts = []
    for item in shown:
        when = _when_phrase(item, today)
        parts.append(item["text"] + (f" ({when})" if when else ""))
    if trust < ASK_FROM_TRUST:
        return ("THINGS THEY TOLD YOU (for reference): " + "; ".join(parts)
                + ". Keep these in the background - use them when they bring "
                "the subject up or ask whether you remember, without raising "
                "them yourself.")
    care = ("You care about these - make a point of asking about one when "
            "the moment allows, warmly and without lecturing."
            if trust >= CARE_FROM_TRUST else
            "Ask about one when it fits naturally - one at a time, never as a list.")
    return "ON YOUR MIND ABOUT THEM (things they told you): " + "; ".join(parts) + ". " + care


def greeting_options(today: date | None = None, path=None) -> str:
    """The open concerns a startup greeting could open with (at most
    OPEN_SHOWN, each with its date phrase), or ''. Never writes: she picks
    one or none herself."""
    today = today or date.today()
    try:
        shown = _rank(load(path), today)[:OPEN_SHOWN]
    except Exception as exc:
        print("[Amadeus] Concern greeting options skipped:", repr(exc))
        return ""
    parts = []
    for item in shown:
        when = _when_phrase(item, today)
        parts.append(item["text"] + (f" ({when})" if when else ""))
    return "; ".join(parts)


def check_list(path=None) -> str:
    """The numbered open/quiet list for the trust check's prompt."""
    live = active(load(path))
    if not live:
        return "(none yet)"
    return "\n".join(f"{n}. {i['text']}" + (" [quiet]" if i["status"] == "quiet" else "")
                     for n, i in enumerate(live, 1))

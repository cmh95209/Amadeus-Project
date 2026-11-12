"""Cross-tab memory (2026-11-12): what she knows about the user's OTHER chat tabs.

Two independent features, each with its own switch in Settings -> Preferences
(both ON by default; data/cross_tab.json):

1. CATCH-UP ("Remember my other chats") - catch_up_block():
   a short note on the user's other tabs, newest first, filled up to a token
   budget: the newest FULL_TABS tabs get their rolling summary + last lines,
   older ones a one-line mention. No day or tab limit - the budget decides.
   It changes only when another tab changes (dates, not "2 hours ago"), so it
   sits with her fixed blocks and keeps the prompt-cache discount.

2. SEARCH ("Search old chats") - search_block(query):
   a plain-text search of every other tab for what the user just wrote, no
   model call. Language-agnostic: words for scripts that use spaces, 2-letter
   pieces for scripts that don't (Chinese, Japanese, Thai, ...), and words
   that are too common in the user's own history weigh nothing (stop words
   in any language, no word lists). The best few matches, each with its tab
   name and date, go in her "right now" note for this reply only.

Plus LEARN ON LEAVE (always on): when the user leaves a tab with enough new
messages, the memory service learns from it at once instead of waiting for
its usual 10 messages (cm_bridge.learn_now).

Every number worth tuning is in the TUNABLES block below. Neither feature
ever writes to the chat database; both fail closed (any error -> no block,
her prompt is simply without it).
"""
from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from datetime import datetime
from typing import Dict, List, Optional

import memory as store

# ============================== TUNABLES =====================================
# Token budgets per brain: (catch-up, search). Cloud prompts are cached, so the
# catch-up costs little there; her built-in model has a small context window
# (the history budget gives this room back - reserve_tokens()).
BUDGETS = {
    "cloud":   (1200, 600),
    "local":   (900, 450),     # the user's own local / LAN server
    "builtin": (450, 300),     # her built-in model (16K context tiers)
}
FULL_TABS = 3                  # newest tabs that get summary + last lines
MAX_TABS = 40                  # other tabs looked at, at most (the budget usually stops sooner)
SUMMARY_CHARS = 420            # a full tab's summary, at most
LAST_LINE_CHARS = 160          # each of its last lines, at most
LAST_LINES = 2                 # the user's last line + her reply to it
SHORT_CHARS = 110              # an older tab's one-line mention, at most

SEARCH_RESULTS = 3             # excerpts at most
SEARCH_EXCERPT_CHARS = 220     # each side of an excerpt (user / her), at most
SEARCH_RARE_SHARE = 0.01       # a query word in at most this share of exchanges is "rare" ...
SEARCH_RARE_MIN = 5            # ... (or in at most this many) - a match needs one
SEARCH_UNIQUE_HITS = 2         # a rare NAME this unique is a match on its own
SEARCH_TAIL_SHARE = 0.5        # other matches need at least this share of the best one's score
SEARCH_COMMON_SHARE = 0.08     # a term in more than this share of exchanges = stop word ...
SEARCH_COMMON_MIN = 12         # ... and in more than this many (small histories)
SEARCH_MAX_ROWS = 50000        # newest messages scanned at most
SEARCH_MAX_TERMS = 40          # query terms used at most
STEM_FROM = 5                  # words this long also match their other forms ...
STEM_MIN = 4                   # ... by their first letters: at least this many,
STEM_CUT = 2                   # ... the word minus this many end letters

LEARN_ON_LEAVE_MIN = 6         # new messages in the tab left (user + her) to learn now

DEFAULTS = {"catch_up": True, "search": True}
# Scripts written without spaces between words (Unicode character-name
# prefixes). Text in these is searched by 2-letter pieces; everything else by
# words. Add a script here to change how it is searched.
UNSPACED_SCRIPTS = ("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "THAI", "LAO",
                    "KHMER", "MYANMAR", "TIBETAN", "HALFWIDTH KATAKANA",
                    "IDEOGRAPHIC", "YI ", "BOPOMOFO")

CATCH_UP_LABEL = (
    "YOUR OTHER CHATS (what you and the user talked about in your OTHER chat "
    "tabs, newest first - you remember these yourself; bring them up only when "
    "it fits, and never mix them up with this chat):\n")
SEARCH_LABEL = (
    "FROM YOUR OTHER CHATS (found by searching your other chat tabs for what "
    "the user just wrote - it may or may not be what they mean; use it only if "
    "it fits, and say which chat it was if you mention it):\n")
# =============================================================================

SETTINGS_FILE = "cross_tab.json"


# ---------------------------------------------------------------- settings --

def _settings_path() -> str:
    return os.path.join(store.DATA_DIR, SETTINGS_FILE)


def load_settings() -> Dict[str, bool]:
    """Both switches; a missing or broken file means the defaults."""
    out = dict(DEFAULTS)
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in DEFAULTS:
            if isinstance(data.get(key), bool):
                out[key] = data[key]
    except Exception:
        pass
    return out


def save_settings(changes: Dict[str, object]) -> Dict[str, bool]:
    """Change one or both switches (unknown keys / non-booleans: ValueError)."""
    current = load_settings()
    for key, value in (changes or {}).items():
        if key not in DEFAULTS or not isinstance(value, bool):
            raise ValueError(f"Unknown setting or not on/off: {key}")
        current[key] = value
    path = _settings_path()
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f)
    os.replace(tmp, path)
    return current


def catch_up_on() -> bool:
    return load_settings()["catch_up"]


def search_on() -> bool:
    return load_settings()["search"]


# ------------------------------------------------------------------ budget --

def _brain() -> str:
    try:
        import builtin_llm
        if builtin_llm.active():
            return "builtin"
        import llm
        return "local" if llm._is_local_host(llm._configured_server_url()) else "cloud"
    except Exception:
        return "cloud"


def budgets() -> tuple:
    return BUDGETS.get(_brain(), BUDGETS["cloud"])


def reserve_tokens() -> int:
    """Room the history gives up for these blocks - built-in model only (its
    context window is fixed and small; other brains have room to spare)."""
    if _brain() != "builtin":
        return 0
    s = load_settings()
    catch, search = BUDGETS["builtin"]
    return (catch if s["catch_up"] else 0) + (search if s["search"] else 0)


def _tokens(text: str) -> int:
    return store.estimate_tokens(text)


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _day(stamp: str) -> str:
    """'2026-11-12 18:30' -> 'Thu 12 Nov 2026' (a date, so the text stays the
    same all day - the prompt cache needs it unchanged)."""
    try:
        d = datetime.fromisoformat(str(stamp))
        return d.strftime("%a ") + str(d.day) + d.strftime(" %b %Y")
    except (TypeError, ValueError):
        return str(stamp or "")[:10]


# ---------------------------------------------------------------- catch-up --

def _her_line(row) -> str:
    # Her lines as the chat history shows them to her: the spoken (Japanese)
    # line when there is one.
    return (row["japanese"] or row["content"] or "") if row["role"] == "assistant" else (row["content"] or "")


def _have_chats() -> bool:
    """Never create a chat database just to read it (a fresh install, or a
    test without one)."""
    return os.path.exists(store.PATH_TO_MEMORY)


def _dicts(cursor, sql: str, params=()) -> List[dict]:
    """Run one query; rows as dicts by column name (the store's connections
    are wrapped, so a row_factory cannot be set on them)."""
    result = cursor.execute(sql, params)
    names = [d[0] for d in result.description]
    return [dict(zip(names, row)) for row in result.fetchall()]


def _other_tabs(current: int) -> List[Dict[str, object]]:
    """Other tabs with at least one user message, newest activity first."""
    if not _have_chats():
        return []
    conn = store._connect_db()
    try:
        c = conn.cursor()
        tabs = _dicts(c,
            "SELECT c.id, c.title, COALESCE(c.rolling_summary, '') AS summary, "
            "       MAX(m.created_at) AS last_at, MAX(m.id) AS last_id "
            "FROM conversations c JOIN messages m ON m.conversation_id = c.id "
            "WHERE c.id != ? AND COALESCE(m.active, 1) = 1 "
            "GROUP BY c.id HAVING SUM(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END) > 0 "
            "ORDER BY last_at DESC, last_id DESC", (int(current),))
        tabs = tabs[:MAX_TABS]
        for tab in tabs:
            last = _dicts(c,
                "SELECT role, content, japanese FROM messages "
                "WHERE conversation_id = ? AND COALESCE(active, 1) = 1 "
                "AND COALESCE(aside, 0) = 0 AND role IN ('user', 'assistant') "
                "ORDER BY id DESC LIMIT 6", (tab["id"],))
            lines, seen_user = [], False
            for r in last:                      # newest first
                if r["role"] == "user":
                    lines.append(("User", r["content"] or ""))
                    seen_user = True
                    break
                if not lines:
                    lines.append(("You", _her_line(r)))
            tab["last"] = list(reversed(lines)) if seen_user else lines
    finally:
        conn.close()
    return tabs


def _short(head: str, tab: dict) -> str:
    """An older tab in one line: its summary, else the user's last line."""
    gist = tab["summary"] or next((text for who, text in tab.get("last", []) if who == "User"), "")
    return head + (": " + _clip(gist, SHORT_CHARS) if gist.strip() else "")


def catch_up_block(conversation_id=None, budget: Optional[int] = None) -> Optional[dict]:
    """The catch-up note as one system message, or None (switch off, no other
    tabs, or any error)."""
    try:
        if not catch_up_on():
            return None
        current = store.load_active_conversation() if conversation_id is None else int(conversation_id)
        budget = budgets()[0] if budget is None else budget
        tabs = _other_tabs(current)
        if not tabs:
            return None
        parts, used = [], _tokens(CATCH_UP_LABEL)
        for i, tab in enumerate(tabs):
            title = _clip(str(tab["title"] or "New chat"), 60)
            head = f'- "{title}" (last talked {_day(tab["last_at"])})'
            if i < FULL_TABS:
                bits = []
                if tab["summary"].strip():
                    bits.append(_clip(tab["summary"], SUMMARY_CHARS))
                last = "; ".join(f'{who}: "{_clip(text, LAST_LINE_CHARS)}"'
                                 for who, text in tab.get("last", [])[-LAST_LINES:] if text.strip())
                if last:
                    bits.append("Last: " + last)
                line = head + ": " + " ".join(bits) if bits else head
            else:
                line = _short(head, tab)
            cost = _tokens(line) + 1
            if used + cost > budget:
                if i < FULL_TABS:           # a full one does not fit: try it short
                    line = _short(head, tab)
                    cost = _tokens(line) + 1
                if used + cost > budget:
                    break
            parts.append(line)
            used += cost
        if not parts:
            return None
        return {"role": "system", "content": CATCH_UP_LABEL + "\n".join(parts)}
    except Exception as exc:
        print(f"[Amadeus] cross-tab catch-up skipped: {exc!r}")
        return None


# ------------------------------------------------------------------ search --

_RUN = re.compile(r"\w+", re.UNICODE)
_unspaced_cache: Dict[str, bool] = {}


def _normal(text: str) -> str:
    """Same form for the query and the messages: NFKC (full-width -> normal,
    compatibility forms folded) + casefold (case-insensitive in every script)."""
    return unicodedata.normalize("NFKC", text or "").casefold()


def _unspaced(ch: str) -> bool:
    hit = _unspaced_cache.get(ch)
    if hit is None:
        name = unicodedata.name(ch, "")
        hit = name.startswith(UNSPACED_SCRIPTS)
        _unspaced_cache[ch] = hit
    return hit


def query_terms(text: str) -> List[str]:
    """Search terms, in any language: whole words where the script uses
    spaces, 2-letter pieces where it does not (a run mixing both is split at
    the change). Single letters are dropped."""
    terms: List[str] = []
    for run in _RUN.findall(_normal(text)):
        piece, kind = "", None
        for ch in run + "\0":
            k = None if ch == "\0" else _unspaced(ch)
            if k != kind and piece:
                if kind:
                    terms.extend(piece[i:i + 2] for i in range(len(piece) - 1))
                elif len(piece) >= 2:
                    terms.append(piece)
                piece = ""
            if ch != "\0":
                piece += ch
            kind = k
    out, seen = [], set()
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:SEARCH_MAX_TERMS]


def name_terms(text: str) -> set:
    """Words that look like names in the user's own writing: capitalised
    after the start of a sentence, or holding a digit (Gaviscon, Genshin,
    RTX5090). Only cased scripts have capitals, so elsewhere this is empty -
    those words then need a second matching word like any other."""
    out = set()
    text = unicodedata.normalize("NFKC", text or "")
    for m in _RUN.finditer(text):
        word = m.group()
        before = text[:m.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?。！？\n"
        if any(ch.isdigit() for ch in word) or (word[:1].isupper() and not sentence_start):
            out.add(word.casefold())
    return out


def _matcher(term: str):
    """A test for one term. 2-letter pieces (unspaced scripts) match anywhere.
    Words match at the START of a word, and a word of STEM_FROM+ letters
    also matches its other forms by its first letters (user/users,
    suggest/suggestion, plan/planned) - a crude, language-neutral stemmer;
    shorter words must match whole ('cat' never hits 'category')."""
    if any(_unspaced(ch) for ch in term):
        return lambda text: term in text
    if len(term) >= STEM_FROM:
        stem = term[:max(STEM_MIN, len(term) - STEM_CUT)]
        pattern = re.compile(r"(?<!\w)" + re.escape(stem))
        return lambda text: stem in text and pattern.search(text) is not None
    pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")
    return lambda text: term in text and pattern.search(text) is not None


def _candidates(current: int) -> List[dict]:
    if not _have_chats():
        return []
    conn = store._connect_db()
    try:
        return _dicts(conn.cursor(),
            "SELECT m.id, m.conversation_id, m.role, m.content, m.japanese, m.created_at, "
            "       c.title FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.conversation_id != ? AND COALESCE(m.active, 1) = 1 "
            "AND COALESCE(m.aside, 0) = 0 AND COALESCE(m.greeting, 0) = 0 "
            "AND m.role IN ('user', 'assistant') "
            "ORDER BY m.id DESC LIMIT ?", (int(current), SEARCH_MAX_ROWS))
    finally:
        conn.close()


def _exchanges(rows: List[dict]) -> List[dict]:
    """Messages grouped into exchanges - a user line and her reply - per tab
    (her line without a user line before it stands alone). Searching whole
    exchanges finds a topic the user raised and she named, or the reverse."""
    by_conv: Dict[int, List[dict]] = {}
    for r in rows:
        by_conv.setdefault(r["conversation_id"], []).append(r)
    units = []
    for tab_rows in by_conv.values():
        tab_rows.sort(key=lambda r: r["id"])
        unit = None
        for r in tab_rows:
            if r["role"] == "user" or unit is None or unit["her"] is not None:
                unit = {"user": r if r["role"] == "user" else None,
                        "her": r if r["role"] == "assistant" else None, "first": r}
                units.append(unit)
            else:
                unit["her"] = r
    for u in units:
        parts = [(x["content"] or "") + "\n" + (x["japanese"] or "")
                 for x in (u["user"], u["her"]) if x]
        u["text"] = _normal("\n".join(parts))
    return units


def search(query: str, conversation_id=None, limit: int = SEARCH_RESULTS) -> List[dict]:
    """The best matching exchanges in other tabs: [{conversation_id, title,
    created_at, id, score, user, her}] - best first."""
    terms = query_terms(query)
    if not terms:
        return []
    current = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    units = _exchanges(_candidates(current))
    if not units:
        return []
    n = len(units)
    tests = {t: _matcher(t) for t in terms}
    hits = {t: [i for i, u in enumerate(units) if tests[t](u["text"])] for t in terms}
    weights = {}
    for t, where in hits.items():
        df = len(where)
        if df == 0 or df > max(SEARCH_COMMON_MIN, SEARCH_COMMON_SHARE * n):
            continue                     # never used before, or a stop word here
        weights[t] = math.log((n + 1) / (df + 0.5))
    if not weights:
        return []
    # The topic is carried by the RARE words of the query (rare in this
    # user's own history - so it works the same in every language). A match
    # must hold one of them, plus a second query word unless that rare word
    # is a name (name_terms) found almost nowhere else; the best match sets
    # the bar for the rest.
    rare_cut = max(SEARCH_RARE_MIN, SEARCH_RARE_SHARE * n)
    names = name_terms(query)
    rare = {t for t in weights if len(hits[t]) <= rare_cut}
    if not rare:
        return []
    score: Dict[int, float] = {}
    count: Dict[int, int] = {}
    unique: Dict[int, bool] = {}
    for t, w in weights.items():
        for i in hits[t]:
            score[i] = score.get(i, 0.0) + w
            count[i] = count.get(i, 0) + 1
            if t in rare and t in names and len(hits[t]) <= SEARCH_UNIQUE_HITS:
                unique[i] = True
    holders = {i for t in rare for i in hits[t]}
    good = [i for i in holders if count[i] >= 2 or unique.get(i)]
    if not good:
        return []
    best = max(score[i] for i in good)
    ranked = sorted((i for i in good if score[i] >= SEARCH_TAIL_SHARE * best),
                    key=lambda i: (score[i], units[i]["first"]["id"]), reverse=True)
    total = sum(weights.values())
    out = []
    for i in ranked[:limit]:
        u = units[i]
        first = u["first"]
        out.append({"conversation_id": first["conversation_id"], "title": first["title"],
                    "created_at": first["created_at"], "id": first["id"],
                    "score": round(score[i] / total, 3),
                    "user": (u["user"] or {}).get("content") or "",
                    "her": _her_line(u["her"]) if u["her"] else ""})
    return out


def search_block(query, conversation_id=None, budget: Optional[int] = None) -> Optional[dict]:
    """The search results as one system message for her "right now" note, or
    None (switch off, nothing good enough, or any error)."""
    try:
        if not search_on() or not isinstance(query, str) or not query.strip():
            return None
        budget = budgets()[1] if budget is None else budget
        found = search(query, conversation_id)
        if not found:
            return None
        parts, used = [], _tokens(SEARCH_LABEL)
        for f in found:
            # Too long for what is left of the budget: the same match, cut
            # shorter (a small budget - her built-in model - still gets the
            # best match rather than nothing).
            for chars in (SEARCH_EXCERPT_CHARS, SEARCH_EXCERPT_CHARS // 2, SEARCH_EXCERPT_CHARS // 4):
                bits = []
                if f["user"].strip():
                    bits.append(f'User: "{_clip(f["user"], chars)}"')
                if f["her"].strip():
                    bits.append(f'You: "{_clip(f["her"], chars)}"')
                line = f'- In "{_clip(str(f["title"] or "New chat"), 60)}" ({_day(f["created_at"])}): ' + " / ".join(bits)
                cost = _tokens(line) + 1
                if used + cost <= budget:
                    break
            if used + cost > budget:
                break
            parts.append(line)
            used += cost
        if not parts:
            return None
        return {"role": "system", "content": SEARCH_LABEL + "\n".join(parts)}
    except Exception as exc:
        print(f"[Amadeus] cross-tab search skipped: {exc!r}")
        return None


# --------------------------------------------------------- learn on leave --

def on_tab_left(previous_id, new_id) -> None:
    """The user switched tabs: ask the memory service to learn from the tab
    they left now (it skips it when fewer than LEARN_ON_LEAVE_MIN new
    messages wait there). Fire-and-forget; never raises."""
    try:
        if previous_id is None or int(previous_id) == int(new_id):
            return
        import cm_bridge
        cm_bridge.learn_now(int(previous_id), min_new=LEARN_ON_LEAVE_MIN)
    except Exception as exc:
        print(f"[Amadeus] learn-on-leave skipped: {exc!r}")

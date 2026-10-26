# -*- coding: utf-8 -*-
"""Keyless web search engines for Amadeus (standard library only).

Firecrawl is the first engine her web search tries: no account or API key
("keyless": free, a daily cap per internet connection), about 2 seconds,
recent dated results in English and Japanese, 2 free credits per search.
Keenable (keenable_search, below) is the second: keyless, ~1,000 searches an
hour per connection, every result dated. chat._run_web_search falls back to
the next engine - and finally to the `ddgs` library - whenever one fails, is
slow, finds nothing or is out of free use.

Deliberate properties:
  * BOUNDED  - one attempt with a hard timeout; never retried here (the ddgs
               fallback is the retry).
  * QUIET    - when Firecrawl reports its free limit is used up, it is skipped
               for a few hours (QUOTA_COOLDOWN), and for a short while after
               any other failure (ERROR_COOLDOWN), so a refusing or dead
               service never adds its timeout to every search.
  * SAME SHAPE - results come back as ddgs-style dicts ({"title", "body",
               "href"}, plus "date" when known), so the caller formats both
               engines identically.
  * DATED    - old news must not read as new news (with ddgs she often took a
               years-old article for today's). A time-sensitive query ("today",
               "latest", "news", "最新"...) is limited to recent pages
               (freshness_for), and the age a snippet carries ("2 days ago ·",
               "Oct 13, 2026 ·", "3日前") becomes a real date (snippet_date).

firecrawl_search() returns a list (possibly empty) or raises
FirecrawlUnavailable with a short reason.
"""
from __future__ import annotations

import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

SEARCH_URL = "https://api.firecrawl.dev/v2/search"
DEFAULT_TIMEOUT = 6             # seconds, the single attempt
QUOTA_COOLDOWN = 3 * 3600       # seconds to skip Firecrawl after "free limit used up"
ERROR_COOLDOWN = 120            # seconds to skip it after any other failure
MAX_SNIPPET_CHARS = 400         # a result's snippet is a hint, not the page

_SSL_CTX = ssl.create_default_context()
_lock = threading.Lock()
_skip_until = 0.0               # monotonic time before which Firecrawl is skipped


class FirecrawlUnavailable(Exception):
    """Firecrawl could not answer this time (the reason is in the message)."""


def _cool_down(seconds: float) -> None:
    global _skip_until
    with _lock:
        _skip_until = max(_skip_until, time.monotonic() + seconds)


def reset_cooldown() -> None:
    """Forget any cooldown (tests; a fresh start)."""
    global _skip_until
    with _lock:
        _skip_until = 0.0


def cooling_down() -> bool:
    return time.monotonic() < _skip_until


# --- freshness: how recent a time-sensitive query's pages must be ------------
# Checked in order; the first group that matches wins. "d" = past day,
# "w" = past week, "m" = past month (Firecrawl's tbs=qdr:X, ddgs' timelimit).
_FRESH_RULES = (
    ("d", re.compile(
        r"\b(today'?s?|tonight|this (morning|evening|afternoon)|breaking|right now|"
        r"live (score|scores|updates?)|yesterday|last night)\b|今日|本日|今夜|今朝|速報|昨日|昨夜", re.I)),
    ("w", re.compile(
        r"\b(news|headlines?|latest|current(ly)?|recent(ly)?|this week|just (released|announced|came out)|"
        r"newest|upcoming)\b|ニュース|最新|今週|現在|最近|発表", re.I)),
    ("m", re.compile(r"\b(this month|this season|new release|newly)\b|今月|今期|新作", re.I)),
)
# A query that names a past year is about the past: never limit it to recent pages.
_YEAR_RE = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")


def freshness_for(query, today=None):
    """"d" / "w" / "m" when the query asks for something current, else None."""
    q = str(query or "")
    this_year = (today or date.today()).year
    if any(int(y) < this_year for y in _YEAR_RE.findall(q)):
        return None
    for code, rx in _FRESH_RULES:
        if rx.search(q):
            return code
    return None


# --- dates carried by snippets ------------------------------------------------
_AGO_RE = re.compile(
    r"^\s*(\d+)\s*(minute|min|hour|hr|day|week|month|year)s?\s+ago\b", re.I)
_AGO_JA_RE = re.compile(r"^\s*(\d+)\s*(分|時間|日|週間|か月|ヶ月|ヵ月|カ月|年)前")
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MDY_RE = re.compile(r"^\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})\b")
_DMY_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b")
_YMD_RE = re.compile(r"^\s*(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")


def snippet_date(text, now=None):
    """The date a snippet starts with ("2 days ago ·", "Oct 13, 2026 ·",
    "2026/10/20", "3日前"), as YYYY-MM-DD; "" when it carries none."""
    t = str(text or "")[:40]
    now = now or datetime.now()
    try:
        m = _AGO_RE.match(t) or _AGO_JA_RE.match(t)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            if unit in ("minute", "min", "分", "hour", "hr", "時間"):
                delta = timedelta(hours=n) if unit in ("hour", "hr", "時間") else timedelta(minutes=n)
            elif unit in ("day", "日"):
                delta = timedelta(days=n)
            elif unit in ("week", "週間"):
                delta = timedelta(weeks=n)
            elif unit in ("year", "年"):
                delta = timedelta(days=365 * n)
            else:  # months
                delta = timedelta(days=30 * n)
            return (now - delta).date().isoformat()
        m = _MDY_RE.match(t)
        if m and m.group(1).lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        m = _DMY_RE.match(t)
        if m and m.group(2).lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
        m = _YMD_RE.match(t)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        pass  # an impossible date ("Feb 31") is no date
    return ""


def _clean_snippet(text) -> str:
    """Firecrawl snippets sometimes carry markdown (headings, links): keep the words."""
    t = str(text or "")
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)   # [text](url) -> text
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*$", r"\1", t)    # a link cut off at the end
    t = re.sub(r"!\[\]", "", t)                         # empty image marks
    t = re.sub(r"(?m)^\s*#+\s*", "", t)                 # heading marks
    t = re.sub(r"\*\*|__", "", t)                       # bold marks
    t = re.sub(r"(\s*\|\s*-*\s*){2,}", " | ", t)        # table rules ("| | |-|")
    t = re.sub(r"\s+", " ", t).strip(" |")
    if len(t) > MAX_SNIPPET_CHARS:
        t = t[:MAX_SNIPPET_CHARS].rstrip() + "..."
    return t


def firecrawl_search(query: str, max_results: int = 5, timeout: float = DEFAULT_TIMEOUT,
                     freshness=None) -> list:
    """Search Firecrawl (keyless) and return [{"title", "body", "href", "date"}]
    in rank order ("date" is "" when unknown). `freshness` ("d"/"w"/"m")
    limits the search to pages from the past day / week / month."""
    if cooling_down():
        raise FirecrawlUnavailable("skipped (cooling down after an earlier failure)")
    body = {"query": query, "limit": max_results}
    if freshness in ("d", "w", "m", "y"):
        body["tbs"] = "qdr:" + freshness
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(SEARCH_URL, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # 429 = the daily keyless cap; 402 = out of credits. Both last for hours.
        if e.code in (402, 429):
            _cool_down(QUOTA_COOLDOWN)
            raise FirecrawlUnavailable(f"free limit reached (HTTP {e.code})") from None
        _cool_down(ERROR_COOLDOWN)
        raise FirecrawlUnavailable(f"HTTP {e.code}") from None
    except Exception as e:
        _cool_down(ERROR_COOLDOWN)
        raise FirecrawlUnavailable(repr(e)) from None

    if not isinstance(data, dict) or not data.get("success"):
        _cool_down(ERROR_COOLDOWN)
        raise FirecrawlUnavailable(f"unexpected answer: {str(data)[:120]}")
    block = data.get("data") or {}
    items = block.get("web") if isinstance(block, dict) else block
    results = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        href = str(item.get("url") or "").strip()
        if not href:
            continue
        raw = item.get("description")
        results.append({
            "title": str(item.get("title") or "").strip(),
            "body": _clean_snippet(raw),
            "href": href,
            "date": snippet_date(raw),
        })
        if len(results) >= max_results:
            break
    return results


# --- Keenable: the second engine (2026-10-25) ----------------------------------
# Keenable's keyless public search: no account, no key, no credits; limited to
# about 1,000 searches an hour per internet connection. Fast (~0.5 s), and
# every result carries its publish date. Used when Firecrawl cannot answer
# (its keyless daily cap is small); ddgs stays the last resort. Results come
# in many languages (there is no language setting).
KEENABLE_URL = "https://api.keenable.ai/v1/search/public"
KEENABLE_APP = "Amadeus"              # the X-Keenable-Title the public endpoint requires
KEENABLE_TIMEOUT = 5                  # seconds, the single attempt
KEENABLE_QUOTA_COOLDOWN = 3600        # seconds to skip it after "rate limited" (hourly cap)
KEENABLE_ERROR_COOLDOWN = 30          # seconds after one timeout / error (it is usually ~0.5 s;
                                      # 2 minutes once sent 13 searches in a row to ddgs)
KEENABLE_FRESHNESS = {"d": "1d", "w": "7d", "m": "30d", "y": "365d"}
_keenable_skip_until = 0.0


class KeenableUnavailable(Exception):
    """Keenable could not answer this time (the reason is in the message)."""


def _keenable_cool_down(seconds: float) -> None:
    global _keenable_skip_until
    with _lock:
        _keenable_skip_until = max(_keenable_skip_until, time.monotonic() + seconds)


def reset_keenable_cooldown() -> None:
    global _keenable_skip_until
    with _lock:
        _keenable_skip_until = 0.0


def keenable_cooling_down() -> bool:
    return time.monotonic() < _keenable_skip_until


def keenable_search(query: str, max_results: int = 5, timeout: float = KEENABLE_TIMEOUT,
                    freshness=None) -> list:
    """Search Keenable (keyless) -> [{"title", "body", "href", "date"}] in rank
    order. `freshness` ("d"/"w"/"m") keeps pages published in the past day /
    week / month. Raises KeenableUnavailable when it cannot answer."""
    if keenable_cooling_down():
        raise KeenableUnavailable("skipped (cooling down after an earlier failure)")
    body = {"query": query, "max_results": int(max_results), "snippet_max_length": 400}
    if freshness in KEENABLE_FRESHNESS:
        body["published_after"] = KEENABLE_FRESHNESS[freshness]
    req = urllib.request.Request(
        KEENABLE_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "X-Keenable-Title": KEENABLE_APP})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (402, 429):
            _keenable_cool_down(KEENABLE_QUOTA_COOLDOWN)
            raise KeenableUnavailable(f"rate limit reached (HTTP {e.code})") from None
        _keenable_cool_down(KEENABLE_ERROR_COOLDOWN)
        raise KeenableUnavailable(f"HTTP {e.code}") from None
    except Exception as e:
        _keenable_cool_down(KEENABLE_ERROR_COOLDOWN)
        raise KeenableUnavailable(repr(e)) from None
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        _keenable_cool_down(KEENABLE_ERROR_COOLDOWN)
        raise KeenableUnavailable(f"unexpected answer: {str(data)[:120]}")
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        href = str(item.get("url") or "").strip()
        if not href:
            continue
        text = item.get("snippet") or item.get("description") or ""
        published = str(item.get("published_at") or "")
        when = published[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", published) else snippet_date(text)
        results.append({"title": str(item.get("title") or "").strip(),
                        "body": _clean_snippet(text), "href": href, "date": when})
        if len(results) >= max_results:
            break
    return results


# --- Year guard: past years she adds to her own search words (2026-10-25) ------
# Her sense of "now" comes from training, so she often writes a past year into
# a search the user never dated ("current banner 2024", "原神 ピックアップ
# 2025年1月") - in the test set 54 of 124 searches did, and every stale answer
# about banners / anime / Windows started there. Such a year (with its month,
# "June 2024", "2025年1月") is removed before the search, so it is about NOW
# and the recent-pages limit applies. A year the USER mentioned (this message
# or the recent ones), its neighbours ("the year after"), and relative years
# ("last year", 去年, "two years ago") stay; the current and future years
# always stay.
_MONTHS_RE = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
              r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_YEAR_ANY_RE = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
_RELATIVE_YEARS = (
    (re.compile(r"\bthis year\b|今年|本年", re.I), 0),
    (re.compile(r"\blast year\b|\bprevious year\b|去年|昨年|前年", re.I), -1),
    (re.compile(r"\byear before last\b|一昨年|おととし", re.I), -2),
    (re.compile(r"\bnext year\b|来年", re.I), 1),
)
_N_YEARS_AGO_RE = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\s+ago\b"
                             r"|(\d{1,2})\s*年前", re.I)
# The user points at a past time without a number ("back when Elden Ring came
# out", 当時): then the year she works out for it is what they meant - kept.
_PAST_TIME_RE = re.compile(
    r"\bback (?:then|when|in the day)\b|\bat (?:that|the) time\b|\bin those days\b"
    r"|\baround the time\b"
    r"|当時|その頃|あの頃|その当時|発売された頃|出た頃", re.IGNORECASE)
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10}


def relative_years(texts, today=None) -> set:
    """The years the user's words point at WITHOUT naming them ("last year",
    去年, "two years ago", "this year")."""
    this_year = (today or date.today()).year
    years = set()
    for text in texts or []:
        t = str(text or "")
        for rx, delta in _RELATIVE_YEARS:
            if rx.search(t):
                years.add(this_year + delta)
        for m in _N_YEARS_AGO_RE.finditer(t):
            n = m.group(1) or m.group(2)
            n = _NUMBER_WORDS.get(n.lower(), None) if not n.isdigit() else int(n)
            if n:
                years.add(this_year - n)
    return years


def years_the_user_means(texts, today=None) -> set:
    """Every year the user's words name or point at (plus the neighbours of
    named years, for "the year after" / "the year before")."""
    this_year = (today or date.today()).year
    years = set()
    for text in texts or []:
        t = str(text or "")
        for y in _YEAR_ANY_RE.findall(t):
            years.update({int(y) - 1, int(y), int(y) + 1})
        for rx, delta in _RELATIVE_YEARS:
            if rx.search(t):
                years.add(this_year + delta)
        for m in _N_YEARS_AGO_RE.finditer(t):
            n = m.group(1) or m.group(2)
            n = _NUMBER_WORDS.get(n.lower(), None) if not n.isdigit() else int(n)
            if n:
                years.add(this_year - n)
    return years


# Words that only make sense next to the year they qualify ("late 2022",
# "released around February 2022", "in 2024") go with it.
_YEAR_LEAD_RE = r"(?:late|early|mid|around|in|of|during|from|since|circa)"


def guard_query_years(query, user_texts, today=None):
    """-> (search words without the past years she added on her own, [removed
    years]). When the user meant one year without naming it ("last year",
    去年) and she wrote a different past year, the year the user meant takes
    its place. The words come back unchanged when nothing needs removing (or
    when removing would leave nothing to search for)."""
    q = str(query or "")
    this_year = (today or date.today()).year
    allowed = years_the_user_means(user_texts, today)
    if any(_PAST_TIME_RE.search(str(t or "")) for t in user_texts or []):
        return q, []        # the user means a past time; her year for it stays
    removed = sorted({int(y) for y in _YEAR_ANY_RE.findall(q)
                      if int(y) < this_year and int(y) not in allowed})
    if not removed:
        return q, []
    out = q
    meant = relative_years(user_texts, today)
    named = {int(y) for y in _YEAR_ANY_RE.findall(q)}
    if len(meant) == 1 and not (meant & named):
        # "What happened last year?" searched as "... 2023": search 2025 instead.
        target = str(next(iter(meant)))
        out = re.sub(r"(?<!\d)" + str(removed[0]) + r"(?!\d)", target, out, count=1)
    for y in removed:
        ys = str(y)
        lead = r"(?:\b" + _YEAR_LEAD_RE + r"[\s-]+)?"
        # Japanese "2025年1月" / "2025年", English "June 2024", "fall 2024", "2024-2025", plain "2024".
        out = re.sub(ys + r"\s*年(?:\s*\d{1,2}\s*月)?", " ", out)
        out = re.sub(lead + r"\b" + _MONTHS_RE + r"\.?\s+" + ys + r"\b", " ", out, flags=re.I)
        out = re.sub(lead + r"(?<!\d)" + ys + r"(?!\d)", " ", out, flags=re.I)
    out = re.sub(r"\s*[-/~〜]\s*(?=\s|$)", " ", out)       # a range's leftover dash
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;-")
    if not _has_words(out):
        return q, []
    return out, removed


def _has_words(text) -> bool:
    return bool(re.search(r"\w", str(text or "")))


# --- "What is on now" vs news (2026-10-25) --------------------------------------
# One freshness rule served two kinds of question. NEWS ("news", "today",
# ニュース) wants recent pages. A question about what is current - a game
# banner, a schedule, standings, the anime season - is best answered by a
# page that keeps being UPDATED (a schedule page), which a "published in the
# past week" filter drops: "current Genshin banner" then read only this week's
# announcements of the NEXT banners. Such questions search without the filter
# too, and those results come first.
_STATE_RE = re.compile(
    r"\b(?:current(?:ly)?|right now|now|running|ongoing|this season|this week|banners?|"
    r"schedule|standings?|leaderboard|rankings?|line-?up|event)\b"
    r"|現在|今の|今は|開催中|ピックアップ|ガチャ|今期|今シーズン|配信中|スケジュール", re.IGNORECASE)
_NEWS_RE = re.compile(r"\b(?:news|headlines?|breaking|announce(?:d|ment)?)\b|ニュース|速報|発表",
                      re.IGNORECASE)


def is_state_question(query, question="") -> bool:
    """A question about what is current / running (not news)."""
    text = f"{query or ''} {question or ''}"
    return bool(_STATE_RE.search(text)) and not _NEWS_RE.search(text)


# --- Season guard (2026-10-26) ----------------------------------------------------
# Like the past years: asked "which anime this season?" in October, she searched
# "new anime season WINTER popularity" and answered about Winter 2026. A season
# word she adds that the user did not say becomes the CURRENT season (anime
# cours: Jan-Mar winter, Apr-Jun spring, Jul-Sep summer, Oct-Dec fall), or the
# next / previous one when the user said "next season" / "last season" (来期 /
# 前期). Left alone when the user named a season, a year, or a past time.
_SEASONS_EN = ("winter", "spring", "summer", "fall")
_SEASONS_JA = ("冬", "春", "夏", "秋")
_SEASON_EN_RE = re.compile(r"\b(winter|spring|summer|fall|autumn)\b", re.IGNORECASE)
_SEASON_JA_RE = re.compile(r"(冬|春|夏|秋)(?=\s*(?:アニメ|ドラマ|期|クール|シーズン|新作))")
# Only for TV / anime season line-ups ("summer festival" is not one).
_SEASON_CONTEXT_RE = re.compile(r"\b(?:anime|season|cour|drama|tv|series|shows?|line-?up|premieres?|simulcasts?)\b",
                                re.IGNORECASE)
_USER_SEASON_RE = re.compile(r"\b(?:winter|spring|summer|fall|autumn)\b|冬|春|夏|秋", re.IGNORECASE)
_NEXT_SEASON_RE = re.compile(r"\bnext (?:season|cour)\b|来期|来季|次のクール|次期", re.IGNORECASE)
_LAST_SEASON_RE = re.compile(r"\b(?:last|previous) (?:season|cour)\b|前期|前季|前のクール", re.IGNORECASE)


def season_index(today=None) -> int:
    """0 winter, 1 spring, 2 summer, 3 fall (anime cours by month)."""
    return ((today or date.today()).month - 1) // 3


def guard_query_seasons(query, user_texts, today=None):
    """-> (search words with the season she added on her own replaced by the
    season the user means, [the season words replaced])."""
    q = str(query or "")
    texts = [str(t or "") for t in user_texts or []]
    if not ((_SEASON_EN_RE.search(q) and _SEASON_CONTEXT_RE.search(q)) or _SEASON_JA_RE.search(q)):
        return q, []
    if any(_USER_SEASON_RE.search(t) or _YEAR_ANY_RE.search(t) or _PAST_TIME_RE.search(t)
           for t in texts):
        return q, []
    target = season_index(today)
    if any(_NEXT_SEASON_RE.search(t) for t in texts):
        target = (target + 1) % 4
    elif any(_LAST_SEASON_RE.search(t) for t in texts):
        target = (target - 1) % 4
    en, ja = _SEASONS_EN[target], _SEASONS_JA[target]
    changed = []

    def fix_en(m):
        word = m.group(1).lower()
        word = "fall" if word == "autumn" else word
        if word != en:
            changed.append(m.group(1))
            return en.capitalize() if m.group(1)[0].isupper() else en
        return m.group(1)

    def fix_ja(m):
        if m.group(1) != ja:
            changed.append(m.group(1))
            return ja
        return m.group(1)
    out = q if not _SEASON_CONTEXT_RE.search(q) else _SEASON_EN_RE.sub(fix_en, q)
    out = _SEASON_JA_RE.sub(fix_ja, out)
    return (out, changed) if changed else (q, [])

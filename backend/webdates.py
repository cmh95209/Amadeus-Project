# -*- coding: utf-8 -*-
"""Reading dates in web text, relative to TODAY (2026-10-25; standard library only).

Two weak spots of her web answers (search test set, 2026-10-25) are about time:
  * "current" questions answered with the UPCOMING entry of a schedule - the
    page or paragraph whose date range contains today is the answer
    (covers_today), so it is read first;
  * upcoming dates said in the past tense ("the consoles were released on
    October 29" on October 25) - every date she is shown gets a tag relative to
    today (label_dates: "October 29 (upcoming, in 4 days)"), and her reply is
    checked for future dates in past-tense sentences (past_tense_future_dates).

Formats read: "October 29, 2026", "Oct 29", "October 29th", "29 October 2026",
"2026-10-29", "2026/10/29", "2026年10月29日", "10月29日"; ranges joined by
- – — ~ ～ 〜 "to" "through" "until" "から"; open ends "until November 3",
"ends Nov 3", "11月3日まで". A date without a year takes the year that puts it
closest to today.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_WORD = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
               r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
# One pattern per format; each gives (year | None, month, day) via _parts.
_DATE_RES = (
    ("ymd", re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")),
    ("ja", re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日")),
    ("mdy", re.compile(r"\b" + _MONTH_WORD + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?(?!\d)",
                       re.IGNORECASE)),
    ("dmy", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_WORD + r"\b\.?(?:,?\s+(\d{4}))?(?!\d)",
                       re.IGNORECASE)),
)
_RANGE_JOIN_RE = re.compile(
    r"^\s*(?:,?\s*\d{4})?\s*(?:-|–|—|~|～|〜|to|through|thru|until|till|から)\s*$", re.IGNORECASE)
_OPEN_END_RE = re.compile(r"(?:\b(?:until|till|through|thru|ends?|ending|ended|runs? (?:until|through))\s*$)",
                          re.IGNORECASE)
_OPEN_END_JA_RE = re.compile(r"^\s*(?:まで|迄)")
OPEN_RANGE_DAYS = 60     # "until Nov 3" counts as current only when the end is this close


def _infer_year(month: int, day: int, today: date) -> int:
    """The year that puts month/day closest to today."""
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best.year if best else today.year


def find_dates(text, today=None) -> list:
    """Every date in the text: [(start, end, date)] (span + the date), in
    order, overlaps removed. A date without a year gets the closest one."""
    today = today or date.today()
    t = str(text or "")
    found = []
    for kind, rx in _DATE_RES:
        for m in rx.finditer(t):
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "ja":
                    y = int(m.group(1)) if m.group(1) else None
                    mo, d = int(m.group(2)), int(m.group(3))
                elif kind == "mdy":
                    mo = _MONTHS.index(m.group(1)[:3].lower()) + 1
                    d = int(m.group(2))
                    y = int(m.group(3)) if m.group(3) else None
                else:
                    d = int(m.group(1))
                    mo = _MONTHS.index(m.group(2)[:3].lower()) + 1
                    y = int(m.group(3)) if m.group(3) else None
                if y is None:
                    y = _infer_year(mo, d, today)
                if not 1990 <= y <= 2100:
                    continue
                found.append((m.start(), m.end(), date(y, mo, d)))
            except ValueError:
                continue
    found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    out, last_end = [], -1
    for s, e, d in found:
        if s >= last_end:
            out.append((s, e, d))
            last_end = e
    return out


def date_ranges(text, today=None) -> list:
    """Date ranges in the text: [(start or None, end)]. "Oct 13 - Nov 3",
    "10月13日～11月3日", "October 13 to November 3, 2026"; open ends "until
    Nov 3" / "11月3日まで" give (None, end)."""
    today = today or date.today()
    t = str(text or "")
    dates = find_dates(t, today)
    ranges, used = [], set()
    for i in range(len(dates) - 1):
        s1, e1, d1 = dates[i]
        s2, e2, d2 = dates[i + 1]
        if _RANGE_JOIN_RE.match(t[e1:s2]):
            if d2 < d1:                      # "Dec 20 - Jan 5": the end is next year
                try:
                    d2 = d2.replace(year=d2.year + 1)
                except ValueError:
                    continue
            ranges.append((d1, d2))
            used.update({i, i + 1})
    for i, (s, e, d) in enumerate(dates):
        if i in used:
            continue
        before = t[max(0, s - 25):s]
        if _OPEN_END_RE.search(before) or _OPEN_END_JA_RE.match(t[e:e + 4]):
            ranges.append((None, d))
    return ranges


def covers_today(text, today=None) -> bool:
    """Does the text give a date range that includes today?"""
    today = today or date.today()
    for start, end in date_ranges(text, today):
        if start is None:
            if today <= end <= today + timedelta(days=OPEN_RANGE_DAYS):
                return True
        elif start <= today <= end:
            return True
    return False


def relative_label(d: date, today=None) -> str:
    """"upcoming, in 4 days" / "today" / "12 days ago" / "about 3 months ago"."""
    today = today or date.today()
    n = (d - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "upcoming, tomorrow"
    if n == -1:
        return "yesterday"
    if 1 < n <= 45:
        return f"upcoming, in {n} days"
    if -45 <= n < -1:
        return f"{-n} days ago"
    months = round(abs(n) / 30.4)
    if abs(n) < 365:
        return f"upcoming, in about {months} months" if n > 0 else f"about {months} months ago"
    years = round(abs(n) / 365.25)
    plural = "s" if years != 1 else ""
    return f"upcoming, in about {years} year{plural}" if n > 0 else f"about {years} year{plural} ago"


def label_dates(text, today=None) -> str:
    """Every date in the text gets its distance from today in brackets:
    "October 29 (upcoming, in 4 days)". Already-labelled dates are left alone."""
    today = today or date.today()
    t = str(text or "")
    out, pos = [], 0
    for s, e, d in find_dates(t, today):
        out.append(t[pos:e])
        if not re.match(r"\s*\((?:upcoming|today|yesterday|\d+ days ago|about )", t[e:e + 14]):
            out.append(f" ({relative_label(d, today)})")
        pos = e
    out.append(t[pos:])
    return "".join(out)


# --- her reply: future dates said in the past tense -------------------------------
_PAST_EN_RE = re.compile(
    r"\b(?:was|were|has been|have been|had been|came out|went live|is out|are out|"
    r"already (?:out|released|available|live)|launched|released|arrived|debuted)\b", re.IGNORECASE)
_FUTURE_EN_RE = re.compile(
    r"\b(?:will|won't|going to|is (?:set|scheduled|due|expected|planned|slated)|"
    r"are (?:set|scheduled|due|expected|planned|slated)|upcoming|coming|scheduled|planned|"
    r"starts?|begins?|launches|releases|arrives|in \d+ days|next week|later this)\b", re.IGNORECASE)
_PAST_JA_RE = re.compile(r"発売され(?:た|ました)|発売しました|発売済み|既に発売|もう発売|発売になった|"
                         r"リリースされ(?:た|ました)|配信され(?:た|ました)|配信開始され(?:た|ました)|"
                         r"配信が始まった|登場し(?:た|ました)|始まった|始まりました|開始され(?:た|ました)")
_FUTURE_JA_RE = re.compile(r"予定|これから|される|開始する|始まる|発売する|まで|になる|します")


def _sentences(text, ja=False):
    parts = re.split(r"(?<=[。！？!?])" if ja else r"(?<=[.!?])\s+", str(text or ""))
    return [p for p in parts if p.strip()]


def past_tense_future_dates(english, japanese="", today=None) -> list:
    """The future dates her reply describes as already past: [(date, sentence)]."""
    today = today or date.today()
    found = []
    for ja, text, past_re, future_re in ((False, english, _PAST_EN_RE, _FUTURE_EN_RE),
                                         (True, japanese, _PAST_JA_RE, _FUTURE_JA_RE)):
        for sentence in _sentences(text, ja):
            future = [d for _, _, d in find_dates(sentence, today) if d > today]
            if future and past_re.search(sentence) and not future_re.search(sentence):
                found.extend((d, sentence.strip()) for d in future)
    return found

# -*- coding: utf-8 -*-
"""Spoken-form rewriting for the voice (GPT-SoVITS) - layer 1: numbers.

GPT-SoVITS splits each line by language before speaking and reads Japanese
through pyopenjtalk. Numbers trip it up in ways that sound English or broken:
  - a number next to a symbol or Latin unit ("60%", "5m/s", "14:30") is
    often given to the English (or Chinese) model, or read symbol by symbol;
  - a decimal point or a thousands comma becomes a PAUSE ("23.5" ->
    "にじゅうさん… ご", "1,500" -> "いち… ごひゃく").
to_spoken() rewrites them into ordinary Japanese notation - digits with kanji
counters and katakana units ("23点5度", "14時30分", "60パーセント") - which
pyopenjtalk reads natively, with the right sound changes (6分 = ろっぷん,
1日 after a month = ついたち) AND natural pitch. Spelling numbers out in
hiragana was tried first and rejected by ear (2026-10-15): the same sounds,
but long kana runs get chopped into odd phrases and come out slow.

Only the text sent to the voice changes; the stored and displayed Japanese
line is untouched. Pure rules, no model call. Numbers glued onto Latin
letters ("GPT-4", "iPhone15") are left alone - that word is spoken in English
anyway (a later layer handles English words).
"""
import re

# The one spelling chosen by ear for this common air-quality term.
PM25_SPOKEN = "ＰＭ2点5"   # full-width PM: Japanese-style pitch

_KATA_DIGIT = {"0": "ゼロ", "1": "イチ", "2": "ニー", "3": "サン", "4": "ヨン",
               "5": "ゴー", "6": "ロク", "7": "ナナ", "8": "ハチ", "9": "キュー"}

# Latin units -> (word said before the number, word said after it)
_UNITS = {
    "km/h": ("時速", "キロ"), "m/s": ("秒速", "メートル"),
    "°F": ("華氏", "度"), "hPa": ("", "ヘクトパスカル"),
    "mm": ("", "ミリ"), "cm": ("", "センチ"), "km": ("", "キロ"),
    "kg": ("", "キロ"), "mL": ("", "ミリリットル"), "ml": ("", "ミリリットル"),
    "GB": ("", "ギガバイト"), "MB": ("", "メガバイト"), "TB": ("", "テラバイト"),
    "m": ("", "メートル"), "g": ("", "グラム"), "L": ("", "リットル"),
    "℃": ("", "度"), "°C": ("", "度"), "°": ("", "度"), "%": ("", "パーセント"),
}
_CURRENCY = {"$": "ドル", "¥": "円", "￥": "円", "€": "ユーロ", "£": "ポンド"}

_FULLWIDTH = str.maketrans("０１２３４５６７８９％", "0123456789%")
_LATIN_BEFORE = re.compile(r"[A-Za-z]-?$")

_PHONE = re.compile(r"(?<![\d-])\d+(?:-\d+){2,}(?![\d-])")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?!\d)")
_SLASH = re.compile(r"(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/.])")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
_RANGE = re.compile(r"(?<=\d)\s*[~〜～-]\s*(?=[$¥￥€£]?\d)")

_UNIT_ALT = "|".join(re.escape(k) for k in sorted(_UNITS, key=len, reverse=True))
_NUMBER = re.compile(
    r"(?P<cur>[$¥￥€£])?(?P<sign>[-−－])?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)*)"
    r"(?:\s?(?P<unit>(?:" + _UNIT_ALT + r")(?![A-Za-z])))?"
)


def _skip(text: str, start: int) -> bool:
    """True when the match is glued onto a Latin word (GPT-4, iPhone15)."""
    return bool(_LATIN_BEFORE.search(text[max(0, start - 2):start]))


def _sub(pattern, fn, text):
    def _repl(m):
        return m.group(0) if _skip(m.string, m.start()) else fn(m)
    return pattern.sub(_repl, text)


def _plain(num: str) -> str:
    """'1,500' -> '1500', '23.5' -> '23点5' (commas and points are pauses)."""
    return num.replace(",", "").replace(".", "点")


def _phone(m):
    return "の".join("".join(_KATA_DIGIT[d] for d in g) for g in m.group(0).split("-"))


def _iso_date(m):
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)
    return f"{y}年{mo}月{d}日"


def _slash(m):
    a, b = int(m.group(1)), int(m.group(2))
    if 1 <= a <= 12 and 1 <= b <= 31:
        return f"{a}月{b}日"                     # 10/15 = October 15
    if b:
        return f"{b}ぶんの{a}"                   # 3/40; 分 alone reads as minutes
    return m.group(0)


def _time(m):
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return m.group(0)
    out = ("零" if h == 0 else str(h)) + "時"   # 0時 alone reads ゼロジ
    if mi:
        out += f"{mi}分"
    if m.group(3) and int(m.group(3)):
        out += f"{int(m.group(3))}秒"
    return out


def _number(m):
    text, start = m.string, m.start()
    if _skip(text, start):
        return m.group(0)
    out = ("マイナス" if m.group("sign") else "") + _plain(m.group("num"))
    unit = m.group("unit")
    if unit:
        before, after = _UNITS[unit]
        if before.endswith("速") and text[:start].rstrip().endswith("速"):
            before = ""                          # 風速5m/s already says "speed"
        out = before + out + after
    return out + _CURRENCY.get(m.group("cur") or "", "")


def _normalize(text: str) -> str:
    t = text.translate(_FULLWIDTH)
    for wide, ascii_ in (("．", "."), ("，", ","), ("：", ":"), ("／", "/")):
        t = re.sub(r"(?<=\d)" + wide + r"(?=\d)", ascii_, t)
    return t


def to_spoken(text: str) -> str:
    """Rewrite numbers (with units, times, dates) into the notation the voice
    engine reads naturally. Text with no digits comes back unchanged."""
    if not text:
        return text
    t = _normalize(text)
    if not re.search(r"\d", t):
        return text
    t = t.replace("PM2.5", PM25_SPOKEN)
    t = _sub(_ISO_DATE, _iso_date, t)
    t = _sub(_PHONE, _phone, t)
    t = _sub(_TIME, _time, t)
    t = _sub(_SLASH, _slash, t)
    t = _RANGE.sub("から", t)
    return _NUMBER.sub(_number, t)

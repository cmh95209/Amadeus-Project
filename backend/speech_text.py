# -*- coding: utf-8 -*-
"""Spoken-form rewriting for the voice (GPT-SoVITS).

Layer 1 = numbers (below). Layer 2 = the user's name: its saved kana reading
replaces the Latin spelling (see to_spoken / rule_reading). Layer 3 = other
English words -> katakana (pronunciation lists, acronyms, e2k).


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
letters ("GPT-4", "iPhone15") are left to layer 3, which converts the word
first.
"""
import os
import re
import threading

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


# --- layer 2: the user's name ------------------------------------------------
_LETTERS = {
    "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー", "F": "エフ",
    "G": "ジー", "H": "エイチ", "I": "アイ", "J": "ジェー", "K": "ケー", "L": "エル",
    "M": "エム", "N": "エヌ", "O": "オー", "P": "ピー", "Q": "キュー", "R": "アール",
    "S": "エス", "T": "ティー", "U": "ユー", "V": "ブイ", "W": "ダブリュー",
    "X": "エックス", "Y": "ワイ", "Z": "ゼット",
}
_KANA_ONLY = re.compile(r"[ぁ-ゖァ-ヺー・ 　]+")


def is_kana(text: str) -> bool:
    """True for a non-empty run of hiragana/katakana (a usable reading)."""
    return bool(text) and bool(_KANA_ONLY.fullmatch(text.strip()))


def spell_letters(text: str) -> str:
    """'JD' -> 'ジェーディー': Latin letters said one by one, Japanese style."""
    return "".join(_LETTERS.get(c.upper(), c) for c in text if not c.isspace())


def rule_reading(name: str) -> str:
    """A reading that needs no model call, or '' when one is needed:
    a kana name is its own reading; ALL-CAPS initials (2-4 letters) are
    spelled out. Everything else ('Alex', 'Sam', kanji) needs the model."""
    name = (name or "").strip()
    if is_kana(name):
        return name
    if re.fullmatch(r"[A-Z]{2,4}", name):
        return spell_letters(name)
    return ""


def _swap_name(text: str, name: str, reading: str) -> str:
    """Replace the user's name, as a whole word, with its spoken reading."""
    if not name or not reading or name == reading:
        return text
    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", name):
        pattern = r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])"
        return re.sub(pattern, reading, text, flags=re.IGNORECASE)
    return text.replace(name, reading)


# --- Japanese words the engine's dictionary misreads (2026-10-26) -------------
# pyopenjtalk splits 言ってね / 言って。 at the start of a phrase (or after
# ちゃんと, 時々, ...) into the noun 言 (ゲン) + the quote particle って:
# "ゲンッテネ". Writing the verb in kana gives the right イッテネ. Skipped
# when a kanji comes right before 言, so compounds keep their reading
# (発言って = ハツゲンッテ); 々 is a repeat mark, not a word part, so 時々言って
# is still fixed. Runs on every line, Japanese-only ones included.
_MISREAD = [
    (re.compile(r"(?<![㐀-䶿一-鿿豈-﫿])言って"), "いって"),
]


def _fix_misreadings(text: str) -> str:
    for pattern, spoken in _MISREAD:
        text = pattern.sub(spoken, text)
    return text


# --- layer 3: English words (2026-10-17) --------------------------------------
# Order: the user's pronunciation list, then the built-in one (exact, whole
# words, case-insensitive) -> ALL-CAPS acronyms spelled the Japanese way
# (GPU -> ジーピーユー) -> e2k (offline English->katakana model) for any
# other word. A run of 3+ English words is left alone: chat cleaning drops
# such phrases from the voice line, and a whole sentence in katakana sounds
# wrong. Latin units glued to a number ("16GB", "5 km") are left for the
# number layer. Without e2k installed, unknown words keep today's reading.
DEFAULT_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pronunciations_default.txt")
USER_LIST = os.path.join("data", "pronunciations.txt")   # personal, never in git
_USER_LIST_TEMPLATE = """\
# How she says particular words (her voice only - the text is unchanged).
# One per line:  word = カタカナ      Lines starting with # are ignored.
# Matching ignores upper/lower case. Your entries win over the built-in list
# (backend/pronunciations_default.txt). Changes apply to her next line.
#
# Steins;Gate = シュタインズ・ゲート
"""
_EN_NUMBERS = ["ゼロ", "ワン", "ツー", "スリー", "フォー", "ファイブ", "シックス",
               "セブン", "エイト", "ナイン", "テン"]
_UNIT_WORDS = {u for u in _UNITS if re.fullmatch(r"[A-Za-z/]+", u)}
_PHRASE = re.compile(r"[A-Za-z][A-Za-z']*(?:[ \t]+[A-Za-z][A-Za-z']*){2,}")
_WORD = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z']*)(-?)(\d{1,3})?(?![A-Za-z0-9])")
_list_cache: dict = {}
_e2k_state = {"tried": False, "c2k": None}
_e2k_cache: dict = {}
_e2k_lock = threading.Lock()


def _read_list(path: str) -> dict:
    """{word (casefolded): kana} from a 'word = カタカナ' file; cached by
    modification time AND size so an edit applies to the next line (two
    writes within one timestamp tick keep the same time - the size still
    changes; this made the user-list test flaky)."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    mtime = (st.st_mtime_ns, st.st_size)
    cached = _list_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    entries = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                word, reading = (part.strip() for part in line.split("=", 1))
                if word and is_kana(reading):
                    entries[word.casefold()] = reading
    except OSError:
        return {}
    _list_cache[path] = (mtime, entries)
    return entries


def _ensure_user_list() -> None:
    """Create the personal list with instructions the first time."""
    if os.path.exists(USER_LIST):
        return
    try:
        os.makedirs(os.path.dirname(USER_LIST), exist_ok=True)
        with open(USER_LIST, "w", encoding="utf-8") as f:
            f.write(_USER_LIST_TEMPLATE)
    except OSError:
        pass


def _pronunciations() -> dict:
    _ensure_user_list()
    merged = dict(_read_list(DEFAULT_LIST))
    merged.update(_read_list(USER_LIST))       # the user's entries win
    return merged


# ---------- an English sentence, or a name / title? (2026-10-24) ----------
# Her Japanese line may carry a product name or a title in Latin letters
# ("iPhone 18 Pro Max", 『My First Cookie Book』) - those stay on screen and her
# voice says them in katakana. Only real English sentences (a leaked
# translation) are removed from the line and left out of her voice.
_FUNCTION_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "am", "to", "of",
    "and", "or", "but", "in", "on", "at", "for", "with", "from", "by", "as",
    "it", "its", "it's", "this", "that", "these", "those", "i", "i'm", "you",
    "you're", "your", "we", "they", "he", "she", "my", "me", "our", "their",
    "do", "does", "did", "not", "so", "if", "what", "how", "can", "will",
    "just", "have", "has", "there", "here", "about", "like", "very",
}
_QUOTED = re.compile(r"『[^』]*』|「[^」]*」|“[^”]*”|【[^】]*】")


def is_english_sentence(run: str) -> bool:
    """True for English prose (40+ letters, or 3+ words with an everyday
    word such as "the" / "is" / "you"); False for a name or a title."""
    words = re.findall(r"[A-Za-z][A-Za-z']*", run)
    if sum(len(w) for w in words) >= 40:
        return True
    return len(words) >= 3 and any(w.lower() in _FUNCTION_WORDS for w in words)


def quoted_spans(text: str) -> list:
    """(start, end) of every 『…』「…」“…”【…】 in the text - a title inside
    Japanese quotes is never a leaked sentence."""
    return [m.span() for m in _QUOTED.finditer(text or "")]


def keeps_latin_run(text: str, start: int, run: str) -> bool:
    """Whether a Latin run in her Japanese line is a name or title to keep."""
    if any(a <= start < b for a, b in quoted_spans(text)):
        return sum(c.isalpha() for c in run) <= 60
    return not is_english_sentence(run)


# ---------- the shared name list (option C, 2026-10-22) ----------
# The same list also keeps her written line and the English line consistent:
# Settings -> "Names she knows" edits the user's entries; her prompt asks for
# them in katakana (ja_voice), a listed word still in Latin letters in her
# Japanese line is swapped (swap_listed_words), and the translator is told
# the English spelling of the listed names her line contains (names_in).

def _read_pairs(path: str) -> list:
    """[(word, kana)] in file order, the word as typed."""
    pairs = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                word, reading = (part.strip() for part in line.split("=", 1))
                if word and is_kana(reading):
                    pairs.append((word, reading))
    except OSError:
        pass
    return pairs


def load_user_names() -> list:
    """The user's own entries, [(word, kana)]."""
    _ensure_user_list()
    return _read_pairs(USER_LIST)


def save_user_names(pairs) -> list:
    """Replace the user's entries; the comment lines at the top are kept.
    Pairs with an empty word, a word holding '=' or a line break, or a
    reading that is not kana are dropped. Returns what was saved."""
    clean, seen = [], set()
    for word, reading in pairs:
        word, reading = (word or "").strip(), (reading or "").strip()
        if (not word or "=" in word or "\n" in word or not is_kana(reading)
                or word.casefold() in seen):
            continue
        seen.add(word.casefold())
        clean.append((word, reading))
    _ensure_user_list()
    header = []
    try:
        with open(USER_LIST, encoding="utf-8-sig") as f:
            for line in f:
                if not line.strip().startswith("#"):
                    break
                header.append(line.rstrip("\n"))
    except OSError:
        header = _USER_LIST_TEMPLATE.rstrip("\n").split("\n")
    with open(USER_LIST, "w", encoding="utf-8") as f:
        f.write("\n".join(header + [f"{w} = {r}" for w, r in clean]) + "\n")
    return clean


def swap_listed_words(text: str) -> str:
    """Her written Japanese line: every listed word still in Latin letters
    becomes its katakana (exact whole words, any case). A run of 3+ English
    words (a quoted phrase) is left alone, the same rule the voice uses."""
    if not text or not re.search(r"[A-Za-z]", text):
        return text
    entries = _pronunciations()
    if not entries:
        return text
    out, last = [], 0
    for m in _PHRASE.finditer(text):
        if not is_english_sentence(m.group(0)):
            continue  # a name or title: its listed words are swapped too
        out.append(_apply_list(text[last:m.start()], entries))
        out.append(m.group(0))
        last = m.end()
    out.append(_apply_list(text[last:], entries))
    return "".join(out)


def names_in(text: str) -> list:
    """[(word, kana)] for the listed readings her Japanese line contains, the
    user's entries first (one English spelling per reading)."""
    if not text:
        return []
    found, seen = [], set()
    for word, reading in load_user_names() + _read_pairs(DEFAULT_LIST):
        # A whole katakana word only: クリス, not the start of クリスマス.
        if reading in seen or not re.search(
                r"(?<![ァ-ヺー])" + re.escape(reading) + r"(?![ァ-ヺー])", text):
            continue
        seen.add(reading)
        found.append((word, reading))
    return found


def _apply_list(text: str, entries: dict) -> str:
    if not entries:
        return text
    alternatives = "|".join(re.escape(w) for w in sorted(entries, key=len, reverse=True))
    # Whole words, but a number may follow straight on (iPhone15).
    pattern = re.compile(r"(?<![A-Za-z])(?:" + alternatives + r")(?![A-Za-z])",
                         re.IGNORECASE)
    return pattern.sub(lambda m: entries[m.group(0).casefold()], text)


def _e2k_reading(word: str) -> str:
    """Katakana for one English word from e2k, or '' (not installed, or an
    answer that is not kana). Loaded once, on first use (~0.3 s)."""
    key = word.lower()
    if key in _e2k_cache:
        return _e2k_cache[key]
    with _e2k_lock:
        if not _e2k_state["tried"]:
            _e2k_state["tried"] = True
            try:
                from e2k import C2K
                _e2k_state["c2k"] = C2K()
            except Exception as exc:
                print(f"[AmadeusSpeak] e2k unavailable, English words keep their reading: {exc!r}")
        c2k = _e2k_state["c2k"]
    reading = ""
    if c2k is not None:
        try:
            reading = c2k(key)
        except Exception:
            reading = ""
    reading = reading if is_kana(reading) else ""
    _e2k_cache[key] = reading
    return reading


def _word_reading(word: str) -> str:
    if word.isupper() and 1 <= len(word) <= 5:
        return spell_letters(word)            # AI, GPU, OK, A (e.g. Aランク)
    if len(word) == 1:
        return ""
    return _e2k_reading(word)


def _english_to_kana(text: str) -> str:
    if not re.search(r"[A-Za-z]", text):
        return text
    text = _apply_list(text, _pronunciations())
    # Only unquoted English prose stays as it is (the chat cleaning drops it
    # from her line); a name or title of 3+ words, or anything she quotes
    # (iPhone 18 Pro Max, 『My First Cookie Book』), is said in katakana.
    quoted = quoted_spans(text)
    protected = [m.span() for m in _PHRASE.finditer(text)
                 if is_english_sentence(m.group(0))
                 and not any(a <= m.start() < b for a, b in quoted)]

    def _repl(m):
        start = m.start()
        if any(a <= start < b for a, b in protected):
            return m.group(0)
        word, hyphen, digits = m.group(1), m.group(2), m.group(3)
        before = text[:start]
        if (word in _UNIT_WORDS and re.search(r"\d\s?$", before)) \
                or before.endswith("°"):
            return m.group(0)                 # 16GB, 5 km, 23°C: the number layer's job
        reading = _word_reading(word)
        if not reading:
            return m.group(0)
        if digits is None:
            return reading + hyphen
        if word.isupper() and int(digits) <= 10:
            return reading + _EN_NUMBERS[int(digits)]    # GPT-4 -> ...フォー
        return reading + digits                # iPhone15 -> アイフォーン15
    text = _WORD.sub(_repl, text)
    # A dash between a converted word and its number is not a minus sign
    # (COVID-19 -> コビッド19), so the number layer must not see it.
    return re.sub(r"(?<=[ァ-ヺー])-(?=\d)", "", text)


def to_spoken(text: str, name: str = "", name_reading: str = "") -> str:
    """Rewrite text into the form the voice engine reads naturally:
    the user's name -> its saved reading, English words -> katakana, then
    numbers (with units, times, dates). Text with nothing to rewrite comes
    back unchanged."""
    if not text:
        return text
    swapped = _fix_misreadings(_swap_name(text, name, name_reading))
    normalized = _normalize(swapped)
    t = _english_to_kana(normalized.replace("PM2.5", PM25_SPOKEN))
    if not re.search(r"\d", t):
        return swapped if t == normalized else t
    t = _sub(_ISO_DATE, _iso_date, t)
    t = _sub(_PHONE, _phone, t)
    t = _sub(_TIME, _time, t)
    t = _sub(_SLASH, _slash, t)
    t = _RANGE.sub("から", t)
    return _NUMBER.sub(_number, t)

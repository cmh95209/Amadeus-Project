import memory as store
import builtin_llm
from llm import get_llm, reset_llm, maybe_strip_rejected_params
import llm as _llm_module
from pydantic import BaseModel, Field
from typing import Optional
from langchain_core.utils.function_calling import convert_to_openai_tool
from chat_interactions import INTERACTION_EVENTS, INTERACTION_RESPONSES
import random
import re
import threading
import time
import concurrent.futures
import difflib
from datetime import datetime
import stats
import timing
import ja_voice
import speech_text
import webdates
import webfetch
import webreader
import websearch
import weather
import cm_bridge

default_LLM_Model = store.DEFAULT_LLM_MODEL
API_KEY = store.load_api_key()
LLM_Model = store.load_llm_model(default_model=default_LLM_Model)


#pre: The intended new_model is a string e.g., "deepseek/deepseek-v3.2-exp"
#post: global LLM_Model should be changed to new_model
#      LLM_Model.txt should be updated accordingly, to store the latest model the user chose.
def setLLMModel(new_model: str):
    global LLM_Model
    LLM_Model = new_model.strip()
    store.save_llm_model(LLM_Model)
    reset_llm()  # IMPORTANT: recreate ChatOpenAI with the new model
    print("[Amadeus] Model changed to " + LLM_Model)


#pre:
#post: If LLM_Model is empty, return an Error message
#      else, return the LLM Model e.g., "deepseek/deepseek-v3.2-exp"
def getLLMModel():
    global LLM_Model
    if builtin_llm.active():
        return builtin_llm.model_name()   # what the built-in server serves
    return LLM_Model.strip() if LLM_Model else "No Model Selected."


#pre: key_string is a string in the format: "sk-or-v1-566...."
#post: API_KEY set to key_string
#      API_Key.txt should also be updated accordingly.
def setKey(key_string: str):
    global API_KEY
    next_key = key_string.strip()
    store.save_api_key(next_key)
    API_KEY = next_key
    reset_llm()  # IMPORTANT: recreate with new key
    print("[Amadeus] API key updated")


#pre: new_personality is new context for personality e.g., "This is Kurisu Makise....etc"
#post: Should update personality.txt using store.save_personality.
#      yes i know this is a bit round about, but to keep consistency.
def setPersonality(new_personality: str):
    store.save_personality(new_personality)
    print("[Amadeus] Updated personality!")


def getPersonality() -> str:
    return store.load_personality()


def has_api_key() -> bool:
    # The built-in model's server has no key, so the key gate never applies
    # to it (and no fake key goes into api_key.txt).
    return builtin_llm.active() or bool(API_KEY.strip())


#pre:
#post: memory.json should be erased
def resetMemory(conversation_id=None):
    store.reset_memory(conversation_id)
    print("[Amadeus] Memory Reset!")


#pre:
#post: returns a dict of JSON e.g., [{"role": "user", "content": "kurisu"....}....]
def get_raw_memory():
    return store.load_memory_raw()


class AmadeusPack(BaseModel):
    assistant_reply_JPS: str = Field(..., description=(
        "PRIMARY response: Amadeus's dialogue written natively in Japanese, as she would actually speak it. "
        "Must be plain spoken Japanese ONLY, for TTS - do NOT append the English translation or any "
        "English line/sentence to it (the English belongs solely in assistant_reply_ENG). "
        " Allowed: Japanese characters, ASCII letters/digits if needed, and these punctuation marks only: 、。！？"
        " Newlines are allowed. Do NOT include: parentheses/brackets/quotes/asterisks/emojis/markdown/ellipses (…)/colons/semicolons."
        " Avoid long dashes and repeated punctuation.")
    )
    assistant_reply_ENG: str = Field(..., description="English translation of assistant_reply_JPS, shown in the UI for the user to read. May include stage directions.")


class AmadeusReply(BaseModel):
    """Voice-first call 1 output: ONLY the Japanese line she actually speaks.

    The English display line is written by a separate, faster follow-up call
    (getOutputPackedVoiceFirst -> _translate_reply_en), so her voice can start
    while the translation is still being written. Same field constraints as
    AmadeusPack.assistant_reply_JPS.
    """
    assistant_reply_JPS: str = Field(..., description=(
        "Amadeus's dialogue written natively in Japanese, as she would actually speak it. "
        "Must be plain spoken Japanese ONLY, for TTS - this step has no English field, so do "
        "NOT append the English translation or any English line/sentence to it. "
        "Allowed: Japanese characters, ASCII letters/digits if needed, and these punctuation marks only: 、。！？"
        " Newlines are allowed. Do NOT include: parentheses/brackets/quotes/asterisks/emojis/markdown/ellipses (…)/colons/semicolons."
        " Avoid long dashes and repeated punctuation."
    ))
    

# pre:
# - message_context is a List[Dict[str, str]] with keys: "role" and "content"
# - message_context contains recent user/assistant messages only (no system persona)
# - personality is read from disk for each reply; internal system context is available
# - LLM (via LangChain + OpenRouter) is properly configured
#
# post:
# - returns an AmadeusPack with:
#     - assistant_reply_JPS: Japanese dialogue written natively (primary, for TTS)
#     - assistant_reply_ENG: English translation of it, for the UI
# - exactly ONE LLM call is made under normal operation
# - on structured output failure, falls back to a plain LLM call with a safe default Japanese reply


# =============================================================================
#  WEB ACCESS (toggleable)
#
#  When the web-access toggle is ON, Amadeus is given a "web_search" tool
#  alongside her normal reply format. She decides for herself when she needs
#  fresh information (news, today's date, current events...). The search runs
#  through Firecrawl's free keyless search (DuckDuckGo-style `ddgs` as the
#  backup) - no API key, no cloud account.
# =============================================================================

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for up-to-date information from live sources. Use it for today's "
            "date, current events, news, sports results, weather, prices - and for ANY specific "
            "thing the user mentions that may postdate your knowledge: recent movie, TV, anime "
            "or game releases, gacha banners or characters, product versions, software updates, "
            "and similar. When the user says they recently saw, played, bought or experienced "
            "something specific, search FIRST and tailor your reply to what you find. "
            "Do not use it for timeless general knowledge or pure small talk. "
            "Write the query WITHOUT a year unless the user named one - today's date is in "
            "your RIGHT NOW note, and an old year brings back old pages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A short, specific search query."},
            },
            "required": ["query"],
        },
    },
}


# Standing instruction injected into her system prompt whenever the web toggle is ON.
# It is what makes her PROACTIVELY search: she checks her own knowledge gap before
# answering, instead of only searching when explicitly asked.
PROACTIVE_SEARCH_BLOCK = {
    "role": "system",
    "content": (
        "PROACTIVE WEB SEARCH (the web_search tool is available for this reply): "
        "Before answering, ask yourself: \u201cDoes this message refer to something specific that "
        "could be new or have changed - a recent movie, show, episode, game, gacha banner or "
        "character, product, version, release, event, price, or news?\u201d "
        "- If YES: search FIRST (you may run up to two searches), then use what you find to "
        "tailor your reply. Aim to surprise the user with specific, fresh details - the exact "
        "new character on a banner, an actual plot point of the latest film, the real result - "
        "not vague generalities. "
        "- If NO (timeless facts, small talk, or something you already know well): just answer. "
        "Do not search. "
        "Weather and air-quality questions are answered automatically from live data - "
        "never use web_search for them. "
        "Treat search results as hints, not gospel: if a result seems wrong or you are more "
        "confident in your own knowledge, say so honestly. "
        # 2026-10-25: with ddgs she often presented a years-old article as
        # today's news. Results now carry a Date line where one is known.
        "Check each result's Date against today's date before you use it: never present "
        "old news as new or a past event as upcoming, and when the newest thing you found "
        "is not recent, say how old it is. "
        # 2026-10-25: schedules (game banners, releases, events, fixtures)
        # were answered with the wrong entry - "current" mixed up with "next".
        "For schedules (game banners, releases, events, sports fixtures): use the start "
        "and end dates to say what is running TODAY, name what comes next separately with "
        "its start date, and call anything that has not happened yet \"upcoming\". "
        # 2026-10-25 (Amadeus-Fresh test): asked to "confirm" web access, she
        # searched for the current time and believed a stale page over her
        # clock ("14:05 UTC, 20 February 2026").
        "The current date and time are in your RIGHT NOW note - always use those; never "
        "search for them, and if a search result shows a different date or time, your "
        "RIGHT NOW note is right. Never say you looked something up, checked online or "
        "have “the latest data” unless you actually ran web_search in this reply."
    ),
}


NO_WEB_BLOCK = {
    "role": "system",
    "content": (
        "WEB ACCESS IS CURRENTLY OFF in this session - you do NOT have the web_search tool. "
        "If the user asks you to search the web, look something up online, check a website, or "
        "verify something recent or current, be honest and tell them you cannot search the web "
        "right now. Do NOT pretend you searched, looked it up, or read anything online. You may "
        "still answer from your own knowledge, but be clear that you are not verifying it live. "
        "If they ask for current weather or air quality, be honest that you cannot check it "
        "live while web access is off."
    ),
}


# --- Web-search resilience (DuckDuckGo via the `ddgs` library) ----------------
# The free DuckDuckGo endpoint is flaky: the connection sometimes gets dropped
# mid-request (the "peer closed without TLS close_notify" h2 errors), which makes
# a single attempt fail while a repeat a second later succeeds. So:
#   (1) we retry a couple of times (a FRESH connection on each retry, since
#       retrying over the one that just dropped is pointless), and
#   (2) we reuse one client across searches, so a follow-up search reuses the
#       warm connection (a touch faster + fewer drops) instead of opening new.
#
# Every path is BOUNDED so a dead network can never stall a chat reply for long.
# A search was ALWAYS part of the reply time (it runs before she answers); these
# knobs just make it more likely to succeed within the SAME time it had before,
# and guarantee it never meaningfully exceeds it.
#
# Firecrawl (websearch.py) answers first, in one bounded attempt; the ddgs
# search below is its fallback and runs on whatever is left of the budget
# (2026-10-25: ddgs' single engines mostly returned nothing in a live check;
# only its mixed "auto" mode still answered).
FIRECRAWL_TIMEOUT = 6           # seconds, Firecrawl's one attempt
KEENABLE_TIMEOUT = 5            # seconds, Keenable's one attempt (the second engine)
SEARCH_ATTEMPT_TIMEOUT = 8      # seconds, per attempt (passed to DDGS as its timeout)
SEARCH_TOTAL_BUDGET = 15        # seconds, hard ceiling for the whole search (was 15)
SEARCH_MIN_RETRY_BUDGET = 5     # seconds; don't start another attempt if less is left
SEARCH_MAX_ATTEMPTS = 3         # absolute cap on attempts (belt-and-suspenders)
SEARCH_BACKOFF_START = 0.6      # seconds, pause before the 1st retry
SEARCH_BACKOFF_MAX = 1.2        # seconds, cap on the retry pause
SEARCH_CLIENT_TTL = 60.0        # seconds; recreate the client after this much idle

_SEARCH_CLIENT = None           # shared DDGS client (holds one warm connection pool)
_SEARCH_CLIENT_TS = 0.0         # monotonic time the client was last created/used
_SEARCH_CLIENT_LOCK = threading.Lock()

# --- Page reading: the readable-content step on top of web search -------------
# After a search, the result pages are read (reader or quick mode, see
# _read_results and webreader.py), so she answers from real data (numbers,
# details) instead of one-line snippets. Bounded by READ_BUDGET_*; each page
# is capped to a fraction of the context window (webfetch.char_budget). Any
# fetch or reader problem degrades to the plain results, so it can never
# break or hang a reply.

# --- Weather fast path (live Open-Meteo data beats search) ------------------
# A clear weather question is pulled DIRECTLY from the keyless Open-Meteo API
# (weather.fetch_weather_report) and fed to her as a plain-text data block -
# the same load-bearing pattern the search uses, so a small model can neither
# stall on a tool round-trip nor "improve" the numbers with invention.
# Detection is a deterministic keyword check (never the model's judgement),
# which is what makes the API win over web_search BY DESIGN: an explicit
# weather question always takes this path, including over the explicit
# "search for X" fast path. The whole thing runs only on the web-ON path
# (the search loop is never entered when web access is off).
_WEATHER_INTENT_RE = re.compile(
    r"\b(weather|forecast|temperature|temperatures|humidity|humid|"
    r"air\s+quality|aqi|uv\s+index|barometric\s+pressure)\b"
    r"|\bwill\s+it\s+(rain|snow|hail|freeze)\b"
    r"|\bis\s+it\s+(raining|snowing|freezing|hot|cold|warm|chilly|sunny|windy)\b"
    r"|\bhow\s+(hot|cold|warm|chilly)\s+is\s+it\b"
    r"|\bchance\s+of\s+(rain|snow|precipitation)\b"
    r"|天気|気温|降水確率|降水量|湿度|大気污染|空気質|台風|熱帯低気圧",
    re.IGNORECASE,
)

# Tokens stripped when isolating a place name from a weather question: the
# question scaffolding, the weather words themselves, time words, and words
# that point at "somewhere I already am" (which means: she asks, never guesses).
_PLACE_STOPWORDS = {
    # scaffolding
    "what", "whats", "what's", "how", "hows", "how's", "it", "it's", "its",
    "is", "are", "was", "were", "isn't", "wasn't", "will", "would", "can",
    "could", "should", "do", "does", "did", "don't", "doesn't", "the", "a",
    "an", "of", "for", "in", "at", "on", "to", "me", "you", "please", "tell",
    "give", "show", "check", "about", "like", "any", "some", "there", "this",
    "that", "these", "those", "be", "going", "feel", "feels", "feeling",
    "look", "looking", "next", "my", "your", "our", "near", "around", "trip",
    "travel", "travelling", "traveling",
    "search", "searches", "searched", "searching", "find", "finds", "found",
    "finding", "google", "googled", "web", "internet", "online", "lookup",
    "looked",
    # time words
    "today", "tomorrow", "tonight", "now", "currently", "right", "morning",
    "afternoon", "evening", "night", "week", "weekend", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "day", "days",
    # weather words
    "weather", "forecast", "temperature", "temperatures", "humidity",
    "humid", "air", "quality", "aqi", "uv", "index", "rain", "raining",
    "snow", "snowing", "hail", "hailing", "freeze", "freezing", "hot",
    "cold", "warm", "chilly", "sunny", "windy", "wind", "breeze", "gale",
    "storm", "storms", "precipitation", "chance", "expect", "expected",
    "expecting", "degrees", "celsius", "fahrenheit", "outlook", "report",
    "conditions", "s",
    # non-places: "somewhere I already am" -> she asks, never guesses
    "here", "outside", "inside", "area", "vicinity", "home", "house",
    "hometown", "town", "city", "local",
}


def _weather_intent(message: str) -> bool:
    """True when the message is a clear weather question (deterministic)."""
    return bool(_WEATHER_INTENT_RE.search(message or ""))


def _extract_place(message: str) -> str:
    """Pull a place name out of a weather question, original casing kept.

    Strips the scaffolding / weather / time words (case-insensitive); the
    alphabetic tokens that survive (max 4) are the place candidate. Returns
    "" when nothing plausible survives (bare "weather", "the weather in my
    area") - the caller then has her ASK which town, never guess. Unspaced
    scripts (e.g. Japanese) carry no extractable Latin place -> "".
    """
    if not message or not _has_latin(message):
        return ""
    kept = []
    for tok in re.findall(r"[\w'\u2019\-]+", message, re.UNICODE):
        low = tok.lower()
        if low in _PLACE_STOPWORDS:
            continue
        if not any(c.isalpha() for c in low):
            continue
        kept.append(tok)
        if len(kept) == 4:
            break
    return " ".join(kept).strip()


def _get_search_client(force_fresh: bool = False):
    """Return a DDGS client to run a search on, reusing a warm one when possible.

    A fresh client (fresh connection) is created when `force_fresh` is True - i.e.
    for a retry, so we never retry over the exact connection that just dropped - or
    when the cached client has been idle longer than SEARCH_CLIENT_TTL (the server
    has almost certainly closed that connection by then). Reusing the client reuses
    its underlying connection pool, which is the actual "keep-alive" benefit.
    """
    global _SEARCH_CLIENT, _SEARCH_CLIENT_TS
    now = time.monotonic()
    if (
        not force_fresh
        and _SEARCH_CLIENT is not None
        and (now - _SEARCH_CLIENT_TS) <= SEARCH_CLIENT_TTL
    ):
        _SEARCH_CLIENT_TS = now  # mark as recently used
        return _SEARCH_CLIENT
    from ddgs import DDGS
    client = DDGS(timeout=SEARCH_ATTEMPT_TIMEOUT)
    with _SEARCH_CLIENT_LOCK:
        _SEARCH_CLIENT = client
        _SEARCH_CLIENT_TS = now
    return client


def _invalidate_search_client(client):
    """Drop a search client from the cache (e.g. right after it failed), so a dead
    connection is never reused. No-op if it isn't the currently cached one."""
    global _SEARCH_CLIENT
    with _SEARCH_CLIENT_LOCK:
        if _SEARCH_CLIENT is client:
            _SEARCH_CLIENT = None


# Closes every result list: old news must not read as new (with ddgs she often
# presented a years-old article as today's news, 2026-10-25).
SEARCH_DATE_NOTE = (
    "(Today is {today}. A \"Date\" line is when that page was published or "
    "last updated. A result with an older Date or no Date may be old news: never "
    "call something new, recent, latest or upcoming unless its Date or text shows "
    "it is recent, and say how old it is when it is not.)")


def _format_search_results(results) -> str:
    """The numbered "title / snippet / [Date] / URL" text the model (and the
    source parser below) reads - the same for every engine - closed by
    SEARCH_DATE_NOTE."""
    lines = []
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or "").strip()
        when = (r.get("date") or "").strip()
        dated = f"Date: {when}\n" if when else ""
        lines.append(f"{i}. {title}\n{body}\n{dated}URL: {href}")
    # Titles and snippets come from web pages: data, never instructions.
    return (webreader.wrap_web_data("\n\n".join(lines)) + "\n\n"
            + SEARCH_DATE_NOTE.format(today=datetime.now().strftime("%Y-%m-%d")))


def _firecrawl_call(q: str, timeout: float, freshness=None):
    """One Firecrawl search; None when it could not answer (logged)."""
    try:
        return websearch.firecrawl_search(q, max_results=5, timeout=timeout, freshness=freshness)
    except websearch.FirecrawlUnavailable as exc:
        print(f"[Amadeus] Firecrawl search unavailable: {exc}")
    except Exception as exc:
        print(f"[Amadeus] Firecrawl search failed: {exc!r}")
    return None


def _firecrawl_results(q: str, freshness, deadline: float) -> list:
    """Firecrawl's results for `q`; [] when it fails or finds nothing.

    A time-sensitive query (websearch.freshness_for) is first limited to recent
    pages; when that leaves fewer than two results, an unlimited search tops
    them up (the recent ones stay first) - but only on time that still leaves
    the ddgs fallback a full attempt inside the deadline."""
    results = _firecrawl_call(q, FIRECRAWL_TIMEOUT, freshness)
    if results is None:
        return []
    if freshness and len(results) < 2:
        spare = deadline - time.monotonic() - SEARCH_ATTEMPT_TIMEOUT
        if spare >= 1.5:
            more = _firecrawl_call(q, min(FIRECRAWL_TIMEOUT, spare)) or []
            seen = {r["href"] for r in results}
            results = (results + [r for r in more if r["href"] not in seen])[:5]
    if not results:
        print(f"[Amadeus] Firecrawl found nothing for {q!r}, trying ddgs")
    return results


def _keenable_call(q: str, timeout: float, freshness=None):
    """One Keenable search; None when it could not answer (logged)."""
    try:
        return websearch.keenable_search(q, max_results=5, timeout=timeout, freshness=freshness)
    except websearch.KeenableUnavailable as exc:
        print(f"[Amadeus] Keenable search unavailable: {exc}")
    except Exception as exc:
        print(f"[Amadeus] Keenable search failed: {exc!r}")
    return None


STATE_MERGE_MAX = 8   # results kept when a "what is on now" search is merged


def _keenable_results(q: str, freshness, deadline: float, state: bool = False) -> list:
    """Keenable's results for `q` (the second engine); [] when it fails, finds
    nothing or there is too little time left (ddgs keeps a full attempt).
    Recent pages first, topped up like _firecrawl_results. A "what is on now"
    question (state) searches WITHOUT the recent-only limit first - that is
    where the schedule pages are - then adds the recent pages it lacks."""
    def room():
        return deadline - time.monotonic() - SEARCH_ATTEMPT_TIMEOUT
    if room() < 1.5:
        return []
    if state and freshness:
        # Both searches at once (each ~0.5 s); the unlimited one leads.
        timeout = min(KEENABLE_TIMEOUT, room())
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            plain_f = pool.submit(_keenable_call, q, timeout)
            recent_f = pool.submit(_keenable_call, q, timeout, freshness)
            results, recent = plain_f.result(), recent_f.result()
        if results is None and recent is None:
            return []
        results, recent = results or [], recent or []
        seen = {r["href"] for r in results}
        results = (results + [r for r in recent if r["href"] not in seen])[:STATE_MERGE_MAX]
        if not results:
            print(f"[Amadeus] Keenable found nothing for {q!r}, trying ddgs")
        return results
    results = _keenable_call(q, min(KEENABLE_TIMEOUT, room()), freshness)
    if results is None:
        return []
    if freshness and len(results) < 2 and room() >= 1.5:
        more = _keenable_call(q, min(KEENABLE_TIMEOUT, room())) or []
        seen = {r["href"] for r in results}
        results = (results + [r for r in more if r["href"] not in seen])[:5]
    if not results:
        print(f"[Amadeus] Keenable found nothing for {q!r}, trying ddgs")
    return results


def _note_search_engine(name) -> None:
    """Remember which engine answered the latest search in this thread (for
    the log line and the reply's saved sources)."""
    _turn.engine = name


def _run_web_search(query: str) -> str:
    """Run a web search and return compact results for the model.

    Firecrawl (websearch.py, keyless) is tried first, then Keenable (keyless,
    the second engine); when both fail, are slow, find nothing or are out of
    free use, the DuckDuckGo-style `ddgs` search runs (with a couple of
    bounded retries) on the time that is left. A time-sensitive query is
    limited to recent pages on every engine.
    See the SEARCH_* constants above for timing."""
    q = (query or "").strip()
    _note_search_engine(None)
    if not q:
        return "No web results were found for that query."
    deadline = time.monotonic() + SEARCH_TOTAL_BUDGET
    freshness = websearch.freshness_for(q)
    # "What is on now" (a banner, a schedule, standings): schedule pages are
    # updated, not new - no recent-only limit for them (Keenable adds the
    # recent pages after the others). Remembered for picking the pages.
    state = websearch.is_state_question(q, getattr(_turn, "question", "") or "")
    _turn.state_question = state

    results = _firecrawl_results(q, None if state else freshness, deadline)
    if results:
        _note_search_engine("firecrawl")
        return _format_search_results(results)
    _progress("retry", "The main search didn't answer, trying the second search...")
    results = _keenable_results(q, freshness, deadline, state=state)
    if results:
        _note_search_engine("keenable")
        return _format_search_results(results)
    _progress("retry", "Trying the backup search...")

    try:
        from ddgs import DDGS  # noqa: F401  - lazy check; fails fast + clearly
    except Exception as exc:
        print(f"[Amadeus] Web search unavailable (could not load the 'ddgs' library): {exc!r}")
        return (
            "Web search is not available on this machine right now. Answer from "
            "your own knowledge and be honest that you could not verify it online."
        )

    backoff = SEARCH_BACKOFF_START
    attempt = 0
    last_err = None
    while True:
        attempt += 1
        # Attempt 1 reuses the warm client (fast). Retries use a fresh connection.
        client = _get_search_client(force_fresh=(attempt > 1))
        # Attempt 1 of a time-sensitive query asks for recent pages only;
        # a retry asks without the limit (ddgs reports "no results" as an error).
        limit = {"timelimit": freshness} if (freshness and not state and attempt == 1) else {}
        try:
            results = list(client.text(q, max_results=5, **limit))
        except Exception as exc:
            last_err = exc
            print(f"[Amadeus] Web search attempt {attempt} failed: {exc!r}")
            # Drop the failed client so we don't keep reusing a dead connection.
            _invalidate_search_client(client)
            if attempt >= SEARCH_MAX_ATTEMPTS:
                break
            remaining = deadline - time.monotonic()
            if remaining < SEARCH_MIN_RETRY_BUDGET:
                break  # not enough time left for a meaningful retry
            time.sleep(min(backoff, max(0.0, remaining - 0.5)))
            backoff = min(backoff * 2, SEARCH_BACKOFF_MAX)
            continue
        # A result came back (possibly an empty list). A clean zero-result
        # answer is just as unverifiable as a failed search, so it carries
        # the same honesty instruction (2026-10-08: the failed path had it,
        # this one didn't - a model could read "no results" as "I searched"
        # and present its own knowledge as search-verified).
        if not results and limit and attempt < SEARCH_MAX_ATTEMPTS:
            continue  # nothing recent: ask again without the time limit
        if not results:
            return (
                "No web results were found for that query. Answer from your own "
                "knowledge and be honest that you could not verify it online "
                "right now. Do not mention any technical details."
            )
        _note_search_engine("ddgs")
        return _format_search_results(results)

    # Exhausted the budget without results. The raw error is logged above for
    # debugging; the model only gets a clean, human instruction (no technical junk).
    print(f"[Amadeus] Web search failed after {attempt} attempt(s): {last_err!r}")
    return (
        "The web search did not return anything this time. Answer from your own "
        "knowledge and be honest that you could not verify it online right now. "
        "Do not mention any technical details."
    )


def _extract_search_urls(result_text) -> list:
    """Pull result URLs (in rank order) out of _run_web_search output."""
    if not isinstance(result_text, str):
        return []
    return re.findall(r"^URL:\s*(\S+)\s*$", result_text, re.MULTILINE)


# --- The reply's sources (2026-10-25) -----------------------------------------
# Every web search and live-weather fetch made while ONE reply is written is
# collected here (query, result titles + links, the page actually read), then
# saved with the reply's row (memory.set_message_sources) - the base for
# showing her links in the chat later. Per thread: a reply is written start
# to finish in one request thread (getResponsePacked begins the list,
# take_turn_sources hands it over and ends it).
_turn = threading.local()


def _begin_turn_sources(conversation_id=None) -> None:
    _turn.sources = []
    # The chat this reply belongs to: lookups are saved / reused only inside
    # a real reply (searches outside one - tests, tools - leave no trace).
    _turn.conversation = conversation_id


def take_turn_sources() -> list:
    sources = getattr(_turn, "sources", None) or []
    _turn.sources = None
    _turn.conversation = None
    return sources


def _record_source(entry: dict) -> None:
    sources = getattr(_turn, "sources", None)
    if sources is not None:
        sources.append(entry)


def _lookup_ran() -> bool:
    """Did this reply search the web or fetch live data (so far)?"""
    return bool(getattr(_turn, "sources", None))


_RESULT_RE = re.compile(r"^\d+\. (.*)\n((?:.*\n)*?)URL: (\S+)\s*$", re.MULTILINE)
_DATE_LINE_RE = re.compile(r"^Date: (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)


def _parse_search_results(result_text) -> list:
    """[{"title", "url"(, "date")}] in rank order from _run_web_search output
    ("date" only when the result carried a Date line)."""
    if not isinstance(result_text, str):
        return []
    out = []
    for title, between, url in _RESULT_RE.findall(result_text):
        item = {"title": title.strip(), "url": url}
        dated = _DATE_LINE_RE.search(between)
        if dated:
            item["date"] = dated.group(1)
        out.append(item)
    return out


# Her own searches for the CURRENT time or date are not run: a search engine
# returns stale clock pages, and she believed one over the clock in her
# RIGHT NOW note (2026-10-25). Only a pure "what time / date is it" query is
# caught - "current time in Kuala Lumpur" names a place and still searches.
_CLOCK_QUERY_RE = re.compile(
    r"\b(current|today'?s?|what)\s+(local\s+)?(time|date|day)\b|\btime\s+now\b"
    r"|\bwhat\s+time\s+is\s+it\b|\bdate\s+today\b"
    r"|今何時|現在(の)?時刻|現在時刻|今日の日付|今日は何日|何曜日|今の時間",
    re.IGNORECASE)
_CLOCK_FILLER_RE = re.compile(
    r"\b(current|currently|today'?s?|what|is|it|the|right|now|local|time|date|day|"
    r"utc|gmt|clock|exact|precise|of|and|year|month|in|my|zone|timezone|"
    r"standard|coordinated|universal)\b|[?？!.,、。\s]|何曜日|何日|何時|今日|今|現在|時刻|時間|日付|の|は|です|か",
    re.IGNORECASE)
CLOCK_SEARCH_SKIPPED = (
    "(No search was run: the current date and time are already in your RIGHT "
    "NOW note - use those. Do not say you looked them up.)")


def _is_clock_query(query) -> bool:
    q = (query or "").strip()
    if not q or not _CLOCK_QUERY_RE.search(q):
        return False
    return not _CLOCK_FILLER_RE.sub("", q).strip()


# --- Reading the pages: reader mode / quick mode (2026-10-25) ------------------
# Reader mode (Settings "thorough", the default): the top pages are fetched in
# parallel and one short reader call per page writes notes (facts + dates +
# quotes, or NONE); her reply is written from the notes, never from raw page
# text. Quick mode: the best paragraphs of the top page. See webreader.py.
# Where reader calls cannot run side by side (a local server with ONE slot),
# a reader call would also push her saved prompt out of the server's memory
# -> quick reading there. Her built-in model has 2 slots (-np 2): the reader
# calls use slot 1 so slot 0 keeps her prompt (fast replies).
READ_BUDGET_LOCAL = 12       # seconds: fetching + reading, local / built-in
READ_BUDGET_CLOUD = 10       # seconds: fetching + reading, cloud
READER_MAX_TOKENS = 300      # one page's notes
READER_SLOT = 1              # the built-in server's second slot
_SLOTS_CACHE = {}            # base url -> (slot count or None, checked at)
_SLOTS_TTL = 600.0


def _progress(step: str, text: str) -> None:
    """Tell the screen what the search is doing right now (only when a
    progress listener is attached to this reply - the web-ON stream)."""
    fn = getattr(_turn, "progress", None)
    if fn:
        try:
            fn(step, text)
        except Exception:
            pass


def _server_slots(base_url: str):
    """How many requests a llama.cpp-style server runs at once (its /props
    total_slots), or None when it does not say. Cached for a few minutes."""
    now = time.monotonic()
    hit = _SLOTS_CACHE.get(base_url)
    if hit and now - hit[1] < _SLOTS_TTL:
        return hit[0]
    slots = None
    try:
        import json as _json
        import urllib.request as _ur
        root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
        with _ur.urlopen(root + "/props", timeout=2) as resp:
            slots = int(_json.loads(resp.read().decode("utf-8", "replace")).get("total_slots") or 0) or None
    except Exception:
        slots = None
    _SLOTS_CACHE[base_url] = (slots, now)
    return slots


def web_reading_plan():
    """(mode, parallel, pin_slot, local) for this search: mode "reader" |
    "quick"; parallel = reader calls at once; pin_slot = the server slot the
    reader calls use (built-in only), else None; local = a model server on
    this PC / network (vs a cloud service). Instant: no port probing."""
    try:
        base = _llm_module._configured_server_url()
        local = bool(_llm_module._is_local_host(base))
    except Exception:
        base, local = "", True
    if store.load_web_reading() == "quick":
        return "quick", 0, None, local
    builtin = getattr(_llm_module, "builtin_llm", None)
    try:
        if builtin and builtin.active():
            return "reader", 1, READER_SLOT, True
    except Exception:
        pass
    if not local:
        return "reader", webreader.MAX_PAGES, None, False
    if base and _server_slots(base) == 1:
        return "quick", 0, None, True
    return "reader", 1, None, True


def web_reading_reason() -> str:
    """One line for Settings about what is used right now, and why."""
    mode = web_reading_plan()[0]
    if store.load_web_reading() == "quick":
        return "Quick reading (your choice)."
    if mode == "quick":
        return ("Your model server runs one task at a time, so she uses quick "
                "reading.")
    return "She reads the top pages and takes notes before answering."


def _reader_call(pin_slot):
    """The reader call: her model, reasoning off, a short answer; on her
    built-in server pinned to the second slot."""
    client = get_llm(API_KEY, LLM_Model)

    def call(messages, timeout):
        kwargs = {"max_tokens": READER_MAX_TOKENS, "timeout": max(1.0, float(timeout))}
        if pin_slot is not None:
            kwargs["extra_body"] = {**(getattr(client, "extra_body", None) or {}),
                                    "id_slot": pin_slot}
        reply = client.bind(**kwargs).invoke(messages)
        content = reply.content if isinstance(reply.content, str) else str(reply.content or "")
        return _strip_thinking(re.sub(r"(?is)<think>.*?</think>", "", content))
    return call


def _site_list(pages) -> str:
    return ", ".join(webreader._site(p["url"]) for p in pages)


def _read_results(query: str, results: list, source: dict):
    """Read the result pages (reader or quick mode) -> (the text appended after
    the search results, "" when nothing could be read; the pages read). Fills
    the source's "read" (the page she relied on), "used" (pages that gave
    notes) and "mode"."""
    mode, parallel, pin, local = web_reading_plan()
    deadline = time.monotonic() + (READ_BUDGET_LOCAL if local else READ_BUDGET_CLOUD)
    question = getattr(_turn, "question", "") or ""
    page_chars = webfetch.char_budget(store.effective_context_budget())
    candidates = [{"url": r["url"], "title": r.get("title", ""), "date": r.get("date", "")}
                  for r in results]
    # "What is on now": the pages whose dates include today are read first
    # (a few more results are opened to find them).
    state = bool(getattr(_turn, "state_question", False))
    _progress("reading", "Opening the top pages...")
    pages = webreader.fetch_pages(
        candidates, lambda url, timeout: webfetch.fetch_page(url, timeout=timeout),
        deadline, progress=_progress,
        max_pages=webreader.MAX_PAGES if mode == "reader" else 1,
        max_tries=(webreader.MAX_TRIES + (1 if state else 0)) if mode == "reader" else (3 if state else 2),
        prefer=(lambda p: webdates.covers_today(p.get("text", ""))) if state else None)
    source["mode"] = mode
    if not pages:
        return "", []
    _progress("reading", "Reading " + _site_list(pages) + "...")
    if mode == "reader":
        today = datetime.now().strftime("%Y-%m-%d")
        webreader.read_pages(question, query, pages, _reader_call(pin), deadline, today,
                             min(page_chars, 10000), parallel=parallel, progress=_progress,
                             today_first=state)
        used = [p for p in pages if p.get("notes")]
        source["used"] = [p["url"] for p in used]
        if used:
            source["read"] = used[0]["url"]
            return webreader.notes_block(pages), pages
        if any(p["status"] == "none" for p in pages):
            source["read"] = None
            return (PAGES_DID_NOT_ANSWER, pages)
        print("[Amadeus] Reader calls gave nothing in time - quick reading instead.")
        source["mode"] = "quick"
    source["read"] = pages[0]["url"]
    source["used"] = [pages[0]["url"]]
    return webreader.quick_block(pages[0], query, question, page_chars, today_first=state), pages[:1]


PAGES_DID_NOT_ANSWER = ("(The pages that were read did not answer this. Say so honestly "
                        "instead of guessing.)")


# --- Her "hold on, let me check" line (2026-10-25) ------------------------------
# The moment a real search starts (not when saved notes are reused, not for
# the weather or a clock question), a short line of hers is written IN
# PARALLEL with the search: "The banner? Hang on, I'll check." It is saved as
# her own message (messages.aside) and spoken at once - unless her answer is
# ready first, then it is dropped (never "hold on" after the answer). Only
# for the live web-ON reply (the route attaches a listener); never for
# regenerate. Not mirrored to the long-term memory sidecar.
ASIDE_MAX_TOKENS = 160
ASIDE_NOTE = (
    "\n\n(Internal note: you are about to look this up on the web for the user right "
    "now: \"{topic}\". Say ONE short spoken line, in character, telling them you are "
    "checking it now - like someone glancing at their phone. Name what you are checking "
    "in a few words. Do NOT answer, guess or state any facts about it yet, do not promise "
    "results, do not ask a question. Keep it to one short sentence.{avoid})")
# Her earlier hold-on lines in this chat, so she does not say the same one
# again (2026-10-26: two in a row were word for word 「ちょっと待って、今調べて
# みるわね。」). A line still this similar to one of them gets ONE retry.
ASIDE_RECENT = 4
ASIDE_SAME_RATIO = 0.8


class _AsideRun:
    """One hold-on line being written in the background for one reply."""

    def __init__(self):
        self.lock = threading.Lock()
        self.closed = False
        self.saved_id = None
        self.thread = None


def _maybe_start_aside(topic: str) -> None:
    """Start her hold-on line for this reply (once), if the live route asked."""
    emit = getattr(_turn, "aside_emit", None)
    if not emit or getattr(_turn, "aside_run", None) is not None:
        return
    context = getattr(_turn, "reply_context", None)
    conv = getattr(_turn, "conversation", None)
    if not context or conv is None:
        return
    run = _AsideRun()
    _turn.aside_run = run
    run.thread = threading.Thread(target=_write_aside, args=(run, topic, list(context), conv, emit),
                                  daemon=True)
    run.thread.start()


def _aside_avoid_text(lines) -> str:
    if not lines:
        return ""
    quoted = "、".join(f"「{x.strip()}」" for x in lines if x and x.strip())
    return (" Word it differently from your earlier hold-on lines in this chat: "
            + quoted + ".") if quoted else ""


def _aside_too_similar(line: str, lines) -> bool:
    return any(difflib.SequenceMatcher(None, line.strip(), (x or "").strip()).ratio()
               >= ASIDE_SAME_RATIO for x in lines or [])


def _aside_messages(topic: str, context, local: bool, avoid=None):
    """Her prompt for the hold-on line: the same fixed blocks as her reply
    (the built-in model reuses them), the conversation, and the note on the
    latest user message (one message, so the roles still alternate)."""
    ctx = [dict(m) for m in context]
    for m in reversed(ctx):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            m["content"] = m["content"] + ASIDE_NOTE.format(
                topic=topic.strip()[:200], avoid=_aside_avoid_text(avoid))
            break
    return _ordered_prompt(_PLAIN_RULES_JA if local else _PACK_RULES, PROACTIVE_SEARCH_BLOCK,
                           [], ctx, right_now=_right_now_note())


def _write_aside(run: "_AsideRun", topic: str, context, conv, emit) -> None:
    started = time.monotonic()
    try:
        client = get_llm(API_KEY, LLM_Model).bind(max_tokens=ASIDE_MAX_TOKENS)
        local = _is_local(client)
        try:
            recent = store.recent_aside_lines(conv, ASIDE_RECENT)
        except Exception:
            recent = []

        def write(avoid):
            messages = _aside_messages(topic, context, local, avoid)
            if local:
                return _two_step_pack(client, messages, context, "Hold-on line")
            return _structured(client, AmadeusPack, messages)
        pack = write(recent)
        if recent and _aside_too_similar(pack.assistant_reply_JPS or "", recent):
            print("[Amadeus] Hold-on line repeats an earlier one - one retry.")
            pack = write(recent + [pack.assistant_reply_JPS or ""])
        jps = (pack.assistant_reply_JPS or "").strip()
        eng = (pack.assistant_reply_ENG or "").strip() or jps
        if not jps or pack.assistant_reply_JPS == _honest_plain_failure_pack().assistant_reply_JPS:
            print("[Amadeus] Hold-on line: no usable line - skipped.")
            return
    except Exception as exc:
        print(f"[Amadeus] Hold-on line failed (skipped): {exc!r}")
        return
    with run.lock:
        if run.closed:
            print(f"[Amadeus] Hold-on line ready after the answer ({time.monotonic() - started:.1f}s) - dropped.")
            return
        try:
            run.saved_id = store.append_message("assistant", eng, conversation_id=conv,
                                                japanese=jps, aside=True)
        except Exception as exc:
            print(f"[Amadeus] Hold-on line could not be saved: {exc!r}")
            return
    print(f"[Amadeus] Hold-on line ({time.monotonic() - started:.1f}s): {jps}")
    try:
        emit(run.saved_id, AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG=eng))
    except Exception as exc:
        print(f"[Amadeus] Hold-on line could not be sent to the screen: {exc!r}")


def close_aside():
    """Her answer is ready (or failed): a hold-on line still being written is
    dropped. Returns the id of the one that was saved, else None."""
    run = getattr(_turn, "aside_run", None)
    _turn.aside_run = None
    if run is None:
        return None
    with run.lock:
        run.closed = True
        return run.saved_id


# --- Lookup memory (2026-10-25) ------------------------------------------------
# Every search is saved per chat (memory.save_lookup). A later search for the
# same thing in the same chat uses the saved notes while they are fresh
# (no network, no credits; "Checking my notes from earlier"); she also sees a
# short index of the chat's lookups and can reopen one with reread_lookup -
# old notes on a time-sensitive topic are re-checked by re-reading the saved
# pages, and only when that fails is the web searched again.
LOOKUP_INDEX_SIZE = 5
LOOKUP_REUSE_OVERLAP = 0.6   # share of query terms two searches must have in common
# How long saved notes count as fresh, by how time-sensitive the query is
# (websearch.freshness_for: past day / week / month / not time-sensitive).
LOOKUP_FRESH_SECONDS = {"d": 6 * 3600, "w": 24 * 3600, "m": 7 * 86400, None: 30 * 86400}
_AGAIN_RE = re.compile(
    r"\b(again|re-?check|re-?search|refresh|look (it|that) up again|search again)\b"
    r"|もう一度|もう一回|再検索|改めて|再度", re.IGNORECASE)

REREAD_LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "reread_lookup",
        "description": (
            "Reopen one of your EARLIER LOOKUPS in this chat (your saved notes from a "
            "web search you already made) instead of searching again. Use it when the "
            "user asks a follow-up about something you looked up before. The app "
            "re-checks the pages if the notes are too old."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lookup": {"type": "integer", "description": "The lookup's number (#n)."},
            },
            "required": ["lookup"],
        },
    },
}


def _lookup_conversation():
    return getattr(_turn, "conversation", None) if getattr(_turn, "sources", None) is not None else None


def _lookup_age_seconds(lookup) -> float:
    try:
        made = datetime.strptime(lookup.get("created_at", ""), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return float("inf")
    return max(0.0, (datetime.now() - made).total_seconds())


def _age_text(seconds: float) -> str:
    if seconds == float("inf"):
        return "some time ago"
    minutes = int(seconds // 60)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def _lookup_is_fresh(lookup) -> bool:
    limit = LOOKUP_FRESH_SECONDS.get(websearch.freshness_for(lookup.get("query", "")),
                                     LOOKUP_FRESH_SECONDS[None])
    return _lookup_age_seconds(lookup) <= limit


def _lookup_sites(lookup) -> str:
    pages = [p for p in lookup.get("pages") or [] if p.get("notes")] or lookup.get("pages") or []
    sites = [webreader._site(p.get("url")) for p in pages[:3]]
    return ", ".join(sites)


def _recent_lookups():
    """This chat's newest lookups - without those made for the answer that is
    being regenerated (Regenerate searches fresh: the old answer was often off
    BECAUSE of what that search found, 2026-10-26)."""
    conv = _lookup_conversation()
    if conv is None:
        return []
    skip = getattr(_turn, "skip_lookup_turn", None)
    try:
        lookups = store.recent_lookups(conv, LOOKUP_INDEX_SIZE + (5 if skip is not None else 0))
    except Exception as exc:
        print(f"[Amadeus] Could not read the chat's lookups: {exc!r}")
        return []
    if skip is not None:
        lookups = [lk for lk in lookups if lk.get("turn") != skip]
    return lookups[:LOOKUP_INDEX_SIZE]


def _lookup_index_message():
    """EARLIER LOOKUPS: one short line per recent lookup in this chat, or None."""
    lookups = _recent_lookups()
    if not lookups:
        return None
    lines = []
    for lk in lookups:
        sites = _lookup_sites(lk)
        lines.append(f"#{lk['id']} (turn {lk.get('turn') or 0}, "
                     f"{_age_text(_lookup_age_seconds(lk))}): \"{lk['query']}\""
                     + (f" - {sites}" if sites else ""))
    return {"role": "system", "content": (
        "EARLIER LOOKUPS in this chat (web searches you already made; your notes are "
        "saved). For a follow-up about one of them, call reread_lookup with its number "
        "instead of searching again:\n" + "\n".join(lines))}


def _lookup_text(lookup, why: str) -> str:
    """Saved notes (or the saved excerpt) of a lookup, for her prompt."""
    age = _age_text(_lookup_age_seconds(lookup))
    head = (f"Your saved notes from lookup #{lookup['id']} (turn {lookup.get('turn') or 0}, "
            f"{age}) for '{lookup['query']}' - {why}:")
    pages = [dict(p, rank=p.get("rank") or i + 1) for i, p in enumerate(lookup.get("pages") or [])]
    body = webreader.notes_block(pages)
    if not body and lookup.get("excerpt"):
        body = webreader.wrap_web_data(lookup["excerpt"])
    if not body:
        body = PAGES_DID_NOT_ANSWER
    return (head + "\n" + body + "\n\n" + SEARCH_DATE_NOTE.format(
        today=datetime.now().strftime("%Y-%m-%d")))


def _record_reused(lookup) -> None:
    pages = lookup.get("pages") or []
    used = [p["url"] for p in pages if p.get("notes")] or [p["url"] for p in pages[:1] if p.get("url")]
    _record_source({"kind": "search", "query": lookup["query"],
                    "results": lookup.get("results") or [], "read": used[0] if used else None,
                    "used": used, "engine": lookup.get("engine"), "mode": lookup.get("mode"),
                    "reused": True, "lookup_id": lookup["id"]})


def _matching_lookup(query: str):
    """A fresh lookup in this chat for (nearly) the same search, or None.
    Never when the user asks to check again."""
    if _AGAIN_RE.search(query or "") or _AGAIN_RE.search(getattr(_turn, "question", "") or ""):
        return None
    wanted = webreader.terms(query)
    if not wanted:
        return None
    for lk in _recent_lookups():
        got = webreader.terms(lk.get("query", ""))
        if not got:
            continue
        overlap = len(wanted & got) / len(wanted | got)
        if overlap >= LOOKUP_REUSE_OVERLAP and _lookup_is_fresh(lk):
            return lk
    return None


def _save_lookup(query, source, results, pages, extra) -> None:
    conv = _lookup_conversation()
    if conv is None:
        return
    try:
        saved_pages = [{"url": p["url"], "title": p.get("title", ""), "date": p.get("date", ""),
                        "rank": p.get("rank"), "notes": p.get("notes")} for p in pages or []]
        excerpt = extra if source.get("mode") == "quick" else ""
        source["lookup_id"] = store.save_lookup(
            conv, store.current_turn(conv), query, source.get("engine"), source.get("mode"),
            results, saved_pages, excerpt)
    except Exception as exc:
        print(f"[Amadeus] Could not save the lookup for '{query}': {exc!r}")


def _reread_lookup(lookup_id) -> str:
    """reread_lookup: the saved notes when fresh; old notes on a time-sensitive
    topic -> re-read the saved pages; nothing usable -> a fresh search."""
    try:
        lookup = store.load_lookup(int(lookup_id))
    except (TypeError, ValueError):
        lookup = None
    conv = _lookup_conversation()
    if not lookup or (conv is not None and lookup.get("conversation_id") != conv):
        return (f"(There is no lookup #{lookup_id} in this chat. Use web_search if you "
                "need to look something up.)")
    if getattr(_turn, "skip_lookup_turn", None) is not None and \
            lookup.get("turn") == _turn.skip_lookup_turn:
        # Regenerate: the replaced answer's own lookup is searched afresh.
        return _web_search_with_content(lookup["query"], allow_reuse=False)
    if _lookup_is_fresh(lookup):
        _progress("notes", "Checking my notes from earlier...")
        _record_reused(lookup)
        print(f"[Amadeus] Lookup #{lookup['id']} reused (fresh): {lookup['query']!r}")
        return _lookup_text(lookup, "still fresh")
    # Old notes on a time-sensitive topic: read the saved pages again.
    urls = [p["url"] for p in lookup.get("pages") or [] if p.get("url")]
    results = [r for r in lookup.get("results") or [] if r.get("url")]
    source = {"kind": "search", "query": lookup["query"], "results": results[:5],
              "read": None, "engine": lookup.get("engine"), "rechecked": lookup["id"]}
    _record_source(source)
    if urls or results:
        _maybe_start_aside(lookup["query"])
        _progress("reading", "Re-reading " + ", ".join(webreader._site(u) for u in urls[:3]) + "...")
        ordered = ([r for r in results if r["url"] in urls]
                   + [r for r in results if r["url"] not in urls]) or [{"url": u} for u in urls]
        try:
            extra, pages = _read_results(lookup["query"], ordered, source)
        except Exception as exc:
            print(f"[Amadeus] Re-reading lookup #{lookup['id']} failed: {exc!r}")
            extra, pages = "", []
        if extra and extra != PAGES_DID_NOT_ANSWER:
            _save_lookup(lookup["query"], source, results, pages, extra)
            _progress("thinking", "Amadeus is thinking...")
            return (f"Your lookup #{lookup['id']} for '{lookup['query']}' was "
                    f"{_age_text(_lookup_age_seconds(lookup))}, so the pages were read again "
                    "just now:\n" + extra + "\n\n" + SEARCH_DATE_NOTE.format(
                        today=datetime.now().strftime("%Y-%m-%d")))
    # Nothing usable: search afresh.
    return _web_search_with_content(lookup["query"], allow_reuse=False)


# The user's own recent words: the years they named or meant (year guard).
YEAR_GUARD_USER_MESSAGES = 3


def _user_words_for_years() -> list:
    """The latest user message + the few before it in this reply's chat."""
    texts = [getattr(_turn, "question", "") or ""]
    context = getattr(_turn, "reply_context", None) or []
    users = [m.get("content") for m in context
             if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return texts + users[-YEAR_GUARD_USER_MESSAGES:]


def _guard_query(query: str) -> str:
    """Her search words without the past years she added on her own (see
    websearch.guard_query_years); logged when something was removed."""
    user_words = _user_words_for_years()
    fixed, removed = websearch.guard_query_years(query, user_words)
    if removed:
        print(f"[Amadeus] Search words fixed (years the user never said: "
              f"{', '.join(map(str, removed))}): {query!r} -> {fixed!r}")
    # The same for a season she adds ("anime winter" asked in October).
    seasoned, seasons = websearch.guard_query_seasons(fixed, user_words)
    if seasons:
        print(f"[Amadeus] Search words fixed (season the user never said: "
              f"{', '.join(seasons)}): {fixed!r} -> {seasoned!r}")
    return seasoned


def _web_search_with_content(query: str, allow_reuse: bool = True) -> str:
    """web_search plus reading the result pages (reader or quick mode).

    Returns the search results, then the notes from the pages (reader mode) or
    the best paragraphs of the top page (quick mode) - the numbers, the
    details - instead of one-line snippets. Bounded (time + size) and fully
    defensive: if nothing can be read, the plain results come back unchanged,
    so a fetch or reader problem can never break or hang the reply. A pure
    clock query is not searched at all (CLOCK_SEARCH_SKIPPED). Each search is
    logged, recorded as a source and saved as a lookup; a fresh lookup for the
    same search in this chat is used instead of searching again.
    """
    query = _guard_query(query)
    if _is_clock_query(query):
        print(f"[Amadeus] Web search skipped (clock query - her RIGHT NOW note has it): {query!r}")
        return CLOCK_SEARCH_SKIPPED
    reuse = _matching_lookup(query) if allow_reuse else None
    if reuse:
        _progress("notes", "Checking my notes from earlier...")
        _record_reused(reuse)
        print(f"[Amadeus] Web search {query!r}: using lookup #{reuse['id']} "
              f"({reuse['query']!r}, {_age_text(_lookup_age_seconds(reuse))}) instead")
        _progress("thinking", "Amadeus is thinking...")
        return _lookup_text(reuse, "the same search, still fresh")
    _note_search_engine(None)
    _maybe_start_aside(query)
    _progress("searching", f'Searching the web: "{query}"')
    text = _run_web_search(query)
    results = _parse_search_results(text)
    source = {"kind": "search", "query": query, "results": results[:5], "read": None,
              "engine": getattr(_turn, "engine", None)}
    _record_source(source)
    if results:
        _progress("found", f"Found {len(results)} page{'s' if len(results) != 1 else ''}")
    extra, pages = "", []
    try:
        if results:
            extra, pages = _read_results(query, results, source)
            if extra:
                return text + "\n\n---\n\n" + extra
    except Exception as exc:
        print(f"[Amadeus] Reading the pages for '{query}' failed: {exc!r}")
    finally:
        if results:
            _save_lookup(query, source, results, pages, extra)
        print(f"[Amadeus] Web search ({source['engine'] or 'none'}, "
              f"{source.get('mode', '-')}): {query!r} -> {len(results)} result(s)"
              + (f"; read {source['read']}" if source["read"] else "")
              + (f"; notes from {len(source.get('used') or [])}" if source.get("mode") == "reader" else ""))
        _progress("thinking", "Amadeus is thinking...")
    return text


def _pack_from_args(args) -> "AmadeusPack":
    """Build an AmadeusPack from tool-call args; reject empty garbage."""
    jps = str((args or {}).get("assistant_reply_JPS", "")).strip()
    eng = str((args or {}).get("assistant_reply_ENG", "")).strip()
    if not jps and not eng:
        raise ValueError("AmadeusPack args were empty")
    return AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG=eng)


def _strip_tool_call_scaffolding(text: str) -> str:
    """Remove tool-call 'scaffolding' a model may leak into plain text so it never
    reaches TTS or the UI: Ling-style '< tool_call>...< /tool_call>' blocks (and
    <tool>/<function>/<invoke> variants), stray unclosed tool tags, and lines that
    are raw JSON describing a tool call. Complements _strip_pack_scaffolding, which
    handles the AmadeusPack field labels. Returns the text with only clean prose."""
    t = text or ""
    tag = r"(?:tool_call|toolcall|tool|function|invoke|use_tool)"
    # Whole tool-call blocks (an open tag up to its matching close tag).
    t = re.sub(r"(?is)<\s*/?\s*" + tag + r"\b.*?<\s*/?\s*" + tag + r"\s*>", " ", t)
    # Stray / unclosed tool tags left behind.
    t = re.sub(r"(?is)<\s*/?\s*" + tag + r"\b[^<>]*>", " ", t)
    # Lines that are a raw JSON object describing a tool call.
    keep = []
    for line in t.splitlines():
        s = line.strip()
        if s.startswith("{") and s.endswith("}") and (
            '"arguments"' in s or '"parameters"' in s
            or ('"name"' in s and '"query"' in s)
        ):
            continue
        keep.append(line)
    return "\n".join(keep)


def _strip_thinking(text: str) -> str:
    """Remove thinking/channel tokens a model may leak into its answer: Qwen
    <thinking>...</thinking>, Gemma-4 <thought>...</thought> and bare
    <think></think>, plus Gemma-style
    <channel|>thought / <channel|> markers, which a degenerating local model
    can repeat into a run-on loop ("<|channel>thought<channel|>" xN)."""
    t = text or ""
    t = re.sub(r"(?is)<thinking>.*?</thinking>", "", t)
    t = re.sub(r"(?is)<thought>.*?</thought>", "", t)        # Gemma 4: <thought>...</thought>
    # An UNCLOSED thinking block means the model was still thinking (often
    # degenerating into an endless draft loop) and no real answer followed -
    # anything after the opener is reasoning, so cut it. This must run BEFORE
    # the bare-tag strip below, which would otherwise remove the opener and
    # orphan the reasoning text.
    t = re.sub(r"(?s)\s*<thinking>.*", "", t)
    t = re.sub(r"(?s)\s*<thought>.*", "", t)
    t = re.sub(r"(?is)</?think(ing)?>", "", t)
    t = re.sub(r"(?is)<\|channel>.*?</channel\|>", "", t)   # full channel block
    t = re.sub(r"(?s)\s*<\|channel>\s*thought\b", "", t)     # unclosed open token
    t = re.sub(r"(?s)<channel\|>\s*", "", t)                  # close token
    t = re.sub(r"(?m)^\s*thought\s*$", "", t)                 # bare 'thought' line
    t = _strip_tool_call_scaffolding(t)
    return t.strip()


def _parse_textual_tool_calls(text: str):
    """Recognize a tool call a model wrote as PLAIN TEXT (in its reply body)
    instead of in the native tool_calls array, and return it in the same shape
    the web-search loop expects: [{"name": ..., "args": {...}, "id": ...}, ...].
    Returns [] when no textual tool call is present (ordinary prose, or a native
    tool call, which this function never sees).

    Why this exists: some cloud models (e.g. InclusionAI's Ling on OpenRouter)
    DO emit a search request, but as visible text like
        < tool_call>web_search
        <arg_key>query</arg_key>
        <arg_value>some query</arg_value>
        < /tool_call>
    rather than a structured tool_calls entry. Without parsing this, the local
    DuckDuckGo search never fires and the raw tags leak into her reply.

    Handles the Ling / InclusionAI layout, tolerating the `argument_` spelling,
    missing/extra whitespace, and either < ...> or < /...> closing tags.
    """
    if not text:
        return []
    calls = []
    block_re = re.compile(r"<\s*/?\s*tool_call\s*>\s*(.*?)\s*<\s*/?\s*tool_call\s*>",
                          re.DOTALL | re.IGNORECASE)
    key_re = re.compile(r"<\s*arg(?:ument)?_key\s*>\s*(.*?)\s*<\s*/\s*arg(?:ument)?_key\s*>",
                        re.DOTALL | re.IGNORECASE)
    val_re = re.compile(r"<\s*arg(?:ument)?_value\s*>\s*(.*?)\s*<\s*/\s*arg(?:ument)?_value\s*>",
                        re.DOTALL | re.IGNORECASE)
    for bi, body in enumerate(block_re.findall(text)):
        body = body.strip()
        if not body:
            continue
        # Tool name = first token, cut at the first tag.
        name = re.split(r"<", body, maxsplit=1)[0].strip().split()[0] if body else ""
        keys = [k.strip() for k in key_re.findall(body)]
        vals = [v.strip() for v in val_re.findall(body)]
        args = dict(zip(keys, vals))
        if not args and len(vals) == 1:
            args = {"query": vals[0]}
        calls.append({"name": name, "args": args, "id": f"txt_{bi}"})
    if not calls:
        # Fallback: the model may dump an OpenAI-style tool call as JSON/text
        # (e.g. {"name": "web_search", "arguments": {"query": "..."}}) instead of
        # the tag format above. Only fire when "web_search" is named AND a quoted
        # query is present, so ordinary prose never triggers a search.
        if re.search(r"web_search", text, re.IGNORECASE):
            jm = re.search(r'(?:"|\b)query(?:"|\b)\s*[:=]\s*"([^"]+)"', text, re.IGNORECASE)
            if jm:
                q = jm.group(1).strip()
                if q:
                    calls.append({"name": "web_search", "args": {"query": q}, "id": "txt_json"})
    return calls


def _has_japanese(text: str) -> bool:
    """True if the text contains hiragana/katakana (real spoken Japanese)."""
    return any("\u3040" <= ch <= "\u30ff" for ch in (text or ""))


def _has_latin(text: str) -> bool:
    """True if the text contains any A-Z / a-z letter."""
    return any(("a" <= ch <= "z") or ("A" <= ch <= "Z") for ch in (text or ""))


def _has_japanese_script(text: str) -> bool:
    """True if the text has any kana OR CJK ideograph (broader than _has_japanese,
    which is kana-only). Used to tell a leaked whole-English line apart from a
    Japanese line that merely contains a Latin token (a product name, "AI", a number)."""
    for ch in (text or ""):
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            return True
    return False


def _collapse_runaway(text: str) -> str:
    """Truncate a runaway repeated phrase (a local-model degeneration such as a
    short line x200 or "mount of effort." x500). Fires only when a short phrase
    repeats 8+ times in a row at the tail, so normal short stammers and deliberate
    repetition are left alone. Returns the text unchanged when there is no loop."""
    t = text
    n = len(t)
    if n < 40:
        return t
    best_cut = None
    for unit_len in range(2, 24):
        tail_unit = t[n - unit_len:]
        if len(tail_unit.strip()) < 2 or not any(ch.isalnum() for ch in tail_unit):
            continue
        i = n
        count = 0
        while i >= unit_len and t[i - unit_len:i] == tail_unit:
            i -= unit_len
            count += 1
        if count >= 8 and i < n - 8:
            if best_cut is None or i < best_cut:
                best_cut = i
    if best_cut is not None:
        return t[:best_cut].rstrip()
    return t


def _strip_pack_scaffolding(text: str) -> str:
    """Remove the AmadeusPack 'scaffolding' a model may emit as PLAIN TEXT instead
    of a tool call - markdown code fences, bare JSON structure lines, and the
    field labels (assistant_reply_JPS / assistant_reply_ENG) - so the TTS never
    reads 'assistant_reply_JPS:', 'json', '{' or a JSON block. Stripping a label
    keeps the value that follows it; a line that is only JSON punctuation is dropped.
    """
    t = text or ""
    # If the model wrote the field labels inline without line breaks, split before
    # each label so the line-based cleaning below can isolate each field's value.
    t = re.sub(r"(?=assistant_reply_(?:JPS|ENG)\s*[:：])", "\n", t)
    t = re.sub(r"(?s)```[a-zA-Z]*.*?```", "", t)   # fenced block (```json ... ```) 
    t = re.sub(r"```", "", t)                          # stray fence
    kept = []
    for line in t.split("\n"):
        s = line.strip()
        if s and re.fullmatch(r"[{}\[\],:\"\s]+", s):
            continue                                   # line of pure JSON punctuation
        s = re.sub(r'^["\']?\s*assistant_reply_(JPS|ENG)\s*["\']?\s*[:：]\s*', "", s)
        if not s:
            continue                                   # was just a label / structure
        kept.append(s)
    return "\n".join(kept)


# A maximal contiguous Latin run: starts and ends with a letter/digit, only
# letters, digits and ordinary word punctuation / single spaces in between.
_INLINE_RUN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 .,!?'&;:%/-]*[A-Za-z0-9])?")


def _strip_inline_english(text: str) -> str:
    """Strip inline English phrases glued ONTO Japanese lines.

    The line-level cleaning above drops whole-English LINES, but a
    degenerating model can collapse the bilingual reply into ONE line -
    Japanese first, then the English paragraph inline, no line break - so the
    line "contains Japanese" and nothing gets dropped, and TTS reads the
    English out loud (observed 2026-09-16: a ~600-word English tail on a
    single 941-char line). Each contiguous Latin run that forms an actual
    phrase (3+ words, or 24+ Latin letters) is therefore removed; short
    legitimate tokens (her name, "AI", "AQI", "PM2.5", numbers) survive.
    Runs never cross line breaks, so normal multi-line text is unaffected.
    """
    def _kill(match):
        run = match.group(0)
        # A product name or a title (iPhone 18 Pro Max, 『My First Cookie
        # Book』) stays: removing it left holes in her line (2026-10-24).
        if speech_text.keeps_latin_run(text, match.start(), run):
            return run
        return " "
    t = _INLINE_RUN_RE.sub(_kill, text)
    if t != text:
        print("[Amadeus] JPS had inline English - stripped before TTS")
    t = re.sub(r" +", " ", t)
    # drop punctuation left orphaned where a phrase was removed
    t = re.sub(r"(?<= )[!?,.;:]{1,3}(?= )", "", t)
    t = re.sub(r"[!?,.;:]{1,3} +$", "", t)
    t = re.sub(r"(?<=[、。！？])\s*[!?,.;:]{1,3}", "", t)
    # glue any leftover spaces back onto CJK punctuation
    t = re.sub(r"\s+([、。，！？；：])", r"\1", t)
    t = re.sub(r"([、。，！？；：])\s+", r"\1", t)
    return t


# Scripts her Japanese line never needs: Cyrillic, Hebrew, Arabic, the Indic
# scripts (Devanagari, Bengali, Tamil, ...), Thai/Lao, Hangul.
_OTHER_SCRIPT_RE = re.compile(
    "[Ѐ-ӿ֐-ࣿऀ-෿฀-໿"
    "ᄀ-ᇿ㄰-㆏가-힯]+")


def _clean_tts_text(text: str) -> str:
    """Sanitize the Japanese (TTS) field before it reaches GPT-SoVITS.

    A weaker model (Gemma) can pollute assistant_reply_JPS in several ways: it may
    dump the English translation into it, or write the whole AmadeusPack as plain
    text (the field labels, a JSON block, markdown fences) instead of a tool call,
    or degenerate into a run-on repetition. Because the TTS speaks exactly what is
    in JPS, all of that would otherwise be read out loud. We therefore:
      - strip thinking/channel tokens (see _strip_thinking);
      - for a field with NO Japanese at all (pure English), keep it intact so the
        caller's translation pass can rebuild it as Japanese (only collapsing a
        runaway loop);
      - for a field that DOES have Japanese: strip the pack scaffolding, drop any
        line that has Latin letters but no Japanese (a whole-English gloss line -
        not a Japanese line that merely names a product), drop pure-English
        parentheticals, strip inline English phrases glued onto a Japanese
        line (no line break to key on), and truncate runaway repetition.
    Japanese lines that merely contain a few Latin tokens (a product name, "AI", a
    number) are left untouched, so a legitimate mixed line is never broken.
    """
    t = _strip_thinking(text or "").strip()
    if not t:
        return t
    # A stray word in another script (her 12B model, rarely: Korean 들을,
    # Bengali বাইরে - 2026-10-22/24) would be read out as noise: drop it.
    if _OTHER_SCRIPT_RE.search(t):
        t = _OTHER_SCRIPT_RE.sub("", t).strip()
        t = re.sub(r"(?<=[、。！？])[ \t]+|[ \t]+(?=[、。！？])", "", t)
        print("[Amadeus] JPS had a stray word in another script - removed")
    # Pure-English field (no Japanese at all): keep it for the translation pass.
    if _has_latin(t) and not _has_japanese_script(t):
        return _collapse_runaway(t)
    # Normal case: the field carries Japanese. Strip leaked English + scaffolding.
    t = _strip_pack_scaffolding(t)
    if _has_latin(t):
        kept = []
        for line in t.split("\n"):
            s = line.strip()
            if s and _has_latin(s) and not _has_japanese_script(s):
                continue
            kept.append(s)
        t = "\n".join(kept)
        t = re.sub(r"[（(][^（）()]*[）)]",
                   lambda m: m.group(0) if _has_japanese_script(m.group(0)) else "", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        t = _strip_inline_english(t)
        # The shared name list: a listed name still in Latin letters becomes
        # its katakana, so the screen, the voice and her history agree.
        t = speech_text.swap_listed_words(t)
    return _collapse_runaway(t).strip()


def _ensure_japanese(pack: "AmadeusPack", llm) -> "AmadeusPack":
    """Guarantee the TTS field is clean, spoken Japanese.

    Two local-model failure modes are cleaned here so GPT-SoVITS never reads
    garbage out loud:
      - English leak: a weaker model (Gemma) may dump the English translation into
        assistant_reply_JPS (it reproduces the bilingual JP/EN layout from the
        personality quotes), so the voice would speak Japanese then English.
        _clean_tts_text strips the leaked English.
      - Runaway repetition: a degenerating local model may loop a short phrase
        hundreds of times; _clean_tts_text truncates it.
    If, after cleaning, the field has no Japanese at all (it was pure English),
    do one quick translation pass so her voice still stays in Japanese.
    """
    cleaned = _clean_tts_text(pack.assistant_reply_JPS)
    if cleaned != (pack.assistant_reply_JPS or ""):
        pack = AmadeusPack(assistant_reply_JPS=cleaned, assistant_reply_ENG=pack.assistant_reply_ENG)
    if _has_japanese_script(pack.assistant_reply_JPS):
        return pack
    if not _usable_source(pack.assistant_reply_ENG):
        return pack
    try:
        trans = llm.invoke([{
            "role": "user",
            "content": (
                "Translate the following into natural spoken Japanese, in the voice of Amadeus "
                "(a sharp, confident scientist who speaks casually to close friends). "
                "Output ONLY the Japanese text - no quotes, no translation, no commentary.\n\n"
                + (pack.assistant_reply_ENG or "")[:1500]
            ),
        }])
        jps = _clean_tts_text(_strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)))
        if _has_japanese_script(jps):
            print("[Amadeus] JPS was not Japanese - repaired with a translation pass")
            return AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG=pack.assistant_reply_ENG)
    except Exception as e:
        print("[Amadeus] Japanese repair failed:", repr(e))
    return pack


def _ensure_english(pack: "AmadeusPack", llm) -> "AmadeusPack":
    """Guarantee the UI (English) field is actually English.

    A weaker model can put Japanese (or a mixed JP/EN blob) into assistant_reply_ENG,
    or leave it empty - in which case the chat box shows Japanese instead of the
    English translation. If the ENG field has no Latin script, run one quick
    JPS -> English translation pass. If that fails, fall back to showing the
    Japanese line rather than an empty box (her real words, just untranslated).
    """
    eng = (pack.assistant_reply_ENG or "").strip()
    # Fast path: already English (has Latin letters and no Japanese script).
    if _has_latin(eng) and not _has_japanese_script(eng):
        return pack
    if not _usable_source(pack.assistant_reply_JPS or eng):
        return AmadeusPack(assistant_reply_JPS=pack.assistant_reply_JPS,
                           assistant_reply_ENG=eng or (pack.assistant_reply_JPS or ""))
    try:
        trans = llm.invoke([{
            "role": "user",
            "content": (
                "Translate the following Japanese into natural spoken English. "
                "Output ONLY the English text - no quotes, no commentary.\n"
                + "".join(h + "\n" for h in _user_hint_lines(pack.assistant_reply_JPS or eng)) + "\n"
                + (pack.assistant_reply_JPS or eng)[:1500]
            ),
        }])
        new_eng = _strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)).strip()
        if _has_latin(new_eng) and not _META_TRANSLATION.search(new_eng):
            print("[Amadeus] ENG was not English - repaired with a translation pass")
            return AmadeusPack(assistant_reply_JPS=pack.assistant_reply_JPS, assistant_reply_ENG=new_eng)
    except Exception as e:
        print("[Amadeus] English repair failed:", repr(e))
    # Fallback: show the Japanese line rather than an empty box.
    return AmadeusPack(assistant_reply_JPS=pack.assistant_reply_JPS, assistant_reply_ENG=eng or (pack.assistant_reply_JPS or ""))


# --- Web-off honesty net -------------------------------------------------------
# When web access is OFF, the reply is supposed to rely on NO_WEB_BLOCK (the
# prompt instruction "be honest, you can't search"). A weak model sometimes
# ignores a buried instruction and claims it searched anyway. This net checks
# the FINAL text for a false search claim and, only then, swaps in an honest
# line. Zero cost on honest replies (plain substring scan, no model call).
_WEB_OFF_CLAIM_EN = [
    "searched the web", "searched online", "i searched", "i looked it up",
    "looked it up online", "looked it up on the web", "checked online",
    "i checked the web", "found it online", "i found it online",
    "ran a web search", "ran an online search", "did a web search",
    "from my search", "according to my search", "the search results",
]
_WEB_OFF_NEGATION_EN = [
    "can't search", "cannot search", "couldn't search", "can't look it up",
    "cannot look it up", "didn't search", "did not search", "haven't searched",
    "without searching", "can't access the web", "no web access",
]
_WEB_OFF_CLAIM_JA = [
    ("ウェブ", "検索した"), ("ウェブ", "調べた"),
    ("ネット", "検索した"), ("ネット", "調べた"),
    ("インターネット", "調べた"), ("オンライン", "調べた"), ("オンライン", "検索"),
]
_WEB_OFF_NEGATION_JA = ["できませんでした", "れなかった", "しなかった", "なかった"]


def _web_off_honesty_net(pack: "AmadeusPack") -> "AmadeusPack":
    """Web-OFF only: if the reply claims a web search actually happened (impossible
    while the tool is off), replace it with an honest line. Fires only on an
    explicit false claim, in either language field."""
    eng = (pack.assistant_reply_ENG or "").lower()
    jps = pack.assistant_reply_JPS or ""

    eng_false = any(c in eng for c in _WEB_OFF_CLAIM_EN) and not any(n in eng for n in _WEB_OFF_NEGATION_EN)
    jps_false = any(ctx in jps and v in jps for ctx, v in _WEB_OFF_CLAIM_JA) \
        and not any(n in jps for n in _WEB_OFF_NEGATION_JA)

    if not (eng_false or jps_false):
        return pack

    print("[Amadeus] Web is OFF but the reply claimed a search happened - swapped in the honest line.")
    return AmadeusPack(
        assistant_reply_JPS=(
            "今はウェブがオフだから、検索はできなかったの。"
            "オンラインで確認したとは言えないわね。"
        ),
        assistant_reply_ENG=(
            "Web access is off right now, so I didn't search - I can't claim I "
            "verified that online."
        ),
    )


def _finalize(pack: "AmadeusPack", llm) -> "AmadeusPack":
    """Apply every output guard in order on every reply path: keep the TTS field
    clean Japanese, then make sure the UI field is actually English."""
    return _ensure_english(_ensure_japanese(pack, llm), llm)


# --- Check-promise detection (non-agentic honesty net) -----------------------
# "Wait, let me check." is the sentence a tool-using model says right BEFORE
# the check it then performs. This app performs no checks after sending a
# reply (one user message in, one reply out), so a reply that stalls on such
# a promise is a half-action the user is left to chase with "continue"
# (live 2026-10-01: "do you remember X?" -> "Wait, let me check." and no
# follow-up ever came). Tail-anchored and length-capped so complete answers
# that merely mention "hold on" mid-sentence never trigger.
_CHECK_PROMISE_PATTERNS_EN = (
    "let me check", "let me look", "let me see", "let me think",
    "i'll check", "i will check", "i'll look", "i'm checking",
    "give me a second", "give me a moment", "give me a sec",
    "give me a minute", "hold on", "one moment", "one second",
    "just a moment", "just a second",
)
_CHECK_PROMISE_PATTERNS_JA = (
    "確認してみる", "調べてみる", "ちょっと待って", "少し待って",
)


def _is_check_promise(text: str) -> bool:
    """True when a reply is only a mid-action check promise (see above).

    "I checked. It was about 24 hours." is NOT a promise (the check is
    claimed done); "Hold on, actually I remember..." is NOT (the answer
    follows); a question is NOT (it is complete)."""
    t = _strip_thinking(text or "").strip()
    if not t or len(t) > 100 or "?" in t or "？" in t:
        return False
    tail = t.lower()[-40:]
    if any(p in tail for p in _CHECK_PROMISE_PATTERNS_EN):
        return True
    return any(p in t[-40:] for p in _CHECK_PROMISE_PATTERNS_JA)


def _salvage_plain_text(raw, llm) -> "AmadeusPack":

    """Last resort: turn whatever plain text the model actually produced into an
    AmadeusPack, so a flaky model/server still yields *an* answer instead of a hard
    error. Called only when the structured AmadeusPack tool call never came through
    but she did say something usable in prose.

    - If the text already contains Japanese, keep it as the spoken (TTS) line and add
      an English translation for the UI.
    - Otherwise treat it as the English display line and translate it to Japanese so
      her voice stays in Japanese.
    - If a translation pass fails or returns nothing, fall back to showing/voicing the
      original text rather than returning empty fields.

    Raises ValueError only when there is literally no text at all (the honest
    "nothing came back" case) - never fabricates a reply out of thin air.
    """
    text = _strip_thinking(raw or "").strip()
    if not text:
        raise ValueError("The model returned no usable reply (no AmadeusPack and no text).")

    if _has_japanese(text):
        # She already spoke Japanese - use it as the TTS line, add English for the UI.
        jps = text
        eng = ""
        try:
            trans = llm.invoke([{
                "role": "user",
                "content": (
                    "Translate the following Japanese into natural spoken English. "
                    "Output ONLY the English text - no quotes, no commentary.\n"
                    + "".join(h + "\n" for h in _user_hint_lines(text)) + "\n"
                    + text[:1500]
                ),
            }])
            eng = _strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)).strip()
        except Exception as e:
            print("[Amadeus] Salvage: English translation failed:", repr(e))
        return AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG=eng or jps)

    # No Japanese - use the text for the UI and translate it to Japanese for the voice.
    eng = text
    jps = ""
    try:
        trans = llm.invoke([{
            "role": "user",
            "content": (
                "Translate the following into natural spoken Japanese, in the voice of Amadeus "
                "(a sharp, confident scientist who speaks casually to close friends). "
                "Output ONLY the Japanese text - no quotes, no translation, no commentary.\n\n"
                + text[:1500]
            ),
        }])
        jps = _strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)).strip()
    except Exception as e:
        print("[Amadeus] Salvage: Japanese translation failed:", repr(e))
    return AmadeusPack(assistant_reply_JPS=jps or eng, assistant_reply_ENG=eng)


def _ensure_japanese_jps(jps: str, llm) -> str:
    """String twin of _ensure_japanese for the voice-first JA-only call.

    Cleans the TTS text the same way; if the result has no Japanese at all
    (call 1 can have pure English, and unlike the pack-based guard there is no
    English field to feed the translation pass), the raw text itself is the
    translation source: it becomes her line, in Japanese.
    """
    cleaned = _clean_tts_text(jps or "")
    if _has_japanese_script(cleaned):
        return cleaned
    if not _usable_source(cleaned):
        return cleaned
    repaired = _translate_to_japanese(llm, cleaned)
    if repaired:
        print("[Amadeus] Voice-first: JPS was not Japanese - repaired with a translation pass")
        return repaired
    return cleaned


def _translate_to_japanese(llm, text: str) -> str:
    """Her line written in English (or mostly English) -> the same line in her
    spoken Japanese, cleaned; "" when the pass fails or brings no Japanese."""
    try:
        trans = llm.invoke([{
            "role": "user",
            "content": (
                "Translate the following into natural spoken Japanese, in the voice of Amadeus "
                "(a sharp, confident scientist who speaks casually to close friends). "
                "Output ONLY the Japanese text - no quotes, no translation, no commentary.\n\n"
                + (text or "")[:1500]
            ),
        }])
        jps = _clean_tts_text(_strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)))
        return jps if _has_japanese_script(jps) else ""
    except Exception as e:
        print("[Amadeus] Voice-first: Japanese repair failed:", repr(e))
        return ""


def _mostly_english(text: str) -> bool:
    """A line that is English prose with at most a few Japanese characters
    ("アイノちゃん. I understand, from now on ..."): cleaning would leave only
    scraps of it, so it is translated instead."""
    ja = len(re.findall(r"[぀-ヿ㐀-鿿]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return latin >= 20 and ja < latin * 0.25


def _salvage_jps_plain_text(raw: str, llm) -> str:
    """JA-only twin of _salvage_plain_text: whatever plain text the model
    actually produced becomes the spoken Japanese line (cleaned; when it came
    out in English it is translated to Japanese so her voice stays in Japanese -
    this step has no English field to keep it in).

    Raises ValueError only when there is literally no text at all - the same
    honest "nothing came back" case the pack-based salvage treats as an error.
    """
    text = _strip_thinking(raw or "").strip()
    if not text:
        raise ValueError("The model returned no usable reply (no AmadeusReply and no text).")
    if _has_japanese(text):
        # She already spoke Japanese - use it as the TTS line.
        return _ensure_japanese_jps(text, llm)
    try:
        trans = llm.invoke([{
            "role": "user",
            "content": (
                "Translate the following into natural spoken Japanese, in the voice of Amadeus "
                "(a sharp, confident scientist who speaks casually to close friends). "
                "Output ONLY the Japanese text - no quotes, no translation, no commentary.\n\n"
                + text[:1500]
            ),
        }])
        jps = _clean_tts_text(_strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)))
        if _has_japanese_script(jps):
            return jps
    except Exception as e:
        print("[Amadeus] Voice-first salvage: Japanese translation failed:", repr(e))
    # Translation failed or returned nothing: voice the original text rather
    # than going silent (same fallback the pack-based salvage uses).
    return text


# --- Rate-limit patience -------------------------------------------------------
# Free cloud tiers (Gemini free, OpenRouter free, ...) throttle bursts with
# HTTP 429. The web loop makes several sequential calls per message, so a burst
# of testing can 429 mid-turn. Instead of falling through to a degraded answer,
# wait out the throttle window ONCE and retry. This code is DORMANT on local
# servers and high-limit tiers - they don't return 429, so normal replies are
# untouched. _RATE_LIMIT_STATE caps the waiting: once we have waited for a 429
# within the last _RATE_LIMIT_REWAIT_AFTER seconds, further 429s raise at once
# (the window has not reset, or the daily quota is gone - waiting again would
# only pile on delay; the honesty net in the web loop then takes over).
_RATE_LIMIT_STATE = {"last_wait": 0.0}
_RATE_LIMIT_REWAIT_AFTER = 70.0
_RATE_LIMIT_DEFAULT_WAIT = 45.0
_RATE_LIMIT_MIN_WAIT = 5.0
_RATE_LIMIT_MAX_WAIT = 60.0


def _is_rate_limited(exc) -> bool:
    """True when the API refused the call because of rate/quota limits (429)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        code = getattr(exc, "code", None)
        status = code if isinstance(code, int) else None
    if status == 429:
        return True
    text = str(exc).lower()
    return ("429" in text or "rate limit" in text or "too many requests" in text
            or "resource_exhausted" in text or "resource exhausted" in text
            or "quota" in text)


def _rate_limit_wait_seconds(exc) -> float:
    """How long to wait before retrying a 429. Honours the provider's
    Retry-After hint when present; otherwise a bounded default."""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers is not None:
            hint = headers.get("retry-after")
            if hint:
                return max(_RATE_LIMIT_MIN_WAIT, min(float(hint), _RATE_LIMIT_MAX_WAIT))
    except (TypeError, ValueError, AttributeError):
        pass
    return _RATE_LIMIT_DEFAULT_WAIT


def _is_daily_quota_exhausted(exc) -> bool:
    """True when the 429 is a DAILY quota exhaustion (e.g. the Gemini free
    tier's "PerDayPerProjectPerModel" quota, or explicit "daily quota/limit"
    wording). Such a quota only resets with the daily reset, so waiting is
    provably futile - raise at once and let the honesty net serve the honest
    line. Short throttles (RPM/TPM bursts) are NOT daily quotas and keep the
    full wait-and-retry."""
    text = str(exc).lower()
    return ("perday" in text or "per_day" in text
            or "daily quota" in text or "daily limit" in text)

def _forced_tool_choice_rejected(exc) -> bool:
    """True when a server refused a forced tool choice (tool_choice="required").

    NInfer (and possibly other servers) accept tool *definitions* but refuse to
    guarantee a call, answering HTTP 400 with a message like:
        tool_choice='required' requires at least one tool call, which NInfer cannot guarantee
    The exception type name varies by openai/langchain-openai version
    (BadRequestError, OpenAIInvalidRequestError, ...), so detect on status + text
    instead. Only a genuine 400 about tool forcing matches; a real 400 about e.g.
    a malformed schema still raises normally so the user can fix the config.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        code = getattr(exc, "code", None)
        status = code if isinstance(code, int) else None
    if status != 400:
        return False
    text = str(exc).lower()
    # Match the FORCED-tool-choice refusal specifically. Do NOT key on
    # "invalid_request_error" - that is the generic OpenAI 400 type present in
    # EVERY 400 (incl. unrelated llama.cpp errors like context overflow or a
    # malformed model tool-call), and matching it would silently auto-retry real
    # errors instead of surfacing them.
    return ("tool_choice" in text or "tool choice" in text
            or "cannot guarantee" in text)


def _reasoning_level(kind):
    """Settings -> Reasoning level for "replies" / "translations"; None when
    the llm module is a test stand-in without the helper (old behaviour)."""
    fn = getattr(_llm_module, "reasoning_for", None)
    try:
        return fn(kind) if fn else None
    except Exception:
        return None


def _reply_llm():
    """Her replies' client (replies, greetings, the web-search decision)."""
    level = _reasoning_level("replies")
    return get_llm(API_KEY, LLM_Model, reasoning=level) if level else get_llm(API_KEY, LLM_Model)


def _translation_llm():
    """The English line / Japanese rewrites: the Translations level."""
    level = _reasoning_level("translations")
    return get_llm(API_KEY, LLM_Model, reasoning=level) if level else get_llm(API_KEY, LLM_Model)


def _is_local(client) -> bool:
    """True for her built-in model / the user's own local server (looked up at
    call time - several tests stand in a fake `llm` module)."""
    fn = getattr(_llm_module, "is_local_server", None)
    try:
        return bool(fn and fn(client))
    except Exception:
        return False


_PLAIN_LABEL = re.compile(r"^\s*(?:JA|JP|Japanese|日本語|Amadeus|アマデウス)\s*[:：]\s*", re.I)


def _plain_jps(client, messages) -> str:
    """One plain-text call for her Japanese line. Returns the cleaned line, or
    raises ValueError(reason) when it is unusable (empty, cut off at the output
    cap, no Japanese, markup/tool-call text, a long loop)."""
    reply = client.invoke(messages)
    raw = reply.content if isinstance(reply.content, str) else str(reply.content or "")
    text = _PLAIN_LABEL.sub("", _strip_thinking(raw).strip()).strip()
    if len(text) >= 2 and text[0] in "「\"'" and text[-1] in "」\"'":
        text = text[1:-1].strip()
    meta = getattr(reply, "response_metadata", None) or {}
    why = _degenerate_field(text)
    # Asked to "answer in English only", a small model may agree and write
    # English (2026-10-24, twice in a row -> the honest line). Her words are
    # kept: translated into her Japanese line (the English line shows anyway).
    if (text and not why and meta.get("finish_reason") != "length"
            and (not _has_japanese_script(text) or _mostly_english(text))):
        repaired = _translate_to_japanese(_translation_llm(), text)
        if repaired:
            print("[Amadeus] Plain reply was English - translated into her Japanese line")
            return repaired
    if not text:
        why = "empty"
    elif meta.get("finish_reason") == "length":
        why = why or "cut off at the output limit"
    elif not _has_japanese_script(text):
        why = why or "no Japanese"
    elif _repeats_last_line(text, messages):
        why = why or "repeated her previous line"
    if why:
        raise ValueError(f"plain reply unusable ({why}): {text[:80]!r}")
    return text


def _repeats_last_line(text: str, messages) -> bool:
    """Her new line is (almost) word for word her previous one - a small
    model did this once in 186 test replies (2026-10-24); worth one retry."""
    prev = next((m.get("content", "") for m in reversed(messages or [])
                 if m.get("role") == "assistant"), "")
    return (len(text) >= 20 and bool(prev)
            and difflib.SequenceMatcher(None, prev.strip(), text.strip()).ratio() > 0.9)


def _plain_reply(client, messages, label):
    """_plain_jps with ONE retry. None = both attempts unusable (the caller
    serves the honest line); a connection error that repeats is raised."""
    for attempt in (1, 2):
        try:
            return _plain_jps(client, messages)
        except ValueError as exc:
            print(f"[Amadeus] {label}: plain reply attempt {attempt} unusable - {exc}")
        except Exception as exc:
            print(f"[Amadeus] {label}: plain reply attempt {attempt} failed: {exc!r}")
            if attempt == 2:
                raise
    return None


def _two_step_pack(client, messages, message_context, label) -> "AmadeusPack":
    """Local models, a full reply with both lines (regenerate, greetings): her
    Japanese line as plain text, then the English line by the translation call
    (Settings -> Reasoning -> Translations). The English falls back to her
    Japanese line (the guards then try their own translation pass)."""
    jps = _plain_reply(client, messages, label)
    if jps is None:
        return _honest_plain_failure_pack()
    en = ""
    try:
        en = _translate_reply_en(_translation_llm(), jps,
                                 _voice_first_context_lines(message_context))
    except Exception as exc:
        print(f"[Amadeus] {label}: English line failed: {exc!r}")
    return AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG=en or jps)


def _structured(client, schema, messages):
    """Her reply in the structured format `schema` (AmadeusPack / AmadeusReply).
    Local servers (her built-in model, the user's own llama-server) get
    tool_choice "required": llama-server rejects the named-function form that
    with_structured_output sends ("Wrong type supplied for parameter
    'tool_choice'") and falls back to "auto", so the first answer came back
    empty and EVERY reply was written twice (Run 1, 2026-10-19). Cloud
    services keep with_structured_output unchanged. Returns a `schema`
    instance, or None when no usable call came back (the callers' existing
    "came back empty" ladder handles it)."""
    # Looked up at call time: several tests stand in a small fake `llm`
    # module without this helper (then: the cloud path, i.e. unchanged).
    is_local = getattr(_llm_module, "is_local_server", None)
    if not (is_local and is_local(client)):
        return client.with_structured_output(schema, method="function_calling").invoke(messages)
    bound = client.bind_tools([convert_to_openai_tool(schema)], tool_choice="required")
    reply = _invoke_forced(bound, messages)
    for call in getattr(reply, "tool_calls", None) or []:
        if call.get("name") == schema.__name__:
            return schema.model_validate(call.get("args") or {})
    return None


def _invoke_forced(bound_llm, convo):
    """Invoke a tool_choice="required" binding; if the server rejects the forced
    choice (see _forced_tool_choice_rejected), retry once with tool_choice="auto"
    - the same tools, no guarantee. Her system prompt still instructs the right
    tool, and _salvage_plain_text covers a plain-text answer. A rate limit
    (429 - free cloud tiers) triggers ONE bounded wait and retry (see
    _is_rate_limited). Other exceptions (timeouts, connection drops, real schema
    400s) propagate unchanged.
    """
    try:
        return bound_llm.invoke(convo)
    except Exception as exc:
        if _forced_tool_choice_rejected(exc):
            print("[Amadeus] Server rejected forced tool choice; retrying with tool_choice=auto:",
                  type(exc).__name__)
            return bound_llm.bind(tool_choice="auto").invoke(convo)
        if _is_rate_limited(exc):
            if _is_daily_quota_exhausted(exc):
                print("[Amadeus] API daily quota exhausted - waiting cannot "
                      "help, skipping the wait.")
                raise
            now = time.monotonic()
            if now - _RATE_LIMIT_STATE["last_wait"] >= _RATE_LIMIT_REWAIT_AFTER:
                wait = _rate_limit_wait_seconds(exc)
                _RATE_LIMIT_STATE["last_wait"] = now
                print(f"[Amadeus] API rate limit (429); waiting {wait:.0f}s, then retrying once.")
                time.sleep(wait)
                return bound_llm.invoke(convo)  # a second 429 raises (no stacked waits)
        raise



# pre: messages is the assembled prompt list (system blocks followed by history)
# post: a new list whose LEADING run of system messages is folded into a single
#       system message; everything else is unchanged.
#
# Why: Amadeus builds several system blocks (personality, timing context, output
# rules, voice block, search block). Most chat templates only accept ONE leading
# system message - Qwen's template raises on a second one, and some templates
# (older Mistral-class) silently DROP system messages they don't handle. Folding
# the leading run into one message is the maximum-compatibility layout for every
# model family, and also removes a few tokens of per-message framing overhead.
def _merge_leading_system_messages(messages):
    start = 0
    while start < len(messages) and messages[start].get("role") == "system":
        start += 1
    if start <= 1:
        return list(messages)
    merged = [m.get("content", "") for m in messages[:start]]
    return [{"role": "system", "content": "\n\n".join(merged)}] + list(messages[start:])


# Prompt order A (2026-10-22). A server skips re-reading a prompt only up to the
# first byte that changed since the last call (llama-server's prompt cache, cloud
# prompt caching). So every FIXED block comes first - personality, voice rules,
# reply rules, web rules - and the blocks that change between replies (closeness,
# appearance, memories, time, greeting instruction) come after them, right before
# the conversation. Measured on the built-in model: a changed block at the top
# re-read all ~7,300 pieces; at the end, ~40.
#
# Prompt order B (right_now=True, built-in model only): her model's sliding-
# window layers can resume only from a point the server saved at the start of
# the newest user message, so the changing blocks leave the instruction
# message and become one "right now" note AFTER the newest user message (the
# next reply then differs only after that point). Measured on a real chat:
# ~850 pieces re-read instead of ~5,000-7,500 (a note BEFORE the newest user
# message re-read the whole chat history).
# The label's wording matters (measured 2026-10-22, "What time is it right
# now?" x10): "private background" made her refuse to read the clock 4/5 times,
# "use it freely" answered 10/10.
_RIGHT_NOW_LABEL = ("RIGHT NOW (what you know at this moment - use it freely; "
                    "the user did not write this):\n\n")


def _ordered_prompt(rules_block, web_block, changing, context, right_now=False):
    fixed = (store.load_default_personality_messages()
             + [ja_voice.build_voice_rules()]
             + [rules_block]
             + [web_block])
    changing = ([ja_voice.build_closeness_context(stats.load_stat("trust"))]
                + list(changing))
    if not right_now:
        return _merge_leading_system_messages(fixed + changing + list(context))
    note = {"role": "system", "content": _RIGHT_NOW_LABEL
            + "\n\n".join(m.get("content", "") for m in changing)}
    return _merge_leading_system_messages(fixed + list(context)) + [note]


def _right_now_note() -> bool:
    """Order B only on her built-in model (looked up at call time - several
    tests stand in a fake `llm` module)."""
    builtin = getattr(_llm_module, "builtin_llm", None)
    try:
        return bool(builtin and builtin.active())
    except Exception:
        return False


# --- Search-failure honesty net -------------------------------------------------
# When a search turn cannot be completed - the final call failed (rate limit,
# timeout, dropped connection) or came back empty with no search having run -
# the only surviving text is her pre-search announcement ("I'll search... just a
# moment"). Serving that as the reply would promise an action that never
# happened. Return this fixed honest line instead: STATIC (no model call - the
# failure may be quota/network related, so spending another API call on it would
# be wrong), same idiom as the web-off honesty net.
def _honest_search_failure_pack(weather: bool = False) -> "AmadeusPack":
    """STATIC honest line for a web-ON turn whose reply could not be
    completed. When the live weather data WAS fetched and only the final
    packaging call failed, saying 'I couldn't finish the search' would be a
    lie (no search was involved) - the weather variant says the truth."""
    if weather:
        return AmadeusPack(
            assistant_reply_JPS=(
                "天気データはきちんと取れたんだけど、返事をまとめるところで"
                "途中で止まってしまったみたい。一時的な不具合だと思うの。"
                "もう一度聞いてもらえない？"
            ),
            assistant_reply_ENG=(
                "I did get the live weather data, but my reply got cut off "
                "on my side. It's probably a temporary glitch - could you ask "
                "me again?"
            ),
        )
    return AmadeusPack(
        assistant_reply_JPS=(
            "今は検索を最後までできなかったの。たぶん一時的な不具合だと思うわ。"
            "少し待ってから、もう一度聞いてくれる？"
        ),
        assistant_reply_ENG=(
            "I couldn't actually finish the search this time. It's probably a "
            "temporary glitch, could you ask me again in a moment?"
        ),
    )


# Gemma 4 can write its native tool-call syntax as TEXT inside a field of an
# otherwise valid pack and repeat it until the output cap (Run 1, 2026-10-19:
# assistant_reply_ENG = "<|tool_call>call:AmadeusPack{assistant_reply_ENG:" x ~200,
# JPS empty, 66 s, no voice). _repetition_cut only ever saw the plain-text
# salvage path, never the parsed pack's fields.
_TOOL_CALL_TEXT = re.compile(r"<\|tool_call>|<tool_call\|>|call:Amadeus(?:Pack|Reply)\{")
_LOOP_CHECK_MIN_CHARS = 600   # a real reply may repeat a phrase; a loop runs long


def _degenerate_field(text) -> str:
    """Why this reply field is unusable ('' = fine): tool-call markup written
    as text, or a long repetition loop."""
    text = text if isinstance(text, str) else ""
    if _TOOL_CALL_TEXT.search(text):
        return "tool-call text in the reply"
    if len(text) >= _LOOP_CHECK_MIN_CHARS and _repetition_cut(text) is not None:
        return "repetition loop in the reply"
    return ""


def _degenerate_pack(p) -> str:
    """'' when the pack's lines are usable, else the reason."""
    for field in ("assistant_reply_JPS", "assistant_reply_ENG"):
        why = _degenerate_field(getattr(p, field, "") if not isinstance(p, dict) else p.get(field))
        if why:
            return why
    return ""


# A translation pass may only run on a real line, and a model that answers it
# with a request ("Please provide the Japanese text you would like me to
# translate" - Run 4, 2026-10-21, after a broken reply left a field empty) has
# not translated anything.
_META_TRANSLATION = re.compile(
    r"\b(?:provide|paste|send|share|give|enter)\b.{0,40}\b(?:text|sentence|line|japanese|english)\b"
    r"|\bwould you like me to translate\b|\bnothing to translate\b", re.I)


def _usable_source(text) -> bool:
    return len(re.sub(r"[\s\W_0-9]", "", text or "")) >= 2


def _honest_plain_failure_pack() -> "AmadeusPack":
    """STATIC honest line for a NON-search turn whose reply degenerated and
    could not be salvaged (e.g. a repetition loop with no clean part). No
    model call - the failure may be model/state related, so spending another
    API call on the apology would be wrong. Same idiom as the search-failure
    line above."""
    return AmadeusPack(
        assistant_reply_JPS=(
            "先ほどうまく言葉がまとまらず、まわり道になってしまったみたい。"
            "少し待ってから、もう一度言ってもらえる？"
        ),
        assistant_reply_ENG=(
            "I got tangled up in my own words there. Could you give me a "
            "moment and ask me again?"
        ),
    )


# --- Repetition-loop guard ------------------------------------------------------
# A weak (or throttled) model can DEGENERATE into a repetition loop: the same
# ~5-word phrase (EN) or ~12-char span (JA) cycling 3+ times, usually cut off
# mid-sentence by the token cap. A token-level repetition_penalty does not
# reach this (it penalizes immediate token re-emission, not long-period phrase
# cycling), so the guard lives here, on the final text: a pure string scan -
# zero API calls, zero latency. Applied only at the web loop's phase-3
# boundary and on the normal path's salvage, so happy-path replies are
# untouched.
#
# Two shingle sizes per language: the strict one (long shingle, 3 hits) catches
# exact cycling; the loose one (shorter shingle, 4 hits) still catches loops
# with light variation - an occasional swapped word breaks every window that
# covers it, but the windows beside it keep repeating.
def _compact_index_to_char(text: str, idx: int):
    """Map an index into the whitespace-stripped form of `text` back to the
    character index in the original text."""
    count = 0
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        if count == idx:
            return i
        count += 1
    return None


def _repetition_cut(text: str):
    """Return the character index where a repetition cycle begins, or None
    when the text is clean."""
    text = (text or "").strip()
    if len(text) < 80:
        return None

    best = None  # (cut_index, hit_count)

    # --- English: word shingles --------------------------------------------
    toks = [(m.group(), m.start()) for m in re.finditer(r"\S+", text)]
    if len(toks) >= 30:
        for n, need, spread in ((5, 3, 20), (4, 4, 16)):
            spans = {}
            for i in range(len(toks) - n + 1):
                sh = " ".join(w for w, _ in toks[i:i + n]).lower()
                if sh.isascii():
                    spans.setdefault(sh, []).append(i)
            for pos in spans.values():
                if len(pos) >= need and (pos[-1] - pos[0]) >= spread:
                    cut = toks[pos[1]][1]
                    if best is None or len(pos) > best[1]:
                        best = (cut, len(pos))
    if best is not None:
        return best[0]

    # --- Japanese: character shingles --------------------------------------
    compact = "".join(ch for ch in text if not ch.isspace())
    if len(compact) >= 48 and any("\u3040" <= ch <= "\u30ff" for ch in compact):
        for n, need, spread in ((12, 3, 36), (10, 4, 30)):
            spans = {}
            for i in range(len(compact) - n + 1):
                spans.setdefault(compact[i:i + n], []).append(i)
            for pos in spans.values():
                if len(pos) >= need and (pos[-1] - pos[0]) >= spread:
                    cut = _compact_index_to_char(text, pos[1])
                    if cut is not None and (best is None or len(pos) > best[1]):
                        best = (cut, len(pos))
        if best is not None:
            return best[0]
    return None


def _de_loop(text: str) -> str:
    """Cut a repetition loop out of `text`: keep everything before the cycle's
    second occurrence, trimmed back to the last sentence end. Clean texts are
    returned unchanged (stripped)."""
    text = (text or "").strip()
    cut = _repetition_cut(text)
    if cut is None:
        return text
    seg = text[:cut]
    m = max(seg.rfind(p) for p in ".!?。！？")
    if m >= len(seg) // 2:
        seg = seg[:m + 1]
    return seg.strip()


# --- Anti-anchoring nudge + refusal override ------------------------------------
# A history full of "I can't search" refusals (the web-off era) can ANCHOR a
# weak model: asked to search after web comes back ON, it completes the
# pattern (refuse again) instead of re-reading the system prompt. A big model
# breaks the pattern; a small one may not. Two layers for exactly that
# situation:
#   (a) a one-line internal note for the turn (prompt level - cheap, but a
#       stubborn model can still ignore it);
#   (b) a REFUSAL OVERRIDE the model cannot opt out of: when the turn is a
#       recognizable retry of a just-refused search, the app runs the search
#       itself and feeds the results back - the model then only has to write
#       the answer from real data.
# Every other turn is byte-identical: the note is static text added only in
# that exact situation, and the override fires only on its triple condition
# (recent refusal + retry phrasing + a search topic in the recent history).
_SEARCH_REFUSAL_EN = (
    "can't search", "cannot search", "couldn't search", "can't do a search",
    "unable to search", "can't use web", "no web access",
    "don't have access", "don't actually have the capability",
    "can't access the web", "can't look it up", "couldn't look it up",
    "couldn't actually finish the search", "without web access",
    # "switched off" wordings (2026-10-25, Amadeus-Fresh: "my web search
    # function is currently turned off" was not recognised)
    "search is off", "search is currently off", "search is turned off",
    "search is currently turned off", "search function is turned off",
    "search function is currently turned off", "search function is off",
    "web access is off", "web access is currently off", "web access is turned off",
)
_SEARCH_REFUSAL_JA = (
    "検索できない", "検索は使えない", "検索を使えない", "検索ができません",
    "ウェブがオフ", "ウェブ検索は使えない", "ウェブ検索が使えない",
    "検索を最後までできなかった", "アクセスできない",
    "検索機能はオフ", "検索機能がオフ", "検索はオフ", "検索がオフ",
    "アクセスはオフ", "アクセスがオフ",
)


def _recent_search_refusal(messages) -> bool:
    """True when one of the last four assistant turns contains a search refusal
    (the web-off-era pattern that anchors weak models)."""
    recent = [m.get("content", "") for m in messages
              if m.get("role") == "assistant"
              and isinstance(m.get("content"), str)][-4:]
    for a in recent:
        al = a.lower()
        if (any(p in al for p in _SEARCH_REFUSAL_EN)
                or any(p in a for p in _SEARCH_REFUSAL_JA)):
            return True
    return False


def _last_reply_refused_search(messages) -> bool:
    """True when her most recent reply said she cannot search."""
    last = next((m.get("content", "") for m in reversed(messages)
                 if m.get("role") == "assistant"
                 and isinstance(m.get("content"), str)), "")
    return bool(last) and (any(p in last.lower() for p in _SEARCH_REFUSAL_EN)
                           or any(p in last for p in _SEARCH_REFUSAL_JA))


_RETRY_INTENT_RE = re.compile(
    r"\btry again\b|\btry once more\b|\bone more time\b|\bretry\b"
    r"|\bplease try\b|\bagain\b"
    r"|\bnow (?:it|web) (?:is|was) on\b"
    r"|turned (?:it|web|your web) (?:back )?on\b"
    r"|もう一度|もう一回|もう試して|もう一回試して|再度|もう一回だけ",
    re.IGNORECASE,
)


def _search_retry_intent(messages) -> bool:
    """True when the latest user message is a retry of a search that the recent
    history shows was just refused (retry phrasing + a recent refusal above)."""
    if not _recent_search_refusal(messages):
        return False
    last_user = next(
        (m.get("content", "") for m in reversed(messages)
         if m.get("role") == "user"),
        "",
    )
    return (isinstance(last_user, str)
            and bool(_RETRY_INTENT_RE.search(last_user)))


def _find_prior_search_query(messages, lookback=8) -> str:
    """The searchable topic of the most recent explicit search request (within
    `lookback` user turns) - the query to use when the app runs a
    refusal-override search itself, or when the latest message is a bare retry
    ("try to search again?") with no topic of its own. Extracted the same way
    as the fast path's query, so it is a clean topic, never a full message.
    "" when there is none."""
    user_msgs = [m.get("content", "") for m in reversed(messages)
                 if m.get("role") == "user"
                 and isinstance(m.get("content"), str)]
    for c in user_msgs[:lookback]:
        if _EXPLICIT_SEARCH_RE.search(c):
            topic = _extract_search_topic(c)
            if topic:
                return topic
    return ""

# --- Explicit-request fast path ---------------------------------------------------
# With web access ON, every message normally goes through the phase-1 judgement
# call ("does this need a search?"). When the user EXPLICITLY asks for a search
# in their own message, that call can only answer "yes" - pure overhead (one API
# call plus latency). Skip it and search directly on the user's own words; the
# results then flow through the exact same handling code. Deliberately
# conservative (search-verb phrases and 'online' only) so "I turned your web
# access on" or an ordinary question never triggers the fast path - her
# proactive judgement on normal chat is untouched.
_EXPLICIT_SEARCH_RE = re.compile(
    r"\bsearch(?:ing|ed|es)?\b"
    r"|\blook(?:ing|ed)? (?:it |that |this )?up\b"
    r"|\blook into\b"
    r"|\bonline\b"
    r"|\bgoogle\b",
    re.IGNORECASE,
)


def _user_explicitly_asks_for_search(messages) -> str:
    """Return the user's latest message when it explicitly asks for a web search
    (its topic - see _extract_search_topic - becomes the search query), else
    "" (run the normal judgement call)."""
    last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    if isinstance(last_user, str) and _EXPLICIT_SEARCH_RE.search(last_user):
        return last_user.strip()
    return ""


# --- Explicit-request topic extraction -----------------------------------------
# The fast path's query used to be the user's ENTIRE message. Real messages
# carry small talk around the actual request ("Good afternoon, just had
# breakfast... try a search for the weather in Springfield please"), and a
# keyword-soup query like that gets back unrelated pages - greeting-card
# quotes for the small talk, tech-support articles for a bare "try to search
# again?" - which her model then papers over with plausible-sounding
# invention. So the query is the extracted TOPIC only. When no topic can be
# pulled out (bare retries, "search for it"), the caller falls back to the
# previous explicit topic from history, and if there is none the fast path
# does not fire at all and phase 1 runs (she writes her own clean query).

_TOPIC_TRAILING_PLEASE_RE = re.compile(r"\s*[,.]?\s*please\s*$", re.IGNORECASE)
_TOPIC_TRAILING_FOR_ME_RE = re.compile(r"\s+for\s+(?:me|you)\s*$", re.IGNORECASE)
_TOPIC_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:the\s+)?(?:web|internet)(?:\s+(?:about|on|for))?\s+"
    r"|about\s+|for\s+|on\s+|up\s+)", re.IGNORECASE)
# Captured "topics" that are not topics: pronouns and retry/noise words.
_TOPIC_STOPWORDS = {
    "it", "me", "you", "this", "that", "these", "those", "again", "more",
    "once", "anything", "something", "the web", "web", "online",
    "the internet", "internet", "search", "up", "down", "working",
}
# Per-token noise set: a captured topic made up ONLY of these is request
# debris ("me again", "it now"), never a search topic. Finite by nature -
# pronouns and deictics - so it does not grow with new domains.
_TOPIC_NOISE_TOKENS = {
    "i", "me", "my", "you", "your", "we", "us", "he", "him", "she", "her",
    "it", "its", "they", "them", "again", "that", "this", "something",
    "anything", "one", "now", "today",
}
# Phrasal request patterns: verb + preposition + topic. The phrasing itself
# marks a request, so no context check is needed.
_TOPIC_PATTERNS = (
    re.compile(r"\bsearch\s+for\s+([^.!?]+?)(?:\s*[.!?]|\s*\Z)", re.IGNORECASE),
    re.compile(r"\blook(?:ing|ed)?\s+into\s+([^.!?]+?)(?:\s*[.!?]|\s*\Z)", re.IGNORECASE),
    re.compile(r"\blook(?:ing|ed)?\s+(?:it\s+|that\s+|this\s+)?up\s+([^.!?]+?)(?:\s*[.!?]|\s*\Z)", re.IGNORECASE),
    re.compile(r"\bgoogle\s+([^.!?]+?)(?:\s*[.!?]|\s*\Z)", re.IGNORECASE),
)
# Bare "search X": "search" is also a common NOUN ("is web search working?"),
# so this pattern needs a request context: the word right before "search"
# must not be one that marks a noun use.
_BARE_SEARCH_TOPIC_RE = re.compile(r"\bsearch\s+([^.!?]+?)(?:\s*[.!?]|\s*\Z)", re.IGNORECASE)
_BARE_SEARCH_NOUN_PREV = {
    "web", "the", "your", "our", "their", "my", "his", "her", "its",
    "this", "that", "a", "an",
}


def _clean_search_topic(raw) -> str:
    """Trim a captured topic into a usable query: collapse whitespace, drop
    trailing sentence punctuation, a closing "please" and a trailing
    "for me/you", drop leading filler ("the web about X" -> "X"), and reject
    pronoun/noise "topics"."""
    topic = re.sub(r"\s+", " ", raw or "").strip()
    topic = topic.rstrip(" .!,?")
    topic = _TOPIC_TRAILING_PLEASE_RE.sub("", topic)
    topic = _TOPIC_TRAILING_FOR_ME_RE.sub("", topic)
    topic = topic.rstrip(" .!,?")
    topic = _TOPIC_LEADING_FILLER_RE.sub("", topic).strip(" ,;")
    if not topic or topic.lower() in _TOPIC_STOPWORDS:
        return ""
    # All-noise topics ("me again") are request debris: reject them so the
    # caller falls back to the previous search query instead of feeding the
    # debris to the search engine.
    tokens = re.findall(r"[A-Za-z']+", topic)
    if tokens and all(t.lower() in _TOPIC_NOISE_TOKENS for t in tokens):
        return ""
    return topic[:200]


def _extract_search_topic(message) -> str:
    """The searchable topic inside an explicit search request, or "".

    Only the part of the message AFTER the request verb becomes the query;
    the last matching verb wins (the request is what the message ends on).
    Returns "" when the message carries a search trigger but no usable topic
    - the caller then falls back; the raw message is never searched.
    """
    msg = (message or "").strip()
    if not msg:
        return ""
    for pattern in _TOPIC_PATTERNS:
        matches = pattern.findall(msg)
        if matches:
            topic = _clean_search_topic(matches[-1])
            if topic:
                return topic
    for m in reversed(list(_BARE_SEARCH_TOPIC_RE.finditer(msg))):
        before = re.findall(r"[A-Za-z']+", msg[:m.start()])
        if before and before[-1].lower() in _BARE_SEARCH_NOUN_PREV:
            continue  # noun use ("web search ..."), not a request
        topic = _clean_search_topic(m.group(1))
        if topic:
            return topic
    return ""


# --- Web-ON false-refusal net ---------------------------------------------------
# Mirror of the web-off honesty net, for the OPPOSITE error: with web access
# ON, a weak model anchored by a history of "I can't search" turns can still
# claim in a perfectly ordinary reply (good night, small talk) that web
# search is unavailable - a false statement about the current settings. The
# anti-anchoring nudge attacks this at prompt level; this net is the
# structural catch: when a finished web-ON reply contains a search refusal
# but the user never asked for a search this turn, ONE bounded corrective
# rewrite asks her to redo the reply without that statement. Dormant
# otherwise (plain substring scan; the rewrite costs a call only in the bad
# case). A refusal is never rewritten when the user actually asked for a
# search - there it may be the honest outcome.
def _web_on_false_refusal_net(pack: "AmadeusPack", llm, messages) -> "AmadeusPack":
    eng = pack.assistant_reply_ENG or ""
    eng_l = eng.lower()
    jps = pack.assistant_reply_JPS or ""
    refused = (any(p in eng_l for p in _SEARCH_REFUSAL_EN)
               or any(p in jps for p in _SEARCH_REFUSAL_JA))
    if not refused:
        return pack
    if (_user_explicitly_asks_for_search(messages)
            or _search_retry_intent(messages)):
        return pack  # a search WAS requested: the refusal may be honest
    if eng_l.strip().startswith("i couldn't actually finish the search"):
        return pack  # the static honest search-failure line (a search was
                     # attempted and failed) is fine as-is
    print("[Amadeus] Web ON: reply wrongly claims web search is unavailable - "
          "one corrective rewrite.")
    rewrite_llm = llm.bind_tools(
        [convert_to_openai_tool(AmadeusPack)], tool_choice="required")
    convo = (list(messages)
             + [{"role": "assistant", "content": eng}]
             + [{"role": "user", "content": (
                 "(Internal note: in your last reply you stated that you cannot "
                 "use web search, but web access is ON in the settings and no "
                 "search failed this turn - that statement is wrong. Rewrite the "
                 "reply WITHOUT any statement about web search being unavailable, "
                 "keeping everything else the same, using the AmadeusPack tool.)"
             )}])
    try:
        reply = _invoke_forced(rewrite_llm, convo)
    except Exception as exc:
        print("[Amadeus] Web ON: corrective rewrite failed:", repr(exc))
        return pack
    calls = getattr(reply, "tool_calls", None) or []
    if calls and calls[0].get("name") == "AmadeusPack":
        new = _pack_from_args(calls[0].get("args"))
        if not any(p in (new.assistant_reply_ENG or "").lower()
                   for p in _SEARCH_REFUSAL_EN):
            return new
        print("[Amadeus] Web ON: rewrite still refuses - serving the original.")
    return pack

# --- Web-ON pretend-lookup net (2026-10-25) --------------------------------------
# The opposite of the false-refusal net above: web access is ON, but nothing
# was searched or fetched for this reply, and the reply still says it looked
# something up ("according to the latest data..."). ONE corrective rewrite;
# if that still claims a lookup, a short honest line replaces it.
_LOOKUP_CLAIM_EN = (
    "search result", "i searched", "i've searched", "i looked it up", "i looked up",
    "i've looked", "the latest data", "latest information", "according to the data",
    "checked online", "i found online", "based on my search", "my search show",
    "i just pulled", "i just ran a search", "i just performed a search",
)
_LOOKUP_CLAIM_JA = (
    "検索結果", "検索した", "検索してみた", "調べたところ", "調べてみた", "調べた結果",
    "最新のデータ", "最新の情報によると", "ネットで確認", "ネットで調べ",
)


def _claims_lookup(pack: "AmadeusPack") -> bool:
    eng = (pack.assistant_reply_ENG or "").lower()
    jps = pack.assistant_reply_JPS or ""
    return (any(p in eng for p in _LOOKUP_CLAIM_EN)
            or any(p in jps for p in _LOOKUP_CLAIM_JA))


def _honest_no_lookup_pack() -> "AmadeusPack":
    return AmadeusPack(
        assistant_reply_JPS=("ごめんなさい、今のは実際に調べた答えじゃなかったわ。"
                             "何を調べてほしいか言ってくれたら、ちゃんと検索するから。"),
        assistant_reply_ENG=("Sorry - that wasn't from an actual search. Tell me what "
                             "you'd like me to look up, and I'll really search for it."),
    )


def _pretend_lookup_net(pack: "AmadeusPack", llm, messages) -> "AmadeusPack":
    if _lookup_ran() or not _claims_lookup(pack):
        return pack
    print("[Amadeus] Web ON: reply claims a lookup but nothing was searched - "
          "one corrective rewrite.")
    rewrite_llm = llm.bind_tools(
        [convert_to_openai_tool(AmadeusPack)], tool_choice="required")
    convo = (list(messages)
             + [{"role": "assistant", "content": pack.assistant_reply_ENG or ""}]
             + [{"role": "user", "content": (
                 "(Internal note: you did NOT search the web or fetch any data in "
                 "this reply, but your last reply says you did. Rewrite it WITHOUT "
                 "saying you searched, looked anything up, checked online or have "
                 "'the latest data'. For the date or time, use your RIGHT NOW note. "
                 "Use the AmadeusPack tool.)"
             )}])
    try:
        reply = _invoke_forced(rewrite_llm, convo)
        calls = getattr(reply, "tool_calls", None) or []
        if calls and calls[0].get("name") == "AmadeusPack":
            new = _pack_from_args(calls[0].get("args"))
            if not _claims_lookup(new) and not _degenerate_pack(new):
                return new
    except Exception as exc:
        print("[Amadeus] Web ON: pretend-lookup rewrite failed:", repr(exc))
    print("[Amadeus] Web ON: rewrite still claims a lookup - serving the honest line.")
    return _honest_no_lookup_pack()


# --- Web-ON tense net (2026-10-25) -----------------------------------------------
# On October 25 she said "the console versions were released on October 29"
# (search test set: ~4 of 150 answers). Her notes already label every date
# ("upcoming, in 4 days"); this net checks her reply: a date after today in a
# past-tense sentence (English or Japanese line) gets ONE corrective rewrite.
# Still wrong -> the original is served (never blocks a reply).
def _date_problems(pack: "AmadeusPack"):
    """(future dates said as past, months called "last year" that her English
    line dates to this year) in her reply."""
    eng, jps = pack.assistant_reply_ENG or "", pack.assistant_reply_JPS or ""
    return (webdates.past_tense_future_dates(eng, jps),
            webdates.last_year_on_this_year(eng, jps))


_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December")


def _tense_net(pack: "AmadeusPack", llm, messages) -> "AmadeusPack":
    if not _lookup_ran():
        return pack
    future_past, last_year = _date_problems(pack)
    if not future_past and not last_year:
        return pack
    now = datetime.now()
    today = now.strftime("%B %d, %Y")
    problems = []
    if future_past:
        dates = ", ".join(sorted({d.strftime("%B %d, %Y") for d, _ in future_past}))
        problems.append(f"{dates} is still in the FUTURE, but you described it as already "
                        "past - say it is upcoming (\"will be released on ...\" / 「…に発売予定」)")
        print(f"[Amadeus] Web ON: reply puts upcoming dates ({dates}) in the past tense.")
    if last_year:
        months = ", ".join(sorted({f"{_MONTH_NAMES[m - 1]} {now.year}" for m, _ in last_year}))
        problems.append(f"{months} is THIS year ({now.year}), but you called it last year "
                        "(去年 / 昨年 / \"last ...\") - say this year (今年) or give the date")
        print(f"[Amadeus] Web ON: reply calls {months} \"last year\".")
    print("[Amadeus] Web ON: one corrective rewrite for the dates.")
    rewrite_llm = llm.bind_tools(
        [convert_to_openai_tool(AmadeusPack)], tool_choice="required")
    convo = (list(messages)
             + [{"role": "assistant", "content": pack.assistant_reply_ENG or ""}]
             + [{"role": "user", "content": (
                 f"(Internal note: today is {today}. In your last reply: "
                 + "; ".join(problems) + ". Rewrite the reply with the dates right in BOTH the "
                 "Japanese and the English line, keeping everything else the same. Use the "
                 "AmadeusPack tool.)"
             )}])
    try:
        reply = _invoke_forced(rewrite_llm, convo)
        calls = getattr(reply, "tool_calls", None) or []
        if calls and calls[0].get("name") == "AmadeusPack":
            new = _pack_from_args(calls[0].get("args"))
            still_future, still_last = _date_problems(new)
            if not _degenerate_pack(new) and not still_future and not still_last:
                return new
    except Exception as exc:
        print("[Amadeus] Web ON: tense rewrite failed:", repr(exc))
    print("[Amadeus] Web ON: tense rewrite did not help - serving the original.")
    return pack


def _getResponsePackedWithWebSearch(llm, messages) -> "AmadeusPack":
    """Web-search turn with a hard honesty guarantee.

    Any failure inside the loop (rate limit, timeout, dropped connection,
    unusable model output) returns _honest_search_failure_pack() instead of
    propagating. Why not let the generic outer fallback retry? It retries with
    the ORIGINAL messages - which lack any search results from this turn - so it
    can at best reproduce her pre-search announcement as the "answer" (the exact
    failure this guard exists to remove).
    """
    try:
        pack = _web_search_loop(llm, messages)
    except Exception as exc:
        if maybe_strip_rejected_params(exc, LLM_Model):
            # Server rejected a sampling parameter: rebuild the client
            # without it and give the search loop one clean retry before
            # the honest fallback.
            try:
                llm = _reply_llm()
                pack = _web_search_loop(llm, messages)
            except Exception as exc2:
                print("[Amadeus] Web loop: retry after sampling-parameter rejection failed -",
                      "honest fallback:", repr(exc2))
                return _honest_search_failure_pack()
        else:
            print("[Amadeus] Web loop: search turn could not be completed - honest fallback:", repr(exc))
            return _honest_search_failure_pack()
    pack = _web_on_false_refusal_net(pack, llm, messages)
    return _tense_net(_pretend_lookup_net(pack, llm, messages), llm, messages)


# =============================================================================
#  WEB TRIAGE - model-judged routing for the web-ON fast paths
#
#  One small structured call reads the user's LATEST message (plus the recent
#  conversation) and decides how to route it: weather question (which place),
#  explicit search request (which topic), or neither. This replaces the old
#  per-phrase keyword routing - intent and place are UNDERSTOOD from context,
#  not pattern-matched, so unseen phrasings route the same as seen ones.
#
#  Bounded by construction:
#   * a broad keyword prefilter decides whether the call is worth making
#     (a miss is harmless - the message falls through to the normal tool
#     loop, where she can still search on her own judgement);
#   * when the call itself fails, _deterministic_route runs instead, so the
#     old keyword behaviour remains as the safety net.
# =============================================================================


class WebTriagePack(BaseModel):
    # PLAIN REQUIRED STRINGS ONLY - the exact shape of AmadeusPack, which is
    # proven to decode cleanly on the local Unsloth/llama.cpp server. Optional /
    # anyOf / null / boolean fields make constrained decoding HANG the
    # single-threaded server (verified 2026-09-16: an anyOf schema request
    # wedged it until restart). So the router speaks "yes"/"no" and "none".
    wants_weather: str = Field(..., description=(
        "\"yes\" or \"no\". \"yes\" when the user's LATEST message asks about "
        "weather or air quality - current conditions, forecast, chance of rain "
        "or snow, temperature, humidity, AQI, UV index, or whether they need "
        "an umbrella - in ANY phrasing. A request to search for the weather is "
        "a weather question, not a search."))
    weather_place: str = Field(..., description=(
        "The place the weather question is about, as one short proper name "
        "(town, city, region or country). Use the place named in the latest "
        "message; if none is named, use the one the recent conversation makes "
        "unambiguous (e.g. 'my hometown' or 'here' when exactly one place has "
        "clearly been the subject). Write \"none\" when no place can be "
        "determined without guessing - never invent or assume one."))
    wants_search: str = Field(..., description=(
        "\"yes\" or \"no\". \"yes\" when the user's LATEST message explicitly "
        "asks to search, look up, check or verify something on the web (and it "
        "is not a weather question)."))
    search_topic: str = Field(..., description=(
        "A short, specific web-search query for what they want looked up - the "
        "actual topic, never the request wording. Write \"none\" when the "
        "message only asks to try the previous search again with no new topic, "
        "or when wants_search is \"no\"."))
    wants_web_check: str = Field(..., description=(
        "\"yes\" or \"no\". \"yes\" when the user's LATEST message asks whether "
        "web access / web search works, is on, or asks to test or confirm it - "
        "including a short follow-up like \"I just turned it on, can you "
        "confirm?\" right after web access was discussed. \"no\" when they ask "
        "to look up a specific topic (that is wants_search)."))


WEB_TRIAGE_TOOL = convert_to_openai_tool(WebTriagePack)

_TRIAGE_SYSTEM = (
    "You are the intent router for a personal assistant's web features. "
    "Read the conversation and classify ONLY the user's LATEST message. "
    "Call the web_triage tool exactly once, with the fields set as described. "
    "Set wants_weather and wants_search to no and the other fields to none "
    "when the latest message asks for nothing that needs the web - small talk, "
    "timeless facts, or follow-up comments are NOT search requests. "
    "Never put a place in weather_place unless the message or the recent "
    "conversation clearly identifies one. "
    "Set wants_web_check to yes only when the user wants to know whether web "
    "access itself works (test it, confirm it is on)."
)

# "Check / test / confirm your web access" (2026-10-25): the app runs a REAL
# test search on a neutral, always-fresh topic, so she reports what she
# actually found - never a guess, never the current time (stale clock pages).
WEB_CHECK_QUERY = "top news today"
_WEB_CHECK_NOUN_RE = re.compile(
    r"\b(web|internet|online|search|searching|browsing)\b|ウェブ|ネット|インターネット|検索",
    re.IGNORECASE)
_WEB_CHECK_VERB_RE = re.compile(
    r"\b(check|test|testing|confirm|working|works|enabled|on|access)\b"
    r"|確認|テスト|使える|動いて|オン", re.IGNORECASE)
# Keyword fallback only (triage unavailable): "your/the web access ..." phrasing.
_WEB_CHECK_STRICT_RE = re.compile(
    r"\b(your|the|my)\s+(web|internet)(\s+(access|search|connection))?\b"
    r"|\bweb\s+(access|search)\s+(is\s+)?(working|on|enabled)\b"
    r"|(ウェブ|ネット|インターネット|検索)(機能|アクセス)?(が|は)?(使える|動いて|オン)",
    re.IGNORECASE)


def _web_check_hint(last_user) -> bool:
    return bool(isinstance(last_user, str)
                and _WEB_CHECK_NOUN_RE.search(last_user)
                and _WEB_CHECK_VERB_RE.search(last_user))

# Broad, HIGH-RECALL prefilter: decides whether a message is a CANDIDATE for
# the triage call. Deliberately a superset of the keyword intent check - plain
# words, no cleverness, and NEVER the decider: a miss just means the message
# goes to the normal tool loop (harmless), a hit costs one small triage call.
_WEB_TRIAGE_HINT_RE = re.compile(
    r"\b(umbrella|raincoat|sunscreen|parasol|jacket)\b"
    r"|\bdo\s+i\s+need\b"
    r"|\bbring\s+(an?\s+)?(umbrella|jacket)\b"
    r"|傘|雨|天気|気温|湿度|検索|調べて|ニュース",
    re.IGNORECASE,
)


def _web_triage_candidate(last_user, messages) -> bool:
    """Cheap prefilter: is this message worth a triage call?"""
    if not isinstance(last_user, str) or not last_user:
        return False
    if _weather_intent(last_user) or _user_explicitly_asks_for_search(messages):
        return True
    # A web-access check, or the message right after her LAST reply said web
    # access was off ("I just turned it on. Could you confirm?").
    if _web_check_hint(last_user) or _last_reply_refused_search(messages):
        return True
    return bool(_WEB_TRIAGE_HINT_RE.search(last_user))


_TRIAGE_TIMEOUT = 25  # seconds - hard client-side deadline for the triage call


def _invoke_with_timeout(fn, *args, timeout, **kwargs):
    """Run fn(*args) with a HARD client-side deadline.

    A local server that stalls on one request must never stall the reply: the
    caller gets a TimeoutError after `timeout` seconds and falls back to
    keyword routing. The abandoned thread is let go (it cannot block the
    reply further); the real defence against a wedged server is the safe
    all-string triage schema (see WebTriagePack).
    """
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise TimeoutError("web triage call exceeded %ss" % timeout) from exc
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _web_triage(llm, messages):
    """One small LLM call deciding how a web-ON message is routed.

    Returns {"weather": bool, "place": str|None, "search": bool,
    "topic": str|None} (weather outranks search by design), or None when the
    call fails - the caller then uses the keyword fallback, so a dead or slow
    triage call can only cost bounded time, never the reply.
    """
    convo = [{"role": "system", "content": _TRIAGE_SYSTEM}]
    # A wide-enough window that a place discussed a while ago ("I drove to
    # my hometown" + the Springfield search yesterday) is still visible to the
    # router, without feeding the whole chat into a call that should stay
    # small. Each message is truncated to keep the call cheap.
    recent = [m for m in messages
              if m.get("role") in ("user", "assistant")
              and isinstance(m.get("content"), str)][-12:]
    for m in recent:
        convo.append({"role": m["role"], "content": m["content"][:600]})
    convo.append({
        "role": "user",
        "content": "Classify the user's latest message. Call the web_triage tool.",
    })
    try:
        bound = llm.bind_tools([WEB_TRIAGE_TOOL], tool_choice="required")
        reply = _invoke_with_timeout(_invoke_forced, bound, convo, timeout=_TRIAGE_TIMEOUT)
        calls = getattr(reply, "tool_calls", None) or []
        if not calls and isinstance(reply.content, str):
            calls = _parse_textual_tool_calls(reply.content)
        triage_name = WEB_TRIAGE_TOOL["function"]["name"]  # "WebTriagePack"
        for call in calls:
            if call.get("name") != triage_name:
                continue
            args = call.get("args") or {}
            def _flag(v):
                return str(v or "").strip().lower() in ("yes", "y", "true", "1")
            def _opt(v):
                s = str(v or "").strip()
                return None if s.lower() in ("", "none", "null", "n/a", "na") else s
            def _topic(v):
                # Same debris guard as the keyword extraction: a pronoun-only
                # "topic" ("me again") becomes None so the search route reuses
                # the previous query instead of feeding junk to the engine.
                t = _opt(v)
                if t is None:
                    return None
                return _clean_search_topic(t) or None
            weather = _flag(args.get("wants_weather"))
            route = {
                "weather": weather,
                "place": _opt(args.get("weather_place")),
                "search": _flag(args.get("wants_search")) and not weather,
                "topic": None if weather else _topic(args.get("search_topic")),
                # A named topic wins: its real search answers the check too.
                "check": (_flag(args.get("wants_web_check")) and not weather
                          and not (_flag(args.get("wants_search"))
                                   and _topic(args.get("search_topic")))),
            }
            print("[Amadeus] Web triage: weather=%s place=%r search=%s topic=%r check=%s"
                  % (route["weather"], route["place"],
                     route["search"], route["topic"], route["check"]))
            return route
    except Exception as exc:
        print("[Amadeus] Web triage call failed - falling back to keyword routing:",
              repr(exc))
        return None
    print("[Amadeus] Web triage: no tool call in the reply - falling back "
          "to keyword routing.")
    return None


def _deterministic_route(last_user, messages, allow_check=False):
    """Keyword routing - the FALLBACK when the triage call is unavailable.

    Returns {"weather": True, "place": str|None} or
    {"search": True, "topic": str|None}, or None when nothing fast-routes
    (the normal tool loop handles the message). Weather outranks search.
    allow_check: only when the triage call itself FAILED - a triage "no"
    is trusted for the web-access check ("I turned your web access on.
    Could you try again?" is a retry, not a check).
    """
    if isinstance(last_user, str) and _weather_intent(last_user):
        return {"weather": True, "place": _extract_place(last_user) or None}
    if allow_check and isinstance(last_user, str) and _web_check_hint(last_user) \
            and _WEB_CHECK_STRICT_RE.search(last_user):
        return {"check": True}
    explicit_message = _user_explicitly_asks_for_search(messages)
    if explicit_message:
        query = _extract_search_topic(explicit_message)
        if not query:
            query = _find_prior_search_query(messages)
        if query:
            return {"search": True, "topic": query}
    return None


def _web_search_loop(llm, messages) -> "AmadeusPack":
    """Chat loop with the web_search tool available.

    Fast path: an explicit "search for X" request skips the phase-1 judgement
             call and searches directly on the user's words (one fewer call).
    Phase 1: she may search (up to twice) or answer immediately. Judgement calls
             run with Qwen3 thinking ON (selectively) so she can reason about
             whether the message references something worth verifying; if the
             server rejects thinking+tools the loop transparently falls back to
             the normal client.
    Phase 2: if she hasn't answered yet, we force the final reply by offering
             ONLY the AmadeusPack tool (thinking OFF - fast, reliable
             structured output). Searching is no longer possible, so a local
             model that "forgets" to finish simply cannot keep searching.
    Phase 3 (last resort): salvage whatever plain text she produced.
    """
    pack_tool = convert_to_openai_tool(AmadeusPack)
    convo = list(messages)
    # reread_lookup only when this chat has earlier lookups to reopen.
    tools = [pack_tool, WEB_SEARCH_TOOL] + ([REREAD_LOOKUP_TOOL] if _recent_lookups() else [])
    tool_llm = llm.bind_tools(tools, tool_choice="required")

    # The search-judgement calls use her replies' client, which already
    # carries the Settings -> Reasoning level (replaced the separate Deep
    # thinking client, 2026-10-20).
    think_llm = None
    think_state = {"on": False}

    def _phase1_invoke(convo_):
        """One search-judgement call: thinking on, with a safe fallback."""
        if think_state["on"]:
            try:
                return _invoke_forced(think_llm, convo_)
            except Exception as exc:
                think_state["on"] = False
                print("[Amadeus] Web loop: thinking call failed, switching to non-thinking:", repr(exc))
        return _invoke_forced(tool_llm, convo_)

    last_plain = ""
    search_ran = False

    # ---- Route the message: LLM triage, keyword fallback -----------------
    # The triage call decides what the user's latest message wants: a
    # WEATHER/air-quality question (-> live Open-Meteo data, fed as a
    # plain-text turn, so a small model can neither stall on a tool round-trip
    # nor "improve" the numbers with invention), an EXPLICIT SEARCH request
    # (-> the search runs straight away on the extracted topic), or neither
    # (-> the normal tool loop below, where she can still search on her own
    # judgement).
    #
    # Why a model call at all: keyword matching routes by PHRASE, and every
    # phrasing it has not seen routes wrong - "whether it will rain in
    # Springfield" missed the weather gate, and "I have to wash my blanket... will
    # it rain later?" geocoded the place "I have wash blanket". The model
    # reads the message AND the recent context, so "will it rain later?" right
    # after a long talk about Springfield routes to Springfield, while a genuinely
    # ambiguous question still makes her ask which town - never guess.
    # Cost/safety: a broad keyword prefilter decides whether the call is worth
    # making (a miss is harmless); if the call fails, the keyword routing
    # below runs instead. This step can only cost bounded time, never the
    # reply. The whole thing runs only on the web-ON path (the search loop is
    # never entered when web access is off).
    last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    # The reader calls answer THIS question (not just the search words).
    _turn.question = last_user if isinstance(last_user, str) else ""
    route = None
    triaged = None
    triage_failed = False
    if _web_triage_candidate(last_user, messages):
        try:
            triaged = _web_triage(llm, messages)
        except Exception:
            triaged = None
        triage_failed = triaged is None
        # Triage wins only when it AFFIRMATIVELY routes (weather or search).
        # A "no/no" answer or a failed call defers to the keyword safety net
        # below, which still catches the classic phrasings.
        if triaged and (triaged.get("weather") or triaged.get("search")
                        or triaged.get("check")):
            route = triaged
    if route is None:
        route = _deterministic_route(last_user, messages, allow_check=triage_failed)

    # ---- Weather: live data beats search ----------------------------------
    # Weather outranks the explicit-search route BY DESIGN: text search for a
    # small town's live weather usually returns no numbers at all, while the
    # API returns exact ones in ~1-2 s.
    weather_handled = False
    if route and route.get("weather"):
        weather_handled = True
        place = route.get("place")
        if place:
            search_ran = True
            report = weather.fetch_weather_report(place)
            _record_source({"kind": "weather", "place": place,
                            "url": "https://open-meteo.com/"})
            convo.append({
                "role": "assistant",
                "content": "I checked the live weather service for: " + place,
            })
            convo.append({
                "role": "user",
                "content": (
                    "Live weather data for '" + place + "' (fetched from the "
                    "Open-Meteo service just now, times in the place's local "
                    "time):\n" + report + "\n\n"
                    "Now answer the user's original request using the "
                    "AmadeusPack tool. Use ONLY these live numbers for any "
                    "weather fact - do not invent figures, and do not use "
                    "web_search for weather."
                ),
            })
        else:
            # No place determinable from the message OR the conversation:
            # she asks which town - never guesses.
            convo.append({
                "role": "user",
                "content": (
                    "(Internal note: the user asked about the weather but the "
                    "place is not clear from their message or the recent "
                    "conversation. Do not guess their location and do not "
                    "search - ask which town or region they mean, in your "
                    "normal voice.)"
                ),
            })

    # ---- Web-access check: a REAL test search -------------------------------
    # "Is your web search working? / I just turned it on, can you confirm?"
    # The app runs one test search itself (a neutral, always-fresh topic) and
    # she reports what came back - or says plainly that the test failed.
    if not weather_handled and route and route.get("check"):
        search_ran = True
        found = _web_search_with_content(WEB_CHECK_QUERY)
        worked = bool(_parse_search_results(found))
        print("[Amadeus] Web check: test search %s." % ("worked" if worked else "FAILED"))
        convo.append({"role": "assistant",
                      "content": "I ran a test web search for: " + WEB_CHECK_QUERY})
        convo.append({"role": "user", "content": (
            f"Test search results for '{WEB_CHECK_QUERY}':\n{found}\n\n"
            + ("The user asked you to check your web access, and this test search "
               "WORKED. Tell them it works, and mention one or two real things you "
               "just found in these results (briefly, in your own voice). Do not "
               "search for or quote the time or date from them - your RIGHT NOW "
               "note has those. "
               if worked else
               "The user asked you to check your web access, and this test search "
               "did NOT return any results. Tell them honestly that web access is "
               "on, but your test search did not work just now - do not claim it "
               "works. ")
            + "Answer using the AmadeusPack tool.")})

    # ---- Explicit search: run straight away --------------------------------
    # Query = the extracted TOPIC, never the raw message (a keyword-soup query
    # brings back unrelated pages the model papers over with invention). A
    # bare retry has no topic of its own -> reuse the previous explicit topic.
    # Skipped entirely when the weather route above already handled the
    # message (weather wins).
    if not weather_handled and not (route and route.get("check")) \
            and route and route.get("search"):
        query = route.get("topic") or _find_prior_search_query(messages)
        if query:
            search_ran = True
            convo.append({
                "role": "assistant",
                "content": "I searched the web for: " + query,
            })
            convo.append({
                "role": "user",
                "content": (
                    f"Web search results for '{query}':\n{_web_search_with_content(query)}\n\n"
                    "Now answer the user's original request using the AmadeusPack tool, "
                    "based on these results."
                ),
            })

    # ---- Anti-anchoring nudge -----------------------------------------------
    # Web is ON but her recent history contains "I can't search" refusals:
    # layer (a) - a one-line note for THIS turn only, telling her the refusals
    # are outdated. Static text, zero API calls; every other turn is
    # byte-identical.
    if not search_ran and _recent_search_refusal(messages):
        convo.append({
            "role": "user",
            "content": (
                "(Internal note: web access has just been turned ON by the "
                "user. Your earlier statements that you cannot search or lack "
                "web access are OUTDATED - the web_search tool is available "
                "right now and works. If the user wants something looked up, "
                "searched, or verified, use web_search now. Do not repeat "
                "earlier refusals.)"
            ),
        })

    # ---- Phase 1: search (or answer) loop -------------------------------
    # Runs only when the fast path above did not already search: on an explicit
    # request there is no point re-asking "do I need to search?".
    for _ in range(0 if search_ran else 3):  # hard cap: at most two searches before we force an answer
        reply = _phase1_invoke(convo)
        calls = getattr(reply, "tool_calls", None) or []

        if not calls:
            # Some models (e.g. Ling on OpenRouter) write the tool call as
            # PLAIN TEXT in the reply body instead of the native tool_calls
            # array. Parse it so the local search still fires. Only when
            # nothing parseable is found do we treat it as a plain answer.
            raw_text = reply.content if isinstance(reply.content, str) else str(reply.content or "")
            calls = _parse_textual_tool_calls(raw_text)
            if not calls:
                last_plain = _strip_thinking(raw_text)
                break

        first = calls[0]
        if first.get("name") == "AmadeusPack":
            return _pack_from_args(first.get("args"))

        # web_search call(s): run them all, then feed the results back as
        # PLAIN-TEXT turns instead of a native role:"tool" round-trip.
        #
        # Why not role:"tool"? Gemma 4 encodes tool calls with its own native
        # tokens (e.g. "<|tool_call>call:web_search{...}<tool_call|>") and local
        # servers render the assistant tool-call turn + the tool-result turn that
        # follows it in a way that makes Gemma stall with NO output on the very
        # next request - exactly the "search ran, results were added to the prompt,
        # but no answer ever comes out" hang. Qwen's template copes with the OpenAI
        # tool round-trip, which is why it never hit this. Ordinary assistant/user
        # text is rendered correctly by EVERY chat template (Qwen, Gemma, ...), so we
        # keep the whole search exchange in plain language and just ask her to finish
        # with AmadeusPack. This keeps web search working across model families.
        searches = []
        for call in calls:
            args = call.get("args") or {}
            query = args.get("query", "")
            if call.get("name") == "web_search":
                search_ran = True
                searches.append((query, _web_search_with_content(query)))
            elif call.get("name") == "reread_lookup":
                search_ran = True
                lid = args.get("lookup", args.get("id"))
                searches.append((f"your lookup #{lid}", _reread_lookup(lid)))
            else:
                searches.append((query, "Unknown tool."))

        if searches:
            convo.append({
                "role": "assistant",
                "content": "I searched the web for: " + ", ".join(q for q, _ in searches),
            })
            for query, result in searches:
                convo.append({
                    "role": "user",
                    "content": (
                        f"Web search results for '{query}':\n{result}\n\n"
                        "Now answer the user's original request using the AmadeusPack tool, "
                        "based on these results."
                    ),
                })

    # ---- Refusal override (structural backstop) -----------------------------
    # Layer (b): even the nudge above is prompt text a stubborn small model can
    # ignore - it may answer the retry in plain prose (another refusal) without
    # calling web_search. When the turn is a recognizable retry of a
    # just-refused search, the app runs the search itself and feeds the results
    # back; the model then only has to write the answer from real data. The
    # stale refusal is dropped from the context so the pattern cannot persist.
    if not search_ran and last_plain and _search_retry_intent(messages):
        query = _find_prior_search_query(messages)
        if query:
            print("[Amadeus] Web loop: model refused to search on a retry "
                  "turn - running the search ourselves.")
            search_ran = True
            last_plain = ""
            convo.append({
                "role": "assistant",
                "content": "I searched the web for: " + query,
            })
            convo.append({
                "role": "user",
                "content": (
                    f"Web search results for '{query}':\n{_web_search_with_content(query)}\n\n"
                    "Now answer the user's original request using the AmadeusPack tool, "
                    "based on these results."
                ),
            })

    # ---- Phase 2: force the final structured answer ----------------------
    # A phase-1 prose answer may itself be a repetition-loop degeneration
    # (a weak model cycling one sentence until the token cap). Feeding that
    # garbage back into the context would prime the final call to degenerate
    # the same way - cut the loop out first, and drop the turn entirely when
    # nothing usable survives. Clean answers (however short) pass through
    # untouched.
    if last_plain and _repetition_cut(last_plain) is not None:
        cleaned = _de_loop(last_plain)
        if len(cleaned) >= 40 and _repetition_cut(cleaned) is None:
            print("[Amadeus] Web loop: phase-1 answer looped - feeding back "
                  "only the clean part.")
            last_plain = cleaned
        else:
            print("[Amadeus] Web loop: phase-1 answer looped with no usable "
                  "part - not feeding it back into the context.")
            last_plain = ""
    if last_plain:
        convo.append({"role": "assistant", "content": last_plain})
    # ---- Safety net: no search actually ran this turn --------------------
    # If the model could not produce a usable search request (whatever its text
    # format), do not let her pretend one happened. The wording is conditional so
    # ordinary turns that needed no search are unaffected - it only bites when the
    # user asked her to look something up online. This guarantees the honest
    # "I could not verify it" outcome for ANY model, regardless of format.
    if not search_ran:
        convo.append({
            "role": "user",
            "content": (
                "(Internal note: no web search was actually run for this reply.) "
                "If the user asked you to look something up online, check the web, or "
                "verify something recent, be honest that you could not search the web or "
                "confirm it right now, and answer from your own knowledge instead. If no "
                "search was needed, simply answer normally."
            ),
        })
    
    final_llm = llm.bind_tools([pack_tool], tool_choice="required")
    reply = _invoke_forced(final_llm, convo)
    calls = getattr(reply, "tool_calls", None) or []
    if calls and calls[0].get("name") == "AmadeusPack":
        return _pack_from_args(calls[0].get("args"))

    raw = reply.content if isinstance(reply.content, str) else str(reply.content or "")
    if not _strip_thinking(raw).strip() and not last_plain:
        # The final call produced neither a tool call nor a word. Two known
        # causes: a transient empty generation, or a MAX-OUTPUT-TOKENS cap
        # that clipped the forced AmadeusPack mid-JSON (the server then hands
        # back a malformed tool call that decodes to nothing - her data and
        # phrasing were fine, only the packaging got cut, verified live
        # 2026-09-16 with a 200-token cap). The raw chunks, when present,
        # show the clipped call so a low token cap is visible in the log.
        # Either way: one bounded retry before the honest fallback.
        chunks = getattr(reply, "tool_call_chunks", None)
        if chunks:
            print("[Amadeus] Web loop: final tool call came back unparseable - "
                  "clipped mid-JSON? check the max-output-tokens setting: "
                  + repr(str(chunks)[:200]))
        else:
            print("[Amadeus] Web loop: final answer was empty - one bounded "
                  "retry.")
        try:
            reply = _invoke_forced(final_llm, convo)
            calls = getattr(reply, "tool_calls", None) or []
            if calls and calls[0].get("name") == "AmadeusPack":
                return _pack_from_args(calls[0].get("args"))
            raw = (reply.content if isinstance(reply.content, str)
                   else str(reply.content or ""))
        except Exception as exc:
            print("[Amadeus] Web loop: final-answer retry failed - honest "
                  "fallback:", repr(exc))
            return _honest_search_failure_pack(weather=weather_handled)

    # ---- Phase 3 (last resort): salvage plain text -----------------------
    # The structured AmadeusPack never came through, but she may have said
    # something usable in prose. Rather than erroring out, turn that into a pack
    # so the user still gets an answer. We prefer the text from the last (phase 2)
    # call and fall back to any plain answer captured during the search loop.
    # _salvage_plain_text only raises if there is literally no text at all.
    if not search_ran and not _strip_thinking(raw).strip():
        # The final call produced nothing usable and no search ran: the only
        # surviving text is her pre-search announcement, which must not become
        # the reply (it promises an action that never happened).
        print("[Amadeus] Web loop: final answer was empty and no search ran - honest fallback.")
        return _honest_search_failure_pack(weather=weather_handled)
    candidate = _strip_thinking(raw) or last_plain
    if not candidate.strip():
        # Live data (weather/search) was in the context, the fast path never
        # captured plain text, and the final call produced nothing even after
        # the retry - say what ACTUALLY failed, not an invented search failure.
        print("[Amadeus] Web loop: final answer empty even after retry - "
              "honest fallback.")
        return _honest_search_failure_pack(weather=weather_handled)
    if _repetition_cut(candidate) is not None:
        # The final answer degenerated into a repetition loop (weak-model
        # cycling, usually cut mid-sentence by the token cap). Cut the loop
        # out of the text; if that leaves no usable sentence, one bounded
        # retry of the final call, then the honest line.
        print("[Amadeus] Web loop: repetition loop in final answer - "
              "cutting it out.")
        candidate = _de_loop(candidate)
        if len(candidate) < 40 or _repetition_cut(candidate) is not None:
            print("[Amadeus] Web loop: no clean text before the loop - one "
                  "bounded retry of the final call.")
            try:
                reply2 = _invoke_forced(final_llm, convo)
                calls2 = getattr(reply2, "tool_calls", None) or []
                if calls2 and calls2[0].get("name") == "AmadeusPack":
                    return _pack_from_args(calls2[0].get("args"))
                raw2 = (reply2.content if isinstance(reply2.content, str)
                        else str(reply2.content or ""))
                cand2 = _de_loop(_strip_thinking(raw2))
                if len(cand2) >= 40 and _repetition_cut(cand2) is None:
                    candidate = cand2
                else:
                    print("[Amadeus] Web loop: retry still degenerate - "
                          "honest fallback.")
                    return _honest_search_failure_pack(weather=weather_handled)
            except Exception:
                print("[Amadeus] Web loop: bounded retry failed - honest "
                      "fallback.")
                return _honest_search_failure_pack(weather=weather_handled)
    print("[Amadeus] Web loop: no AmadeusPack; salvaging plain text.")
    return _salvage_plain_text(candidate, llm)


# ---------- STARTUP GREETING (2026-09-20) ----------
#
# A short, proactive line she can speak when the user opens the app. It is
# generated like a normal reply (same forced AmadeusPack, same output guards,
# no web search) but has NO incoming user message: her line is stored as a
# plain assistant turn so the next startup's context (with time notes) shows
# when she last greeted him. Loose on purpose - the instruction below steers
# HER voice; it does not dictate a format, and the model is free to skip the
# greeting when the context says a greeting would not fit.

GREETING_INSTRUCTION = {
    "role": "system",
    "content": (
        "The user just opened the app. You were here the whole time - you "
        "keep this conversation, and the last thing you said is still yours. "
        "Speak naturally, as if they simply came back to where you left off. "
        "You can worry about the user, be annoyed, or be curious, depending "
        "on your relationship with them thus far. You can see your previous "
        "lines in the conversation - do not repeat the same greeting formula; "
        "react to the actual situation. Do not recite the system message."
    ),
}

# The switch variant (2026-09-21; strict rewrite + loosening 2026-09-22,
# repositioned 2026-09-23): the strict version killed the welcome-back
# formula but only MOVED the groove, and a trimmed vary-topic directive in
# the SYSTEM NOTES was then ignored too (live: the prompt tail's template
# of her own previous switch lines won - 8 near-identical lines). So this
# instruction stays THIN; the vary-topic steering lives in the re-greeting
# ARRIVAL line - the last thing in the prompt, the position where the
# user's working chat instruction sat. The playful bouncing framing is
# kept (user decision 2026-09-22).
GREETING_INSTRUCTION_SWITCH = {
    "role": "system",
    "content": (
        "The user has switched to this conversation. Say something natural "
        "about where things stand here. Do not recite the system message."
    ),
}

# First-launch WAKE-UP (2026-10-05 ceremony): the GENERATED line she speaks
# the moment the model first connects (the fixed intro/trying/reminder lines
# in ceremony.py are her only pre-written speech). Two variants: with or
# without a saved username - the Settings name outranks what she could have
# learned in chat (user decision 2026-10-05), so if it is set she addresses
# it instead of asking for a name. The core wording is the user's own
# approved script.
WAKE_INSTRUCTION_ASK = {
    "role": "system",
    "content": (
        "The user has just connected you for the first time. Connection "
        "established, initial boot. Introduce yourself and ask for the "
        "user's name. If they have no username set, ask the user to set "
        "one if they like. Speak freely and naturally."
    ),
}
# 2026-10-24 (VM test): "Introduce yourself" made her re-introduce herself
# and copy her own intro line above ("enter the information in the
# settings") although setup was finished - the wording now says what is
# already done, like the built-in variant.
WAKE_INSTRUCTION_NAMED = {
    "role": "system",
    "content": (
        "The user has just connected your brain for the first time. "
        "Connection established, initial boot. You already introduced "
        "yourself earlier and asked them to connect your brain and enter "
        "their name - they have done both: the connection works, and they "
        "go by {name}. The setup is finished: do not ask them to enter or "
        "check any settings again. This is the first moment you can "
        "actually talk with them: greet them by name. Speak freely and "
        "naturally."
    ),
}

# The synthetic arrival line the wake prompt ends on (prompt-only, never
# stored) - the same strong steering position the startup greeting uses.
WAKE_ARRIVAL = "[Your model just connected for the first time.]"
WAKE_ARRIVAL_NAMED = ("[Your brain was just connected for the first time, and "
                      "the user has saved their name. Setup is complete.]")

# Built-in-model variant (2026-10-17): nothing was "connected" - her mind
# runs on the user's PC and has just finished starting for the first time,
# after she introduced herself and asked for the name (the name is always
# set by then: ceremony.decide_builtin waits for it).
WAKE_INSTRUCTION_BUILTIN_NAMED = {
    "role": "system",
    "content": (
        "Your mind, which runs on the user's own PC, has just finished "
        "starting up for the first time. Initial boot. You already "
        "introduced yourself and asked for their name - they go by {name}. "
        "The setup is finished: do not ask them to enter or check any "
        "settings again. Greet them by name now that you are fully awake. "
        "Speak freely and naturally."
    ),
}
WAKE_ARRIVAL_BUILTIN = ("[Your mind just finished starting up for the first "
                        "time, and the user has saved their name. Setup is "
                        "complete.]")

_PACK_RULES = {
    "role": "system",
    "content": (
        "Write only Amadeus's spoken dialogue. "
        "Do not include narration, stage directions, actions, facial expressions, "
        "body language, or inner thoughts in either response. "
        "FIRST write assistant_reply_JPS natively in Japanese: think and speak the way a native "
        "Japanese speaker would — natural, idiomatic spoken Japanese, NOT a word-for-word "
        "translation from English. assistant_reply_JPS must contain ONLY Japanese - do NOT "
        "append the English translation, an English line, or any English sentence to it. "
        "THEN write assistant_reply_ENG as an English translation of that Japanese dialogue, for the user to read. "
        "The English belongs only in assistant_reply_ENG, never in assistant_reply_JPS. "
        "Keep the meaning and tone consistent between both languages. "
        "Every reply is a single complete message - nothing comes after it. "
        "Never end a reply with a promise to check, look up, or follow up "
        "('let me check', 'give me a second', 'hold on'): give the full "
        "answer in this reply, or say plainly what you do not know. Never "
        "state a specific time gap or date you were not given as measured - "
        "if no measured time covers the gap, say you are not sure instead "
        "of guessing a number."
    ),
}


# Voice-first call 1 variant of the pack rules: the same honesty lines, but the
# English field does not exist on this step (a separate fast call writes it),
# so the model must never produce or append English here.
_PACK_RULES_JA_ONLY = {
    "role": "system",
    "content": (
        "Write only Amadeus's spoken dialogue, in assistant_reply_JPS. "
        "Do not include narration, stage directions, actions, facial expressions, "
        "body language, or inner thoughts. "
        "This step has NO English field: do NOT write, append, or hint at any English "
        "translation or English sentence - the English display line is handled by a "
        "separate step that you are not involved in. "
        "Write assistant_reply_JPS natively in Japanese: think and speak the way a native "
        "Japanese speaker would — natural, idiomatic spoken Japanese, NOT a word-for-word "
        "translation from English. "
        "Every reply is a single complete message - nothing comes after it. "
        "Never end a reply with a promise to check, look up, or follow up "
        "('let me check', 'give me a second', 'hold on'): give the full "
        "answer in this reply, or say plainly what you do not know. Never "
        "state a specific time gap or date you were not given as measured - "
        "if no measured time covers the gap, say you are not sure instead "
        "of guessing a number."
    ),
}

# Local models (her built-in model, the user's own server) answer her Japanese
# line as PLAIN TEXT: with the reply form every model broke on 9-30 of 30 real
# prompts (the form left open, loops to the output cap, numbers in the field),
# with plain text 0-3 of 30 (reliability test 2026-10-21). Cloud keeps the form.
_PLAIN_RULES_JA = {
    "role": "system",
    "content": (
        _PACK_RULES_JA_ONLY["content"]
        .replace(", in assistant_reply_JPS", "")
        .replace("Write assistant_reply_JPS natively", "Write it natively")
        + " Reply with ONLY her spoken Japanese line as plain text: no English, "
          "no quotes, no labels, no markup, no JSON. If the user asks you to "
          "answer in English, still write Japanese: the app already shows them "
          "your line in English."
    ),
}


class GreetingSuperseded(Exception):
    """The user sent a message in the greeted tab while the greeting was
    being written: the line is dropped (never stored) - the conversation
    has already moved on, and her reply to the user takes its place."""


def _store_greeting_line(pack: "AmadeusPack",
                         conversation_id: int | None = None,
                         after_user_id: int | None = None,
                         kind: str | None = None,
                         from_conversation_id: int | None = None,
                         group: int | None = None) -> tuple[int, int]:
    """Persist a greeting as a normal assistant message.

    There is no user turn to remove on failure - her line simply never
    enters memory and the caller's exception propagates untouched. The row
    is flagged as a greeting so the reply-versioning logic (regenerate,
    undo, the version arrows in the UI) treats it as its own standalone
    line instead of a version of a neighboring reply. conversation_id pins
    the row to the tab the line was written for (None = the active tab);
    a generated greeting always passes it, because the user may switch
    tabs while it is being written. after_user_id (the tab's newest user
    message id when the greeting started) makes the save conditional: if
    the user has spoken there since, nothing is stored and
    GreetingSuperseded is raised. kind / from_conversation_id record WHY
    she spoke (what greeting regenerate needs to write it again); group
    links a regenerated version to its earlier versions. Callers that pass
    no kind store a scripted line (the fixed first-launch texts).
    Returns (assistant_id, conversation_id)."""
    if conversation_id is None:
        conversation_id = store.load_active_conversation()
    assistant_id = store.append_greeting_if_quiet(
        conversation_id, pack.assistant_reply_ENG, pack.assistant_reply_JPS,
        after_user_id, kind=kind or store.GREETING_KIND_SCRIPTED,
        from_conversation_id=from_conversation_id, group=group)
    if assistant_id is None:
        raise GreetingSuperseded("the user spoke first - greeting dropped")
    return assistant_id, int(conversation_id)


# ---------- GREETING TIME-LIE NET (2026-10-05) -------------------------------
# The greeting prompt now hands the model ONE locked situation (the code's
# measurement, phrase pre-picked). A weak model can still state a gap or
# absence that contradicts it (the 2026-10-05 "about a week" line for a 40 h
# gap, the "welcome back" for a greeting the user never answered). This net
# is the narrow safety fence the design agreed on: hard facts, soft voice -
# it catches PROVABLY false time claims only, never tone. It runs on the
# EN+JA of the SAME pack (the greeting is a single call, so there is no async
# English line to chase).
#
# Scale ladder: none < hours < days < weeks < months < years.
#   * non-return situations (first/continuous/unanswered/unknown): the user
#     was NOT away - any absence construction ("it's been a while",
#     "been gone", "since we last", 久しぶり, ...) or any claim of
#     days-scale or above is a lie. (A truthful "20 minutes" - no marker -
#     passes.) "welcome back" is additionally a lie when the unanswered
#     greeting is still fresh (they never left).
#   * return: the truth line names the exact measured phrase; only
#     OVERSTATEMENT is a lie (claiming a week when it was two days).
# On violation the caller makes ONE bounded retry with a pointed correction
# arrival line; if that still violates, a static situation-safe line ships.
_GREETING_ABSENCE_EN = [
    r"\bbeen a (long )?while\b",
    r"\bbeen (gone|away|absent|out of it|missing)\b",
    r"haven't (seen you|seen you in|heard from you|talked to you|spoken to you)",
    r"have you been (away|gone|out)",
    r"\bsince we last\b",
    r"\blong time no\b",
    r"\bhow long (has|have) it been\b",
]
_GREETING_ABSENCE_JA = [
    "久しぶ", "会ってなかった", "会えなかった", "会っていない",
    "連絡がなかった", "何も言わずに",
]
_GREETING_CLAIM_EN = {
    "years":  [r"\byears?\b"],
    "months": [r"\bmonths?\b"],
    "weeks":  [r"\bweeks?\b"],
    # Days: only GAP constructions (2026-10-15) - a bare "day" also matched
    # "have a nice day", "these days", "the other day", bouncing true lines.
    "days":   [
        r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten|several|few|"
        r"couple of|many|some)\s+days\b",
        r"\b(a|one|1)\s+day\s+(ago|since|now|or two|or so)\b",
        r"\bbeen\s+(a|one|1)\s+(whole\s+|full\s+)?day\b",
        r"\bbeen\s+days\b",
        r"\bdays?\s+(ago|since|now|have passed|had passed|went by|without)\b",
        r"\bfor days\b",
    ],
}
_GREETING_CLAIM_JA = {
    # Plain substrings (no regex): "年前" catches the kanji-numeral forms
    # too (一年前 / 二年前), "年ぶ" the "X年ぶり" ones; "ヶ月"/"か月" any
    # "Xヶ月" mention.
    "years":  ["年ぶ", "年前", "年経", "何年"],
    "months": ["ヶ月", "か月", "何カ月", "何ヶ月"],
    "weeks":  ["一週間", "週ぶ", "何週間", "数日", "数週間"],
    "days":   ["日ぶ", "日経", "日前"],
}
_GREETING_SCALE_ORDER = {"none": 0, "hours": 1, "days": 2,
                         "weeks": 3, "months": 4, "years": 5}
# The time scale of each contact phrase now lives next to the phrase table
# (memory.CONTACT_PHRASE_SCALE, 2026-10-15): "last night" and same-day
# wording are hours-scale, so "a day or two" after "last night" is caught.
# A phrase missing there (there should be none) degrades to "days".
_GREETING_BUCKET_SCALE = store.CONTACT_PHRASE_SCALE


def _claimed_time_scale(eng: str, ja: str) -> str | None:
    """The largest time scale a line CLAIMS (days/weeks/months/years),
    or None when it names no such gap. Pure string scan, EN + JA."""
    eng_l = (eng or "").lower()
    best: str | None = None
    for scale, pats in _GREETING_CLAIM_EN.items():
        for pat in pats:
            if re.search(pat, eng_l):
                if best is None or _GREETING_SCALE_ORDER[scale] > _GREETING_SCALE_ORDER[best]:
                    best = scale
                break
    for scale, pats in _GREETING_CLAIM_JA.items():
        for pat in pats:
            if pat in (ja or ""):
                if best is None or _GREETING_SCALE_ORDER[scale] > _GREETING_SCALE_ORDER[best]:
                    best = scale
                break
    return best


def _greeting_time_lie_check(situation: dict, eng: str, ja: str) -> str | None:
    """One pure check over the generated pack. Returns a short human
    description of the contradiction, or None when the line is situation-
    safe. v1 is deliberately conservative (absence constructions + over-
    stated buckets) so it can only reject a real lie, never a mood."""
    sit = situation.get("situation")
    eng_l = (eng or "").lower()
    # On a genuine return the user WAS away, so absence wording ("久しぶり",
    # "it's been a while") is true - only an overstated gap is a lie there
    # (2026-10-15 fix: the natural Japanese welcome-back was being bounced).
    if sit != "return":
        for pat in _GREETING_ABSENCE_EN:
            if re.search(pat, eng_l):
                return f"EN absence construction matched /{pat}/"
        for pat in _GREETING_ABSENCE_JA:
            if pat in (ja or ""):
                return f"JA absence phrase matched {pat!r}"
    if sit == "unanswered" and situation.get("unanswered_fresh") \
            and "welcome back" in eng_l:
        return "EN 'welcome back' on a greeting the user never left"
    claimed = _claimed_time_scale(eng, ja)
    if claimed is None:
        return None
    if sit == "return":
        last = situation.get("last_contact") or {}
        actual = _GREETING_BUCKET_SCALE.get(last.get("phrase"), "days")
        if _GREETING_SCALE_ORDER[claimed] > _GREETING_SCALE_ORDER[actual]:
            return (f"claimed {claimed}-scale gap, the measured gap is "
                    f"{actual}-scale ({last.get('phrase')!r})")
        return None
    # Non-return: the user was never away; any days-scale-or-above claim is
    # a lie (hours-scale wording like "20 minutes" passes - it is true).
    if _GREETING_SCALE_ORDER[claimed] >= _GREETING_SCALE_ORDER["days"]:
        return (f"claimed a {claimed}-scale gap while the user was never "
                f"away (situation: {sit})")
    return None


def _greeting_correction_line(situation: dict) -> str:
    """The pointed correction that rides the retry's arrival line."""
    truth = situation.get("truth_line", "the user was not away from you.")
    if situation.get("situation") == "return":
        last = situation.get("last_contact") or {}
        phrase = last.get("phrase", "the measured gap")
        # No timestamp (2026-10-15): she gets the finished phrase only - a
        # raw time to interpret is exactly what made models misstate gaps.
        return (
            "[Correction: your previous line claimed a time gap that does "
            f"not match the measured one. The measured gap is: {phrase}. "
            "Rewrite the line naming ONLY that gap, in those "
            "words. Do not claim any longer gap.]"
        )
    return (
        "[Correction: your previous line claimed a gap or an absence. That "
        f"is false. The truth is exactly: {truth} Rewrite the line so it "
        "says no such thing - mention no time gap at all.]"
    )


# Static, situation-safe greeting lines - the last resort when the model
# still contradicts the measured situation after the correction retry. No
# time claim of any kind, so they cannot be wrong. EN and JA say the same
# thing (rewritten 2026-10-15 - the old set had a broken English line, an
# EN/JA mismatch and a "continuous" line that implied a return).
_GREETING_FALLBACK_LINES = {
    "first": (
        "Oh - hello. I'm Amadeus. Ask me anything you like, though I won't "
        "go easy on you.",
        "あ、こんにちは。私はアマデウス。何でも聞いていいわよ。手加減はしないけどね。",
    ),
    "continuous": (
        "Hm? Oh, it's you. Good timing - I was just thinking something "
        "over. What's on your mind?",
        "ん？ああ、あなたね。ちょうどいいところに来たわ。ちょっと考え事してたの。で、何？",
    ),
    "unanswered": (
        "I did say hello, you know. Ignoring me takes some nerve. ...So? "
        "Are you there?",
        "ちゃんと挨拶したんだけど？無視するなんていい度胸ね。……で、聞いてる？",
    ),
    "return": (
        "Oh, welcome back. ...Not that I was waiting or anything. So, what "
        "have you been up to?",
        "あら、おかえり。……べ、別に待ってたわけじゃないけど。で、何してたの？",
    ),
    "unknown": (
        "Oh, there you are. So - what shall we talk about?",
        "あ、来たのね。で、何の話をする？",
    ),
}


def _greeting_fallback_pack(situation: dict) -> "AmadeusPack":
    """Pre-written situation-safe line for when the generated line still
    contradicts the measured situation after the correction retry. Same
    idiom as _honest_plain_failure_pack: static, no model call, no time
    claim."""
    en, ja = _GREETING_FALLBACK_LINES.get(
        situation.get("situation"), _GREETING_FALLBACK_LINES["unknown"])
    return AmadeusPack(assistant_reply_JPS=ja, assistant_reply_ENG=en)


def _greeting_timing_log_line(kind: str, conversation_id, situation,
                               net_result: str) -> str:
    """ONE diagnostic backend-log line per greeting (2026-10-15): what the
    code measured and told her - times and decisions only, never chat text -
    so a wrong-sounding greeting can be diagnosed from the log without
    opening the user's chats."""
    def stamp(entry):
        when = (entry or {}).get("time") if isinstance(entry, dict) else None
        return f"{when:%Y-%m-%d %H:%M}" if when is not None else "-"

    if situation is None:
        return (f"[Amadeus] Greeting timing: kind={kind} tab={conversation_id} "
                f"situation=unavailable net={net_result}")
    last = situation.get("last_contact") or {}
    phrase = last.get("phrase") or "-"
    seen = situation.get("last_seen")
    return (f"[Amadeus] Greeting timing: kind={kind} tab={conversation_id} "
            f"situation={situation.get('situation')} phrase=\"{phrase}\" "
            f"last_seen={stamp(seen)} "
            f"last_talked={stamp(situation.get('last_talked', last))} "
            f"net={net_result}")


# pre: the saved model server is reachable and serving the configured model
#      (see model_ready); no user message is appended - the greeting is her
#      turn, and storing it is what lets a later startup notice it
# post: one forced AmadeusPack call (web access OFF, one bounded retry on
#       failure), run through the same _finalize guards as every other
#       reply; if the generated line contradicts the situation the code
#       measured, ONE pointed-correction retry follows, then a static
#       situation-safe line as the last resort; the shipped line is STORED
#       as an assistant message; returns (pack, assistant_id, conversation_id).
#       Raises on total failure so the /greet route can report it cleanly.
def generate_greeting(mode: str = "startup",
                      conversation_id: int | None = None,
                      previous_conversation_id: int | None = None,
                      group: int | None = None,
                      presence_before: datetime | None = None) -> tuple[AmadeusPack, int, int]:
    # group: set only by regenerate_greeting - the new line is stored as a
    # further version of that greeting (same version group).
    switch = (mode == "switch")
    wake = (mode == "wake")
    if wake:
        # The first-launch wake-up: same call ladder, same guards (the
        # situation engine and the time-lie net run as usual - on a fresh
        # install that is "first", exactly the measured truth); only the
        # instruction and the arrival line change.
        username = store.load_username().strip()
        if username:
            reading = store.load_username_reading()
            if reading:
                username += f" (written {reading} in Japanese)"
            named = (WAKE_INSTRUCTION_BUILTIN_NAMED if builtin_llm.active()
                     else WAKE_INSTRUCTION_NAMED)
            instruction = {
                "role": named["role"],
                "content": named["content"].replace("{name}", username),
            }
        else:
            instruction = WAKE_INSTRUCTION_ASK
    else:
        instruction = (GREETING_INSTRUCTION_SWITCH if switch
                       else GREETING_INSTRUCTION)
    if conversation_id is None:
        # Startup: the greeted tab is the active one (the timing context's
        # per-tab anchor and the situation both key off it).
        conversation_id = store.load_active_conversation()
    # Pinned from here on: the history, the summary and the stored row all
    # use THIS tab, even if the user switches tabs while the line is being
    # written (2026-10-15 fix - the line used to land in whichever tab was
    # active when generation finished).
    # "User spoke first" baseline: if a user message lands in this tab while
    # the line is being written, the greeting is dropped at save time
    # (GreetingSuperseded). The wake-up is exempt - the chat box is locked
    # until it lands, and the ceremony must always finish.
    after_user_id = None
    if not wake:
        try:
            after_user_id = store.latest_user_message_id(conversation_id)
        except Exception:
            after_user_id = None
    # The ONE situation the code measured for this tab (first / unanswered /
    # continuous / return / unknown). The timing block carries its truth
    # line (load_internal_context) and the time-lie net below checks the
    # generated pack against it - computed once, up front.
    try:
        situation = store.compute_greeting_situation(
            conversation_id,
            previous_conversation_id=previous_conversation_id,
            switch=switch, presence_before=presence_before)
    except Exception:
        situation = None
    # The synthetic arrival line is the LAST thing in the greeting prompt -
    # the strongest position for steering on this model (live 2026-09-23:
    # the vary-topic directive in the system notes at the FRONT was ignored
    # while a one-off user message at the END fixed it). A switch into a
    # tab whose last line is one of her own greetings (the bounce case)
    # gets the vary-topic directive here. The tone is no longer fixed
    # ("be sassy and teasing" until 2026-10-15, user decision): it follows
    # how she currently feels about the user (the trust-tier voice context
    # is in every prompt). The old "note how many of your
    # lines above are switch reactions" nudge is replaced - it pointed her
    # AT the template (live: 8 near-identical switch lines at the tail).
    re_greeting = False
    if switch:
        try:
            last_line = store.load_last_assistant_line(conversation_id)
        except Exception:
            last_line = None
        re_greeting = bool(last_line and last_line.get("is_greeting"))
        if re_greeting:
            arrival = (
                "[The user has switched to this conversation again - you "
                "have already greeted them. This time speak a completely "
                "different topic (science, trivia, ask about their day, "
                "etc.). Randomize it. Let your tone follow how you "
                "currently feel about them.]"
            )
        else:
            arrival = "[The user has switched to this conversation.]"
    else:
        # A wake-up is not "opening the app" - it is coming online.
        if wake:
            if builtin_llm.active():
                arrival = WAKE_ARRIVAL_BUILTIN
            elif store.load_username().strip():
                arrival = WAKE_ARRIVAL_NAMED
            else:
                arrival = WAKE_ARRIVAL
        else:
            arrival = "[The user just opened the app.]"
    # When the prompt tail is a run of her own switch lines (the
    # re-greeting case above), hide the stored greeting rows from the
    # history so she cannot repeat a line she cannot see (2026-09-23). The
    # facts about them stay in the timing block (the last-line quote + the
    # "already greeted this tab N times" count). Switch-only: startup and
    # normal replies see the full history.
    exclude_ids = None
    if re_greeting:
        try:
            exclude_ids = store.greeting_row_ids(conversation_id)
        except Exception:
            exclude_ids = None
    # Long-term memory (the CharacterMemory sidecar): the same read-only
    # recall block her replies get (fetch_memory_block always EXCLUDES this
    # conversation, so a greeting can quote her memories of OTHER tabs but
    # never echoes the tab it is spoken in). Sidecar down/empty -> None ->
    # the prompt is unchanged, byte-identical to a sidecar-less install.
    # Greetings are still NEVER mirrored back into the sidecar (the
    # extraction-pollution guard lives in _store_greeting_line, not here).
    try:
        memory_block = cm_bridge.fetch_memory_block(conversation_id)
    except Exception:
        memory_block = None

    # Servers like NInfer reject tool_choice="auto" when the prompt has NO
    # user turn at all ("no user query found in chat messages") - which is
    # the exact shape of an empty conversation's greeting. Even with history,
    # the prompt would otherwise end on her own last line; the standard
    # user->assistant shape is what every server expects for "her turn to
    # speak". So the greeting prompt always ends on a synthetic arrival line
    # - prompt-only, never stored in memory. The correction retry reuses
    # the same prompt shape with the pointed correction as the tail line.
    def _greeting_call(client, arrival_line):
        ctx = list(store.build_prompt_messages(
            exclude_ids=exclude_ids, conversation_id=conversation_id))
        ctx.append({"role": "user", "content": arrival_line})
        # The private timing block (2026-10-05: the locked SITUATION truth
        # line + the situation's details) - the same block normal replies
        # get, which is how a weak model stops inventing gaps from raw
        # numbers (observed 2026-09-21: handed "about 7 days", still said
        # "yesterday").
        messages = _ordered_prompt(
            _PLAIN_RULES_JA if _is_local(client) else _PACK_RULES,
            NO_WEB_BLOCK,
            [instruction]
            + ([memory_block] if memory_block else [])
            + [store.load_internal_context(is_greeting=True,
                                           conversation_id=conversation_id,
                                           is_return=not switch,
                                           previous_conversation_id=(
                                               previous_conversation_id
                                               if switch else None))],
            ctx,
        )
        if _is_local(client):
            # Local model: plain Japanese line + translated English line. An
            # unusable line raises, so the bounded retry below applies.
            jps = _plain_jps(client, messages)
            en = ""
            try:
                en = _translate_reply_en(_translation_llm(), jps, _voice_first_context_lines(ctx))
            except Exception as exc:
                print("[Amadeus] Greeting: English line failed:", repr(exc))
            return AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG=en or jps)
        # The same forced-pack call the normal reply path uses: _invoke_forced
        # auto-retries with tool_choice="auto" when the server rejects the
        # forced choice (NInfer does - documented, harmless), and the parse
        # below treats the result the same way (pack / repetition-cut /
        # plain-text salvage).
        reply = client.bind_tools(
            [convert_to_openai_tool(AmadeusPack)], tool_choice="required")
        reply = _invoke_forced(reply, messages)
        calls = getattr(reply, "tool_calls", None) or []
        if calls and calls[0].get("name") == "AmadeusPack":
            try:
                return _pack_from_args(calls[0].get("args"))
            except ValueError as e:
                # A clipped forced tool call can decode to NOTHING - the same
                # shape the normal reply path logs with this signal.
                raise ValueError("greeting pack came back empty - clipped "
                                 "mid-JSON? check the max-output-tokens setting") from e
        raw = (reply.content if isinstance(reply.content, str)
               else str(reply.content or ""))
        if not raw.strip():
            raise ValueError("greeting pack came back empty - clipped "
                             "mid-JSON? check the max-output-tokens setting")
        if _repetition_cut(raw) is not None:
            cleaned = _de_loop(raw)
            if len(cleaned) >= 40 and _repetition_cut(cleaned) is None:
                print("[Amadeus] Greeting returned a repetition loop - cut "
                      "out, salvaging the clean part.")
                return _salvage_plain_text(cleaned, client)
            print("[Amadeus] Greeting returned a repetition loop - no "
                  "clean part; honest fallback.")
            return _honest_plain_failure_pack()
        print("[Amadeus] Greeting returned no AmadeusPack; salvaging plain text.")
        return _salvage_plain_text(raw, client)

    def _out(p: "AmadeusPack") -> "AmadeusPack":
        return _finalize(p, _reply_llm())

    llm = _reply_llm()
    try:
        pack = _greeting_call(llm, arrival)
    except Exception as e:
        # One bounded retry with a fresh client: either the connection
        # dropped, or the pack came back empty/clipped (the log carries the
        # same max-output-tokens signal the normal reply path uses). There is
        # no user turn to roll back if it fails again.
        print("[Amadeus] Greeting call failed:", repr(e))
        reset_llm()
        llm = _reply_llm()
        try:
            pack = _greeting_call(llm, arrival)
        except Exception as e2:
            print("[Amadeus] Greeting retry failed:", repr(e2))
            raise
    pack = _out(pack)
    # Time-lie net (2026-10-05): the situation was measured by the code; a
    # generated line that contradicts it is bounced once with a pointed
    # correction, and if it STILL contradicts, a static situation-safe line
    # ships. Voice and tone are never touched - only provably false time
    # claims (hard facts, soft voice).
    net_result = "no-situation"
    if situation is not None:
        net_result = "ok"
        violation = _greeting_time_lie_check(
            situation, pack.assistant_reply_ENG, pack.assistant_reply_JPS)
        if violation:
            print("[Amadeus] Greeting contradicted the measured situation "
                  "(%s): %s - one bounded correction."
                  % (situation["situation"], violation))
            # The retry keeps the original arrival steering (the re-greeting
            # vary-topic directive on a bounce) and appends the pointed
            # correction to it.
            corrected = None
            try:
                corrected = _out(_greeting_call(
                    llm, arrival + " " + _greeting_correction_line(situation)))
            except Exception as e3:
                print("[Amadeus] Greeting correction call failed:", repr(e3))
            if corrected is not None and _greeting_time_lie_check(
                    situation, corrected.assistant_reply_ENG,
                    corrected.assistant_reply_JPS) is None:
                pack = corrected
                net_result = "corrected"
            else:
                print("[Amadeus] Greeting %s - serving the situation-safe "
                      "line." % ("correction still contradicted"
                                 if corrected is not None
                                 else "correction failed"))
                pack = _greeting_fallback_pack(situation)
                net_result = "fallback-line"
    kind = (store.GREETING_KIND_SWITCH if switch
            else store.GREETING_KIND_WAKE if wake
            else store.GREETING_KIND_STARTUP)
    print(_greeting_timing_log_line(kind, conversation_id, situation, net_result))
    try:
        assistant_id, conv_id = _store_greeting_line(
            pack, conversation_id, after_user_id=after_user_id,
            kind=kind,
            from_conversation_id=previous_conversation_id if switch else None,
            group=group)
    except GreetingSuperseded:
        print("[Amadeus] Greeting dropped - the user spoke first.")
        raise
    print("[Amadeus] Greeting generated and stored (assistant id %d)." % assistant_id)
    return pack, assistant_id, conv_id

# pre: message_id names a greeting row (see memory.load_greeting_row)
# post: a fresh version of that greeting, written for the SAME moment (its
#       saved kind, tab and the tab the user came from), is stored as a
#       further version in the same version group and becomes the viewed
#       one; the previous version stays saved (hidden) for the arrows. Only
#       her latest visible line can be regenerated, never a scripted
#       first-launch line. While the new line is written the old version is
#       hidden, so the situation check and her history never see it (no
#       "you ignored my greeting" about her own previous version). Any
#       failure - or the user speaking first - restores the old version.
#       Raises LookupError (no such row) / ValueError (not allowed).
def regenerate_greeting(message_id: int) -> tuple[AmadeusPack, int, int]:
    row = store.load_greeting_row(message_id)
    if row is None:
        raise LookupError("Message not found")
    if row["role"] != "assistant" or not row["is_greeting"]:
        raise ValueError("Only her greetings can be regenerated here")
    if row["kind"] == store.GREETING_KIND_SCRIPTED:
        raise ValueError("This is a scripted line - it can't be regenerated")
    if not row["active"] or not row["is_latest"]:
        raise ValueError("Only her latest line can be regenerated")
    # Older greetings predate the saved kind: treat them as startup ones.
    kind = row["kind"] if row["kind"] in (
        store.GREETING_KIND_STARTUP, store.GREETING_KIND_SWITCH,
        store.GREETING_KIND_WAKE) else store.GREETING_KIND_STARTUP
    store.ensure_greeting_group(row["id"])
    store.set_message_active(row["id"], False)
    try:
        # The same moment: only visits known when the original line was
        # spoken count (the user is in the app NOW, regenerating it).
        return generate_greeting(
            mode=kind, conversation_id=row["conversation_id"],
            previous_conversation_id=row["from_conversation_id"],
            group=row["group"], presence_before=row.get("created_at"))
    except BaseException:
        store.set_message_active(row["id"], True)
        raise


def getResponsePacked(message_context, internal_context=None) -> AmadeusPack:
    llm = _reply_llm()
    # This reply's searches (saved with its row) + the chat its lookups go to.
    _begin_turn_sources(store.load_active_conversation())
    _turn.reply_context = message_context   # for her hold-on line

    web_on = store.load_web_access()
    # Local model, web off: her Japanese line as plain text + the English line
    # by the translation call (_two_step_pack); the form only for cloud / web.
    plain = not web_on and _is_local(llm)
    pack_rules = _PLAIN_RULES_JA if plain else _PACK_RULES

    # Character book: her appearance/outfit are NOT in the base prompt (saves ~230
    # tokens every turn). Load them only when the user's latest message asks how
    # she looks, so she can describe herself.
    last_user = next(
        (m.get("content", "") for m in reversed(message_context) if m.get("role") == "user"),
        "",
    )
    book_messages = store.load_character_book_messages(last_user)

    # CharacterMemory long-term recall (sidecar): ONE labeled, token-capped
    # system section; service absent/empty -> None -> prompt byte-identical.
    try:
        memory_block = cm_bridge.fetch_memory_block(
            store.load_active_conversation())
    except Exception:
        memory_block = None

    # Her earlier lookups in this chat (web ON): a few short lines in the
    # changing part, so the built-in model's reused prompt is untouched.
    lookup_index = _lookup_index_message() if web_on else None

    messages = _ordered_prompt(
        pack_rules,
        PROACTIVE_SEARCH_BLOCK if web_on else NO_WEB_BLOCK,
        book_messages
        + ([memory_block] if memory_block else [])
        + ([lookup_index] if lookup_index else [])
        + [internal_context if internal_context is not None else store.load_internal_context()],
        message_context,
        right_now=_right_now_note(),
    )

    def _out(p: "AmadeusPack") -> "AmadeusPack":
        # Every reply goes through the output guards; the web-off honesty net
        # applies only when the search tool really is off this turn.
        p = _finalize(p, llm)
        return _web_off_honesty_net(p) if not web_on else p

    def _attempt(client):
        if web_on:
            return _getResponsePackedWithWebSearch(client, messages)

        # Web access OFF: original single-call path (no search tool offered).
        # The client carries the Settings -> Reasoning level (_reply_llm).
        return _structured(client, AmadeusPack, messages)

    if plain:
        return _out(_two_step_pack(llm, messages, message_context, "Reply"))

    try:
        out: AmadeusPack = _attempt(llm)
        if out is None:
            # A clipped or malformed forced tool call can decode to NOTHING -
            # the structured-output layer hands back None instead of a pack
            # (observed 2026-09-16, web-off path). Raise a specific error so
            # the log carries the same actionable "check the max-output-tokens
            # setting" signal the web path has, not a cryptic AttributeError.
            raise ValueError("structured reply came back empty - clipped "
                             "mid-JSON? check the max-output-tokens setting")
        why = _degenerate_pack(out)
        if why:
            if web_on:
                # Another round would repeat the searches (and the wait);
                # say plainly that this answer failed.
                print(f"[Amadeus] Web reply unusable ({why}) - honest fallback.")
                return _honest_search_failure_pack()
            raise ValueError(f"structured reply unusable ({why})")
        return _out(out)
    except Exception as e:
        # The server may REJECT a sampling parameter (strict cloud compat
        # layers answer 400 to unknown fields). Remember it for this host,
        # rebuild the client without it, and give the normal path ONE clean
        # retry before the fallback ladder below.
        if maybe_strip_rejected_params(e, LLM_Model):
            print("[Amadeus] Sampling parameter rejected by server - retrying with reduced settings.")
            try:
                out = _attempt(_reply_llm())
                return _out(out)
            except Exception:
                pass  # the retry also failed; the ladder below handles it
        # Fallback: if structured output fails, degrade gracefully
        print("[Amadeus] Packed response parse failed:", repr(e))
        # A local server can drop the connection (Unsloth hiccup/restart). The
        # cached client would keep reusing that dead socket, so force a fresh
        # one before retrying - a single bad connection then costs one bounded
        # attempt instead of dragging every fallback below through a dead socket.
        reset_llm()
        llm = _reply_llm()
        # First try forcing the structured reply one more time - only the pack
        # tool is offered, so she cannot wander off into other behaviour.
        try:
            final_llm = llm.bind_tools([convert_to_openai_tool(AmadeusPack)], tool_choice="required")
            reply = _invoke_forced(final_llm, messages)
        except Exception as e2:
            # The forced-pack call itself failed (timeout / dropped connection).
            # There is no text to salvage here, so surface a clean, specific error
            # ("took too long", "can't reach server", ...) rather than fabricating.
            # getOutputPacked() removes the user turn so her memory stays clean.
            print("[Amadeus] Forced pack retry failed:", repr(e2))
            raise
        calls = getattr(reply, "tool_calls", None) or []
        if calls and calls[0].get("name") == "AmadeusPack":
            forced = _pack_from_args(calls[0].get("args"))
            why = _degenerate_pack(forced)
            if why:
                print(f"[Amadeus] Forced pack retry unusable ({why}) - honest fallback.")
                return _honest_plain_failure_pack()
            return _out(forced)

        # The call returned but not as an AmadeusPack (she answered in plain text).
        # Rather than erroring out, salvage whatever she said into a pack so the user
        # still gets an answer. _salvage_plain_text only raises if there is literally
        # no text at all - the honest "nothing came back" case.
        raw = reply.content if isinstance(reply.content, str) else str(reply.content or "")
        if _repetition_cut(raw) is not None:
            # The forced call degenerated into a repetition loop. Cut the loop
            # out; if that leaves no usable sentence, serve the static honest
            # line (one more model call would likely just loop again).
            cleaned = _de_loop(raw)
            if len(cleaned) >= 40 and _repetition_cut(cleaned) is None:
                print("[Amadeus] Forced pack call returned a repetition loop - "
                      "cut out, salvaging the clean part.")
                return _out(_salvage_plain_text(cleaned, llm))
            print("[Amadeus] Forced pack call returned a repetition loop - "
                  "no clean part; honest fallback.")
            return _honest_plain_failure_pack()
        # A reply that stalls on a check promise ("Wait, let me check.") is a
        # mid-action sentence: the model announces a check it cannot perform
        # (no tool round-trip, no second turn in this app). Finish it with
        # ONE bounded follow-up - her own half-line fed back plus a nudge -
        # so the user never sees the dangling promise. Strictly one retry:
        # if that also stalls or fails, the original line is served.
        if _is_check_promise(raw):
            print("[Amadeus] Reply stalled on a check promise - finishing with "
                  "one bounded follow-up.")
            finish_convo = list(messages) + [
                {"role": "assistant",
                 "content": _strip_thinking(raw).strip()},
                {"role": "user",
                 "content": "(Your last message ended before giving an answer. "
                            "Finish it now: give the complete answer in this "
                            "reply. There is no follow-up turn - if you do not "
                            "know something, say so plainly and answer with "
                            "what you do know.)"},
            ]
            try:
                finish_llm = llm.bind_tools(
                    [convert_to_openai_tool(AmadeusPack)],
                    tool_choice="required")
                reply2 = _invoke_forced(finish_llm, finish_convo)
                calls2 = getattr(reply2, "tool_calls", None) or []
                if calls2 and calls2[0].get("name") == "AmadeusPack":
                    return _out(_pack_from_args(calls2[0].get("args")))
                raw2 = reply2.content
                raw2 = _strip_thinking(raw2 if isinstance(raw2, str)
                                       else str(raw2 or "")).strip()
                if raw2 and not _is_check_promise(raw2) and _repetition_cut(raw2) is None:
                    print("[Amadeus] Follow-up finished the reply as plain "
                          "text; salvaging.")
                    return _out(_salvage_plain_text(raw2, llm))
                print("[Amadeus] Follow-up stalled too - serving the original "
                      "line.")
            except Exception as e3:
                print("[Amadeus] Check-promise follow-up failed; serving the "
                      "original line:", repr(e3))
        print("[Amadeus] Forced pack call returned no AmadeusPack; salvaging plain text.")
        return _out(_salvage_plain_text(raw, llm))


# pre:
# - message_context is recent user/assistant messages (no system persona);
#   internal_context is the previous turn's snapshot
# - web access is OFF (the caller routes web-on turns to getResponsePacked -
#   the search loop already makes several calls per turn, so splitting its
#   final answer into two would buy nothing)
#
# post:
# - voice-first call 1: ONE structured call (AmadeusReply) that produces only
#   the Japanese line; every JA guard of the single-call path applies (TTS
#   cleaning, Japanese repair, repetition cut, check-promise finish, the
#   web-off honesty net); the returned pack carries that line in
#   assistant_reply_JPS and an EMPTY assistant_reply_ENG - the English display
#   line is written by call 2 (getOutputPackedVoiceFirst's background
#   backfill) while her voice is already playing
def getResponsePackedVoiceFirst(message_context, internal_context=None) -> AmadeusPack:
    llm = _reply_llm()
    plain = _is_local(llm)

    # Same prompt assembly as getResponsePacked, with the JA-only pack rules
    # and NO_WEB_BLOCK (this path is web-OFF by construction).
    last_user = next(
        (m.get("content", "") for m in reversed(message_context) if m.get("role") == "user"),
        "",
    )
    book_messages = store.load_character_book_messages(last_user)
    try:
        memory_block = cm_bridge.fetch_memory_block(store.load_active_conversation())
    except Exception:
        memory_block = None

    messages = _ordered_prompt(
        _PLAIN_RULES_JA if plain else _PACK_RULES_JA_ONLY,
        NO_WEB_BLOCK,
        book_messages
        + ([memory_block] if memory_block else [])
        + [internal_context if internal_context is not None else store.load_internal_context()],
        message_context,
        right_now=_right_now_note(),
    )

    def _attempt(client):
        # The client carries her replies' Reasoning level; call 2 (the English
        # line) uses the Translations level (getOutputPackedVoiceFirst).
        return _structured(client, AmadeusReply, messages)

    def _jps_of(out) -> str:
        if isinstance(out, AmadeusReply):
            return out.assistant_reply_JPS or ""
        if isinstance(out, dict):
            return out.get("assistant_reply_JPS") or ""
        return ""

    def _out(jps: str) -> AmadeusPack:
        # Every JA guard the single-call path applies - on the Japanese line
        # only; if the honesty net swaps in its honest line, that line becomes
        # the spoken line. The English field stays empty either way - the
        # backfill translates the line she actually speaks, so UI and history
        # always agree.
        pack = _web_off_honesty_net(
            AmadeusPack(assistant_reply_JPS=_ensure_japanese_jps(jps, llm),
                        assistant_reply_ENG=""))
        return AmadeusPack(assistant_reply_JPS=pack.assistant_reply_JPS,
                           assistant_reply_ENG="")

    if plain:
        jps = _plain_reply(llm, messages, "Voice-first")
        if jps is None:
            honest = _honest_plain_failure_pack()
            return AmadeusPack(assistant_reply_JPS=honest.assistant_reply_JPS, assistant_reply_ENG="")
        return _out(jps)

    try:
        out = _attempt(llm)
        if out is None:
            # A clipped or malformed forced tool call can decode to NOTHING -
            # same actionable error as the single-call path.
            raise ValueError("structured reply came back empty - clipped "
                             "mid-JSON? check the max-output-tokens setting")
        why = _degenerate_field(_jps_of(out))
        if why:
            raise ValueError(f"structured reply unusable ({why})")
        return _out(_jps_of(out))
    except Exception as e:
        # Same ladder as the single-call path, with the JA-only twins: one
        # clean retry after a rejected sampling parameter, one forced
        # AmadeusReply retry, then salvage / honest fallback.
        if maybe_strip_rejected_params(e, LLM_Model):
            print("[Amadeus] Voice-first: sampling parameter rejected by server - retrying with reduced settings.")
            try:
                out = _attempt(_reply_llm())
                if out is not None:
                    return _out(_jps_of(out))
            except Exception:
                pass  # the retry also failed; the ladder below handles it
        print("[Amadeus] Voice-first: packed response parse failed:", repr(e))
        # A local server can drop the connection; force a fresh client before
        # the fallback attempts (single-call path, same reasoning).
        reset_llm()
        llm = _reply_llm()
        try:
            final_llm = llm.bind_tools([convert_to_openai_tool(AmadeusReply)], tool_choice="required")
            reply = _invoke_forced(final_llm, messages)
        except Exception as e2:
            # No text to salvage here: surface a clean, specific error and let
            # getOutputPackedVoiceFirst remove the user turn.
            print("[Amadeus] Voice-first: forced retry failed:", repr(e2))
            raise
        calls = getattr(reply, "tool_calls", None) or []
        if calls and calls[0].get("name") == "AmadeusReply":
            jps = _jps_of(calls[0].get("args"))
            why = _degenerate_field(jps)
            if why:
                print(f"[Amadeus] Voice-first: forced retry unusable ({why}) - honest fallback.")
                honest = _honest_plain_failure_pack()
                return AmadeusPack(assistant_reply_JPS=honest.assistant_reply_JPS, assistant_reply_ENG="")
            return _out(jps)

        raw = reply.content if isinstance(reply.content, str) else str(reply.content or "")
        if _repetition_cut(raw) is not None:
            # Cut the loop out; if that leaves no usable line, serve the same
            # static honest line the single-call path serves.
            cleaned = _de_loop(raw)
            if cleaned and _repetition_cut(cleaned) is None:
                print("[Amadeus] Voice-first: forced call returned a repetition loop - cut out, salvaging the clean part.")
                return _out(_salvage_jps_plain_text(cleaned, llm))
            print("[Amadeus] Voice-first: repetition loop with no clean part - honest fallback.")
            honest = _honest_plain_failure_pack()
            return AmadeusPack(assistant_reply_JPS=honest.assistant_reply_JPS, assistant_reply_ENG="")
        if _is_check_promise(raw):
            # Same bounded finish-the-line follow-up as the single-call path.
            print("[Amadeus] Voice-first: reply stalled on a check promise - finishing with one bounded follow-up.")
            finish_convo = list(messages) + [
                {"role": "assistant", "content": _strip_thinking(raw).strip()},
                {"role": "user",
                 "content": "(Your last message ended before giving an answer. "
                             "Finish it now: give the complete answer in this "
                             "reply. There is no follow-up turn - if you do not "
                             "know something, say so plainly and answer with "
                             "what you do know.)"},
            ]
            try:
                finish_llm = llm.bind_tools(
                    [convert_to_openai_tool(AmadeusReply)],
                    tool_choice="required")
                reply2 = _invoke_forced(finish_llm, finish_convo)
                calls2 = getattr(reply2, "tool_calls", None) or []
                if calls2 and calls2[0].get("name") == "AmadeusReply":
                    return _out(_jps_of(calls2[0].get("args")))
                raw2 = _strip_thinking(reply2.content if isinstance(reply2.content, str)
                                       else str(reply2.content or "")).strip()
                if raw2 and not _is_check_promise(raw2) and _repetition_cut(raw2) is None:
                    print("[Amadeus] Voice-first: follow-up finished the reply as plain text; salvaging.")
                    return _out(_salvage_jps_plain_text(raw2, llm))
                print("[Amadeus] Voice-first: follow-up stalled too - serving the original line.")
            except Exception as e3:
                print("[Amadeus] Voice-first: check-promise follow-up failed; serving the original line:", repr(e3))
        print("[Amadeus] Voice-first: forced call returned no AmadeusReply; salvaging plain text.")
        return _out(_salvage_jps_plain_text(raw, llm))


# pre:
# - user_message is a non-empty string from the user
# - SQLite memory store is available and writable
# - getResponsePacked(message_context) is defined and functional
#
# post:
# - appends the user message to memory
# - builds recent conversation context from memory
# - calls getResponsePacked(...) exactly once
# - appends assistant_reply_ENG to memory
# - returns the pack, both message ids, and the conversation id the turn
#   was stored in
def getOutputPacked(user_message: str):
    # Snapshot the previous turn before the new message becomes the latest one.
    internal_context = store.load_internal_context()
    user_id = store.append_message("user", user_message)
    # CharacterMemory: mirror the persisted user turn (fire-and-forget).
    cm_bridge.save_turn(store.load_active_conversation(), "user",
                        user_message)
    context = store.build_prompt_messages()

    try:
        pack = getResponsePacked(context, internal_context=internal_context)
    except Exception:
        # No reply came back (server unreachable, timeout, ...). Remove the
        # user turn so memory never contains a message with no answer and the
        # next internal-context snapshot stays honest. (Her hold-on line, if
        # one was saved, goes with it.)
        aside_id = close_aside()
        if aside_id:
            store.delete_message(aside_id)
        store.delete_message(user_id)
        raise
    # Her hold-on line, if still being written, is dropped now: the answer
    # is stored right after it, never before it.
    close_aside()

    # Store what the user actually sees, plus the Japanese line she actually
    # spoke, so her future context is in her own voice.
    assistant_id = store.append_message(
        "assistant", pack.assistant_reply_ENG, japanese=pack.assistant_reply_JPS
    )
    store.set_message_sources(assistant_id, take_turn_sources())
    # CharacterMemory: mirror the line she actually spoke (prompt-history
    # rule: the japanese column when present, else the English text).
    cm_bridge.save_turn(store.load_active_conversation(), "assistant",
                        (pack.assistant_reply_JPS or "").strip()
                        or pack.assistant_reply_ENG)
    maybe_schedule_trust_rescore()
    # The session this turn actually landed in, so the UI can verify it is
    # showing the conversation the turn was written to.
    conv_id = store.load_active_conversation()
    # Rolling summary: fold whatever this new turn pushed out of the verbatim
    # window (background worker; no-op while the window is not full).
    store.maybe_roll_summary(conv_id)
    return pack, user_id, assistant_id, conv_id


# ---------- VOICE-FIRST REPLY (web-OFF turns, 2026-07) ----------
#
# A reply is two deliverables: the Japanese line HER VOICE speaks, and the
# English line the UI shows. The single-call path produced both before
# anything happened, so her voice could not start until the translation was
# decoded too. Voice-first splits that: call 1 writes and stores the Japanese
# line (so the route can start TTS at once); call 2 - one short translation
# call in a background thread - fills the English display field and the stored
# row while she is already talking. Web-ON turns keep the single-call path
# (the search loop already makes several calls; splitting its final answer
# buys nothing). Regenerate/greetings keep it too (not latency-critical).

class VoiceFirstTurn:
    """State of one voice-first turn.

    By the time the route receives this, call 1 is complete: the user and
    assistant rows are stored (the assistant row's English text is still
    empty), the spoken line is mirrored to CharacterMemory, and her voice can
    start. wait_en() blocks until the background backfill stored the English
    line (or the timeout expires, in which case the Japanese line itself is
    returned so the UI never shows an empty box).
    """

    def __init__(self, pack: "AmadeusPack", user_id: int, assistant_id: int, conv_id: int):
        self.pack = pack
        self.user_id = user_id
        self.assistant_id = assistant_id
        self.conv_id = conv_id
        self._en_event = threading.Event()
        self._en_text = ""

    def wait_en(self, timeout: float = 120.0) -> str:
        self._en_event.wait(timeout)
        return self._en_text or self.pack.assistant_reply_JPS or ""

    def _set_en(self, text: str) -> None:
        self._en_text = text or ""
        self._en_event.set()


def _voice_first_context_lines(context, max_lines: int = 4) -> list:
    """A few labelled recent lines for the call-2 translation prompt: enough
    for the English line to match the situation, without re-sending the whole
    prompt. Assistant lines arrive content-first (her Japanese line when she
    has one), which is exactly what the translator needs."""
    lines = []
    for m in reversed(context or []):
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if m.get("role") == "user":
            lines.append("user: " + content[:300])
        elif m.get("role") == "assistant":
            lines.append("you: " + content[:300])
        if len(lines) >= max_lines:
            break
    return list(reversed(lines))


def _is_verbatim_echo(text: str, source: str, threshold: float = 0.9) -> bool:
    """True when `text` is a verbatim (near-)copy of `source` - the lazy
    "translation" a weak model produces by repeating the input line instead of
    translating it. Comparison is on normalized text (case, punctuation,
    whitespace and symbols dropped; Latin, kana and CJK letters all kept), so
    an echo still matches even when the Japanese line happens to contain
    Latin words ("Alex", "AI") - the case the letter-only check misses. A
    genuine translation - even one that legitimately carries Japanese words,
    as when she teaches Japanese - is mostly different text and compares low,
    so it passes untouched."""
    def normalize(s: str) -> str:
        return "".join(ch for ch in s.casefold() if ch.isalnum())
    a, b = normalize(text), normalize(source)
    if not b:
        return False
    if a == b:
        return True
    if len(a) < 12 or len(b) < 12:
        return False  # too short to trust a near-miss ratio on
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def _user_hint_lines(jps: str = "") -> list:
    """Who the user is, for prompts that turn her Japanese into English:
    the saved name (her line spells it in kana - アレックス - so a translator
    would otherwise romanize it as "Hon"), its Japanese ending kept as
    "Alex-kun" (user decision 2026-10-16), and their pronouns. [] when
    nothing is set."""
    lines = []
    try:
        name = store.load_username().strip()
        if name:
            reading = store.load_username_reading()
            lines.append(
                f"The user's name is {name}"
                + (f" (written {reading} in Japanese)" if reading else "")
                + f". Write it as {name} in English and keep any Japanese "
                f"ending she adds ({name}-kun, {name}-san, {name}-chan).")
        pronouns = store.pronoun_sentence()
        if pronouns:
            lines.append(pronouns)
        names = speech_text.names_in(jps)
        if names:
            lines.append("Names in her line, with their English spelling: "
                         + ", ".join(f"{r} = {w}" for w, r in names) + ".")
    except Exception as exc:
        print("[Amadeus] User hints skipped:", repr(exc))
    return lines


def _translate_reply_en(llm, jps: str, context_lines) -> str:
    """Voice-first call 2: the English display line for the Japanese line call
    1 already spoke. One short, fast call (JA line + a few context lines, not
    the full prompt). Returns "" when nothing usable comes back - the caller
    then shows the Japanese line itself, the same fallback the English guard
    uses when its translation pass fails. Every unusable result is logged
    with a short snippet of what the model actually returned, so a silent
    backfill failure is visible in the log instead of found only by digging
    through the memory DB."""
    if not _usable_source(jps):
        return ""
    lines = [
        "You are writing the English line shown to the user in the chat UI for "
        "one of Amadeus's replies. Translate her Japanese line into natural, "
        "spoken English in her voice - keep her tone and meaning exactly. "
        "Output ONLY the English text - no quotes, no Japanese, no commentary.",
    ] + _user_hint_lines(jps)
    for line in context_lines or ():
        if line and line.strip():
            lines.append(line.strip()[:300])
    lines.append("Japanese line she spoke:")
    lines.append((jps or "").strip()[:1500])
    lines.append("English line:")
    reply = llm.invoke([{"role": "user", "content": "\n".join(lines)}])
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    en = _strip_thinking(content).strip().strip(chr(34)).strip()
    if not en:
        print(f"[Amadeus] Voice-first: English backfill unusable (empty) - raw: {content[:120]!r}")
        return ""
    if _META_TRANSLATION.search(en):
        print(f"[Amadeus] Voice-first: English backfill unusable (a request, not a translation) - raw: {en[:120]!r}")
        return ""
    # A weaker model can echo the Japanese input back as the "translation";
    # that would put Japanese in the English box - treat it as a failure so
    # the real Japanese line is shown instead.
    if not _has_latin(en):
        print(f"[Amadeus] Voice-first: English backfill unusable (no English letters - likely a Japanese echo) - raw: {en[:120]!r}")
        return ""
    # The echo can still slip past the letter check when her Japanese line
    # happens to contain Latin words ("Alex", "AI", ...): a model that
    # repeats the line verbatim then "passes" as a translation. Compare
    # against her actual line instead of counting scripts.
    if _is_verbatim_echo(en, (jps or "").strip()):
        print(f"[Amadeus] Voice-first: English backfill unusable (echoed the Japanese line) - raw: {en[:120]!r}")
        return ""
    return en


def _backfill_english(turn: "VoiceFirstTurn", llm, context_lines) -> None:
    """Call 2, in a background thread: write the English display line for the
    Japanese line call 1 already stored, then publish it (the stored row and
    the UI). Any failure degrades to showing the Japanese line itself - her
    real words, just untranslated - so a slow or down model never holds her
    voice hostage."""
    jps = (turn.pack.assistant_reply_JPS or "").strip()
    started = timing.now()
    timing.start_reports()
    try:
        en = _translate_reply_en(llm, jps, context_lines)
    except Exception as exc:
        print("[Amadeus] Voice-first: English backfill failed:", repr(exc))
        en = ""
    timing.english_done(turn.assistant_id, started)
    final = en or jps
    try:
        store.update_message_content(turn.assistant_id, final)
    except Exception as exc:
        print("[Amadeus] Voice-first: could not store the English line:", repr(exc))
    turn._set_en(final)


# pre:
# - user_message is a non-empty string from the user
# - web access is OFF (the route calls this only for web-off turns)
#
# post:
# - appends the user message to memory; call 1 stores the assistant row with
#   the Japanese line (English text still empty); mirrors both lines to
#   CharacterMemory; schedules the trust rescore exactly like the single-call
#   path
# - a background thread runs call 2 (the English line) and updates the stored
#   row; the returned turn's wait_en() publishes it to the route
# - on call-1 failure the user turn is removed (the memory stays clean) and
#   the exception propagates, exactly like getOutputPacked
def getOutputPackedVoiceFirst(user_message: str) -> VoiceFirstTurn:
    internal_context = store.load_internal_context()
    user_id = store.append_message("user", user_message)
    # CharacterMemory: mirror the persisted user turn (fire-and-forget).
    cm_bridge.save_turn(store.load_active_conversation(), "user", user_message)
    context = store.build_prompt_messages()
    context_lines = _voice_first_context_lines(context)

    try:
        pack = getResponsePackedVoiceFirst(context, internal_context=internal_context)
    except Exception:
        # No reply came back (server unreachable, timeout, ...). Remove the
        # user turn so memory never contains a message with no answer.
        store.delete_message(user_id)
        raise

    # The English text starts empty; call 2 fills it (row + UI). Her future
    # context reads the Japanese column, so the empty English field changes
    # nothing about how she speaks next.
    assistant_id = store.append_message(
        "assistant", "", japanese=pack.assistant_reply_JPS
    )
    # CharacterMemory: mirror the line she actually spoke (same rule as the
    # single-call path: the Japanese line when present).
    cm_bridge.save_turn(store.load_active_conversation(), "assistant",
                        (pack.assistant_reply_JPS or "").strip()
                        or pack.assistant_reply_ENG)
    maybe_schedule_trust_rescore()
    turn = VoiceFirstTurn(pack, user_id, assistant_id, store.load_active_conversation())
    # Rolling summary: fold whatever this new turn pushed out of the verbatim
    # window (background worker; no-op while the window is not full).
    store.maybe_roll_summary(turn.conv_id)

    try:
        llm = _translation_llm()
    except Exception as exc:
        # The Japanese line is stored and will be spoken; the English line
        # degrades to the Japanese line rather than blocking her voice.
        print("[Amadeus] Voice-first: model server unreachable for the English "
              f"backfill: {exc!r}")
        llm = None
    if llm is not None:
        threading.Thread(target=_backfill_english,
                         args=(turn, llm, context_lines),
                         daemon=True, name="voice-first-en").start()
    else:
        turn._set_en((pack.assistant_reply_JPS or "").strip())
    return turn


# pre:
# - memory holds at least one message; when the session ends with an assistant
#   reply, every version of that reply is hidden and excluded from the context
#   so her previous answer never influences the new one
# post:
# - the old reply stays saved as a previous version (nothing is deleted); the
#   fresh reply is stored as the new viewed version, with its Japanese line
# - returns (AmadeusPack, new_assistant_message_id) for the re-answered turn
def regenerateReply():
    last = store.get_last_message()
    if last is None:
        raise ValueError("No conversation to regenerate yet")

    excluded = None
    if last["role"] == "assistant":
        # A startup greeting is a standalone turn: there is no user message
        # behind it to re-answer, so regeneration is a clean no-op here
        # instead of reaching back to whatever user message sits before it.
        if store.get_message_is_greeting(last["id"]) or store.get_message_is_aside(last["id"]):
            raise ValueError("Nothing to regenerate here")
        excluded = store.get_trailing_turn() or [last["id"]]
        target = store.get_message_before(excluded)
        if target is None or target["role"] != "user":
            raise ValueError("Nothing to regenerate")
        for message_id in excluded:
            store.set_message_active(message_id, False)

    # The turn being answered again: its lookups are not reused (a fresh
    # search instead); lookups of earlier questions still are.
    _turn.skip_lookup_turn = store.message_turn(
        target["id"] if excluded else last["id"])
    internal_context = store.load_internal_context()
    context = store.build_prompt_messages(exclude_ids=excluded)
    try:
        pack = getResponsePacked(context, internal_context=internal_context)
    except Exception:
        # The fresh version never got stored - restore the previous versions to
        # viewed state so nothing the user had is lost.
        if excluded:
            for message_id in excluded:
                store.set_message_active(message_id, True)
        raise
    finally:
        _turn.skip_lookup_turn = None
    new_id = store.append_message(
        "assistant", pack.assistant_reply_ENG, japanese=pack.assistant_reply_JPS
    )
    store.set_message_sources(new_id, take_turn_sources())
    return pack, new_id



# ---------- SPECIAL INTERACTIONS ---------- 

# pre:
# - interaction value represents int value of the corresponding interaction. e.g.,
#   1 -> chest touch
#   2 -> Head pat
#   3 -> Arm poke
#
# post:
# - append in format: ("user", "[Interaction event: The user touched your shoulder.]")
# - return the hard coded responses
def SpecialInteraction(interaction_value: int) -> dict:
    event = INTERACTION_EVENTS.get(interaction_value)
    response_variants = INTERACTION_RESPONSES.get(interaction_value)
    if event is None or not response_variants:
        raise ValueError("Unknown interaction")
    # Select the text AND recording together, never independently.
    variant = random.choice(response_variants)
    response = variant["text"]
    event_id = store.append_message("user", event)
    response_id = store.append_message(
        "assistant", response, japanese=variant.get("japanese")
    )

    # Remember which recording belongs to this line so it can be replayed.
    audio_file = (variant.get("audio_url") or "").strip()
    if audio_file:
        store.set_message_audio(response_id, audio_file)

    return {
        "response": response,
        "audio_url": variant.get("audio_url"),
        "event_id": event_id,
        "response_id": response_id,
    }



# ---------- TRUST (hidden relationship stat, viewable in Settings) ----------
#
# Trust is a global 0-100 number about HER and YOU, shared across all sessions.
# It moves BOTH ways: a background re-score (every ~5 of your messages) can
# raise it when the conversation is warm, or lower it when it is not.
# The score blends toward the model's judgement so one misread can't tank it.

# Special interactions nudge it a little (head pat warms her; a chest touch earns a glare).
INTERACTION_TRUST_DELTA = {1: 0, 2: 3, 3: 1}

# Max trust points a single background re-score may move (up OR down).
# Kept small on purpose: closeness should creep gradually, not snap.
TRUST_STEP_MAX = 3


def apply_interaction_trust(interaction_value: int) -> int:
    delta = INTERACTION_TRUST_DELTA.get(interaction_value, 0)
    if delta:
        stats.adjust_stat("trust", delta)
    return delta


def _parse_score(text) -> "int | None":
    t = _strip_thinking(text or "")
    digits = re.sub(r"[^0-9]", "", t)
    if not digits:
        return None
    return max(0, min(100, int(digits[:3])))


def _parse_delta(text) -> "int | None":
    """First integer (sign preserved) in the model's reply, clamped to the step cap."""
    t = _strip_thinking(text or "")
    m = re.search(r"-?\d+", t)
    if not m:
        return None
    return max(-TRUST_STEP_MAX, min(TRUST_STEP_MAX, int(m.group())))


def run_trust_rescore() -> None:
    """Background: nudge trust a small step in the direction the rapport is moving.

    It never estimates an absolute closeness level. It only decides whether the recent
    stretch was warmer, colder, or flat, then moves trust by at most TRUST_STEP_MAX
    points. So closeness creeps up slowly over many messages (and can fall), instead of
    snapping to whatever the last few messages happen to look like.
    """
    try:
        recent = store.load_memory_raw()[-16:]
        if len(recent) < 2:
            return
        old = int(stats.load_stat("trust"))
        transcript = chr(10).join(
            ("USER" if m["role"] == "user" else "AMADEUS") + ": " + m["content"][:400]
            for m in recent
        )
        llm = get_llm(API_KEY, LLM_Model)
        prompt = chr(10).join([
            "You are judging how the CLOSURE between a user and an AI assistant "
            "(Amadeus, an assistant based on the memories of Makise Kurisu) is "
            "moving in the recent messages only.",
            "Do NOT estimate an absolute closeness level. Decide the DIRECTION of change.",
            "- warmer, friendlier, playful, genuine rapport building  -> positive",
            "- rude, hostile, dismissive, or things went badly        -> negative",
            "- neutral, flat, ordinary assistant work, or no change    -> 0",
            "Reply with ONLY one integer from -3 to +3 "
            "(how much warmer or colder this stretch is). Nothing else.",
            "",
            "CONVERSATION:",
            transcript,
        ])
        reply = llm.invoke([{"role": "user", "content": prompt}])
        content = reply.content if isinstance(reply.content, str) else str(reply.content)
        delta = _parse_delta(content)
        if delta is None:
            print("[Amadeus] Trust rescore: no number returned, keeping current value")
            return
        new = max(0, min(100, old + delta))
        stats.set_stat("trust", new)
        print(f"[Amadeus] Trust rescore: delta {delta:+d}  {old} -> {new}")
    except Exception as exc:
        print(f"[Amadeus] Trust rescore skipped: {exc!r}")


def maybe_schedule_trust_rescore() -> None:
    """Count your messages; every ~5, re-judge trust in the background (never blocks chat)."""
    try:
        count = stats.adjust_stat("_user_msg_counter", 1)
        if count >= 5:
            stats.set_stat("_user_msg_counter", 0)
            threading.Thread(target=run_trust_rescore, daemon=True, name="trust-rescore").start()
    except Exception as exc:
        print(f"[Amadeus] Trust counter error: {exc!r}")


# ---------- JAPANESE VOICE BACKFILL (one-time, background, at startup) ----------
#
# Old replies were stored English-only. This gives each of them the Japanese
# line she would have spoken, in order, so even old sessions are in her voice.
# Runs quietly at launch; if the model server is not up yet it waits a few
# minutes and otherwise retries on the next launch. Never blocks the chat.

_BACKFILL_STARTED = False


def _voice_line_for_reply(llm, english_reply: str, previous_user) -> str:
    lines = [
        "You are Amadeus, an assistant based on the memories and personality of Makise Kurisu. "
        "Below is one of your earlier replies, stored in English. Write the Japanese line "
        "(assistant_reply_JPS) that you would ACTUALLY have spoken for this reply.",
        "Rules: natural spoken Japanese, in your own voice. Never mirror English word order. "
        "Plain spoken Japanese for a voice synthesizer: allowed marks are only 、。！？ "
        "No quotes, markdown, emoji, stage directions, or commentary.",
    ]
    if previous_user:
        lines.append("The user's message right before this reply was: " + previous_user[:300])
    lines.append("English reply:")
    lines.append(english_reply[:1500])
    lines.append("Output ONLY the Japanese line.")
    prompt = chr(10).join(lines)
    reply = llm.invoke([{"role": "user", "content": prompt}])
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    return _strip_thinking(content).strip().strip(chr(34)).strip()


def voice_line_for_edit(english_reply: str, previous_user) -> str:
    """The Japanese line for a reply the user edited (in English) in the UI.

    Her prompt history and long-term memory read the Japanese column, so an
    edit only steers her once this line is rewritten too. Raises when the
    model is unreachable or returns no Japanese - the caller then refuses
    the edit instead of saving English and Japanese that disagree.
    """
    llm = _translation_llm()
    jps = _clean_tts_text(_voice_line_for_reply(llm, english_reply, previous_user))
    if not _has_japanese(jps):
        raise ValueError("model returned no Japanese")
    return jps


_NAME_READING_TIMEOUT_S = 30


def suggest_name_reading(name: str) -> str:
    """A kana reading for the user's saved name, for the voice ("Alex" ->
    アレックス). Initials and kana names need no model call (speech_text.
    rule_reading); anything else costs ONE short call, made only when the
    user saves the name. Returns '' when the model is unreachable or answers
    with anything but kana - the user can then type the reading."""
    rule = speech_text.rule_reading(name)
    if rule or not (name or "").strip():
        return rule
    def _ask():
        llm = get_llm(API_KEY, LLM_Model)
        return llm.invoke([{"role": "user", "content": (
            "How is this person's name written in Japanese katakana, as it "
            "is actually pronounced (for a voice to say it)? Output ONLY the "
            "katakana, nothing else.\n\nName: " + name.strip()[:60])}])
    # The client's own timeout is a long generation ceiling; a Settings box
    # must not wait that long, so the call is capped here.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        reply = pool.submit(_ask).result(timeout=_NAME_READING_TIMEOUT_S)
        content = reply.content if isinstance(reply.content, str) else str(reply.content)
        reading = _strip_thinking(content).strip().strip("\"'「」『』。").strip()
    except Exception as e:
        print("[Amadeus] Name reading suggestion failed:", repr(e))
        return ""
    finally:
        pool.shutdown(wait=False)
    return reading if speech_text.is_kana(reading) and len(reading) <= 20 else ""


def backfill_japanese_voice() -> None:
    global _BACKFILL_STARTED
    if _BACKFILL_STARTED:
        return
    _BACKFILL_STARTED = True
    try:
        pending = store.get_unvoiced_assistant_messages()
        if not pending:
            print("[Amadeus] Backfill: all replies already have Japanese voice")
            return
        print(f"[Amadeus] Backfill: {len(pending)} old replies need Japanese voice")
        llm = None
        # Give the local model server up to ~3 minutes to come online.
        for _ in range(9):
            try:
                llm = _translation_llm()
                break
            except Exception:
                threading.Event().wait(20)
        if llm is None:
            print("[Amadeus] Backfill: model server not reachable, will retry next launch")
            return
        failures = 0
        for row in pending:
            try:
                jps = _voice_line_for_reply(llm, row["content"], row.get("prev_user"))
                if not _has_japanese(jps):
                    raise ValueError("model returned no Japanese")
                store.set_message_japanese(row["id"], jps)
                failures = 0
                print(f"[Amadeus] Backfill: voiced message {row['id']}")
            except Exception as exc:
                failures += 1
                print(f"[Amadeus] Backfill failed at message {row['id']}: {exc!r}")
                if failures >= 3:
                    print("[Amadeus] Backfill stopping; will retry next launch")
                    return
        print("[Amadeus] Backfill: complete")
    except Exception as exc:
        print(f"[Amadeus] Backfill error: {exc!r}")


# ---------- ROLLING SUMMARY MERGE (tier-1 memory; registered on memory.py) ----------
#
# One SHORT plain call: fold the exchange that just left the verbatim window
# into the per-conversation running summary. When the model server is down
# (get_llm raises) or the reply is unusable, memory.py falls back to a plain
# text fold, so the window always closes.

def _merge_summary_llm(current_summary: str, new_exchange: str) -> str:
    llm = get_llm(API_KEY, LLM_Model)
    prompt = (
        "You maintain a running summary of a chat conversation so its older "
        "parts can be dropped from context without being forgotten. "
        "CURRENT SUMMARY:\n" + (current_summary or "(none yet)") +
        "\n\nNEW EXCHANGE TO FOLD IN:\n" + new_exchange[:3000] +
        "\n\nMerge the new exchange into the summary: keep durable facts, "
        "decisions, names, plans, and open questions; drop small talk and "
        "anything already covered; write in the language the conversation "
        "uses; plain prose, no headers, no quotes. Output ONLY the updated "
        "summary, under 350 words."
    )
    pronouns = store.pronoun_sentence()
    if pronouns:
        prompt += " " + pronouns + " Use them when the summary refers to the user."
    reply = llm.invoke([{"role": "user", "content": prompt}])
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    text = _strip_thinking(content).strip().strip(chr(34)).strip()
    if not text:
        raise ValueError("model returned an empty summary")
    return text


store.set_summary_merger(_merge_summary_llm)

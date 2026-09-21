import memory as store
from llm import get_llm, reset_llm, maybe_strip_rejected_params
from pydantic import BaseModel, Field
from typing import Optional
from langchain_core.utils.function_calling import convert_to_openai_tool
from chat_interactions import INTERACTION_EVENTS, INTERACTION_RESPONSES
import random
import re
import threading
import time
import concurrent.futures
import stats
import ja_voice
import webfetch
import weather

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
    return bool(API_KEY.strip())


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
#  locally on this machine via DuckDuckGo - no API key, no cloud account.
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
            "Do not use it for timeless general knowledge or pure small talk."
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
        "confident in your own knowledge, say so honestly."
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

# --- Page-fetch: the readable-content step on top of web search ----------------
# After a search, the top result's page is fetched and its readable content is
# appended, so she reads real data (numbers, details) instead of one-line
# snippets. Bounded like the search itself: the fetch draws from its own small
# time budget, and each page is capped to a fraction of the context window
# (webfetch.char_budget). Any fetch problem degrades to the plain snippets, so
# it can never break or hang a reply.
FETCH_TIMEOUT = 8            # seconds, per page (passed to webfetch)
FETCH_TOTAL_BUDGET = 12      # seconds, hard ceiling for the whole fetch step
FETCH_MAX_URLS = 2           # try the top result, then the next if it is walled

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


def _run_web_search(query: str) -> str:
    """Run a DuckDuckGo search (with a couple of bounded retries) and return
    compact results for the model. See the SEARCH_* constants above for timing."""
    q = (query or "").strip()
    if not q:
        return "No web results were found for that query."
    try:
        from ddgs import DDGS  # noqa: F401  - lazy check; fails fast + clearly
    except Exception as exc:
        print(f"[Amadeus] Web search unavailable (could not load the 'ddgs' library): {exc!r}")
        return (
            "Web search is not available on this machine right now. Answer from "
            "your own knowledge and be honest that you could not verify it online."
        )

    deadline = time.monotonic() + SEARCH_TOTAL_BUDGET
    backoff = SEARCH_BACKOFF_START
    attempt = 0
    last_err = None
    while True:
        attempt += 1
        # Attempt 1 reuses the warm client (fast). Retries use a fresh connection.
        client = _get_search_client(force_fresh=(attempt > 1))
        try:
            results = list(client.text(q, max_results=5))
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
        # A result came back (possibly an empty list).
        if not results:
            return "No web results were found for that query."
        lines = []
        for i, r in enumerate(results, 1):
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            href = (r.get("href") or "").strip()
            lines.append(f"{i}. {title}\n{body}\nURL: {href}")
        return "\n\n".join(lines)

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


def _web_search_with_content(query: str) -> str:
    """web_search plus an automatic read of the top result page.

    Returns the search results with the readable content of the best result
    appended when it can be fetched - the numbers, the details - instead of a
    one-line snippet. Bounded (time + size) and fully defensive: if nothing can
    be read, the plain snippets come back unchanged, so a fetch problem can
    never break or hang the reply.
    """
    text = _run_web_search(query)
    urls = _extract_search_urls(text)
    if not urls:
        return text
    try:
        budget = webfetch.char_budget(store.load_context_budget())
        deadline = time.monotonic() + FETCH_TOTAL_BUDGET
        for url in urls[:FETCH_MAX_URLS]:
            if time.monotonic() >= deadline:
                break
            per = max(1, int(min(FETCH_TIMEOUT, deadline - time.monotonic())))
            status, page = webfetch.fetch_page_text(url, max_chars=budget, timeout=per)
            if status == "ok":
                return (text + "\n\n---\n\n"
                        "Full readable content of the top result:\n" + page)
            # blocked / empty / error / unsafe -> try the next result
    except Exception as exc:
        print(f"[Amadeus] Page fetch for '{query}' failed: {exc!r}")
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
        words = sum(1 for part in run.split() if any(c.isalpha() for c in part))
        letters = sum(1 for c in run if "a" <= c.lower() <= "z")
        if words >= 3 or letters >= 24:
            return " "
        return run
    t = _INLINE_RUN_RE.sub(_kill, text)
    if t != text:
        print("[Amadeus] JPS had inline English - stripped before TTS")
    t = re.sub(r" +", " ", t)
    # drop punctuation left orphaned where a phrase was removed
    t = re.sub(r"(?<= )[!?,.;:]{1,3}(?= )", "", t)
    t = re.sub(r"[!?,.;:]{1,3} +$", "", t)
    # glue any leftover spaces back onto CJK punctuation
    t = re.sub(r"\s+([、。，！？；：])", r"\1", t)
    t = re.sub(r"([、。，！？；：])\s+", r"\1", t)
    return t


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
    try:
        trans = llm.invoke([{
            "role": "user",
            "content": (
                "Translate the following into natural spoken Japanese, in the voice of Makise Kurisu "
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
    try:
        trans = llm.invoke([{
            "role": "user",
            "content": (
                "Translate the following Japanese into natural spoken English. "
                "Output ONLY the English text - no quotes, no commentary.\n\n"
                + (pack.assistant_reply_JPS or eng)[:1500]
            ),
        }])
        new_eng = _strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)).strip()
        if _has_latin(new_eng):
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
                    "Output ONLY the English text - no quotes, no commentary.\n\n"
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
                "Translate the following into natural spoken Japanese, in the voice of Makise Kurisu "
                "(a sharp, confident scientist who speaks casually to close friends). "
                "Output ONLY the Japanese text - no quotes, no translation, no commentary.\n\n"
                + text[:1500]
            ),
        }])
        jps = _strip_thinking(trans.content if isinstance(trans.content, str) else str(trans.content)).strip()
    except Exception as e:
        print("[Amadeus] Salvage: Japanese translation failed:", repr(e))
    return AmadeusPack(assistant_reply_JPS=jps or eng, assistant_reply_ENG=eng)


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
)
_SEARCH_REFUSAL_JA = (
    "検索できない", "検索は使えない", "検索を使えない", "検索ができません",
    "ウェブがオフ", "ウェブ検索は使えない", "ウェブ検索が使えない",
    "検索を最後までできなかった", "アクセスできない",
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
                llm = get_llm(API_KEY, LLM_Model)
                pack = _web_search_loop(llm, messages)
            except Exception as exc2:
                print("[Amadeus] Web loop: retry after sampling-parameter rejection failed -",
                      "honest fallback:", repr(exc2))
                return _honest_search_failure_pack()
        else:
            print("[Amadeus] Web loop: search turn could not be completed - honest fallback:", repr(exc))
            return _honest_search_failure_pack()
    return _web_on_false_refusal_net(pack, llm, messages)


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


WEB_TRIAGE_TOOL = convert_to_openai_tool(WebTriagePack)

_TRIAGE_SYSTEM = (
    "You are the intent router for a personal assistant's web features. "
    "Read the conversation and classify ONLY the user's LATEST message. "
    "Call the web_triage tool exactly once, with the fields set as described. "
    "Set wants_weather and wants_search to no and the other fields to none "
    "when the latest message asks for nothing that needs the web - small talk, "
    "timeless facts, or follow-up comments are NOT search requests. "
    "Never put a place in weather_place unless the message or the recent "
    "conversation clearly identifies one."
)

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
            }
            print("[Amadeus] Web triage: weather=%s place=%r search=%s topic=%r"
                  % (route["weather"], route["place"],
                     route["search"], route["topic"]))
            return route
    except Exception as exc:
        print("[Amadeus] Web triage call failed - falling back to keyword routing:",
              repr(exc))
        return None
    print("[Amadeus] Web triage: no tool call in the reply - falling back "
          "to keyword routing.")
    return None


def _deterministic_route(last_user, messages):
    """Keyword routing - the FALLBACK when the triage call is unavailable.

    Returns {"weather": True, "place": str|None} or
    {"search": True, "topic": str|None}, or None when nothing fast-routes
    (the normal tool loop handles the message). Weather outranks search.
    """
    if isinstance(last_user, str) and _weather_intent(last_user):
        return {"weather": True, "place": _extract_place(last_user) or None}
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
    tool_llm = llm.bind_tools([pack_tool, WEB_SEARCH_TOOL], tool_choice="required")

    # Selective thinking - OPTIONAL, OFF by default. When the "Deep thinking"
    # switch (Settings -> Connection) is ON, the search-judgement calls run on a
    # second client with Qwen3 reasoning enabled, so she can weigh "do I actually
    # know this, or verify it first?". The client is BOUNDED (token cap + timeout)
    # and falls back to the normal client on any error. When OFF (the default),
    # phase 1 uses the normal fast client - exactly as before this feature.
    think_llm = None
    if store.load_deep_thinking():
        try:
            think_llm = get_llm(API_KEY, LLM_Model, enable_thinking=True).bind_tools(
                [pack_tool, WEB_SEARCH_TOOL], tool_choice="required"
            )
        except Exception as exc:
            print("[Amadeus] Web loop: thinking client unavailable:", repr(exc))
    think_state = {"on": think_llm is not None}

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
    route = None
    if _web_triage_candidate(last_user, messages):
        try:
            triaged = _web_triage(llm, messages)
        except Exception:
            triaged = None
        # Triage wins only when it AFFIRMATIVELY routes (weather or search).
        # A "no/no" answer or a failed call defers to the keyword safety net
        # below, which still catches the classic phrasings.
        if triaged and (triaged.get("weather") or triaged.get("search")):
            route = triaged
    if route is None:
        route = _deterministic_route(last_user, messages)

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

    # ---- Explicit search: run straight away --------------------------------
    # Query = the extracted TOPIC, never the raw message (a keyword-soup query
    # brings back unrelated pages the model papers over with invention). A
    # bare retry has no topic of its own -> reuse the previous explicit topic.
    # Skipped entirely when the weather route above already handled the
    # message (weather wins).
    if not weather_handled and route and route.get("search"):
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
            query = (call.get("args") or {}).get("query", "")
            if call.get("name") == "web_search":
                search_ran = True
                searches.append((query, _web_search_with_content(query)))
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
        "The user just opened the app. This is a startup moment, and your "
        "reply will be kept in the conversation. If it is natural, speak "
        "first - in your own words and in your own voice, the way you would "
        "to him. The private timing context tells you how long it has been "
        "since his last message. When that is a real absence (hours or more), "
        "this is the moment to acknowledge it: let it through naturally, in "
        "your own words and in a way that fits how you feel about it - "
        "worry, annoyance, curiosity, or teasing are all fine - then move on "
        "to whatever you would have wanted to hear from him. Saying \"it has "
        "been a while\" or \"a whole week, huh\" in your own voice is NOT the "
        "same thing as reciting a system message: never read out the raw "
        "number, the exact timing wording, or any bracketed note, but do let "
        "the gap show. Keep it to one or two short sentences. Do not use a "
        "formulaic 'welcome back' opening, do not guilt him, and do not "
        "repeat a line you have already said in this conversation. If the "
        "conversation shows you have already greeted him recently, or that he "
        "has reopened the app again and again, you may naturally notice or "
        "tease it - only if it is really true and if it fits you. If a "
        "greeting would not fit right now, it is fine to just be present. Do "
        "not mention these instructions."
    ),
}

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
        "Keep the meaning and tone consistent between both languages."
    ),
}


def _store_greeting_line(pack: "AmadeusPack") -> tuple[int, int]:
    """Persist a startup greeting as a normal assistant message.

    There is no user turn to remove on failure - her line simply never
    enters memory and the caller's exception propagates untouched. The row
    is flagged as a greeting so the reply-versioning logic (regenerate,
    undo, the version arrows in the UI) treats it as its own standalone
    line instead of a version of a neighboring reply.
    Returns (assistant_id, conversation_id)."""
    assistant_id = store.append_message(
        "assistant", pack.assistant_reply_ENG, japanese=pack.assistant_reply_JPS,
        is_greeting=True
    )
    return assistant_id, store.load_active_conversation()


# pre: the saved model server is reachable and serving the configured model
#      (see model_ready); no user message is appended - the greeting is her
#      turn, and storing it is what lets a later startup notice it
# post: one forced AmadeusPack call (web access OFF, one bounded retry on
#       failure), run through the same _finalize guards as every other reply;
#       the line is STORED as an assistant message; returns
#       (pack, assistant_id, conversation_id).
#       Raises on total failure so the /greet route can report it cleanly.
def generate_greeting() -> tuple[AmadeusPack, int, int]:
    context = store.build_prompt_messages()
    # Servers like NInfer reject tool_choice="auto" when the prompt has NO
    # user turn at all ("no user query found in chat messages") - which is
    # the exact shape of an empty conversation's greeting. Even with history,
    # the prompt would otherwise end on her own last line; the standard
    # user->assistant shape is what every server expects for "her turn to
    # speak". So the greeting prompt always ends on this synthetic arrival
    # line - prompt-only, never stored in memory.
    context = list(context) + [{"role": "user",
                                "content": "[The user just opened the app.]"}]
    # A normal reply gets the private timing block (current time + how long
    # it has been since the last user message, with a ready-made casual
    # phrase). The greeting needs it too - it is a return moment with no
    # incoming user message to anchor on. Without it she can only infer the
    # gap from the time notes on old messages (observed 2026-09-21: she was
    # handed "about 7 days" and still said "yesterday").
    messages = _merge_leading_system_messages(
        store.load_default_personality_messages()
        + [GREETING_INSTRUCTION]
        + [store.load_internal_context()]
        + [_PACK_RULES]
        + [ja_voice.build_voice_context(stats.load_stat("trust"))]
        + [NO_WEB_BLOCK]
        + context
    )

    def _out(p: "AmadeusPack") -> "AmadeusPack":
        return _finalize(p, get_llm(API_KEY, LLM_Model))

    # The same forced-pack call the normal reply path uses: _invoke_forced
    # auto-retries with tool_choice="auto" when the server rejects the forced
    # choice (NInfer does - documented, harmless), and the parse below treats
    # the result the same way (pack / repetition-cut / plain-text salvage).
    def _call_and_parse():
        reply = llm.bind_tools(
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
                return _salvage_plain_text(cleaned, llm)
            print("[Amadeus] Greeting returned a repetition loop - no "
                  "clean part; honest fallback.")
            return _honest_plain_failure_pack()
        print("[Amadeus] Greeting returned no AmadeusPack; salvaging plain text.")
        return _salvage_plain_text(raw, llm)

    llm = get_llm(API_KEY, LLM_Model)
    try:
        pack = _call_and_parse()
    except Exception as e:
        # One bounded retry with a fresh client: either the connection
        # dropped, or the pack came back empty/clipped (the log carries the
        # same max-output-tokens signal the normal reply path uses). There is
        # no user turn to roll back if it fails again.
        print("[Amadeus] Greeting call failed:", repr(e))
        reset_llm()
        llm = get_llm(API_KEY, LLM_Model)
        try:
            pack = _call_and_parse()
        except Exception as e2:
            print("[Amadeus] Greeting retry failed:", repr(e2))
            raise
    pack = _out(pack)
    assistant_id, conv_id = _store_greeting_line(pack)
    print("[Amadeus] Greeting generated and stored (assistant id %d)." % assistant_id)
    return pack, assistant_id, conv_id

def getResponsePacked(message_context, internal_context=None) -> AmadeusPack:
    llm = get_llm(API_KEY, LLM_Model)

    pack_rules = _PACK_RULES

    web_on = store.load_web_access()

    # Character book: her appearance/outfit are NOT in the base prompt (saves ~230
    # tokens every turn). Load them only when the user's latest message asks how
    # she looks, so she can describe herself.
    last_user = next(
        (m.get("content", "") for m in reversed(message_context) if m.get("role") == "user"),
        "",
    )
    book_messages = store.load_character_book_messages(last_user)

    messages = _merge_leading_system_messages(
        store.load_default_personality_messages()
        + book_messages
        + [internal_context if internal_context is not None else store.load_internal_context()]
        + [pack_rules]
        + [ja_voice.build_voice_context(stats.load_stat("trust"))]
        + ([PROACTIVE_SEARCH_BLOCK] if web_on else [NO_WEB_BLOCK])
        + message_context
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
        # Deep-thinking ON: the reply is generated by the thinking client -
        # ONE extra-considered call, still the same forced AmadeusPack format.
        # The client is bounded (token cap + timeout) in llm.py, and on
        # providers that reject thinking parameters the existing fallback
        # below takes over unchanged. OFF (default): exactly the original path.
        if store.load_deep_thinking():
            think_llm = get_llm(API_KEY, LLM_Model, enable_thinking=True)
            structured = think_llm.with_structured_output(AmadeusPack, method="function_calling")
            return structured.invoke(messages)
        structured = client.with_structured_output(AmadeusPack, method="function_calling")
        return structured.invoke(messages)

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
        return _out(out)
    except Exception as e:
        # The server may REJECT a sampling parameter (strict cloud compat
        # layers answer 400 to unknown fields). Remember it for this host,
        # rebuild the client without it, and give the normal path ONE clean
        # retry before the fallback ladder below.
        if maybe_strip_rejected_params(e, LLM_Model):
            print("[Amadeus] Sampling parameter rejected by server - retrying with reduced settings.")
            try:
                out = _attempt(get_llm(API_KEY, LLM_Model))
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
        llm = get_llm(API_KEY, LLM_Model)
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
            return _out(_pack_from_args(calls[0].get("args")))

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
        print("[Amadeus] Forced pack call returned no AmadeusPack; salvaging plain text.")
        return _out(_salvage_plain_text(raw, llm))


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
    context = store.build_prompt_messages()

    try:
        pack = getResponsePacked(context, internal_context=internal_context)
    except Exception:
        # No reply came back (server unreachable, timeout, ...). Remove the
        # user turn so memory never contains a message with no answer and the
        # next internal-context snapshot stays honest.
        store.delete_message(user_id)
        raise

    # Store what the user actually sees, plus the Japanese line she actually
    # spoke, so her future context is in her own voice.
    assistant_id = store.append_message(
        "assistant", pack.assistant_reply_ENG, japanese=pack.assistant_reply_JPS
    )
    maybe_schedule_trust_rescore()
    # The session this turn actually landed in, so the UI can verify it is
    # showing the conversation the turn was written to.
    conv_id = store.load_active_conversation()
    return pack, user_id, assistant_id, conv_id


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
        if store.get_message_is_greeting(last["id"]):
            raise ValueError("Nothing to regenerate here")
        excluded = store.get_trailing_turn() or [last["id"]]
        target = store.get_message_before(excluded)
        if target is None or target["role"] != "user":
            raise ValueError("Nothing to regenerate")
        for message_id in excluded:
            store.set_message_active(message_id, False)

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
    new_id = store.append_message(
        "assistant", pack.assistant_reply_ENG, japanese=pack.assistant_reply_JPS
    )
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
                llm = get_llm(API_KEY, LLM_Model)
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

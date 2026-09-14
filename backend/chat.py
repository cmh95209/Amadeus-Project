import memory as store
from llm import get_llm, reset_llm
from pydantic import BaseModel, Field
from langchain_core.utils.function_calling import convert_to_openai_tool
from chat_interactions import INTERACTION_EVENTS, INTERACTION_RESPONSES
import random
import re
import threading
import stats
import ja_voice

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
        "Treat search results as hints, not gospel: if a result seems wrong or you are more "
        "confident in your own knowledge, say so honestly."
    ),
}


def _run_web_search(query: str) -> str:
    """Run a DuckDuckGo search and return compact results for the model."""
    try:
        from ddgs import DDGS
        results = list(DDGS(timeout=15).text((query or "").strip(), max_results=5))
    except Exception as exc:
        return (f"Web search failed ({exc!r}). Answer from your own knowledge "
                f"and tell the user you could not verify it online.")
    if not results:
        return "No web results were found for that query."
    lines = []
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or "").strip()
        lines.append(f"{i}. {title}\n{body}\nURL: {href}")
    return "\n\n".join(lines)


def _pack_from_args(args) -> "AmadeusPack":
    """Build an AmadeusPack from tool-call args; reject empty garbage."""
    jps = str((args or {}).get("assistant_reply_JPS", "")).strip()
    eng = str((args or {}).get("assistant_reply_ENG", "")).strip()
    if not jps and not eng:
        raise ValueError("AmadeusPack args were empty")
    return AmadeusPack(assistant_reply_JPS=jps, assistant_reply_ENG=eng)


def _strip_thinking(text: str) -> str:
    """Remove thinking/channel tokens a model may leak into its answer: Qwen
    <thinking>...</thinking> and bare <think></think>, plus Gemma-style
    <channel|>thought / <channel|> markers, which a degenerating local model
    can repeat into a run-on loop ("<|channel>thought<channel|>" xN)."""
    t = text or ""
    t = re.sub(r"(?is)<thinking>.*?</thinking>", "", t)
    t = re.sub(r"(?is)</?think(ing)?>", "", t)
    t = re.sub(r"(?is)<\|channel>.*?</channel\|>", "", t)   # full channel block
    t = re.sub(r"(?s)\s*<\|channel>\s*thought\b", "", t)     # unclosed open token
    t = re.sub(r"(?s)<channel\|>\s*", "", t)                  # close token
    t = re.sub(r"(?m)^\s*thought\s*$", "", t)                 # bare 'thought' line
    return t.strip()


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
        parentheticals, and truncate runaway repetition.
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
    tool, and _salvage_plain_text covers a plain-text answer. Other exceptions
    (timeouts, connection drops, real schema 400s) propagate unchanged.
    """
    try:
        return bound_llm.invoke(convo)
    except Exception as exc:
        if not _forced_tool_choice_rejected(exc):
            raise
        print("[Amadeus] Server rejected forced tool choice; retrying with tool_choice=auto:",
              type(exc).__name__)
        return bound_llm.bind(tool_choice="auto").invoke(convo)



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


def _getResponsePackedWithWebSearch(llm, messages) -> "AmadeusPack":
    """Chat loop with the web_search tool available.

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

    # ---- Phase 1: search (or answer) loop -------------------------------
    last_plain = ""
    for _ in range(3):  # hard cap: at most two searches before we force an answer
        reply = _phase1_invoke(convo)
        calls = getattr(reply, "tool_calls", None) or []

        if not calls:
            # She answered with plain text instead of a tool call.
            raw = reply.content if isinstance(reply.content, str) else str(reply.content)
            last_plain = _strip_thinking(raw)
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
                searches.append((query, _run_web_search(query)))
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

    # ---- Phase 2: force the final structured answer ----------------------
    if last_plain:
        convo.append({"role": "assistant", "content": last_plain})
    final_llm = llm.bind_tools([pack_tool], tool_choice="required")
    reply = _invoke_forced(final_llm, convo)
    calls = getattr(reply, "tool_calls", None) or []
    if calls and calls[0].get("name") == "AmadeusPack":
        return _pack_from_args(calls[0].get("args"))

    # ---- Phase 3 (last resort): salvage plain text -----------------------
    # The structured AmadeusPack never came through, but she may have said
    # something usable in prose. Rather than erroring out, turn that into a pack
    # so the user still gets an answer. We prefer the text from the last (phase 2)
    # call and fall back to any plain answer captured during the search loop.
    # _salvage_plain_text only raises if there is literally no text at all.
    raw = reply.content if isinstance(reply.content, str) else str(reply.content or "")
    candidate = _strip_thinking(raw) or last_plain
    print("[Amadeus] Web loop: no AmadeusPack; salvaging plain text.")
    return _salvage_plain_text(candidate, llm)


def getResponsePacked(message_context, internal_context=None) -> AmadeusPack:
    llm = get_llm(API_KEY, LLM_Model)

    # IMPORTANT: Add a system rule that tells the model exactly what to output.
    pack_rules = {
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
        + ([PROACTIVE_SEARCH_BLOCK] if web_on else [])
        + message_context
    )

    try:
        if web_on:
            return _ensure_japanese(_getResponsePackedWithWebSearch(llm, messages), llm)

        # Web access OFF: original single-call path (no search tool offered).
        structured = llm.with_structured_output(AmadeusPack, method="function_calling")
        out: AmadeusPack = structured.invoke(messages)
        return _ensure_japanese(out, llm)
    except Exception as e:
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
            return _ensure_japanese(_pack_from_args(calls[0].get("args")), llm)

        # The call returned but not as an AmadeusPack (she answered in plain text).
        # Rather than erroring out, salvage whatever she said into a pack so the user
        # still gets an answer. _salvage_plain_text only raises if there is literally
        # no text at all - the honest "nothing came back" case.
        raw = reply.content if isinstance(reply.content, str) else str(reply.content or "")
        print("[Amadeus] Forced pack call returned no AmadeusPack; salvaging plain text.")
        return _ensure_japanese(_salvage_plain_text(raw, llm), llm)


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
# - returns an AmadeusPack containing:
#     - assistant_reply_JPS (native Japanese dialogue, for TTS)
#     - assistant_reply_ENG (English translation, for the UI)
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
    return pack, user_id, assistant_id


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

"""The LLM provider list for Settings -> Custom Model (feature/provider-picker).

One table, in the order Settings shows it: local servers (auto-detect,
Ollama, LM Studio), the OpenAI-compatible cloud providers, Google Vertex AI
(JSON-key sign-in, see vertex_auth.py) and a free-form custom address.

Each provider keeps its own API key in a personal file (data/api_keys.json,
like api_key.txt - on the user's PC only, never in git); data/llm_provider.txt
remembers which one is active. Existing installs (a server address + a key in
api_key.txt, no provider chosen) keep working: the provider matching the
saved address becomes active, and api_key.txt stays "the key in use" - the
memory sidecar reads it.
"""
import json
import os
import re
import threading
import time

import builtin_llm
import llm
import memory
import vertex_auth

# order = the order Settings shows
PROVIDERS = [
    {"id": "auto",       "label": "Local (auto-detect)", "kind": "local",  "url": ""},
    {"id": "ollama",     "label": "Ollama",              "kind": "local",  "url": "http://localhost:11434/v1"},
    {"id": "lmstudio",   "label": "LM Studio",           "kind": "local",  "url": "http://localhost:1234/v1"},
    {"id": "openrouter", "label": "OpenRouter",          "kind": "cloud",  "url": "https://openrouter.ai/api/v1"},
    {"id": "gemini",     "label": "Google Gemini (API key)", "kind": "cloud", "url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    {"id": "vertex",     "label": "Google Vertex AI (JSON key)", "kind": "vertex", "url": ""},
    {"id": "openai",     "label": "OpenAI",              "kind": "cloud",  "url": "https://api.openai.com/v1"},
    {"id": "deepseek",   "label": "DeepSeek",            "kind": "cloud",  "url": "https://api.deepseek.com"},
    {"id": "groq",       "label": "Groq",                "kind": "cloud",  "url": "https://api.groq.com/openai/v1"},
    {"id": "mistral",    "label": "Mistral",             "kind": "cloud",  "url": "https://api.mistral.ai/v1"},
    {"id": "xai",        "label": "xAI",                 "kind": "cloud",  "url": "https://api.x.ai/v1"},
    {"id": "together",   "label": "Together AI",         "kind": "cloud",  "url": "https://api.together.xyz/v1"},
    {"id": "cerebras",   "label": "Cerebras",            "kind": "cloud",  "url": "https://api.cerebras.ai/v1"},
    {"id": "nvidia",     "label": "NVIDIA",              "kind": "cloud",  "url": "https://integrate.api.nvidia.com/v1"},
    {"id": "huggingface","label": "Hugging Face",        "kind": "cloud",  "url": "https://router.huggingface.co/v1"},
    {"id": "moonshot",   "label": "Moonshot AI",         "kind": "cloud",  "url": "https://api.moonshot.ai/v1"},
    {"id": "nanogpt",    "label": "NanoGPT",             "kind": "cloud",  "url": "https://nano-gpt.com/api/v1"},
    {"id": "custom",     "label": "Custom address",      "kind": "custom", "url": ""},
]
# What Vertex serves today (the sign-in flow needs no /models probe: the
# pass either works or Google says why).
VERTEX_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"]

# api.py wires these once at import: set_key_hook saves the key
# (api_key.txt + the in-memory key + a rebuilt client, chat.setKey); the
# clear hook empties it (a provider without a key may still be local).
# This module must not import chat or api - the hooks keep it decoupled.
set_key_hook = None
clear_key_hook = None

_keeper_lock = threading.Lock()
_keeper_started = False


# ---------- personal files (paths from memory.DATA_DIR AT CALL TIME) ------

def _keys_path() -> str:
    return os.path.join(memory.DATA_DIR, "api_keys.json")


def _provider_path() -> str:
    return os.path.join(memory.DATA_DIR, "llm_provider.txt")


def _read_provider_file() -> str:
    try:
        with open(_provider_path(), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_provider_file(pid: str) -> None:
    os.makedirs(memory.DATA_DIR, exist_ok=True)
    with open(_provider_path(), "w", encoding="utf-8") as f:
        f.write(pid)


# ---------- the table ------------------------------------------------------

def get(pid: str):
    """The table row for a provider id, or None."""
    for row in PROVIDERS:
        if row["id"] == pid:
            return row
    return None


def provider_for_address(address: str) -> str:
    """Which provider this saved server address belongs to.

    '' -> "auto"; a Vertex address -> "vertex"; a table address (case and
    trailing slash ignored) -> its id; the old native Gemini endpoint
    (generativelanguage.googleapis.com/v1beta) -> "gemini"; anything else
    -> "custom".
    """
    address = (address or "").strip().lower().rstrip("/")
    if not address:
        return "auto"
    if vertex_auth.is_vertex_url(address):
        return "vertex"
    for row in PROVIDERS:
        url = (row["url"] or "").lower().rstrip("/")
        if url and url == address:
            return row["id"]
    if "generativelanguage.googleapis.com" in address and address.endswith("/v1beta"):
        return "gemini"
    return "custom"


def active_id() -> str:
    """The provider Amadeus is using right now.

    data/llm_provider.txt wins when it holds a known id that fits the saved
    server address ("custom" fits any non-empty address, "vertex" a Vertex
    address, the others their table address); otherwise the address itself
    decides - which is how existing installs (no provider file yet) map to
    the table.
    """
    address = memory.load_llm_server()
    pid = _read_provider_file()
    if pid and get(pid) is not None:
        if pid == "custom" and address:
            return pid
        if pid == "auto" and not address:
            return pid
        if pid == "vertex" and vertex_auth.is_vertex_url(address):
            return pid
        row = get(pid)
        if row["url"] and address.lower().rstrip("/") == row["url"].lower().rstrip("/"):
            return pid
    return provider_for_address(address)


def address_for(pid: str, address: str = "") -> str:
    """The server address this provider uses: the custom one given (or the
    saved one when it is already active), nothing for auto-detect, Vertex's
    per-project address, or the table address."""
    if get(pid) is None:
        raise ValueError(f"Unknown provider: {pid}")
    if pid == "auto":
        return ""
    if pid == "custom":
        address = (address or "").strip()
        if not address and pid == active_id():
            address = memory.load_llm_server()
        return address
    if pid == "vertex":
        return vertex_auth.info()["address"]
    return get(pid)["url"]


# ---------- keys (per provider, plus "the key in use") ---------------------

def saved_keys() -> dict:
    """{provider_id: key} from api_keys.json (missing or broken -> {})."""
    try:
        with open(_keys_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _write_keys(keys: dict) -> None:
    os.makedirs(memory.DATA_DIR, exist_ok=True)
    with open(_keys_path(), "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)


def key_for(pid: str) -> str:
    """The key to use with this provider: "" for Vertex (its "key" is the
    signed pass, renewed by the keeper); otherwise the per-provider key, or
    - when the provider is the active one and has no key of its own - the
    content of api_key.txt (how existing installs keep their key)."""
    if pid == "vertex":
        return ""
    keys = saved_keys()
    if pid in keys:
        return keys[pid]
    if pid == active_id():
        return memory.load_api_key()
    return ""


def has_key(pid: str) -> bool:
    if pid == "vertex":
        return vertex_auth.info()["configured"]
    return bool(key_for(pid))


def save_provider_key(pid: str, key: str) -> None:
    """Store a plain API key for one provider. Unknown ids, Vertex (it uses
    a JSON key) and empty keys are ValueError. When the provider is the
    active one, the key goes into use right away (the hooks in api.py)."""
    if get(pid) is None:
        raise ValueError(f"Unknown provider: {pid}")
    if pid == "vertex":
        raise ValueError("Google Vertex uses a JSON key, not an API key.")
    key = (key or "").strip()
    if not key:
        raise ValueError("Enter an API key first.")
    keys = saved_keys()
    keys[pid] = key
    _write_keys(keys)
    if pid == active_id():
        _apply_key(key)


def _apply_key(key: str) -> None:
    """Hand a key to the running app through the hooks api.py wires
    (saves api_key.txt, updates the in-memory key, rebuilds the client); an
    empty key goes through the clear hook instead."""
    if key:
        if set_key_hook is not None:
            set_key_hook(key)
    elif clear_key_hook is not None:
        clear_key_hook()
    else:
        memory.save_api_key("")


# ---------- switching providers --------------------------------------------

def activate(pid: str, address: str = "") -> dict:
    """Make one provider the active brain.

    The key in use now is stashed under the OLD provider (its own file), so
    switching back keeps it; then the new address + key are persisted
    (api_key.txt stays the key in use) and the app is pointed at the new
    server. The setup mistakes (custom without an address, Vertex without a
    saved JSON key) are refused BEFORE anything is written, so a failed
    switch changes nothing. ValueError on those mistakes; VertexSignInError
    when signing in to Google fails (after the stash, which is the old
    provider's own key under its own name - it stays usable). Returns
    {"id", "address"}.
    """
    if get(pid) is None:
        raise ValueError(f"Unknown provider: {pid}")
    if pid == "vertex" and not vertex_auth.info()["configured"]:
        raise ValueError("Save your Google Vertex JSON key first.")
    new_address = address_for(pid, address)
    if pid == "custom" and not new_address:
        raise ValueError("Enter the server address first.")
    old = active_id()
    current = memory.load_api_key()
    if old != "vertex" and current:
        keys = saved_keys()
        keys[old] = current
        _write_keys(keys)
    if pid == "vertex":
        key = vertex_auth.token()
    else:
        key = saved_keys().get(pid, "")
    memory.save_llm_server(new_address)
    _write_provider_file(pid)
    _apply_key(key)
    return {"id": pid, "address": new_address}


# ---------- probes (no chat, no model call) --------------------------------

def probe(pid: str, address: str = "") -> dict:
    """{"reachable", "models", "loading", "error", "has_key", "key_checked",
    "catalogue"} for one provider, the same shape llm.test_server returns
    (+ the key facts; catalogue is Vertex-only). Vertex is checked by
    signing in and asking one free question (vertex_auth.check_ready); its
    model list is the saved endpoint's own catalogue when it could be
    loaded (catalogue True), else the fixed VERTEX_MODELS - the catalogue
    over-promises (a listed model can still answer 404), so it is a help
    for choosing, never the proof: that stays the Test button (any_model).
    key_checked = the model list the probe loaded really needed a key (a
    bogus key is refused); a provider that lists models without a key can
    only be checked with a real test message. Unknown provider -> ValueError."""
    row = get(pid)
    if row is None:
        raise ValueError(f"Unknown provider: {pid}")
    if pid == "vertex":
        result = vertex_auth.check_ready()
        if result.get("ok"):
            catalogue = vertex_auth.list_models()
            data = {"reachable": True,
                    "models": catalogue or list(VERTEX_MODELS),
                    "loading": False, "error": None,
                    "catalogue": bool(catalogue)}
        else:
            data = {"reachable": False, "models": [], "loading": False,
                    "error": result.get("error") or "Google Vertex sign-in failed.",
                    "catalogue": False}
        data["has_key"] = has_key(pid)
        data["key_checked"] = False
        return data
    base_url = llm.autodetect_url() if pid == "auto" else address_for(pid, address)
    key = key_for(pid)
    if pid == "custom" and not base_url:
        data = {"reachable": False, "models": [], "loading": False,
                "error": "Enter the server address first."}
        data["has_key"] = has_key(pid)
        data["key_checked"] = False
        return data
    data = llm.test_server(base_url, key)
    data["has_key"] = has_key(pid)
    data["key_checked"] = _key_proves_listing(row["kind"], base_url, key, data)
    return data


def _key_proves_listing(kind: str, base_url: str, key: str, result: dict) -> bool:
    """True when a model list the probe just loaded really needed a key:
    ask the same /models endpoint once with a bogus key. Refused = the good
    list proves the key is right; a provider that lists models with the
    bogus key too (OpenRouter, NVIDIA, Hugging Face, NanoGPT) proves
    nothing, and any other error in this extra check counts as "not
    proven". Only a SUCCESSFUL listing can prove anything (an error reply
    or no models at all is a failure, whatever the key says) - and it is
    only worth trying for cloud providers with a saved key: local servers
    and Vertex take no key, and a keyless listing has nothing to check."""
    if kind != "cloud" or not key or not result.get("reachable"):
        return False
    if result.get("error") or not (result.get("models") or result.get("any_model")):
        return False
    try:
        fake = llm.test_server(base_url, "invalid-key-check", timeout=10.0)
    except Exception:
        return False
    if not fake.get("reachable"):
        return False
    if fake.get("models") or fake.get("any_model"):
        return False
    return True


# ---------- a real test: one message in her own reply format ---------------

def _test_result(ok: bool, outcome: str, message: str, model: str, started: float) -> dict:
    """The Settings "Test" reply - the page shows `message` as is (outcome
    lets it re-use the words; seconds is how long the test took)."""
    return {"ok": ok, "outcome": outcome, "message": message,
            "model": model, "seconds": round(time.time() - started, 1)}


def _http_status(exc):
    """The HTTP status of an API error, or None (network and format errors
    carry no status)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    return status if isinstance(status, int) else None


_NETWORK_ERROR_NAMES = ("APIConnectionError", "APITimeoutError", "ConnectionError",
                        "ConnectTimeout", "ReadTimeout", "Timeout", "TimeoutError")


def _network_error_types():
    """The openai SDK's connection/timeout classes when they are importable
    (older installs may not have them); an empty tuple means "by name only"."""
    try:
        import openai
    except ImportError:
        return ()
    types = []
    for name in ("APIConnectionError", "APITimeoutError"):
        cls = getattr(openai, name, None)
        if isinstance(cls, type):
            types.append(cls)
    return tuple(types)


def _is_network_error(exc) -> bool:
    """Connection refused / a timeout - the server never answered at all.

    The openai SDK may raise its connection errors under a wrapper class or
    a renamed one (and they may carry no status_code), so look three ways:
    by isinstance against the openai classes (when importable), by class
    name anywhere in the type's hierarchy, and by walking the __cause__ /
    __context__ chain of a wrapping exception."""
    network_types = _network_error_types()
    seen = set()
    chain = [exc]
    while chain:
        error = chain.pop()
        if error is None or id(error) in seen:
            continue
        seen.add(id(error))
        if network_types and isinstance(error, network_types):
            return True
        if any(name in _NETWORK_ERROR_NAMES
               for name in (cls.__name__ for cls in type(error).__mro__)):
            return True
        chain.extend((getattr(error, "__cause__", None),
                      getattr(error, "__context__", None)))
    return False


# Account / request ids the provider's raw error bodies carry (user_id,
# request_id, org ids): never shown to the user, cut out below.
_ID_TOKENS_RE = re.compile(r"\b(?:user_[A-Za-z0-9_-]+|org-[A-Za-z0-9_-]+|req_[A-Za-z0-9_-]+)\b")


def _body_message(body):
    """The provider's own sentence from a parsed error body - the openai SDK
    keeps it on .body: {"error": {"message": ...}}, {"message": ...} or a
    list of such objects (Google's shape). None when there is no message."""
    items = body if isinstance(body, list) else [body]
    for item in items:
        if isinstance(item, str):
            return item
        if not isinstance(item, dict):
            continue
        holder = item.get("error")
        message = (holder.get("message") if isinstance(holder, dict)
                   else item.get("message"))
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None


def _test_reason(exc) -> str:
    """A short, plain provider reason for the "error" outcome - just the
    provider's OWN sentence, never its raw body: from the error's parsed
    body when the SDK kept one, else the first 'message': '...' out of the
    error text, else the text itself; then the key, any Bearer value and
    the account ids (user_... / org-... / req_...) are cut out, and the
    result is trimmed to 120 chars - it is shown to the user as is."""
    text = ""
    body = getattr(exc, "body", None)
    if body is not None:
        text = _body_message(body) or ""
    if not text:
        flattened = " ".join(str(exc).split())
        match = re.search(r"['\"]message['\"]\s*[:=]\s*['\"]([^'\"]+)['\"]", flattened)
        text = match.group(1) if match else flattened
    text = re.sub(r"[Bb]earer\s+\S+", "Bearer <key>", text)
    text = re.sub(r"\bsk[-_][A-Za-z0-9._-]{8,}\b", "<key>", text)
    text = _ID_TOKENS_RE.sub("<id>", text)
    return " ".join(text.split())[:120]


def _pack_speaks(pack) -> bool:
    """A usable structured reply: at least one of her lines has a word in it."""
    for field in ("assistant_reply_JPS", "assistant_reply_ENG"):
        text = getattr(pack, field, None)
        if isinstance(text, str) and text.strip():
            return True
    return False


_MODEL_UNKNOWN_WORDS = (
    "not found", "not a valid model", "invalid model", "model not found",
    "model_not_found", "does not exist", "no such model", "unknown model",
    "is not supported",
)


def _says_model_unknown(text: str) -> bool:
    """The error says the MODEL itself is the problem (an unknown model id):
    one of the usual phrases, with the text also mentioning a model."""
    if "model" not in text:
        return False
    return any(word in text for word in _MODEL_UNKNOWN_WORDS)


def _kind_word(kind: str) -> str:
    """The words local and custom servers get say "server" (a custom
    address is usually someone's own server), not "provider"; cloud and
    Vertex keep "provider"."""
    return "server" if kind in ("local", "custom") else "provider"


def _classify_http(exc, model: str, started: float, kind: str,
                  no_key_saved: bool = False) -> dict:
    """One HTTP error from the test call -> the outcome the page shows;
    the same wording checks chat.py uses in her reply ladder. When no key
    was saved (the client sent a placeholder), a refusal means "no key
    saved yet", not "wrong key" - that is the fix, so that is what it says."""
    import chat  # lazy, as in test_model
    who = _kind_word(kind)
    status = _http_status(exc)
    text = str(exc).lower()
    refused = (f"The {who} rejected the request - no key is saved yet."
               if no_key_saved else f"The {who} rejected this key.")
    if status in (401, 403):
        return _test_result(False, "bad_key", refused, model, started)
    if status == 402:
        return _test_result(False, "no_credit",
                            "The provider says this account has no credit or quota left.",
                            model, started)
    if status == 429:
        if chat._is_daily_quota_exhausted(exc) or \
                any(word in text for word in ("quota", "billing", "credit")):
            return _test_result(False, "no_credit",
                                "The provider says this account has no credit or quota left.",
                                model, started)
        return _test_result(False, "rate_limited",
                            "The provider is rate-limiting right now. Try again in a minute.",
                            model, started)
    if status == 404:
        return _test_result(False, "model_missing",
                            f"The {who} does not know the model {model}.", model, started)
    if status in (400, 422):
        if status == 400 and any(word in text for word in ("api key", "api-key", "apikey", "invalid key")):
            return _test_result(False, "bad_key", refused, model, started)
        if _says_model_unknown(text):
            return _test_result(False, "model_missing",
                                f"The {who} does not know the model {model}.", model, started)
    return _test_result(False, "error", f"The test failed: {_test_reason(exc)}", model, started)


def _plain_fallback(client, messages, model: str, started: float, kind: str,
                    no_key_saved: bool = False) -> dict:
    """The structured call came back empty (or its tool format itself
    failed): one plain call tells "the model answers, but not in the form
    Amadeus needs" apart from "the model does not answer at all"."""
    try:
        reply = client.invoke(messages)
    except Exception as exc:
        if _http_status(exc) is not None:
            return _classify_http(exc, model, started, kind, no_key_saved)
        if _is_network_error(exc):
            return _test_result(False, "unreachable", "Could not reach the server.", model, started)
        return _test_result(False, "error", f"The test failed: {_test_reason(exc)}", model, started)
    text = getattr(reply, "content", None)
    if isinstance(text, str) and text.strip():
        return _test_result(False, "wrong_format",
                            "The model answers, but not in the form Amadeus needs.",
                            model, started)
    return _test_result(False, "error",
                        "The test failed: the model did not answer.", model, started)


def test_model(pid: str, address: str = "", model: str = "") -> dict:
    """The Settings "Test" button: one short message through the chosen
    model, in Amadeus's own reply format (the forced tool call her real
    replies use) - so it proves the provider, the key and the model work
    for HER, not just that the model list loads. Nothing is saved or
    changed: no key, provider, model or client of the running app is
    touched. ValueError for an unknown provider id.

    Outcomes (the page shows the matching words): ok, no_model, no_key
    (Vertex without a JSON key - every other provider may be tested with no
    key: the client sends a placeholder, and a refusal then says so),
    bad_key, model_missing, no_credit, rate_limited, wrong_format,
    unreachable, error.
    """
    import chat  # lazy: chat imports llm and api wires providers - a
                 # top-level import here would be a circle
    row = get(pid)
    if row is None:
        raise ValueError(f"Unknown provider: {pid}")
    started = time.time()
    model = (model or "").strip()
    if not model:
        return _test_result(False, "no_model", "Pick a model first.", "", started)
    no_key_saved = False
    if row["kind"] == "vertex":
        info = vertex_auth.info()
        if not info["configured"]:
            return _test_result(False, "no_key", "No JSON key saved yet.", "", started)
        base_url = vertex_auth.base_url(info["project"], info["region"])
        try:
            key = vertex_auth.token()
        except vertex_auth.VertexSignInError as exc:
            return _test_result(False, "bad_key", str(exc)[:160], model, started)
        model = vertex_auth.model_name(model)
    else:
        # Like the probe: "auto" asks autodetect (the first local port that
        # answers), everyone else their table address (or the typed one).
        base_url = (llm.autodetect_url() if pid == "auto" else address_for(pid, address)
                    or "").strip()
        # No key saved: the client sends a placeholder anyway, so the test
        # still goes out - a refusal then means "no key saved yet", not
        # "wrong key" (no_key_saved turns the words that way).
        key = key_for(pid)
        no_key_saved = not key
    if not base_url:
        return _test_result(False, "error",
                            "The test failed: no server address for this provider.",
                            model, started)

    client = llm.client_for(base_url, key, model)
    messages = [
        {"role": "system",
         "content": "You are Amadeus. This is a connection test. Reply with one short greeting."},
        {"role": "user", "content": "Hello"},
    ]
    try:
        result = chat._structured(client, chat.AmadeusPack, messages)
    except Exception as exc:
        if _http_status(exc) is not None:
            return _classify_http(exc, model, started, row["kind"], no_key_saved)
        if _is_network_error(exc):
            return _test_result(False, "unreachable", "Could not reach the server.", model, started)
        # A non-HTTP failure (often the tool format itself): the model may
        # still answer in plain text - one plain call tells the two apart.
        return _plain_fallback(client, messages, model, started, row["kind"],
                               no_key_saved)
    if result is not None and _pack_speaks(result):
        return _test_result(True, "ok",
                            f"Works with Amadeus. {model} replied in {time.time() - started:.1f} s.",
                            model, started)
    return _plain_fallback(client, messages, model, started, row["kind"],
                           no_key_saved)



def listing() -> dict:
    """Everything Settings needs: the table (+ has_key per row), the active
    provider, the saved address, and Vertex's sign-in state (never a key)."""
    return {
        "providers": [dict(row, has_key=has_key(row["id"])) for row in PROVIDERS],
        "active": active_id(),
        "address": memory.load_llm_server(),
        "vertex": vertex_auth.info(),
    }


# ---------- keeping the Vertex pass fresh ----------------------------------

def refresh_vertex_if_due(force: bool = False) -> bool:
    """Only when Vertex is the active provider AND her built-in model is not
    in use: if forced or the pass is older than REFRESH_AFTER, sign in again
    and hand the fresh pass to the app (it differs from the one in
    api_key.txt). A failed sign-in is printed (never with the token) and
    False is returned. True when a new pass was applied.

    While the built-in brain is active the custom pass is never used, so a
    renewal is pointless; the /setBrainMode route forces a fresh pass the
    moment the user switches back to custom mode, so the first chat after
    the switch cannot go out with an expired one."""
    try:
        if active_id() != "vertex":
            return False
        if builtin_llm.active():
            return False
        if not force and vertex_auth.token_age() < vertex_auth.REFRESH_AFTER:
            return False
        token = vertex_auth.token(force=force)
        if not token or token == memory.load_api_key():
            return False
        _apply_key(token)
        return True
    except vertex_auth.VertexSignInError as exc:
        print(f"[Amadeus] Google Vertex sign-in failed: {exc}")
        return False


def start_vertex_keeper(interval: int = 60) -> None:
    """One daemon thread that keeps the active Vertex pass fresh: the first
    round is forced (a pass left in api_key.txt by an earlier run is an
    hour old at best), then a renewal check every interval seconds. The
    thread never raises; a second start is a no-op."""
    global _keeper_started
    with _keeper_lock:
        if _keeper_started:
            return
        _keeper_started = True

    def _loop():
        first = True
        while True:
            try:
                refresh_vertex_if_due(force=first)
                first = False
            except Exception as exc:  # the keeper must never die
                print(f"[Amadeus] Vertex pass refresh skipped: {exc!r}")
            time.sleep(interval)

    threading.Thread(target=_loop, daemon=True, name="vertex-keeper").start()

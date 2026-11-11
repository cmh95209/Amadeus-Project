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
import threading
import time

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
    server. ValueError for the two setup mistakes (custom without an
    address, Vertex without a saved JSON key); VertexSignInError when
    signing in to Google fails. Returns {"id", "address"}.
    """
    if get(pid) is None:
        raise ValueError(f"Unknown provider: {pid}")
    old = active_id()
    current = memory.load_api_key()
    if old != "vertex" and current:
        keys = saved_keys()
        keys[old] = current
        _write_keys(keys)
    if pid == "vertex" and not vertex_auth.info()["configured"]:
        raise ValueError("Save your Google Vertex JSON key first.")
    new_address = address_for(pid, address)
    if pid == "custom" and not new_address:
        raise ValueError("Enter the server address first.")
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
    """{"reachable", "models", "loading", "error"} for one provider, the
    same shape llm.test_server returns. Vertex is checked by signing in and
    asking one free question (vertex_auth.check_ready); the model list is
    the fixed VERTEX_MODELS. Unknown provider -> ValueError."""
    if get(pid) is None:
        raise ValueError(f"Unknown provider: {pid}")
    if pid == "vertex":
        result = vertex_auth.check_ready()
        if result.get("ok"):
            return {"reachable": True, "models": list(VERTEX_MODELS),
                    "loading": False, "error": None}
        return {"reachable": False, "models": [], "loading": False,
                "error": result.get("error") or "Google Vertex sign-in failed."}
    if pid == "auto":
        return llm.test_server(llm.autodetect_url(), key_for(pid))
    address = address_for(pid, address)
    if pid == "custom" and not address:
        return {"reachable": False, "models": [], "loading": False,
                "error": "Enter the server address first."}
    return llm.test_server(address, key_for(pid))


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
    """Only when Vertex is the active provider: if forced or the pass is
    older than REFRESH_AFTER, sign in again and hand the fresh pass to the
    app (it differs from the one in api_key.txt). A failed sign-in is
    printed (never with the token) and False is returned. True when a new
    pass was applied."""
    try:
        if active_id() != "vertex":
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

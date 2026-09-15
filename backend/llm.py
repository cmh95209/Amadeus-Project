import socket
from pathlib import Path

import requests
from langchain_openai import ChatOpenAI

import memory

# =============================================================================
#  Amadeus -> YOUR local model (Unsloth Desktop)
#
#  This file is what makes Amadeus talk to your local model instead of
#  OpenRouter. You do NOT need to understand it - just keep it in place.
#
#  The address of your model server is read from the small text file
#  "llm_server.txt" that sits right next to this file (in the backend/ folder).
#  Unsloth picks a new port whenever it restarts, so if the saved address is
#  not answering we automatically probe the usual ports (8888, 8000).
#
# =============================================================================

_HERE = Path(__file__).resolve().parent
_SERVER_FILE = _HERE / "llm_server.txt"

# Used only if llm_server.txt does not exist and no live port is found:
DEFAULT_SERVER_URL = "http://localhost:8000/v1"

# Ports Unsloth commonly boots on.
CANDIDATE_PORTS = (8888, 8000)


def _port_alive(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _server_url() -> str:
    # 1) What the user's file says (remember it, but check if it answers).
    configured = ""
    try:
        if _SERVER_FILE.exists():
            configured = _SERVER_FILE.read_text(encoding="utf-8").strip().rstrip("/")
    except Exception:
        pass

    candidates = []
    if configured:
        candidates.append(configured)
    for port in CANDIDATE_PORTS:
        url = f"http://localhost:{port}/v1"
        if url not in candidates:
            candidates.append(url)

    # 2) Pick the first candidate whose port is open. If none is open, fall
    #    back to the configured/known URL so the usual error still surfaces.
    for url in candidates:
        host_port = url.split("//", 1)[-1].split("/")[0]
        host, _, port_s = host_port.partition(":")
        try:
            if _port_alive(host or "localhost", int(port_s or 80)):
                return url
        except ValueError:
            continue
    return configured or DEFAULT_SERVER_URL


# One cached client per (model, thinking) pair, so a thinking-enabled client
# can coexist with the normal fast one. The address (port) each client was
# built against is tracked separately, because Unsloth picks a NEW port
# whenever it restarts. If the port moves, we must rebuild the client instead
# of keeping the old one - otherwise every request goes to a dead address and
# hangs until its timeout (the "first message stalls, restart fixes it" bug).
_LLM_CACHE = {}
_LLM_CACHE_URL = {}
_LLM_CACHE_PARAMS = {}   # cache key -> list of user sampling params that client sends

# (base_url, param) -> True: this server proved it rejects that sampling
# parameter with HTTP 400. In-memory only (session-scoped), so switching to a
# different server later starts with a clean slate.
_REJECTED = {}


def reset_llm():
    """Force Amadeus to rebuild its connections (e.g. after a model/key change)."""
    _LLM_CACHE.clear()
    _LLM_CACHE_URL.clear()
    _LLM_CACHE_PARAMS.clear()


def _is_local_host(base_url: str) -> bool:
    """True for localhost / private-LAN addresses (vs. a public cloud host)."""
    host = base_url.split("//", 1)[-1].split("/")[0].split(":")[0].lower()
    return (
        host in ("localhost", "127.0.0.1", "::1")
        or host.startswith(("192.168.", "10.", "172.16."))
    )


def _is_gemini_host(base_url: str) -> bool:
    """True for Google's Gemini API (the OpenAI-compat layer)."""
    return "generativelanguage.googleapis.com" in base_url


def test_server(base_url: str, api_key: str = "", timeout: float = 5.0) -> dict:
    """Probe an OpenAI-compatible server's standard /models endpoint.

    Works for any local server (Unsloth, Ollama, llama.cpp, LM Studio, vLLM)
    or cloud provider (OpenRouter, OpenAI). Returns:
    {"reachable": bool, "models": [model ids], "error": str or None}
    """
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {(api_key or '').strip() or 'unsloth'}"}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return {"reachable": False, "models": [],
                "error": f"{type(exc).__name__}: {exc}"}
    if response.status_code == 401 or response.status_code == 403:
        return {"reachable": True, "models": [],
                "error": f"Server is running but rejected the API key (HTTP {response.status_code})"}
    if response.status_code >= 400:
        return {"reachable": True, "models": [],
                "error": f"Server responded with HTTP {response.status_code}"}
    try:
        data = response.json()
    except ValueError:
        return {"reachable": True, "models": [], "error": "Server reply was not JSON"}
    models = []
    for item in (data.get("data") if isinstance(data, dict) else None) or []:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str):
            models.append(mid)
    return {"reachable": True, "models": models, "error": None}


def _effective_sampling(base_url: str, enable_thinking: bool):
    """(standard_kwargs, extra_body_kwargs, sent_param_names) for this server,
    from the user's Model Sampling settings.

    - Disabled parameters (the default) are NOT sent: the server's own default
      applies, which is exactly what Amadeus did before this feature existed.
    - top_k / min_p / repetition_penalty are local-server-only: strict cloud
      compat layers (notably Gemini's) answer HTTP 400 to unknown fields.
    - Parameters this host has proven it rejects (see
      maybe_strip_rejected_params) are skipped for the rest of the app session.
    - The thinking client keeps a 4096 max_tokens floor: reasoning tokens count
      against the output cap, so a smaller user cap would clip every
      deep-thinking reply mid-reason.
    """
    local = _is_local_host(base_url)
    standard, extra = {}, {}
    for name, entry in memory.load_sampling().items():
        if not entry.get("enabled") or entry.get("value") is None:
            continue
        if name in memory.SAMPLING_LOCAL_ONLY and not local:
            continue
        if _REJECTED.get((base_url, name)):
            continue
        value = entry["value"]
        if name == "max_tokens":
            if enable_thinking and int(value) < 4096:
                continue  # keep the built-in 4096 floor for reasoning
            standard["max_tokens"] = int(value)
        elif name in memory.SAMPLING_LOCAL_ONLY:
            extra[name] = value
        else:
            standard[name] = value
    return standard, extra, list(standard) + list(extra)


def rejected_sampling_params(base_url: str) -> list:
    """Sampling params this host rejected during this app session (the
    Settings UI notes them so the user is never flying blind)."""
    return sorted(name for (url, name) in _REJECTED if url == base_url)


_PARAM_REJECT_WORDS = ("unrecognized", "unexpected", "unknown", "invalid",
                       "unsupported", "not supported")
_PARAM_FIELD_WORDS = ("field", "parameter", "property", "argument")


def maybe_strip_rejected_params(exc, model: str) -> bool:
    """Called from chat.py when an LLM call fails.

    If the server rejected one of the sampling parameters we sent (HTTP 400/422
    naming the field, or a generic "unknown field" complaint), remember the
    rejection for this host, force a client rebuild without it, and return
    True so the caller can retry cleanly once. Any other error returns False
    and the caller continues its normal fallback ladder.
    """
    base_url, sent = None, set()
    for key, params in _LLM_CACHE_PARAMS.items():
        if key[0] == model:
            base_url = _LLM_CACHE_URL.get(key) or base_url
            sent.update(params)
    if not base_url or not sent:
        return False
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return False
    text = str(exc).lower()
    if not any(w in text for w in _PARAM_REJECT_WORDS):
        return False
    named = {n for n in sent if n in text}
    if not named and not any(w in text for w in _PARAM_FIELD_WORDS):
        return False  # a 400 about something else; the normal ladder handles it
    victims = named if named else set(sent)
    for name in victims:
        _REJECTED[(base_url, name)] = True
    print(f"[Amadeus] {base_url} rejected sampling parameter(s) {sorted(victims)}; "
          f"disabling them for this app session.")
    reset_llm()
    return True


def get_llm(api_key: str, model: str, enable_thinking: bool = False):
    """Build (once per model + thinking + sampling settings) and return a
    client that talks
    to your local Unsloth model.

    enable_thinking=True turns Qwen3's internal reasoning ON for that client.
    Amadeus uses it only for the web-search judgement call (deciding whether a
    message references something she should verify); the final reply always
    comes from the normal thinking-off client to stay fast and reliable.
    """
    # Re-probe the local server on every call. When the saved port is alive
    # this is a single fast localhost check; it only matters when the port
    # has changed. If the address moved since we built the client, rebuild it
    # so we never keep talking to a dead port.
    base_url = _server_url()

    # The user's Model Sampling settings are part of the client identity:
    # changing them rebuilds the client, so new values apply to the next
    # message without an app restart.
    sampling_standard, sampling_extra, sent_params = _effective_sampling(base_url, enable_thinking)
    key = (model, bool(enable_thinking),
           tuple(sorted(sampling_standard.items())),
           tuple(sorted(sampling_extra.items())))

    cached = _LLM_CACHE.get(key)
    if cached is not None and _LLM_CACHE_URL.get(key) == base_url:
        return cached

    # Local servers still expect *some* key. If Amadeus has none stored yet,
    # send a placeholder - you paste the real one into Amadeus Settings.
    resolved_key = (api_key or "").strip() or "unsloth"

    # The enable_thinking flag is a Qwen3/llama.cpp "chat template" feature.
    # Local servers (Unsloth, vLLM, ...) understand it; public cloud providers
    # may reject unknown body fields, so it is only sent for local/LAN hosts.
    local = _is_local_host(base_url)

    if enable_thinking:
        # The thinking client is BOUNDED on purpose: a hard cap on tokens stops
        # runaway reasoning, and a shorter timeout means a slow or hung
        # reasoning call fails over to the normal client quickly instead of
        # stalling the whole reply for many minutes.
        kwargs = {
            "model": model,
            "api_key": resolved_key,
            "base_url": base_url,
            "timeout": 240,
            "max_retries": 1,
            "max_tokens": 4096,
        }
        extra_body = dict(sampling_extra)
        if local:
            # Qwen3/llama.cpp chat-template flag for local servers.
            extra_body["chat_template_kwargs"] = {"enable_thinking": True}
        elif _is_gemini_host(base_url):
            # Gemini 3 models take thinking level via the compat layer's
            # reasoning_effort field (verified live 2026-09-14: "high" runs
            # ~1000 internal reasoning tokens, "low" ~270; the google-scoped
            # thinking_config shape now 400s). The reasoning happens
            # server-side and stays INTERNAL - content (and therefore her
            # UI/voice output) comes back clean. Models that don't support
            # the parameter (e.g. Gemma on the Gemini API) get an HTTP 400,
            # which the caller's existing fallback already handles.
            extra_body["reasoning_effort"] = "high"
        if extra_body:
            kwargs["extra_body"] = extra_body
    else:
        kwargs = {
            "model": model,
            "api_key": resolved_key,
            "base_url": base_url,
            # Local 27B-class models can be SLOW on a cold start: the first real
            # message after a restart prefills her full persona + tool
            # definitions, which can take several minutes before the first token.
            # So this timeout is a GENERATION ceiling, not a "server is dead"
            # probe - a truly dead server is caught in ~1.5s by the port-alive
            # check above, while a slow-but-working reply is allowed to finish
            # instead of being clipped at 120s. If it genuinely never completes,
            # chat.py surfaces a clean error. Tune this single number if you want
            # a shorter/longer ceiling (e.g. 300 = 5 minutes).
            "timeout": 600,
            "max_retries": 0,
            # Bound the OUTPUT length. Without a cap, a model stuck in a
            # repetition loop (a known Gemma failure mode when it cannot satisfy
            # a forced tool call) would generate until the full timeout - i.e.
            # appear to hang for ~10 minutes. Real replies are 150-300 tokens,
            # so 1024 is far more than she needs, and a runaway/stuck
            # generation now stops at a quarter of the old worst case instead
            # of an endless spin.
            "max_tokens": 1024,
        }
        extra_body = dict(sampling_extra)
        if local:
            extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        if extra_body:
            kwargs["extra_body"] = extra_body

    # User sampling settings (temperature / top_p / presence_penalty, and the
    # user's own output cap when set) override the built-in defaults above.
    kwargs.update(sampling_standard)

    _LLM_CACHE[key] = ChatOpenAI(**kwargs)
    _LLM_CACHE_URL[key] = base_url
    _LLM_CACHE_PARAMS[key] = sent_params
    return _LLM_CACHE[key]

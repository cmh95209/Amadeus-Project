import socket
from pathlib import Path

import requests
from langchain_openai import ChatOpenAI

import builtin_llm
import memory
import timing

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


def _configured_server_url() -> str:
    """The server the user set (or her built-in one), WITHOUT probing ports -
    enough to tell a cloud service from a local one, and instant."""
    if builtin_llm.active():
        return builtin_llm.server_url()
    try:
        if _SERVER_FILE.exists():
            return _SERVER_FILE.read_text(encoding="utf-8").strip().rstrip("/") or DEFAULT_SERVER_URL
    except Exception:
        pass
    return DEFAULT_SERVER_URL


def _server_url() -> str:
    # 0) Built-in mode: only the launcher's own llama-server - never another
    #    local server found on the usual ports (see builtin_llm).
    if builtin_llm.active():
        return builtin_llm.server_url()

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
# Output cap for her built-in model's normal client. Her longest measured
# reply was ~950 pieces (both lines); a Gemma loop that hits the cap now costs
# ~12 s on a fast card instead of over a minute (Run 1, 2026-10-19: 2 x 4,096
# pieces, 66 s). Cloud / custom models keep 4096.
BUILTIN_MAX_TOKENS = 1536

_LLM_CACHE = {}
_LLM_CACHE_URL = {}
_LLM_CACHE_PARAMS = {}   # cache key -> list of user sampling params that client sends

# (base_url, param) -> True: this server proved it rejects that sampling
# parameter with HTTP 400. In-memory only (session-scoped), so switching to a
# different server later starts with a clean slate.
_REJECTED = {}


# Reasoning levels (Settings -> Connection -> Reasoning, MH 2026-10-20).
# "off" = no thinking, or the lowest level a model accepts (some cloud models,
# e.g. Gemini 3.8 Flash on OpenRouter, refuse to switch it off completely).
REASONING_LEVELS = ("off", "low", "medium", "high")
# Local servers (llama.cpp): a per-request thinking budget in pieces. Without
# one Gemma 4 thought up to ~2,300 pieces (18 s) in the 2026-10-20 test.
THINKING_BUDGETS = {"low": 512, "medium": 1024, "high": 2048}


def _reasoning_fallback(extra_body, exc):
    """A softer reasoning setting after the server refused ours (HTTP 400/422
    mentioning reasoning/thinking), or None when this error is something else.
    OpenRouter, Gemini 3.8 Flash: "Reasoning is mandatory for this endpoint and
    cannot be disabled." (2026-10-20) - "minimal" works there."""
    if getattr(exc, "status_code", None) not in (400, 422):
        return None
    text = str(exc).lower()
    if "reasoning" not in text and "thinking" not in text:
        return None
    extra = dict(extra_body or {})
    if isinstance(extra.get("reasoning"), dict):
        current = extra["reasoning"]
        if current.get("enabled") is False:
            extra["reasoning"] = {"effort": "minimal"}
        elif current.get("effort") == "minimal":
            extra["reasoning"] = {"effort": "low"}
        else:
            extra.pop("reasoning")
        return extra
    if "reasoning_effort" in extra:
        if extra["reasoning_effort"] == "minimal":
            extra["reasoning_effort"] = "low"
        else:
            extra.pop("reasoning_effort")
        return extra
    return None


class _ThoughtFreeChatOpenAI(ChatOpenAI):
    """ChatOpenAI whose replies never carry an inline Gemma 4 thought block
    (builtin_llm.strip_thoughts). Every Amadeus call - chat, greetings,
    tools, translations - goes through this one place."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # A server that refuses our reasoning setting gets one retry with a
        # softer one; the client keeps it for the rest of the session.
        try:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as exc:
            softer = _reasoning_fallback(self.extra_body, exc)
            if softer is None:
                raise
            print(f"[Amadeus] Server refused the reasoning setting {self.extra_body!r} - "
                  f"retrying with {softer!r}.")
            self.extra_body = softer or None
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _create_chat_result(self, response, generation_info=None):
        # Speed log: hand the server's own report (llama-server "timings" /
        # cloud "usage") to the stopwatch. Never lets a failure through.
        timing.note_server_report(response)
        result = super()._create_chat_result(response, generation_info)
        for gen in result.generations:
            message = getattr(gen, "message", None)
            if message is not None and isinstance(message.content, str):
                cleaned = builtin_llm.strip_thoughts(message.content)
                if cleaned != message.content:
                    message.content = cleaned
                    if hasattr(gen, "text"):
                        gen.text = cleaned
        return result


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


def reasoning_levels() -> dict:
    """The three Reasoning choices with defaults applied (Settings shows these):
    replies_builtin "off", replies_custom "low" on a cloud host / "off" on a
    local or LAN server, translations "off" (MH 2026-10-20)."""
    saved = memory.load_reasoning()
    custom_default = "off" if _is_local_host(_configured_server_url()) else "low"
    return {
        "replies_builtin": saved.get("replies_builtin", "off"),
        "replies_custom": saved.get("replies_custom", custom_default),
        "translations": saved.get("translations", "off"),
    }


def reasoning_for(kind: str) -> str:
    """The level for one kind of call right now: "replies" (her replies, the
    web-search decision, greetings) follows the active brain; "translations"
    (English line, repair passes) is one setting. Anything else: "off"."""
    try:
        levels = reasoning_levels()
    except Exception:
        return "off"
    if kind == "translations":
        return levels["translations"]
    if kind == "replies":
        return levels["replies_builtin"] if builtin_llm.active() else levels["replies_custom"]
    return "off"


def is_local_server(client) -> bool:
    """True when this client talks to a server on this PC / the LAN (her
    built-in model, the user's own llama-server, ...)."""
    return _is_local_host(str(getattr(client, "openai_api_base", None) or ""))


def _is_openrouter_host(base_url: str) -> bool:
    return "openrouter.ai" in base_url


def _is_gemini_host(base_url: str) -> bool:
    """True for Google's Gemini API (the OpenAI-compat layer)."""
    return "generativelanguage.googleapis.com" in base_url


def test_server(base_url: str, api_key: str = "", timeout: float = 5.0) -> dict:
    """Probe an OpenAI-compatible server's standard /models endpoint.

    Works for any local server (Unsloth, Ollama, llama.cpp, LM Studio, vLLM)
    or cloud provider (OpenRouter, OpenAI). Returns:
    {"reachable": bool, "models": [model ids], "error": str or None,
     "loading": bool}
    "loading" is True when the server is up but says it is still loading the
    model: HTTP 503 (llama.cpp-style servers answer that while loading) or an
    error reply whose text mentions loading. Servers that load on the first
    request (Ollama, LM Studio) or are unreachable until ready (vLLM) never
    report it - the WebUI covers those with timing.
    """
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {(api_key or '').strip() or 'unsloth'}"}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return {"reachable": False, "models": [], "loading": False,
                "error": f"{type(exc).__name__}: {exc}"}
    if response.status_code == 401 or response.status_code == 403:
        return {"reachable": True, "models": [], "loading": False,
                "error": f"Server is running but rejected the API key (HTTP {response.status_code})"}
    if response.status_code >= 400:
        try:
            body_text = (response.text or "")[:500].lower()
        except Exception:
            body_text = ""
        if response.status_code == 503 or "loading" in body_text:
            return {"reachable": True, "models": [], "loading": True,
                    "error": "Server is still loading the model"}
        return {"reachable": True, "models": [], "loading": False,
                "error": f"Server responded with HTTP {response.status_code}"}
    try:
        data = response.json()
    except ValueError:
        return {"reachable": True, "models": [], "loading": False,
                "error": "Server reply was not JSON"}
    models = []
    for item in (data.get("data") if isinstance(data, dict) else None) or []:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str):
            models.append(mid)
    return {"reachable": True, "models": models, "loading": False, "error": None}


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


def get_llm(api_key: str, model: str, enable_thinking: bool = False, reasoning: str = None):
    """Build (once per model + thinking + sampling settings) and return a
    client that talks
    to your local Unsloth model.

    reasoning = one of REASONING_LEVELS (Settings -> Reasoning: her replies /
    translations; helper calls use "off"). enable_thinking=True is the old
    shape and means "medium".
    """
    # Re-probe the local server on every call. When the saved port is alive
    # this is a single fast localhost check; it only matters when the port
    # has changed. If the address moved since we built the client, rebuild it
    # so we never keep talking to a dead port.
    base_url = _server_url()
    if builtin_llm.active():
        model = builtin_llm.model_name()

    # The user's Model Sampling settings are part of the client identity:
    # changing them rebuilds the client, so new values apply to the next
    # message without an app restart.
    # reasoning: one of REASONING_LEVELS. None = the old call shape:
    # enable_thinking=True means "medium", otherwise "off" (helper calls).
    if reasoning not in REASONING_LEVELS:
        reasoning = "medium" if enable_thinking else "off"
    thinking = reasoning != "off"
    sampling_standard, sampling_extra, sent_params = _effective_sampling(base_url, thinking)
    key = (model, reasoning,
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
    reply_cap = BUILTIN_MAX_TOKENS if builtin_llm.active() else 4096

    if thinking:
        # The thinking client is BOUNDED on purpose: a hard cap on tokens stops
        # runaway reasoning, and a shorter timeout means a slow or hung
        # reasoning call fails over quickly instead of stalling the whole
        # reply for many minutes. Thinking counts against the output cap
        # (local AND cloud), so the cap is her normal room PLUS the budget.
        budget = THINKING_BUDGETS[reasoning]
        kwargs = {
            "model": model,
            "api_key": resolved_key,
            "base_url": base_url,
            "timeout": 240,
            "max_retries": 1,
            "max_tokens": reply_cap + budget if builtin_llm.active() else max(4096, reply_cap + budget),
        }
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
            # Bound the OUTPUT length. Two jobs: (1) without a cap, a model
            # stuck in a repetition loop (a known Gemma failure mode when it
            # cannot satisfy a forced tool call) would generate until the full
            # timeout - i.e. appear to hang for ~10 minutes; a cap stops the
            # spin. (2) Top-tier cloud models write long structured replies
            # (English line + Japanese line + fields), and 1024 proved too
            # small in the field: a reply clipped mid-JSON decoded to nothing
            # and surfaced as "no usable reply" (fresh-VM incident, 2026-10-08).
            # 4096 gives real headroom for long replies - the same floor the
            # thinking client already uses - while still bounding a runaway.
            # Her built-in model: BUILTIN_MAX_TOKENS (see there).
            "max_tokens": reply_cap,
        }
    extra_body = dict(sampling_extra)
    if local:
        # llama.cpp / Qwen3-style chat-template flag + a per-request thinking
        # budget (llama-server b11400 honours thinking_budget_tokens; the
        # thoughts come back apart from the reply - --reasoning-format).
        extra_body["chat_template_kwargs"] = {"enable_thinking": thinking}
        if thinking:
            extra_body["thinking_budget_tokens"] = THINKING_BUDGETS[reasoning]
    elif _is_openrouter_host(base_url):
        # OpenRouter's unified field. "minimal" = the lowest level; Gemini 3.8
        # Flash refuses to switch reasoning off completely (2026-10-20).
        extra_body["reasoning"] = {"effort": reasoning if thinking else "minimal"}
    elif _is_gemini_host(base_url):
        # Gemini 3 models take thinking level via the compat layer's
        # reasoning_effort field (verified live 2026-09-14: "high" runs ~1000
        # internal reasoning tokens, "low" ~270; the google-scoped
        # thinking_config shape now 400s). The reasoning happens server-side
        # and stays INTERNAL - content (and therefore her UI/voice output)
        # comes back clean. A refusal falls back (_reasoning_fallback).
        extra_body["reasoning_effort"] = reasoning if thinking else "minimal"
    elif thinking:
        # Other cloud services: the common OpenAI-style field; a server that
        # rejects it is handled by maybe_strip_rejected_params / fallback.
        extra_body["reasoning_effort"] = reasoning
    if extra_body:
        kwargs["extra_body"] = extra_body

    # User sampling settings (temperature / top_p / presence_penalty, and the
    # user's own output cap when set) override the built-in defaults above.
    kwargs.update(sampling_standard)

    _LLM_CACHE[key] = _ThoughtFreeChatOpenAI(**kwargs)
    _LLM_CACHE_URL[key] = base_url
    _LLM_CACHE_PARAMS[key] = sent_params
    return _LLM_CACHE[key]

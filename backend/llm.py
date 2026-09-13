import socket
from pathlib import Path

import requests
from langchain_openai import ChatOpenAI

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


def reset_llm():
    """Force Amadeus to rebuild its connections (e.g. after a model/key change)."""
    _LLM_CACHE.clear()
    _LLM_CACHE_URL.clear()


def _is_local_host(base_url: str) -> bool:
    """True for localhost / private-LAN addresses (vs. a public cloud host)."""
    host = base_url.split("//", 1)[-1].split("/")[0].split(":")[0].lower()
    return (
        host in ("localhost", "127.0.0.1", "::1")
        or host.startswith(("192.168.", "10.", "172.16."))
    )


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


def get_llm(api_key: str, model: str, enable_thinking: bool = False):
    """Build (once per model + thinking setting) and return a client that talks
    to your local Unsloth model.

    enable_thinking=True turns Qwen3's internal reasoning ON for that client.
    Amadeus uses it only for the web-search judgement call (deciding whether a
    message references something she should verify); the final reply always
    comes from the normal thinking-off client to stay fast and reliable.
    """
    key = (model, bool(enable_thinking))

    # Re-probe the local server on every call. When the saved port is alive
    # this is a single fast localhost check; it only matters when the port
    # has changed. If the address moved since we built the client, rebuild it
    # so we never keep talking to a dead port.
    base_url = _server_url()
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
        if local:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
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
        }
        if local:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    _LLM_CACHE[key] = ChatOpenAI(**kwargs)
    _LLM_CACHE_URL[key] = base_url
    return _LLM_CACHE[key]

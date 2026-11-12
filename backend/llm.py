import ipaddress
import os
import socket
import threading
import time
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


def autodetect_url() -> str:
    """The first usual local port that answers (Settings -> provider "Local
    (auto-detect)"), ignoring the saved address; the default when none does."""
    for port in CANDIDATE_PORTS:
        if _port_alive("localhost", port):
            return f"http://localhost:{port}/v1"
    return DEFAULT_SERVER_URL


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


# ---------------------------------------------------------------------------
#  Cloud servers stall: "stalled? send it again" (owner's rule, 2026-11-12)
#
#  Measured on the Google Vertex address (aiplatform.googleapis.com, the
#  "global" region): identical tiny requests answered in ~2.5 s, or only
#  after 16 / 26 / 45 / 71 s, sometimes not within 200 s - always as a normal
#  HTTP 200, no 429, no error. A second identical request sent while the
#  first one hangs usually answers in 2-3 s. The rule is applied to EVERY
#  cloud server - any address that is NOT localhost / private LAN /
#  link-local / .local (the _is_local_host check; her built-in model is
#  127.0.0.1, so the same check excludes it) - and to none of those local
#  servers: one graphics card, a second copy of the request only makes both
#  slower, and slow answers are normal there. _ThoughtFreeChatOpenAI._generate
#  therefore re-sends a stalled cloud request once, after the patience
#  below; AMADEUS_CLOUD_PATIENCE (seconds) overrides it for tests and
#  support - 0 or less switches the re-send off.
# ---------------------------------------------------------------------------

def _prompt_chars(messages) -> int:
    """Total characters of all message contents (the small/large prompt
    split for the patience): plain strings and dicts, langchain message
    objects, and multimodal content lists (their text parts count)."""
    total = 0
    for message in messages or []:
        content = message
        if not isinstance(message, (str, dict, list)):
            content = getattr(message, "content", message)
        if isinstance(content, list):
            text = []
            for part in content:
                text.append(part.get("text", "") if isinstance(part, dict) else str(part))
            content = "".join(text)
        elif isinstance(content, dict):
            # A dict message ("content" key) or a content dict ("text" key).
            content = content.get("content", content.get("text", ""))
        total += len(str(content or ""))
    return total


def _effort_level(extra_body) -> str:
    """The reasoning level this client sends, in whatever shape _build_client
    wrote it for the host (Settings -> Reasoning): most cloud hosts put it
    in extra_body["reasoning_effort"] (OpenAI, Gemini API, Vertex: "low",
    "medium", "high" - the Gemini API also "minimal"); OpenRouter puts it in
    extra_body["reasoning"] = {"effort": ...} (or {"enabled": False} when
    thinking is off). Absent or in an unknown shape -> "" (the patience
    table then waits like "off")."""
    if not isinstance(extra_body, dict):
        return ""
    reasoning = extra_body.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("enabled") is False:
            return "off"
        effort = reasoning.get("effort")
        return str(effort).lower() if isinstance(effort, str) else ""
    effort = extra_body.get("reasoning_effort")
    return str(effort).lower() if isinstance(effort, str) else ""


def _cloud_patience(messages, effort) -> float:
    """Seconds to wait for a CLOUD model server before the request is sent
    again (local / LAN servers never wait - see the note above). 0 = the
    re-send is off (the call behaves exactly as before).
    Small prompt (under 8,000 characters of message content - her English
    line, a helper call): 12 s; a large prompt (her full reply): 30 s.
    Thinking takes longer: off/minimal/low x1, medium x2, high x3 (the
    client's own reasoning level, from Settings -> Reasoning - read from
    whatever shape the host uses, _effort_level; a level the table does not
    know waits like "off"). A thinking client is built with its own 240 s
    timeout, but every cloud thinking client carries its level in its
    extra_body (each per-host field in _build_client), so no separate
    handling is needed. AMADEUS_CLOUD_PATIENCE (seconds) overrides the whole
    table; 0 or less (or junk) = off."""
    override = os.environ.get("AMADEUS_CLOUD_PATIENCE")
    if override is not None:
        try:
            seconds = float(override)
        except ValueError:
            seconds = 0.0
        return seconds if seconds > 0 else 0.0
    base = 12.0 if _prompt_chars(messages) < 8000 else 30.0
    mult = {"medium": 2, "high": 3}.get(str(effort or "").lower(), 1)
    return base * mult


# The label of her "right now" note (chat.py prompt order B: the blocks that
# change every reply - time, closeness, memories - sent AFTER the newest user
# message, so everything before it stays identical between replies).
# Must equal chat._RIGHT_NOW_LABEL (test_prompt_order checks it); the wording
# was measured, see there.
RIGHT_NOW_LABEL = ("RIGHT NOW (what you know at this moment - use it freely; "
                   "the user did not write this):\n\n")


def _right_now_note_as_user(messages):
    """Cloud servers only: the "right now" note goes out as a USER message.

    Prompt caching (2026-11-12, measured on OpenRouter / Gemini 3.8 Flash):
    a cloud provider reuses the earlier part of a prompt at a discount, but
    Gemini folds EVERY system message - also one at the end - into its single
    instruction block, so a note that changes each reply made the whole
    prompt new every time (0% reused). Sent as a user message at the end, the
    instruction block and the chat before it stay identical: 55-68% reused,
    about half the price per reply. Inside the app the note stays a system
    message, so the code that looks for the user's newest message (pictures,
    search intent, links) never mistakes the note for it. Only a system
    message AFTER the first non-system one that starts with the label is
    changed; everything else is passed through untouched."""
    from langchain_core.messages import HumanMessage
    out, leading = [], True
    for m in messages:
        if getattr(m, "type", "") != "system":
            leading = False
        elif (not leading and isinstance(m.content, str)
              and m.content.startswith(RIGHT_NOW_LABEL)):
            m = HumanMessage(content=m.content)
        out.append(m)
    return out


class _ThoughtFreeChatOpenAI(ChatOpenAI):
    """ChatOpenAI whose replies never carry an inline Gemma 4 thought block
    (builtin_llm.strip_thoughts). Every Amadeus call - chat, greetings,
    tools, translations - goes through this one place (the app sends no
    streaming or async calls through this client anywhere in backend/ - only
    .invoke() - so _generate is the single entry point; _stream/async are
    left alone).

    Cloud servers stall random requests for tens of seconds (measured 2026-11-12
    on the Google Vertex global endpoint: identical tiny requests answered in
    ~2.5 s or only after 16 / 26 / 45 / 71 s, always a normal HTTP 200; a
    second identical request sent during the stall usually answers in 2-3 s).
    So for every client whose server is NOT local (not localhost / private
    LAN / link-local / .local - the same _is_local_host check the client
    building uses, which also covers her built-in model at 127.0.0.1),
    _generate re-sends a stalled request once (see _cloud_patience): the
    call runs in a worker thread, is waited on for a patience limit, and when
    it is still silent the SAME request goes out a second time; whichever of
    the two answers first wins, and a third attempt is never started. A local
    or LAN server keeps today's exact behaviour: one attempt, the calling
    thread, no waiting (a second copy would only slow both copies down)."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Cloud servers: her "right now" note goes out as a user message, so
        # the prompt start stays the same between replies (prompt cache -
        # _right_now_note_as_user). Done once, before any re-send, so both
        # attempts send the same request.
        if not _is_local_host(str(getattr(self, "openai_api_base", None) or "")):
            messages = _right_now_note_as_user(messages)
        # `run_manager` is passed through to the worker thread as today. The
        # app never attaches callbacks to these clients (it does not rely on
        # streaming callbacks anywhere), so LangChain's empty run manager is
        # harmless to call from another thread.
        # The re-send rule covers every CLOUD server - the address is NOT
        # localhost / private LAN / link-local / .local (it covers all cloud
        # providers and a custom internet address, and her built-in model's
        # 127.0.0.1 stays local by the same check). The client's own
        # reasoning level (Settings -> Reasoning) is read from whatever shape
        # the host uses: thinking legitimately takes longer, so it waits
        # longer.
        patience = 0.0
        if not _is_local_host(str(self.openai_api_base or "")):
            patience = _cloud_patience(messages, _effort_level(self.extra_body))
        if patience <= 0:
            # A local / LAN server - or AMADEUS_CLOUD_PATIENCE is 0/negative
            # and the re-send is switched off for support. Exactly today's
            # behaviour: one attempt in this thread, no waiting (the client
            # is not shared, so the fallback may mutate it as it always has).
            committed = {}
            try:
                result = self._generate_once(messages, stop, run_manager, committed,
                                             share_client=True, **kwargs)
            except Exception:
                self._apply_extra_body(committed)   # a refused setting sticks, as before
                raise
            self._apply_extra_body(committed)
            return result

        def _spawn(number):
            box = {}
            committed = {}

            def worker():
                # One attempt, in its own daemon thread: if it loses the race
                # it is left to finish on its own and its result is dropped.
                # Two things must not leak out of the thread:
                # - the timing reports (timing keeps them per thread): the
                #   worker hands them back with its outcome, and _generate
                #   re-attaches the WINNING attempt's to this thread, so the
                #   stage's "[Timing] ... sent N pieces" line keeps them;
                # - the reasoning fallback's session change (self.extra_body,
                #   a shared value): _generate_once only REPORTS it through
                #   `committed`; _generate applies it, on this thread, for
                #   the winning attempt only. That is the choice: a late
                #   loser can never corrupt the shared client (a lock-guarded
                #   mutation would still let two interleaved fallbacks settle
                #   on the wrong value).
                try:
                    box["result"] = self._generate_once(messages, stop, run_manager,
                                                        committed, **kwargs)
                except Exception as exc:
                    box["error"] = exc
                finally:
                    box["committed"] = committed
                    box["reports"] = timing.take_reports()
                    box["done"] = True

            thread = threading.Thread(target=worker, daemon=True,
                                      name=f"amadeus-cloud-try-{number}")
            thread.start()
            return thread, box

        first_thread, first_box = _spawn(1)
        first_thread.join(timeout=patience)
        if first_box.get("done"):
            # The first attempt finished inside the patience: exactly
            # today's outcome (its answer, or its error) - no re-send.
            self._apply_extra_body(first_box.get("committed"))
            timing.add_reports(first_box.get("reports"))
            if "error" in first_box:
                raise first_box["error"]
            return first_box["result"]

        # It has not answered in time: send the same request again.
        print(f"[Amadeus] The model server has not answered after {patience:g} s - "
              "sending the request again.")
        second_thread, second_box = _spawn(2)

        # Whichever of the two finishes first with an answer wins; a finished
        # FAILURE only wins once the other attempt is also done (it might
        # still answer). Both attempts keep their normal request timeout, so
        # the total wait stays bounded like today's single call.
        while not (first_box.get("done") or second_box.get("done")):
            first_thread.join(timeout=0.05)
            second_thread.join(timeout=0.05)
        if first_box.get("done") and "error" not in first_box:
            winner = first_box
        elif second_box.get("done") and "error" not in second_box:
            winner = second_box
        else:
            # Every finished attempt failed; a still-running one might still
            # answer, so wait for it before giving up.
            if not first_box.get("done"):
                first_thread.join()
            if not second_box.get("done"):
                second_thread.join()
            if "error" not in first_box:
                winner = first_box
            elif "error" not in second_box:
                winner = second_box
            else:
                # Both attempts failed: the first attempt's error is what the
                # caller sees, and its fallback (if any) sticks - exactly
                # what one failed call does to the client today.
                self._apply_extra_body(first_box.get("committed"))
                timing.add_reports(first_box.get("reports"))
                raise first_box["error"]
        self._apply_extra_body(winner.get("committed"))
        timing.add_reports(winner.get("reports"))
        return winner["result"]

    def _generate_once(self, messages, stop, run_manager, committed=None,
                       share_client=False, **kwargs):
        """One attempt at the request: the call itself plus the two
        reasoning fallbacks (a server that refuses our reasoning setting gets
        one retry with a softer one; an unexplained 400/422 gets one try
        without the effort field). Raises like the original _generate did.

        `share_client` says whether self.extra_body may be mutated mid-call:
        - the single-attempt paths (not Vertex, re-send off) run in the
          calling thread and own the client: exactly today's behaviour, the
          fallback mutates the client and the retry reads it from there;
        - the Vertex re-send runs two attempts on the SAME client at once:
          an attempt must never mutate the shared client (a late loser would
          corrupt it after the winner settled) - it sends its adjusted body
          as a per-request extra_body (langchain merges it over the
          client's own, so the request on the wire is exactly today's) and
          REPORTS the session-level value through `committed`, which
          _generate applies, on the calling thread, for the winning attempt
          only."""
        try:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as exc:
            softer = _reasoning_fallback(self.extra_body, exc)
            if softer is not None:
                # A server that refuses our reasoning setting gets one
                # retry with a softer one (the client keeps it, like before).
                print(f"[Amadeus] Server refused the reasoning setting {self.extra_body!r} - "
                      f"retrying with {softer!r}.")
                if share_client:
                    self.extra_body = softer or None
                elif committed is not None:
                    committed["value"] = softer or None
                if share_client:
                    return super()._generate(messages, stop=stop, run_manager=run_manager,
                                             **kwargs)
                retry_kwargs = dict(kwargs)
                if "extra_body" not in retry_kwargs:
                    retry_kwargs["extra_body"] = softer or None
                return super()._generate(messages, stop=stop, run_manager=run_manager,
                                         **retry_kwargs)
            return self._retry_without_effort(exc, messages, stop, run_manager, kwargs,
                                              committed, share_client)

    def _retry_without_effort(self, exc, messages, stop, run_manager, kwargs,
                              committed=None, share_client=False):
        """Safety net for cloud providers nobody has tried yet: a 400/422 that
        does not say why, while we sent the common reasoning_effort field,
        gets ONE try without the field. A single (non-shared) attempt mutates
        the client like before - if that works the client keeps the plain
        request for the session; if not, the setting is put back and the
        first error surfaces, so every other fallback sees what it saw
        before. A shared attempt (the Vertex re-send) sends the plain body
        per-request and reports it through `committed` instead (see
        _generate_once)."""
        before = self.extra_body
        if (getattr(exc, "status_code", None) not in (400, 422)
                or not isinstance(before, dict) or "reasoning_effort" not in before):
            raise exc
        plain = {k: v for k, v in before.items() if k != "reasoning_effort"}
        if share_client:
            self.extra_body = plain or None
            try:
                result = super()._generate(messages, stop=stop, run_manager=run_manager,
                                           **kwargs)
            except Exception:
                self.extra_body = before
                raise exc
        else:
            retry_kwargs = dict(kwargs)
            if "extra_body" not in retry_kwargs:
                retry_kwargs["extra_body"] = plain or None
            try:
                result = super()._generate(messages, stop=stop, run_manager=run_manager,
                                           **retry_kwargs)
            except Exception:
                # The retry failed: the shared client was never touched,
                # so there is nothing to put back; the first error surfaces
                # as before.
                raise exc
        if committed is not None:
            committed["value"] = plain or None
        print("[Amadeus] Server refused the request with a reasoning setting - "
              "it works without one; keeping it off for this session.")
        return result

    def _apply_extra_body(self, committed):
        """The session-level part of a reasoning fallback: what
        _generate_once reported through `committed` becomes the client's
        extra_body (the next calls keep the setting the server accepted).
        Nothing to do when no fallback applied (no "value" key)."""
        if committed is not None and "value" in committed:
            self.extra_body = committed["value"]

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
    netloc = base_url.split("//", 1)[-1].split("/")[0].lower()
    host = netloc[1:].split("]")[0] if netloc.startswith("[") else netloc.split(":")[0]
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        # Every private range: 10/8, 172.16/12 (172.16-172.31: Docker, WSL,
        # some routers), 192.168/16, loopback, link-local, IPv6 private.
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


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
    """True for Google's Gemini API (the OpenAI-compat layer) and for Google
    Vertex AI (the same models, signed in with a JSON key - vertex_auth)."""
    return ("generativelanguage.googleapis.com" in base_url
            or "aiplatform.googleapis.com" in base_url)


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
    if "aiplatform.googleapis.com" in base_url:
        # Google Vertex AI has no model list. Ask a free question instead
        # (does the sign-in pass work for this project?) and report
        # "any_model": the callers then accept the model name as typed.
        return _test_vertex(base_url, headers, timeout)
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
        # Some providers (Google Gemini, xAI) answer a wrong key with HTTP 400.
        if response.status_code == 400 and ("api key" in body_text or "api_key" in body_text
                                            or "api-key" in body_text):
            return {"reachable": True, "models": [], "loading": False,
                    "error": "Server is running but rejected the API key (HTTP 400)"}
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


def _test_vertex(base_url: str, headers: dict, timeout: float) -> dict:
    url = base_url.rstrip("/").rsplit("/endpoints/openapi", 1)[0] + "/endpoints"
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return {"reachable": False, "models": [], "loading": False,
                "error": f"{type(exc).__name__}: {exc}"}
    if response.status_code == 200:
        return {"reachable": True, "models": [], "loading": False, "error": None,
                "any_model": True}
    if response.status_code in (401, 403):
        return {"reachable": True, "models": [], "loading": False,
                "error": f"Google Vertex refused the sign-in (HTTP {response.status_code}) - "
                         "open Settings and save the JSON key again"}
    return {"reachable": True, "models": [], "loading": False,
            "error": f"Server responded with HTTP {response.status_code}"}


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
    builtin = builtin_llm.active()
    if builtin:
        model = builtin_llm.model_name()

    # reasoning: one of REASONING_LEVELS. None = the old call shape:
    # enable_thinking=True means "medium", otherwise "off" (helper calls).
    if reasoning not in REASONING_LEVELS:
        reasoning = "medium" if enable_thinking else "off"

    # The user's Model Sampling settings are part of the client identity:
    # changing them rebuilds the client, so new values apply to the next
    # message without an app restart.
    thinking = reasoning != "off"
    sampling_standard, sampling_extra, _ = _effective_sampling(base_url, thinking)
    key = (model, reasoning,
           tuple(sorted(sampling_standard.items())),
           tuple(sorted(sampling_extra.items())))

    cached = _LLM_CACHE.get(key)
    if cached is not None and _LLM_CACHE_URL.get(key) == base_url:
        return cached

    client, sent_params = _build_client(base_url, api_key, model, reasoning,
                                        builtin=builtin,
                                        reply_cap=BUILTIN_MAX_TOKENS if builtin else 4096)
    _LLM_CACHE[key] = client
    _LLM_CACHE_URL[key] = base_url
    _LLM_CACHE_PARAMS[key] = sent_params
    return client


def _build_client(base_url, api_key, model, reasoning, builtin, reply_cap,
                  timeout=None, max_tokens=None):
    """Assemble one client for `base_url` + `model` - the one place every
    Amadeus client is built: get_llm's cached app clients and client_for's
    one-off Settings test clients share these per-host reasoning fields,
    the user's sampling settings and the thought-stripping wrapper, so a
    model that works in chat works in the Settings test, and vice versa.

    `timeout` / `max_tokens` override the built-in ceilings (the test client
    passes 30 s and a small output cap); None keeps them. With thinking on,
    a set cap grows by the thinking budget, exactly like the real clients.
    Returns (client, sent sampling params).
    """
    thinking = reasoning != "off"
    sampling_standard, sampling_extra, sent_params = _effective_sampling(base_url, thinking)

    # Local servers still expect *some* key. If Amadeus has none stored yet,
    # send a placeholder - you paste the real one into Amadeus Settings.
    resolved_key = (api_key or "").strip() or "unsloth"

    # The enable_thinking flag is a Qwen3/llama.cpp "chat template" feature.
    # Local servers (Unsloth, vLLM, ...) understand it; public cloud providers
    # may reject unknown body fields, so it is only sent for local/LAN hosts.
    local = _is_local_host(base_url)

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
            "max_tokens": reply_cap + budget if builtin else max(4096, reply_cap + budget),
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
    elif "aiplatform.googleapis.com" in base_url:
        # Google Vertex AI: the same field as the Gemini API, but Gemini 3.8
        # Flash there refuses "minimal" ("Thinking level is unsupported",
        # checked live 2026-11-12) - "low" is its lowest level.
        extra_body["reasoning_effort"] = reasoning if thinking else "low"
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

    # The Settings test client's short ceilings (timeout, output cap) override
    # the defaults - and any user cap - it just inherited. It must also
    # NEVER retry: with the ~5 s connect ceiling that timeout carries, a dead
    # server answers in ~5 s instead of after the SDK's retry-with-backoff.
    if timeout is not None:
        kwargs["timeout"] = timeout
        kwargs["max_retries"] = 0
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens + (THINKING_BUDGETS[reasoning] if thinking else 0)

    return _ThoughtFreeChatOpenAI(**kwargs), sent_params


def _test_client_timeout(total):
    """The Settings test client's timeout with a short CONNECT ceiling (~5 s,
    the openai SDK's own default): a refused or dropped connection must not
    hold the test for the whole `total`, while a slow-but-working model still
    gets it. Uses the installed openai SDK's own httpx Timeout type when
    importable (httpx2 on openai 3.x, then plain httpx); a stack without
    either keeps the plain float (the old behaviour)."""
    if not isinstance(total, (int, float)):
        return total
    import importlib
    for module in ("httpx2", "httpx"):
        try:
            Timeout = getattr(importlib.import_module(module), "Timeout", None)
        except ImportError:
            continue
        if Timeout is not None:
            return Timeout(total, connect=5.0)
    return total


def client_for(base_url: str, api_key: str, model: str, reasoning: str = None,
               timeout: float = 30.0, max_tokens: int = 200):
    """A one-off client for a chosen address (Settings -> Custom Model ->
    Test): built the same way as the real chat clients - per-host reasoning
    fields, the user's sampling settings, the thought-stripping wrapper - so
    the test feels like a real reply. It never enters _LLM_CACHE and never
    touches the configured server, and it asks for a short answer (30 s
    total with a ~5 s connect ceiling, no retries, a small output cap).
    reasoning=None = the user's "her replies" level.
    """
    if reasoning is None:
        reasoning = reasoning_for("replies")
    if reasoning not in REASONING_LEVELS:
        reasoning = "off"
    client, _ = _build_client(base_url, api_key, model, reasoning,
                              builtin=False, reply_cap=max_tokens,
                              timeout=_test_client_timeout(timeout),
                              max_tokens=max_tokens)
    return client

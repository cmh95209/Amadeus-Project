"""Her built-in model's server (llama-server on port 8091), run by the app.

The backend - not the launcher - starts and stops it, so Settings can
unload her model (to free graphics memory for a game), load it again, or
switch to another GGUF file in the models folder while Amadeus keeps
running. The launcher only says whether to load it at start
(AMADEUS_LOAD_MODEL=0 = "start without her model"). The server dies with the
backend: the launcher ends the backend's whole process tree on exit, and an
exit handler here stops it too.

States: "unloaded" -> "loading" -> "loaded", or "failed" (with a reason and
the end of logs/llm.log).
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import builtin_llm
import timing

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "llm.log"
LOAD_TIMEOUT_S = 900   # a 14 GB model from a slow disk; the server says "loading" meanwhile

_lock = threading.RLock()
_state = {"state": "unloaded", "file": "", "error": "", "since": 0.0}
_proc: subprocess.Popen | None = None


def override_config() -> dict | None:
    """The launcher's test option --simulate-vram-gb hands over the settings
    of the pretend card's tier (AMADEUS_BUILTIN_OVERRIDE, JSON)."""
    raw = os.environ.get("AMADEUS_BUILTIN_OVERRIDE", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# 6-7 GB cards (tiers v5, MH 2026-10-21: offered after a NOTICE): the 12B
# Q3_K_M (~6.4 GB) does not fit, so no "-ngl 999" - llama-server's own fit
# puts the layers that do not fit in normal memory (slow, but it starts).
SMALL_CARD_TIER = "6-7"


def server_command(cfg: dict, model_file: str, safe: bool = False) -> list[str]:
    """llama-server with the tier's settings; sampling = Unsloth's
    recommendation for Gemma 4 (the Model Sampling panel can override).
    Thinking is NOT set server-wide: every request already says it (the
    backend's normal client "off", Deep thinking "on", the memory sidecar
    "off"), and b11400 deprecates --chat-template-kwargs for this while its
    replacement (--reasoning off) might also refuse Deep thinking's per-
    request "on". The backend's thought filter catches any leak. The model
    is always served under one fixed name (builtin_llm.ALIAS), so switching
    files never confuses the backend or the memory sidecar."""
    cmd = [
        str(cfg["server_exe"]),
        "-m", str(model_file),
        "--host", "127.0.0.1",
        "--port", str(cfg.get("port") or builtin_llm.PORT),
        "--alias", builtin_llm.ALIAS,
        "-c", str(int(cfg.get("context") or 16384)),
        "--jinja",
        "--temp", "1.0", "--top-p", "0.95", "--top-k", "64",
        # Thoughts (Settings -> Reasoning above Off) come back in their own
        # field, never inside her reply text (tested 2026-10-20: 24/24 clean).
        "--reasoning-format", "deepseek",
    ]
    if cfg.get("tier") != SMALL_CARD_TIER:
        cmd += ["-ngl", str(int(cfg.get("gpu_layers") if cfg.get("gpu_layers") is not None else 999))]
    # Two reply slots sharing ONE context (her reply + the memory helper or
    # the English line at the same time); the default 4 slots cost card
    # memory (the short-window cache is kept per slot) and -np without -kvu
    # would split her context in halves (measured 2026-10-20).
    cmd += ["-np", "2", "-kvu"]
    if safe:
        # The retry after a failed start (an older AMD/Intel card whose
        # driver lacks flash attention / compressed memory): llama.cpp's own
        # defaults - full-size conversation memory, flash attention "auto".
        return cmd
    context = int(cfg.get("context") or 16384)
    # Smaller working space on the smaller-card tiers (16K / 24K context).
    cmd += ["-ub", "256" if context <= 24576 else "512", "-fa", "on"]
    kv = cfg.get("kv_cache")
    if kv in ("q8_0", "q4_0"):
        # A compressed conversation memory (needs flash attention on).
        cmd += ["-ctk", kv, "-ctv", kv]
    return cmd


def status() -> dict:
    with _lock:
        _poll()
        return dict(_state)


def _set(state: str, file: str = "", error: str = "") -> None:
    _state.update({"state": state, "file": file, "error": error, "since": time.time()})


def _poll() -> None:
    """Notice a server that stopped on its own (crash, out of memory)."""
    global _proc
    if _proc is not None and _proc.poll() is not None:
        code = _proc.returncode
        _proc = None
        if _state["state"] in ("loading", "loaded"):
            _set("failed", _state["file"],
                 f"her model stopped (exit {code}) - often not enough graphics memory. "
                 f"Details: {LOG_FILE}\n" + _log_tail())


def _log_tail(lines: int = 6) -> str:
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(t for t in text[-lines:] if t.strip())


def _port_taken(port: int, wait_s: float = 3.0) -> bool:
    """Is something listening on the port? Waits a moment first, so a
    server we just stopped has time to let go of it."""
    import socket
    deadline = time.monotonic() + wait_s
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                taken = True
        except OSError:
            taken = False
        if not taken or time.monotonic() >= deadline:
            return taken
        time.sleep(0.25)


def _health(port: int, timeout: float = 2.0) -> int:
    """llama-server's /health: 200 = model loaded, 503 = still loading,
    0 = not answering."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def load(file: str | None = None) -> dict:
    """Start her model (stopping any running one first). file = a GGUF in
    the models folder (default: the saved choice). Returns at once - the
    model loads in the background (state "loading"). ValueError for a file
    that is not in the models folder; LookupError when built-in mode is off
    or its helper program is missing."""
    cfg = override_config() or builtin_llm.load_config()
    if not cfg or (not override_config() and not builtin_llm.active()):
        raise LookupError("Her built-in model is switched off (Settings -> Connection).")
    exe = str(cfg.get("server_exe") or "")
    if not cfg.get("server_ok") or not os.path.isfile(exe):
        raise LookupError("Her model's helper program (llama.cpp) is not installed - "
                          "run the installer again.")
    if file:
        target = builtin_llm.resolve_model_file(file)
        if target is None:
            raise ValueError(f"'{file}' is not a model file in her models folder.")
    else:
        target = (cfg.get("model_file") if override_config() else builtin_llm.selected_file())
        if not target or not os.path.isfile(str(target)):
            raise LookupError("No model file yet: put a GGUF file in her models folder and press "
                              "Refresh list (or run the installer again and choose A to download hers).")
    target = str(target)
    port = int(cfg.get("port") or builtin_llm.PORT)
    with _lock:
        unload()
        if _port_taken(port):
            # Another program answers on her port (e.g. the user's own
            # llama-server): starting ours would fail to bind, and the
            # readiness check would see THEIR server as hers.
            _set("failed", target, f"port {port} is used by another program - close it "
                                   "(or restart Amadeus, which frees the port), then load again.")
            return dict(_state)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "w", encoding="utf-8")
        kwargs: dict = {"stdout": log, "stderr": subprocess.STDOUT, "cwd": str(PROJECT_ROOT)}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        global _proc
        started, memory_before = timing.now(), timing.gpu_memory()
        try:
            _proc = subprocess.Popen(server_command(cfg, target), **kwargs)
        except OSError as e:
            log.close()
            _set("failed", target, f"could not start llama-server: {e}")
            return dict(_state)
        _set("loading", target)
        proc = _proc
    def _retry_safe():
        print("[Amadeus] her model did not start with the fast settings - retrying once "
              "with llama.cpp's safe defaults.")
        try:
            log.write("\n[Amadeus] retrying with safe settings\n")
            log.flush()
            return subprocess.Popen(server_command(cfg, target, safe=True), **kwargs)
        except (OSError, ValueError):
            return None

    threading.Thread(target=_watch_loading, args=(proc, target, port, log, started, memory_before,
                                                  _retry_safe),
                     daemon=True, name="builtin-model-load").start()
    return status()


def _watch_loading(proc, target, port, log, started=None, memory_before=None,
                   retry=None) -> None:
    """Background: wait until the model answers, or report why it did not.
    A start that fails gets ONE retry with safe settings (`retry` returns
    the new process). A newer load/unload replaces `proc`; then this watcher
    just ends."""
    global _proc
    deadline = time.monotonic() + LOAD_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                if retry is not None:
                    with _lock:
                        again = retry() if _proc is proc else None
                        if again is not None:
                            _proc = again
                    retry = None
                    if again is not None:
                        proc = again
                        continue
                with _lock:
                    if _proc is proc:
                        _proc = None
                        _set("failed", target,
                             f"her model could not load (exit {proc.returncode}) - often "
                             f"not enough graphics memory. Details: {LOG_FILE}\n" + _log_tail())
                return
            if _health(port) == 200:
                with _lock:
                    current = _proc is proc
                    if current:
                        _set("loaded", target)
                        _remember(target)
                if current and started is not None:
                    timing.model_loaded(started, memory_before)
                return
            time.sleep(1)
        with _lock:
            if _proc is proc:
                _set("failed", target, f"her model did not finish loading in {LOAD_TIMEOUT_S // 60} "
                                       f"minutes. Details: {LOG_FILE}")
                _stop(proc)
    finally:
        # The server keeps its own copy of the log handle.
        try:
            log.close()
        except OSError:
            pass


def _remember(target: str) -> None:
    """A file that loaded fine becomes the one she starts with next time
    (never saved for a test-option run)."""
    if override_config():
        return
    try:
        builtin_llm.save_selected_file(target)
    except (LookupError, OSError):
        pass


def _stop(proc) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def unload() -> dict:
    """Stop her model and free its graphics memory."""
    global _proc
    with _lock:
        proc, _proc = _proc, None
        if proc is not None:
            _stop(proc)
        _set("unloaded")
        return dict(_state)


def start_at_launch() -> None:
    """Called once when the backend starts: load her model unless the
    launcher said not to (AMADEUS_LOAD_MODEL=0) or built-in mode is off."""
    if os.environ.get("AMADEUS_LOAD_MODEL", "1") == "0":
        print("[Amadeus] her built-in model was not loaded (started without it).")
        return
    if not (builtin_llm.active() or override_config()):
        return
    try:
        load()
        print("[Amadeus] loading her built-in model...")
    except (LookupError, ValueError) as e:
        print(f"[Amadeus] her built-in model was not started: {e}")


atexit.register(unload)

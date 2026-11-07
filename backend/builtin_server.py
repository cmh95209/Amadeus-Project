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
# pictures: she runs with her vision file; pictures_note: why not (Settings).
_state = {"state": "unloaded", "file": "", "error": "", "since": 0.0,
          "pictures": False, "pictures_where": "", "pictures_note": ""}
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
# Pictures (attachments Phase 3): the vision part goes on the graphics card
# when it fits (MH 2026-10-26: 8 GB and 12 GB cards too), else on the
# processor (--no-mmproj-offload: no card memory, about the same speed on an
# 8-core processor - Phase 0). llama.cpp does NOT fall back by itself: on
# Windows the driver quietly pushes part of her model into normal memory
# instead (Phase 0, pretend 8 GB card: 120 -> 65 pieces/s). So the free card
# memory is measured right before her model starts (vision_on_card). 6-7 GB
# cards: part of her model is already on the processor - the vision part too.
VISION_ON_PROCESSOR_TIERS = ("6-7",)
VISION_CARD_GB = 0.6      # the vision part on the card (0.3-0.45 GB measured) + a margin
VOICE_CARD_GB = 2.0       # her voice on the card (part of vram_need_gb on those tiers)
VOICE_PORT = 9880


def _voice_running() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", VOICE_PORT), timeout=0.3):
            return True
    except OSError:
        return False


TIER_TABLE = PROJECT_ROOT / "scripts" / "local_llm_tiers.json"


def model_need_gb(cfg: dict, model_file=None) -> float:
    """Graphics memory her model needs: the tier's estimate (her voice
    included when it runs on the card), adjusted by how much bigger or
    smaller the file she loads is than the tier's own model (a GGUF picked
    in Settings) - the same rule as the launcher's check."""
    need = float(cfg.get("vram_need_gb") or 0)
    try:
        size = os.path.getsize(str(model_file or cfg.get("model_file") or ""))
    except OSError:
        return need
    if size <= 0:
        return need       # an empty / unfinished file says nothing about her model
    own = None
    try:
        table = json.loads(TIER_TABLE.read_text(encoding="utf-8"))
        tier = next(t for t in table["tiers"] if t["id"] == cfg.get("tier"))
        own = int(table["models"][tier["model"]]["bytes"])
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        try:
            own = os.path.getsize(str(cfg.get("model_file") or ""))
        except OSError:
            own = None
    return need + (size - own) / 1024 ** 3 if own else need


def vision_on_card(cfg: dict, memory=None, model_file=None) -> tuple[bool, str]:
    """(on the card?, why) for her vision part, from the free card memory
    right now. memory = (used GB, total GB) (default: measured)."""
    tier = str(cfg.get("tier") or "")
    if builtin_llm.shared_memory(cfg):
        # A Mac's Apple chip: one memory for processor and graphics - the
        # graphics side reads pictures, nothing to measure.
        return True, "Apple chip (shared memory)"
    if tier in VISION_ON_PROCESSOR_TIERS:
        return False, "part of her model already runs on the processor"
    mem = memory if memory is not None else timing.gpu_memory()
    if mem is None:
        # Not an NVIDIA card (or no nvidia-smi): nothing to measure - the
        # card only on 16 GB+ tiers, where there is plenty of room.
        on = tier in ("16-23", "24-plus")
        return on, "card memory could not be measured"
    used, total = mem
    need = model_need_gb(cfg, model_file)
    if cfg.get("voice_device") == "cuda" and _voice_running():
        need = max(0.0, need - VOICE_CARD_GB)   # her voice already counts in "used"
    free = total - used
    on = free >= need + VISION_CARD_GB
    return on, (f"{free:.1f} GB free on the card, her model needs about {need:.1f} GB "
                f"+ {VISION_CARD_GB} GB for pictures")


def _place(cfg: dict, on_card: bool) -> str:
    """Where her pictures are read, in words."""
    if not on_card:
        return "processor"
    return "Apple chip" if builtin_llm.shared_memory(cfg) else "graphics card"


def server_command(cfg: dict, model_file: str, safe: bool = False,
                   vision: bool = True, vision_on_card: bool | None = None) -> list[str]:
    """llama-server with the tier's settings; sampling = Unsloth's
    recommendation for Gemma 4 (the Model Sampling panel can override).
    Thinking is NOT set server-wide: every request already says it (the
    backend's normal client "off", Deep thinking "on", the memory sidecar
    "off"), and b11400 deprecates --chat-template-kwargs for this while its
    replacement (--reasoning off) might also refuse Deep thinking's per-
    request "on". The backend's thought filter catches any leak. The model
    is always served under one fixed name (builtin_llm.ALIAS), so switching
    files never confuses the backend or the memory sidecar. vision: load
    the model's vision file when there is one (builtin_llm.vision_file) -
    False = the retry without it. vision_on_card: where the vision part runs
    (None = by tier: the processor only on 6-7 GB cards; load() measures)."""
    vision_path = builtin_llm.vision_file(model_file) if vision else ""
    if vision_on_card is None:
        vision_on_card = cfg.get("tier") not in VISION_ON_PROCESSOR_TIERS
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
    if vision_path:
        cmd += ["--mmproj", vision_path]
        if not vision_on_card:
            cmd += ["--no-mmproj-offload"]
    if safe:
        # The retry after a failed start (an older AMD/Intel card whose
        # driver lacks flash attention / compressed memory): llama.cpp's own
        # defaults - full-size conversation memory, flash attention "auto".
        return cmd
    context = int(cfg.get("context") or 16384)
    # Smaller working space on the smaller-card tiers (16K / 24K context) -
    # but with pictures 512 everywhere: 256 caps a picture at ~250 pieces and
    # screenshot text was misread (Phase 0); 512 costs ~0.09 GB.
    cmd += ["-ub", "256" if context <= 24576 and not vision_path else "512", "-fa", "on"]
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
    """Notice a LOADED server that stopped on its own (crash, out of
    memory). While her model is loading, the loading watcher decides (it may
    still retry: safe defaults, then without the vision file) - a status
    request in between must not call it failed first (2026-10-26)."""
    global _proc
    if _state["state"] == "loaded" and _proc is not None and _proc.poll() is not None:
        code = _proc.returncode
        _proc = None
        if _state["state"] in ("loading", "loaded"):
            _set("failed", _state["file"],
                 f"her model stopped (exit {code}) - often not enough "
                 f"{'memory' if builtin_llm.shared_memory() else 'graphics memory'}. "
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
        vision_path = builtin_llm.vision_file(target)
        on_card, why = vision_on_card(cfg, memory_before, target) if vision_path else (False, "")
        if vision_path:
            print(f"[Amadeus] her pictures are read on the {_place(cfg, on_card)} "
                  f"({why}).")
        try:
            _proc = subprocess.Popen(server_command(cfg, target, vision_on_card=on_card), **kwargs)
        except OSError as e:
            log.close()
            _set("failed", target, f"could not start llama-server: {e}")
            return dict(_state)
        _set("loading", target)
        _state.update({"pictures": bool(vision_path),
                       "pictures_where": ("card" if on_card else "processor") if vision_path else "",
                       "pictures_note": "" if vision_path else
                       "no vision file next to her model - she can't see pictures with it"})
        proc = _proc

    def _start(note: str, safe: bool, vision: bool, card: bool = False):
        def again():
            print(f"[Amadeus] her model did not start - retrying {note}.")
            try:
                log.write(f"\n[Amadeus] retrying {note}\n")
                log.flush()
                new = subprocess.Popen(server_command(cfg, target, safe=safe, vision=vision,
                                                      vision_on_card=card), **kwargs)
            except Exception:   # noqa: BLE001 - a retry that can't start just ends the chain
                return None
            if vision:
                _state["pictures_where"] = "card" if card else "processor"
            else:
                # She runs without pictures now - Settings says why (V1 a).
                _state.update({"pictures": False, "pictures_where": "", "pictures_note":
                               "her model would not start with its vision file "
                               f"({os.path.basename(vision_path)}), so it runs without it - "
                               "she can't see pictures"})
            return new
        return again

    # A failed start: llama.cpp's safe defaults (older AMD/Intel drivers);
    # pictures on the card -> the same with them on the processor; then
    # without the vision file at all (a wrong or broken vision file, or a
    # card too full, must never keep her from starting).
    retries = [_start("once with llama.cpp's safe defaults", True, True, on_card)]
    if vision_path and on_card:
        retries += [_start("with her pictures read on the processor", False, True, False)]
    if vision_path:
        retries += [_start("without her vision file", False, False),
                    _start("without her vision file, safe defaults", True, False)]
    threading.Thread(target=_watch_loading, args=(proc, target, port, log, started, memory_before,
                                                  retries),
                     daemon=True, name="builtin-model-load").start()
    return status()


def _watch_loading(proc, target, port, log, started=None, memory_before=None,
                   retry=None) -> None:
    """Background: wait until the model answers, or report why it did not.
    A start that fails gets the retries in `retry` (one callable or a list,
    tried in order; each returns the new process). A newer load/unload
    replaces `proc`; then this watcher just ends."""
    global _proc
    deadline = time.monotonic() + LOAD_TIMEOUT_S
    retries = list(retry) if isinstance(retry, (list, tuple)) else ([retry] if retry else [])
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                again = None
                while retries and again is None:
                    step = retries.pop(0)
                    with _lock:
                        if _proc is not proc:
                            return
                        again = step()
                        if again is not None:
                            _proc = again
                if again is not None:
                    proc = again
                    continue
                with _lock:
                    if _proc is proc:
                        _proc = None
                        _set("failed", target,
                             f"her model could not load (exit {proc.returncode}) - often "
                             f"not enough {'memory' if builtin_llm.shared_memory() else 'graphics memory'}. "
                             f"Details: {LOG_FILE}\n" + _log_tail())
                return
            if _health(port) == 200:
                with _lock:
                    current = _proc is proc
                    if current:
                        _set("loaded", target)
                        _remember(target)
                        _forget_picture_check()
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
        _state.update({"pictures": False, "pictures_where": "", "pictures_note": ""})
        _forget_picture_check()
        return dict(_state)


def _forget_picture_check() -> None:
    """Her server changed: "can she see pictures?" is asked again."""
    try:
        import attachments   # late import: keeps this module light
        attachments.reset_capabilities()
    except Exception:
        pass


def start_at_launch() -> None:
    """Called once when the backend starts: load her model unless the
    launcher said not to (AMADEUS_LOAD_MODEL=0) or built-in mode is off."""
    if not (builtin_llm.active() or override_config()):
        return   # custom model: nothing to load (and nothing to report)
    if os.environ.get("AMADEUS_LOAD_MODEL", "1") == "0":
        print("[Amadeus] her built-in model was not loaded (started without it).")
        return
    try:
        load()
        print("[Amadeus] loading her built-in model...")
    except (LookupError, ValueError) as e:
        print(f"[Amadeus] her built-in model was not started: {e}")


atexit.register(unload)

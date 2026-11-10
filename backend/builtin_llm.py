"""Built-in local model: the llama-server on port 8091 (run by the app,
builtin_server.py).

The installer's option A writes data/local_llm.json (model, paths, tier
settings). While its "mode" is "builtin", Amadeus talks ONLY to that server:
no auto-probing of other local ports (a user's own Unsloth Desktop on
8000/8888 must never stand in for it), no API key needed (the server has
none), and the model is always served under one fixed name (ALIAS).
Settings can pick another GGUF file from the models folder (selected_file).
Without the file, or with "mode": "custom", nothing here changes anything.
"""
import json
import os
import re
from pathlib import Path

import memory

PORT = 8091
# The one name llama-server serves her model under (--alias), whichever
# GGUF file is loaded: the backend and the memory sidecar check the server's
# model list for this name, so switching files never breaks them.
ALIAS = "amadeus-builtin"
_CONFIG_NAME = "local_llm.json"
_cache = {"key": None, "data": None}


def config_path() -> str:
    # memory.DATA_DIR is relative to the backend folder (the app's working
    # directory), like every other data file.
    return os.path.join(memory.DATA_DIR, _CONFIG_NAME)


def load_config():
    """The settings dict, or None (no file / unreadable). Re-read only when
    the file changes, so the per-request checks stay cheap."""
    path = os.path.abspath(config_path())
    try:
        st = os.stat(path)
    except OSError:
        _cache["key"], _cache["data"] = None, None
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        try:
            # utf-8-sig: tolerate a byte-order mark from hand edits.
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            _cache["data"] = data if isinstance(data, dict) else None
        except (OSError, ValueError):
            _cache["data"] = None
        _cache["key"] = key
    return _cache["data"]


def shared_memory(cfg=None) -> bool:
    """Her model runs on a Mac's Apple chip (the installer's Metal build of
    llama.cpp): processor and graphics share one memory, so there is no
    graphics card to measure or mention (scripts/launcher.py has the same
    rule)."""
    cfg = cfg if cfg is not None else (load_config() or {})
    return str(cfg.get("build") or "").startswith("metal")


def active() -> bool:
    cfg = load_config()
    return bool(cfg) and cfg.get("mode") == "builtin"


def server_url() -> str:
    cfg = load_config() or {}
    port = cfg.get("port") if isinstance(cfg.get("port"), int) else PORT
    return f"http://127.0.0.1:{port}/v1"


def model_name(cfg=None) -> str:
    """The name llama-server serves her model under (--alias)."""
    return ALIAS


def models_dir(cfg=None):
    """The folder her models live in (next to the project folder), or None."""
    cfg = cfg if cfg is not None else (load_config() or {})
    model = str(cfg.get("model_file") or "")
    return Path(model).parent if model else None


_SPLIT_PART = re.compile(r"-(\d{5})-of-\d{5}\.gguf$")

# The name parts that mark a GGUF as the picture-reading (vision) part of a
# model, not a chat model (2026-11-11: "mmproj" was the only marker, so a
# vision file NAMED AFTER ITS MODEL - "mmproj-Gemma4-12B-....gguf" - matched
# nothing, and with two Gemma vision files in the folder even the "only
# vision file" fallback never applied). A marker counts only as a WHOLE name
# part, so a chat model called "envision" is never a vision file.
_VISION_PARTS = ("mmproj", "vision", "clip", "projector")
_VISION_MARKER = re.compile(
    r"(?<![a-z0-9])(?:mmproj|mm-proj|vision|clip|projector)(?![a-z0-9])")
# Quant/precision tags a vision file carries but its model need not ("...
# -qat-mmproj-F16" vs "...-qat-UD-Q4_K_XL"): dropped before the names are
# compared. "qat" is a name part, not a tag: it stays.
_QUANT_START = re.compile(r"^(?:i?iq|q)\d{1,2}$")
_QUANT_TAIL = re.compile(r"^(?:[a-z]{1,2}|\d+)$")
_PRECISION = re.compile(r"^b?f\d+$")


def _is_vision_name(name: str) -> bool:
    """A .gguf whose name carries one of the vision markers as a whole part:
    "mmproj-X", "X-mmproj-F16", "X.mmproj.gguf", "X-vision", "X-clip",
    "mm-proj-X"."""
    low = name.lower()
    if low.endswith(".gguf"):
        low = low[:-5]
    return bool(_VISION_MARKER.search(low))


def _match_parts(name: str) -> list:
    """The name's parts for matching a model with its vision file:
    lower-cased, minus the vision markers (the marker may be a prefix OR a
    suffix) and minus the quant/precision tags:
    "Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M.gguf" ->
    ["gemma4", "12b", "qat", "uncensored", "hauhaucs", "balanced"]."""
    parts = [p for p in re.split(r"[-_. ]", name.lower()) if p and p != "gguf"]
    out, i = [], 0
    while i < len(parts):
        p = parts[i]
        if p in _VISION_PARTS:
            i += 1
        elif p == "mm" and i + 1 < len(parts) and parts[i + 1] == "proj":
            i += 2
        elif _QUANT_START.match(p):
            i += 1
            while i < len(parts) and _QUANT_TAIL.match(parts[i]):
                i += 1
        elif _PRECISION.match(p):
            i += 1
        else:
            out.append(p)
            i += 1
    return out


def _match_score(vision: list, model: list) -> tuple:
    """How well a vision file's remaining name matches the model's: the
    shared start (the model name goes on in order after the marker), and at
    a tie the file whose remaining name ends at the break (one that carries
    extra parts after the model's name matches less well)."""
    score = 0
    for v, m in zip(vision, model):
        if v != m:
            break
        score += 1
    return (score, vision[:score] == vision)


def _is_main_model(path: Path) -> bool:
    """A GGUF that is a chat model: not a speed-up (MTP) or vision file,
    and of a model split into parts ("-00001-of-00003.gguf") only the first
    part - llama.cpp loads the others from it."""
    name = path.name.lower()
    split = _SPLIT_PART.search(name)
    return (name.endswith(".gguf") and not name.startswith("mtp-")
            and not _is_vision_name(name) and (split is None or split.group(1) == "00001"))


# Settings -> "Get more models" (2026-10-18 - option B: links, no
# in-app downloader): the official Gemma 4 QAT files, pinned to the same
# Hugging Face revisions the installer uses (12B-Q3 / 12B must match
# scripts/local_llm_tiers.json - a test keeps them equal). The download
# runs in the user's browser; they move the file into the models folder
# and press Refresh list. 26B-A4B and 31B are offered here only: the
# installer no longer picks them (31B ~31 GB and 26B-A4B 31.5 GB at peak on
# a 32 GB card with this app's settings).
MODEL_CATALOG = [
    {"key": "12B-Q3", "label": "Gemma 4 12B (3-bit, for 8 GB cards)", "repo": "unsloth/gemma-4-12b-it-GGUF",
     "revision": "fc034cfff751157913579611efad8462ac1be606",
     "file": "gemma-4-12b-it-Q3_K_M.gguf", "bytes": 5693872960},
    {"key": "12B", "label": "Gemma 4 12B (QAT)", "repo": "unsloth/gemma-4-12b-it-qat-GGUF",
     "revision": "980b060c40a8539ac159e0501a3e0f66a6365af3",
     "file": "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf", "bytes": 6716356800},
    {"key": "26B-A4B", "label": "Gemma 4 26B-A4B (QAT)", "repo": "unsloth/gemma-4-26B-A4B-it-qat-GGUF",
     "revision": "7b92b5b28818151e8669af2e45e88d6086f490dd",
     "file": "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf", "bytes": 14249047104},
    {"key": "31B", "label": "Gemma 4 31B (QAT)", "repo": "unsloth/gemma-4-31B-it-qat-GGUF",
     "revision": "43cc1aeb31adf47ec06a854507ce552cd9862e6f",
     "file": "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf", "bytes": 17287670048},
]


# On a Mac the 3-bit model is not "for 8 GB cards" (scripts/local_llm_tiers.json
# "mac" -> "labels"; a test keeps them equal).
MAC_LABELS = {"12B-Q3": "Gemma 4 12B (3-bit)"}


def catalog(cfg=None) -> list:
    """The "Get more models" list for Settings: each model's size, its
    official download link, whether it is already in the models folder, and
    how it compares with the model the installer picked for this PC
    ("recommended" / "smaller" / "bigger" - bigger ones may be slow or not
    fit this graphics card)."""
    cfg = cfg if cfg is not None else (load_config() or {})
    folder = models_dir(cfg)
    tier_key = str(cfg.get("model") or "")
    tier_bytes = next((m["bytes"] for m in MODEL_CATALOG if m["key"] == tier_key), None)
    out = []
    for m in MODEL_CATALOG:
        if tier_bytes is None:
            fit = ""
        elif m["key"] == tier_key:
            fit = "recommended"
        else:
            fit = "smaller" if m["bytes"] < tier_bytes else "bigger"
        out.append({
            "key": m["key"],
            "label": MAC_LABELS.get(m["key"], m["label"]) if shared_memory(cfg) else m["label"],
            "file": m["file"],
            "gb": round(m["bytes"] / 1024 ** 3, 1),
            "url": (f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/"
                    f"{m['file']}?download=true"),
            "installed": bool(folder) and (folder / m["file"]).is_file(),
            "fit": fit,
        })
    return out


def open_models_folder() -> str:
    """Open the models folder in Explorer / Finder (created if missing).
    Returns its path; LookupError when no built-in model is set up."""
    folder = models_dir()
    if folder is None:
        raise LookupError("No built-in model is set up on this install.")
    folder.mkdir(parents=True, exist_ok=True)
    import subprocess
    import sys
    if sys.platform == "win32":
        os.startfile(str(folder))   # noqa: S606 - the user's own folder
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    return str(folder)


def list_models(cfg=None) -> list:
    """Every chat-model GGUF in the models folder (the installer's and any
    the user put there), for Settings: [{"file", "name", "gb"}], by name."""
    folder = models_dir(cfg)
    if folder is None or not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if path.is_file() and _is_main_model(path):
            try:
                gb = round(path.stat().st_size / 1024 ** 3, 1)
            except OSError:
                continue
            out.append({"file": str(path), "name": path.name, "gb": gb})
    return out


# Her vision files (attachments Phase 3, 2026-10-26): the installer saves the
# model's own vision file next to it under these names (local_llm_tiers.json
# "mmproj" -> "save_as"; a test keeps them equal).
VISION_FILES = {
    "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf": "gemma-4-12B-it-qat-mmproj-F16.gguf",
    "gemma-4-12b-it-Q3_K_M.gguf": "gemma-4-12b-it-mmproj-F16.gguf",
}


def vision_file(model_file) -> str:
    """The vision file her model can see pictures with, or "" when there is
    none: the installer's file for its own models; for a model the user
    added, the vision file next to it whose name carries the model's name -
    the marker part may sit in front of it ("mmproj-Gemma4-12B-...") or
    behind it ("Gemma4-12B-...-mmproj-F16", "...-vision") - the best match
    on the remaining name, else the only vision file in the folder. A wrong
    one only means the server starts again without it (builtin_server)."""
    if not model_file:
        return ""
    model = Path(str(model_file))
    folder = model.parent
    known = VISION_FILES.get(model.name)
    if known and (folder / known).is_file():
        return str(folder / known)
    try:
        found = [p for p in sorted(folder.iterdir(), key=lambda p: p.name.lower())
                 if p.is_file() and p.suffix.lower() == ".gguf"
                 and _is_vision_name(p.name)]
    except OSError:
        return ""
    model_parts = _match_parts(model.name)
    best = None
    for p in found:
        score = _match_score(_match_parts(p.name), model_parts)
        if best is None or score > best[0]:
            best = (score, p)
    if best is not None and best[0][0] > 0:
        return str(best[1])
    return str(found[0]) if len(found) == 1 else ""


def resolve_model_file(file: str):
    """The full path of `file` (a name or a path) when it is a chat-model
    GGUF directly inside the models folder, else None - Settings can never
    point the server at a file anywhere else on the PC."""
    folder = models_dir()
    if folder is None or not file:
        return None
    candidate = (folder / Path(str(file)).name).resolve()
    if candidate.parent != folder.resolve() or not candidate.is_file() or not _is_main_model(candidate):
        return None
    return candidate


def vision_files(cfg=None) -> list:
    """Every vision-file GGUF in the models folder (Settings' Vision file
    picker), as [{"file", "name", "gb"}], by name - like list_models()."""
    folder = models_dir(cfg)
    if folder is None or not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if path.is_file() and path.suffix.lower() == ".gguf" and _is_vision_name(path.name):
            try:
                gb = round(path.stat().st_size / 1024 ** 3, 1)
            except OSError:
                continue
            out.append({"file": str(path), "name": path.name, "gb": gb})
    return out


def resolve_vision_file(file: str):
    """The full path of `file` (a name or a path) when it is a vision-file
    GGUF directly inside the models folder, else None - Settings can never
    point the server at a file anywhere else on the PC (the same rule as a
    chat-model file)."""
    folder = models_dir()
    if folder is None or not file:
        return None
    candidate = (folder / Path(str(file)).name).resolve()
    if (candidate.parent != folder.resolve() or not candidate.is_file()
            or not _is_vision_name(candidate.name)):
        return None
    return candidate


def vision_for(model_file: str, cfg=None) -> str:
    """The vision file loaded WITH `model_file` on the next Load: the one
    picked in Settings (a file name, or "none" = the user chose no
    pictures), while it still exists; a saved file that is gone counts as
    Automatic again. Else the auto-detected one next to the model ("" when
    there is none)."""
    cfg = cfg if cfg is not None else (load_config() or {})
    chosen = str(cfg.get("selected_vision_file") or "")
    if chosen == "none":
        return ""
    if chosen and os.path.isfile(chosen):
        return chosen
    return vision_file(model_file)


def selected_vision_file(cfg=None) -> str:
    """The vision file the model she loads (selected_file) runs with - the
    picked one, or the auto-detected one ("" = no pictures)."""
    cfg = cfg if cfg is not None else (load_config() or {})
    return vision_for(selected_file(cfg), cfg)


def vision_choice(cfg=None) -> str:
    """What Settings' Vision file picker shows: "" = Automatic, "none" = the
    user chose no pictures, else the picked file's name (a saved file that
    is gone counts as Automatic again)."""
    cfg = cfg if cfg is not None else (load_config() or {})
    chosen = str(cfg.get("selected_vision_file") or "")
    if chosen == "none":
        return "none"
    if chosen and os.path.isfile(chosen):
        return os.path.basename(chosen)
    return ""


def save_selected_vision_file(path: str) -> None:
    """Save the Vision file choice (kept like the chosen model file): '' =
    Automatic (cleared again), 'none' = no pictures, else a vision-file GGUF
    directly inside the models folder, kept by its full path. LookupError
    when no built-in model is set up; ValueError for a file that is not a
    vision file inside the folder."""
    cfg = load_config()
    if not cfg:
        raise LookupError("No built-in model is set up on this install.")
    cfg = dict(cfg)
    if not path:
        cfg.pop("selected_vision_file", None)
    elif path == "none":
        cfg["selected_vision_file"] = "none"
    else:
        target = resolve_vision_file(path)
        if target is None:
            raise ValueError(f"'{path}' is not a vision file in her models folder.")
        cfg["selected_vision_file"] = str(target)
    save_config(cfg)


def selected_file(cfg=None) -> str:
    """The GGUF she loads: the one picked in Settings (while it still
    exists), else the installer's, else the first chat model in the models
    folder (an option-B install has llama.cpp but no model of hers - the
    user's own GGUF is then the one). "" -> the installer's (missing) path."""
    cfg = cfg if cfg is not None else (load_config() or {})
    chosen = str(cfg.get("selected_file") or "")
    if chosen and os.path.isfile(chosen):
        return chosen
    tier_file = str(cfg.get("model_file") or "")
    if tier_file and os.path.isfile(tier_file):
        return tier_file
    others = list_models(cfg)
    return others[0]["file"] if others else tier_file


def model_label(cfg=None) -> str:
    """What Settings calls the model she loads."""
    cfg = cfg if cfg is not None else (load_config() or {})
    chosen = selected_file(cfg)
    if chosen and os.path.normcase(chosen) != os.path.normcase(str(cfg.get("model_file") or "")):
        return Path(chosen).stem
    return cfg.get("model_label") or Path(chosen).stem


def save_selected_file(path: str) -> None:
    cfg = load_config()
    if not cfg:
        raise LookupError("No built-in model is set up on this install.")
    cfg = dict(cfg)
    if os.path.normcase(str(path)) == os.path.normcase(str(cfg.get("model_file") or "")):
        cfg.pop("selected_file", None)
    else:
        cfg["selected_file"] = str(path)
    save_config(cfg)


def describe() -> dict:
    """What Settings shows: is a built-in model set up on this install, is
    it the one in use, and its state."""
    cfg = load_config()
    available = bool(cfg) and bool(cfg.get("model_file"))
    return {
        "available": available,
        "mode": "builtin" if active() else "custom",
        "model_label": model_label(cfg) if available else "",
        "model_missing": available and model_missing(),
        # llama.cpp itself is missing (vs. only the model file).
        "runner_missing": available and runner_missing(),
        # Her vision file with the model she loads (pictures, Phase 3):
        # the picked one, or the auto-detected one next to it.
        "vision_file": os.path.basename(selected_vision_file(cfg)) if available else "",
        "context": (cfg or {}).get("context") if available else None,
        # A Mac with an Apple chip: Settings says "memory", not "graphics card".
        "shared_memory": available and shared_memory(cfg),
    }


def save_mode(mode: str) -> None:
    """Switch between the built-in model and the user's own. LookupError when
    there is no built-in model on this install to switch to."""
    cfg = load_config()
    if not cfg or not cfg.get("model_file"):
        raise LookupError("No built-in model is set up on this install. Run the "
                          "installer again and choose option A.")
    cfg = dict(cfg)
    cfg["mode"] = "builtin" if mode == "builtin" else "custom"
    save_config(cfg)


def save_config(cfg: dict) -> None:
    """Write data/local_llm.json in one step (a crash mid-write never leaves
    half a file: the launcher and the installer read it too)."""
    path = config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)


def runner_missing() -> bool:
    """llama.cpp (llama-server) is not installed."""
    cfg = load_config() or {}
    return not cfg.get("server_ok") or not os.path.isfile(str(cfg.get("server_exe") or ""))


def model_missing() -> bool:
    """True when llama.cpp or a model file to load is missing: the
    installer could not finish, the files were removed, or an option-B
    install has no GGUF yet. A model file counts as present whenever it is
    there under its final name - the installer only gives a download that
    name after its checksum passed (".part" before), and a file the user
    put in the folder by hand is theirs to choose (2026-10-18: her standard
    12B, downloaded by hand on an option-B install, was "missing" because
    model_ok said the installer had not fetched it - she never woke)."""
    cfg = load_config() or {}
    if not cfg.get("server_ok") or not os.path.isfile(str(cfg.get("server_exe") or "")):
        return True
    chosen = selected_file(cfg)
    return not chosen or not os.path.isfile(chosen)


# What one prompt needs besides the chat history (her personality, memories,
# tools, instructions - the Settings help's "about 6000") and her reply
# (the client's max_tokens). The built-in server's context window must hold
# all three, so the history gets what is left.
PROMPT_OVERHEAD_TOKENS = 6000
REPLY_TOKENS = 4096
MIN_HISTORY_TOKENS = 1000


def history_budget_cap():
    """The most chat history (tokens) a prompt may carry on the built-in
    model, or None when built-in mode is off (no cap). 16K -> 6288,
    32K -> 22672."""
    if not active():
        return None
    try:
        context = int((load_config() or {}).get("context") or 0)
    except (TypeError, ValueError):
        return None
    if context <= 0:
        return None
    return max(MIN_HISTORY_TOKENS, context - PROMPT_OVERHEAD_TOKENS - REPLY_TOKENS)


def history_default():
    """The conversation memory the built-in model starts with: the tier's
    history_default (installer), else everything its context can hold."""
    cap = history_budget_cap()
    if cap is None:
        return None
    try:
        value = int((load_config() or {}).get("history_default") or 0)
    except (TypeError, ValueError):
        value = 0
    return min(value, cap) if value > 0 else cap


def history_budget():
    """The built-in model's OWN conversation memory number (Settings), kept
    apart from the custom model's (data/context_budget.txt): switching
    between the two never overwrites the other's number. Never more than
    the context can hold. None when built-in mode is off."""
    cap = history_budget_cap()
    if cap is None:
        return None
    try:
        value = int((load_config() or {}).get("history_budget") or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return history_default()
    return max(min(value, cap), memory.MIN_CONTEXT_BUDGET)


def save_history_budget(budget: int):
    """Save the built-in model's conversation number (clamped to what its
    context holds) and return what was saved."""
    cap = history_budget_cap()
    cfg = load_config()
    if cap is None or not cfg:
        raise LookupError("Built-in mode is off.")
    value = max(memory.MIN_CONTEXT_BUDGET, min(int(budget), cap))
    cfg = dict(cfg)
    cfg["history_budget"] = value
    save_config(cfg)
    return value


# Gemma 4 writes its reasoning between these markers. Thinking is off, but if
# a thought block still comes back inline it must never reach her text or
# voice. An opener without its closer means the rest of the reply is thought.
# <think>...</think> is the same for the "thinking" GGUFs a user may load
# from the models folder (Qwen, DeepSeek and similar).
_THOUGHT_BLOCK = re.compile(r"<\|channel>thought.*?<channel\|>|<think>.*?</think>", re.S)
_THOUGHT_OPEN = re.compile(r"(?:<\|channel>thought|<think>).*\Z", re.S)


def strip_thoughts(text):
    if not isinstance(text, str) or ("<|channel>" not in text and "<think>" not in text):
        return text
    cleaned = _THOUGHT_OPEN.sub("", _THOUGHT_BLOCK.sub("", text))
    return cleaned.strip()

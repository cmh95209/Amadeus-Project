"""Built-in local model: the llama-server the launcher starts on port 8091.

The installer's option A writes data/local_llm.json (model, paths, tier
settings). While its "mode" is "builtin", Amadeus talks ONLY to that server:
no auto-probing of other local ports (a user's own Unsloth Desktop on
8000/8888 must never stand in for it), no API key needed (the server has
none), and the model name is the one the launcher serves (--alias).
Without the file, or with "mode": "custom", nothing here changes anything.
"""
import json
import os
import re
from pathlib import Path

import memory

PORT = 8091
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


def active() -> bool:
    cfg = load_config()
    return bool(cfg) and cfg.get("mode") == "builtin"


def server_url() -> str:
    cfg = load_config() or {}
    port = cfg.get("port") if isinstance(cfg.get("port"), int) else PORT
    return f"http://127.0.0.1:{port}/v1"


def model_name(cfg=None) -> str:
    """The name llama-server serves the model under (the launcher passes the
    same value as --alias): the model file's name without .gguf."""
    cfg = cfg if cfg is not None else (load_config() or {})
    stem = Path(str(cfg.get("model_file") or "")).stem
    return stem or "builtin-model"


def model_missing() -> bool:
    """True when the installer could not finish the downloads (or the files
    were removed since): the launcher then has nothing to start."""
    cfg = load_config() or {}
    for ok_key, file_key in (("model_ok", "model_file"), ("server_ok", "server_exe")):
        if not cfg.get(ok_key) or not os.path.isfile(str(cfg.get(file_key) or "")):
            return True
    return False


# Gemma 4 writes its reasoning between these markers. Thinking is off, but if
# a thought block still comes back inline it must never reach her text or
# voice. An opener without its closer means the rest of the reply is thought.
_THOUGHT_BLOCK = re.compile(r"<\|channel>thought.*?<channel\|>", re.S)
_THOUGHT_OPEN = re.compile(r"<\|channel>thought.*\Z", re.S)


def strip_thoughts(text):
    if not isinstance(text, str) or "<|channel>" not in text:
        return text
    cleaned = _THOUGHT_OPEN.sub("", _THOUGHT_BLOCK.sub("", text))
    return cleaned.strip()

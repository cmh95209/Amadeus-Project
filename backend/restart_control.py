"""Restart from Settings and the live status of her parts (2026-11-01).

The launcher (scripts/launcher.py) starts the backend with
AMADEUS_LAUNCHER_DIR set to its runtime folder. A restart request is a file
there; the launcher picks it up within a second, restarts her brain (this
backend), voice and memory, and keeps restart_status.json up to date. The
Settings panel reads that status plus a quick look at each part's port.
Without the launcher (started by hand, tests) there is nobody to restart
the parts, so a request is refused.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

REQUEST_NAME = "restart.request"
STATUS_NAME = "restart_status.json"
# Quit from Settings on the PC (2026-11-06): the launcher stops everything and
# closes its window. Only a launcher that knows this sets AMADEUS_LAUNCHER_SHUTDOWN
# (an older one still running would leave the request lying there).
SHUTDOWN_NAME = "shutdown.request"

# Her parts and the port each listens on (the launcher's own numbers).
PART_PORTS = {"voice": 9880, "memory": 9870}

# A status still "running" this long after it started is a launcher that
# died mid-restart: it no longer blocks a new request.
STALE_SECONDS = 15 * 60


def launcher_dir() -> Path | None:
    raw = os.environ.get("AMADEUS_LAUNCHER_DIR", "").strip()
    return Path(raw) if raw else None


def load_status(folder: Path | None = None) -> dict | None:
    """The launcher's latest restart status, or None (no restart this run,
    or no launcher). Never raises."""
    folder = folder or launcher_dir()
    if folder is None:
        return None
    try:
        data = json.loads((folder / STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def restart_in_progress(status: dict | None, now: float | None = None) -> bool:
    if not status or status.get("state") not in ("requested", "running"):
        return False
    started = status.get("started")
    if not isinstance(started, (int, float)):
        return True
    return ((now or time.time()) - started) < STALE_SECONDS


def request_restart(folder: Path | None = None, now: float | None = None) -> dict:
    """Ask the launcher for a restart.

    post: {"ok": True, "status": ...} - the request file is written and the
          status says "requested" (the launcher takes over within a second);
          or {"ok": False, "reason": "no-launcher" | "busy"}."""
    folder = folder or launcher_dir()
    if folder is None:
        return {"ok": False, "reason": "no-launcher"}
    stamp = now or time.time()
    if restart_in_progress(load_status(folder), stamp):
        return {"ok": False, "reason": "busy"}
    status = {"state": "requested", "started": stamp, "finished": None, "message": "",
              "parts": {"brain": "waiting", "voice": "waiting", "memory": "waiting"}}
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (STATUS_NAME + ".tmp")
    tmp.write_text(json.dumps(status), encoding="utf-8")
    os.replace(tmp, folder / STATUS_NAME)
    (folder / REQUEST_NAME).write_text(json.dumps({"at": stamp}), encoding="utf-8")
    return {"ok": True, "status": status}


def shutdown_supported() -> bool:
    return launcher_dir() is not None and os.environ.get("AMADEUS_LAUNCHER_SHUTDOWN") == "1"


def request_shutdown(folder: Path | None = None) -> dict:
    """Ask the launcher to shut Amadeus down.

    post: {"ok": True} - the request file is written (the launcher stops
          every part within a second); or {"ok": False, "reason":
          "no-launcher"} (started by hand, or a launcher too old to know)."""
    if folder is None:
        if not shutdown_supported():
            return {"ok": False, "reason": "no-launcher"}
        folder = launcher_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SHUTDOWN_NAME).write_text(json.dumps({"at": time.time()}), encoding="utf-8")
    return {"ok": True}


def port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def system_status(folder: Path | None = None, probe=None) -> dict:
    """What Settings shows: whether a restart is possible, each part's live
    state (her brain is answering this very request), and the latest restart."""
    folder = folder or launcher_dir()
    probe = probe or port_open
    parts = {"brain": "ready"}
    for part, port in PART_PORTS.items():
        parts[part] = "ready" if probe(port) else "offline"
    return {"launcher": folder is not None, "shutdown": shutdown_supported(),
            "parts": parts, "restart": load_status(folder)}

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import IO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
GPT_DIR = PROJECT_ROOT / "GPT-SoVITS"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
# Every log in a plain, visible folder: macOS Finder hides dot-folders, so
# logs under .runtime/ were invisible to Mac users until they turned on
# hidden files (2026-10-18). .runtime/ keeps only internal files.
LOG_DIR = PROJECT_ROOT / "logs"
LAUNCHER_LOG = LOG_DIR / "launcher.log"

GPT_PORT = 9880
BACKEND_PORT = 5050
FRONTEND_PORT = 5173
CM_PORT = 9870

AMADEUS_PORTS = {
    "GPT-SoVITS": GPT_PORT,
    "backend": BACKEND_PORT,
    "frontend": FRONTEND_PORT,
    "cm-sidecar": CM_PORT,
}

# CharacterMemory sidecar (official component since Phase 4). Its venv is
# created on first run by ensure_cm_sidecar(); if the engine is absent or
# its install fails, the app simply runs without long-term memory (soft-fail).
CM_DIR = PROJECT_ROOT / "memory_sidecar"

# An install made with the installer's -EnvSuffix option uses its own conda
# environments (e.g. "GPTSoVits-test"); the installer records the suffix
# here (start_windows.bat reads the same file for the app environment).
ENV_SUFFIX_FILE = RUNTIME_DIR / "env_suffix.txt"


# ---------------------------------------------------------------------------
# The console: a few plain status lines for the user; every detail (paths,
# each step, the full output of one-time setups) goes to logs/launcher.log.
# ---------------------------------------------------------------------------
_launcher_log: IO[str] | None = None
_line_open = False   # a "Starting ...:" line waits for its result
_last_tail: list[str] = []   # run_tool: the last lines the program printed


def open_launcher_log() -> None:
    global _launcher_log
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _launcher_log = LAUNCHER_LOG.open("w", encoding="utf-8")
    except OSError:
        _launcher_log = None   # no log file is never a reason not to start


def note(text: str) -> None:
    """A detail for logs/launcher.log only."""
    if _launcher_log is not None:
        try:
            _launcher_log.write(time.strftime("%H:%M:%S  ") + text + "\n")
            _launcher_log.flush()
        except (OSError, ValueError):
            pass


def _close_line() -> None:
    global _line_open
    if _line_open:
        print(flush=True)
        _line_open = False


def say(text: str = "", file=None) -> None:
    """A line for the user (and the log)."""
    _close_line()
    print(text, file=file or sys.stdout, flush=True)
    if text.strip():
        note(text.strip())


def begin(text: str) -> None:
    """Start a status line ("  Her voice ..."); end() finishes it."""
    global _line_open
    _close_line()
    print(f"  {text} ...", end="", flush=True)
    _line_open = True
    note(text + " ...")


def end(result: str) -> None:
    global _line_open
    if _line_open:
        print(f" {result}", flush=True)
        _line_open = False
    else:
        print(f"  {result}", flush=True)
    note("  -> " + result)


def _short(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 3)] + "..."


def run_tool(label: str, command: list[str], cwd: Path | None = None,
             passthrough: bool = False) -> int:
    """Run a one-time setup program (pip, venv, npm). Its full output goes
    to logs/launcher.log; the console gets ONE line that updates in place
    (the package being fetched and its download percentage). passthrough:
    print the output as it is instead (the installer runs the memory setup
    this way and draws its own progress bar from it). Returns the exit code
    (127 = could not start)."""
    note(f">>> {label}: {' '.join(command)}")
    started = time.monotonic()
    try:
        proc = subprocess.Popen(command, cwd=str(cwd) if cwd else None,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except OSError as e:
        note(f"<<< {label}: could not start ({e})")
        return 127
    width = max(40, shutil.get_terminal_size((80, 20)).columns - 1)
    count, current, pct, last = 0, "", -1, 0.0
    tail: list[str] = []
    import re
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        if passthrough:
            print(line, flush=True)
        m = re.match(r"^Progress (\d+) of (\d+)$", line)
        if m:
            total = int(m.group(2))
            pct = int(100 * int(m.group(1)) / total) if total else -1
        else:
            note("    | " + line)
            tail = (tail + [line])[-15:]
            m = re.match(r"^Collecting (\S+)", line)
            if m:
                count, current, pct = count + 1, m.group(1), -1
            elif line.startswith("Installing collected packages"):
                current, pct = "installing", -1
        now = time.monotonic()
        if not passthrough and now - last >= 0.25:
            last = now
            status = f"package {count}: {current}" if count else "working"
            if pct >= 0:
                status += f" {pct}%"
            print("\r" + _short(f"  {label}: {status}", width).ljust(width),
                  end="", flush=True)
    code = proc.wait()
    proc.stdout.close()
    _last_tail[:] = tail
    if not passthrough:
        print("\r" + " " * width + "\r", end="", flush=True)
    note(f"<<< {label}: exit {code} ({int(time.monotonic() - started)} s)")
    return code


def env_suffix() -> str:
    """The recorded environment suffix ("-test"), or "" for a normal install.
    A malformed file is ignored rather than turned into a bad env name."""
    try:
        text = ENV_SUFFIX_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    ok = text.startswith("-") and len(text) > 1 and \
        all(c.isalnum() or c in "_-" for c in text[1:])
    return text if ok else ""


def env_name(base: str) -> str:
    """The conda environment name this install uses for `base`."""
    return base + env_suffix()


# ---------------------------------------------------------------------------
# Built-in local model (installer option A): llama-server on its own port.
# 8091 on purpose - the backend used to auto-probe 8000/8888 (Unsloth
# Desktop's ports), and the built-in brain must never collide with those.
# ---------------------------------------------------------------------------
LLM_PORT = 8091
BUILTIN_CONFIG = BACKEND_DIR / "data" / "local_llm.json"


def load_builtin_config() -> dict | None:
    """The installer's built-in model settings when that mode is on, else
    None (no file, unreadable, or the user chose their own model)."""
    import json
    try:
        data = json.loads(BUILTIN_CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("mode") != "builtin":
        return None
    return data


# The one name her model is served under (llama-server --alias); the same
# value as backend/builtin_llm.py's ALIAS (a test keeps them equal). The
# backend runs the server itself (backend/builtin_server.py), so Settings
# can unload, load and switch it while Amadeus runs.
BUILTIN_ALIAS = "amadeus-builtin"


def builtin_model_name(cfg: dict) -> str:
    """The name the model is served under (--alias)."""
    return BUILTIN_ALIAS


def selected_model_file(cfg: dict) -> str:
    """The GGUF she loads: the one picked in Settings (while it exists),
    else the installer's, else the first chat model in the models folder
    (the same rule as backend/builtin_llm.py selected_file)."""
    chosen = str(cfg.get("selected_file") or "")
    if chosen and Path(chosen).is_file():
        return chosen
    tier_file = str(cfg.get("model_file") or "")
    if not tier_file or Path(tier_file).is_file():
        return tier_file
    folder = Path(tier_file).parent
    try:
        others = sorted((p for p in folder.iterdir()
                         if p.is_file() and p.name.lower().endswith(".gguf")
                         and not p.name.lower().startswith("mtp-")
                         and "mmproj" not in p.name.lower()
                         and not _re_split_part(p.name.lower())),
                        key=lambda p: p.name.lower())
    except OSError:
        others = []
    return str(others[0]) if others else tier_file


def _re_split_part(name: str) -> bool:
    """A later part of a split model ("-00002-of-00003.gguf"): llama.cpp
    loads it from part 1 (the same rule as backend/builtin_llm.py)."""
    import re
    m = re.search(r"-(\d{5})-of-\d{5}\.gguf$", name)
    return bool(m) and m.group(1) != "00001"


def builtin_files_ready(cfg: dict) -> tuple[bool, str]:
    """(True, "") when llama-server and the model are on disk, else
    (False, plain-English reason)."""
    exe = str(cfg.get("server_exe") or "")
    model = selected_model_file(cfg)
    if not cfg.get("server_ok") or not Path(exe).is_file():
        return False, "her model's helper program (llama.cpp) is not installed"
    # Present under its final name = usable (the same rule as
    # backend/builtin_llm.py model_missing): the installer names a download
    # only after its checksum passed, and a hand-placed file is the user's.
    if not model or not Path(model).is_file():
        return False, f"her model file is not downloaded ({Path(model).name or 'unknown'})"
    return True, ""


def builtin_vram_need_gb(cfg: dict) -> float | None:
    """Graphics memory her model needs (the tier's estimate, which includes
    her voice when it runs on the card), adjusted for a GGUF picked in
    Settings that is bigger or smaller than the tier's own. None = unknown."""
    try:
        need = float(cfg.get("vram_need_gb") or 0)
    except (TypeError, ValueError):
        return None
    if need <= 0:
        return None
    chosen, tier_file = selected_model_file(cfg), str(cfg.get("model_file") or "")
    if chosen != tier_file:
        # The estimate is for the tier's model; adjust by the size difference.
        # Its size comes from the tier table, so this also works when that
        # file was never downloaded (an option-B install with a GGUF of the
        # user's own).
        try:
            tier_bytes = Path(tier_file).stat().st_size
        except OSError:
            tier_bytes = _tier_model_bytes(cfg)
        try:
            if tier_bytes:
                need += (Path(chosen).stat().st_size - tier_bytes) / 1024 ** 3
        except OSError:
            pass
    return round(need, 1)


def _tier_model_bytes(cfg: dict) -> int | None:
    """The size of the tier's model file, from scripts/local_llm_tiers.json."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import llm_tiers
        return int(llm_tiers.load_table()["models"][str(cfg.get("model"))]["bytes"])
    except Exception:
        return None


def nvidia_free_vram_gb() -> float | None:
    """Free graphics memory on the NVIDIA card (the largest, if several), or
    None when it cannot be read (no NVIDIA card / no nvidia-smi): AMD and
    Intel cards have no reliable free-memory reading on Windows, so the
    check is skipped there."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    values = []
    for line in out.stdout.splitlines():
        try:
            values.append(float(line.strip()) / 1024)   # MiB -> GiB
        except ValueError:
            pass
    return round(max(values), 1) if out.returncode == 0 and values else None


def ask_about_graphics_memory(need: float, read_free=None, ask=input) -> bool:
    """When the card is too full for her model: ask the user. True = load
    her model, False = start without it. Without a person at the window
    (no console input) she starts anyway, as before. A free card asks
    nothing."""
    read_free = read_free or nvidia_free_vram_gb
    free = read_free()
    if free is None or free >= need:
        note(f"Graphics memory: {free} GB free, her model needs about {need} GB.")
        return True
    if not sys.stdin or not sys.stdin.isatty():
        note(f"Graphics memory low ({free} of {need} GB) - no one to ask, loading anyway.")
        return True
    while True:
        say(f"\n  Your graphics card is busy: {free} GB free, her model needs about {need} GB.")
        say("  Close games or other programs for full speed, then press Enter to check")
        say("  again. Or type S to start her model anyway (she will be slower until the")
        say("  card has room), or N to start without it (load it later in")
        say("  Settings -> Connection).")
        try:
            answer = ask("  Your choice [Enter / S / N]: ").strip().upper()
        except EOFError:
            answer = "S"
        note(f"  (answer: {answer or 'Enter'})")
        if answer == "S":
            return True
        if answer == "N":
            return False
        free = read_free()
        if free is None or free >= need:
            say(f"  Now {free} GB free - starting her model.")
            return True


def apply_simulated_vram(cfg: dict, vram_gb: int) -> dict | None:
    """Test option: run the settings of the tier a card with `vram_gb`
    would get (the same rules as the installer). Uses that tier's model
    file if it was downloaded into the same models folder; None when such
    a card would get no local model at all."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import llm_tiers
    table = llm_tiers.load_table()
    vendor = "nvidia" if str(cfg.get("build", "")).startswith("cuda") else "amd"
    choice = llm_tiers.choose(table, vendor, vram_gb, -1, "999")
    if not choice["offered"]:
        return None
    model = table["models"][choice["model"]]
    models_dir = Path(str(cfg.get("model_file") or "")).parent
    out = dict(cfg)
    out.update({
        "model": choice["model"], "model_label": model["label"], "tier": choice["tier"],
        "context": choice["context"], "kv_cache": choice["kv_cache"],
        "voice_device": choice["voice_device"],
        "history_default": choice["history_default"],
        "vram_need_gb": choice["vram_need_gb"],
        "model_file": str(models_dir / model["file"]),
        "model_ok": (models_dir / model["file"]).is_file(),
        "simulated": True,
    })
    return out


def sidecar_builtin_env(cfg: dict) -> dict:
    """Point the memory sidecar at the built-in server WITHOUT touching the
    user's own settings files: it reads its server + model from files whose
    paths these variables override."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    server_file = RUNTIME_DIR / "builtin_llm_server.txt"
    model_file = RUNTIME_DIR / "builtin_llm_model.txt"
    server_file.write_text(f"http://127.0.0.1:{cfg.get('port') or LLM_PORT}/v1", encoding="utf-8")
    model_file.write_text(builtin_model_name(cfg), encoding="utf-8")
    return {"CM_SERVER_FILE": str(server_file), "CM_MODEL_FILE": str(model_file)}


def voice_env(cfg: dict | None) -> dict:
    """Tiers under 16 GB run her voice on the processor (the card is full
    with the model); backend/start_gptsovits.py reads this."""
    if cfg and cfg.get("voice_device") == "cpu":
        return {"AMADEUS_VOICE_DEVICE": "cpu"}
    return {}


def physical_cores() -> int:
    """Real processor cores (not threads). Measured 2026-10-21 (Ryzen 7
    5800X3D): the processor voice is fastest with one thread per core."""
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    except Exception:
        pass
    logical = os.cpu_count() or 4
    return max(1, logical // 2) if logical >= 4 else logical


def voice_on_processor(cfg: dict | None) -> bool:
    """Her voice runs on the processor: a built-in tier says so, or there is
    no NVIDIA card (the voice engine falls back to the processor)."""
    return bool(cfg and cfg.get("voice_device") == "cpu") or shutil.which("nvidia-smi") is None


def voice_speed_env(cfg: dict | None) -> dict:
    """Voice engine env for a processor voice: one thread per core
    (0.88-0.95x -> 0.77-0.82x real time). Card voice: nothing. (Streaming
    mode 2 is the default for every voice since 2026-10-22 - backend/tts.py.)"""
    if not voice_on_processor(cfg):
        return {}
    n = str(physical_cores())
    return {"OMP_NUM_THREADS": n, "MKL_NUM_THREADS": n, "AMADEUS_VOICE_THREADS": n}


def _cm_python() -> Path:
    rel = Path("venv") / "Scripts" / "python.exe" if os.name == "nt" \
        else Path("venv") / "bin" / "python"
    return CM_DIR / rel


def _cm_marker() -> Path:
    """Install-complete marker: its existence means the venv holds ALL the
    pinned packages (it is written only after a fully successful pip run)."""
    return CM_DIR / ".venv_installed"


CM_SETUP_LABEL = "Setting up her memory (one time, about 1.3 GB)"


def _pip_install_sidecar(passthrough: bool = False) -> bool:
    """pip-install the sidecar's pinned requirements into its venv.
    Returns True on success (the marker is written by the caller only then).
    pip's "raw" progress mode feeds the progress line; a pip too old to know
    it is asked again without it."""
    req = CM_DIR / "requirements.txt"
    base = [str(_cm_python()), "-m", "pip", "install"]
    rest = ["--no-cache-dir", "-r", str(req)]
    code = run_tool(CM_SETUP_LABEL, base + ["--progress-bar", "raw"] + rest,
                    passthrough=passthrough)
    if code not in (0, 127) and any(
            "no such option" in t or "invalid choice" in t for t in _last_tail):
        code = run_tool(CM_SETUP_LABEL, base + rest, passthrough=passthrough)
    if code != 0:
        say("  WARNING: her memory could not be set up (details: "
            f"{LAUNCHER_LOG}). She works without long-term memory for now; "
            "Amadeus tries again at the next start.")
        return False
    return True


def _cm_base_python() -> Path | None:
    """Interpreter to build the sidecar venv from, if a better one exists.

    The sidecar's pinned package list needs Python 3.12+ (numpy 2.5.x), but
    the app environment the installer builds is 3.10. The installer also
    creates a dedicated 'amadeus-cm' conda environment (Python 3.13) for
    exactly this purpose; when it exists the venv is built from it, so the
    sidecar installs cleanly on EVERY fresh machine. Returns None when no
    such environment is present (falls back to this launcher's interpreter,
    which is fine on machines whose sidecar venv is already installed)."""
    conda = find_conda()
    if not conda:
        return None
    home = conda_home(conda)
    if home is None:
        return None
    name = env_name("amadeus-cm")
    if os.name == "nt":
        candidate = home / "envs" / name / "python.exe"
    else:
        candidate = home / "envs" / name / "bin" / "python"
    return candidate if candidate.exists() else None


def conda_home(conda: str) -> Path | None:
    """The conda installation folder (the one holding envs/) for whichever
    conda program PATH found. Usually two folders up (Scripts\\conda.exe,
    bin/conda), but some setups put miniconda3\\Library\\bin\\conda.BAT or
    condabin\\conda.bat first on PATH - "two up" then lands in Library\\ and
    the sidecar's own environment is not found (2026-10-18, test install:
    the sidecar venv was built from Python 3.10 and could never install).
    So walk up until a folder with envs/ (or conda-meta/) appears."""
    path = Path(conda).resolve()
    for folder in list(path.parents)[:4]:
        if (folder / "envs").is_dir() or (folder / "conda-meta").is_dir():
            return folder
    return None


# The sidecar's pinned packages need this Python or newer (numpy 2.5.x).
CM_MIN_PYTHON = (3, 12)


def _cm_venv_python_ok() -> bool:
    """Does the sidecar venv's Python meet CM_MIN_PYTHON? (Read from the
    venv's pyvenv.cfg; unreadable = assume fine, as before.)"""
    import re
    try:
        text = (CM_DIR / "venv" / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    m = re.search(r"^\s*version(?:_info)?\s*=\s*(\d+)\.(\d+)", text, re.M)
    if not m:
        return True
    return (int(m.group(1)), int(m.group(2))) >= CM_MIN_PYTHON


def ensure_cm_sidecar(base_python: Path | None = None,
                      passthrough: bool = False) -> bool:
    """Install the memory sidecar if it is not installed yet: create its
    venv and pip-install its pinned requirements. The installer runs this
    (`launcher.py --setup-memory`, base_python = its 'amadeus-cm'
    environment); at launch it only repairs a missing or unfinished install.
    An interrupted run (crash or PC off mid-download) can never masquerade
    as a complete install: the .venv_installed marker is written only after
    a fully successful pip run, so the next run redoes the missing part
    instead of starting a broken service. Returns True when the sidecar can
    start. Never raises - the sidecar is optional and its absence must not
    block the app."""
    if not (CM_DIR / "cm_sidecar.py").exists():
        return False
    req = CM_DIR / "requirements.txt"
    if not req.exists():
        return False
    if _cm_python().exists() and _cm_marker().exists():
        return True
    if _cm_python().exists() and not _cm_venv_python_ok():
        # An unfinished venv built from a Python too old for the pinned
        # packages (numpy 2.5 needs 3.12+) would fail on every start: start
        # it over from the right interpreter instead of retrying forever.
        note("Memory sidecar venv was built from a Python older than "
             f"{CM_MIN_PYTHON[0]}.{CM_MIN_PYTHON[1]} - rebuilding it.")
        shutil.rmtree(CM_DIR / "venv", ignore_errors=True)
    if _cm_python().exists():
        note("Memory sidecar venv exists but its install is unverified (an "
             "earlier run was interrupted?) - re-running its pip install.")
        if not _pip_install_sidecar(passthrough):
            return False
    else:
        base = base_python if base_python is not None else _cm_base_python()
        if base is None and sys.version_info[:2] < CM_MIN_PYTHON:
            say(f"  WARNING: her memory needs Python {CM_MIN_PYTHON[0]}."
                f"{CM_MIN_PYTHON[1]}+, and its own '{env_name('amadeus-cm')}' "
                "environment was not found. She works without long-term "
                "memory; running the installer again fixes this.")
            return False
        interpreter = str(base) if base is not None else sys.executable
        note(f"Memory sidecar not installed yet - building its venv from {interpreter}.")
        code = run_tool("Preparing her memory", [interpreter, "-m", "venv",
                                                 str(CM_DIR / "venv")],
                        passthrough=passthrough)
        if code != 0:
            say(f"  WARNING: her memory could not be set up (details: {LAUNCHER_LOG}). "
                "She works without long-term memory for now.")
            return False
        if not _pip_install_sidecar(passthrough):
            return False
    _cm_marker().write_text(
        "installed " + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n",
        encoding="utf-8")
    note("Memory sidecar installed.")
    return True

processes: list[subprocess.Popen] = []
log_handles: list[IO[str]] = []


def find_conda() -> str | None:
    candidates = [
        shutil.which("conda"),
        os.environ.get("CONDA_EXE"),
        str(Path.home() / "anaconda3" / "bin" / "conda"),
        str(Path.home() / "miniconda3" / "bin" / "conda"),
        str(Path.home() / "miniforge3" / "bin" / "conda"),
        str(Path.home() / "opt" / "anaconda3" / "bin" / "conda"),
        "/opt/anaconda3/bin/conda",
        "/opt/anaconda3/condabin/conda",
        "/opt/homebrew/Caskroom/miniconda/base/bin/conda",
        r"C:\ProgramData\anaconda3\Scripts\conda.exe",
        str(Path.home() / "anaconda3" / "Scripts" / "conda.exe"),
        str(Path.home() / "miniconda3" / "Scripts" / "conda.exe"),
    ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    return None


def find_npm() -> str | None:
    names = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)

    for name in names:
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        "/opt/homebrew/bin/npm",
        "/usr/local/bin/npm",
        # Node.js the macOS installer may place in the user's home folder
        str(Path.home() / ".amadeus-tools" / "node" / "bin" / "npm"),
        r"C:\Program Files\nodejs\npm.cmd",
    ]

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    return None


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _pids_on_port_unix(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []

    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def _kill_port_unix(port: int) -> None:
    pids = _pids_on_port_unix(port)
    if not pids:
        return

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        remaining = _pids_on_port_unix(port)
        if not remaining:
            return
        time.sleep(0.25)

    for pid in _pids_on_port_unix(port):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _kill_port_windows(port: int) -> None:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        check=False,
    )

    pids: set[int] = set()

    for line in result.stdout.splitlines():
        if "LISTENING" not in line.upper():
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        local_address = parts[1]
        pid_text = parts[-1]

        if not local_address.endswith(f":{port}"):
            continue

        if pid_text.isdigit():
            pids.add(int(pid_text))

    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def clear_port(name: str, port: int) -> None:
    if not port_open(port):
        return

    note(f"Clearing stale {name} process on port {port}...")

    if os.name == "nt":
        _kill_port_windows(port)
    else:
        _kill_port_unix(port)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not port_open(port):
            note(f"Port {port} is free.")
            return
        time.sleep(0.25)

    raise RuntimeError(
        f"Could not free port {port} for {name}. "
        "A protected/system process may be using it."
    )


def clear_stale_amadeus_processes(extra: dict | None = None) -> None:
    note("Cleaning stale Amadeus processes...")
    ports = {**AMADEUS_PORTS, **(extra or {})}
    if any(port_open(port) for port in ports.values()):
        say("  (Closing an earlier Amadeus that was still running.)")
    for name, port in ports.items():
        clear_port(name, port)

    note("Cleanup complete.")


def wait_for_port(name: str, port: int, process: subprocess.Popen, timeout: int) -> None:
    note(f"Waiting for {name} on port {port}...")

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if port_open(port):
            note(f"{name} is ready.")
            return

        if process.poll() is not None:
            raise RuntimeError(
                f"{name} exited during startup. Details: {LOG_DIR}"
            )

        time.sleep(1)

    raise TimeoutError(
        f"{name} did not become ready within {timeout} seconds. "
        f"Details: {LOG_DIR}"
    )


def open_log(name: str) -> IO[str]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handle = (LOG_DIR / f"{name}.log").open("w", encoding="utf-8")
    log_handles.append(handle)
    return handle


def start_process(name: str, command: list[str], cwd: Path,
                  extra_env: dict | None = None) -> subprocess.Popen:
    log = open_log(name)

    env = dict(os.environ)
    env.update(extra_env or {})
    env["PYTHONUNBUFFERED"] = "1"  # print() reaches the log live, not on exit
    # Every Python service writes its console (this UTF-8 log file, see
    # open_log) in UTF-8 whatever the user's Windows locale is: on an
    # English PC the default Windows-1252 cannot hold her Japanese lines
    # and one print used to crash the request (2026-10-07, fresh en-US
    # VM - 500 on every chat message).
    env["PYTHONIOENCODING"] = "utf-8"
    kwargs: dict = {
        "cwd": str(cwd),
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "text": True,
        "env": env,
    }

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    processes.append(process)
    _add_to_job(process)
    return process


# ---------------------------------------------------------------------------
# Windows: every part the launcher starts goes into one "job" that Windows
# ends - with everything those parts started, e.g. the backend's
# llama-server - the moment the launcher process ends, however it ends
# (Ctrl+C, the window's X / Alt+F4, Task Manager, a crash). Closing the
# window used to leave her built-in model running, holding ~10 GB of
# graphics memory (2026-10-19): Windows ends the window's own processes
# first, the backend among them, and its hidden llama-server child was
# orphaned before the launcher's cleanup could reach it. The browser is
# NOT in the job (it is started separately), so a browser the user had
# open is never closed.
# ---------------------------------------------------------------------------
_job_handle = None


def _windows_job():
    """The launcher's kill-on-close job (created once), or None."""
    global _job_handle
    if os.name != "nt":
        return None
    if _job_handle is not None:
        return _job_handle or None
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMITS(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMITS),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                 wintypes.LPVOID, wintypes.DWORD)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        note("Could not create the shutdown job - parts may outlive a closed window.")
        _job_handle = 0
        return None
    limits = EXTENDED_LIMITS()
    limits.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        note("Could not set up the shutdown job - parts may outlive a closed window.")
        _job_handle = 0
        return None
    _job_handle = job   # kept open for the launcher's whole life (closing it ends the job)
    return job


def _add_to_job(process: subprocess.Popen) -> None:
    job = _windows_job()
    if job is None:
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
        note(f"Could not add process {process.pid} to the shutdown job "
             f"(error {ctypes.get_last_error()}).")


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)

            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


_cleanup_lock = threading.Lock()
_cleaned_up = False
# Set by run() when her built-in model may be running this session (the
# backend starts it; see cleanup()).
_builtin_this_run = False


def cleanup() -> None:
    """Stop every part of Amadeus. Runs once, from whichever comes first:
    the normal exit (Ctrl+C, an error) or Windows closing the launcher
    window (X, Alt+F4, sign-out, shutdown - see install_close_handler)."""
    global _cleaned_up
    with _cleanup_lock:
        if _cleaned_up:
            return
        _cleaned_up = True
    if processes:
        try:
            say("\nShutting down Amadeus...")
        except (OSError, ValueError):
            pass   # the window may already be gone

    for process in reversed(processes):
        terminate_process_tree(process)

    # Her built-in model is the backend's child and goes with its process
    # tree; should anything still hold its port (it was started another
    # way, or outlived its parent), end it too - it holds ~10 GB of
    # graphics memory. Only in built-in mode: in custom mode 8091 may be
    # the user's own server.
    if _builtin_this_run:
        try:
            clear_port("built-in model", LLM_PORT)
        except Exception as exc:
            note(f"Could not free port {LLM_PORT}: {exc}")

    for handle in log_handles:
        try:
            handle.close()
        except Exception:
            pass


# Keeps the Windows callback alive (ctypes would otherwise free it).
_close_handler = None

# Windows console events: 0 Ctrl+C, 1 Ctrl+Break (Python's own handling),
# 2 the window is closed (X / Alt+F4), 5 sign-out, 6 shutdown.
_CLOSE_EVENTS = (2, 5, 6)


def _on_console_event(event: int) -> int:
    """Windows calls this (on its own thread) for console events. For a
    closing window it allows about 5 seconds before ending the process -
    enough for cleanup(), which used to be skipped, so her built-in model
    kept running in the background holding graphics memory (2026-10-19,
    llama-server.exe still running after closing the window with X)."""
    if event in _CLOSE_EVENTS:
        note(f"Windows is closing the launcher (console event {event}) - shutting everything down.")
        cleanup()
        return 1
    return 0   # Ctrl+C / Ctrl+Break: Python's own handling (KeyboardInterrupt)


def install_close_handler() -> None:
    """Make closing the launcher window shut Amadeus down like Ctrl+C does.
    Windows: a console control handler. macOS / Linux: closing the Terminal
    window sends SIGHUP (and a shutdown SIGTERM)."""
    global _close_handler
    if os.name == "nt":
        import ctypes
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        _close_handler = handler_type(_on_console_event)
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_close_handler, True):
            note("Could not register the window-close handler.")
        return

    def on_signal(signum, _frame):
        note(f"Signal {signum} - shutting everything down.")
        cleanup()
        os._exit(0)

    for name in ("SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, on_signal)


def check_required_paths() -> None:
    required = [
        BACKEND_DIR / "main.py",
        BACKEND_DIR / "start_gptsovits.py",
        FRONTEND_DIR / "package.json",
        GPT_DIR,
    ]

    missing = [path for path in required if not path.exists()]

    if missing:
        details = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Required Amadeus files are missing:\n{details}")


def ensure_frontend_dependencies(npm: str) -> None:
    node_modules = FRONTEND_DIR / "node_modules"

    if node_modules.exists():
        return

    note("Frontend dependencies are not installed - running npm install.")
    code = run_tool("Setting up the web page (one time)",
                    [npm, "install", "--no-audit", "--no-fund"], FRONTEND_DIR)
    if code != 0:
        raise RuntimeError(f"Setting up the web page failed. Details: {LAUNCHER_LOG}")

    note("Frontend dependencies installed.")


def preflight(conda: str, npm: str) -> None:
    check_required_paths()

    note("Preflight")
    note(f"  Project : {PROJECT_ROOT}")
    note(f"  Conda   : {conda}")
    note(f"  npm     : {npm}")
    note(f"  Python  : {sys.executable}")
    if env_suffix():
        note(f"  Envs    : this install's own (suffix '{env_suffix()}')")


# Browsers that accept Chromium's flags (matched on the executable's base
# name, so it works no matter where the user installed the browser).
_CHROMIUM_EXE_HINTS = ("chrome", "msedge", "edge", "brave", "chromium",
                       "vivaldi")

# Amadeus opens in her OWN app window: a Chromium browser started with
# --app (no tabs or address bar) and its own profile folder. The profile
# makes it a separate browser instance, so the autoplay flag always
# applies and her first line can speak at once - with the user's normal
# profile, an already-open Chrome ignored the flag and her voice waited
# for a click (2026-10-17 / 2026-10-18 test installs). Her UI settings
# live in this profile (the user's normal browser profile is untouched).
APP_PROFILE_DIR = RUNTIME_DIR / "app-window"


def _is_chromium(exe: str) -> bool:
    base = Path(exe).name.lower().removesuffix(".exe")
    return any(hint in base for hint in _CHROMIUM_EXE_HINTS)


def app_window_args(url: str) -> list[str]:
    """The Chromium flags for her own app window."""
    APP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return [
        f"--app={url}",
        f"--user-data-dir={APP_PROFILE_DIR}",
        "--autoplay-policy=no-user-gesture-required",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,860",
    ]


def _windows_default_browser_exe() -> str | None:
    """The default browser's program, from Windows' own records (the exe the
    browser registered at install time - no path guessing)."""
    import re as _re
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice") as k:
        prog_id, _ = winreg.QueryValueEx(k, "ProgId")
    command = None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT):
        try:
            with winreg.OpenKey(root, prog_id + r"\shell\open\command") as k:
                command, _ = winreg.QueryValueEx(k, None)
            break
        except OSError:
            continue
    if not command:
        return None
    # e.g. "C:\Program Files\...\chrome.exe" --single-argument %1 - the exe
    # is the leading token and may be quoted (paths with spaces).
    m = _re.match(r'^"([^"]+)"', command)
    exe = m.group(1) if m else (command.split()[0] if command.split() else "")
    return exe if exe and Path(exe).is_file() else None


def _windows_edge_exe() -> str | None:
    """Microsoft Edge (part of every Windows 10/11), from its App Paths entry."""
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                exe, _ = winreg.QueryValueEx(k, None)
            if exe and Path(exe).is_file():
                return exe
        except OSError:
            continue
    return None


def _open_browser_windows(url: str) -> bool:
    """Her app window in the default browser when it is Chromium-based
    (Chrome, Edge, Brave, ...), else in Edge; a plain browser tab only
    when neither works. Returns False to let the caller fall back."""
    try:
        exe = _windows_default_browser_exe()
    except Exception as exc:
        note(f"Could not read the default browser ({exc}).")
        exe = None
    if not exe or not _is_chromium(exe):
        try:
            edge = _windows_edge_exe()
        except Exception:
            edge = None
        if edge:
            note(f"Default browser {Path(exe).name if exe else '(unknown)'} cannot make an "
                 "app window - using Microsoft Edge.")
            exe = edge
    if not exe or not _is_chromium(exe):
        return False
    try:
        subprocess.Popen([exe, *app_window_args(url)],
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    except OSError as exc:
        note(f"Could not open her app window ({exc}).")
        return False
    note(f"Opening her app window with {Path(exe).name}.")
    return True


def _open_browser_macos(url: str) -> bool:
    """macOS: her app window in the default browser when it is a known
    Chromium app (asked from Launch Services via plutil + JSON), else in
    Google Chrome / Microsoft Edge when installed; else the default
    browser plainly. `open -na` = a new instance, so the flags apply."""
    import json
    bundle_to_app = {
        "com.google.Chrome": "Google Chrome",
        "com.brave.Browser": "Brave Browser",
        "com.microsoft.edgemac": "Microsoft Edge",
        "org.chromium.Chromium": "Chromium",
    }
    try:
        app = None
        plist = (Path.home() /
                 "Library/Preferences/com.apple.LaunchServices/"
                 "com.apple.launchservices.secure.plist")
        if plist.is_file():
            out = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(plist)],
                                 capture_output=True, text=True, timeout=15).stdout
            for entry in json.loads(out).get("LSHandlers", []):
                if entry.get("LSHandlerPreferredFamily") == "https":
                    app = bundle_to_app.get(entry.get("LSHandlerRoleAll"))
                    break
        if app is None:
            app = next((name for name in ("Google Chrome", "Microsoft Edge", "Brave Browser")
                        if Path(f"/Applications/{name}.app").exists()), None)
        if app is None:
            subprocess.Popen(["open", url])
            note("No Chromium browser found; opening the default browser plainly.")
            return True
        subprocess.Popen(["open", "-na", app, "--args", *app_window_args(url)])
        note(f"Opening her app window with {app}.")
        return True
    except Exception as exc:
        note(f"Could not resolve the default browser ({exc}); "
             "falling back to webbrowser.open().")
        return False


def _open_browser_linux(url: str) -> bool:
    """Linux: xdg-settings reports the default browser's .desktop file;
    parse its Exec= line and open her app window when it is Chromium-based."""
    try:
        desktop_name = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        if not desktop_name:
            return False
        search_dirs = [Path("/usr/share/applications"),
                       Path("/usr/local/share/applications"),
                       Path.home() / ".local/share/applications"]
        desktop = next((d / desktop_name for d in search_dirs
                        if (d / desktop_name).is_file()), None)
        if desktop is None:
            return False
        exec_line = None
        for line in desktop.read_text().splitlines():
            if line.lower().startswith("exec="):
                exec_line = line.split("=", 1)[1].strip()
                break
        if not exec_line:
            return False
        # Drop URL placeholders (%f %F %u %U) and quotes.
        parts = [tok.strip('"') for tok in exec_line.split() if not tok.startswith("%")]
        if not parts:
            return False
        if _is_chromium(parts[0]):
            parts = [parts[0], *app_window_args(url)]
        else:
            parts.append(url)
        subprocess.Popen(parts, start_new_session=True)
        note(f"Opening the WebUI with {Path(parts[0]).name}.")
        return True
    except Exception as exc:
        note(f"Could not resolve the default browser ({exc}); "
             "falling back to webbrowser.open().")
        return False


def _open_browser(url: str) -> None:
    """Open the WebUI in her own app window (see APP_PROFILE_DIR). Never
    guesses install paths: each OS resolves the browser through its own
    records. Any failure falls back to plain webbrowser.open() - the app
    can never fail to open."""
    if sys.platform == "win32":
        if _open_browser_windows(url):
            return
    elif sys.platform == "darwin":
        if _open_browser_macos(url):
            return
    else:
        if _open_browser_linux(url):
            return
    webbrowser.open(url)

def gptsovits_command(conda: str) -> list[str]:
    """The voice engine runs in its own conda environment."""
    return [
        conda,
        "run",
        "-n",
        env_name("GPTSoVITS"),
        "--no-capture-output",
        "python",
        str(BACKEND_DIR / "start_gptsovits.py"),
    ]


def _secs(started: float) -> str:
    return f"{int(time.monotonic() - started)} s"


def run(no_browser: bool = False, simulate_vram_gb: int | None = None,
        no_model: bool = False) -> None:
    conda = find_conda()
    npm = find_npm()
    builtin = load_builtin_config()
    say(f"Amadeus is starting. (Details: {LAUNCHER_LOG})")
    say()
    if builtin and simulate_vram_gb is not None:
        say(f"  TEST: pretending the graphics card has {simulate_vram_gb} GB.")
        builtin = apply_simulated_vram(builtin, simulate_vram_gb)
        if builtin is None:
            say("  TEST: such a card gets no local model - starting without it.")

    if not conda:
        raise RuntimeError(
            "Conda was not found. Install Anaconda/Miniconda or make conda available on PATH."
        )

    if not npm:
        raise RuntimeError(
            "npm was not found. Install Node.js before launching the WebUI."
        )

    preflight(conda, npm)
    clear_stale_amadeus_processes({"built-in model": LLM_PORT} if builtin else None)
    ensure_frontend_dependencies(npm)

    # Her built-in model: the app's backend loads it (backend/builtin_server.py,
    # so Settings can unload / load / switch it). The launcher only decides
    # WHETHER it loads at start: not with "start without her model", and the
    # user is asked first when the graphics card is too full for it (games).
    # A missing model never stops the app - the backend reports it.
    load_model = False
    if builtin:
        global _builtin_this_run
        _builtin_this_run = True
        ready, why = builtin_files_ready(builtin)
        note(f"Built-in model: {Path(selected_model_file(builtin)).name}, "
             f"{builtin.get('context')} context, {builtin.get('kv_cache')} memory, "
             f"voice on the {'processor' if builtin.get('voice_device') == 'cpu' else 'graphics card'}.")
        if no_model:
            say("  Her built-in model will not be loaded (load it in Settings -> Connection).")
        elif not ready:
            if "llama.cpp" in why:
                say(f"  WARNING: her built-in model cannot start - {why}. "
                    "Run the installer again to finish setting it up.")
            else:
                say("  Her built-in model has no model file yet - put a GGUF file in "
                    f"{Path(str(builtin.get('model_file') or '')).parent} and load it in "
                    "Settings -> Connection (or run the installer again and choose A).")
        else:
            need = builtin_vram_need_gb(builtin)
            load_model = ask_about_graphics_memory(need) if need else True
            if not load_model:
                say("  Starting without her model (load it in Settings -> Connection).")
    backend_env = {"AMADEUS_LOAD_MODEL": "1" if load_model else "0"}
    voice_extra = voice_speed_env(builtin)
    if builtin and builtin.get("simulated"):
        import json
        backend_env["AMADEUS_BUILTIN_OVERRIDE"] = json.dumps(builtin)

    begin("Her voice")
    started = time.monotonic()
    gpt = start_process("gptsovits", gptsovits_command(conda), PROJECT_ROOT,
                        {**voice_env(builtin), **voice_extra})
    wait_for_port("GPT-SoVITS", GPT_PORT, gpt, timeout=180)
    end(f"ready ({_secs(started)})")

    # CharacterMemory sidecar: the slowest starter (CPU embedder + lore
    # index, ~30-60s with the shipped seed; a one-time cold lore build can
    # take ~10 min, hence the 600s wait), so it starts here and is waited
    # on LAST - its startup overlaps the other children. Soft-fail on
    # purpose: the app is fully functional without it (the bridge degrades
    # to the no-memory baseline). On a RAM-constrained machine its first
    # start can die mid-load (2026-10-07: the 18 GB test box, sharing RAM
    # with a local 27 B model, failed a 55 MB allocation), so the launcher
    # retries twice more before giving up. The installer sets it up; here
    # ensure_cm_sidecar() only repairs a missing or unfinished install.
    cm = None
    cm_available = False
    cm_env = sidecar_builtin_env(builtin) if builtin else None
    if not (_cm_python().exists() and _cm_marker().exists()) and \
            (CM_DIR / "cm_sidecar.py").exists():
        say("  Her memory needs a one-time setup first (a few minutes).")
    if ensure_cm_sidecar():
        begin("Her memory")
        cm_started = time.monotonic()
        cm = start_process(
            "cm-sidecar",
            [str(_cm_python()), str(CM_DIR / "cm_sidecar.py")],
            CM_DIR,
            cm_env,
        )
        end("starting in the background")
        cm_available = True
    else:
        say("  Her memory is not available - she works without long-term memory.")

    begin("The app")
    started = time.monotonic()
    backend = start_process(
        "backend",
        [sys.executable, "main.py"],
        BACKEND_DIR,
        backend_env,
    )
    wait_for_port("backend", BACKEND_PORT, backend, timeout=45)
    end(f"ready ({_secs(started)})")
    if load_model:
        say("  Her built-in model ... loading in the background "
            "(this window says when it is ready)")

    begin("The web page")
    started = time.monotonic()
    frontend = start_process(
        "frontend",
        [npm, "run", "dev", "--", "--host", "127.0.0.1"],
        FRONTEND_DIR,
    )
    wait_for_port("WebUI", FRONTEND_PORT, frontend, timeout=45)
    end(f"ready ({_secs(started)})")

    if cm_available:
        begin("Her memory")
        for attempt in range(1, 4):
            try:
                wait_for_port("cm-sidecar", CM_PORT, cm, timeout=600)
                end(f"ready ({_secs(cm_started)})")
                break
            except (TimeoutError, RuntimeError) as e:
                terminate_process_tree(cm)
                if attempt < 3:
                    note(f"The memory sidecar did not come up on attempt {attempt} "
                         f"({e}) - trying again in 30 s.")
                    time.sleep(30)
                    cm = start_process(
                        "cm-sidecar",
                        [str(_cm_python()), str(CM_DIR / "cm_sidecar.py")],
                        CM_DIR,
                        cm_env,
                    )
                else:
                    end("did not start")
                    say("  WARNING: her memory did not start after 3 tries - she "
                        "works without long-term memory. Details: "
                        f"{LOG_DIR / 'cm-sidecar.log'}")
                    cm = None

    url = f"http://127.0.0.1:{FRONTEND_PORT}/"

    say("\n========================================")
    say(" Amadeus is online")
    say(f" {url}")
    say(" Press Ctrl+C to shut everything down.")
    say("========================================\n")

    if not no_browser:
        _open_browser(url)

    cm_warned = False
    model_seen: dict | None = None
    watch_model = BUILTIN_CONFIG.is_file()   # a built-in model is set up (either mode)
    next_model_check = 0.0
    while True:
        for name, process in (
            ("GPT-SoVITS", gpt),
            ("backend", backend),
            ("WebUI", frontend),
        ):
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"{name} stopped unexpectedly with exit code {return_code}. "
                    f"Details: {LOG_DIR}"
                )
        # The memory sidecar is OPTIONAL: if it dies the app keeps working
        # (the bridge degrades to the no-memory baseline), so a dead sidecar
        # warns once instead of tearing the whole stack down.
        if cm is not None and not cm_warned and cm.poll() is not None:
            cm_warned = True
            say(f"  WARNING: her memory stopped (exit {cm.returncode}) - she "
                "works without long-term memory until the next start. "
                f"Details: {LOG_DIR / 'cm-sidecar.log'}")
        # Her built-in model: say each load / unload / stop in this window.
        if watch_model and time.monotonic() >= next_model_check:
            next_model_check = time.monotonic() + MODEL_WATCH_SECONDS
            now = builtin_model_status()
            if now is not None:
                line = describe_model_change(model_seen, now)
                if line:
                    say(line)
                model_seen = now

        time.sleep(1)


# ---------------------------------------------------------------------------
# Her built-in model in the launcher window (2026-10-19): the backend
# runs it, so Settings' Load / Unload / Switch and a switch to a custom model
# happen there - the launcher asks the backend every few seconds and prints
# each change, so the window always says whether her model is running.
# ---------------------------------------------------------------------------
MODEL_WATCH_SECONDS = 2


def builtin_model_status(timeout: float = 2.0) -> dict | None:
    """The backend's /builtinModel answer (mode + server state), or None
    when it does not answer (still starting, busy) - then nothing is said."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/builtinModel",
                                    timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("available"):
        return None
    server = data.get("server") if isinstance(data.get("server"), dict) else {}
    return {"mode": data.get("mode"), "state": server.get("state"),
            "file": Path(str(server.get("file") or "")).name,
            "error": str(server.get("error") or "")}


def describe_model_change(prev: dict | None, now: dict) -> str | None:
    """The launcher-window line for a change of her model's state, or None.
    prev None = the first look after start-up: only a finished load or a
    failure is news then (the start-up lines already said what began)."""
    key = (now["mode"], now["state"], now["file"])
    if prev is not None and key == (prev["mode"], prev["state"], prev["file"]):
        return None
    name = now["file"] or "her model"
    state = now["state"]
    if state == "loading":
        return None if prev is None else f"  Her built-in model: loading {name}..."
    if state == "loaded":
        return f"  Her built-in model: loaded ({name}) - ready to talk."
    if state == "failed":
        reason = now["error"].splitlines()[0] if now["error"] else "see logs/llm.log"
        return f"  WARNING: her built-in model could not load ({name}): {reason}"
    # unloaded
    if prev is None or prev["state"] == "unloaded" and prev["mode"] == now["mode"]:
        return None
    if now["mode"] != "builtin":
        return ("  Her built-in model: stopped - she now uses your own model "
                "(Settings -> Connection). Its graphics memory is free.")
    return "  Her built-in model: unloaded - its graphics memory is free."


def setup_memory(cm_python: str | None) -> int:
    """`launcher.py --setup-memory`: the installer's memory step. Prints
    pip's own output (the installer logs it and draws its progress bar from
    it); exit 0 when her memory is ready to start."""
    base = Path(cm_python) if cm_python else None
    if base is not None and not base.is_file():
        say(f"The memory environment's Python was not found: {base}")
        return 1
    return 0 if ensure_cm_sidecar(base_python=base, passthrough=True) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the complete Amadeus stack.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the WebUI in a browser.",
    )
    parser.add_argument(
        "--simulate-vram-gb",
        type=int,
        default=None,
        help="TEST: run the built-in model with the settings a card with this "
             "much graphics memory would get (its model must be downloaded).",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Start without loading her built-in model (load it later in "
             "Settings -> Connection). The 'without her model' shortcut uses this.",
    )
    parser.add_argument(
        "--setup-memory",
        action="store_true",
        help="Only set up her memory (the installer runs this), then exit.",
    )
    parser.add_argument(
        "--cm-python",
        default=None,
        help="With --setup-memory: the Python to build the memory venv from "
             "(the installer passes its 'amadeus-cm' environment).",
    )
    args = parser.parse_args()

    if args.setup_memory:
        # Its own log file: the next start's launcher.log must not erase it.
        global LAUNCHER_LOG
        LAUNCHER_LOG = LOG_DIR / "memory-setup.log"
        open_launcher_log()
        return setup_memory(args.cm_python)

    open_launcher_log()
    install_close_handler()
    try:
        run(no_browser=args.no_browser, simulate_vram_gb=args.simulate_vram_gb,
            no_model=args.no_model)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        say(f"\nERROR: {error}", file=sys.stderr)
        return 1
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

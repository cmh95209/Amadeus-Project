from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import IO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
GPT_DIR = PROJECT_ROOT / "GPT-SoVITS"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
LOG_DIR = RUNTIME_DIR / "logs"

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


def builtin_model_name(cfg: dict) -> str:
    """The name the model is served under (--alias). backend/builtin_llm.py
    derives the same name from the same file."""
    return Path(str(cfg.get("model_file") or "")).stem or "builtin-model"


def builtin_files_ready(cfg: dict) -> tuple[bool, str]:
    """(True, "") when llama-server and the model are on disk, else
    (False, plain-English reason)."""
    exe = str(cfg.get("server_exe") or "")
    model = str(cfg.get("model_file") or "")
    if not cfg.get("server_ok") or not Path(exe).is_file():
        return False, "her model's helper program (llama.cpp) is not installed"
    if not cfg.get("model_ok") or not Path(model).is_file():
        return False, f"her model file is not downloaded ({Path(model).name or 'unknown'})"
    return True, ""


def llama_server_command(cfg: dict) -> list[str]:
    """llama-server with the tier's settings; sampling = Unsloth's
    recommendation for Gemma 4 (the Model Sampling panel can override).
    Thinking is NOT set server-wide: every request already says it (the
    backend's normal client "off", Deep thinking "on", the memory sidecar
    "off"), and b11400 deprecates --chat-template-kwargs for this while its
    replacement (--reasoning off) might also refuse Deep thinking's per-
    request "on". The backend's thought filter catches any leak."""
    cmd = [
        str(cfg["server_exe"]),
        "-m", str(cfg["model_file"]),
        "--host", "127.0.0.1",
        "--port", str(cfg.get("port") or LLM_PORT),
        "--alias", builtin_model_name(cfg),
        "-c", str(int(cfg.get("context") or 16384)),
        "-ngl", str(int(cfg.get("gpu_layers") if cfg.get("gpu_layers") is not None else 999)),
        "--jinja",
        "--temp", "1.0", "--top-p", "0.95", "--top-k", "64",
    ]
    if cfg.get("kv_cache") == "q8_0":
        # A compressed conversation memory needs flash attention on.
        cmd += ["-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0"]
    drafter = str(cfg.get("drafter_file") or "")
    if cfg.get("use_drafter") and cfg.get("drafter_ok") and Path(drafter).is_file():
        # Gemma 4's own speed-up drafter (Unsloth's recommended settings).
        cmd += ["-md", drafter, "--spec-type", "draft-mtp", "--spec-draft-n-max", "4"]
    return cmd


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
        "model_file": str(models_dir / model["file"]),
        "model_ok": (models_dir / model["file"]).is_file(),
        "drafter_file": str(models_dir / model["drafter"]["file"]),
        "drafter_ok": (models_dir / model["drafter"]["file"]).is_file(),
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


def _cm_python() -> Path:
    rel = Path("venv") / "Scripts" / "python.exe" if os.name == "nt" \
        else Path("venv") / "bin" / "python"
    return CM_DIR / rel


def _cm_marker() -> Path:
    """Install-complete marker: its existence means the venv holds ALL the
    pinned packages (it is written only after a fully successful pip run)."""
    return CM_DIR / ".venv_installed"


def _pip_install_sidecar() -> bool:
    """pip-install the sidecar's pinned requirements into its venv.
    Returns True on success (the marker is written by the caller only then)."""
    req = CM_DIR / "requirements.txt"
    try:
        subprocess.run([str(_cm_python()), "-m", "pip", "install",
                        "--no-cache-dir", "-r", str(req)], check=True)
        return True
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"[Launcher] WARNING: memory sidecar install failed ({e}) - "
              "the app runs without long-term memory (retried at next "
              "start).")
        return False


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


def ensure_cm_sidecar() -> bool:
    """First-run install of the memory sidecar (mirrors the npm-install
    pattern): create its venv and pip-install its pinned requirements.
    An interrupted first run (crash or PC off mid-download) can never
    masquerade as a complete install: the .venv_installed marker is written
    only after a fully successful pip run, so the next launch re-runs the
    missing part instead of starting a broken service. Returns True when
    the sidecar can start. Never raises - the sidecar is optional and its
    absence must not block the app."""
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
        print("[Launcher] Memory sidecar venv was built from a Python older "
              f"than {CM_MIN_PYTHON[0]}.{CM_MIN_PYTHON[1]} - rebuilding it...")
        shutil.rmtree(CM_DIR / "venv", ignore_errors=True)
    if _cm_python().exists():
        print("[Launcher] Memory sidecar venv exists but its install is "
              "unverified (an earlier run was interrupted?) - re-running "
              "its pip install...")
        if not _pip_install_sidecar():
            return False
    else:
        base = _cm_base_python()
        if base is None and sys.version_info[:2] < CM_MIN_PYTHON:
            print("[Launcher] WARNING: the memory sidecar needs Python "
                  f"{CM_MIN_PYTHON[0]}.{CM_MIN_PYTHON[1]}+, and its own "
                  f"'{env_name('amadeus-cm')}' environment was not found - "
                  "the app runs without long-term memory. Re-running the "
                  "installer creates that environment.")
            return False
        source = ("the dedicated 'amadeus-cm' environment"
                  if base is not None else "the app environment")
        print("[Launcher] Memory sidecar not installed yet - one-time setup "
              f"(creates its venv from {source} and downloads packages, "
              "a few minutes)...")
        try:
            interpreter = str(base) if base is not None else sys.executable
            subprocess.run([interpreter, "-m", "venv", str(CM_DIR / "venv")],
                           check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"[Launcher] WARNING: memory sidecar venv creation "
                  f"failed ({e}) - the app runs without long-term memory.")
            return False
        if not _pip_install_sidecar():
            return False
    _cm_marker().write_text(
        "installed " + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n",
        encoding="utf-8")
    print("[Launcher] Memory sidecar installed.")
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

    print(f"[Launcher] Clearing stale {name} process on port {port}...")

    if os.name == "nt":
        _kill_port_windows(port)
    else:
        _kill_port_unix(port)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not port_open(port):
            print(f"[Launcher] Port {port} is free.")
            return
        time.sleep(0.25)

    raise RuntimeError(
        f"Could not free port {port} for {name}. "
        "A protected/system process may be using it."
    )


def clear_stale_amadeus_processes(extra: dict | None = None) -> None:
    print("[Launcher] Cleaning stale Amadeus processes...")

    for name, port in {**AMADEUS_PORTS, **(extra or {})}.items():
        clear_port(name, port)

    print("[Launcher] Cleanup complete.")


def wait_for_port(name: str, port: int, process: subprocess.Popen, timeout: int) -> None:
    print(f"[Launcher] Waiting for {name} on port {port}...")

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if port_open(port):
            print(f"[Launcher] {name} is ready.")
            return

        if process.poll() is not None:
            raise RuntimeError(
                f"{name} exited during startup. Check .runtime/logs for details."
            )

        time.sleep(1)

    raise TimeoutError(
        f"{name} did not become ready within {timeout} seconds. "
        "Check .runtime/logs for details."
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
    return process


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


def cleanup() -> None:
    if processes:
        print("\n[Launcher] Shutting down Amadeus...")

    for process in reversed(processes):
        terminate_process_tree(process)

    for handle in log_handles:
        try:
            handle.close()
        except Exception:
            pass


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

    print("[Launcher] Frontend dependencies are not installed.")
    print("[Launcher] Running npm install...")

    result = subprocess.run([npm, "install"], cwd=str(FRONTEND_DIR))

    if result.returncode != 0:
        raise RuntimeError("npm install failed.")

    print("[Launcher] Frontend dependencies installed.")


def preflight(conda: str, npm: str) -> None:
    check_required_paths()

    print("[Launcher] Preflight")
    print(f"  Project : {PROJECT_ROOT}")
    print(f"  Conda   : {conda}")
    print(f"  npm     : {npm}")
    print(f"  Python  : {sys.executable}")
    if env_suffix():
        print(f"  Envs    : this install's own (suffix '{env_suffix()}')")


# Browsers that accept Chromium's autoplay flag (matched on the executable's
# base name, so it works no matter where the user installed the browser).
_CHROMIUM_EXE_HINTS = ("chrome", "msedge", "edge", "brave", "chromium",
                       "vivaldi")


def _is_chromium(exe: str) -> bool:
    base = Path(exe).name.lower().removesuffix(".exe")
    return any(hint in base for hint in _CHROMIUM_EXE_HINTS)


def _open_browser_windows(url: str) -> bool:
    """Open the URL in the user's DEFAULT browser (Windows' own registry
    records - the exact exe the browser registered at install time, so no
    path guessing), adding Chromium's autoplay flag for Chromium-family
    browsers. Returns False to let the caller fall back."""
    import winreg
    try:
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
            return False
        # The command is the launch string the browser registered itself
        # with, e.g.: "C:\Program Files\...\chrome.exe" --single-argument %1
        # The exe path is the leading token and may be quoted (paths with
        # spaces), so pull it out of the leading quoted string when present.
        import re as _re
        _m = _re.match(r'^"([^"]+)"', command)
        exe = _m.group(1) if _m else (command.split()[0] if command.split() else "")
        if not exe or not Path(exe).is_file():
            return False
        if _is_chromium(exe):
            args = [exe, "--autoplay-policy=no-user-gesture-required", url]
        else:
            args = [exe, url]
        subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        print(f"[Launcher] Opening WebUI with the default browser ({Path(exe).name}).")
        return True
    except Exception as exc:
        print(f"[Launcher] Could not resolve the default browser ({exc}); "
              "falling back to webbrowser.open().")
        return False


def _open_browser_macos(url: str) -> bool:
    """macOS: ask Launch Services (via plutil + JSON) for the default https
    handler, then `open -a` that app with the Chromium autoplay flag. The
    app is resolved by Launch Services by name - no path guessing."""
    import json
    bundle_to_app = {
        "com.google.Chrome": "Google Chrome",
        "com.brave.Browser": "Brave Browser",
        "com.microsoft.edgemac": "Microsoft Edge",
        "org.chromium.Chromium": "Chromium",
    }
    try:
        plist = (Path.home() /
                 "Library/Preferences/com.apple.LaunchServices/"
                 "com.apple.launchservices.secure.plist")
        if not plist.is_file():
            return False
        out = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(plist)],
                             capture_output=True, text=True, timeout=15).stdout
        bundle = None
        for entry in json.loads(out).get("LSHandlers", []):
            if entry.get("LSHandlerPreferredFamily") == "https":
                bundle = entry.get("LSHandlerRoleAll")
                break
        if not bundle or bundle not in bundle_to_app:
            # Default browser is not a known Chromium app - open plainly
            # (the in-app one-time gesture unlock covers audio there).
            subprocess.Popen(["open", url])
            print("[Launcher] Default browser is not Chromium; opening plainly.")
            return True
        app = bundle_to_app[bundle]
        subprocess.Popen(["open", "-a", app, "--args",
                          "--autoplay-policy=no-user-gesture-required", url])
        print(f"[Launcher] Opening WebUI with the default browser ({app}).")
        return True
    except Exception as exc:
        print(f"[Launcher] Could not resolve the default browser ({exc}); "
              "falling back to webbrowser.open().")
        return False


def _open_browser_linux(url: str) -> bool:
    """Linux: xdg-settings reports the default browser's .desktop file;
    parse its Exec= line and add the Chromium autoplay flag for
    Chromium-family browsers."""
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
            parts.append("--autoplay-policy=no-user-gesture-required")
        parts.append(url)
        subprocess.Popen(parts, start_new_session=True)
        print(f"[Launcher] Opening WebUI with the default browser ({Path(parts[0]).name}).")
        return True
    except Exception as exc:
        print(f"[Launcher] Could not resolve the default browser ({exc}); "
              "falling back to webbrowser.open().")
        return False


def _open_browser(url: str) -> None:
    """Open the WebUI in the user's DEFAULT browser, adding Chromium's
    autoplay flag when possible so her startup greeting needs no user
    gesture. Never guesses install paths: each OS resolves the default
    browser through its own records. Any failure falls back to plain
    webbrowser.open() - the app can never fail to open."""
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


def run(no_browser: bool = False, simulate_vram_gb: int | None = None) -> None:
    conda = find_conda()
    npm = find_npm()
    builtin = load_builtin_config()
    if builtin and simulate_vram_gb is not None:
        print(f"[Launcher] TEST: pretending the graphics card has {simulate_vram_gb} GB.")
        builtin = apply_simulated_vram(builtin, simulate_vram_gb)
        if builtin is None:
            print("[Launcher] TEST: such a card gets no local model - starting without it.")

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

    # The built-in model starts FIRST: loading 4-17 GB takes the longest, so
    # it overlaps the other services' start-up. A missing model never stops
    # the app - the backend reports it (and the WebUI shows it).
    llm_proc = None
    if builtin:
        ready, why = builtin_files_ready(builtin)
        if ready:
            print(f"\n[Launcher] Starting her built-in model ({builtin.get('model_label', builtin_model_name(builtin))}, "
                  f"{builtin.get('context')} context, {builtin.get('kv_cache')} memory, "
                  f"voice on the {'processor' if builtin.get('voice_device') == 'cpu' else 'graphics card'})...")
            llm_proc = start_process("llm", llama_server_command(builtin), PROJECT_ROOT)
        else:
            print(f"\n[Launcher] WARNING: built-in model not started - {why}. "
                  "Run the installer again to finish setting it up.")

    print("\n[Launcher] Starting GPT-SoVITS...")
    gpt = start_process("gptsovits", gptsovits_command(conda), PROJECT_ROOT,
                        voice_env(builtin))
    wait_for_port("GPT-SoVITS", GPT_PORT, gpt, timeout=180)

    # CharacterMemory sidecar: the slowest starter (CPU embedder + lore
    # index, ~30-60s with the shipped seed; a one-time cold lore build can
    # take ~10 min, hence the 600s wait), so it starts here and is waited
    # on LAST - its startup overlaps the other children. Soft-fail on
    # purpose: the app is fully functional without it (the bridge degrades
    # to the no-memory baseline). On a RAM-constrained machine its first
    # start can die mid-load (2026-10-07: the 18 GB test box, sharing RAM
    # with a local 27 B model, failed a 55 MB allocation), so the launcher
    # retries twice more before giving up.
    cm = None
    cm_available = False
    cm_env = sidecar_builtin_env(builtin) if builtin else None
    if ensure_cm_sidecar():
        print("[Launcher] Starting CharacterMemory sidecar...")
        cm = start_process(
            "cm-sidecar",
            [str(_cm_python()), str(CM_DIR / "cm_sidecar.py")],
            CM_DIR,
            cm_env,
        )
        cm_available = True
    else:
        print("[Launcher] CharacterMemory sidecar not available - "
              "continuing without long-term memory.")

    print("[Launcher] Starting Amadeus backend...")
    backend = start_process(
        "backend",
        [sys.executable, "main.py"],
        BACKEND_DIR,
    )
    wait_for_port("backend", BACKEND_PORT, backend, timeout=45)

    print("[Launcher] Starting WebUI...")
    frontend = start_process(
        "frontend",
        [npm, "run", "dev", "--", "--host", "127.0.0.1"],
        FRONTEND_DIR,
    )
    wait_for_port("WebUI", FRONTEND_PORT, frontend, timeout=45)

    if cm_available:
        for attempt in range(1, 4):
            try:
                wait_for_port("cm-sidecar", CM_PORT, cm, timeout=600)
                break
            except (TimeoutError, RuntimeError) as e:
                terminate_process_tree(cm)
                if attempt < 3:
                    print("[Launcher] The memory sidecar did not come up on "
                          f"attempt {attempt} ({e}) - trying again in 30 s...")
                    time.sleep(30)
                    cm = start_process(
                        "cm-sidecar",
                        [str(_cm_python()), str(CM_DIR / "cm_sidecar.py")],
                        CM_DIR,
                        cm_env,
                    )
                else:
                    print("[Launcher] WARNING: cm-sidecar not ready after 3 "
                          "attempts - the app runs without long-term memory. "
                          "Check .runtime/logs/cm-sidecar.log for details.")
                    cm = None

    # llama-server opens its port right away and answers "loading" until the
    # model is in memory; the WebUI shows that state. Here we only make sure
    # it did not die on start (e.g. not enough graphics memory).
    if llm_proc is not None:
        try:
            wait_for_port("built-in model", LLM_PORT, llm_proc, timeout=180)
        except (TimeoutError, RuntimeError) as e:
            print(f"[Launcher] WARNING: her built-in model did not start ({e}). "
                  "Check .runtime/logs/llm.log for details.")

    url = f"http://127.0.0.1:{FRONTEND_PORT}/"

    print("\n========================================")
    print(" Amadeus is online")
    print(f" {url}")
    print(" Press Ctrl+C to shut everything down.")
    print(" Relaunching Amadeus will clean stale processes automatically.")
    print("========================================\n")

    if not no_browser:
        _open_browser(url)

    cm_warned = False
    llm_warned = False
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
                    "Check .runtime/logs for details."
                )
        # The memory sidecar is OPTIONAL: if it dies the app keeps working
        # (the bridge degrades to the no-memory baseline), so a dead sidecar
        # warns once instead of tearing the whole stack down.
        if cm is not None and not cm_warned and cm.poll() is not None:
            cm_warned = True
            print("[Launcher] WARNING: cm-sidecar stopped (exit "
                  f"{cm.returncode}) - continuing without long-term memory. "
                  "Check .runtime/logs/cm-sidecar.log for details.")
        # The built-in model stopping does not tear the app down either:
        # she reports it as unreachable, and a restart brings it back.
        if llm_proc is not None and not llm_warned and llm_proc.poll() is not None:
            llm_warned = True
            print("[Launcher] WARNING: her built-in model stopped (exit "
                  f"{llm_proc.returncode}). Restart Amadeus to bring it back; "
                  "details in .runtime/logs/llm.log.")

        time.sleep(1)


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
    args = parser.parse_args()

    try:
        run(no_browser=args.no_browser, simulate_vram_gb=args.simulate_vram_gb)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"\n[Launcher] ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

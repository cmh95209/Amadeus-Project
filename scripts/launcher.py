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

AMADEUS_PORTS = {
    "GPT-SoVITS": GPT_PORT,
    "backend": BACKEND_PORT,
    "frontend": FRONTEND_PORT,
}

processes: list[subprocess.Popen] = []
log_handles: list[IO[str]] = []


def find_conda() -> str | None:
    candidates = [
        shutil.which("conda"),
        os.environ.get("CONDA_EXE"),
        str(Path.home() / "anaconda3" / "bin" / "conda"),
        str(Path.home() / "miniconda3" / "bin" / "conda"),
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


def clear_stale_amadeus_processes() -> None:
    print("[Launcher] Cleaning stale Amadeus processes...")

    for name, port in AMADEUS_PORTS.items():
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


def start_process(name: str, command: list[str], cwd: Path) -> subprocess.Popen:
    log = open_log(name)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"  # print() reaches the log live, not on exit
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


def run(no_browser: bool = False) -> None:
    conda = find_conda()
    npm = find_npm()

    if not conda:
        raise RuntimeError(
            "Conda was not found. Install Anaconda/Miniconda or make conda available on PATH."
        )

    if not npm:
        raise RuntimeError(
            "npm was not found. Install Node.js before launching the WebUI."
        )

    preflight(conda, npm)
    clear_stale_amadeus_processes()
    ensure_frontend_dependencies(npm)

    print("\n[Launcher] Starting GPT-SoVITS...")
    gpt = start_process(
        "gptsovits",
        [
            conda,
            "run",
            "-n",
            "GPTSoVITS",
            "--no-capture-output",
            "python",
            str(BACKEND_DIR / "start_gptsovits.py"),
        ],
        PROJECT_ROOT,
    )
    wait_for_port("GPT-SoVITS", GPT_PORT, gpt, timeout=180)

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

    url = f"http://127.0.0.1:{FRONTEND_PORT}/"

    print("\n========================================")
    print(" Amadeus is online")
    print(f" {url}")
    print(" Press Ctrl+C to shut everything down.")
    print(" Relaunching Amadeus will clean stale processes automatically.")
    print("========================================\n")

    if not no_browser:
        webbrowser.open(url)

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

        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the complete Amadeus stack.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the WebUI in a browser.",
    )
    args = parser.parse_args()

    try:
        run(no_browser=args.no_browser)
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

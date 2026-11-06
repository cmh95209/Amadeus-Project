"""Speech in videos -> written text with times (video plan Phase 1, 2026-11-03).

Her built-in model cannot hear, so the words spoken in a video are written
down first (Phase 0: without them she misread a talk video). faster-whisper
already comes with her voice engine's Python, so the backend runs a small
helper there (transcribe_helper.py) instead of adding packages to its own.
The speech model ("small", ~0.5 GB, MH P2 a) is downloaded by the installers
(MH 2026-11-03: no question); when it is missing the app downloads it itself
(at start and when a video needs it). It lives in ~/.amadeus-tools/speech,
next to her portable FFmpeg, so a reinstall of the app keeps it.

On the graphics card when it has room (a 3-minute clip: ~13 s), else on the
processor (~30 s) - never squeezed next to her model on a full card.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

import timing

MODEL = {
    "name": "small",
    "repo": "Systran/faster-whisper-small",
    "revision": "536b0662742c02347bc0e980a01041f333bce120",
    "files": [
        ("config.json", 2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
        ("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        ("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        ("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    ],
}
MODEL_BYTES = sum(size for _name, size, _sha in MODEL["files"])
CARD_FREE_GB = 2.0          # the card only with this much free (model ~1 GB + working room)
HELPER = Path(__file__).resolve().parent / "transcribe_helper.py"
_READY_MARK = ".verified"

_STATE = {"state": "", "done": 0, "message": ""}
_LOCK = threading.Lock()
_THREAD = [None]
_WHEN_READY = []            # callbacks once a download finishes (videos waiting for it)


def model_dir() -> Path:
    override = os.environ.get("AMADEUS_SPEECH_MODEL_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".amadeus-tools" / "speech" / f"faster-whisper-{MODEL['name']}"


def model_ready() -> bool:
    folder = model_dir()
    if not (folder / _READY_MARK).is_file():
        return False
    for name, size, _sha in MODEL["files"]:
        try:
            if (folder / name).stat().st_size != size:
                return False
        except OSError:
            return False
    return True


def voice_python():
    """Her voice engine's Python (it has faster-whisper), or None. The
    backend runs in the "amadeus<suffix>" conda environment; the voice engine
    in "GPTSoVits<suffix>" next to it (an -EnvSuffix test install: both
    "-test")."""
    override = os.environ.get("AMADEUS_SPEECH_PYTHON", "").strip()
    if override:
        return override if os.path.isfile(override) else None
    own_env = Path(sys.executable).resolve().parent
    if own_env.name in ("bin", "Scripts"):
        own_env = own_env.parent
    name = own_env.name
    if not name.lower().startswith("amadeus") or own_env.parent.name.lower() != "envs":
        return None
    suffix = name[len("amadeus"):]
    if suffix.startswith("-cm"):     # never the memory sidecar's environment
        return None
    for env in ("GPTSoVits" + suffix, "GPTSoVITS" + suffix):
        for rel in ("python.exe", "bin/python"):
            candidate = own_env.parent / env / rel
            if candidate.is_file():
                return str(candidate)
    return None


def status() -> dict:
    """{"state": ready / missing / downloading / failed / unavailable,
    "done", "total" (bytes), "message"} for the screen."""
    with _LOCK:
        state = dict(_STATE)
    if state["state"] != "downloading":
        if model_ready():
            state = {"state": "ready", "done": MODEL_BYTES, "message": ""}
        elif voice_python() is None:
            state = {"state": "unavailable", "done": 0,
                     "message": "Her voice engine is missing, so speech can't be written down."}
        elif state["state"] != "failed":
            state = {"state": "missing", "done": 0, "message": ""}
    state["total"] = MODEL_BYTES
    state["gb"] = round(MODEL_BYTES / 1024 ** 3, 1)
    return state


def _set(**fields) -> None:
    with _LOCK:
        _STATE.update(fields)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _download(session=None) -> None:
    """Every file of the speech model, pinned to one revision and checked
    against its checksum; a broken-off download goes on where it stopped."""
    session = session or requests
    folder = model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / _READY_MARK).unlink(missing_ok=True)
    done = 0
    for name, size, sha in MODEL["files"]:
        final = folder / name
        if final.is_file() and final.stat().st_size == size and _sha256(final) == sha:
            done += size
            _set(done=done)
            continue
        part = folder / (name + ".part")
        have = part.stat().st_size if part.is_file() else 0
        if have > size:
            part.unlink()
            have = 0
        if have < size:
            url = f"https://huggingface.co/{MODEL['repo']}/resolve/{MODEL['revision']}/{name}"
            headers = {"Range": f"bytes={have}-"} if have else {}
            with session.get(url, headers=headers, stream=True, timeout=60) as resp:
                if have and resp.status_code != 206:
                    have = 0          # the server sent the whole file again
                elif resp.status_code not in (200, 206):
                    raise RuntimeError(f"download refused ({resp.status_code})")
                with open(part, "ab" if have else "wb") as f:
                    for block in resp.iter_content(1024 * 1024):
                        f.write(block)
                        have += len(block)
                        _set(done=done + have)
        if part.stat().st_size != size or _sha256(part) != sha:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{name} arrived damaged")
        os.replace(part, final)
        done += size
    (folder / _READY_MARK).write_text(MODEL["revision"], encoding="utf-8")


def _download_job() -> None:
    try:
        _download()
        _set(state="", done=MODEL_BYTES, message="")
        print("[Amadeus] Speech model ready:", model_dir())
        with _LOCK:
            waiting = list(_WHEN_READY)
            _WHEN_READY.clear()
        for callback in waiting:
            try:
                callback()
            except Exception as exc:
                print("[Amadeus] After the speech download:", repr(exc))
    except Exception as exc:
        print("[Amadeus] Speech model download failed:", repr(exc))
        _set(state="failed", message="The download stopped - try again (it goes on where it stopped).")


def start_download() -> dict:
    """Start (or keep going with) the one-time download. Returns status()."""
    with _LOCK:
        busy = _THREAD[0] is not None and _THREAD[0].is_alive()
    if busy or model_ready():
        return status()
    if voice_python() is None:
        return status()
    _set(state="downloading", done=0, message="")
    thread = threading.Thread(target=_download_job, daemon=True, name="speech-model-download")
    with _LOCK:
        _THREAD[0] = thread
    thread.start()
    return status()


def when_ready(callback) -> None:
    """Run callback once the speech model has arrived (videos prepared
    before it did get their speech then)."""
    with _LOCK:
        _WHEN_READY.append(callback)


def pick_device() -> str:
    """"cuda" when the graphics card has room next to everything on it now
    (her model included), else "cpu"."""
    if os.environ.get("AMADEUS_SPEECH_DEVICE", "").strip().lower() == "cpu":
        return "cpu"
    mem = timing.gpu_memory()
    if mem is None:
        return "cpu"
    used, total = mem
    return "cuda" if total - used >= CARD_FREE_GB else "cpu"


def transcribe(audio_path: str, seconds: float = 60.0) -> dict:
    """{"language", "segments": [{"start", "end", "text"}], "device", "took"}
    for one audio file (16 kHz mono WAV). Raises RuntimeError with a plain
    reason when it can't be done."""
    python = voice_python()
    if python is None:
        raise RuntimeError("her voice engine's Python was not found")
    if not model_ready():
        raise RuntimeError("the speech model is not downloaded")
    device = pick_device()
    started = time.monotonic()
    try:
        out = _run_helper(python, device, audio_path, seconds)
    except RuntimeError:
        if device == "cpu":
            raise
        device = "cpu"             # the card was busy after all: the processor
        out = _run_helper(python, device, audio_path, seconds)
    out["device"] = device
    out["took"] = round(time.monotonic() - started, 1)
    return out


def _run_helper(python: str, device: str, audio_path: str, seconds: float) -> dict:
    # The processor writes down ~6x faster than real time (Phase 0); a slow
    # PC gets generous room before the attempt counts as stuck.
    timeout = 180 + 4 * float(seconds or 0)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.run([python, str(HELPER), str(model_dir()), device, str(audio_path)],
                              capture_output=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"the speech helper did not finish ({exc.__class__.__name__})")
    lines = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    if proc.returncode != 0 or not lines:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise RuntimeError("the speech helper failed: " + " | ".join(tail))
    try:
        data = json.loads(lines[-1])
    except ValueError:
        raise RuntimeError("the speech helper gave no result")
    segments = [{"start": round(float(s["start"]), 1), "end": round(float(s["end"]), 1),
                 "text": str(s["text"]).strip()}
                for s in data.get("segments") or [] if str(s.get("text") or "").strip()]
    return {"language": str(data.get("language") or ""), "segments": segments}

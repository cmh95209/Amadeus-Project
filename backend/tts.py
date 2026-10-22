import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path

import requests

from speech_text import to_spoken

# Resolve paths from this file so the backend works regardless of cwd.
BACKEND_DIR = Path(__file__).resolve().parent
REF_WAV = BACKEND_DIR / "assets" / "reference_audio" / "kurisu10s.wav"
OUT_WAV = BACKEND_DIR / "generated" / "generated.wav"
REF_TXT = "ん? ほっと来てくれませんか?ん? ふざけてないでちょっと来てくださいアニカって何ですか?人激の悪い私は"

GPTSOVITS_API_URL = os.getenv("GPTSOVITS_API_URL", "http://127.0.0.1:9880")
# 2 = the engine streams inside a sentence: first sound after ~1 s instead
# of 2-5 s on long first sentences (2026-10-21/22: MH's ear test - sounds
# almost identical to 1; mode 3 is more tinny). Env overrides.
STREAMING_MODE = int(os.getenv("GPTSOVITS_STREAMING_MODE", "2"))
_speech_lock = threading.Lock()


def _saved_name():
    """(name, reading) from Settings; read per line so a change applies at
    once. Imported lazily: memory pulls in the whole data layer."""
    import memory
    return memory.load_username(), memory.load_username_reading()


def _spoken(text: str) -> str:
    """The user's name and numbers -> the forms the engine reads in Japanese
    (speech_text). A rewriting bug must never silence her: fall back to the
    text as-is."""
    try:
        name, reading = _saved_name()
        return to_spoken(text, name, reading)
    except Exception as exc:
        print(f"[AmadeusSpeak] to_spoken failed, speaking raw text: {exc!r}")
        return text


def _request_payload(text: str) -> dict:
    if not REF_WAV.exists():
        raise FileNotFoundError(f"Reference audio not found: {REF_WAV}")
    return {
        "text": _spoken(text),
        "text_lang": "ja",
        "ref_audio_path": str(REF_WAV),
        "prompt_text": REF_TXT,
        "prompt_lang": "ja",
        "text_split_method": "cut5",
        "batch_size": 1,
        "batch_threshold": 0.75,
        "split_bucket": True,
        "speed_factor": 1.0,
        "fragment_interval": 0.15,
        "seed": -1,
        # Steadier delivery (picked by ear 2026-10-16, setting "B"): the
        # engine defaults (temperature 1, top_k 15) let short lines swing
        # muted or high-pitched between takes. Still random per line.
        "temperature": 0.7,
        "top_k": 10,
        "media_type": "wav",
        "parallel_infer": True,
        "repetition_penalty": 1.35,
        "sample_steps": 16,
        "super_sampling": False,
        "streaming_mode": STREAMING_MODE,
        "overlap_length": 2,
        "min_chunk_length": 16,
    }


def _start_stream_player():
    """Start a player that accepts a WAV header followed by PCM bytes."""
    ffplay = shutil.which("ffplay")
    if ffplay:
        return subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "error", "-i", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    mpv = shutil.which("mpv")
    if mpv:
        return subprocess.Popen(
            [mpv, "--no-video", "--really-quiet", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    print("[AmadeusSpeak] No ffplay or mpv found; saving audio and playing after generation.")
    return None


def _finish_stream_player(player):
    if player is None:
        return
    try:
        if player.stdin:
            player.stdin.close()
        player.wait(timeout=30)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
        try:
            player.kill()
        except OSError:
            pass


def _stream_audio(text: str, play: bool):
    OUT_WAV.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OUT_WAV.with_suffix(".streaming.tmp")
    player = _start_stream_player() if play else None

    try:
        with requests.post(
            f"{GPTSOVITS_API_URL}/tts",
            json=_request_payload(text),
            stream=True,
            timeout=None,
        ) as response:
            response.raise_for_status()
            with temp_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=4096):
                    if not chunk:
                        continue
                    output.write(chunk)
                    if player is not None and player.stdin is not None:
                        try:
                            player.stdin.write(chunk)
                            player.stdin.flush()
                        except (BrokenPipeError, OSError):
                            _finish_stream_player(player)
                            player = None
        temp_path.replace(OUT_WAV)
    finally:
        _finish_stream_player(player)
        if temp_path.exists():
            temp_path.unlink()


def streamVoiceChunks(text: str, save_path=None):
    """Yield GPT-SoVITS WAV chunks for browser playback while saving a copy.

    When save_path is given, the finished line is also kept there so the
    message it belongs to can be replayed later.
    """
    with _speech_lock:
        OUT_WAV.parent.mkdir(parents=True, exist_ok=True)
        temp_path = OUT_WAV.with_suffix(".browser.tmp")

        try:
            with requests.post(
                f"{GPTSOVITS_API_URL}/tts",
                json=_request_payload(text),
                stream=True,
                timeout=(10, 120),
            ) as response:
                response.raise_for_status()
                with temp_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=4096):
                        if not chunk:
                            continue
                        output.write(chunk)
                        yield chunk
            temp_path.replace(OUT_WAV)
            if save_path is not None:
                try:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    save_path.write_bytes(OUT_WAV.read_bytes())
                except Exception:
                    pass
        finally:
            if temp_path.exists():
                temp_path.unlink()


def streamVoice(text: str):
    """Stream native GPT-SoVITS audio directly to one continuous player."""
    with _speech_lock:
        _stream_audio(text, play=True)


def generateVoice(text: str):
    """Generate a complete WAV without starting playback."""
    with _speech_lock:
        _stream_audio(text, play=False)


WARM_UP_LINE = "うん、準備できたわ。"


def warm_up(wait_s: float = 600.0) -> None:
    """Speak one short line into a throw-away file once the voice engine is
    up, so her first real reply is not the slowest (cold first line on the
    processor: first sound 4.7-6.3 s, up to 1.26x real time - 2026-10-21).
    Background only; any failure is just logged."""
    import time
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            requests.get(f"{GPTSOVITS_API_URL}/docs", timeout=2)
            break
        except requests.RequestException:
            time.sleep(2)
    else:
        return
    started = time.monotonic()
    target = OUT_WAV.with_name("warm_up.wav")
    ok = renderVoiceToPath(WARM_UP_LINE, target)
    try:
        target.unlink()
    except OSError:
        pass
    if ok:
        print(f"[Timing] voice warm-up took {time.monotonic() - started:.1f} s", flush=True)


def renderVoiceToPath(text: str, path) -> bool:
    """Synthesize a line straight into a specific WAV file (no playback).

    Used to re-voice an old reply on demand when its file was deleted by the
    retention cap. Returns True when the file was written.
    """
    from pathlib import Path
    path = Path(path)
    with _speech_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".tmp")
        try:
            with requests.post(
                f"{GPTSOVITS_API_URL}/tts",
                json=_request_payload(text),
                stream=True,
                timeout=(10, 120),
            ) as response:
                response.raise_for_status()
                with temp_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=4096):
                        if chunk:
                            output.write(chunk)
            temp_path.replace(path)
            return True
        except Exception as exc:
            print(f"[AmadeusSpeak] renderVoiceToPath failed: {exc!r}")
            return False
        finally:
            if temp_path.exists():
                temp_path.unlink()


def _play_wav_path(wav_path: Path):
    path = str(wav_path.resolve())
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["afplay", path], check=False)
        elif system == "Windows":
            subprocess.run([
                "powershell", "-NoProfile", "-Command",
                f'(New-Object Media.SoundPlayer "{path}").PlaySync();',
            ], check=False)
        else:
            for command in (
                ["paplay", path],
                ["aplay", path],
                ["ffplay", "-nodisp", "-autoexit", path],
            ):
                if shutil.which(command[0]):
                    subprocess.run(command, check=False)
                    break
    except Exception as error:
        print(f"[AmadeusSpeak] play_sound failed: {error}")


def play_sound():
    _play_wav_path(OUT_WAV)

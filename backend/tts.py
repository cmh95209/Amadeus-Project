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
STREAMING_MODE = int(os.getenv("GPTSOVITS_STREAMING_MODE", "1"))
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


def _wav_data_offset(head: bytes):
    """Where the 'data' chunk's header starts in a RIFF/WAVE header, or None."""
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(head):
        if head[pos:pos + 4] == b"data":
            return pos
        pos += 8 + int.from_bytes(head[pos + 4:pos + 8], "little")
    return None


def _browser_stream(chunks):
    """The engine starts a streamed line with a WAV header that says "0 bytes
    of sound" (it does not know the length yet). Chrome/Edge ignore that;
    Firefox believes it and refuses the line (silence, 2026-11-02 tester
    report). Rewrite those sizes to 0xFFFFFFFF - the usual "length unknown,
    still streaming" value, checked in real Firefox - and pass the rest on
    untouched."""
    head = b""
    for chunk in chunks:
        if head is None:
            yield chunk
            continue
        head += chunk
        offset = _wav_data_offset(head)
        maybe_wav = (len(head) < 12 and b"RIFF".startswith(head[:4])) or head[8:12] == b"WAVE"
        if (offset is None or offset + 8 > len(head)) and maybe_wav and len(head) < 256:
            continue   # header not complete yet
        fixed = bytearray(head)
        if offset is not None and offset + 8 <= len(fixed):
            fixed[4:8] = b"\xff\xff\xff\xff"
            fixed[offset + 4:offset + 8] = b"\xff\xff\xff\xff"
        head = None
        yield bytes(fixed)
    if head:
        yield head


def fix_wav_sizes(path) -> bool:
    """Write the real sizes into a finished WAV file's header (a saved line
    keeps the engine's "0 bytes" header otherwise, which Firefox will not
    replay). Safe to call on any file: anything that is not a WAV with a
    'data' chunk is left alone. Returns True when the header was changed."""
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("r+b") as f:
            head = f.read(256)
            offset = _wav_data_offset(head)
            if offset is None:
                return False
            riff = (size - 8).to_bytes(4, "little")
            data = max(0, size - offset - 8).to_bytes(4, "little")
            if head[4:8] == riff and head[offset + 4:offset + 8] == data:
                return False
            f.seek(4)
            f.write(riff)
            f.seek(offset + 4)
            f.write(data)
            return True
    except (OSError, OverflowError):
        return False


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
        fix_wav_sizes(temp_path)
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
                    for chunk in _browser_stream(c for c in response.iter_content(chunk_size=4096) if c):
                        output.write(chunk)
                        yield chunk
            fix_wav_sizes(temp_path)
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
            fix_wav_sizes(temp_path)
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

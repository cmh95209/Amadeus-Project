"""Videos the user shows Amadeus - Phase 1: preparing (video plan, 2026-11-03).

The flow: the screen uploads a video in pieces (resumable - phone videos are
big and mobile data drops; 500 MB at most, V6a): POST /attachments/video
starts, PUT .../<upload id>?offset=N adds a piece, POST .../done finishes.
On "done" the video is checked (FFprobe: length <= 10 minutes, V4a), gets a
card picture (its first frame that isn't black) and becomes a card not sent
yet, like a picture. One background worker then prepares it, one video at a
time (FFmpeg uses every processor core):

1. the watch copy: H.264 MP4, 720p, sound as AAC, "faststart" - it plays in
   every browser (phone .mov / HEVC too) and is what she is shown;
2. the frames: up to 16 time-stamped stills, 768 px (Phase 0: 16 at that
   size answered best on the 8 GB tier; packed 2x2 pictures invented quotes);
3. the cloud copy, only while her brain is a cloud model: 480p, 1 frame per
   second, mono sound (Phase 0: Gemini counts the same pieces per second at
   any size; the full phone clip was refused as too large);
4. the speech, written down with times (transcribe.py; when the speech
   model is not downloaded yet it is written once it arrives).

Then the uploaded original is deleted (V5a). FFmpeg uses the graphics
card's video chip when it has one that works (MH P3: card first), else the
processor; any failure on the card falls back to the processor.

Files (next to the other attachments, all "<id>_..."): _watch.mp4 (path),
_thumb.jpg (thumb), _card.jpg, _f01.jpg..., _cloud.mp4, _video.json (state,
frames, speech), _original<ext> (until prepared).
"""
import base64
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import attachments
import memory as store
import transcribe

try:
    from PIL import Image, ImageStat
except ImportError:  # pragma: no cover - the card picture is then the first frame
    Image = None

VIDEO_EXT = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".3gp", ".3g2")
VIDEO_FORMAT_NAMES = "MP4, MOV, WebM, M4V, MKV, AVI or 3GP"
MAX_VIDEO_BYTES = 500 * 1024 * 1024    # V6a
MAX_SECONDS = 10 * 60                  # V4a
PIECE_MAX = 8 * 1024 * 1024            # one upload piece
FRAME_COUNT = 16
FRAME_SIDE = 768                       # longest side of a frame she sees
FRAME_TOKENS = 150                     # Phase 0: ~128-152 pieces per 768 px frame
SPEECH_TOKENS_PER_MIN = 300            # Phase 0: ~300 pieces per minute of speech
CARD_SIDE = 768
WATCH_SHORT_SIDE = 720
CLOUD_SHORT_SIDE = 480
CLOUD_MAX_BYTES = 18 * 1024 * 1024     # Phase 0: 22 MB went through, 25 MB was refused
UPLOADS = "_uploads"

READY, PREPARING, FAILED = "ready", "preparing", "failed"


class Gone(Exception):
    """The video was deleted while it was being prepared."""


# ---------- Tools ----------

def _tool(name: str):
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if os.name == "nt" else "")
    own = Path.home() / ".amadeus-tools" / "ffmpeg" / "bin" / exe
    return str(own) if own.is_file() else None


def tools_ready() -> bool:
    return bool(_tool("ffmpeg") and _tool("ffprobe"))


def _flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run(cmd, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_flags())


def ffmpeg(args, duration: float = 0.0, on_pct=None, timeout: float = 0.0) -> None:
    """Run FFmpeg; on_pct(0-100) follows its progress through the video.
    Raises RuntimeError with FFmpeg's last words when it fails."""
    exe = _tool("ffmpeg")
    if not exe:
        raise attachments.AttachmentError("no_ffmpeg")
    cmd = [exe, "-hide_banner", "-nostdin", "-v", "error", "-y"]
    if on_pct and duration > 0:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)
    limit = timeout or max(300.0, 20.0 * duration)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, creationflags=_flags())
        started = time.monotonic()
        try:
            for raw in proc.stdout:
                line = raw.decode("ascii", "replace").strip()
                if on_pct and line.startswith("out_time_us="):
                    try:
                        done = int(line.split("=", 1)[1]) / 1e6
                    except ValueError:
                        continue
                    on_pct(max(0, min(100, int(100 * done / duration))))
                if time.monotonic() - started > limit:
                    proc.kill()
                    break
            proc.wait(timeout=max(5.0, limit - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-2:]
            raise RuntimeError("FFmpeg failed: " + " | ".join(tail))


# ---------- The graphics card's video chip (MH P3: card first) ----------

_HW_ENCODERS = ("h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox")
_ENCODER = [None]
_ENCODER_LOCK = threading.Lock()


def encoder() -> str:
    """The H.264 encoder to use: the first video chip that encodes a short
    test, else the processor (libx264). Tested once per run."""
    if os.environ.get("AMADEUS_VIDEO_ENCODER", "").strip().lower() in ("cpu", "libx264"):
        return "libx264"
    with _ENCODER_LOCK:
        if _ENCODER[0]:
            return _ENCODER[0]
        exe = _tool("ffmpeg")
        listed = ""
        if exe:
            try:
                listed = _run([exe, "-hide_banner", "-encoders"], 20).stdout.decode("ascii", "replace")
            except (OSError, subprocess.TimeoutExpired):
                listed = ""
        chosen = "libx264"
        for name in _HW_ENCODERS:
            if name not in listed:
                continue
            try:
                ok = _run([exe, "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                           "color=c=black:s=320x240:d=0.5", "-vf", "format=yuv420p",
                           "-c:v", name, "-f", "null", "-"], 30).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if ok:
                chosen = name
                break
        _ENCODER[0] = chosen
        return chosen


def _processor_only() -> None:
    """The card failed on a real video: the processor for the rest of this run."""
    with _ENCODER_LOCK:
        _ENCODER[0] = "libx264"


def _video_args(enc: str, avg_kbps: int, max_kbps: int, crf: int) -> list:
    if enc == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                "-maxrate", f"{max_kbps}k", "-bufsize", f"{2 * max_kbps}k"]
    return ["-c:v", enc, "-b:v", f"{avg_kbps}k", "-maxrate", f"{max_kbps}k",
            "-bufsize", f"{2 * max_kbps}k"]


def _scale_short(side: int) -> str:
    """Shorter side at most `side`, even sizes, 8-bit 4:2:0 colour. The
    colour step matters: the card's encoder refused a ProRes clip's 10-bit
    4:2:2 colour (Phase 0) - every encoder gets plain colour."""
    return (f"scale='if(gt(iw,ih),-2,trunc(min({side},iw)/2)*2)'"
            f":'if(gt(iw,ih),trunc(min({side},ih)/2)*2,-2)',format=yuv420p")


def _scale_long(side: int) -> str:
    return (f"scale='if(gt(iw,ih),min({side},iw),-2)':'if(gt(iw,ih),-2,min({side},ih))'")


def encode(src: str, dest: str, short_side: int, duration: float, has_audio: bool,
           avg_kbps: int, max_kbps: int, crf: int, fps=None, mono: bool = False,
           on_pct=None) -> str:
    """One H.264 MP4 copy; returns the encoder that made it. A failure on
    the card is tried once more on the processor."""
    def build(enc):
        args = ["-i", src, "-map", "0:v:0", "-vf", _scale_short(short_side)]
        if fps:
            args += ["-r", str(fps)]
        args += _video_args(enc, avg_kbps, max_kbps, crf)
        if has_audio:
            args += ["-map", "0:a:0?", "-c:a", "aac",
                     "-ac", "1" if mono else "2", "-b:a", "48k" if mono else "128k"]
        else:
            args += ["-an"]
        return args + ["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", dest]

    enc = encoder()
    try:
        ffmpeg(build(enc), duration, on_pct)
        return enc
    except RuntimeError:
        if enc == "libx264":
            raise
        print(f"[Amadeus] Video: the graphics card's encoder ({enc}) failed - using the processor.")
        _processor_only()
        ffmpeg(build("libx264"), duration, on_pct)
        return "libx264"


# ---------- Checking a video ----------

def probe(path: str) -> dict:
    """{"duration", "width", "height" (as shown, phone rotation applied),
    "has_audio", "codec"}; raises AttachmentError when it is no video."""
    exe = _tool("ffprobe")
    if not exe:
        raise attachments.AttachmentError("no_ffmpeg")
    try:
        proc = _run([exe, "-v", "error", "-print_format", "json", "-show_format",
                     "-show_streams", str(path)], 60)
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise attachments.AttachmentError("video_broken")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if proc.returncode != 0 and not streams:
        raise attachments.AttachmentError("video_broken")
    if video is None:
        raise attachments.AttachmentError("not_video")
    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not duration > 0:
        raise attachments.AttachmentError("video_broken")
    if duration > MAX_SECONDS + 1:
        raise attachments.AttachmentError("video_too_long")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation:
        try:
            rotation = int((video.get("tags") or {}).get("rotate") or 0)
        except ValueError:
            rotation = 0
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return {"duration": round(duration, 2), "width": width, "height": height,
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
            "codec": str(video.get("codec_name") or "")}


def frame_times(duration: float) -> list:
    """When the frames are taken: the middle of FRAME_COUNT equal stretches
    (a clip under 16 s: one per second)."""
    n = FRAME_COUNT if duration >= FRAME_COUNT else max(1, math.ceil(duration))
    return [round((i + 0.5) * duration / n, 2) for i in range(n)]


def length_text(seconds) -> str:
    total = int(round(float(seconds or 0)))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def estimate_tokens(duration: float, has_audio: bool, speech=None) -> int:
    """Her prompt room for this video: its frames + the written speech."""
    frames = len(frame_times(duration)) * FRAME_TOKENS
    if speech is not None:
        return frames + store.estimate_tokens(speech)
    return frames + (int(SPEECH_TOKENS_PER_MIN * duration / 60) if has_audio else 0)


# ---------- Files and state ----------

def _full(rel: str) -> str:
    return os.path.join(store.attachments_dir(), rel)


def _rel_base(row) -> str:
    """"<folder>/<id>_" - every file of this video starts with it."""
    for key in ("thumb", "path", "original"):
        if row.get(key):
            return row[key].split("/", 1)[0] + f"/{row['id']}_"
    return f"{row['conversation_id']}/{row['id']}_"


def meta_path(row) -> str:
    return _full(_rel_base(row) + "video.json")


def load_meta(row) -> dict:
    try:
        with open(meta_path(row), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_meta(row, meta: dict) -> None:
    path = meta_path(row)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, path)


def _update_row(attachment_id, **fields) -> None:
    if not fields:
        return
    conn = store._connect_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE attachments SET " + ", ".join(f"{k} = ?" for k in fields)
                  + " WHERE id = ?", (*fields.values(), int(attachment_id)))
        conn.commit()
    finally:
        conn.close()


# ---------- Upload in pieces ----------

_UPLOAD_LOCK = threading.Lock()
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")


def is_video_name(name) -> bool:
    return attachments._clean_name(name).lower().endswith(VIDEO_EXT)


def _upload_files(upload_id: str) -> tuple:
    if not _UPLOAD_ID.match(str(upload_id or "")):
        raise attachments.AttachmentError("upload_gone")
    folder = os.path.join(store.attachments_dir(), UPLOADS)
    return os.path.join(folder, upload_id + ".json"), os.path.join(folder, upload_id + ".part")


def _upload_info(upload_id: str) -> tuple:
    info_path, part = _upload_files(upload_id)
    try:
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        raise attachments.AttachmentError("upload_gone")
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    return info, part, have


def start_upload(name, size, conversation_id=None) -> dict:
    """A new upload: {"upload_id", "received": 0, "piece": PIECE_MAX}."""
    name = attachments._clean_name(name)
    if not name.lower().endswith(VIDEO_EXT):
        raise attachments.AttachmentError("video_unsupported")
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise attachments.AttachmentError("empty")
    if size <= 0:
        raise attachments.AttachmentError("empty")
    if size > MAX_VIDEO_BYTES:
        raise attachments.AttachmentError("video_too_big")
    if not tools_ready():
        raise attachments.AttachmentError("no_ffmpeg")
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    upload_id = uuid.uuid4().hex
    info_path, part = _upload_files(upload_id)
    os.makedirs(os.path.dirname(info_path), exist_ok=True)
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "size": size, "conversation_id": conv,
                   "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f)
    open(part, "wb").close()
    return {"upload_id": upload_id, "received": 0, "size": size, "piece": PIECE_MAX}


def upload_status(upload_id) -> dict:
    info, _part, have = _upload_info(upload_id)
    return {"upload_id": upload_id, "received": have, "size": info["size"], "piece": PIECE_MAX}


def add_piece(upload_id, offset, data: bytes) -> dict:
    """Append one piece at `offset` (= what has arrived so far). A piece for
    another offset (a retry after a dropped connection) changes nothing; the
    answer says where to go on from. Returns upload_status + "ok"."""
    with _UPLOAD_LOCK:
        info, part, have = _upload_info(upload_id)
        ok = False
        if (isinstance(offset, int) and offset == have and data
                and len(data) <= PIECE_MAX and have + len(data) <= info["size"]):
            with open(part, "ab") as f:
                f.write(data)
            have += len(data)
            ok = True
    return {"upload_id": upload_id, "received": have, "size": info["size"],
            "piece": PIECE_MAX, "ok": ok}


def cancel_upload(upload_id) -> None:
    for path in _upload_files(upload_id):
        try:
            os.remove(path)
        except OSError:
            pass


def finish_upload(upload_id) -> dict:
    """The whole video has arrived: check it, make its card, start preparing
    it. Returns the row; raises AttachmentError (the upload is then gone)."""
    with _UPLOAD_LOCK:
        info, part, have = _upload_info(upload_id)
        if have != info["size"]:
            raise attachments.AttachmentError("upload_incomplete")
        info_path, _ = _upload_files(upload_id)
        os.remove(info_path)        # a second "done" finds nothing
    try:
        found = probe(part)
    except attachments.AttachmentError:
        cancel_upload(upload_id)
        raise
    conv = int(info["conversation_id"])
    name = info["name"]
    ext = os.path.splitext(name)[1].lower() or ".mp4"
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("INSERT INTO attachments (conversation_id, kind, name, mime, bytes, width, "
                  "height, tokens, created_at) VALUES (?, 'video', ?, 'video/mp4', ?, ?, ?, ?, ?)",
                  (conv, name, have, found["width"], found["height"],
                   estimate_tokens(found["duration"], found["has_audio"]),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        new_id = c.lastrowid
        conn.commit()
    finally:
        conn.close()
    base = f"{conv}/{new_id}_"
    os.makedirs(_full(str(conv)), exist_ok=True)
    os.replace(part, _full(base + "original" + ext))
    _update_row(new_id, original=base + "original" + ext)
    row = attachments.get(new_id)
    try:
        _card_picture(_full(row["original"]), found["duration"], base)
    except Exception:
        drop(row)
        raise attachments.AttachmentError("video_broken")
    _update_row(new_id, thumb=base + "thumb.jpg")
    row = attachments.get(new_id)
    _save_meta(row, {"state": PREPARING, "step": "watch", "pct": 0, "message": "",
                     **found, "frames": [], "watch": None, "cloud": None,
                     "speech": {"state": "writing" if found["has_audio"] else "none"}})
    start(row)
    return row


def drop(row) -> None:
    """Delete a video's row and every file it has (an upload that failed)."""
    conn = store._connect_db()
    try:
        c = conn.cursor()
        files = store._delete_attachments(c, "id = ?", (row["id"],))
        conn.commit()
    finally:
        conn.close()
    store.remove_attachment_files(files)


def _card_picture(src: str, duration: float, base: str) -> None:
    """The card's picture: the first of a few early frames that isn't black
    (videos often fade in), else the brightest; + its thumbnail."""
    card, thumb = _full(base + "card.jpg"), _full(base + "thumb.jpg")
    best = None
    for t in [0.0, 0.5, 1.0, 2.0, duration * 0.1]:
        if t >= duration and t > 0:
            continue
        try_path = _full(base + "cardtry.jpg")
        ffmpeg(["-ss", f"{t:.2f}", "-i", src, "-map", "0:v:0", "-frames:v", "1",
                "-vf", _scale_long(CARD_SIDE), "-q:v", "3", try_path], timeout=60)
        if not os.path.isfile(try_path):
            continue
        light = 255.0
        if Image is not None:
            with Image.open(try_path) as img:
                light = ImageStat.Stat(img.convert("L")).mean[0]
        if best is None or light > best:
            best = light
            os.replace(try_path, card)
        if light >= 24:
            break
    try:
        os.remove(_full(base + "cardtry.jpg"))
    except OSError:
        pass
    if best is None:
        raise RuntimeError("no frame could be read")
    if Image is not None:
        with Image.open(card) as img:
            img = img.convert("RGB")
            img.thumbnail((attachments.THUMB_SIDE, attachments.THUMB_SIDE), Image.LANCZOS)
            img.save(thumb, "JPEG", quality=82)
    else:
        shutil.copyfile(card, thumb)


# ---------- Preparing (one background worker) ----------

_QUEUE = []                 # [("prepare" | "speech", attachment id)]
_QUEUE_LOCK = threading.Lock()
_WAKE = threading.Event()
_THREAD = [None]


def start(row, job: str = "prepare") -> None:
    with _QUEUE_LOCK:
        if (job, row["id"]) not in _QUEUE:
            _QUEUE.append((job, row["id"]))
        if _THREAD[0] is None or not _THREAD[0].is_alive():
            _THREAD[0] = threading.Thread(target=_worker, daemon=True, name="video-prepare")
            _THREAD[0].start()
    _WAKE.set()


def _worker() -> None:
    while True:
        _WAKE.wait()
        with _QUEUE_LOCK:
            if not _QUEUE:
                _WAKE.clear()
                continue
            job, attachment_id = _QUEUE.pop(0)
        try:
            (speech_job if job == "speech" else prepare_job)(attachment_id)
        except Exception as exc:  # never let one video stop the worker
            print(f"[Amadeus] Video #{attachment_id}: {job} stopped:", repr(exc))


def _live(attachment_id):
    row = attachments.get(attachment_id)
    if row is None or row.get("kind") != "video":
        raise Gone()
    return row


def _wants_cloud_copy() -> bool:
    return attachments._cloud_brain()


STEPS = {"watch": (0, 55), "frames": (55, 70), "cloud": (70, 80), "speech": (80, 100)}


def prepare_job(attachment_id) -> None:
    """Make everything a video needs (steps already done are kept, so a
    restart goes on where the last run stopped)."""
    try:
        row = _live(attachment_id)
    except Gone:
        return
    meta = load_meta(row)
    if meta.get("state") != PREPARING:
        return
    base = _rel_base(row)
    duration, has_audio = float(meta["duration"]), bool(meta.get("has_audio"))
    last = [-1]

    def step(name, pct=0):
        lo, hi = STEPS[name]
        value = lo + (hi - lo) * pct // 100
        if name != meta.get("step") or value >= last[0] + 2:
            _live(attachment_id)
            meta.update(step=name, pct=value)
            last[0] = value
            _save_meta(row, meta)

    try:
        if not meta.get("watch"):
            step("watch")
            src = _full(row["original"]) if row.get("original") else None
            if not src or not os.path.isfile(src):
                raise RuntimeError("the uploaded video is missing")
            started = time.monotonic()
            meta["encoder"] = encode(src, _full(base + "watch.mp4"), WATCH_SHORT_SIDE, duration,
                                     has_audio, 1000, 2000, 26,
                                     on_pct=lambda p: step("watch", p))
            meta["watch_seconds"] = round(time.monotonic() - started, 1)
            meta["watch"] = base + "watch.mp4"
            _live(attachment_id)
            _update_row(attachment_id, path=meta["watch"])
            _save_meta(row, meta)
        watch = _full(meta["watch"])
        if not meta.get("frames"):
            step("frames")
            times = frame_times(duration)
            frames = []
            for i, t in enumerate(times, start=1):
                rel = f"{base}f{i:02d}.jpg"
                ffmpeg(["-ss", f"{t:.2f}", "-i", watch, "-map", "0:v:0", "-frames:v", "1",
                        "-vf", _scale_long(FRAME_SIDE), "-q:v", "4", _full(rel)], timeout=60)
                frames.append({"t": t, "file": rel})
                step("frames", 100 * i // len(times))
            meta["frames"] = frames
            _save_meta(row, meta)
        if meta.get("cloud") is None and _wants_cloud_copy():
            step("cloud")
            meta["cloud"] = make_cloud_copy(row, meta, on_pct=lambda p: step("cloud", p))
            _save_meta(row, meta)
        if (meta.get("speech") or {}).get("state") in ("writing", "waiting"):
            step("speech")
            # From the original while it is there: the watch copy's re-encoded
            # sound tipped a short Malay line into "Chinese" (Phase 1 try-out).
            row = _live(attachment_id)
            original = _full(row["original"]) if row.get("original") else ""
            meta["speech"] = _write_speech(row, original if os.path.isfile(original) else watch,
                                           duration)
            _update_row(attachment_id, tokens=_tokens(meta))
        _finish(row, meta, READY, "")
        start_notes(attachments.get(attachment_id))
    except Gone:
        return
    except attachments.AttachmentError as exc:
        _fail(attachment_id, str(exc))
    except Exception as exc:
        print(f"[Amadeus] Video #{attachment_id} could not be prepared:", repr(exc))
        _fail(attachment_id, "This video could not be prepared (FFmpeg could not read it).")


def _tokens(meta) -> int:
    speech = meta.get("speech") or {}
    text = speech_text(speech.get("segments") or []) if speech.get("state") == "done" else None
    return estimate_tokens(float(meta["duration"]), bool(meta.get("has_audio")), text)


def _finish(row, meta, state, message) -> None:
    try:
        row = _live(row["id"])
    except Gone:
        return
    meta.update(state=state, step="done", pct=100, message=message)
    _save_meta(row, meta)
    if row.get("original"):          # V5a: only the watch copy is kept
        store.remove_attachment_files([row["original"]])
        _update_row(row["id"], original=None)


def _fail(attachment_id, message: str) -> None:
    try:
        row = _live(attachment_id)
    except Gone:
        return
    meta = load_meta(row)
    print(f"[Amadeus] Video #{attachment_id}: {message}")
    _finish(row, meta, FAILED, message)


def make_cloud_copy(row, meta, on_pct=None) -> dict:
    """The copy a cloud model that watches videos gets: 480p, 1 frame per
    second, mono. Over CLOUD_MAX_BYTES it is made again at a lower rate;
    {"too_big": True} when even that does not fit (she gets the frames)."""
    base = _rel_base(row)
    rel = base + "cloud.mp4"
    src = _full(meta.get("watch") or row.get("original") or "")
    duration, has_audio = float(meta["duration"]), bool(meta.get("has_audio"))
    encode(src, _full(rel), CLOUD_SHORT_SIDE, duration, has_audio, 150, 300, 30, fps=1,
           mono=True, on_pct=on_pct)
    size = os.path.getsize(_full(rel))
    if size > CLOUD_MAX_BYTES:
        kbps = int(CLOUD_MAX_BYTES * 8 * 0.85 / duration / 1000) - (48 if has_audio else 0)
        if kbps >= 40:
            ffmpeg(["-i", src, "-map", "0:v:0", "-vf", _scale_short(CLOUD_SHORT_SIDE), "-r", "1",
                    "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k",
                    "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k"]
                   + (["-map", "0:a:0?", "-c:a", "aac", "-ac", "1", "-b:a", "48k"] if has_audio
                      else ["-an"])
                   + ["-movflags", "+faststart", _full(rel)], duration)
            size = os.path.getsize(_full(rel))
    if size > CLOUD_MAX_BYTES:
        store.remove_attachment_files([rel])
        return {"too_big": True}
    return {"file": rel, "bytes": size}


# ---------- Speech ----------

def stamp(seconds) -> str:
    """A moment in a video, "1:05" = in its 66th second (rounded down, so two
    stills a second apart never get the same mark)."""
    total = int(max(0.0, float(seconds or 0)))
    return f"{total // 60}:{total % 60:02d}"


def speech_text(segments) -> str:
    """The written speech as she reads it: "[0:31] Come back!" per line."""
    return "\n".join(f"[{stamp(s['start'])}] {s['text']}" for s in segments or [])


def _write_speech(row, source: str, duration: float) -> dict:
    status = transcribe.status()["state"]
    if status == "unavailable":
        return {"state": "unavailable"}
    if status != "ready":
        # The installer downloads it; when that did not happen, she gets it now
        # (MH 2026-11-03: no question) and writes the speech once it is here.
        transcribe.when_ready(catch_up_speech)
        transcribe.start_download()
        return {"state": "waiting"}
    wav = _full(_rel_base(row) + "audio.wav")
    try:
        ffmpeg(["-i", source, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                wav], duration)
        _live(row["id"])
        out = transcribe.transcribe(wav, duration)
        return {"state": "done", **out}
    except (Gone, attachments.AttachmentError):
        raise
    except Exception as exc:
        print(f"[Amadeus] Video #{row['id']}: speech not written:", repr(exc))
        return {"state": "failed"}
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def speech_job(attachment_id) -> None:
    """Speech for a video prepared before the speech model arrived."""
    try:
        row = _live(attachment_id)
        meta = load_meta(row)
        if meta.get("state") != READY or (meta.get("speech") or {}).get("state") != "waiting":
            return
        meta["speech"] = {"state": "writing"}
        _save_meta(row, meta)
        speech = _write_speech(row, _full(meta["watch"]), float(meta["duration"]))
        row = _live(attachment_id)
        meta = load_meta(row)
        meta["speech"] = speech
        _save_meta(row, meta)
        _update_row(attachment_id, tokens=_tokens(meta))
        start_notes(attachments.get(attachment_id))
    except Gone:
        return


def catch_up_speech() -> int:
    """The speech model arrived: write the speech of every video waiting for it."""
    count = 0
    for row in store.attachments_where("kind = 'video'", ()):
        if (load_meta(row).get("speech") or {}).get("state") == "waiting":
            start(row, "speech")
            count += 1
    return count


def resume() -> int:
    """Startup: videos the last run did not finish preparing go on."""
    count = 0
    for row in store.attachments_where("kind = 'video'", ()):
        meta = load_meta(row)
        if meta.get("state") == PREPARING:
            start(row)
            count += 1
        elif meta.get("state") == READY and (meta.get("speech") or {}).get("state") in ("writing",
                                                                                       "waiting"):
            if (meta["speech"]["state"] == "writing"):
                meta["speech"] = {"state": "waiting"}
                _save_meta(row, meta)
            if transcribe.model_ready():
                start(row, "speech")
            else:
                transcribe.when_ready(catch_up_speech)
        elif meta.get("state") == READY and row.get("notes") in (None, store.NOTES_FAILED):
            start_notes(row)       # Phase 4: notes the last run did not write
    return count


# ---------- Her prompt (Phase 3) ----------
# A video goes into her reply calls as a message of its own right before the
# message it came with (like a document - so the next turns reuse it): its
# label, then either the stills with their times (her built-in model and
# cloud models that only see pictures, V1a) or the cloud copy itself (a
# cloud model that watches videos, V3a: the turn it is sent and when asked
# about again), then what is said in it, then an end mark.

WATCH = "video"        # the real video (cloud copy) with its sound
FRAMES = "frames"      # stills + what is said
SPEECH_ONLY = "text"   # her model can't see pictures: only what is said

_URLS = {}             # file -> (mtime, data url); the desk's stills are not re-read
_URLS_MAX = 64


def _file_url(rel: str, mime: str, keep: bool = True):
    full = _full(rel)
    try:
        mtime = os.path.getmtime(full)
    except OSError:
        return None
    hit = _URLS.get(rel)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(full, "rb") as f:
        url = f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    if keep:
        if len(_URLS) >= _URLS_MAX:
            _URLS.pop(next(iter(_URLS)))
        _URLS[rel] = (mtime, url)
    return url


def is_ready(row) -> bool:
    return load_meta(row).get("state") == READY


def mode_for(row, fresh: bool, see, watch: bool) -> str:
    """How her reply call gets this video: the real video only when it is
    this message's (or asked about again), her model watches videos and the
    cloud copy is there; else the stills; only the speech when her model
    can't see pictures."""
    if see is False:
        return SPEECH_ONLY
    cloud = (load_meta(row).get("cloud") or {}).get("file")
    if fresh and watch and cloud and os.path.isfile(_full(cloud)):
        return WATCH
    return FRAMES


def speech_part(meta) -> str:
    speech = meta.get("speech") or {}
    state = speech.get("state")
    if not meta.get("has_audio") or state == "none":
        return "(The video has no sound.)"
    if state == "done":
        text = speech_text(speech.get("segments") or [])
        if not text:
            return "(Nobody speaks in it - any music or sounds were not written down.)"
        return ("WHAT IS SAID IN IT (written down by speech recognition - it can mishear words "
                "and names, and it does not say who speaks):\n" + text)
    if state in ("writing", "waiting"):
        return "(What is said in it is still being written down - you don't have it yet.)"
    return "(What is said in it could not be written down.)"


def video_block(row, mode: str, note: str = "") -> list:
    """The video as her reply call gets it (message parts)."""
    meta = load_meta(row)
    number = row.get("chat_no") or row["id"]
    parts = [{"type": "text", "text": store.video_label({**row, "caption": None}, note)}]
    if mode == WATCH:
        url = _file_url(meta["cloud"]["file"], "video/mp4", keep=False)
        if url:
            parts.append({"type": "video_url", "video_url": {"url": url}})
    elif mode == FRAMES:
        stills = []
        for f in meta.get("frames") or []:
            url = _file_url(f["file"], "image/jpeg")
            if url:
                stills += [{"type": "text", "text": f"[{stamp(f['t'])}]"},
                           {"type": "image_url", "image_url": {"url": url}}]
        if stills:
            parts.append({"type": "text", "text": "STILLS FROM IT, each marked with its time "
                                                  "from the start of the video:"})
            parts += stills
    else:
        parts.append({"type": "text", "text": "(Your current model can't see pictures: you "
                                              "could not see this video.)"})
    if mode != WATCH and row.get("notes") == store.NOTES_DONE:
        notes = notes_lines(load_watch_notes(row))
        if notes:
            parts.append({"type": "text", "text": "YOUR WATCH NOTES ON IT (you wrote them earlier, "
                                                  "one per stretch, from more stills and what is "
                                                  "said):\n" + notes})
    parts.append({"type": "text", "text": speech_part(meta)})
    parts.append({"type": "text", "text": f"[End of video {number}]"})
    return parts


def watched_words(row, mode: str) -> str:
    """For the "Watched:" line under her reply."""
    number = row.get("chat_no") or row["id"]
    meta = load_meta(row)
    said = (meta.get("speech") or {}).get("state") == "done" and (meta["speech"].get("segments"))
    if mode == WATCH:
        return f"Video {number} (the whole video with its sound)"
    if mode == FRAMES:
        return (f"Video {number} ({len(meta.get('frames') or [])} stills"
                + (" + what is said)" if said else ")"))
    return f"Video {number} (only what is said - her model can't see pictures)"


CAPTION_PROMPT = ("Describe this video in ONE short English phrase (at most 15 words) for a "
                  "note: what it shows and, if people speak, what it is about. Only what is "
                  "really there. No quotes, no 'This video shows'.")
CAPTION_STILLS = 4
CAPTION_SPEECH_CHARS = 2000


def write_caption(llm, row, see=True) -> str:
    """Its one-line note (her list, the label once it is off her desk, her
    long-term memory): from a few stills and what is said."""
    meta = load_meta(row)
    parts = [{"type": "text", "text": CAPTION_PROMPT}]
    frames = (meta.get("frames") or []) if see is not False else []
    picked = [frames[(2 * i + 1) * len(frames) // (2 * CAPTION_STILLS)]
              for i in range(min(CAPTION_STILLS, len(frames)))] if frames else []
    for f in picked:
        url = _file_url(f["file"], "image/jpeg")
        if url:
            parts += [{"type": "text", "text": f"[{stamp(f['t'])}]"},
                      {"type": "image_url", "image_url": {"url": url}}]
    speech = meta.get("speech") or {}
    said = speech_text(speech.get("segments") or []) if speech.get("state") == "done" else ""
    if said:
        parts.append({"type": "text", "text": "What is said in it (speech recognition):\n"
                                              + said[:CAPTION_SPEECH_CHARS]})
    if len(parts) == 1:
        return ""
    reply = llm.invoke([{"role": "user", "content": parts}])
    text = reply.content if isinstance(reply.content, str) else attachments._text(reply.content)
    text = re.sub(r"<[^>]*>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip()
    return text[:200]


# ---------- Watch notes and closer looks (Phase 4) ----------
# MH 2026-11-03 (1b, 2a, 3a): once a video is prepared, her model writes one
# short watch note per ~30 s stretch in the background (the document notes'
# worker), from that stretch's stills and what is said in it - every video,
# short ones too (it keeps a timeline in her prompt and her list). A message
# that points at a moment ("at 1:30", "1分30秒", "the end") - or, failing
# that, asks something whose words match a stretch's notes or speech - gets a
# closer look at that stretch: more stills and what is said then (a cloud
# model that watches videos: a short clip of it, with its sound).

STRETCH_SECONDS = 30
STRETCH_STILLS = 8
CLOSE_HALF = 10            # a closer look: 10 s each side of the moment
CLOSE_STILLS = 8
CLIP_HALF = 15             # the clip a model that watches videos gets
CLOSE_MAX = 2              # closer looks per message
CLOSE_TOKENS = CLOSE_STILLS * FRAME_TOKENS + 200
CLOSE_SPACING = 2.5        # her stills further apart than this: closer looks help

WATCH_NOTE_PROMPT = (
    "You are making watch notes on a video so you can answer questions about it later. Write "
    "ONE short note - at most 40 words, two or three sentences, NOT a line per still - on this "
    "stretch of the video \"{name}\" ({a} to {b}): the main things that happen or are talked "
    "about, each with its time (from the start of the video, as marked), and what is seen. "
    "Names and numbers word for word. Only what the stills and the speech really show - never "
    "invent a movement between two stills, and never guess who someone is. No quotes, no 'In "
    "this stretch'.")

NOTES_CAPTION_PROMPT = (
    "Describe this video in ONE short English phrase (at most 15 words) for a note, from your "
    "watch notes on it: what it shows and, if people speak, what it is about. No quotes, no "
    "'This video shows'.")


def stretches(duration) -> list:
    """[(from, to)] seconds: the video cut into equal stretches of ~30 s."""
    d = max(0.0, float(duration or 0))
    n = max(1, math.ceil(d / STRETCH_SECONDS))
    return [(round(i * d / n, 2), round((i + 1) * d / n, 2)) for i in range(n)]


def _still(row, meta, t):
    """A still at t seconds (made from the watch copy once, then kept)."""
    rel = f"{_rel_base(row)}s{int(round(t * 10)):05d}.jpg"
    if not os.path.isfile(_full(rel)) and meta.get("watch"):
        try:
            ffmpeg(["-ss", f"{t:.2f}", "-i", _full(meta["watch"]), "-map", "0:v:0", "-frames:v", "1",
                    "-vf", _scale_long(FRAME_SIDE), "-q:v", "4", _full(rel)], timeout=60)
        except Exception as exc:
            print(f"[Amadeus] Video #{row['id']}: still at {t:.1f} s not made:", repr(exc))
    return rel if os.path.isfile(_full(rel)) else None


def _evenly(items, count) -> list:
    if len(items) <= count:
        return list(items)
    return [items[(2 * i + 1) * len(items) // (2 * count)] for i in range(count)]


def span_stills(row, meta, a, b, count) -> list:
    """[{"t", "file"}] stills from a to b: the ones she already has when they
    are close enough together (short videos), else new ones."""
    main = [f for f in meta.get("frames") or [] if a <= f["t"] < b]
    if len(main) >= count - 2:
        return _evenly(main, count)
    out = []
    for i in range(count):
        t = round(a + (i + 0.5) * (b - a) / count, 2)
        rel = _still(row, meta, t)
        if rel:
            out.append({"t": t, "file": rel})
    return out


def speech_between(meta, a, b) -> list:
    """The speech lines said from a to b (a line already going at a counts)."""
    speech = meta.get("speech") or {}
    if speech.get("state") != "done":
        return []
    return [s for s in speech.get("segments") or []
            if s["start"] < b and max(float(s.get("end", s["start"])), s["start"] + 0.1) > a]


def _notes_rel(row) -> str:
    return _rel_base(row) + "notes.json"


def load_watch_notes(row) -> dict:
    """{"stretches": [{"from", "to", "note"}], "total": n} or {}."""
    try:
        with open(_full(_notes_rel(row)), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_watch_notes(row, data) -> None:
    path = _full(_notes_rel(row))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def notes_lines(data) -> str:
    return "\n".join(f"- {stamp(s['from'])}-{stamp(s['to'])}: {s['note']}"
                     for s in data.get("stretches") or [] if s.get("note"))


def notes_progress(row) -> dict:
    """{"state": "writing" / "done" / "failed", "done": k, "total": n} or {}."""
    state = row.get("notes")
    if not state:
        return {}
    data = load_watch_notes(row)
    total = int(data.get("total") or 0) or len(stretches(load_meta(row).get("duration")))
    name = {store.NOTES_WRITING: "writing", store.NOTES_DONE: "done",
            store.NOTES_FAILED: "failed"}.get(state, "writing")
    return {"state": name, "done": len(data.get("stretches") or []), "total": total}


def start_notes(row) -> bool:
    """Queue her watch notes once the video is ready and its speech written."""
    if not row or row.get("kind") != "video" or row.get("notes") == store.NOTES_DONE:
        return False
    meta = load_meta(row)
    if meta.get("state") != READY or (meta.get("speech") or {}).get("state") in ("writing", "waiting"):
        return False
    attachments.start_notes(row)
    return True


def write_watch_notes(llm, row, see=None) -> bool:
    """Write (or finish) a video's watch notes, one call per stretch, then
    (a video over a minute) a new one-line caption from them. Stops quietly
    when the video is deleted meanwhile. True when finished."""
    meta = load_meta(row)
    if meta.get("state") != READY:
        return False
    if see is None:
        see = attachments.can_see_pictures()
    spans = stretches(meta["duration"])
    data = load_watch_notes(row)
    data["total"] = len(spans)
    done = data.setdefault("stretches", [])
    name = row.get("name") or "video"
    speech_done = (meta.get("speech") or {}).get("state") == "done"
    for a, b in spans[len(done):]:
        if attachments.get(row["id"]) is None:
            return False
        parts = [{"type": "text", "text": WATCH_NOTE_PROMPT.format(name=name, a=stamp(a), b=stamp(b))}]
        if see is not False:
            for f in span_stills(row, meta, a, b, STRETCH_STILLS):
                url = _file_url(f["file"], "image/jpeg", keep=False)
                if url:
                    parts += [{"type": "text", "text": f"[{stamp(f['t'])}]"},
                              {"type": "image_url", "image_url": {"url": url}}]
        else:
            parts.append({"type": "text", "text": "(You can't see pictures: only what is said.)"})
        said = speech_text(speech_between(meta, a, b))
        parts.append({"type": "text", "text": (
            "What is said then (speech recognition - it can mishear):\n" + said if said
            else "(Nobody speaks then.)" if speech_done else "(No speech written down.)")})
        note = attachments._clean_note(llm.invoke([{"role": "user", "content": parts}]))
        done.append({"from": a, "to": b, "note": note})
        _save_watch_notes(row, data)
    if attachments.get(row["id"]) is None:
        return False
    attachments._set_notes_state(row["id"], store.NOTES_DONE)
    _update_row(row["id"], tokens=_tokens(meta) + store.estimate_tokens(notes_lines(data)))
    if len(spans) > 2:     # over a minute: four stills said too little
        try:
            text = attachments._clean_note(llm.invoke([{"role": "user", "content": (
                NOTES_CAPTION_PROMPT + f'\n\nWatch notes on the video "{name}":\n' + notes_lines(data))}]))
            if text:
                attachments.save_caption(row["id"], text[:200])
        except Exception as exc:
            print(f"[Amadeus] Video #{row['id']}: caption from notes failed:", repr(exc))
    return True


# --- Which moment does this message ask about? --------------------------------
_TIME_RES = [   # (regex, minutes group, seconds group)
    (re.compile(r"(?<![\d:])(\d{1,2}):([0-5]\d)(?![\d:])"), 1, 2),
    (re.compile(r"(\d{1,2})\s*分\s*(\d{1,2})\s*秒"), 1, 2),
    (re.compile(r"\b(\d{1,2})\s*(?:minutes?|mins?|m)\s*(?:and\s*)?(\d{1,2})\s*(?:seconds?|secs?|s)\b",
                re.IGNORECASE), 1, 2),
    (re.compile(r"(\d{1,2})\s*分(?:目|ごろ|頃|くらい|ぐらい|あたり|辺り|のところ|の所|地点)"), 1, None),
    (re.compile(r"\b(\d{1,2})\s*(?:minutes?|mins?)\s+(?:in|into|mark)\b|\bminute\s+(\d{1,2})\b",
                re.IGNORECASE), 1, None),
    (re.compile(r"(?<![分\d])(\d{1,3})\s*秒(?:目|ごろ|頃|くらい|ぐらい|あたり|辺り|のところ|の所|地点)"),
     None, 1),
    (re.compile(r"\b(\d{1,3})\s*(?:seconds?|secs?|s)\s+(?:in|into|mark)\b|\bsecond\s+(\d{1,3})\b",
                re.IGNORECASE), None, 1),
]
_VID_AFTER = r"(?!の?(?:動画|ビデオ|映像|ムービー|録画))"
_END_RE = re.compile(r"\b(?:at|near|towards?)\s+the\s+(?:very\s+)?end\b|\bthe\s+ending\b|"
                     r"\blast\s+(?:part|bit|few\s+seconds|seconds|scene)\b|"
                     r"最後" + _VID_AFTER + r"|終わり|ラスト" + _VID_AFTER + r"|終盤", re.IGNORECASE)
_START_RE = re.compile(r"\b(?:at|near)\s+the\s+(?:very\s+)?(?:start|beginning)\b|"
                       r"\bfirst\s+(?:part|bit|few\s+seconds|seconds|scene)\b|"
                       r"最初" + _VID_AFTER + r"|冒頭|始まり|序盤", re.IGNORECASE)


def _group(m, n) -> int:
    """A regex group, or the next one when an alternative matched there."""
    for g in range(n, (m.re.groups or 0) + 1):
        if m.group(g) is not None:
            return int(m.group(g))
    return 0


def window(t, duration, half=CLOSE_HALF) -> tuple:
    """(from, to) around t, 2 x half long, kept inside the video."""
    d = float(duration or 0)
    a = max(0.0, min(t - half, d - 2 * half))
    return round(a, 2), round(min(d, a + 2 * half), 2)


def moment_times(text: str, duration, ends: bool = True) -> list:
    """The moments (seconds) the message names: "1:30", "1分30秒", "90 seconds
    in", "minute 2", and (ends=True) "the end" / "the start". Times past the
    end of the video are not moments of it."""
    d = float(duration or 0)
    found = []
    taken = []
    for rx, mg, sg in _TIME_RES:
        for m in rx.finditer(text or ""):
            if any(a <= m.start() < b for a, b in taken):
                continue
            if mg and sg:
                t = int(m.group(mg)) * 60 + int(m.group(sg))
            elif mg:
                t = _group(m, mg) * 60
            else:
                t = _group(m, sg)
            if t <= d + 1:
                found.append(min(float(t), d))
                taken.append(m.span())
    if ends and _START_RE.search(text or ""):
        found.append(0.0)
    if ends and _END_RE.search(text or ""):
        found.append(d)
    return found


def needs_close_looks(row) -> bool:
    """True when her stills of it are far enough apart for a closer look to
    show more (and it is ready)."""
    meta = load_meta(row)
    frames = len(meta.get("frames") or [])
    return (meta.get("state") == READY and bool(meta.get("watch")) and frames > 0
            and float(meta.get("duration") or 0) / frames > CLOSE_SPACING)


_MOMENT_STOP = {"video", "videos", "clip", "clips", "movie", "vid", "mp4", "mov", "happens",
                "happen", "happened", "say", "said", "says", "see", "seen", "saw", "when", "where",
                "who", "which", "why", "they", "he", "she", "him", "her", "his", "then", "time",
                "動画", "ビデオ", "映像"}


def search_moment(rows, question: str, context: str = ""):
    """(row, from, to) of the stretch whose notes + speech match the
    question best, or None."""
    docs, where = [], []
    for row in rows:
        meta = load_meta(row)
        notes = {s["from"]: s.get("note", "") for s in load_watch_notes(row).get("stretches") or []}
        for a, b in stretches(meta.get("duration")):
            said = " ".join(s["text"] for s in speech_between(meta, a, b))
            docs.append(attachments._terms(notes.get(a, "") + " " + said))
            where.append((row, a, b))
    if len(docs) < 2:
        return None
    scores = attachments.best_matches(docs, question, context,
                                      attachments._STOP | _MOMENT_STOP)
    if not scores:
        return None
    row, a, b = where[scores[0][1]]
    return row, a, b


def _clip(row, meta, a, b):
    """A short clip of a to b with its sound (480p), for a model that
    watches videos (made once, then kept)."""
    rel = f"{_rel_base(row)}clip{int(a * 10):05d}_{int(b * 10):05d}.mp4"
    if not os.path.isfile(_full(rel)) and meta.get("watch"):
        try:
            ffmpeg(["-ss", f"{a:.2f}", "-i", _full(meta["watch"]), "-t", f"{b - a:.2f}",
                    "-map", "0:v:0", "-map", "0:a:0?", "-vf", _scale_short(CLOUD_SHORT_SIDE),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                    "-c:a", "aac", "-ac", "1", "-b:a", "64k", "-movflags", "+faststart", _full(rel)],
                   b - a, timeout=120)
        except Exception as exc:
            print(f"[Amadeus] Video #{row['id']}: clip {a:.0f}-{b:.0f} s not made:", repr(exc))
    return rel if os.path.isfile(_full(rel)) else None


def close_block(row, t, how: str, watch: bool) -> tuple:
    """(message parts, words for the "Watched:" line, True when it carries a
    clip) of a closer look at the moment t. how = "time" (the message named
    it) or "words" (found by search)."""
    meta = load_meta(row)
    number = row.get("chat_no") or row["id"]
    duration = meta.get("duration")
    a, b = window(t, duration, CLIP_HALF if watch else CLOSE_HALF)
    found = (" - found by matching words of this message in your notes and the speech, so it may "
             "not be the moment they mean" if how == "words" else "")
    parts = [{"type": "text", "text": f"[Video {number}, a closer look at {stamp(a)}-{stamp(b)} "
                                      f"for this message{found}]"}]
    clip = _clip(row, meta, a, b) if watch else None
    url = _file_url(clip, "video/mp4", keep=False) if clip else None
    if url:
        parts += [{"type": "text", "text": f"A CLIP OF THAT STRETCH, with its sound (it starts at "
                                           f"{stamp(a)} of the video: add {stamp(a)} to the clip's "
                                           "own times):"},
                  {"type": "video_url", "video_url": {"url": url}}]
        words = f"Video {number} (the clip {stamp(a)}-{stamp(b)} with its sound)"
    else:
        stills = []
        for f in span_stills(row, meta, a, b, CLOSE_STILLS):
            u = _file_url(f["file"], "image/jpeg", keep=False)
            if u:
                stills += [{"type": "text", "text": f"[{stamp(f['t'])}]"},
                           {"type": "image_url", "image_url": {"url": u}}]
        if stills:
            parts.append({"type": "text", "text": "STILLS FROM THAT STRETCH, each marked with its "
                                                  "time from the start of the video:"})
            parts += stills
        words = f"Video {number} (a closer look at {stamp(a)}-{stamp(b)})"
    said = speech_text(speech_between(meta, a, b))
    if said:
        parts.append({"type": "text", "text": "WHAT IS SAID THEN (speech recognition - it can "
                                              "mishear):\n" + said})
    elif (meta.get("speech") or {}).get("state") == "done":
        parts.append({"type": "text", "text": "(Nobody speaks then.)"})
    parts.append({"type": "text", "text": f"[End of video {number}]"})
    return parts, words, bool(url)


# ---------- For the screen ----------

STEP_WORDS = {"watch": "Preparing", "frames": "Picking frames", "cloud": "Preparing",
              "speech": "Writing down the speech"}


def card_info(row) -> dict:
    meta = load_meta(row)
    state = meta.get("state") or PREPARING
    speech = (meta.get("speech") or {}).get("state") or "none"
    info = {"duration": meta.get("duration"), "length": length_text(meta.get("duration")),
            "has_audio": bool(meta.get("has_audio")),
            "video": {"state": state, "step": meta.get("step") or "watch",
                      "pct": int(meta.get("pct") or 0), "message": meta.get("message") or "",
                      "speech": speech, "frames": len(meta.get("frames") or [])},
            "video_url": f"/attachments/{row['id']}/file" if meta.get("watch") else None}
    if state == PREPARING:
        info["warning"] = f"{STEP_WORDS.get(meta.get('step'), 'Preparing')}... {info['video']['pct']}%"
    elif state == FAILED:
        info["warning"] = meta.get("message") or "This video could not be prepared."
    elif speech in ("waiting", "writing") and meta.get("has_audio"):
        dl = transcribe.status()
        pct = int(100 * dl["done"] / max(1, dl["total"])) if dl["state"] == "downloading" else None
        info["warning"] = ("Writing down the speech" + (f" once the speech model has downloaded ({pct}%)"
                                                        if pct is not None else "") + "...")
    return info

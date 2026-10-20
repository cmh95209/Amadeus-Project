"""Stopwatch lines for the speed work: one "[Timing]" line per stage of a
reply (her Japanese line, the browser asking for her voice, the voice, the
English line) and one when her built-in model loads (with graphics memory).

Measure-only: nothing here changes what she says or how she sounds. Every
public function swallows its own errors - a timing bug may cost a log line,
never a reply.

Where the numbers come from:
- the model server's own report on each answer: llama-server sends
  "timings" (pieces read fresh vs reused from its memory, writing speed);
  cloud services send "usage" (token counts, cached tokens when they say);
- the wall clock (time.monotonic) at each stage;
- the voice WAV header (sample rate, channels, bit depth -> seconds of audio).
"""
import subprocess
import threading
import time

# Model-server reports collected in the current thread since the last
# start_reports() (a reply can make more than one model call).
_local = threading.local()

# reply id -> (enter time, line-ready time), for the voice line's
# "Enter -> first sound". Bounded: only the newest few replies are kept.
_replies: dict = {}
_replies_lock = threading.Lock()
_MAX_REPLIES = 50

WAV_HEADER_FALLBACK = 44


def now() -> float:
    return time.monotonic()


def log(reply, text: str) -> None:
    who = f"reply {reply}" if reply else "reply ?"
    print(f"[Timing] {who}: {text}", flush=True)


# ---------- model-server reports ----------

def start_reports() -> None:
    """Forget earlier reports in this thread (call when a stage begins)."""
    _local.reports = []


def note_server_report(response) -> None:
    """Called with every raw chat answer (a dict or the client's response
    object). Keeps a small summary for the current thread."""
    try:
        data = response if isinstance(response, dict) else response.model_dump()
        report = parse_server_report(data)
        if report is not None:
            if not hasattr(_local, "reports"):
                _local.reports = []
            _local.reports.append(report)
    except Exception:
        pass


def take_reports() -> list:
    reports = getattr(_local, "reports", None) or []
    _local.reports = []
    return reports


def parse_server_report(data) -> dict | None:
    """llama-server "timings" or a cloud "usage" block -> one flat dict."""
    if not isinstance(data, dict):
        return None
    t = data.get("timings")
    if isinstance(t, dict) and "prompt_n" in t:
        return {
            "kind": "local",
            "new": int(t.get("prompt_n") or 0),
            "reused": int(t.get("cache_n") or 0),
            "read_ms": float(t.get("prompt_ms") or 0.0),
            "wrote": int(t.get("predicted_n") or 0),
            "write_ms": float(t.get("predicted_ms") or 0.0),
            # Reasoning on: llama-server returns the thoughts apart
            # (--reasoning-format deepseek) - their length in characters.
            "thought_chars": _thought_chars(data),
        }
    u = data.get("usage")
    if isinstance(u, dict) and "prompt_tokens" in u:
        details = u.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        out_details = u.get("completion_tokens_details") or {}
        thinking = out_details.get("reasoning_tokens") if isinstance(out_details, dict) else None
        return {
            "kind": "cloud",
            "prompt": int(u.get("prompt_tokens") or 0),
            "cached": int(cached) if cached is not None else None,
            "wrote": int(u.get("completion_tokens") or 0),
            "thinking": int(thinking) if thinking is not None else None,
        }
    return None


def _thought_chars(data) -> int:
    try:
        message = (data.get("choices") or [{}])[0].get("message") or {}
        return len(message.get("reasoning_content") or "")
    except (AttributeError, IndexError, TypeError):
        return 0


def describe_reports(reports) -> str:
    """Plain summary of one or more model calls, '' when there is none."""
    if not reports:
        return ""
    calls = f" over {len(reports)} model calls" if len(reports) > 1 else ""
    local = [r for r in reports if r.get("kind") == "local"]
    if local:
        new = sum(r["new"] for r in local)
        reused = sum(r["reused"] for r in local)
        read_s = sum(r["read_ms"] for r in local) / 1000.0
        wrote = sum(r["wrote"] for r in local)
        write_s = sum(r["write_ms"] for r in local) / 1000.0
        speed = f" ({wrote / write_s:.0f} per second)" if write_s > 0 else ""
        thought = sum(r.get("thought_chars", 0) for r in local)
        thought_text = f", of it thinking: {thought:,} characters" if thought else ""
        return (f"read {new:,} new + {reused:,} remembered pieces in {read_s:.2f} s, "
                f"wrote {wrote:,} pieces in {write_s:.2f} s{speed}{thought_text}{calls}")
    prompt = sum(r["prompt"] for r in reports)
    wrote = sum(r["wrote"] for r in reports)
    cached = [r["cached"] for r in reports if r.get("cached") is not None]
    cache = f" ({sum(cached):,} cached by the service)" if cached else ""
    thinking = [r["thinking"] for r in reports if r.get("thinking") is not None]
    think = f" (of it thinking: {sum(thinking):,})" if thinking else ""
    return f"sent {prompt:,} pieces{cache}, got {wrote:,} back{think}{calls}"


# ---------- reply stages ----------

def line_ready(reply, started: float, label: str = "her Japanese line") -> None:
    """Her line is ready to be spoken: log how long it took + the model's
    report, and remember the times for the voice line."""
    try:
        ready = now()
        with _replies_lock:
            if reply:
                _replies[reply] = (started, ready)
                while len(_replies) > _MAX_REPLIES:
                    _replies.pop(next(iter(_replies)))
        detail = describe_reports(take_reports())
        log(reply, f"{label} took {ready - started:.2f} s" + (f" - {detail}" if detail else ""))
    except Exception:
        pass


def english_done(reply, started: float) -> None:
    try:
        detail = describe_reports(take_reports())
        log(reply, f"English line took {now() - started:.2f} s" + (f" - {detail}" if detail else ""))
    except Exception:
        pass


def wav_format(header: bytes):
    """(header length, bytes per second) from a WAV header, or None."""
    try:
        if len(header) < 36 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None
        channels = int.from_bytes(header[22:24], "little")
        rate = int.from_bytes(header[24:28], "little")
        bits = int.from_bytes(header[34:36], "little")
        per_second = rate * channels * bits // 8
        if per_second <= 0:
            return None
        pos = header.find(b"data", 12)
        length = pos + 8 if pos >= 0 else WAV_HEADER_FALLBACK
        return length, per_second
    except Exception:
        return None


class VoiceWatch:
    """Times one voice line as its WAV bytes stream to the browser:
    browser wait (line ready -> /speech asked), first real sound (past
    the header), the whole line, and seconds of audio. Logs on finish()."""

    def __init__(self, reply):
        self.reply = reply
        self.asked = now()
        self.head = b""
        self.total = 0
        self.first = None
        self.done = False
        try:
            with _replies_lock:
                times = _replies.get(reply) if reply else None
            self.enter, self.ready = times if times else (None, None)
            if self.ready is not None:
                log(reply, f"browser asked for her voice {self.asked - self.ready:.2f} s "
                           "after the line was ready")
        except Exception:
            self.enter = self.ready = None

    def feed(self, chunk) -> None:
        try:
            if not chunk:
                return
            self.total += len(chunk)
            if len(self.head) < 512:
                self.head += bytes(chunk[:512 - len(self.head)])
            if self.first is None:
                fmt = wav_format(self.head)
                header_len = fmt[0] if fmt else WAV_HEADER_FALLBACK
                if self.total > header_len:
                    self.first = now()
        except Exception:
            pass

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        try:
            end = now()
            if self.first is None:
                log(self.reply, f"voice produced no sound ({end - self.asked:.2f} s)")
                return
            text = (f"voice first sound after {self.first - self.asked:.2f} s; "
                    f"whole line {end - self.asked:.2f} s")
            fmt = wav_format(self.head)
            if fmt:
                audio_s = max(0, self.total - fmt[0]) / fmt[1]
                if audio_s > 0:
                    text += (f" for {audio_s:.1f} s of audio "
                             f"({(end - self.asked) / audio_s:.2f}x real time)")
            log(self.reply, text)
            if self.enter is not None:
                log(self.reply, f"Enter -> first sound {self.first - self.enter:.2f} s")
        except Exception:
            pass


# ---------- her built-in model ----------

def gpu_memory():
    """(used GB, total GB) of the first NVIDIA card, or None (no NVIDIA
    card / no nvidia-smi / any error)."""
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": 5}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"], **kwargs)
        if out.returncode != 0:
            return None
        used, total = out.stdout.strip().splitlines()[0].split(",")
        return float(used) / 1024.0, float(total) / 1024.0
    except Exception:
        return None


def model_loaded(started: float, before) -> None:
    """Her model answered: load time + graphics memory before -> after."""
    try:
        text = f"her model loaded in {now() - started:.1f} s"
        after = gpu_memory()
        if before and after:
            text += (f"; graphics memory in use {before[0]:.1f} -> {after[0]:.1f} GB "
                     f"(+{after[0] - before[0]:.1f} GB) of {after[1]:.1f} GB")
        elif after:
            text += f"; graphics memory in use {after[0]:.1f} of {after[1]:.1f} GB"
        print(f"[Timing] {text}", flush=True)
    except Exception:
        pass

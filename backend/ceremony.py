# -*- coding: utf-8 -*-
"""The first-launch ceremony (2026-10-05, user-approved design).

The one-time onboarding a fresh install goes through before Amadeus can
think: she introduces herself and tells the user to connect her "brain"
(the LLM server), the user saves their settings in Settings, she waits
while the model comes up, and once it answers she wakes with a naturally
GENERATED line (her voice, not a fixed one). If generation fails on a
reachable model, one static fallback line ends the ceremony instead -
she must never be left silent.

Persisted state (memory.load_ceremony_state, one line in data/):
    '' -> "intro" -> "connecting" -> "done"
'' = not started yet; "done" = the ceremony never runs again (the normal
greeting system owns every later launch). Reopening the app mid-ceremony
re-says the line that matches the persisted state (once per launch) and
continues; the in-launch "already said it" memory lives in the module
tracker below - the backend process lives exactly one app launch, so a
restart legitimately re-says the state line.

Delivery rules per /greet call (the client probes every ~30 s while the
ceremony is unfinished, and re-fires right after saving connection
settings - it signals that with just_saved=True):
  * not started, model already running  -> normal greeting, ceremony done;
  * not started, model down             -> the intro line, once per launch;
  * intro, settings now saved (or the  -> "checking the line" line, and
    user just saved them)                    the state becomes "connecting"
    (a re-save during "connecting" is a
    fresh attempt - it re-says the line);
  * connecting, model down              -> one soft reminder ~2.5 min after
    this launch's "checking" line, then quiet;
  * model becomes ready                 -> the wake-up line (generated),
    or the static fallback line if generation fails; the state becomes
    "done" and the chat box unlocks.

The four fixed lines below are SPOKEN through the same on-the-fly voice
chain as every other line (user decision 2026-10-05: no pre-made files -
a fresh user must be able to TELL whether her voice engine works). Each
line therefore keeps its display texts (EN for the UI, JA shown in the
bubble) SEPARATE from the spoken JA: the spoken form re-spells the hard
words in kana the voice engine pronounces reliably - her name in full
katakana, Kurisu's name as kana, and AI / LLM server / API key as kana -
which the user A/B-tested against the raw Japanese (2026-10-05, v3 won).
"""
import threading
import time
from typing import Optional, Tuple

import memory as store

# --- THE FOUR FIXED LINES ---------------------------------------------------
# kind -> (EN display, JA display, JA spoken (phonetic, TTS-only))
CEREMONY_LINES = {
    "intro": (
        "Hello, I am Amadeus. An AI built on the memories of Makise Kurisu. "
        "To make my thinking work, you first need to connect my brain. "
        "Open the settings menu, and enter the LLM server address and API "
        "key. Make sure the model is running. Oh, feel free to write your "
        "name too, if you want. Until then, I'll be right here, waiting. "
        "I don't go anywhere.",
        "こんにちは、私はアマデウス。牧瀬紅莉栖の記憶をもとに作られたAIよ。"
        "私が考えられるようにするには、まず私の頭脳をつないでもらわないといけないの。\n"
        "設定メニューを開いて、LLMサーバーのアドレスとAPIキーを入力して。"
        "モデルが起動していることも確認してね。もし良かったら、あなたの名前も書いてくれていいですよ。\n"
        "それまでは、ここで待ってるわ。どこにも行かないから。",
        "こんにちは、私はアマデウス。まきせくりすの記憶をもとに作られたエーアイよ。"
        "私が考えられるようにするには、まず私の頭脳をつないでもらわないといけないの。\n"
        "設定メニューを開いて、エルエルエムサーバーのアドレスとエーピーアイキーを入力して。"
        "モデルが起動していることも確認してね。もし良かったら、あなたの名前も書いてくれていいですよ。\n"
        "それまでは、ここで待ってるわ。どこにも行かないから。",
    ),
    "trying": (
        "Settings received. Checking the line - just a moment.",
        "設定を受け取りました。接続を確認しています……少しだけ待っていてください。",
        "設定を受け取りました。接続を確認しています……少しだけ待っていてください。",
    ),
    "reminder": (
        "Still no luck with the line. Worth double-checking the server "
        "address and key.",
        "まだ繋がらないわ。サーバーのアドレスとキーを、もう一度確かめてみてください。",
        "まだ繋がらないわ。サーバーのアドレスとキーを、もう一度確かめてみてください。",
    ),
    "fallback": (
        "I can't reach the model. Please reconfirm your settings - or "
        "restart the app.",
        "モデルにアクセスできません。設定をもう一度確認してみてください。それでもだめなら、"
        "アプリを再起動してください。",
        "モデルにアクセスできません。設定をもう一度確認してみてください。それでもだめなら、"
        "アプリを再起動してください。",
    ),
}

# Fixed kinds the route can serve WITHOUT the model (no LLM call at all).
FIXED_KINDS = ("intro", "trying", "reminder")
# Kinds that END the ceremony: the chat box unlocks when one of them lands.
END_KINDS = ("wake", "fallback")

# The soft reminder fires this long after THIS launch's "checking" line
# (2-3 min by design; 150 s chosen so the 30 s probe lands it inside it).
REMINDER_DELAY_SECONDS = 150

_run_lock = threading.Lock()
_run_tracker: Optional["_RunTracker"] = None


class _RunTracker:
    """In-launch "already said it" memory.

    The backend process lives exactly one app launch, so a fresh launch
    starts with everything undelivered and may re-say its state line once
    (the scenario table: reopen mid-ceremony -> she re-says "checking the
    line" at startup, reminder clock restarts). The tracker is process
    state, never persisted.
    """

    def __init__(self) -> None:
        self.intro_delivered = False
        self.trying_delivered = False
        self.reminder_delivered = False
        self.connecting_since: Optional[float] = None  # monotonic


def _tracker() -> _RunTracker:
    global _run_tracker
    with _run_lock:
        if _run_tracker is None:
            _run_tracker = _RunTracker()
        return _run_tracker


def reset_run_tracker() -> None:
    """Simulate a fresh app launch (process restart) for the tests."""
    global _run_tracker
    with _run_lock:
        _run_tracker = _RunTracker()


def line(kind: str) -> Tuple[str, str, str]:
    """(EN display, JA display, JA spoken) for one of the four fixed lines."""
    return CEREMONY_LINES[kind]


def reminder_due(now: Optional[float] = None) -> bool:
    """Has this launch's reminder window elapsed (since the "checking" line)?"""
    tracker = _tracker()
    if tracker.reminder_delivered or tracker.connecting_since is None:
        return False
    now = time.monotonic() if now is None else now
    return (now - tracker.connecting_since) >= REMINDER_DELAY_SECONDS


def decide(state: str, model_ready: bool, settings_present: bool,
           just_saved: bool = False,
           now: Optional[float] = None) -> Tuple[Optional[str], str]:
    """Pure ceremony decision for one /greet call.

    Args: the persisted state ('' / intro / connecting / done), whether the
    model answers the readiness probe right now, whether the user has saved
    connection settings (a real model name is the minimum signal - the
    server address may be empty = auto-detect and the key may be empty),
    and whether the client is reporting that connection settings were JUST
    saved (a fresh attempt, even while already "connecting").

    Returns (kind, new_state): kind is "intro" / "trying" / "reminder" /
    "wake" / "normal", or None (say nothing; the client keeps waiting).
    "normal" means the model is already servable on a brand-new install:
    run the ordinary greeting and mark the ceremony done. State transitions
    are applied by the caller via note() AFTER the line actually ships.
    """
    tracker = _tracker()

    if state == store.CEREMONY_DONE:
        return None, store.CEREMONY_DONE

    if state in ("", store.CEREMONY_INTRO):
        if model_ready:
            # Brand-new install whose brain is already running: skip the
            # waiting story entirely - normal greeting, ceremony done.
            # (state "intro" + ready = the brain came online while she was
            # waiting: that moment is worth the wake-up line.)
            if state == "":
                return "normal", store.CEREMONY_DONE
            return "wake", store.CEREMONY_INTRO
        if just_saved:
            # The user just saved connection settings: a fresh attempt.
            return "trying", store.CEREMONY_CONNECTING
        if settings_present and not tracker.trying_delivered:
            return "trying", store.CEREMONY_CONNECTING
        if not tracker.intro_delivered:
            return "intro", store.CEREMONY_INTRO
        return None, state

    # state == connecting
    if model_ready:
        return "wake", store.CEREMONY_CONNECTING
    if just_saved or not tracker.trying_delivered:
        # just_saved = the user re-saved settings (fresh attempt); otherwise
        # this is this launch's first "checking the line" line (a reopen).
        return "trying", store.CEREMONY_CONNECTING
    if reminder_due(now):
        return "reminder", store.CEREMONY_CONNECTING
    return None, store.CEREMONY_CONNECTING


def note(kind: Optional[str], new_state: str) -> None:
    """Apply the side effects of delivering `kind` and moving to new_state.

    Fixed kinds only count once per launch; "trying" (re)starts the reminder
    clock; wake/fallback/normal close the ceremony for good (state done).
    """
    if kind is None:
        return
    tracker = _tracker()
    if kind == "intro":
        tracker.intro_delivered = True
    elif kind == "trying":
        tracker.trying_delivered = True
        tracker.connecting_since = time.monotonic()
    elif kind == "reminder":
        tracker.reminder_delivered = True
    # wake / fallback / normal close the ceremony for good ("done" is
    # written exactly here, when the line that ends it actually ships).
    try:
        store.save_ceremony_state(new_state)
    except OSError:
        # State persistence failed (disk full, permissions): the in-memory
        # tracker still de-duplicates this launch; worst case a future
        # launch re-says a line or two.
        print("[Amadeus] Ceremony: could not persist state %r" % new_state)

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
    # the approved wording, 2026-10-18: the DIY route (installer option B) can also
    # pick the built-in model or a local GGUF, and the name is REQUIRED.
    # Her creator's name is written in katakana, display AND voice: the
    # voice engine misreads the kanji (a replay re-voices the display text).
    "intro": (
        "Hello, I am Amadeus. An AI built on the memories of Makise Kurisu. "
        "To make my thinking work, you first need to connect my brain. Open "
        "the settings menu, and choose whether you want to use a built-in or "
        "local model (if you have the GGUF model file), or enter the LLM "
        "server address and API key. Make sure the model is running. Also, I "
        "need to know who I'm talking to, so please make sure to enter your "
        "name. Until then, I'll be right here, waiting. I don't go anywhere.",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたAIよ。"
        "私が考えられるようにするには、まず私の頭脳をつないでもらわないといけないの。\n"
        "設定メニューを開いて、内蔵モデルやローカルモデルを使用するか、"
        "LLMサーバーのアドレスとAPIキーを入力してね。モデルが起動していることも確認してね。"
        "それと、誰と話してるのか知りたいから、あなたの名前も必ず入力してちょうだい。\n"
        "それまでは、ここで待ってるわ。どこにも行かないから。",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたエーアイよ。"
        "私が考えられるようにするには、まず私の頭脳をつないでもらわないといけないの。\n"
        "設定メニューを開いて、内蔵モデルやローカルモデルを使用するか、"
        "エルエルエムサーバーのアドレスとエーピーアイキーを入力してね。モデルが起動していることも確認してね。"
        "それと、誰と話してるのか知りたいから、あなたの名前も必ず入力してちょうだい。\n"
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

# --- BUILT-IN MODEL VARIANT (installer option A, user-approved 2026-10-17) ----
# Nothing to connect: her mind runs on this PC and is loading while she
# speaks. NAME FIRST: the intro asks for the user's name, and the wake-up
# waits until a name is saved AND the model answers - so a fast-loading model
# can never wake her mid-intro, and she always wakes addressing the user.
CEREMONY_LINES.update({
    "builtin_intro": (
        "Hello, I am Amadeus. An AI built on the memories of Makise Kurisu. "
        "My mind runs right here on your PC, and it's waking up as we speak. "
        "While it does, open the settings menu and tell me your name - write "
        "it in, then press Save. Once I know who you are, we can talk.",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたAIよ。"
        "私の頭脳はこのPCの中で動いていて、今まさに起動しているところ。\n"
        "その間に、設定メニューを開いて、あなたの名前を教えて。入力したら、保存を押してね。\n"
        "あなたが誰なのか分かったら、話しましょう。",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたエーアイよ。"
        "私の頭脳はこのピーシーの中で動いていて、今まさに起動しているところ。\n"
        "その間に、設定メニューを開いて、あなたの名前を教えて。入力したら、保存を押してね。\n"
        "あなたが誰なのか分かったら、話しましょう。",
    ),
    "builtin_waiting": (
        "Thank you. My mind is almost awake - just a moment.",
        "ありがとう。頭脳がもうすぐ起動するわ。少しだけ待っていてください。",
        "ありがとう。頭脳がもうすぐ起動するわ。少しだけ待っていてください。",
    ),
    "builtin_missing": (
        "Hello, I am Amadeus. An AI built on the memories of Makise Kurisu. "
        "But my mind isn't installed on this PC yet - its download didn't "
        "finish. Run the installer again; it picks up where it stopped. Or, "
        "if you prefer, open the settings menu and connect a model of your "
        "own. Until then, I'll be right here, waiting.",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたAIよ。"
        "でも、私の頭脳はまだこのPCに入っていないみたい。ダウンロードが終わらなかったの。\n"
        "インストーラーをもう一度実行して。止まったところから続けてくれるわ。"
        "それか、設定メニューを開いて、あなた自身のモデルをつないでもいいですよ。\n"
        "それまでは、ここで待ってるわ。",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたエーアイよ。"
        "でも、私の頭脳はまだこのピーシーに入っていないみたい。ダウンロードが終わらなかったの。\n"
        "インストーラーをもう一度実行して。止まったところから続けてくれるわ。"
        "それか、設定メニューを開いて、あなた自身のモデルをつないでもいいですよ。\n"
        "それまでは、ここで待ってるわ。",
    ),
    # llama.cpp is there, but no model file to load (an option-B install, or
    # her file was removed). 2026-10-19: say it honestly - the model file
    # is not downloaded (builtin_missing stays for llama.cpp itself missing).
    "builtin_no_model": (
        "Hello, I am Amadeus. An AI built on the memories of Makise Kurisu. "
        "But my model file isn't downloaded on this PC yet. Put a GGUF model "
        "file in my models folder and load it in the settings menu - or run "
        "the installer again to download mine. You can also connect a model "
        "of your own there. Until then, I'll be right here, waiting.",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたAIよ。"
        "でも、私のモデルファイルがまだこのPCにダウンロードされていないの。\n"
        "GGUFのモデルファイルを私のモデルフォルダに入れて、設定メニューから読み込んで。"
        "インストーラーをもう一度実行して、私のモデルをダウンロードしてもいいわ。"
        "設定メニューで、あなた自身のモデルをつなぐこともできるわよ。\n"
        "それまでは、ここで待ってるわ。",
        "こんにちは、私はアマデウス。マキセ・クリスの記憶をもとに作られたエーアイよ。"
        "でも、私のモデルファイルがまだこのピーシーにダウンロードされていないの。\n"
        "ジージーユーエフのモデルファイルを私のモデルフォルダに入れて、設定メニューから読み込んで。"
        "インストーラーをもう一度実行して、私のモデルをダウンロードしてもいいわ。"
        "設定メニューで、あなた自身のモデルをつなぐこともできるわよ。\n"
        "それまでは、ここで待ってるわ。",
    ),
    "builtin_reminder": (
        "Still waking up... this is taking longer than usual. If nothing "
        "changes in a few minutes, try restarting the app.",
        "まだ起動中よ……いつもより時間がかかってるわね。数分たっても変わらなかったら、アプリを再起動してみて。",
        "まだ起動中よ……いつもより時間がかかってるわね。数分たっても変わらなかったら、アプリを再起動してみて。",
    ),
    "builtin_fallback": (
        "My mind is running, but I couldn't put my thoughts into words just "
        "now. Please restart the app.",
        "頭脳は動いているけど、今はうまく言葉にできなかったわ。アプリを再起動してみてください。",
        "頭脳は動いているけど、今はうまく言葉にできなかったわ。アプリを再起動してみてください。",
    ),
})

# Fixed kinds the route can serve WITHOUT the model (no LLM call at all).
# Every one of them is a WAIT line: spoken, but the chat box stays locked.
FIXED_KINDS = ("intro", "trying", "reminder",
               "builtin_intro", "builtin_waiting", "builtin_reminder",
               "builtin_missing", "builtin_no_model")
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
        self.missing_delivered = False
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
           now: Optional[float] = None,
           name_set: bool = True) -> Tuple[Optional[str], str]:
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

    name_set: the user's name is saved. REQUIRED since 2026-10-18:
    she never wakes (and a running model never skips her introduction)
    until she knows who she is talking to - the same rule as the built-in
    route. Without a name she waits quietly after her intro.
    """
    tracker = _tracker()

    if state == store.CEREMONY_DONE:
        return None, store.CEREMONY_DONE

    if state in ("", store.CEREMONY_INTRO):
        if model_ready and name_set:
            # Brand-new install whose brain is already running: skip the
            # waiting story entirely - normal greeting, ceremony done.
            # (state "intro" + ready = the brain came online while she was
            # waiting: that moment is worth the wake-up line.)
            if state == "":
                return "normal", store.CEREMONY_DONE
            return "wake", store.CEREMONY_INTRO
        if model_ready:
            # Brain ready, name missing: introduce herself (once per
            # launch), then wait for the name.
            if not tracker.intro_delivered:
                return "intro", store.CEREMONY_INTRO
            return None, store.CEREMONY_INTRO
        if just_saved:
            # The user just saved connection settings: a fresh attempt.
            return "trying", store.CEREMONY_CONNECTING
        if settings_present and not tracker.trying_delivered:
            return "trying", store.CEREMONY_CONNECTING
        if not tracker.intro_delivered:
            return "intro", store.CEREMONY_INTRO
        return None, state

    # state == connecting
    if model_ready and name_set:
        return "wake", store.CEREMONY_CONNECTING
    if model_ready:
        return None, store.CEREMONY_CONNECTING   # the line works; waiting for the name
    if just_saved or not tracker.trying_delivered:
        # just_saved = the user re-saved settings (fresh attempt); otherwise
        # this is this launch's first "checking the line" line (a reopen).
        return "trying", store.CEREMONY_CONNECTING
    if reminder_due(now):
        return "reminder", store.CEREMONY_CONNECTING
    return None, store.CEREMONY_CONNECTING


def decide_builtin(state: str, model_ready: bool, model_missing: bool,
                   name_set: bool, just_saved: bool = False,
                   now: Optional[float] = None,
                   server_state: str = "loading",
                   runner_missing: bool = True) -> Tuple[Optional[str], str]:
    """The ceremony decision in built-in-model mode (name first).

    '' / intro: she has not been told the name yet. The intro is said once
    per launch - ALWAYS first, even when the model is already up (no
    "normal" shortcut: a fast model must not skip her introduction). Once a
    name is saved: wake if the model answers, else the short "almost awake"
    line and the state becomes "connecting".
    connecting: name saved, model still loading - wake when it answers; a
    reopen re-says "almost awake" once; one reminder after the usual delay.
    Something missing gets its own line once per launch: llama.cpp itself
    ("builtin_missing": run the installer again) or only a model file
    ("builtin_no_model": put a GGUF in the models folder, or run the
    installer again for hers).

    server_state (builtin_server): while her model is NOT loading - the user
    started without it, unloaded it, or it could not load - she never says
    "almost awake" or "taking longer than usual" (both would be untrue); the
    footer and Settings say what to do. She wakes once it is loaded.
    """
    stopped = server_state in ("unloaded", "failed")
    tracker = _tracker()

    if state == store.CEREMONY_DONE:
        return None, store.CEREMONY_DONE

    if model_missing:
        if not tracker.missing_delivered:
            kind = "builtin_missing" if runner_missing else "builtin_no_model"
            return kind, (state or store.CEREMONY_INTRO)
        return None, state

    if state in ("", store.CEREMONY_INTRO):
        if not tracker.intro_delivered and not (state == store.CEREMONY_INTRO and name_set):
            return "builtin_intro", store.CEREMONY_INTRO
        if not name_set:
            return None, state or store.CEREMONY_INTRO
        if model_ready:
            return "wake", store.CEREMONY_INTRO
        if stopped:
            return None, state or store.CEREMONY_INTRO
        return "builtin_waiting", store.CEREMONY_CONNECTING

    # state == connecting (the name was saved, the model is loading)
    if model_ready:
        return "wake", store.CEREMONY_CONNECTING
    if stopped:
        return None, store.CEREMONY_CONNECTING
    if just_saved or not tracker.trying_delivered:
        return "builtin_waiting", store.CEREMONY_CONNECTING
    if reminder_due(now):
        return "builtin_reminder", store.CEREMONY_CONNECTING
    return None, store.CEREMONY_CONNECTING


def note(kind: Optional[str], new_state: str) -> None:
    """Apply the side effects of delivering `kind` and moving to new_state.

    Fixed kinds only count once per launch; "trying" (re)starts the reminder
    clock; wake/fallback/normal close the ceremony for good (state done).
    """
    if kind is None:
        return
    tracker = _tracker()
    if kind in ("intro", "builtin_intro"):
        tracker.intro_delivered = True
    elif kind in ("trying", "builtin_waiting"):
        tracker.trying_delivered = True
        tracker.connecting_since = time.monotonic()
    elif kind in ("reminder", "builtin_reminder"):
        tracker.reminder_delivered = True
    elif kind in ("builtin_missing", "builtin_no_model"):
        tracker.missing_delivered = True
    # wake / fallback / normal close the ceremony for good ("done" is
    # written exactly here, when the line that ends it actually ships).
    try:
        store.save_ceremony_state(new_state)
    except OSError:
        # State persistence failed (disk full, permissions): the in-memory
        # tracker still de-duplicates this launch; worst case a future
        # launch re-says a line or two.
        print("[Amadeus] Ceremony: could not persist state %r" % new_state)

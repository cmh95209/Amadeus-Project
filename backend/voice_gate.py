"""Knows when her voice is being made, so background model work can wait.

Her voice engine and her model share one PC. When the voice runs on the
processor (the launcher sets AMADEUS_VOICE_ON_CPU=1: built-in tiers under
12 GB, or no NVIDIA card) it is barely faster than real time (0.77-0.82x on
a Ryzen 7 5800X3D, 2026-10-21). Any model call running beside it - the
English line, the rolling summary, the trust re-check - pushes it past real
time, and her voice stutters (tester report 2026-11-08, 6 GB card: 1-2 s of
speech, then 1.5-6 s of silence, over and over).

So the speech routes report every line here:
  expect(key)   - a line was handed to the browser (it may ask for it soon)
  started(key)  - the browser asked; the engine is making it now
  finished(key) - the line is done (or failed)
and background jobs call wait_quiet() before their model call. A line the
browser never asks for (voice off, tab closed) stops counting after
EXPECT_GRACE_S. Every wait has a cap, so nothing can hang on a lost line.

Only active when the voice is on the processor; with the voice on a
graphics card every call returns at once (nothing changes there).
"""

import os
import threading
import time

EXPECT_GRACE_S = 30.0     # a handed-out line the browser never asked for
QUIET_TIMEOUT_S = 90.0    # longest a background job waits for silence
SETTLE_S = 2.0            # a job started just before her line is registered

_cond = threading.Condition()
_expected = {}            # key -> monotonic deadline
_active = set()


def on_processor() -> bool:
    return os.environ.get("AMADEUS_VOICE_ON_CPU", "").strip() == "1"


def _purge(now: float) -> None:
    for key in [k for k, deadline in _expected.items() if deadline <= now]:
        _expected.pop(key, None)


def expect(key) -> None:
    if not key:
        return
    with _cond:
        _expected[key] = time.monotonic() + EXPECT_GRACE_S
        _cond.notify_all()


def started(key) -> None:
    if not key:
        return
    with _cond:
        _expected.pop(key, None)
        _active.add(key)
        _cond.notify_all()


def finished(key) -> None:
    if not key:
        return
    with _cond:
        _expected.pop(key, None)
        _active.discard(key)
        _cond.notify_all()


def busy() -> bool:
    with _cond:
        _purge(time.monotonic())
        return bool(_expected or _active)


def _pending(key) -> bool:
    _purge(time.monotonic())
    return key in _expected or key in _active


def wait_quiet(timeout: float = QUIET_TIMEOUT_S, settle: float = SETTLE_S) -> bool:
    """Block until no voice line is expected or being made (voice on the
    processor only). `settle` first gives a line that is about to be
    registered time to show up. True = quiet, False = gave up at the cap."""
    if not on_processor():
        return True
    if settle > 0:
        time.sleep(settle)
    deadline = time.monotonic() + timeout
    with _cond:
        while True:
            _purge(time.monotonic())
            if not (_expected or _active):
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _cond.wait(min(left, 1.0))


def wait_for(key, timeout: float = QUIET_TIMEOUT_S) -> bool:
    """Block until this one line is finished (or was never asked for within
    EXPECT_GRACE_S). True = done, False = gave up at the cap."""
    if not key:
        return True
    deadline = time.monotonic() + timeout
    with _cond:
        while _pending(key):
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _cond.wait(min(left, 1.0))
        return True


def _reset_for_tests() -> None:
    with _cond:
        _expected.clear()
        _active.clear()

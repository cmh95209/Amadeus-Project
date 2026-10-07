# -*- coding: utf-8 -*-
"""Force UTF-8 onto the process console, whatever the user's locale says.

Windows encodes redirected console output (the launcher's per-service log
file) with the user's locale - Windows-1252 on an English PC - which
cannot hold the Japanese lines Amadeus prints. The first such print
crashed the whole request with UnicodeEncodeError (2026-10-07, fresh
en-US Windows VM: a 500 on every chat message although the reply had
been generated and stored). main.py calls this at startup so the backend
is immune however it is started; the launcher additionally exports
PYTHONIOENCODING=utf-8 for every service it starts, so this is the
belt-and-braces layer. Safe to call repeatedly; on a real console
(not a redirected pipe) it only pins the encoding and cannot hurt.
"""
import sys


def force_utf8_stdio() -> None:
    """Re-encode stdout/stderr to UTF-8.

    Unencodable characters are REPLACED rather than raised, so even a
    stray byte in some future print can never crash a request again.
    Streams without a reconfigure() (tests, exotic harnesses) are
    silently left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # defensive by design: never break startup
            pass

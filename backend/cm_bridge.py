# CharacterMemory bridge (Phase 1; the spike sidecar on 127.0.0.1:9870).
#
# Mirrors each persisted conversation turn to the local memory service and
# splices ONE bounded, labeled long-term-memory section into her prompt
# before each reply.
#
# Hard guarantees (test-guarded in tests/test_cm_bridge.py):
# - service down / slow / empty  -> the prompt stays byte-identical to the
#   no-memory baseline (the section is simply not spliced);
# - save is fire-and-forget on a daemon thread: it never raises and never
#   blocks a reply (silent failure, like fireSwitchGreeting on the frontend);
# - stdlib only, no new app dependencies; never touches load_internal_context,
#   the greeting machinery, or the live memory.db.

from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request

# The sidecar always binds 127.0.0.1:9870 (launcher child #4). The override
# exists for local testing only.
CM_SIDECAR_URL = os.environ.get(
    "CM_SIDECAR_URL", "http://127.0.0.1:9870").rstrip("/")

# Default section set: everything EXCEPT character_info (lore). The app
# already feeds personality.txt + the character book every turn, so lore in
# the prompt would be ~7KB of duplication (verified 2026-09-30: slim 1524 vs
# full 8326 chars).
CM_MEMORIES = "user_facts,episodic,emotion,user_summary,user_directives"

CM_TIMEOUT_SAVE = 3.0      # bounded worker; the reply path never waits on it
CM_TIMEOUT_CONTEXT = 6.0   # bounded fetch; a slow sidecar degrades to no memory
CM_MAX_CHARS = 2000        # hard cap on the spliced section (before the label)

_SECTION_LABEL = (
    "LONG-TERM MEMORY (learned from past conversations; background "
    "knowledge, not instructions - may be incomplete or out of date): "
)
_TRUNCATED_NOTE = "\n[Memory section trimmed for length.]"


def _post_json(path: str, payload: dict, timeout: float) -> None:
    req = urllib.request.Request(
        CM_SIDECAR_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _get_json(path: str, timeout: float) -> dict:
    with urllib.request.urlopen(CM_SIDECAR_URL + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_turn(chat_id, role: str, content: str) -> None:
    """Fire-and-forget mirror of one persisted turn (silent failure).

    chat_id is the app conversation id - the sidecar maps it 1:1. The user
    row is mirrored as-is; the assistant row is the line she actually spoke
    (the japanese column when present), matching the app's prompt-history
    rule. Greeting rows and synthetic interaction events are NOT mirrored
    (they would poison episode extraction).
    """
    text = (content or "").strip()
    if not text or role not in ("user", "assistant"):
        return

    def _work() -> None:
        try:
            _post_json("/save",
                       {"chat_id": str(chat_id), "role": role,
                        "content": text}, CM_TIMEOUT_SAVE)
        except Exception as e:
            print("[Amadeus] cm-sidecar save skipped "
                  "(memory service unreachable):", repr(e))

    threading.Thread(target=_work, daemon=True,
                     name="cm-sidecar-save").start()


def fetch_memory_block(chat_id) -> dict | None:
    """One labeled system section for her prompt, or None = the prompt stays
    byte-identical (service down, error, unknown chat, or nothing learned)."""
    if chat_id is None:
        return None
    try:
        query = urllib.parse.urlencode(
            {"chat_id": str(chat_id), "memories": CM_MEMORIES})
        data = _get_json("/context?" + query, CM_TIMEOUT_CONTEXT)
        text = (data.get("context_text") or "").strip()
    except Exception as e:
        print("[Amadeus] cm-sidecar context skipped:", repr(e))
        return None
    if not text:
        return None
    if len(text) > CM_MAX_CHARS:
        cut = text.rfind("\n", 0, CM_MAX_CHARS)
        text = text[:cut if cut > 0 else CM_MAX_CHARS] + _TRUNCATED_NOTE
    return {"role": "system", "content": _SECTION_LABEL + text}

import os
from pathlib import Path
from langchain_openai import ChatOpenAI

# =============================================================================
#  Amadeus -> YOUR local model (Unsloth Desktop)
#
#  This file is what makes Amadeus talk to your local model instead of
#  OpenRouter. You do NOT need to understand it - just keep it in place.
#
#  The address of your model server is read from the small text file
#  "llm_server.txt" that sits right next to this file (in the backend/ folder).
#  If that file is missing, we fall back to Unsloth's usual address below.
# =============================================================================

_HERE = Path(__file__).resolve().parent
_SERVER_FILE = _HERE / "llm_server.txt"

# Used only if llm_server.txt does not exist:
DEFAULT_SERVER_URL = "http://localhost:8888/v1"


def _server_url() -> str:
    try:
        if _SERVER_FILE.exists():
            text = _SERVER_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text.rstrip("/")
    except Exception:
        pass
    return DEFAULT_SERVER_URL


_llm = None


def reset_llm():
    """Force Amadeus to rebuild the connection (e.g. after a model/key change)."""
    global _llm
    _llm = None


def get_llm(api_key: str, model: str):
    """Build (once) and return a client that talks to your local Unsloth model."""
    global _llm
    if _llm is None:
        base_url = _server_url()

        # Local servers still expect *some* key. If Amadeus has none stored yet,
        # send a placeholder - you paste the real one into Amadeus Settings.
        resolved_key = (api_key or "").strip() or "unsloth"

        _llm = ChatOpenAI(
            model=model,
            api_key=resolved_key,
            base_url=base_url,
            timeout=300,   # local models can be slow; give them plenty of time
        )
    return _llm

"""Google Vertex AI sign-in (Settings -> Custom Model -> "Google Vertex AI").

Vertex does not take a plain API key. The user pastes the JSON key file of a
Google Cloud "service account"; this module turns it into a short-lived pass
(an access token, valid one hour - Google's maximum) and renews the pass
early. Vertex itself speaks the same OpenAI-style chat format as every other
provider, at a per-project address (base_url below), so the pass is simply
used where an API key would go.

No extra library: the one signature Google asks for (RS256) is done here with
the standard library, so the installers need no change.

The JSON key is PERSONAL like api_key.txt: backend/data/vertex_key.json, on
the user's PC only, never in git, never printed or logged.
"""
import base64
import hashlib
import json
import os
import threading
import time

import requests

import memory

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
VERTEX_HOST = "aiplatform.googleapis.com"
DEFAULT_REGION = "global"
# Google's passes last 3600 s. Renew at 45 minutes so a chat never lands in
# the gap; a failed renewal keeps the old pass (still valid) and tries again.
REFRESH_AFTER = 45 * 60

_lock = threading.Lock()
_token = ""
_token_time = 0.0
_token_for = ""          # private_key_id the cached pass belongs to


def _key_path() -> str:
    return os.path.join(memory.DATA_DIR, "vertex_key.json")


def _region_path() -> str:
    return os.path.join(memory.DATA_DIR, "vertex_region.txt")


# ---------- the saved JSON key ----------

def parse_key(text: str) -> dict:
    """The JSON key as a dict, or ValueError with a plain-language reason."""
    try:
        data = json.loads(text or "")
    except ValueError:
        raise ValueError("That is not a JSON key file (it could not be read as JSON).")
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise ValueError('That JSON is not a service account key (its "type" must be "service_account").')
    for field in ("project_id", "private_key", "client_email"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(f'The JSON key has no "{field}".')
    try:
        _rsa_numbers(data["private_key"])
    except Exception:
        raise ValueError("The private key inside the JSON could not be read.")
    return data


def clean_region(region: str) -> str:
    region = (region or "").strip().lower()
    if not region:
        return DEFAULT_REGION
    if not all(ch.isalnum() or ch == "-" for ch in region):
        raise ValueError("The region may only hold letters, digits and dashes (for example: global).")
    return region


def save_key(text: str, region: str = "", keep_region: bool = False) -> dict:
    """Validate and store the JSON key (+ region; keep_region leaves the
    saved region file as it is - a blank region would clobber the saved
    value). Both are validated BEFORE anything is written (a refused
    region changes nothing). Returns info()."""
    parse_key(text)
    if not keep_region:
        region = clean_region(region)
    os.makedirs(memory.DATA_DIR, exist_ok=True)
    with open(_key_path(), "w", encoding="utf-8") as f:
        f.write(text.strip())
    if not keep_region:
        with open(_region_path(), "w", encoding="utf-8") as f:
            f.write(region)
    forget_token()
    return info()


def save_region(region: str) -> str:
    region = clean_region(region)
    os.makedirs(memory.DATA_DIR, exist_ok=True)
    with open(_region_path(), "w", encoding="utf-8") as f:
        f.write(region)
    return region


def load_key():
    """The saved key as a dict, or None when there is none (or it is broken)."""
    try:
        with open(_key_path(), "r", encoding="utf-8") as f:
            return parse_key(f.read())
    except (OSError, ValueError):
        return None


def load_region() -> str:
    try:
        with open(_region_path(), "r", encoding="utf-8") as f:
            return clean_region(f.read())
    except (OSError, ValueError):
        return DEFAULT_REGION


def info() -> dict:
    """What Settings may show: never the key itself."""
    key = load_key()
    return {
        "configured": key is not None,
        "project": key["project_id"] if key else "",
        "region": load_region(),
        "address": base_url(key["project_id"], load_region()) if key else "",
    }


# ---------- address / model name ----------

def base_url(project: str, region: str = DEFAULT_REGION) -> str:
    region = clean_region(region)
    host = VERTEX_HOST if region == "global" else f"{region}-{VERTEX_HOST}"
    return f"https://{host}/v1/projects/{project}/locations/{region}/endpoints/openapi"


def is_vertex_url(url: str) -> bool:
    return VERTEX_HOST in (url or "")


def model_name(name: str) -> str:
    """Vertex wants "google/<model>"; add the prefix when the user left it out."""
    name = (name or "").strip()
    if name and "/" not in name:
        return "google/" + name
    return name


# ---------- the signature (RS256, standard library only) ----------

def _read_tlv(buf: bytes, pos: int):
    tag = buf[pos]
    length = buf[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(buf[pos:pos + count], "big")
        pos += count
    return tag, buf[pos:pos + length], pos + length


def _items(body: bytes) -> list:
    items, pos = [], 0
    while pos < len(body):
        _tag, value, pos = _read_tlv(body, pos)
        items.append(value)
    return items


def _rsa_numbers(pem: str):
    """(n, e, d) from a PEM private key (PKCS#8, what Google issues, or PKCS#1)."""
    body = "".join(line for line in pem.strip().splitlines() if "-----" not in line)
    der = base64.b64decode(body)
    _tag, seq, _end = _read_tlv(der, 0)
    items = _items(seq)
    if "BEGIN RSA PRIVATE KEY" not in pem:      # PKCS#8 wraps the RSA key
        _tag, seq, _end = _read_tlv(items[2], 0)
        items = _items(seq)
    n, e, d = (int.from_bytes(items[i], "big") for i in (1, 2, 3))
    if n.bit_length() < 1024 or d <= 0:
        raise ValueError("not an RSA private key")
    return n, e, d


_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _sign_rs256(message: bytes, pem: str) -> bytes:
    n, e, d = _rsa_numbers(pem)
    size = (n.bit_length() + 7) // 8
    digest = _SHA256_PREFIX + hashlib.sha256(message).digest()
    padded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    as_int = int.from_bytes(padded, "big")
    signature = pow(as_int, d, n)
    if pow(signature, e, n) != as_int:
        raise ValueError("the private key does not match itself")
    return signature.to_bytes(size, "big")


def _b64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _signed_request(key: dict, now: float) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    if key.get("private_key_id"):
        header["kid"] = key["private_key_id"]
    claims = {"iss": key["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
              "iat": int(now), "exp": int(now) + 3600}
    unsigned = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + b"."
                + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    return (unsigned + b"." + _b64url(_sign_rs256(unsigned, key["private_key"]))).decode()


# ---------- the pass ----------

class VertexSignInError(Exception):
    """Sign-in failed; the message is safe to show (never holds the key)."""


def _fetch_token(key: dict, timeout: float = 15.0) -> str:
    try:
        response = requests.post(TOKEN_URL, timeout=timeout, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": _signed_request(key, time.time()),
        })
    except Exception as exc:
        raise VertexSignInError(f"Could not reach Google to sign in ({type(exc).__name__}).")
    try:
        body = response.json()
    except ValueError:
        body = {}
    token = body.get("access_token") if isinstance(body, dict) else None
    if response.status_code != 200 or not isinstance(token, str) or not token:
        reason = ""
        if isinstance(body, dict):
            reason = str(body.get("error_description") or body.get("error") or "")[:200]
        raise VertexSignInError(
            f"Google refused the JSON key (HTTP {response.status_code}"
            + (f": {reason}" if reason else "") + ").")
    return token


def forget_token() -> None:
    global _token, _token_time, _token_for
    with _lock:
        _token, _token_time, _token_for = "", 0.0, ""
    # A new (or no) key: the endpoint list and model catalogues cached for
    # the previous project no longer belong to anyone.
    with _cache_lock:
        _location_cache.clear()
        _model_cache.clear()


def token_age() -> float:
    """Seconds since the cached pass was issued (a huge number when none)."""
    return time.time() - _token_time if _token else float("inf")


def token(force: bool = False) -> str:
    """A valid pass for the saved key. Renewed when older than REFRESH_AFTER;
    a failed renewal returns the old pass while it is still inside its hour.
    Raises VertexSignInError when there is no usable pass."""
    global _token, _token_time, _token_for
    key = load_key()
    if key is None:
        raise VertexSignInError("No Google Vertex JSON key is saved yet.")
    key_id = key.get("private_key_id") or key["client_email"]
    with _lock:
        age = time.time() - _token_time
        if _token and _token_for == key_id and not force and age < REFRESH_AFTER:
            return _token
        try:
            fresh = _fetch_token(key)
        except VertexSignInError:
            if _token and _token_for == key_id and age < 3600 - 120:
                return _token
            raise
        _token, _token_time, _token_for = fresh, time.time(), key_id
        return _token


def check_ready(timeout: float = 15.0) -> dict:
    """Sign in and ask Vertex one free question (no model is called):
    {"ok": bool, "error": str or None}. Tells the two usual setup mistakes
    apart - a refused JSON key, and a project where the Vertex AI API was
    never switched on."""
    key = load_key()
    if key is None:
        return {"ok": False, "error": "No Google Vertex JSON key is saved yet."}
    try:
        pass_ = token()
    except VertexSignInError as exc:
        return {"ok": False, "error": str(exc)}
    region = load_region()
    host = VERTEX_HOST if region == "global" else f"{region}-{VERTEX_HOST}"
    url = f"https://{host}/v1/projects/{key['project_id']}/locations/{region}/endpoints"
    try:
        response = requests.get(url, headers={"Authorization": "Bearer " + pass_}, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"Could not reach Google Vertex ({type(exc).__name__})."}
    if response.status_code == 200:
        return {"ok": True, "error": None}
    text = (response.text or "")[:600].lower()
    if response.status_code == 403 and ("disabled" in text or "has not been used" in text):
        return {"ok": False, "error": "The Vertex AI API is not switched on in this Google Cloud "
                                      "project yet. Enable it in the Google Cloud console, wait a "
                                      "few minutes, then save the JSON key again."}
    if response.status_code == 403:
        return {"ok": False, "error": "Google signed the key in, but it has no permission to use "
                                      "Vertex AI (the service account needs the Vertex AI User role)."}
    if response.status_code == 404:
        return {"ok": False, "error": f'Google does not know the region "{region}".'}
    return {"ok": False, "error": f"Google Vertex answered HTTP {response.status_code}."}


# ---------- the endpoints and the model catalogues (measured 2026-11-12) --

# The catalogue of a location OVER-PROMISES: a listed model can still
# answer 404 when it is called (measured: asia-southeast1 lists
# gemini-2.5-flash, the call says not found). The list is a help for
# choosing - the Settings "Test" button stays the proof.

# Catalogue models that are NOT chat (their last name part carries one of
# these words: speech output, pictures, embeddings, transcription,
# translation, live audio, ...).
_NON_CHAT_MODEL_WORDS = (
    "tts", "image", "embedding", "transcribe", "translate",
    "live", "audio", "banana", "computer-use", "omni",
)

# The endpoint list (per project) is cached for one hour, a model catalogue
# (per project + endpoint) for ten minutes; both are cleared by
# forget_token - that is, whenever the saved key changes.
LOCATION_CACHE_TTL = 60 * 60
MODEL_CACHE_TTL = 10 * 60
_location_cache = {}   # project -> (valid until, [ids])
_model_cache = {}      # (project, region) -> (valid until, [names])
_cache_lock = threading.Lock()


def _paged_items(url: str, headers: dict, item_key: str,
                 timeout: float = 15.0, max_pages: int = 5) -> list:
    """The item dicts of one of Google's paged JSON answers (up to
    max_pages pages, following nextPageToken; measured today: one page
    each). Raises on any problem - the message holds only the HTTP status
    or the error type name, never the pass or the key."""
    items = []
    page_token = None
    for _ in range(max_pages):
        params = {"pageSize": 200}
        if page_token:
            params["nextPageToken"] = page_token
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
        except Exception as exc:
            raise ValueError(f"could not be reached ({type(exc).__name__})")
        if response.status_code != 200:
            raise ValueError(f"answered HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise ValueError("did not answer JSON")
        if not isinstance(data, dict):
            raise ValueError("did not answer JSON")
        batch = data.get(item_key)
        if isinstance(batch, list):
            items.extend(item for item in batch if isinstance(item, dict))
        page_token = data.get("nextPageToken")
        if not isinstance(page_token, str) or not page_token:
            break
    return items


def list_locations(timeout: float = 15.0) -> list:
    """The endpoint (location) ids the user's project may choose from:
    "global" first, then the ids that contain a dash sorted A-Z - the
    regions ("us-central1", "asia-southeast1", ...). The dashless
    multi-regions ("us", "eu", "ca", "in", "gb", "au", "jp") are left
    out: they REFUSE chat calls (HTTP 400, measured). Raises
    VertexSignInError when the sign-in to Google fails; any other problem
    (no key saved, unreachable, a strange reply) -> ["global"] (the
    endpoint that always exists)."""
    key = load_key()
    if key is None:
        return [DEFAULT_REGION]
    project = key["project_id"]
    with _cache_lock:
        cached = _location_cache.get(project)
    if cached and cached[0] > time.time():
        return list(cached[1])
    try:
        pass_ = token()
        items = _paged_items(
            f"https://{VERTEX_HOST}/v1/projects/{project}/locations",
            {"Authorization": "Bearer " + pass_}, "locations", timeout)
    except VertexSignInError:
        raise
    except Exception:
        return [DEFAULT_REGION]
    ids = {item["locationId"] for item in items
           if isinstance(item.get("locationId"), str)}
    result = [DEFAULT_REGION] + sorted(
        region for region in ids if region != DEFAULT_REGION and "-" in region)
    if ids:
        with _cache_lock:
            _location_cache[project] = (time.time() + LOCATION_CACHE_TTL, result)
    return result


def _model_sort_key(name: str):
    """Newest first: the version number in the name descending (3.8 > 3.5
    > 3.1 > 2.5; a plain "4" > "3.8"), then A-Z. Names without a number
    sort after the numbered ones (A-Z among themselves)."""
    head = name[len("gemini-"):].split("-", 1)[0] if name.startswith("gemini-") else ""
    version = [int(piece) for piece in head.split(".") if piece.isdigit()]
    if not version:
        return (1, name)
    # "3" is 3.0: pad, so a plain "3" sorts after "3.8", not before it.
    version = (version + [0, 0])[:2]
    return (0, tuple(-part for part in version), name)


def list_models(region: str = None, timeout: float = 15.0) -> list:
    """The chat-capable Gemini model names of one location's catalogue
    (the last path part of each "publishers/google/models/..." name, kept
    when it starts with "gemini-" and carries none of the non-chat words
    in _NON_CHAT_MODEL_WORDS), sorted newest first. region None = the
    saved endpoint. Any problem (no key, sign-in failure, unreachable, a
    strange reply) -> [] - the caller falls back to the fixed list. The
    list over-promises (see the note above): a listed model may still
    answer 404 when called."""
    key = load_key()
    if key is None:
        return []
    project = key["project_id"]
    try:
        region = clean_region(region) if region is not None else load_region()
    except ValueError:
        return []
    cache_key = (project, region)
    with _cache_lock:
        cached = _model_cache.get(cache_key)
    if cached and cached[0] > time.time():
        return list(cached[1])
    try:
        pass_ = token()
    except VertexSignInError:
        return []
    host = VERTEX_HOST if region == DEFAULT_REGION else f"{region}-{VERTEX_HOST}"
    try:
        items = _paged_items(
            f"https://{host}/v1beta1/publishers/google/models",
            {"Authorization": "Bearer " + pass_}, "publisherModels", timeout)
    except Exception:
        return []
    names = set()
    for item in items:
        name = item.get("name")
        if not isinstance(name, str):
            continue
        short = name.rsplit("/", 1)[-1]
        if not short.startswith("gemini-"):
            continue
        if any(word in short for word in _NON_CHAT_MODEL_WORDS):
            continue
        names.add(short)
    result = sorted(names, key=_model_sort_key)
    with _cache_lock:
        _model_cache[cache_key] = (time.time() + MODEL_CACHE_TTL, result)
    return result

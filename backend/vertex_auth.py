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
    saved region file as it is - the Settings page no longer asks for the
    region, and a blank one would clobber the saved value). Returns info()."""
    parse_key(text)
    os.makedirs(memory.DATA_DIR, exist_ok=True)
    with open(_key_path(), "w", encoding="utf-8") as f:
        f.write(text.strip())
    if not keep_region:
        region = clean_region(region)
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

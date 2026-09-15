# -*- coding: utf-8 -*-
"""Local web page reader for Amadeus web search (standard library only).

Turns a URL into clean, readable text so she can read the actual content of a
search result instead of just its one-line snippet. Everything runs on this
machine - no third-party dependency, no external service, no API key.

Deliberate properties:
  * BOUNDED  - a hard per-fetch timeout and a character cap tied to the model's
               context window (char_budget), so a slow or huge page can never
               stall a reply or overflow the prompt.
  * SAFE     - an SSRF guard refuses private / loopback / link-local / CGNAT
               targets, so a fetched page can never be turned into a probe of
               the local network.
  * GRACEFUL - bot-walls and empty pages are detected and reported as a status,
               so the caller can fall back to the next result.

fetch_page_text() returns (status, text) where status is one of:
  'ok'      readable content (possibly truncated to the char budget)
  'blocked' the site served an anti-automation / bot wall
  'empty'   the page had no usable readable text
  'unsafe'  the target was refused by the SSRF guard
  'error'   network / decode / parse failure
"""
from __future__ import annotations

import gzip
import io
import ipaddress
import random
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

# --- limits -----------------------------------------------------------------
MAX_PAGE_CHARS = 16000          # ceiling for one fetched page's text
PAGE_CONTEXT_SHARE = 0.35       # a page may claim at most ~35% of the window
MIN_PAGE_CHARS = 2000           # below this a page is "too thin to answer from"
MAX_DOWNLOAD_BYTES = 512 * 1024 # raw download cap
DEFAULT_TIMEOUT = 15            # seconds; the caller usually passes a smaller one

_SSL_CTX = ssl.create_default_context()
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)


def char_budget(context_tokens):
    """How many characters one fetched page may take.

    16,000 chars (~4,000 tokens) is a ceiling for large windows; on a smaller
    window it shrinks to ~PAGE_CONTEXT_SHARE of that window (4 chars/token), so
    a page can only ever be a fraction of what she has to work with. Never
    below MIN_PAGE_CHARS. `context_tokens` is the user's context budget.
    """
    if not context_tokens:
        return MAX_PAGE_CHARS
    return max(MIN_PAGE_CHARS,
               min(MAX_PAGE_CHARS, int(context_tokens * 4 * PAGE_CONTEXT_SHARE)))


# --- SSRF guard -------------------------------------------------------------
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # RFC6598 shared/carrier space


def _is_private_ip(ip_str):
    """True for loopback / private / link-local / reserved / CGNAT addresses."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip in _CGNAT:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _host_is_blocked(host):
    """Whether a URL host points at the local machine / network (SSRF)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in ("localhost", "localhost.localdomain") or host.endswith(
            (".local", ".internal", ".lan")):
        return True
    bare = host.strip("[]")  # strip IPv6 brackets
    try:
        ipaddress.ip_address(bare)  # it is an IP literal
        return _is_private_ip(bare)
    except ValueError:
        pass
    # A name: resolve and block if ANY answer is private (DNS-rebinding guard).
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError, UnicodeError):
        return False  # cannot resolve -> the fetch will fail naturally
    for _fam, _ty, _proto, _canon, sockaddr in infos:
        if _is_private_ip(sockaddr[0]):
            return True
    return False


def is_safe_public_url(url):
    """(allowed, reason). Only http/https to public hosts are allowed."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except Exception:
        return False, "unsafe"
    if parts.scheme not in ("http", "https"):
        return False, "unsafe"
    if _host_is_blocked(parts.hostname or ""):
        return False, "unsafe"
    return True, ""


# --- HTML -> text -----------------------------------------------------------
_VOID = {"br", "img", "input", "hr", "meta", "link", "area", "base",
         "col", "embed", "source", "track", "wbr"}


class _Extractor(HTMLParser):
    # A tag is "hidden" while any of its OPEN ANCESTORS is in SKIP or STRIP,
    # which is robust to real-world HTML (stray end-tags, self-closing and
    # void elements, nested markup) - unlike manual open/close counters, which
    # desync and silently drop everything after the first unmatched tag.
    SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe"}
    STRIP = {"nav", "footer", "header", "aside", "form", "button", "select"}
    HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "td", "th", "table",
             "ul", "ol", "blockquote", "pre", "figure", "main"}
    CONTAINER = {"main", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._open = []       # stack of currently-open (non-void) tags
        self._heading = 0
        self._list = 0
        self._cur = []
        self._all = []
        self._main = []

    def _hidden(self):
        return any(t in self.SKIP or t in self.STRIP for t in self._open)

    def _in_main(self):
        return any(t in self.CONTAINER for t in self._open)

    def _flush(self):
        if self._cur:
            piece = re.sub(r"\s+", " ", "".join(self._cur)).strip()
            self._cur = []
            if not piece:
                return
            if self._heading:
                piece = "#" * min(self._heading, 4) + " " + piece
            elif self._list:
                piece = "  " * max(self._list - 1, 0) + "- " + piece
            self._all.append(piece)
            if self._in_main():
                self._main.append(piece)

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self._open.append(tag)
            if len(self._open) > 2000:      # safety against pathological input
                self._open = self._open[-2000:]
        if tag in self.HEADING:
            self._flush(); self._heading = self.HEADING[tag]
        elif tag in ("ul", "ol"):
            self._flush(); self._list += 1
        elif tag in self.BLOCK or tag == "li":
            self._flush()

    def handle_endtag(self, tag):
        if tag in self._open:
            while self._open and self._open[-1] != tag:
                self._open.pop()
            if self._open:
                self._open.pop()
        if tag in self.HEADING:
            self._flush(); self._heading = 0
        elif tag in ("ul", "ol"):
            self._list = max(0, self._list - 1); self._flush()
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._hidden():
            return
        self._cur.append(data)

    def text(self):
        self._flush()
        main = " ".join(self._main)
        pieces = self._main if len(main.strip()) >= 200 else self._all
        out = "\n".join(pieces)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()


_BOILERPLATE_RE = re.compile(
    r"(?im)^\s*(cookie|cookies|accept all|accept and continue|reject all|"
    r"skip to (main )?content|all rights reserved|privacy (policy|notice)|"
    r"terms of (service|use)|advertisement|advertisements)\s*:?\s*$"
)


def _strip_boilerplate(text):
    kept = [ln for ln in text.splitlines() if not _BOILERPLATE_RE.match(ln)]
    return "\n".join(kept).strip()


def html_to_text(html, main_content=True):
    """Convert an HTML document to clean, readable text (stdlib only)."""
    if not html:
        return ""
    try:
        ex = _Extractor()
        ex.feed(html)
        ex.close()
        text = ex.text()
    except Exception:
        # A malformed page degrades to a rough tag-strip, never raises.
        text = re.sub(r"(?is)<(script|style|noscript|head).*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
    if main_content:
        text = _strip_boilerplate(text)
    return text.strip()


# --- bot-wall detection -----------------------------------------------------
_BOT_WALL_MARKERS = (
    "just a moment", "checking your browser", "verify you are human",
    "verifying you are human", "are you a robot", "unusual traffic",
    "access to this page has been denied", "access denied",
    "you have been blocked", "request blocked",
    "enable javascript and cookies", "pardon our interruption",
    "attention required", "cf-browser-verification", "cf-challenge",
    "challenge-platform", "cloudflare ray id", "error 1015", "captcha",
    "403 forbidden",
)


def looks_bot_walled(text):
    head = (text or "").lower()[:4000]
    return any(m in head for m in _BOT_WALL_MARKERS)


# --- download ---------------------------------------------------------------
def _charset_from(ctype):
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    return m.group(1) if m else ""


def _decode(raw, charset):
    for enc in dict.fromkeys([charset, "utf-8", "latin-1"]):
        if not enc:
            continue
        try:
            return raw.decode(enc).lstrip("\ufeff")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _download(url, timeout):
    """GET a URL. Returns (err, body_text, content_type); err is None on success."""
    req = urllib.request.Request(url, headers={
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            # Re-check the FINAL url (guards against open-redirect SSRF).
            ok, _why = is_safe_public_url(resp.geturl())
            if not ok:
                return "unsafe", "", ""
            raw = resp.read(MAX_DOWNLOAD_BYTES + 1)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if len(raw) > MAX_DOWNLOAD_BYTES:
                raw = raw[:MAX_DOWNLOAD_BYTES]
            if (resp.headers.get("Content-Encoding", "").lower() == "gzip"
                    or raw[:2] == b"\x1f\x8b"):
                try:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                except Exception:
                    pass
            return None, _decode(raw, _charset_from(ctype)), ctype
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_DOWNLOAD_BYTES).decode("utf-8", "replace")
        except Exception:
            body = ""
        return f"http_{e.code}", body, ""
    except Exception as e:
        return repr(e), "", ""


# --- public entry -----------------------------------------------------------
def fetch_page_text(url, max_chars=None, timeout=DEFAULT_TIMEOUT):
    """Fetch a URL and return (status, text). See module docstring for statuses."""
    if max_chars is None:
        max_chars = MAX_PAGE_CHARS
    allowed, _why = is_safe_public_url(url)
    if not allowed:
        return "unsafe", "(this address is on the local network and was not fetched)"
    err, body, ctype = _download(url, timeout)
    if body is None:
        return "error", ""
    stripped = body.lstrip()[:200].lower()
    is_html = ("html" in ctype) or stripped.startswith("<!doctype html") \
        or stripped.startswith("<html")
    text = html_to_text(body) if is_html else re.sub(r"\s+", " ", body).strip()
    if looks_bot_walled(text):
        return "blocked", "(this page blocked automated access)"
    if err is not None:
        return "error", ""
    if len(text) < MIN_PAGE_CHARS:
        return "empty", "(this page had no usable readable text)"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... (truncated, {len(text)} chars total)"
    return "ok", text

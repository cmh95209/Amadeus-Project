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
MAX_REDIRECTS = 5               # hops followed for one address
MAX_URL_CHARS = 2000            # a longer pasted link is not opened

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


# --- links the user pastes (2026-10-26) ----------------------------------------
# A link that carries a login (name:password@host) or a key / token / password
# in its query is never opened: opening it would hand that secret to the site
# (and to the log). Very long addresses are refused as well.
_SECRET_PARAM_RE = re.compile(
    r"^(?:access_?token|id_?token|refresh_?token|token|auth|authorization|api_?key|apikey|"
    r"key|password|passwd|pwd|pass|secret|client_?secret|session|session_?id|sid|sig|"
    r"signature|x-amz-signature|x-amz-credential|x-goog-signature|otp|code_verifier)$",
    re.IGNORECASE)


def risky_link(url) -> str:
    """Why a pasted link must not be opened ("" when it may be): "is too long",
    "has a password or key in it", or "is not a web address"."""
    url = str(url or "").strip()
    if len(url) > MAX_URL_CHARS:
        return "is too long"
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except Exception:
        return "is not a web address"
    if parts.scheme not in ("http", "https") or not host:
        return "is not a web address"
    if parts.username or parts.password:
        return "has a password or key in it"
    for name, _value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        if _SECRET_PARAM_RE.match(name.strip()):
            return "has a password or key in it"
    return ""


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


class _UnsafeRedirect(Exception):
    """A redirect pointed at a private / local address."""


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect hop is checked BEFORE it is followed (2026-10-26): a
    public page that redirects to the local network is refused without the
    local address ever being contacted. At most MAX_REDIRECTS hops."""
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, _why = is_safe_public_url(newurl)
        if not ok:
            raise _UnsafeRedirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(
    _SafeRedirects, urllib.request.HTTPSHandler(context=_SSL_CTX))


def _download(url, timeout, meta=None):
    """GET a URL. Returns (err, body_text, content_type); err is None on success.
    `meta` (a dict), when given, receives "final_url" - where the redirects
    ended."""
    req = urllib.request.Request(url, headers={
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    })
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            # Re-check the FINAL url too (the hops were checked on the way).
            ok, _why = is_safe_public_url(resp.geturl())
            if not ok:
                return "unsafe", "", ""
            if meta is not None:
                meta["final_url"] = resp.geturl()
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
    except _UnsafeRedirect:
        return "unsafe", "", ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_DOWNLOAD_BYTES).decode("utf-8", "replace")
        except Exception:
            body = ""
        return f"http_{e.code}", body, ""
    except Exception as e:
        return repr(e), "", ""


# --- public entry -----------------------------------------------------------
# --- page details: title + dates (2026-10-25) ---------------------------------
# Old news must not read as new: a page's own published / updated date (meta
# tags, JSON-LD, <time>) is read here, plus a "Month YYYY" in its title
# ("Banner Schedule (October 2026)"), which many live pages carry instead.
_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")
_META_DATE_KEYS = {
    "article:published_time": "published", "datepublished": "published",
    "publishdate": "published", "pubdate": "published", "date": "published",
    "dc.date": "published", "dc.date.issued": "published",
    "sailthru.date": "published", "parsely-pub-date": "published",
    "article:modified_time": "updated", "og:updated_time": "updated",
    "datemodified": "updated", "last-modified": "updated", "lastmod": "updated",
}
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _iso_day(value):
    """'2026-10-24T09:00:00+09:00' -> '2026-10-24'; '' when it is no real date."""
    m = _ISO_DATE_RE.search(str(value or ""))
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    if not (1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


class _MetaReader(HTMLParser):
    """Collects <title>, date meta tags, JSON-LD blocks and <time datetime>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.dates = {"published": "", "updated": ""}
        self.times = []
        self.jsonld = []
        self._in = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title" and not self.title:
            self._in = "title"
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop")
                   or a.get("http-equiv") or "").lower()
            if key == "og:title" and not self.og_title:
                self.og_title = a.get("content", "").strip()
            kind = _META_DATE_KEYS.get(key)
            day = _iso_day(a.get("content"))
            if kind and day and not self.dates[kind]:
                self.dates[kind] = day
        elif tag == "time":
            day = _iso_day(a.get("datetime"))
            if day:
                self.times.append(day)
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in = "jsonld"
            self.jsonld.append("")

    def handle_endtag(self, tag):
        if tag in ("title", "script"):
            self._in = None

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in == "jsonld":
            self.jsonld[-1] += data


def _title_month(title):
    """'Banner Schedule (October 2026)' / '2026年10月' -> '2026-10', else ''."""
    t = (title or "").lower()
    m = re.search(r"\b(" + "|".join(_MONTH_NAMES) + r")\s+(\d{4})\b", t)
    if m:
        return f"{int(m.group(2)):04d}-{_MONTH_NAMES.index(m.group(1)) + 1:02d}"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", title or "")
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    return ""


def page_details(html):
    """{"title", "published", "updated", "title_month"} read from a page's HTML
    (each "" when the page does not say). Never raises."""
    out = {"title": "", "published": "", "updated": "", "title_month": ""}
    if not html:
        return out
    r = _MetaReader()
    try:
        r.feed(html[:400000])
        r.close()
    except Exception:
        pass
    out["title"] = re.sub(r"\s+", " ", r.og_title or r.title or "").strip()[:300]
    out["published"], out["updated"] = r.dates["published"], r.dates["updated"]
    for block in r.jsonld:
        for kind, key in (("published", "datePublished"), ("updated", "dateModified")):
            if not out[kind]:
                m = re.search(r'"' + key + r'"\s*:\s*"([^"]+)"', block)
                if m:
                    out[kind] = _iso_day(m.group(1))
    if not out["published"] and r.times:
        out["published"] = min(r.times)
    out["title_month"] = _title_month(out["title"])
    return out


def page_date(details):
    """The date a page is best known by: updated, else published, else the
    title's month ("2026-10"); "" when unknown."""
    d = details or {}
    return d.get("updated") or d.get("published") or d.get("title_month") or ""


# --- public entry -----------------------------------------------------------
# Content types read as text; anything else (PDF, images, video, downloads)
# is "unsupported" - never decoded into garbage text.
_TEXT_TYPES = ("text/", "html", "xml", "json")

# Why a page could not be read, in words she can say (fetch_page "reason").
_HTTP_REASONS = {401: "needs a login", 402: "is behind a paywall",
                 403: "blocked automated access", 404: "was not found",
                 410: "was not found", 429: "is busy right now"}


def _reason(err) -> str:
    m = re.match(r"http_(\d+)$", str(err or ""))
    if m:
        code = int(m.group(1))
        return _HTTP_REASONS.get(code, "had a server error" if code >= 500 else "could not be opened")
    if re.search(r"(?i)timed? ?out", str(err or "")):
        return "took too long"
    return "could not be opened"


def fetch_page(url, timeout=DEFAULT_TIMEOUT, min_chars=None):
    """Fetch a URL -> {"status", "text", "title", "published", "updated",
    "title_month", "reason", "final_url"}. status as in fetch_page_text, plus
    "unsupported" (not a web page: PDF, image, video, a download); "text" is
    the whole readable text (not cut to a budget - the caller picks what it
    needs); "reason" says in words why a page could not be read; "final_url"
    is where the redirects ended. `min_chars` (default MIN_PAGE_CHARS): below
    it a page counts as "empty" - a link the user gave is read when shorter."""
    out = {"status": "error", "text": "", "title": "", "published": "",
           "updated": "", "title_month": "", "reason": "", "final_url": url}
    min_chars = MIN_PAGE_CHARS if min_chars is None else min_chars
    allowed, _why = is_safe_public_url(url)
    if not allowed:
        out["status"] = "unsafe"
        out["reason"] = "is a private or local address"
        return out
    meta = {}
    err, body, ctype = _download(url, timeout, meta)
    out["final_url"] = meta.get("final_url") or url
    if err == "unsafe":
        out["status"] = "unsafe"
        out["reason"] = "redirects to a private or local address"
        return out
    if body is None:
        out["reason"] = _reason(err)
        return out
    if err is None and ctype and not any(t in ctype for t in _TEXT_TYPES):
        out["status"] = "unsupported"
        out["reason"] = "is not a web page (" + ctype.split(";")[0].strip() + ")"
        return out
    stripped = body.lstrip()[:200].lower()
    is_html = ("html" in ctype) or stripped.startswith("<!doctype html") \
        or stripped.startswith("<html")
    text = html_to_text(body) if is_html else re.sub(r"\s+", " ", body).strip()
    if is_html:
        out.update(page_details(body))
    if looks_bot_walled(text):
        out["status"] = "blocked"
        out["reason"] = "blocked automated access"
        return out
    if err is not None:
        out["reason"] = _reason(err)
        return out
    if len(text) < min_chars:
        out["status"] = "empty"
        out["reason"] = "had no readable text (it may need scripts or a login)"
        return out
    out["status"] = "ok"
    out["text"] = text
    return out


def fetch_page_text(url, max_chars=None, timeout=DEFAULT_TIMEOUT):
    """Fetch a URL and return (status, text). See module docstring for statuses."""
    if max_chars is None:
        max_chars = MAX_PAGE_CHARS
    page = fetch_page(url, timeout=timeout)
    status, text = page["status"], page["text"]
    if status == "unsafe":
        return "unsafe", "(this address is on the local network and was not fetched)"
    if status == "blocked":
        return "blocked", "(this page blocked automated access)"
    if status == "empty":
        return "empty", "(this page had no usable readable text)"
    if status != "ok":
        return "error", ""
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... (truncated, {len(text)} chars total)"
    return "ok", text

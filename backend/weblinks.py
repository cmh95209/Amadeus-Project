# -*- coding: utf-8 -*-
"""Links the user pastes into the chat: find them, open them (2026-10-26).

When the user's latest message holds a link and web access is ON, the app
itself opens it - never a link she makes up, and never a link found ON a page
(no sub-links, no crawling): the strict form of the "only fetch what the
user gave" rule. What travels on to her reply is the reader's notes, never
the raw page (chat.py / webreader.py).

How one link is opened (fetch_link):
  1. A link that carries a password / key / token, or is very long, is
     refused (webfetch.risky_link).
  2. An X / Twitter post (x.com/<user>/status/<id>) is read through
     FxTwitter's free public API - X itself shows robots nothing, and its
     own preview service is paid. Other X pages (profiles, searches) need a
     login: said honestly.
  3. Anything else: downloaded on this PC (webfetch.fetch_page - every
     redirect hop checked first, PDFs / images / downloads refused).
  4. A page that blocks robots or shows no text without its scripts: one
     more try through Keenable's free page reader (the link is sent to
     Keenable - the same service that already runs her searches).
Every failure carries a reason in words ("needs a login") she can say.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import webfetch
import websearch

MAX_LINKS = 3              # links opened per message
MIN_LINK_CHARS = 200       # a page the user gave is read even when short
LINK_TIMEOUT = 8           # seconds, one download (the reply's budget caps it)
FX_URL = "https://api.fxtwitter.com/{user}/status/{id}"
KEENABLE_FETCH_URL = "https://api.keenable.ai/v1/fetch/public"
KEENABLE_FETCH_CHARS = 40000
_SSL_CTX = ssl.create_default_context()

# --- finding links ---------------------------------------------------------------
# http(s):// or www. up to a space or a bracket / punctuation mark that cannot
# be part of an address (Japanese brackets and full-width marks included).
_LINK_RE = re.compile(r"(?i)(?:\bhttps?://|\bwww\.)[^\s<>\"'`「」『』【】（）〈〉《》、。，！？・　]+")
_TRAILING = ".,;:!?'\"*_~"


def _trim(link: str) -> str:
    """Remove punctuation stuck to the end of a link ("see https://x.com/a.")
    and a closing bracket that is not part of it (a link inside "(...)"),
    while keeping one that is ("wiki/Foo_(bar)")."""
    while link:
        if link[-1] in _TRAILING:
            link = link[:-1]
        elif link[-1] in ")]}" and link.count({")": "(", "]": "[", "}": "{"}[link[-1]]) < link.count(link[-1]):
            link = link[:-1]
        else:
            break
    return link


def _key(url: str) -> str:
    return re.sub(r"#.*$", "", re.sub(r"(?i)^https?://(www\.)?", "", url)).rstrip("/").lower()


def find_links(text, max_links=MAX_LINKS):
    """The links in a message, in order, each once -> (links, how many more
    were left out). "www.x.com" gets https:// in front."""
    links, seen = [], set()
    for m in _LINK_RE.finditer(str(text or "")):
        link = _trim(m.group(0))
        if not link:
            continue
        if not re.match(r"(?i)https?://", link):
            link = "https://" + link
        if not re.match(r"(?i)https?://[^/\s]+\.[^/\s]+", link):
            continue                      # "https://localhost" style, no dot
        k = _key(link)
        if k in seen:
            continue
        seen.add(k)
        links.append(link)
    return links[:max_links], max(0, len(links) - max_links)


def has_links(text) -> bool:
    return bool(find_links(text, 1)[0])


# --- X / Twitter posts (FxTwitter) ------------------------------------------------
_X_HOSTS = r"(?:www\.|mobile\.|m\.)?(?:x|twitter|fxtwitter|vxtwitter|fixupx|fixvx)\.com"
_X_POST_RE = re.compile(r"(?i)^https?://" + _X_HOSTS + r"/(\w{1,30})/status(?:es)?/(\d{1,25})")
_X_ANY_RE = re.compile(r"(?i)^https?://" + _X_HOSTS + r"(?:/|$)")


def x_post(url):
    """(user, post id) for an X / Twitter post link, else None."""
    m = _X_POST_RE.match(str(url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def is_x_link(url) -> bool:
    return bool(_X_ANY_RE.match(str(url or "").strip()))


def _get_json(url, timeout, headers=None):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                "User-Agent": "Amadeus/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read(2 * 1024 * 1024).decode("utf-8", "replace"))


def _post_day(post) -> str:
    stamp = post.get("created_timestamp")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        return datetime.strptime(str(post.get("created_at") or ""),
                                 "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _post_text(post, label="Post") -> str:
    author = post.get("author") or {}
    who = f"{author.get('name') or ''} (@{author.get('screen_name') or '?'})".strip()
    lines = [f"{label} by {who}" + (f", posted {_post_day(post)}" if _post_day(post) else "") + ":",
             str(post.get("text") or "").strip() or "(no text)"]
    media = post.get("media") or {}
    photos = len(media.get("photos") or []) if isinstance(media, dict) else 0
    videos = len(media.get("videos") or []) if isinstance(media, dict) else 0
    if photos or videos:
        lines.append(f"(with {photos} photo(s) and {videos} video(s) - not described here)")
    counts = [f"{post[k]} {name}" for k, name in (("likes", "likes"), ("retweets", "reposts"),
                                                   ("replies", "replies"), ("views", "views"))
              if isinstance(post.get(k), int)]
    if counts:
        lines.append("(" + ", ".join(counts) + ")")
    if isinstance(post.get("replying_to"), str) and post["replying_to"]:
        lines.insert(1, f"(a reply to @{post['replying_to']})")
    return "\n".join(lines)


def fetch_x_post(url, timeout=LINK_TIMEOUT) -> dict:
    """An X post through FxTwitter -> the same dict as webfetch.fetch_page."""
    out = {"status": "error", "text": "", "title": "", "published": "", "updated": "",
           "title_month": "", "reason": "", "final_url": url}
    user, post_id = x_post(url)
    try:
        data = _get_json(FX_URL.format(user=urllib.parse.quote(user), id=post_id), timeout)
    except urllib.error.HTTPError as e:
        out["reason"] = ("is an X post that is private, deleted or not found" if e.code in (401, 403, 404)
                         else "is an X post that could not be loaded right now")
        return out
    except Exception:
        out["reason"] = "is an X post that could not be loaded right now"
        return out
    post = data.get("tweet") if isinstance(data, dict) else None
    if not isinstance(post, dict) or not (post.get("text") or post.get("media")):
        out["reason"] = "is an X post that is private, deleted or not found"
        return out
    text = _post_text(post)
    if isinstance(post.get("quote"), dict):
        text += "\n\n" + _post_text(post["quote"], "Quoting a post")
    author = post.get("author") or {}
    out.update(status="ok", text=text, published=_post_day(post),
               title=f"Post by {author.get('name') or author.get('screen_name') or 'someone'} on X")
    return out


# --- Keenable's page reader (backup) -------------------------------------------
def _markdown_to_text(md: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md or "")           # images
    t = re.sub(r"\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)", r"\1", t)  # [text](link) -> text
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def keenable_page(url, timeout=LINK_TIMEOUT) -> dict:
    """A page through Keenable's free reader -> the same dict as
    webfetch.fetch_page. Skipped while Keenable is cooling down after its
    hourly limit (shared with her searches)."""
    out = {"status": "error", "text": "", "title": "", "published": "", "updated": "",
           "title_month": "", "reason": "", "final_url": url}
    if websearch.keenable_cooling_down():
        return out
    query = urllib.parse.urlencode({"url": url, "live": "true", "max_chars": KEENABLE_FETCH_CHARS})
    try:
        data = _get_json(KEENABLE_FETCH_URL + "?" + query, timeout,
                         {"X-Keenable-Title": websearch.KEENABLE_APP})
    except urllib.error.HTTPError as e:
        if e.code in (402, 429):
            websearch._keenable_cool_down(websearch.KEENABLE_QUOTA_COOLDOWN)
        return out
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    text = _markdown_to_text(str(data.get("content") or ""))
    if len(text) < MIN_LINK_CHARS or webfetch.looks_bot_walled(text):
        return out
    published = data.get("published_at")
    if isinstance(published, (int, float)) and published > 0:
        published = datetime.fromtimestamp(published, tz=timezone.utc).strftime("%Y-%m-%d")
    elif isinstance(published, str) and re.match(r"^\d{4}-\d{2}-\d{2}", published):
        published = published[:10]
    else:
        published = ""
    out.update(status="ok", text=text, title=str(data.get("title") or "").strip(),
               published=published, final_url=str(data.get("url") or url))
    return out


# --- one link, start to finish ------------------------------------------------------
# Only pages that shut robots out or need their scripts get the second try -
# never a login or paywall page (no getting around those) or a missing one.
_KEENABLE_RETRY = ("blocked", "empty")


def _worth_keenable(page) -> bool:
    return (page.get("status") in _KEENABLE_RETRY
            or (page.get("status") == "error" and page.get("reason") in
                ("blocked automated access", "took too long", "could not be opened")))


def refusal(url):
    """A link that is known at once not to be opened (no network needed):
    one with a password / key in it, or an X page that is not a single post
    -> fetch_link's answer for it; else None. Such a link gets no hold-on
    line - there is nothing to wait for."""
    risky = webfetch.risky_link(url)
    if risky:
        return {"status": "refused", "text": "", "title": "", "reason": risky,
                "url": url, "final_url": url, "via": None}
    if is_x_link(url) and not x_post(url):
        return {"status": "unsupported", "text": "", "title": "", "url": url, "final_url": url,
                "via": None, "reason": "is an X page other than a single post "
                                       "(profiles and searches need an X login)"}
    return None


def fetch_link(url, timeout=LINK_TIMEOUT) -> dict:
    """Open one pasted link -> webfetch.fetch_page's dict plus "url" (as
    pasted) and "via" ("page" | "x" | "keenable" | None)."""
    started = time.monotonic()
    refused = refusal(url)
    if refused:
        return refused
    if x_post(url):
        page = fetch_x_post(url, timeout)
        page.update(url=url, via="x" if page["status"] == "ok" else None)
        return page
    page = webfetch.fetch_page(url, timeout=timeout, min_chars=MIN_LINK_CHARS)
    page.update(url=url, via="page" if page.get("status") == "ok" else None)
    left = timeout - (time.monotonic() - started)
    if _worth_keenable(page) and left >= 2:
        backup = keenable_page(page.get("final_url") or url, timeout=left)
        if backup["status"] == "ok":
            backup.update(url=url, via="keenable")
            return backup
    return page

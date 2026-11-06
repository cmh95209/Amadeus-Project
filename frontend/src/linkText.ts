// Web links inside chat messages (2026-10-26): yours and hers are clickable.
// Pure helpers only - the component lives in components/LinkedText.tsx.
// Only http(s) addresses become links; the text itself is never turned into
// HTML, so a message cannot slip anything else onto the screen.

import { linkKey } from "./sources";
import type { SourceEntry } from "./sources";

export type TextPart = { text: string; href?: string };

// http(s):// or www. up to a space or a bracket / punctuation mark that cannot
// be part of an address (the same rule as the backend's link finder).
const BARE = /(?:\bhttps?:\/\/|\bwww\.)[^\s<>"'`「」『』【】（）〈〉《》、。，！？・　]+/gi;
// [label](address) - a Markdown-style link keeps its label.
const MARKDOWN = /\[([^\]\n]{1,200})\]\(\s*((?:https?:\/\/|www\.)[^\s)]+)\s*\)/gi;
const TRAILING = ".,;:!?'\"*_~";
const OPENER: Record<string, string> = { ")": "(", "]": "[", "}": "{" };

const count = (s: string, ch: string) => s.split(ch).length - 1;

/** "see https://x.com/a." -> "https://x.com/a"; a closing bracket stays only
 *  when it is part of the address ("wiki/Foo_(bar)"). */
export function trimLink(link: string): string {
  let out = link;
  while (out) {
    const last = out[out.length - 1];
    if (TRAILING.includes(last)) out = out.slice(0, -1);
    else if (last in OPENER && count(out, OPENER[last]) < count(out, last)) out = out.slice(0, -1);
    else break;
  }
  return out;
}

/** The address a link opens: "www." gets https:// in front; anything that
 *  is not a plain web address -> null (stays text). */
export function hrefFor(link: string): string | null {
  const url = /^https?:\/\//i.test(link) ? link : "https://" + link;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    if (!parsed.hostname.includes(".")) return null;
  } catch {
    return null;
  }
  return url;
}

function bareParts(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let at = 0;
  for (const m of text.matchAll(BARE)) {
    const link = trimLink(m[0]);
    const href = link ? hrefFor(link) : null;
    if (!href) continue;
    const start = m.index ?? 0;
    if (start > at) parts.push({ text: text.slice(at, start) });
    parts.push({ text: link, href });
    at = start + link.length;
  }
  if (at < text.length) parts.push({ text: text.slice(at) });
  return parts;
}

/** A message split into plain text and links, in order. */
export function splitLinks(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let at = 0;
  for (const m of text.matchAll(MARKDOWN)) {
    const href = hrefFor(trimLink(m[2]));
    if (!href) continue;
    const start = m.index ?? 0;
    if (start > at) parts.push(...bareParts(text.slice(at, start)));
    parts.push({ text: m[1], href });
    at = start + m[0].length;
  }
  if (at < text.length) parts.push(...bareParts(text.slice(at)));
  return parts.filter((p) => p.text !== "");
}

/** Every address this reply's sources hold (search results, the pages she
 *  read, links the user sent and where they led) - a link of hers that is
 *  among them came from a real search, not from her memory. */
export function knownLinkKeys(entries: SourceEntry[] | undefined): Set<string> {
  const keys = new Set<string>();
  for (const e of entries ?? []) {
    const urls: (string | null | undefined)[] =
      e.kind === "search" ? [e.read, ...(e.used ?? []), ...e.results.map((r) => r.url)]
        : e.kind === "link" ? [e.url, e.final]
          : [e.url];
    for (const u of urls) if (u) keys.add(linkKey(u));
  }
  return keys;
}

/** A link without its "?..." part ("?snr=1_300" tracking tails). */
function withoutQuery(key: string): string {
  return key.replace(/\?.*$/, "").replace(/\/+$/, "");
}

/** Her link came from this reply's searches: the same address, the same
 *  page without its "?..." tail, or a shortened form of a found page
 *  (".../app/825630" for ".../app/825630/STEINSGATE_0?snr=..."). */
export function isKnownLink(href: string, known: Set<string>): boolean {
  const key = linkKey(href);
  if (known.has(key)) return true;
  const bare = withoutQuery(key);
  for (const k of known) {
    const page = withoutQuery(k);
    if (page === bare || page.startsWith(bare + "/")) return true;
  }
  return false;
}

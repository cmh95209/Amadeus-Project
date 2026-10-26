// The web sources under her reply (2026-10-25): what she searched, the
// links that came back, the page she actually read, live-weather fetches.
// Pure helpers only - the chip + panel live in components/SourcesBar.tsx.

export type SearchSource = {
  kind: "search";
  query: string;
  results: { title: string; url: string; date?: string }[];
  /** The page she read in full (null when none could be read). */
  read: string | null;
  /** Reader mode: every page she took notes from. */
  used?: string[];
  /** Her saved notes from an earlier lookup were used (no new search). */
  reused?: boolean;
};

export type WeatherSource = {
  kind: "weather";
  place: string;
  url: string | null;
};

/** A link the user sent (2026-10-26): the app opened it for her reply. */
export type LinkSource = {
  kind: "link";
  url: string;
  /** Where it ended up after redirects (the page she read). */
  final: string;
  title: string;
  date?: string;
  opened: boolean;
  /** Why it could not be opened ("needs a login"). */
  reason?: string;
  reused?: boolean;
};

export type SourceEntry = SearchSource | WeatherSource | LinkSource;

/** One link in the panel: every result of every search, once. */
export type SourceLink = {
  url: string;
  title: string;
  domain: string;
  date?: string;
  /** She read this page (took notes from it), not just its snippet. */
  read: boolean;
  /** "weather" for the live-weather service, "web" otherwise. */
  kind: "web" | "weather";
  /** A link the user sent (not a search result). */
  sent?: boolean;
  /** A link the user sent that could not be opened: why. */
  failed?: string;
};

const WEB_LINK = /^https?:\/\/[^\s/]+/i;

/** Only plain web addresses may become clickable links. */
export function isWebLink(url: unknown): url is string {
  return typeof url === "string" && WEB_LINK.test(url.trim());
}

/** The server's sources, checked: anything malformed is dropped. */
export function normalizeSources(raw: unknown): SourceEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: SourceEntry[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    if (e.kind === "search") {
      const results = Array.isArray(e.results)
        ? e.results.flatMap((r) => {
            if (!r || typeof r !== "object") return [];
            const x = r as Record<string, unknown>;
            if (!isWebLink(x.url)) return [];
            return [{
              title: typeof x.title === "string" ? x.title : "",
              url: x.url.trim(),
              ...(typeof x.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(x.date)
                ? { date: x.date } : {}),
            }];
          })
        : [];
      const used = Array.isArray(e.used)
        ? e.used.filter(isWebLink).map((u) => u.trim())
        : [];
      out.push({
        kind: "search",
        query: typeof e.query === "string" ? e.query : "",
        results,
        read: isWebLink(e.read) ? e.read.trim() : null,
        ...(used.length ? { used } : {}),
        ...(e.reused === true ? { reused: true } : {}),
      });
    } else if (e.kind === "weather") {
      out.push({
        kind: "weather",
        place: typeof e.place === "string" ? e.place : "",
        url: isWebLink(e.url) ? e.url.trim() : null,
      });
    } else if (e.kind === "link" && isWebLink(e.url)) {
      out.push({
        kind: "link",
        url: e.url.trim(),
        final: isWebLink(e.final) ? e.final.trim() : e.url.trim(),
        title: typeof e.title === "string" ? e.title : "",
        ...(typeof e.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(e.date)
          ? { date: e.date } : {}),
        opened: e.opened === true,
        ...(typeof e.reason === "string" && e.reason ? { reason: e.reason } : {}),
        ...(e.reused === true ? { reused: true } : {}),
      });
    }
  }
  return out;
}

/** "www.game8.co" -> "game8.co"; "" when the address cannot be read. */
export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, "").toLowerCase();
  } catch {
    return "";
  }
}

/** The same page reached twice (trailing slash, #part, http/https) counts once. */
export function linkKey(url: string): string {
  return url.trim().replace(/^https?:\/\//i, "").replace(/#.*$/, "").replace(/\/+$/, "").toLowerCase();
}

/** Every link of the reply, in the order she met them, each once; the
 *  page she read is marked. */
export function sourceLinks(entries: SourceEntry[]): SourceLink[] {
  const seen = new Map<string, SourceLink>();
  for (const entry of entries) {
    if (entry.kind === "weather") {
      if (!entry.url) continue;
      const key = linkKey(entry.url);
      if (!seen.has(key)) {
        seen.set(key, {
          url: entry.url,
          title: entry.place ? `Live weather for ${entry.place}` : "Live weather",
          domain: domainOf(entry.url),
          read: true,
          kind: "weather",
        });
      }
      continue;
    }
    if (entry.kind === "link") {
      const url = entry.opened ? entry.final : entry.url;
      const key = linkKey(url);
      if (!seen.has(key)) {
        seen.set(key, {
          url,
          title: entry.title || domainOf(url),
          domain: domainOf(url),
          ...(entry.date ? { date: entry.date } : {}),
          read: entry.opened,
          kind: "web",
          sent: true,
          ...(entry.opened ? {} : { failed: entry.reason || "could not be opened" }),
        });
      }
      continue;
    }
    const readKeys = new Set([entry.read, ...(entry.used ?? [])]
      .filter((u): u is string => !!u).map(linkKey));
    for (const r of entry.results) {
      const key = linkKey(r.url);
      const prev = seen.get(key);
      if (prev) {
        if (readKeys.has(key)) prev.read = true;
        if (!prev.date && r.date) prev.date = r.date;
        continue;
      }
      seen.set(key, {
        url: r.url,
        title: r.title || domainOf(r.url),
        domain: domainOf(r.url),
        ...(r.date ? { date: r.date } : {}),
        read: readKeys.has(key),
        kind: "web",
      });
    }
  }
  return [...seen.values()];
}

/** The searches she ran (non-empty queries, each once); `reused` = answered
 *  from her saved notes of an earlier lookup instead of a new search. */
export function searchQueries(entries: SourceEntry[]): { query: string; reused: boolean }[] {
  const out: { query: string; reused: boolean }[] = [];
  for (const e of entries) {
    const q = e.kind === "search" ? e.query.trim() : "";
    if (q && !out.some((x) => x.query === q)) {
      out.push({ query: q, reused: e.kind === "search" && e.reused === true });
    }
  }
  return out;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** A page older than this is marked as old news in the panel. */
export const STALE_DAYS = 180;

/** "today" / "yesterday" / "3 days ago" / "Oct 3, 2025", plus whether the
 *  page is old enough to be marked (STALE_DAYS). null for no / bad date. */
export function dateLabel(iso: string | undefined, now: Date = new Date()): { text: string; stale: boolean } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!m) return null;
  const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  const day = Date.UTC(y, mo - 1, d);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((today - day) / 86400000);
  const full = `${MONTHS[mo - 1]} ${d}, ${y}`;
  let text: string;
  if (days < 0) text = full;            // a future date: just show it
  else if (days === 0) text = "today";
  else if (days === 1) text = "yesterday";
  else if (days < 30) text = `${days} days ago`;
  else text = full;
  return { text, stale: days > STALE_DAYS };
}

/** The chip's text: "5 sources · read 3" / "1 source" / "Live weather";
 *  "From my notes · ..." when every search was answered from saved notes. */
export function chipLabel(links: SourceLink[], entries: SourceEntry[] = []): string {
  if (links.length === 1 && links[0].kind === "weather") return "Live weather";
  if (links.length > 0 && links.every((l) => l.sent)) {
    const opened = links.filter((l) => l.read).length;
    const what = links.length === 1 ? "Your link" : `Your ${links.length} links`;
    if (opened === links.length) return what + (links.length === 1 ? " · read" : " · read all");
    return opened === 0 ? `${what} · couldn't open` : `${what} · read ${opened}`;
  }
  let label = `${links.length} source${links.length === 1 ? "" : "s"}`;
  const read = links.filter((l) => l.kind === "web" && l.read).length;
  if (read > 0) label += ` · read ${read}`;
  const searches = entries.filter((e) => e.kind === "search");
  if (searches.length > 0 && searches.every((e) => e.kind === "search" && e.reused)) {
    label = "From my notes · " + label;
  }
  return label;
}

/** A steady colour for a site's letter badge (same site, same colour). */
export function badgeHue(domain: string): number {
  let h = 0;
  for (const ch of domain) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

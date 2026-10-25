import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  badgeHue,
  chipLabel,
  dateLabel,
  domainOf,
  isWebLink,
  normalizeSources,
  searchQueries,
  sourceLinks,
} from "../src/sources";
import { SourcesBar } from "../src/components/SourcesBar";

// The web sources under her reply: the server's list is checked, links are
// merged once per page, dates read as "2 days ago" / "old · Mar 3, 2024".

const NOW = new Date(2026, 9, 25, 15, 0);

// --- normalizeSources: only well-formed entries and plain web links survive.
const raw = [
  { kind: "search", query: "Genshin banner", read: "https://game8.co/a",
    results: [
      { title: "Banner", url: "https://game8.co/a", date: "2026-10-24" },
      { title: "Evil", url: "javascript:alert(1)" },
      { title: "File", url: "file:///C:/x" },
      { title: "Bad date", url: "https://ign.com/b", date: "yesterday" },
      null, "junk",
    ] },
  { kind: "search", query: "Genshin banner", read: "javascript:alert(1)",
    results: [{ title: "Banner again", url: "https://game8.co/a/#top" },
              { title: "", url: "https://www.prydwen.gg/genshin" }] },
  { kind: "weather", place: "Tokyo", url: "https://open-meteo.com/" },
  { kind: "weather", place: "Nowhere", url: "data:text/html,x" },
  { kind: "unknown" }, null, 7,
];
const entries = normalizeSources(raw);
assert.equal(entries.length, 4);
assert.deepEqual(normalizeSources(null), []);
assert.deepEqual(normalizeSources({ kind: "search" }), []);
const first = entries[0];
assert.ok(first.kind === "search");
assert.equal(first.results.length, 2);                     // the bad links are gone
assert.equal(first.results[1].date, undefined);            // a bad date is dropped
assert.ok(entries[1].kind === "search" && entries[1].read === null);
assert.ok(entries[3].kind === "weather" && entries[3].url === null);

assert.equal(isWebLink("https://a.b/c"), true);
assert.equal(isWebLink("HTTP://A.B"), true);
assert.equal(isWebLink("javascript:alert(1)"), false);
assert.equal(isWebLink("//evil.example"), false);
assert.equal(isWebLink(undefined), false);

// --- sourceLinks: each page once (slash / #part / http-https), read page marked.
const links = sourceLinks(entries);
assert.deepEqual(links.map((l) => l.url), [
  "https://game8.co/a", "https://ign.com/b", "https://www.prydwen.gg/genshin", "https://open-meteo.com/",
]);
assert.equal(links[0].read, true);
assert.equal(links[0].date, "2026-10-24");
assert.equal(links[1].read, false);
assert.equal(links[2].title, "prydwen.gg");               // no title -> the site
assert.equal(links[2].domain, "prydwen.gg");
assert.equal(links[3].kind, "weather");
assert.equal(links[3].title, "Live weather for Tokyo");
assert.deepEqual(searchQueries(entries), [{ query: "Genshin banner", reused: false }]);

// A later search can supply a date the first one lacked.
const merged = sourceLinks(normalizeSources([
  { kind: "search", query: "a", read: null, results: [{ title: "X", url: "https://x.example/p" }] },
  { kind: "search", query: "b", read: "https://x.example/p/",
    results: [{ title: "X", url: "https://x.example/p/", date: "2026-10-20" }] },
]));
assert.equal(merged.length, 1);
assert.equal(merged[0].date, "2026-10-20");
assert.equal(merged[0].read, true);

assert.equal(domainOf("https://www.Game8.co/x"), "game8.co");
assert.equal(domainOf("not a url"), "");

// Reader mode: every page she took notes from is marked "read"; saved notes.
const reader = normalizeSources([
  { kind: "search", query: "q", read: "https://a.example/1",
    used: ["https://a.example/1", "https://c.example/3", "javascript:x"], reused: true,
    results: [{ title: "A", url: "https://a.example/1" }, { title: "B", url: "https://b.example/2" },
              { title: "C", url: "https://c.example/3/" }] },
]);
assert.ok(reader[0].kind === "search" && reader[0].used?.length === 2 && reader[0].reused === true);
const readerLinks = sourceLinks(reader);
assert.deepEqual(readerLinks.map((l) => l.read), [true, false, true]);
assert.equal(chipLabel(readerLinks, reader), "From my notes · 3 sources · read 2");
assert.deepEqual(searchQueries(reader), [{ query: "q", reused: true }]);
const notReused = normalizeSources([{ kind: "search", query: "q", read: null, reused: "yes",
  results: [{ title: "A", url: "https://a.example/1" }] }]);
assert.ok(notReused[0].kind === "search" && notReused[0].reused === undefined);
assert.equal(chipLabel(sourceLinks(notReused), notReused), "1 source");
const openReused = renderToStaticMarkup(createElement(SourcesBar, { sources: reader, defaultOpen: true }));
assert.match(openReused, /from my notes/);

// --- dateLabel: friendly, and old pages are marked.
assert.deepEqual(dateLabel("2026-10-25", NOW), { text: "today", stale: false });
assert.deepEqual(dateLabel("2026-10-24", NOW), { text: "yesterday", stale: false });
assert.deepEqual(dateLabel("2026-10-20", NOW), { text: "5 days ago", stale: false });
assert.deepEqual(dateLabel("2026-08-01", NOW), { text: "Aug 1, 2026", stale: false });
assert.deepEqual(dateLabel("2024-03-03", NOW), { text: "Mar 3, 2024", stale: true });
assert.deepEqual(dateLabel("2026-12-01", NOW), { text: "Dec 1, 2026", stale: false });
assert.equal(dateLabel(undefined, NOW), null);
assert.equal(dateLabel("2026-13-01", NOW), null);
assert.equal(dateLabel("soon", NOW), null);

assert.equal(chipLabel(links), "4 sources · read 1");
assert.equal(chipLabel(links.slice(0, 1)), "1 source · read 1");
assert.equal(chipLabel(links.slice(1, 2)), "1 source");
assert.equal(chipLabel(links.slice(3)), "Live weather");
assert.equal(badgeHue("game8.co"), badgeHue("game8.co"));
assert.ok(badgeHue("game8.co") >= 0 && badgeHue("game8.co") < 360);

// --- SourcesBar: closed it is just the chip; nothing at all without sources.
const closed = renderToStaticMarkup(createElement(SourcesBar, { sources: entries }));
assert.match(closed, /class="sources-chip"/);
assert.match(closed, /aria-expanded="false"/);
assert.match(closed, /4 sources · read 1/);
assert.doesNotMatch(closed, /sources-panel/);
assert.doesNotMatch(closed, /javascript:/);
assert.equal(renderToStaticMarkup(createElement(SourcesBar, { sources: [] })), "");
// A search that found nothing still says it searched.
const nothing = renderToStaticMarkup(createElement(SourcesBar, {
  sources: normalizeSources([{ kind: "search", query: "q", read: null, results: [] }]),
}));
assert.match(nothing, /Searched the web/);

console.log("sources.test.ts: ok");

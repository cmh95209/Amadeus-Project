import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { hrefFor, isKnownLink, knownLinkKeys, splitLinks, trimLink } from "../src/linkText";
import { normalizeSources } from "../src/sources";
import { LinkedText } from "../src/components/LinkedText";

// Links in chat messages (2026-10-26): yours and hers are clickable; hers that
// no search in the reply returned get an "unchecked" mark.

const links = (text: string) => splitLinks(text).filter((p) => p.href).map((p) => [p.text, p.href]);

// --- finding links: the same trimming as the backend's link finder.
assert.deepEqual(links("see https://en.wikipedia.org/wiki/Steins;Gate_0."),
  [["https://en.wikipedia.org/wiki/Steins;Gate_0", "https://en.wikipedia.org/wiki/Steins;Gate_0"]]);
assert.deepEqual(links("(see https://en.wikipedia.org/wiki/Steins;Gate_(TV_series))"),
  [["https://en.wikipedia.org/wiki/Steins;Gate_(TV_series)",
    "https://en.wikipedia.org/wiki/Steins;Gate_(TV_series)"]]);
assert.deepEqual(links("これ「https://game8.co/a」を見て。www.example.com/b、どう？"),
  [["https://game8.co/a", "https://game8.co/a"], ["www.example.com/b", "https://www.example.com/b"]]);
assert.deepEqual(links("Check [Game8's banner page](https://game8.co/a) for dates"),
  [["Game8's banner page", "https://game8.co/a"]]);
assert.deepEqual(links("javascript:alert(1) and http://localhost:5050 and data:text/html,x"), []);
assert.deepEqual(links("no links here, just example dot com"), []);
assert.equal(trimLink("https://a.com/x!?"), "https://a.com/x");
assert.equal(hrefFor("www.a.com"), "https://www.a.com");
assert.equal(hrefFor("https://nodot"), null);

// The text around links is kept exactly.
const parts = splitLinks("A https://a.com/x B");
assert.deepEqual(parts.map((p) => p.text), ["A ", "https://a.com/x", " B"]);

// --- which of her links came from a real search in this reply.
const sources = normalizeSources([
  { kind: "search", query: "q", read: "https://game8.co/a",
    results: [{ title: "G", url: "https://game8.co/a" }, { title: "P", url: "https://www.prydwen.gg/b/" }] },
  { kind: "link", url: "https://bit.ly/x", final: "https://news.example.com/s", title: "", opened: true },
]);
const known = knownLinkKeys(sources);
assert.ok(known.has("game8.co/a"));
assert.ok(known.has("www.prydwen.gg/b"));
assert.ok(known.has("news.example.com/s"));
assert.ok(known.has("bit.ly/x"));
assert.equal(knownLinkKeys(undefined).size, 0);

// A found page she wrote shorter or without its tracking tail still counts.
const steam = knownLinkKeys(normalizeSources([{ kind: "search", query: "q", read: null, results: [
  { title: "S", url: "https://store.steampowered.com/app/825630/STEINSGATE_0?snr=1_300_morelikev2__307_11" },
  { title: "Y", url: "https://www.youtube.com/watch?v=abc" }] }]));
assert.ok(isKnownLink("https://store.steampowered.com/app/825630", steam));
assert.ok(isKnownLink("https://store.steampowered.com/app/825630/STEINSGATE_0/", steam));
assert.ok(!isKnownLink("https://store.steampowered.com/app/8256", steam));     // not a whole part
assert.ok(!isKnownLink("https://store.steampowered.com/app/999999", steam));
assert.ok(isKnownLink("https://www.youtube.com/watch?v=abc", steam));
assert.ok(isKnownLink("https://store.steampowered.com", steam));               // the site itself

// --- the component: plain text stays plain; links open in a new tab.
assert.equal(renderToStaticMarkup(createElement(LinkedText, { text: "hello <b>there</b>" })),
  "hello &lt;b&gt;there&lt;/b&gt;");
const mine = renderToStaticMarkup(createElement(LinkedText, { text: "read https://a.com/x please" }));
assert.match(mine, /<a class="chat-link" href="https:\/\/a.com\/x" target="_blank" rel="noopener noreferrer"/);
assert.doesNotMatch(mine, /unchecked/);
const hers = renderToStaticMarkup(createElement(LinkedText, {
  text: "Here: https://game8.co/a and https://made-up.example.org/page", known,
}));
assert.equal((hers.match(/chat-link-unchecked/g) ?? []).length, 1);
assert.match(hers, /made-up\.example\.org\/page<\/a><span class="chat-link-unchecked"/);

console.log("linkText.test.ts: ok");

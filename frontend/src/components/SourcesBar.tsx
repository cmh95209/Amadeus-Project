import { useId, useState } from "react";
import {
  badgeHue,
  chipLabel,
  dateLabel,
  searchQueries,
  sourceLinks,
} from "../sources";
import type { SourceEntry, SourceLink } from "../sources";

// Under her reply: a small "5 sources" chip with the sites' letter badges.
// A click opens the panel - what she searched, then one card per link (site,
// title, date, and a mark on the page she read in full). Links open in a new
// tab. No site icons are downloaded: the badge is the site's first letter.

const BADGES_SHOWN = 3;

function SiteBadge({ domain, size = "sm" }: { domain: string; size?: "sm" | "md" }) {
  const letter = (domain.match(/[a-z0-9]/i)?.[0] ?? "?").toUpperCase();
  return (
    <span
      className={`source-badge source-badge-${size}`}
      style={{ ["--badge-hue" as string]: String(badgeHue(domain)) }}
      aria-hidden="true"
    >
      {letter}
    </span>
  );
}

function SourceCard({ link, index }: { link: SourceLink; index: number }) {
  const when = dateLabel(link.date);
  return (
    <li>
      <a
        className="source-card"
        href={link.url}
        target="_blank"
        rel="noopener noreferrer"
        title={link.url}
      >
        <span className="source-card-top">
          <span className="source-index">{index}</span>
          <SiteBadge domain={link.domain} />
          <span className="source-domain">{link.domain || "link"}</span>
          {link.read && (
            <span
              className="source-read"
              title={link.kind === "weather"
                ? "Live data she used for this reply"
                : "She read this page and took notes from it, not just its snippet"}
            >
              {link.kind === "weather" ? "live" : "read"}
            </span>
          )}
        </span>
        <span className="source-title">{link.title}</span>
        {when && (
          <span
            className={`source-date${when.stale ? " stale" : ""}`}
            title={when.stale ? "An older page - this may be old news" : `Published or updated ${link.date}`}
          >
            {when.stale ? `old · ${when.text}` : when.text}
          </span>
        )}
      </a>
    </li>
  );
}

export function SourcesBar({ sources, defaultOpen = false }: {
  sources: SourceEntry[];
  /** Start with the panel open (tests / previews). */
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const links = sourceLinks(sources);
  const queries = searchQueries(sources);
  if (links.length === 0 && queries.length === 0) return null;

  const domains = [...new Set(links.map((l) => l.domain).filter(Boolean))];
  return (
    <div className={`sources${open ? " open" : ""}`}>
      <button
        type="button"
        className="sources-chip"
        aria-expanded={open}
        aria-controls={panelId}
        title={open ? "Hide the sources" : "Show the web pages she used"}
        onClick={() => setOpen((v) => !v)}
      >
        {domains.length > 0 && (
          <span className="source-badges">
            {domains.slice(0, BADGES_SHOWN).map((d) => <SiteBadge key={d} domain={d} />)}
          </span>
        )}
        <span className="sources-chip-label">
          {links.length > 0 ? chipLabel(links, sources) : "Searched the web"}
        </span>
        <span className="sources-chevron" aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>

      {open && (
        <div className="sources-panel" id={panelId}>
          {queries.length > 0 && (
            <div className="sources-queries">
              <span className="sources-heading">Searched</span>
              {queries.map((q) => (
                <span key={q.query} className="sources-query" title={q.query}>
                  {q.query}
                  {q.reused && (
                    <span className="sources-reused"
                      title="Answered from her saved notes of an earlier search in this chat">
                      {" "}· from my notes
                    </span>
                  )}
                </span>
              ))}
            </div>
          )}
          {links.length > 0 ? (
            <ol className="sources-list">
              {links.map((link, i) => (
                <SourceCard key={link.url} link={link} index={i + 1} />
              ))}
            </ol>
          ) : (
            <p className="sources-empty">The search found no pages.</p>
          )}
        </div>
      )}
    </div>
  );
}

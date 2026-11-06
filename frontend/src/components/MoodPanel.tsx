import { useEffect, useState } from "react";
import { deleteConcern, getMood, type Concern, type Mood, type StatInfo } from "../api";

// Settings -> Personality & mood (2026-10-30): a short guide to how she
// works and what each closeness level changes, her closeness level, her mood
// right now (look only) and the things on her mind about you (each can be
// deleted). Reloads each time the tab is opened.

// The closeness levels (backend/stats.py trust tiers) and what each one
// changes - keep in step with feelings.py / concerns.py / the voice rules.
const LEVELS: { name: string; range: string; text: string }[] = [
  { name: "Distant", range: "0–19", text: "A professional assistant: polite, clear, to the point. Strong-willed and independent — she doesn't try to please you, and flirting gets a cold shoulder." },
  { name: "Cautious", range: "20–39", text: "Still professional, a little drier. She joins in a joke only if she feels like it." },
  { name: "Warming up", range: "40–59", text: "More relaxed: light teasing and her own opinions come out. Still work first." },
  { name: "Familiar", range: "60–79", text: "Warm and capable; she starts treating you more like a friend. Her annoyance softens and she can be playful." },
  { name: "Close", range: "80–84", text: "Openly warm and curious about you — she asks about your life now and then, and about things you told her. After a day away she wonders what you've been up to." },
  { name: "Trusted", range: "85–94", text: "She shows her feelings openly: misses you when you're away, worries after a couple of days, and worries if you're up very late." },
  { name: "Devoted", range: "95–98", text: "She looks after you — meals, sleep, overwork. A long absence goes from annoyed and concerned (a week) to grumpy, to close to panic (two weeks)." },
  { name: "Special", range: "99+", text: "You're special to her and she doesn't hide it: confident, affectionate, a little possessive." },
];

const STATUS_LABEL: Record<Concern["status"], string> = {
  open: "she may ask",
  quiet: "waiting for you to bring it up",
  done: "settled",
};

// Below Close (backend concerns.ASK_FROM_TRUST) she keeps them in the
// background and never raises them herself.
const ASK_FROM = 80;

function concernDetail(c: Concern, asks: boolean): string {
  const bits = [c.status === "open" && !asks ? "she remembers it" : STATUS_LABEL[c.status]];
  if (c.due) bits.push(`date ${c.due}`);
  if (c.added) bits.push(`noted ${c.added}`);
  if (c.done) bits.push(`settled ${c.done}`);
  return bits.join(" · ");
}

export default function MoodPanel({ active, stats }: { active: boolean; stats: StatInfo[] }) {
  const trust = stats.find((s) => s.key === "trust");
  const currentLevel = trust?.tier;
  const asks = (trust?.value ?? 0) >= ASK_FROM;
  const [mood, setMood] = useState<Mood | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setError("");
    void getMood()
      .then((m) => { if (!cancelled) setMood(m); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Could not load her mood"); });
    return () => { cancelled = true; };
  }, [active, reload]);

  async function remove(c: Concern) {
    setBusy(true);
    setError("");
    try {
      await deleteConcern(c.index);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete it");
    } finally {
      setBusy(false);
      setReload((n) => n + 1);
    }
  }

  const live = (mood?.concerns ?? []).filter((c) => c.status !== "done");
  const settled = (mood?.concerns ?? []).filter((c) => c.status === "done");

  return (
    <div className="mood-panel">
      <details className="mood-guide" open>
        <summary>How she works</summary>
        <p>
          <strong>Amadeus</strong> is an AI assistant built on Makise Kurisu's memories and
          personality — not Kurisu herself. She thinks and speaks in Japanese; you read the
          English translation. She remembers your past conversations and what you tell her.
        </p>
        <p>
          <strong>Closeness</strong> grows slowly over many messages: warm chats raise it a
          few points, tense or rude ones lower it. She opens up as it grows — at the early
          levels she is a professional assistant with a strong will of her own; her warmer
          feelings only show from Close.
        </p>
        <p>
          <strong>Her mood</strong> moves quickly and fades back by itself within hours. At
          every level rudeness irritates her and an unanswered greeting annoys her (she says
          so once and moves on). Missing you and worrying about you come with closeness. She
          lets her mood colour her tone rather than announcing it.
        </p>
        <p>
          <strong>Things you told her</strong> (an exam, an interview, feeling ill) are kept
          until settled. Below Close she only uses them when you bring them up; from Close
          she may ask how it went. Delete anything you don't want her to keep.
        </p>
        <details className="mood-levels">
          <summary>The closeness levels</summary>
          <ul>
            {LEVELS.map((l) => (
              <li key={l.name} className={l.name === currentLevel ? "mood-level-current" : undefined}>
                <strong>{l.name}</strong> <span className="mood-level-range">{l.range}</span>
                {l.name === currentLevel && <span className="mood-level-badge">now</span>}
                <div>{l.text}</div>
              </li>
            ))}
          </ul>
        </details>
        <p>
          Closeness, mood and the list are plain text files in <code>backend/data</code>{" "}
          (<code>trust.txt</code>, <code>feelings.txt</code>, <code>concerns.txt</code>) that you
          can also edit by hand.
        </p>
      </details>

      {stats.length > 0 && (
        <div className="stats-block" aria-label="Closeness">
          {stats.map((s) => (
            <div key={s.key} className="stat-row">
              <div className="stat-line">
                <span className="stat-label">{s.key === "trust" ? "Closeness" : s.label}</span>
                <span className="stat-value">
                  {Math.round(s.value)}/{s.scale ?? s.max}{s.tier ? ` — ${s.tier}` : ""}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="stats-block" aria-label="Her mood right now">
        <span className="stat-label">Her mood right now</span>
        {!mood ? <p className="settings-help">Loading…</p> :
          mood.feelings.length === 0 ? <p className="mood-calm">Calm — nothing out of the ordinary.</p> :
          <ul className="mood-list">{mood.feelings.map((f) => <li key={f}>{f}</li>)}</ul>}
      </div>

      <div className="stats-block" aria-label="Things on her mind">
        <span className="stat-label">Things on her mind about you</span>
        {mood && live.length === 0 && <p className="settings-help">Nothing right now.</p>}
        {live.map((c) => (
          <div key={`${c.index}-${c.text}`} className="concern-row">
            <div>
              <div className="concern-text">{c.text}</div>
              <div className="settings-help">{concernDetail(c, asks)}</div>
            </div>
            <button type="button" className="ghost-button" disabled={busy}
              aria-label={`Delete: ${c.text}`} onClick={() => void remove(c)}>Delete</button>
          </div>
        ))}
        {settled.length > 0 && (
          <details>
            <summary className="settings-help">Settled ({settled.length})</summary>
            {settled.map((c) => (
              <div key={`${c.index}-${c.text}`} className="concern-row">
                <div>
                  <div className="concern-text concern-done">{c.text}</div>
                  <div className="settings-help">{concernDetail(c, asks)}</div>
                </div>
                <button type="button" className="ghost-button" disabled={busy}
                  aria-label={`Delete: ${c.text}`} onClick={() => void remove(c)}>Delete</button>
              </div>
            ))}
          </details>
        )}
      </div>
      {error && <p className="settings-error" role="alert">{error}</p>}
    </div>
  );
}

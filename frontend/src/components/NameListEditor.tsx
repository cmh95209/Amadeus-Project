import { useState } from "react";
import type { NameEntry } from "../api";

// Settings -> Preferences -> About you -> Names she knows: rows of
// "name as typed" + "how she writes it in Japanese" (katakana). Save in the
// Settings footer stores the list; empty rows are dropped there.

/** True for a reading the backend accepts: kana only (plus ー・ and spaces). */
export function isKanaReading(text: string): boolean {
  return /^[぀-ゟ゠-ヿㇰ-ㇿｦ-ﾟ\s]+$/.test(text.trim());
}

/** The first row that would be refused, as a message ("" = all fine). */
export function nameListProblem(rows: NameEntry[]): string {
  for (const row of rows) {
    const word = row.word.trim();
    const reading = row.reading.trim();
    if (!word && !reading) continue;
    if (!word) return `Add the name for ${reading}, or remove that row.`;
    if (word.includes("=")) return `"${word}": a name can't contain "=".`;
    if (!reading) return `"${word}": add how she writes it (press Suggest), or remove that row.`;
    if (!isKanaReading(reading)) return `"${word}": write how she says it in katakana or hiragana.`;
  }
  return "";
}

type Props = {
  rows: NameEntry[];
  onChange: (rows: NameEntry[]) => void;
  disabled?: boolean;
  /** A reading for a name as typed ("" = none could be made). */
  suggest: (name: string) => Promise<string>;
};

export function NameListEditor({ rows, onChange, disabled, suggest }: Props) {
  const [suggesting, setSuggesting] = useState<number | null>(null);
  const [note, setNote] = useState("");

  function update(index: number, change: Partial<NameEntry>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...change } : row)));
  }

  async function suggestFor(index: number) {
    const name = rows[index]?.word.trim();
    if (!name || suggesting !== null) return;
    setSuggesting(index);
    setNote("");
    try {
      const reading = await suggest(name);
      if (reading) update(index, { reading });
      else setNote(`Couldn't suggest one for "${name}" - type it in katakana instead.`);
    } catch {
      setNote(`Couldn't suggest one for "${name}" - type it in katakana instead.`);
    } finally {
      setSuggesting(null);
    }
  }

  return (
    <div className="name-list" role="group" aria-label="Names she knows" aria-describedby="names-help">
      <span className="name-list-title">Names she knows</span>
      {rows.map((row, i) => (
        <div className="name-row" key={i}>
          <input
            value={row.word}
            disabled={disabled}
            onChange={(event) => update(i, { word: event.target.value })}
            placeholder="Name, e.g. Ineffa"
            aria-label={`Name ${i + 1}`}
            autoComplete="off"
            spellCheck={false}
          />
          <input
            value={row.reading}
            disabled={disabled}
            onChange={(event) => update(i, { reading: event.target.value })}
            placeholder="katakana, e.g. イネファ"
            aria-label={`How she writes ${row.word.trim() || `name ${i + 1}`}`}
            autoComplete="off"
            spellCheck={false}
            lang="ja"
          />
          <button
            type="button"
            className="ghost-button"
            disabled={disabled || suggesting !== null || !row.word.trim()}
            onClick={() => void suggestFor(i)}
          >
            {suggesting === i ? "Suggesting…" : "Suggest"}
          </button>
          <button
            type="button"
            className="ghost-button name-remove"
            disabled={disabled}
            aria-label={`Remove ${row.word.trim() || `name ${i + 1}`}`}
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
          >
            ×
          </button>
        </div>
      ))}
      {note && <p className="settings-error" role="status">{note}</p>}
      <button
        type="button"
        className="ghost-button name-add"
        disabled={disabled}
        onClick={() => onChange([...rows, { word: "", reading: "" }])}
      >
        Add name
      </button>
    </div>
  );
}

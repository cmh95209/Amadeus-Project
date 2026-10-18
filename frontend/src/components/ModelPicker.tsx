import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

// Settings -> Connection -> Model: one searchable dropdown for both modes
// (her built-in model's GGUF files, or the models a custom server lists).
// Cloud providers can list hundreds of models, so typing filters the list;
// with allowCustom a name the server does not list can still be used.

export type ModelOption = {
  /** What gets saved / loaded (a model id or a file name). */
  value: string;
  /** What the list shows (defaults to value). */
  label?: string;
  /** Small grey text after the label, e.g. "6.3 GB". */
  hint?: string;
};

/** The options matching a search: every word of the query must appear in
 * the label or the value (any case, any order). An empty query = all. */
export function filterModelOptions(options: ModelOption[], query: string): ModelOption[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return options;
  return options.filter((o) => {
    const text = `${o.label ?? ""} ${o.value}`.toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

type Props = {
  options: ModelOption[];
  value: string;
  onChange: (value: string) => void;
  /** Accept a typed name that is not in the list (custom servers). */
  allowCustom?: boolean;
  disabled?: boolean;
  /** Shown when the list is empty. */
  emptyText?: string;
  ariaLabel?: string;
};

export function ModelPicker({ options, value, onChange, allowCustom, disabled, emptyText, ariaLabel }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();

  const matches = useMemo(() => filterModelOptions(options, query), [options, query]);
  const current = options.find((o) => o.value === value);
  const shown = open ? query : current?.label ?? value;

  useEffect(() => {
    setHighlight(0);
  }, [query, open]);

  // Keep the highlighted row visible while moving with the arrow keys.
  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.children[highlight] as HTMLElement | undefined;
    row?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  function choose(next: string) {
    onChange(next);
    setOpen(false);
    setQuery("");
    inputRef.current?.blur();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, Math.max(matches.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (matches[highlight]) choose(matches[highlight].value);
      else if (allowCustom && query.trim()) choose(query.trim());
    } else if (event.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  }

  return (
    <div className={`model-picker${open ? " is-open" : ""}`}>
      <input
        ref={inputRef}
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        value={shown}
        disabled={disabled}
        placeholder={
          open
            ? options.length > 0
              ? `Type to search ${options.length.toLocaleString("en-US")} models`
              : allowCustom ? "Type a model name" : ""
            : options.length === 0 && !value ? emptyText ?? "No models" : "Choose a model"
        }
        spellCheck={false}
        autoComplete="off"
        onFocus={() => {
          setQuery("");
          setOpen(true);
        }}
        onBlur={() => {
          // A typed name the list does not have still counts (custom servers).
          if (allowCustom && query.trim() && query.trim() !== value) onChange(query.trim());
          setOpen(false);
          setQuery("");
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul className="model-picker-list" id={listId} role="listbox" ref={listRef}>
          {matches.map((o, i) => (
            <li
              key={o.value}
              role="option"
              aria-selected={o.value === value}
              className={`${i === highlight ? "is-highlighted" : ""}${o.value === value ? " is-current" : ""}`}
              // mousedown, not click: it fires before the input's blur closes the list
              onMouseDown={(event) => {
                event.preventDefault();
                choose(o.value);
              }}
              onMouseEnter={() => setHighlight(i)}
            >
              <span className="model-picker-label">{o.label ?? o.value}</span>
              {o.hint && <span className="model-picker-hint">{o.hint}</span>}
            </li>
          ))}
          {matches.length === 0 && (
            <li className="model-picker-empty" aria-disabled="true">
              {options.length === 0
                ? emptyText ?? "No models"
                : allowCustom && query.trim()
                  ? `No match - press Enter to use "${query.trim()}"`
                  : "No match"}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

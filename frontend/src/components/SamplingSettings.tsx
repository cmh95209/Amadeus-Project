import { useEffect, useState } from "react";
import {
  getSampling,
  setSampling,
  type SamplingEntry,
  type SamplingParamSpec,
  type SamplingSettings,
} from "../api";

const DISPLAY_NAMES: Record<string, string> = {
  temperature: "Temperature",
  top_p: "Top P",
  top_k: "Top K",
  min_p: "Min P",
  repetition_penalty: "Repetition penalty",
  presence_penalty: "Presence penalty",
  max_tokens: "Output max tokens",
};

const TOOLTIPS: Record<string, string> = {
  temperature:
    "How random her replies are. Lower = safe and consistent (but can repeat itself); higher = varied and creative (but can drift or loop). Typical: 0.5 - 1.2.",
  top_p:
    "Only words whose combined likelihood reaches this probability are considered. Lower = safer word choices. Typical: 0.9.",
  top_k:
    "Only the K most likely next words are considered at each step. Lower = safer. Typical: 40.",
  min_p:
    "Ignores any word whose chance is below this fraction of the top word's chance. 0 = off. Typical: 0.0 - 0.1.",
  repetition_penalty:
    "Pushes her away from words she just used. 1.0 = off; higher = fewer repeats, but can start to sound forced. Typical: 1.05 - 1.3.",
  presence_penalty:
    "Bonus for introducing new topics instead of repeating existing ones. Positive = more variety, negative = more repetition. 0 = off.",
  max_tokens:
    "Hard limit on how long one reply may be, in tokens (1 token is about three quarters of a word). When off, Amadeus uses its built-in 1024 limit.",
};

type ParamRowProps = {
  name: string;
  spec: SamplingParamSpec;
  entry: SamplingEntry;
  isLocalOnly: boolean;
  isRejected: boolean;
  disabled: boolean;
  onChange: (entry: SamplingEntry) => void;
};

function clampValue(spec: SamplingParamSpec, value: number): number {
  const clamped = Math.min(spec.max, Math.max(spec.min, value));
  return spec.integer ? Math.round(clamped) : clamped;
}

function ParamRow({ name, spec, entry, isLocalOnly, isRejected, disabled, onChange }: ParamRowProps) {
  const [text, setText] = useState(entry.value === null ? "" : String(entry.value));

  // Keep the typed box in sync when the value changes elsewhere (load, reset, save).
  useEffect(() => {
    setText(entry.value === null ? "" : String(entry.value));
  }, [entry.value]);

  const label = DISPLAY_NAMES[name] ?? name;
  const tooltip = TOOLTIPS[name];

  function commitText() {
    const parsed = Number(text);
    if (text.trim() === "" || !Number.isFinite(parsed)) {
      setText(entry.value === null ? "" : String(entry.value));
      return;
    }
    const value = clampValue(spec, parsed);
    onChange({ ...entry, value });
    setText(String(value));
  }

  return (
    <div className="sampling-row">
      <div className="sampling-head">
        <label className="sampling-name">
          <input
            type="checkbox"
            checked={entry.enabled}
            disabled={disabled}
            onChange={(event) => onChange({ ...entry, enabled: event.target.checked })}
          />
          <span>{label}</span>
          <span className="info-tip" tabIndex={0} aria-label={tooltip}>
            ?
            <span className="info-tip-text" role="tooltip">{tooltip}</span>
          </span>
        </label>
        <input
          className="sampling-value"
          type="number"
          inputMode="decimal"
          min={spec.min}
          max={spec.max}
          step={spec.step}
          value={entry.enabled ? text : ""}
          placeholder="off"
          disabled={disabled || !entry.enabled}
          aria-label={`${label} value`}
          onChange={(event) => setText(event.target.value)}
          onBlur={commitText}
          onKeyDown={(event) => {
            if (event.key === "Enter") (event.target as HTMLInputElement).blur();
          }}
        />
      </div>
      <input
        className="sampling-slider"
        type="range"
        min={spec.min}
        max={spec.max}
        step={spec.step}
        value={entry.enabled ? (entry.value ?? spec.min) : spec.min}
        disabled={disabled || !entry.enabled}
        aria-label={`${label} slider`}
        onChange={(event) => {
          const value = clampValue(spec, Number(event.target.value));
          onChange({ ...entry, enabled: true, value });
        }}
      />
      {isLocalOnly && entry.enabled && (
        <p className="settings-help sampling-note">
          Local servers only - cloud APIs ignore this setting.
        </p>
      )}
      {isRejected && (
        <p className="settings-help sampling-note">
          Your current server refused this setting - it is skipped automatically for this
          app session.
        </p>
      )}
    </div>
  );
}

export default function SamplingSettings({ busy }: { busy: boolean }) {
  const [params, setParams] = useState<Record<string, SamplingParamSpec>>({});
  const [localOnly, setLocalOnly] = useState<string[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [saved, setSaved] = useState<SamplingSettings | null>(null);
  const [draft, setDraft] = useState<SamplingSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    getSampling()
      .then((config) => {
        if (cancelled) return;
        setParams(config.params);
        setLocalOnly(config.local_only);
        setRejected(config.rejected);
        setSaved(config.sampling);
        setDraft(config.sampling);
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Could not load sampling settings");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty =
    saved !== null && draft !== null && JSON.stringify(draft) !== JSON.stringify(saved);
  const disabled = busy || loading || saving || draft === null;

  async function save() {
    if (!dirty || saving || disabled) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await setSampling(draft);
      const config = await getSampling(); // server clamps values; refresh everything
      setParams(config.params);
      setLocalOnly(config.local_only);
      setRejected(config.rejected);
      setSaved(config.sampling);
      setDraft(config.sampling);
      setNotice("Saved. Applies to the next message - no restart needed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save sampling settings");
    } finally {
      setSaving(false);
    }
  }

  function resetAll() {
    if (saved === null) return;
    const cleared: SamplingSettings = {};
    for (const name of Object.keys(saved)) {
      cleared[name] = { enabled: false, value: null };
    }
    setDraft(cleared);
    setNotice("");
    setError("");
  }

  if (loading) {
    return <p className="settings-help" role="status">Loading sampling settings...</p>;
  }
  if (loadError || draft === null) {
    return (
      <p className="settings-error" role="alert">
        {loadError || "Could not load sampling settings"}
      </p>
    );
  }

  const orderedNames = Object.keys(params);

  return (
    <>
      <p className="settings-intro">
        Fine-tune how her model generates replies. Leave a setting off to use your model
        server's own default (Unsloth, Ollama, LM Studio, or the cloud API). Changes apply
        to the next message - no restart needed.
      </p>
      {orderedNames.map((name) => (
        <ParamRow
          key={name}
          name={name}
          spec={params[name]}
          entry={draft[name] ?? { enabled: false, value: null }}
          isLocalOnly={localOnly.includes(name)}
          isRejected={rejected.includes(name)}
          disabled={disabled}
          onChange={(entry) => setDraft({ ...draft, [name]: entry })}
        />
      ))}
      <div className="modal-actions sampling-actions">
        <button className="ghost-button" onClick={resetAll} disabled={disabled}>
          Reset all to server defaults
        </button>
        <button className="primary-button" onClick={() => void save()} disabled={disabled || !dirty}>
          {saving ? "Saving..." : "Save sampling settings"}
        </button>
      </div>
      {error && <p className="settings-error" role="alert">{error}</p>}
      {notice && <p className="settings-success" role="status">{notice}</p>}
    </>
  );
}

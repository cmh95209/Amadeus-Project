import { useState } from "react";
import type { SystemStatusHook } from "../useSystemStatus";
import {
  PART_TITLES,
  describePart,
  formatElapsed,
  partStates,
  type PartName,
} from "../systemStatus";

/** Settings -> System (2026-11-01): the live state of her parts and the
 *  Restart button (so a restart needs no remote desktop). */
export function SystemPanel({ sys }: { sys: SystemStatusHook }) {
  const [confirming, setConfirming] = useState(false);
  const states = partStates(sys.status, sys.restarting, sys.unreachable);
  const canRestart = !!sys.status?.launcher && !sys.restarting && !sys.unreachable;

  return (
    <div className="system-panel">
      <div className="system-heading">
        <span className="brain-choice-label">Live status</span>
        {sys.restarting && (
          <span className="system-progress" role="status">
            <span className="system-spinner" aria-hidden="true" />
            {sys.otherRestart
              ? "Restarting (asked from another device)..."
              : `Restarting... ${formatElapsed(sys.elapsedMs)}`}
          </span>
        )}
      </div>

      <ul className="system-parts" aria-live="polite">
        {(Object.keys(PART_TITLES) as PartName[]).map((part) => {
          const view = describePart(states[part]);
          return (
            <li key={part} className={`system-part tone-${view.tone}`}>
              <span className="system-dot" aria-hidden="true" />
              <span className="system-part-name">
                {PART_TITLES[part].title}
                <small>{PART_TITLES[part].detail}</small>
              </span>
              <span className="system-part-state">{view.label}</span>
            </li>
          );
        })}
        <li className="system-part tone-ok">
          <span className="system-dot" aria-hidden="true" />
          <span className="system-part-name">
            The web page<small>keeps running during a restart</small>
          </span>
          <span className="system-part-state">Running</span>
        </li>
      </ul>

      {sys.gaveUp && (
        <p className="system-note bad">
          She has not come back after several minutes. Amadeus needs to be
          started again on the PC (the launcher window says why).
        </p>
      )}
      {sys.status?.restart?.state === "partial" && !sys.restarting && (
        <p className="system-note">{sys.status.restart.message}</p>
      )}
      {sys.error && <p className="system-note bad">{sys.error}</p>}

      <div className="system-restart">
        {!confirming ? (
          <button
            type="button"
            className="ghost-button"
            disabled={!canRestart || sys.requesting}
            onClick={() => setConfirming(true)}
          >
            Restart Amadeus
          </button>
        ) : (
          <div className="system-confirm" role="group" aria-label="Confirm restart">
            <p>
              Restart her now? Chat works again after about 15 seconds; her
              voice and memory follow within a few minutes. When she is back,
              this page reloads and she greets you.
            </p>
            <div className="system-confirm-buttons">
              <button
                type="button"
                className="primary-button"
                disabled={sys.requesting}
                onClick={() => {
                  setConfirming(false);
                  void sys.restart();
                }}
              >
                Restart now
              </button>
              <button type="button" className="ghost-button" onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {!sys.status?.launcher && sys.status && (
          <p className="system-note">
            Restart works only when Amadeus was started with its launcher (the
            Amadeus shortcut).
          </p>
        )}
      </div>
    </div>
  );
}

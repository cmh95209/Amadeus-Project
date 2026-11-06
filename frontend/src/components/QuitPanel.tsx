import { useEffect, useState } from "react";
import { getSystemStatus, isLocalPage, requestShutdown } from "../api";

/** Quit from Settings (2026-11-06). On a phone it closes the app (Amadeus
 *  keeps running on the PC). On the PC it closes this window, or shuts
 *  everything down through the launcher. A page may close itself only when
 *  it was the first page in its window or tab (the phone app and the
 *  launcher's window are); otherwise it says how to close it by hand. */

export type QuitStep = "ask" | "stopping" | "stopped" | "still-open";

export function stillOpenMessage(local: boolean, stopped: boolean): string {
  if (stopped) return "Amadeus is shut down. You can close this tab now.";
  return local
    ? "This browser won't let the page close itself (the tab was opened on another page first). Close the tab yourself."
    : "Your phone didn't let the app close itself. Swipe it away from your recent apps instead.";
}

function closeThisPage(after: (step: QuitStep) => void, stopped: boolean) {
  window.close();
  // Still here a moment later: the browser refused.
  window.setTimeout(() => after(stopped ? "stopped" : "still-open"), 500);
}

export function QuitPanel({ onCancel, local = isLocalPage }: { onCancel: () => void; local?: boolean }) {
  const [step, setStep] = useState<QuitStep>("ask");
  const [canShutDown, setCanShutDown] = useState<boolean | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!local) return;
    let alive = true;
    getSystemStatus()
      .then((s) => alive && setCanShutDown(!!s.shutdown))
      .catch(() => alive && setCanShutDown(false));
    return () => {
      alive = false;
    };
  }, [local]);

  async function shutDown() {
    setError("");
    setStep("stopping");
    try {
      await requestShutdown();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not ask for a shutdown");
      setStep("ask");
      return;
    }
    closeThisPage(setStep, true);
  }

  if (step === "stopped" || step === "still-open") {
    return (
      <div className="system-confirm quit-panel" role="status">
        <p>{stillOpenMessage(local, step === "stopped")}</p>
        {step === "still-open" && (
          <div className="system-confirm-buttons">
            <button type="button" className="ghost-button" onClick={onCancel}>OK</button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="system-confirm quit-panel" role="group" aria-label="Quit Amadeus">
      <p>
        {local
          ? "Close only this window, or shut Amadeus down completely (her voice, memory and the launcher window too)?"
          : "Quit Amadeus on this device? She keeps running on your PC, and greets you when you open the app again."}
      </p>
      {error && <p className="system-note bad">{error}</p>}
      <div className="system-confirm-buttons">
        {local ? (
          <>
            <button type="button" className="primary-button" disabled={step === "stopping"}
              onClick={() => closeThisPage(setStep, false)}>
              Close this window
            </button>
            <button type="button" className="ghost-button danger"
              disabled={!canShutDown || step === "stopping"} onClick={() => void shutDown()}>
              {step === "stopping" ? "Shutting down..." : "Shut down Amadeus"}
            </button>
          </>
        ) : (
          <button type="button" className="primary-button" onClick={() => closeThisPage(setStep, false)}>
            Quit
          </button>
        )}
        <button type="button" className="ghost-button" disabled={step === "stopping"} onClick={onCancel}>
          Cancel
        </button>
      </div>
      {local && canShutDown === false && (
        <p className="system-note">
          Shut down works when Amadeus was started with its launcher (the
          Amadeus shortcut) after this update. Until then, close the launcher
          window to shut her down.
        </p>
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { getSystemStatus, requestRestart, type SystemStatus } from "./api";
import { RESTART_GIVE_UP_MS, restartFinished } from "./systemStatus";

/** Restart from Settings, page side (2026-11-01). Lives in App (always
 *  mounted), so closing Settings mid-restart still ends in the reload.
 *  The pending restart is kept in sessionStorage, so a refresh mid-restart
 *  keeps watching too. When the restart this page asked for is over, the
 *  page reloads (it picks up any new web page code) and she greets once:
 *  "amadeusGreeted" is cleared and "amadeusRestartGreet" tells the greeting
 *  that she was restarted. */

const PENDING_KEY = "amadeusRestartPending";
export const RESTART_GREET_KEY = "amadeusRestartGreet";

function readPending(): { started: number; since: number } | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    return typeof v?.started === "number" && typeof v?.since === "number" ? v : null;
  } catch {
    return null;
  }
}

function writePending(v: { started: number; since: number } | null) {
  try {
    if (v) sessionStorage.setItem(PENDING_KEY, JSON.stringify(v));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    // no storage: the restart still runs, only a refresh loses the watch
  }
}

export function useSystemStatus(active: boolean) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [pending, setPending] = useState(readPending);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState("");
  const [requesting, setRequesting] = useState(false);
  const reloading = useRef(false);

  const otherRestart = !pending && !!status?.restart &&
    (status.restart.state === "requested" || status.restart.state === "running");
  const restarting = !!pending || otherRestart;
  const gaveUp = !!pending && unreachable && now - pending.since > RESTART_GIVE_UP_MS;

  useEffect(() => {
    if (!active && !restarting) return;
    let alive = true;
    const tick = async () => {
      try {
        const s = await getSystemStatus();
        if (!alive) return;
        setStatus(s);
        setUnreachable(false);
        if (pending && !reloading.current && restartFinished(pending.started, s.restart)) {
          reloading.current = true;
          writePending(null);
          try {
            sessionStorage.removeItem("amadeusGreeted");
            sessionStorage.setItem(RESTART_GREET_KEY, "1");
          } catch {
            // no storage: she simply does not greet
          }
          window.location.reload();
        }
      } catch {
        if (alive) setUnreachable(true);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), restarting ? 1500 : 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [active, restarting, pending]);

  useEffect(() => {
    if (!restarting) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [restarting]);

  async function restart() {
    if (restarting || requesting) return;
    setError("");
    setRequesting(true);
    try {
      const r = await requestRestart();
      const p = { started: r.started, since: Date.now() };
      writePending(p);
      setPending(p);
      setNow(Date.now());
      setStatus((s) => (s ? { ...s, restart: r } : s));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not ask for a restart");
    } finally {
      setRequesting(false);
    }
  }

  return {
    status, unreachable, restarting, otherRestart, gaveUp, requesting, error,
    elapsedMs: pending ? now - pending.since : 0,
    restart,
  };
}

export type SystemStatusHook = ReturnType<typeof useSystemStatus>;

import type { PartState, RestartStatus, SystemStatus } from "./api";

/** Settings -> System (2026-11-01): what each of her parts is doing, in
 *  plain words, and when a restart this page asked for is finished. */

export type PartName = "brain" | "voice" | "memory";

export const PART_TITLES: Record<PartName, { title: string; detail: string }> = {
  brain: { title: "Her brain", detail: "chat and memory store (the backend)" },
  voice: { title: "Her voice", detail: "GPT-SoVITS" },
  memory: { title: "Her long-term memory", detail: "CharacterMemory" },
};

export type PartView = { label: string; tone: "ok" | "busy" | "bad" | "quiet" };

export function describePart(state: PartState | "unreachable"): PartView {
  switch (state) {
    case "ready": return { label: "Running", tone: "ok" };
    case "waiting": return { label: "Waiting its turn", tone: "busy" };
    case "stopping": return { label: "Stopping...", tone: "busy" };
    case "starting": return { label: "Starting...", tone: "busy" };
    case "unreachable": return { label: "Restarting...", tone: "busy" };
    case "failed": return { label: "Did not come back", tone: "bad" };
    case "off": return { label: "Not installed", tone: "quiet" };
    default: return { label: "Offline", tone: "bad" };
  }
}

/** The state shown for each part. During a restart the launcher's status
 *  wins; while her brain itself is down (no answer), it reads "Restarting"
 *  and the other parts keep their last known state. */
export function partStates(
  status: SystemStatus | null,
  restarting: boolean,
  unreachable: boolean,
): Record<PartName, PartState | "unreachable"> {
  const live = status?.restart && restarting ? status.restart.parts : status?.parts;
  const base: Record<PartName, PartState | "unreachable"> = {
    brain: live?.brain ?? "offline",
    voice: live?.voice ?? "offline",
    memory: live?.memory ?? "offline",
  };
  if (unreachable) base.brain = restarting ? "unreachable" : "offline";
  return base;
}

/** True once the restart this page asked for (started at `mine`) is over
 *  and her brain answers again: time to reload the page so she greets. */
export function restartFinished(mine: number | null, restart: RestartStatus | null): boolean {
  if (mine === null || !restart) return false;
  if (Math.abs(restart.started - mine) > 1) return false;
  return restart.state === "done" || restart.state === "partial";
}

export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** How long the page waits for her brain to answer again before it says
 *  something went wrong. */
export const RESTART_GIVE_UP_MS = 4 * 60 * 1000;

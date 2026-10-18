import type { ConnectionStatus, MemoryMessage } from "./api";

/** Where the startup greeting stands - the footer's proof of life. */
export type GreetPhase = "idle" | "waking" | "slow" | "done" | "failed";

export type FooterState = {
  text: string;
  dot: "online" | "offline" | "warning" | "neutral";
  pulse: boolean;
};

/** How long "reachable but the model is not listed" reads as "still
 *  loading" before it becomes "check the model name". */
export const NOT_LISTED_GRACE_MS = 120_000;

/**
 * What the footer says: always what the app is ACTUALLY doing, in plain
 * words. "Ready" is earned - only when the AI server answers with the
 * configured model AND the startup greeting is settled (landed, skipped
 * because the user spoke first, or failed after its retry).
 *
 * Priority: backend down > a running action ("...thinking", notices) >
 * no key > AI server problems (offline / loading / wrong model) > her
 * greeting in progress > ready.
 */
export function deriveFooter(input: {
  status: string;
  backendDown: boolean;
  missingKey: boolean;
  conn: ConnectionStatus | null;
  notListedForMs: number | null;
  greetPhase: GreetPhase;
}): FooterState {
  const { status, backendDown, missingKey, conn, notListedForMs, greetPhase } = input;
  const s = (text: string, dot: FooterState["dot"], pulse = false): FooterState =>
    ({ text, dot, pulse });

  if (backendDown) return s("Backend not running. Restart the app.", "offline");
  if (status !== "Online") return s(status, "neutral", status.endsWith("..."));
  if (missingKey) return s("No API key. Add one in Settings.", "warning");
  if (!conn) return s("Checking the AI server...", "neutral", true);
  if (conn.builtin) {
    // Her built-in model (installer option A): nothing to connect, so the
    // footer speaks about her own model, not "an AI server".
    if (conn.builtin_model_missing) {
      return s("Her built-in model is not installed. Run the installer again.", "warning");
    }
    if (conn.builtin_state === "unloaded") {
      return s("Her built-in model is not loaded. Load it in Settings.", "warning");
    }
    if (conn.builtin_state === "failed") {
      return s("Her built-in model could not load. See Settings.", "offline");
    }
    if (conn.builtin_state === "loading") return s("Her built-in model is loading...", "warning", true);
    if (!conn.reachable) return s("Her built-in model is not running. Restart Amadeus.", "offline");
    if (conn.loading || !conn.model_found) return s("Her built-in model is loading...", "warning", true);
  }
  if (!conn.reachable) return s("AI server offline. Start it, or check Settings.", "offline");
  if (conn.loading) return s("AI server is loading the model...", "warning", true);
  if (!conn.model_configured) return s("No model chosen. Pick one in Settings.", "warning");
  if (!conn.model_found) {
    if (notListedForMs === null || notListedForMs < NOT_LISTED_GRACE_MS) {
      return s("AI server is loading the model...", "warning", true);
    }
    return s(
      `AI server is up, but model "${conn.configured_model}" isn't loaded. Check the name in Settings.`,
      "warning",
    );
  }
  if (greetPhase === "idle") return s("Starting up...", "neutral", true);
  if (greetPhase === "waking") return s("Amadeus is waking up...", "warning", true);
  if (greetPhase === "slow") {
    return s("Still waking up. The model may be loading, so the first reply can be slow.", "warning", true);
  }
  const voiceOff = conn.voice_reachable === false;
  if (greetPhase === "failed") {
    return s(
      `Ready (she couldn't greet, but you can talk)${voiceOff ? " - voice offline" : ""}`,
      voiceOff ? "warning" : "online",
    );
  }
  return voiceOff ? s("Ready (voice offline)", "warning") : s("Ready", "online");
}

/** How long a first-launch line waits for the previous one to finish. */
export const CEREMONY_WAIT_MAX_MS = 120_000;

/**
 * Resolve once `busy()` has been false for `quietMs` in a row (a streamed
 * line can pause briefly between chunks), or after `maxMs` at the latest -
 * a stuck player must never hold her next line back forever. Used so a
 * first-launch line never cuts off the one she is still saying (2026-10-17,
 * user decision: the built-in model can be ready mid-intro).
 */
export async function waitUntilQuiet(
  busy: () => boolean,
  opts: { quietMs?: number; pollMs?: number; maxMs?: number;
          sleep?: (ms: number) => Promise<void>; now?: () => number } = {},
): Promise<void> {
  const quietMs = opts.quietMs ?? 800;
  const pollMs = opts.pollMs ?? 200;
  const maxMs = opts.maxMs ?? CEREMONY_WAIT_MAX_MS;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const now = opts.now ?? (() => Date.now());
  const start = now();
  let quietSince: number | null = busy() ? null : start;
  while (now() - start < maxMs) {
    if (quietSince !== null && now() - quietSince >= quietMs) return;
    await sleep(pollMs);
    if (busy()) quietSince = null;
    else if (quietSince === null) quietSince = now();
  }
}

/** First-launch WAIT lines keep the ceremony going (chat box stays locked);
 *  newer backends flag them with wait=true, older ones by name. */
export function isCeremonyWaitLine(kind: string | undefined, wait: boolean | undefined): boolean {
  return wait === true || kind === "intro" || kind === "trying" || kind === "reminder";
}

/**
 * Put a greeting line where it belongs in the pane.
 *
 * A greeting normally lands at the bottom. But if the user sent a message
 * while it was in flight, the backend stored the greeting FIRST (it drops a
 * greeting the user already spoke past), so it must sit ABOVE that newer
 * message: before the first row that is newer than it - a user message the
 * backend has not numbered yet (id still missing) or any row with a higher
 * id. The pane then matches the stored order, before and after a reload.
 */
export function placeGreeting(
  messages: MemoryMessage[],
  greeting: MemoryMessage,
): MemoryMessage[] {
  const gid = greeting.id;
  // Already showing (a history reload that landed while the greeting was
  // being shown already carried the stored row - 2026-10-18 test install:
  // the same greeting twice in a row): never add it a second time.
  if (typeof gid === "number" && messages.some((m) => m.id === gid)) return messages;
  const index =
    typeof gid === "number"
      ? messages.findIndex(
          (m) =>
            (m.role === "user" && m.id == null) ||
            (typeof m.id === "number" && m.id > gid),
        )
      : -1;
  if (index === -1) return [...messages, greeting];
  return [...messages.slice(0, index), greeting, ...messages.slice(index)];
}

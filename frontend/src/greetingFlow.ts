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

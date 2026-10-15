import assert from "node:assert/strict";
import type { ConnectionStatus, MemoryMessage } from "../src/api";
import { deriveFooter, NOT_LISTED_GRACE_MS, placeGreeting } from "../src/greetingFlow";
import type { GreetPhase } from "../src/greetingFlow";

const greeting = (id: number): MemoryMessage => ({
  role: "assistant", content: `greeting ${id}`, id, is_greeting: true,
});
const ids = (list: MemoryMessage[]) => list.map((m) => m.id ?? "new");

// Nothing newer: the greeting lands at the bottom.
assert.deepEqual(
  ids(placeGreeting([{ role: "user", content: "a", id: 1 }, { role: "assistant", content: "b", id: 2 }], greeting(3))),
  [1, 2, 3],
  "greeting goes to the bottom when nothing is newer",
);

// The user hit send while the greeting was in flight (their message has no
// id yet): the greeting goes ABOVE it - it was stored first.
assert.deepEqual(
  ids(placeGreeting([{ role: "assistant", content: "b", id: 2 }, { role: "user", content: "hi" }], greeting(3))),
  [2, 3, "new"],
  "greeting slots above a just-sent user message",
);

// The reply already came back (ids known): order follows the ids.
assert.deepEqual(
  ids(placeGreeting(
    [{ role: "assistant", content: "b", id: 2 }, { role: "user", content: "hi", id: 4 }, { role: "assistant", content: "r", id: 5 }],
    greeting(3),
  )),
  [2, 3, 4, 5],
  "greeting slots above newer stored rows",
);

// A greeting without an id (old backend) just goes to the bottom.
assert.deepEqual(
  ids(placeGreeting([{ role: "user", content: "hi" }], { role: "assistant", content: "g" })),
  ["new", "new"],
  "id-less greeting appends",
);

// ---- deriveFooter: the status bar always says what is really going on ----
const conn = (over: Partial<ConnectionStatus> = {}): ConnectionStatus => ({
  address: "http://127.0.0.1:8080/v1", configured: true, reachable: true,
  models: ["m"], configured_model: "m", model_configured: true,
  model_found: true, loading: false, voice_reachable: true, ...over,
});
const footer = (over: {
  status?: string; backendDown?: boolean; missingKey?: boolean;
  conn?: ConnectionStatus | null; notListedForMs?: number | null; greetPhase?: GreetPhase;
} = {}) => deriveFooter({
  status: "Online", backendDown: false, missingKey: false, conn: conn(),
  notListedForMs: null, greetPhase: "done", ...over,
});

assert.deepEqual(footer(), { text: "Ready", dot: "online", pulse: false }, "all good = Ready");
assert.equal(footer({ backendDown: true }).text, "Backend not running. Restart the app.");
assert.equal(footer({ backendDown: true }).dot, "offline");
assert.equal(footer({ status: "Amadeus is thinking..." }).text, "Amadeus is thinking...");
assert.equal(footer({ status: "Amadeus is thinking..." }).pulse, true);
assert.equal(footer({ missingKey: true }).text, "No API key. Add one in Settings.");
assert.equal(footer({ conn: null }).text, "Checking the AI server...");
assert.equal(footer({ conn: conn({ reachable: false }) }).dot, "offline");
assert.match(footer({ conn: conn({ reachable: false }) }).text, /AI server offline/);

const loading = footer({ conn: conn({ loading: true, model_found: false }) });
assert.equal(loading.text, "AI server is loading the model...");
assert.equal(loading.dot, "warning");
assert.equal(loading.pulse, true);
// Not listed: loading during the grace period, then "check the name".
assert.equal(footer({ conn: conn({ model_found: false }), notListedForMs: 5_000 }).text,
  "AI server is loading the model...");
assert.match(footer({ conn: conn({ model_found: false }), notListedForMs: NOT_LISTED_GRACE_MS + 1 }).text,
  /model "m" isn't loaded/);
assert.match(footer({ conn: conn({ model_configured: false, model_found: false }) }).text, /No model chosen/);

// The greeting is the proof of life: Ready is earned.
assert.equal(footer({ greetPhase: "idle" }).text, "Starting up...");
assert.equal(footer({ greetPhase: "waking" }).text, "Amadeus is waking up...");
assert.match(footer({ greetPhase: "slow" }).text, /Still waking up/);
assert.equal(footer({ greetPhase: "failed" }).text, "Ready (she couldn't greet, but you can talk)");
assert.equal(footer({ greetPhase: "failed" }).dot, "online");
// Voice engine down: still ready, but says so (amber).
assert.deepEqual(footer({ conn: conn({ voice_reachable: false }) }),
  { text: "Ready (voice offline)", dot: "warning", pulse: false });
// An older backend that does not report the voice: plain Ready.
assert.equal(footer({ conn: conn({ voice_reachable: undefined }) }).text, "Ready");
// Health problems outrank the greeting phase (no "waking up" while offline).
assert.match(footer({ conn: conn({ reachable: false }), greetPhase: "waking" }).text, /offline/);

console.log("greeting.test.ts: ok");

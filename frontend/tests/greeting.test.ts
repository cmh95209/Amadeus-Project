import assert from "node:assert/strict";
import type { ConnectionStatus, MemoryMessage } from "../src/api";
import { deriveFooter, isCeremonyWaitLine, NOT_LISTED_GRACE_MS, placeGreeting, waitUntilQuiet } from "../src/greetingFlow";
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

// The pane already shows this greeting (a history reload carried it): it
// is not added a second time.
assert.deepEqual(
  ids(placeGreeting([{ role: "user", content: "a", id: 1 }, greeting(2)], greeting(2))),
  [1, 2],
  "a greeting already in the pane is not duplicated",
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

// ---- Built-in model (installer option A): the footer names her own model ----
assert.equal(footer({ conn: conn({ builtin: true, builtin_model_missing: true }) }).text,
  "Her built-in model is not installed. Run the installer again.");
assert.equal(footer({ conn: conn({ builtin: true, reachable: false }) }).text,
  "Her built-in model is not running. Restart Amadeus.");
assert.equal(footer({ conn: conn({ builtin: true, loading: true }) }).text,
  "Her built-in model is loading...");
assert.equal(footer({ conn: conn({ builtin: true, model_found: false }) }).text,
  "Her built-in model is loading...");
assert.equal(footer({ conn: conn({ builtin: true }) }).text, "Ready");

// ---- First-launch wait lines (chat box stays locked) ----
for (const kind of ["intro", "trying", "reminder"]) {
  assert.equal(isCeremonyWaitLine(kind, undefined), true, `${kind} is a wait line by name`);
}
assert.equal(isCeremonyWaitLine("builtin_intro", true), true, "flagged built-in wait line");
assert.equal(isCeremonyWaitLine("wake", false), false);
assert.equal(isCeremonyWaitLine("builtin_fallback", false), false);
assert.equal(isCeremonyWaitLine(undefined, undefined), false);

// ---- waitUntilQuiet: a first-launch line never cuts off the previous one ----
{
  // A fake clock: sleep() just advances time.
  const run = async (busyUntil: number, opts: { maxMs?: number } = {}) => {
    let t = 0;
    let checks = 0;
    await waitUntilQuiet(() => { checks += 1; return t < busyUntil; }, {
      quietMs: 800, pollMs: 200, maxMs: opts.maxMs ?? 120_000,
      sleep: async (ms) => { t += ms; }, now: () => t,
    });
    return { t, checks };
  };
  // Not speaking: goes ahead after the short quiet window.
  assert.equal((await run(0)).t, 800, "silent player: only the quiet window");
  // Speaking for 30 s: waits until 30 s + the quiet window.
  const long = await run(30_000);
  assert.ok(long.t >= 30_800 && long.t <= 31_000, `waited ${long.t} ms`);
  // A stuck player never holds her next line back past maxMs.
  assert.equal((await run(10_000_000, { maxMs: 5_000 })).t, 5_000, "gives up at maxMs");
  // A brief gap between streamed chunks does not count as finished.
  let t = 0;
  const busy = () => t < 1_000 || (t >= 1_400 && t < 3_000);   // 400 ms gap
  await waitUntilQuiet(busy, { quietMs: 800, pollMs: 200, sleep: async (ms) => { t += ms; }, now: () => t });
  assert.ok(t >= 3_800, `a 400 ms gap mid-line is not the end (resolved at ${t})`);
}

console.log("greeting.test.ts: ok");

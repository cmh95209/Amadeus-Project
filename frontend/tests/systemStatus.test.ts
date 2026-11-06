import assert from "node:assert/strict";
import type { RestartStatus, SystemStatus } from "../src/api";
import { deriveFooter } from "../src/greetingFlow";
import { describePart, formatElapsed, partStates, restartFinished } from "../src/systemStatus";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QuitPanel, stillOpenMessage } from "../src/components/QuitPanel";

// Settings -> System (2026-11-01): plain words for each part's state.
assert.deepEqual(describePart("ready"), { label: "Running", tone: "ok" });
assert.deepEqual(describePart("starting"), { label: "Starting...", tone: "busy" });
assert.deepEqual(describePart("unreachable"), { label: "Restarting...", tone: "busy" });
assert.deepEqual(describePart("failed"), { label: "Did not come back", tone: "bad" });
assert.deepEqual(describePart("off"), { label: "Not installed", tone: "quiet" });
assert.deepEqual(describePart("offline"), { label: "Offline", tone: "bad" });

const restart = (state: RestartStatus["state"], started = 1000): RestartStatus => ({
  state, started, finished: null, message: "",
  parts: { brain: "ready", voice: "starting", memory: "starting" },
});
const status = (r: RestartStatus | null): SystemStatus => ({
  launcher: true, parts: { brain: "ready", voice: "ready", memory: "offline" }, restart: r,
});

// Normal times: the live port checks.
assert.deepEqual(partStates(status(null), false, false),
  { brain: "ready", voice: "ready", memory: "offline" });
// During a restart: the launcher's status wins.
assert.deepEqual(partStates(status(restart("running")), true, false),
  { brain: "ready", voice: "starting", memory: "starting" });
// Her brain is down on purpose: "Restarting", the rest keep their last state.
assert.deepEqual(partStates(status(restart("requested")), true, true),
  { brain: "unreachable", voice: "starting", memory: "starting" });
// Down without a restart: offline.
assert.equal(partStates(status(null), false, true).brain, "offline");
assert.equal(partStates(null, false, true).voice, "offline");

// Reload only when THIS page's restart is over.
assert.equal(restartFinished(1000, restart("done")), true);
assert.equal(restartFinished(1000, restart("partial")), true);
assert.equal(restartFinished(1000, restart("running")), false);
assert.equal(restartFinished(1000, restart("done", 2000)), false);
assert.equal(restartFinished(null, restart("done")), false);
assert.equal(restartFinished(1000, null), false);

assert.equal(formatElapsed(0), "0:00");
assert.equal(formatElapsed(65_400), "1:05");

// The footer says she is restarting, not "backend not running".
const footer = deriveFooter({
  status: "Online", backendDown: true, missingKey: false, conn: null,
  notListedForMs: null, greetPhase: "done", restarting: true,
});
assert.equal(footer.text, "Amadeus is restarting...");
assert.equal(footer.pulse, true);
assert.equal(deriveFooter({
  status: "Online", backendDown: true, missingKey: false, conn: null,
  notListedForMs: null, greetPhase: "done",
}).text, "Backend not running. Restart the app.");

// Settings -> Quit (2026-11-06): the phone only closes the app; the PC also
// offers a full shutdown; a page the browser won't close says what to do.
const phoneQuit = renderToStaticMarkup(createElement(QuitPanel, { onCancel: () => undefined, local: false }));
assert.ok(phoneQuit.includes("keeps running on your PC") && phoneQuit.includes(">Quit<"));
assert.ok(!phoneQuit.includes("Shut down Amadeus"));
const pcQuit = renderToStaticMarkup(createElement(QuitPanel, { onCancel: () => undefined, local: true }));
assert.ok(pcQuit.includes("Close this window") && pcQuit.includes("Shut down Amadeus"));
assert.match(stillOpenMessage(false, false), /Swipe it away/);
assert.match(stillOpenMessage(true, false), /Close the tab yourself/);
assert.match(stillOpenMessage(true, true), /shut down/);

console.log("systemStatus.test.ts: ok");

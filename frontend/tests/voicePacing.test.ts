import assert from "node:assert/strict";
import {
  decide,
  loadVoicePlayback,
  voiceStartsEarly,
  type PacingInput,
} from "../src/audio/voicePacing";

const base: PacingInput = {
  mode: "smooth", received: 1, ahead: 1, elapsed: 1, total: 10, ended: false, priorRate: null,
};

// A fast PC (more than real time): she starts at once, as before.
assert.equal(decide({ ...base, received: 0.3, ahead: 0.3, elapsed: 0.1, priorRate: 2 }).go, true);
assert.equal(decide({ ...base, received: 2, ahead: 2, elapsed: 1 }).go, true);

// Nothing learned yet: watch the engine for a moment first.
assert.equal(decide({ ...base, received: 0.2, ahead: 0.2, elapsed: 0.2 }).go, false);

// Half real time, 10 s line: start once making the rest finishes before
// playing it would - (10 - R) / 0.5 + 0.4 <= R  ->  R >= 5.2 s.
const slowAt = (r: number, total = 10) =>
  ({ ...base, total, received: r, ahead: r, elapsed: r / 0.5 });
assert.equal(decide(slowAt(5)).go, false);
assert.equal(decide(slowAt(5.5)).go, true);
const halfway = decide(slowAt(3.5));
assert.ok(halfway.progress > 0.4 && halfway.progress < 0.6, `progress ${halfway.progress}`);
// The same measured from the first moments, with the speed learned before.
assert.equal(decide({ ...slowAt(0.4), priorRate: 0.5 }).go, false);

// A guess that was too short never holds her back for long: the line is
// taken to be at least what already arrived.
assert.equal(decide(slowAt(3, 2)).go, true);

// Right away never waits; When fully ready waits for the end.
assert.equal(decide({ ...base, mode: "instant", received: 0.1, ahead: 0.1, elapsed: 0 }).go, true);
assert.equal(decide({ ...base, mode: "whole", priorRate: 5, received: 9, ahead: 9 }).go, false);
assert.equal(decide({ ...base, mode: "whole", ended: true }).go, true);
assert.equal(decide({ ...base, received: 0, ahead: 0, ended: true }).go, false, "an empty line has nothing to play");

// The saved choice, and the old "Start her voice early" switch moving over.
const store = (items: Record<string, string>) => ({ getItem: (k: string) => items[k] ?? null });
assert.equal(loadVoicePlayback(store({})), "smooth");
assert.equal(loadVoicePlayback(store({ amadeusVoiceEarly: "1" })), "early");
assert.equal(loadVoicePlayback(store({ amadeusVoiceEarly: "0" })), "smooth");
assert.equal(loadVoicePlayback(store({ amadeusVoicePlayback: "whole", amadeusVoiceEarly: "1" })), "whole");
assert.equal(loadVoicePlayback(store({ amadeusVoicePlayback: "nonsense" })), "smooth");
assert.equal(loadVoicePlayback({ getItem() { throw new Error("blocked"); } }), "smooth");
assert.equal(loadVoicePlayback(null), "smooth");
assert.deepEqual(["smooth", "early", "instant", "whole"].map(m => voiceStartsEarly(m as never)),
  [false, true, true, false]);

console.log("PASS: voice pacing - fast PCs start at once, slow PCs wait just enough, saved choice + old switch");

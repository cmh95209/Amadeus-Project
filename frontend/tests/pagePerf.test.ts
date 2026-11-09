import assert from "node:assert/strict";
import { MESSAGE_WINDOW, firstShownIndex } from "../src/messageWindow";
import { FRAME_MS, nextDrawAt } from "../src/live2d/frameRate";

// B: only the newest messages are drawn at first.
assert.equal(MESSAGE_WINDOW, 150);
assert.equal(firstShownIndex(34, MESSAGE_WINDOW), 0, "a short chat shows everything");
assert.equal(firstShownIndex(560, MESSAGE_WINDOW), 410, "a long chat starts with its newest 150");
assert.equal(firstShownIndex(560, MESSAGE_WINDOW * 2), 260, "each 'Show earlier' adds 150");
assert.equal(firstShownIndex(560, 10_000), 0);
assert.equal(firstShownIndex(0, MESSAGE_WINDOW), 0);
assert.equal(firstShownIndex(561, MESSAGE_WINDOW), 411, "a new message slides the window");

// D: about 60 drawings a second, whatever the screen's refresh rate.
function drawsPerSecond(hz: number): number {
  let due = 0;
  let draws = 0;
  for (let t = 0; t < 10_000; t += 1000 / hz) {
    if (t < due - 1) continue;
    due = nextDrawAt(due, t);
    draws++;
  }
  return draws / 10;
}
for (const hz of [60, 75, 120, 143, 144, 165, 240]) {
  const fps = drawsPerSecond(hz);
  assert.ok(fps >= 47 && fps <= 61, `${hz} Hz -> ${fps} drawings/s`);
}
assert.ok(Math.abs(drawsPerSecond(60) - 60) <= 1, "a 60 Hz screen keeps every frame");
assert.ok(drawsPerSecond(30) >= 29, "a slow screen is not slowed further");
assert.equal(nextDrawAt(0, 5000), 5000 + FRAME_MS, "a page that fell behind does not catch up");

console.log("PASS: page speed - newest 150 messages, ~60 drawings a second on any screen");

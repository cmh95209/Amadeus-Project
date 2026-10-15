import assert from "node:assert/strict";
import type { MemoryMessage } from "../src/api";
import { placeGreeting } from "../src/greetingFlow";

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

console.log("greeting.test.ts: ok");

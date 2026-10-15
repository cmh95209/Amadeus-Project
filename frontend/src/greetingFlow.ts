import type { MemoryMessage } from "./api";

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

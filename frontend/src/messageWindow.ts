/** How many of the newest messages a chat draws at first, and how many more
 *  each "Show earlier messages" adds. */
export const MESSAGE_WINDOW = 150;

/** Position of the first message drawn: everything before it waits behind
 *  "Show earlier messages". */
export function firstShownIndex(total: number, shown: number): number {
  return Math.max(0, total - Math.max(1, shown));
}

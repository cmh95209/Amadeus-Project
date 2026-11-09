// She is drawn at most about 60 times a second. A 120-240 Hz screen would
// otherwise redraw her 2-4 times as often for no visible gain, and every
// extra drawing takes time the page needs for typing (2026-11-09: 143 Hz
// screen, ~30% of a processor core while idle).
export const FRAME_MS = 1000 / 60;

/** When the next drawing is due: `due` = when the last one was due; a page
 *  that fell behind does not catch up. */
export function nextDrawAt(due: number, now: number): number {
  const next = due + FRAME_MS;
  return next < now ? now + FRAME_MS : next;
}

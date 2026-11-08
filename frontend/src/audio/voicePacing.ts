/**
 * When may her voice start (or carry on after running dry)?
 *
 * Her voice is made a little at a time while it plays. When the PC makes it
 * slower than she speaks (her voice on the processor, a busy PC - tester
 * report 2026-11-08), starting at the first sound means gaps all through the
 * line. "Smooth" waits just long enough that the rest of the line will be
 * ready before she needs it - like a video player buffering.
 *
 * Settings -> Preferences -> Voice playback (saved in this browser):
 *   smooth  - voice and English line together, gap-free start (default)
 *   early   - voice as soon as her Japanese line is written, gap-free start
 *   instant - voice as soon as her Japanese line is written, first sound at
 *             once (gaps on a slow PC; the old "Start her voice early")
 *   whole   - voice and English line together, the whole line made first
 */
export type VoicePlayback = "smooth" | "early" | "instant" | "whole";

export const VOICE_PLAYBACK_CHOICES: { value: VoicePlayback; title: string; desc: string }[] = [
  {
    value: "smooth",
    title: "Smooth",
    desc: "Her voice and the English line arrive together. On a slower PC she waits a moment so she speaks without gaps.",
  },
  {
    value: "early",
    title: "Early",
    desc: "Her voice starts as soon as her Japanese line is written; the English follows. Still waits a moment on a slower PC so there are no gaps.",
  },
  {
    value: "instant",
    title: "Right away",
    desc: "Her voice starts at the very first sound, as early as possible. On a slower PC her voice may stop and start.",
  },
  {
    value: "whole",
    title: "When fully ready",
    desc: "Her whole line is made first, then played in one go. The longest wait, never a gap.",
  },
];

const STORAGE_KEY = "amadeusVoicePlayback";
const OLD_EARLY_KEY = "amadeusVoiceEarly";

export function isVoicePlayback(value: unknown): value is VoicePlayback {
  return value === "smooth" || value === "early" || value === "instant" || value === "whole";
}

/** The saved choice; a browser that had "Start her voice early" on moves to "early". */
export function loadVoicePlayback(storage: Pick<Storage, "getItem"> | null = safeStorage()): VoicePlayback {
  try {
    const saved = storage?.getItem(STORAGE_KEY);
    if (isVoicePlayback(saved)) return saved;
    if (storage?.getItem(OLD_EARLY_KEY) === "1") return "early";
  } catch {
    // private window / blocked storage: the default
  }
  return "smooth";
}

export function saveVoicePlayback(value: VoicePlayback, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(STORAGE_KEY, value);
  } catch {
    // not saved; the choice still applies until the page reloads
  }
}

/** Her voice starts before the English line is there (the old "early" switch). */
export function voiceStartsEarly(mode: VoicePlayback): boolean {
  return mode === "early" || mode === "instant";
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** Seconds of speech per character of her Japanese line (learned per PC). */
export const DEFAULT_SECONDS_PER_CHAR = 0.16;
/** A rate at least this fast never waits (more than real time + headroom). */
export const FAST_ENOUGH = 1.15;
/** Seconds to watch the engine before trusting its measured speed. */
export const MEASURE_S = 0.6;
/** Spare seconds kept in hand against a slow patch. */
export const MARGIN_S = 0.4;

export type PacingInput = {
  mode: VoicePlayback;
  /** Seconds of audio received so far. */
  received: number;
  /** Seconds of audio received but not yet played. */
  ahead: number;
  /** Seconds since the first sound arrived (0 before). */
  elapsed: number;
  /** Best guess of the whole line's length in seconds (null = unknown). */
  total: number | null;
  /** The whole line has arrived. */
  ended: boolean;
  /** Speed learned on earlier lines (seconds of audio per second), or null. */
  priorRate: number | null;
};

export type PacingDecision = {
  /** Start (or carry on) now. */
  go: boolean;
  /** 0..1 how close to starting, for the "preparing her voice" bar. */
  progress: number;
};

/** How fast this line is being made: measured once there is enough to go on, else learned. */
export function currentRate(received: number, elapsed: number, priorRate: number | null): number | null {
  if (elapsed >= MEASURE_S && received > 0) {
    const measured = received / elapsed;
    // Early on, lean on what earlier lines showed; the measurement wins as it grows.
    if (priorRate && elapsed < 2) {
      const w = (elapsed - MEASURE_S) / (2 - MEASURE_S);
      return priorRate * (1 - w) + measured * w;
    }
    return measured;
  }
  return priorRate;
}

export function decide(input: PacingInput): PacingDecision {
  const { mode, received, ahead, elapsed, ended, priorRate } = input;
  if (ended) return { go: ahead > 0 || received > 0, progress: 1 };
  if (received <= 0) return { go: false, progress: 0 };
  if (mode === "instant") return { go: ahead > 0, progress: 1 };

  // The line is at least as long as what already arrived.
  const total = Math.max(input.total ?? 0, received + 0.5);
  const remaining = total - received;
  if (mode === "whole") {
    return { go: false, progress: Math.min(0.97, received / total) };
  }

  const rate = currentRate(received, elapsed, priorRate);
  if (rate === null) {
    // Nothing learned yet: watch the engine for a moment first.
    return { go: false, progress: Math.min(0.3, elapsed / MEASURE_S * 0.3) };
  }
  if (rate >= FAST_ENOUGH) return { go: ahead > 0, progress: 1 };

  // Playing from now lasts ahead + remaining seconds; making the rest takes
  // remaining / rate. Start once making the rest finishes first.
  const needAhead = remaining / Math.max(rate, 0.05) + MARGIN_S - remaining;
  if (ahead >= needAhead) return { go: true, progress: 1 };
  return { go: false, progress: Math.max(0, Math.min(0.97, ahead / needAhead)) };
}

/** Running average for a learned value (rate, seconds per character). */
export function blend(previous: number | null, value: number, weight = 0.35): number {
  if (previous === null || !Number.isFinite(previous)) return value;
  return previous * (1 - weight) + value * weight;
}

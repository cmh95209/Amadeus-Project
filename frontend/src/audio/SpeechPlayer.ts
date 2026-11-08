import {
  DEFAULT_SECONDS_PER_CHAR,
  MARGIN_S,
  blend,
  decide,
  neededLead,
  type VoicePlayback,
} from "./voicePacing";

export type SpeechPlayerCallbacks = {
  onSpeakingChange: (speaking: boolean) => void;
  onAmplitude: (value: number) => void;
  onError?: (message: string) => void;
  /** 0..1 while her voice is being prepared before it can play; null when not waiting. */
  onPreparing?: (progress: number | null) => void;
};

const PLAYBACK_FAILED = "Speech playback failed. Check the TTS server and try again.";
// Learned per PC (this browser): how fast her voice is made, and how long a
// character of her Japanese line takes to say.
const RATE_KEY = "amadeusVoiceRate";
const SECONDS_PER_CHAR_KEY = "amadeusVoiceSecPerChar";
// Head start a line needed (seconds after the first sound, per second of
// audio) so no sentence break ran dry - see voicePacing.neededLead.
const LEAD_KEY = "amadeusVoiceLead";
// A wait shorter than this shows no "preparing" bar (no flicker on a fast PC).
const SHOW_PREPARING_MS = 400;
// Scheduling slack when (re)starting: the first piece plays this far ahead.
const START_LEAD_S = 0.04;

type WavFormat = { channels: number; sampleRate: number; frameBytes: number; dataOffset: number };

type Line = {
  generation: number;
  format: WavFormat | null;
  /** Raw bytes until the WAV header is understood (or the whole file, if it never is). */
  head: Uint8Array;
  /** Not a 16-bit PCM WAV: collect everything and decode at the end. */
  wholeFile: boolean;
  carry: Uint8Array;
  pending: Float32Array[][];
  pendingFrames: number;
  receivedFrames: number;
  firstAt: number | null;
  lastAt: number | null;
  chars: number | null;
  contentLength: number | null;
  playing: boolean;
  started: boolean;
  /** Ran dry at least once: the length guess was too short. */
  ranDry: boolean;
  /** [seconds since the first sound, seconds received] after each piece. */
  arrivals: [number, number][];
  ended: boolean;
  waitingSince: number;
  preparingShown: boolean;
  resolveStart: () => void;
  rejectStart: (error: Error) => void;
};

/**
 * Plays her streamed speech through Web Audio and exposes a smoothed 0..1
 * amplitude for the Live2D lip sync.
 *
 * The line arrives as a WAV stream that the voice engine is still making.
 * The player reads it itself (instead of handing the address to an <audio>
 * element), so it decides WHEN to start: at once, once enough is ready that
 * the rest will arrive in time (voicePacing.ts), or when the whole line is
 * there - Settings -> Preferences -> Voice playback.
 */
export class SpeechPlayer {
  private readonly callbacks: SpeechPlayerCallbacks;

  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private samples: Uint8Array<ArrayBuffer> | null = null;

  private mode: VoicePlayback = "smooth";
  private line: Line | null = null;
  private abort: AbortController | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private nextTime = 0;

  private animationFrame = 0;
  private speaking = false;
  private smoothedAmplitude = 0;
  private destroyed = false;
  // A line is loading or playing: set by play(), cleared only when it ends,
  // fails or is stopped - NOT by a mid-line pause while more is made, so a
  // streamed line counts as busy from request to last word.
  private busy = false;
  private generation = 0;
  private lastFrameTime = 0;

  constructor(callbacks: SpeechPlayerCallbacks) {
    this.callbacks = callbacks;
  }

  setPlayback(mode: VoicePlayback): void {
    this.mode = mode;
  }

  /** Call from a user gesture (Send click/Enter) to unlock Web Audio. */
  async prepare(): Promise<void> {
    if (this.destroyed) return;

    if (!this.context) {
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0;
      analyser.connect(context.destination);

      this.context = context;
      this.analyser = analyser;
      this.samples = new Uint8Array(analyser.fftSize);
    }

    if (this.context.state === "suspended") {
      await this.context.resume();
    }
  }

  /** Resolves once she is heard (or the line ended silent); rejects if it cannot be fetched. */
  async play(url: string): Promise<void> {
    if (this.destroyed) return;
    this.stop();
    const generation = this.generation;
    await this.prepare();
    if (this.destroyed || generation !== this.generation) return;

    this.busy = true;
    const abort = new AbortController();
    this.abort = abort;

    let response: Response;
    try {
      response = await fetch(url, { signal: abort.signal });
    } catch (error) {
      if (this.isStale(generation)) return;
      this.fail();
      throw error instanceof Error ? error : new Error(PLAYBACK_FAILED);
    }
    if (this.isStale(generation)) return;
    if (!response.ok || !response.body) {
      this.fail();
      throw new Error(PLAYBACK_FAILED);
    }

    const chars = Number(response.headers.get("X-Amadeus-Voice-Chars"));
    const length = Number(response.headers.get("Content-Length"));
    const reader = response.body.getReader();

    return new Promise<void>((resolve, reject) => {
      const line: Line = {
        generation,
        format: null,
        head: new Uint8Array(0),
        wholeFile: false,
        carry: new Uint8Array(0),
        pending: [],
        pendingFrames: 0,
        receivedFrames: 0,
        firstAt: null,
        lastAt: null,
        chars: Number.isFinite(chars) && chars > 0 ? chars : null,
        contentLength: Number.isFinite(length) && length > 0 ? length : null,
        playing: false,
        started: false,
        ranDry: false,
        arrivals: [],
        ended: false,
        waitingSince: performance.now(),
        preparingShown: false,
        resolveStart: resolve,
        rejectStart: reject,
      };
      this.line = line;
      void this.pump(line, reader);
    });
  }

  /** Is a line loading or still being spoken? */
  isBusy(): boolean {
    return this.busy && !this.destroyed;
  }

  stop(): void {
    this.generation++;
    this.busy = false;
    this.abort?.abort();
    this.abort = null;
    const line = this.line;
    this.line = null;
    if (line) {
      this.showPreparing(line, null);
      // A caller still awaiting the start: it was stopped, not broken.
      line.resolveStart();
    }
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // already stopped
      }
      source.disconnect();
    }
    this.sources.clear();
    this.nextTime = 0;
    this.finishSpeaking();
  }

  private isStale(generation: number): boolean {
    return this.destroyed || generation !== this.generation;
  }

  private fail(message?: string): void {
    this.busy = false;
    this.line = null;
    this.finishSpeaking();
    if (message && !this.destroyed) this.callbacks.onError?.(message);
  }

  private async pump(line: Line, reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> {
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (this.isStale(line.generation)) {
          void reader.cancel().catch(() => undefined);
          return;
        }
        if (done) break;
        if (value && value.length) this.receive(line, value);
      }
      line.ended = true;
      if (line.wholeFile || !line.format) {
        await this.decodeWhole(line);
      } else {
        this.learn(line);
        this.advance(line);
      }
    } catch (error) {
      if (this.isStale(line.generation)) return;
      this.showPreparing(line, null);
      if (line.started) {
        // Cut off mid-line: whatever is already scheduled plays out.
        line.ended = true;
        if (this.sources.size === 0) this.endLine(line);
      } else {
        this.fail(PLAYBACK_FAILED);
        line.rejectStart(error instanceof Error ? error : new Error(PLAYBACK_FAILED));
      }
    }
  }

  private receive(line: Line, chunk: Uint8Array): void {
    if (line.wholeFile || !line.format) {
      line.head = concat(line.head, chunk);
      if (line.wholeFile) return;
      const parsed = parseWavHeader(line.head);
      if (parsed === "need-more") return;
      if (parsed === null) {
        line.wholeFile = true;
        return;
      }
      line.format = parsed;
      const rest = line.head.subarray(parsed.dataOffset);
      line.head = new Uint8Array(0);
      if (rest.length) this.addPcm(line, rest);
    } else {
      this.addPcm(line, chunk);
    }
    this.advance(line);
  }

  private addPcm(line: Line, bytes: Uint8Array): void {
    const format = line.format!;
    const all = line.carry.length ? concat(line.carry, bytes) : bytes;
    const frames = Math.floor(all.length / format.frameBytes);
    line.carry = all.slice(frames * format.frameBytes);
    if (!frames) return;
    const view = new DataView(all.buffer, all.byteOffset, frames * format.frameBytes);
    const channels: Float32Array[] = [];
    for (let c = 0; c < format.channels; c++) channels.push(new Float32Array(frames));
    for (let i = 0; i < frames; i++) {
      for (let c = 0; c < format.channels; c++) {
        channels[c][i] = view.getInt16((i * format.channels + c) * 2, true) / 32768;
      }
    }
    line.pending.push(channels);
    line.pendingFrames += frames;
    line.receivedFrames += frames;
    const now = performance.now();
    if (line.firstAt === null) line.firstAt = now;
    line.lastAt = now;
    line.arrivals.push([(now - line.firstAt) / 1000, line.receivedFrames / format.sampleRate]);
  }

  /** Start, carry on, or keep waiting - and keep the "preparing" bar current. */
  private advance(line: Line): void {
    if (this.isStale(line.generation) || !line.format || !this.context) return;
    if (line.playing) {
      this.flush(line);
      return;
    }
    const rate = line.format.sampleRate;
    const ahead = Math.max(0, this.nextTime - this.context.currentTime) + line.pendingFrames / rate;
    const total = this.estimateTotal(line);
    const leadRatio = readNumber(LEAD_KEY);
    const decision = decide({
      mode: this.mode,
      received: line.receivedFrames / rate,
      ahead,
      elapsed: line.firstAt === null ? 0 : (performance.now() - line.firstAt) / 1000,
      total,
      ended: line.ended,
      priorRate: readNumber(RATE_KEY),
      // Only at the start of a line (a dry spell mid-line uses the speed).
      // A fast PC needs (almost) none: no wait at all below 0.15 s.
      lead: !line.ranDry ? leadFor(leadRatio, total ?? line.receivedFrames / rate) : null,
    });
    if (decision.go) {
      this.showPreparing(line, null);
      line.playing = true;
      this.flush(line);
    } else {
      this.showPreparing(line, decision.progress);
    }
    if (line.ended && line.pendingFrames === 0 && this.sources.size === 0) this.endLine(line);
  }

  private flush(line: Line): void {
    const context = this.context;
    const analyser = this.analyser;
    if (!context || !analyser || !line.format || line.pendingFrames === 0) return;
    const { channels, sampleRate } = line.format;
    const buffer = context.createBuffer(channels, line.pendingFrames, sampleRate);
    for (let c = 0; c < channels; c++) {
      const target = buffer.getChannelData(c);
      let offset = 0;
      for (const piece of line.pending) {
        target.set(piece[c], offset);
        offset += piece[c].length;
      }
    }
    line.pending = [];
    line.pendingFrames = 0;
    this.schedule(line, buffer);
  }

  private schedule(line: Line, buffer: AudioBuffer): void {
    const context = this.context!;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser!);
    const at = Math.max(this.nextTime, context.currentTime + START_LEAD_S);
    source.start(at);
    this.nextTime = at + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.pieceEnded(line, source);
    if (!line.started) {
      line.started = true;
      line.resolveStart();
    }
    this.startSpeaking();
  }

  private pieceEnded(line: Line, source: AudioBufferSourceNode): void {
    this.sources.delete(source);
    source.disconnect();
    if (this.isStale(line.generation) || this.sources.size > 0) return;
    if (line.ended && line.pendingFrames === 0) {
      this.endLine(line);
      return;
    }
    // Ran dry: more is still being made. Mouth closed; wait as at the start,
    // and stop trusting the length guess (it was too short).
    line.playing = false;
    line.ranDry = true;
    line.waitingSince = performance.now();
    this.finishSpeaking();
    this.advance(line);
  }

  /** Not a plain 16-bit WAV stream (or no header at all): decode it whole. */
  private async decodeWhole(line: Line): Promise<void> {
    const context = this.context;
    if (!context || !line.head.length) {
      this.showPreparing(line, null);
      this.endLine(line);
      return;
    }
    let buffer: AudioBuffer;
    try {
      buffer = await context.decodeAudioData(line.head.slice().buffer);
    } catch {
      if (this.isStale(line.generation)) return;
      this.showPreparing(line, null);
      this.fail(PLAYBACK_FAILED);
      line.rejectStart(new Error(PLAYBACK_FAILED));
      return;
    }
    if (this.isStale(line.generation)) return;
    this.showPreparing(line, null);
    line.playing = true;
    this.schedule(line, buffer);
  }

  private estimateTotal(line: Line): number | null {
    const format = line.format;
    if (!format) return null;
    if (line.contentLength !== null) {
      return Math.max(0, line.contentLength - format.dataOffset) / (format.frameBytes * format.sampleRate);
    }
    let guess = line.chars !== null
      ? line.chars * (readNumber(SECONDS_PER_CHAR_KEY) ?? DEFAULT_SECONDS_PER_CHAR)
      : null;
    if (line.ranDry) {
      // The guess was too short: count on at least half as much again as
      // what already arrived.
      const received = line.receivedFrames / format.sampleRate;
      guess = Math.max(guess ?? 0, received * 1.5);
    }
    return guess;
  }

  /** Remember this PC's voice speed and speech length (streamed lines only). */
  private learn(line: Line): void {
    if (line.contentLength !== null || !line.format || line.firstAt === null || line.lastAt === null) return;
    const seconds = line.receivedFrames / line.format.sampleRate;
    const making = (line.lastAt - line.firstAt) / 1000;
    if (seconds >= 1.5 && making > 0.2) {
      writeNumber(RATE_KEY, blend(readNumber(RATE_KEY), seconds / making));
    }
    if (seconds >= 1 && line.chars) {
      writeNumber(SECONDS_PER_CHAR_KEY, blend(readNumber(SECONDS_PER_CHAR_KEY), seconds / line.chars));
    }
    if (seconds >= 1.5) {
      // Careful on purpose: a bigger head start wins at once, a smaller one
      // only half way (one lucky line must not bring the gaps back).
      const ratio = neededLead(line.arrivals) / seconds;
      const before = readNumber(LEAD_KEY);
      const next = before === null ? ratio : Math.max(ratio, blend(before, ratio, 0.5));
      // 0 is never stored (readNumber ignores it): keep a tiny value instead.
      writeNumber(LEAD_KEY, Math.max(next, 0.0001));
    }
  }

  private showPreparing(line: Line, progress: number | null): void {
    if (progress === null) {
      if (line.preparingShown) {
        line.preparingShown = false;
        this.callbacks.onPreparing?.(null);
      }
      return;
    }
    // Right away never waits on purpose: no bar for its dry spells.
    if (this.mode === "instant") return;
    // Nothing heard from the engine yet: that wait is "she is thinking", not
    // a slow voice. Count from the first sound (or the dry spell).
    if (!line.preparingShown) {
      if (line.firstAt === null) return;
      if (performance.now() - Math.max(line.waitingSince, line.firstAt) < SHOW_PREPARING_MS) return;
    }
    line.preparingShown = true;
    this.callbacks.onPreparing?.(progress);
  }

  private endLine(line: Line): void {
    if (this.line === line) this.line = null;
    this.busy = false;
    this.abort = null;
    this.showPreparing(line, null);
    this.nextTime = 0;
    this.finishSpeaking();
    // A line with no sound at all still settles its caller.
    line.resolveStart();
  }

  private startSpeaking(): void {
    if (this.destroyed || this.speaking) return;
    this.speaking = true;
    this.lastFrameTime = performance.now();
    this.smoothedAmplitude = 0;
    this.callbacks.onSpeakingChange(true);
    this.updateAmplitude();
  }

  private updateAmplitude = (): void => {
    if (
      this.destroyed ||
      !this.speaking ||
      !this.analyser ||
      !this.samples
    ) {
      return;
    }

    this.analyser.getByteTimeDomainData(this.samples);

    let sumSquares = 0;
    for (const sample of this.samples) {
      const normalized = (sample - 128) / 128;
      sumSquares += normalized * normalized;
    }

    const rms = Math.sqrt(sumSquares / this.samples.length);
    const target = Math.min(1, Math.max(0, rms - 0.012) * 11);
    const now = performance.now();
    const dt = Math.min((now - this.lastFrameTime) / 1000, 0.1);
    this.lastFrameTime = now;
    const smoothing = 1 - Math.exp(-dt / (target > this.smoothedAmplitude ? 0.028 : 0.067));
    this.smoothedAmplitude +=
      (target - this.smoothedAmplitude) * smoothing;

    this.callbacks.onAmplitude(this.smoothedAmplitude);
    this.animationFrame = requestAnimationFrame(this.updateAmplitude);
  };

  private finishSpeaking(): void {
    cancelAnimationFrame(this.animationFrame);
    this.animationFrame = 0;
    this.smoothedAmplitude = 0;
    this.callbacks.onAmplitude(0);

    if (this.speaking) {
      this.speaking = false;
      this.callbacks.onSpeakingChange(false);
    }
  }

  destroy(): void {
    if (this.destroyed) return;

    this.stop();
    this.destroyed = true;

    this.analyser?.disconnect();
    this.analyser = null;
    this.samples = null;

    if (this.context) {
      void this.context.close();
      this.context = null;
    }
  }
}

/** Seconds to wait after the first sound for a line of `total` seconds. */
function leadFor(ratio: number | null, total: number): number | null {
  if (ratio === null) return null;
  const lead = ratio * total;
  return lead < 0.15 ? null : lead + MARGIN_S;
}

function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/**
 * The format of a 16-bit PCM WAV header; "need-more" until the start of the
 * sound data has arrived; null when this is not a 16-bit PCM WAV (decoded
 * whole instead). The size fields are ignored: a streamed line says "0" or
 * "0xFFFFFFFF" there (backend/tts.py _browser_stream).
 */
export function parseWavHeader(head: Uint8Array): WavFormat | null | "need-more" {
  if (head.length < 12) return "need-more";
  const ascii = (at: number) => String.fromCharCode(head[at], head[at + 1], head[at + 2], head[at + 3]);
  if (ascii(0) !== "RIFF" || ascii(8) !== "WAVE") return null;
  const view = new DataView(head.buffer, head.byteOffset, head.length);
  let pos = 12;
  let format: Omit<WavFormat, "dataOffset"> | null = null;
  while (pos + 8 <= head.length) {
    const id = ascii(pos);
    const size = view.getUint32(pos + 4, true);
    if (id === "fmt ") {
      if (pos + 8 + 16 > head.length) return "need-more";
      const audioFormat = view.getUint16(pos + 8, true);
      const channels = view.getUint16(pos + 10, true);
      const sampleRate = view.getUint32(pos + 12, true);
      const bits = view.getUint16(pos + 22, true);
      if ((audioFormat !== 1 && audioFormat !== 0xfffe) || bits !== 16 || !channels || !sampleRate) return null;
      format = { channels, sampleRate, frameBytes: channels * 2 };
    } else if (id === "data") {
      return format ? { ...format, dataOffset: pos + 8 } : null;
    }
    pos += 8 + size + (size % 2);
    if (pos > 4096) return null;
  }
  return head.length > 4096 ? null : "need-more";
}

function readNumber(key: string): number | null {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function writeNumber(key: string, value: number): void {
  try {
    if (Number.isFinite(value) && value > 0) localStorage.setItem(key, value.toFixed(4));
  } catch {
    // not saved: next line starts from the defaults again
  }
}

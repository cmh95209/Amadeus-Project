import assert from "node:assert/strict";
import { SpeechPlayer, parseWavHeader } from "../src/audio/SpeechPlayer";

// A Web Audio stand-in: pieces "play" when the test says time has passed.
class FakeSource {
  buffer: { duration: number } | null = null;
  onended: (() => void) | null = null;
  at = 0;
  stopped = false;
  connect() {}
  disconnect() {}
  start(at: number) { this.at = at; FakeContext.instance.started.push(this); }
  stop() { this.stopped = true; }
}
let resume: (() => void) | undefined;
let deferResume = false;
class FakeContext {
  static instance: FakeContext;
  state = "suspended";
  currentTime = 0;
  destination = {};
  started: FakeSource[] = [];
  constructor() { FakeContext.instance = this; }
  createAnalyser() {
    return { fftSize: 256, connect() {}, disconnect() {},
      getByteTimeDomainData(samples: Uint8Array) { samples.fill(150); } };
  }
  createBuffer(channels: number, frames: number, sampleRate: number) {
    const data = Array.from({ length: channels }, () => new Float32Array(frames));
    return { duration: frames / sampleRate, getChannelData: (c: number) => data[c] };
  }
  createBufferSource() { return new FakeSource(); }
  async resume() {
    if (deferResume) await new Promise<void>(resolve => { resume = resolve; });
    this.state = "running";
  }
  async close() { this.state = "closed"; }
  /** Let `seconds` pass: every piece that is over ends. */
  pass(seconds: number) {
    this.currentTime += seconds;
    for (const s of [...this.started]) {
      if (!s.stopped && s.buffer && s.at + s.buffer.duration <= this.currentTime + 1e-9) {
        this.started.splice(this.started.indexOf(s), 1);
        s.onended?.();
      }
    }
  }
}

const RATE = 32000;
function wavHeader(): Uint8Array {
  const b = new Uint8Array(44);
  const v = new DataView(b.buffer);
  const put = (at: number, s: string) => { for (let i = 0; i < 4; i++) b[at + i] = s.charCodeAt(i); };
  put(0, "RIFF"); v.setUint32(4, 0xffffffff, true); put(8, "WAVE");
  put(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, RATE, true); v.setUint32(28, RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  put(36, "data"); v.setUint32(40, 0xffffffff, true);
  return b;
}
const pcm = (seconds: number) => new Uint8Array(Math.round(seconds * RATE) * 2);

// A /speech stream the test feeds piece by piece.
let feed: ReadableStreamDefaultController<Uint8Array>;
let fetchStatus = 200;
let now = 0;
const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));

const globals = globalThis as any;
const original = {
  AudioContext: globals.AudioContext, fetch: globals.fetch,
  raf: globals.requestAnimationFrame, caf: globals.cancelAnimationFrame,
  performance: Object.getOwnPropertyDescriptor(globalThis, "performance"),
};
globals.AudioContext = FakeContext;
globals.fetch = async () => {
  const body = new ReadableStream<Uint8Array>({ start(c) { feed = c; } });
  return new Response(body, { status: fetchStatus, headers: { "X-Amadeus-Voice-Chars": "25" } });
};
let frame: (() => void) | undefined;
globals.requestAnimationFrame = (callback: () => void) => { frame = callback; return 1; };
globals.cancelAnimationFrame = () => { frame = undefined; };
Object.defineProperty(globalThis, "performance", { value: { now: () => now }, configurable: true });

try {
  // -- the WAV header ------------------------------------------------------
  assert.equal(parseWavHeader(wavHeader().subarray(0, 20)), "need-more");
  assert.deepEqual(parseWavHeader(wavHeader()), { channels: 1, sampleRate: RATE, frameBytes: 2, dataOffset: 44 });
  assert.equal(parseWavHeader(new TextEncoder().encode("ID3 not a wav file")), null);
  const deep = wavHeader(); new DataView(deep.buffer).setUint16(34, 24, true);
  assert.equal(parseWavHeader(deep), null, "24-bit sound is decoded whole instead");

  let speaking = false;
  let amplitude = 0;
  const preparing: (number | null)[] = [];
  const player = new SpeechPlayer({
    onSpeakingChange: value => { speaking = value; },
    onAmplitude: value => { amplitude = value; },
    onPreparing: value => { preparing.push(value); },
  });

  // -- Right away: the first sound plays at once; a dry spell shuts her mouth
  player.setPlayback("instant");
  now = 0;
  let started = player.play("http://speech/a");
  await tick(); await tick();
  feed.enqueue(wavHeader());
  feed.enqueue(pcm(0.5));
  await started;
  assert.equal(speaking, true);
  assert.equal(player.isBusy(), true);
  frame?.();
  assert.ok(amplitude >= 0 && amplitude <= 1);
  FakeContext.instance.pass(1);              // ran dry, more still coming
  assert.equal(speaking, false);
  assert.equal(amplitude, 0);
  assert.equal(player.isBusy(), true, "a pause mid-line is still busy");
  feed.enqueue(pcm(0.5));
  await tick();
  assert.equal(speaking, true, "carries on as soon as more arrives");
  feed.close();
  await tick();
  FakeContext.instance.pass(1);
  assert.equal(speaking, false);
  assert.equal(player.isBusy(), false);
  assert.deepEqual(preparing, [], "Right away never shows the bar");

  // -- When fully ready: nothing plays until the whole line is there --------
  player.setPlayback("whole");
  now = 0;
  started = player.play("http://speech/b");
  await tick(); await tick();
  feed.enqueue(wavHeader());
  for (let i = 0; i < 4; i++) {
    now += 500;
    feed.enqueue(pcm(0.5));
    await tick();
  }
  assert.equal(speaking, false, "still waiting for the end");
  const shown = preparing.filter((p): p is number => p !== null);
  assert.ok(shown.length > 0 && shown.every(p => p >= 0 && p < 1), "the bar fills while she waits");
  assert.ok(shown[shown.length - 1] > shown[0], "and grows");
  feed.close();
  await started;
  assert.equal(speaking, true);
  assert.equal(preparing[preparing.length - 1], null, "the bar goes away when she speaks");
  player.stop();
  assert.equal(speaking, false);

  // -- Smooth on a slow PC (half real time): waits, then never runs dry -----
  player.setPlayback("smooth");
  preparing.length = 0;
  now = 0;
  FakeContext.instance.currentTime = 0;
  started = player.play("http://speech/c");
  await tick(); await tick();
  feed.enqueue(wavHeader());
  let startedAfter: number | null = null;
  void started.then(() => { startedAfter = now; });
  // 25 characters x 0.16 s = a 4 s line; the engine makes 0.25 s per 500 ms.
  for (let i = 0; i < 16 && startedAfter === null; i++) {
    feed.enqueue(pcm(0.25));
    await tick();
    now += 500;
  }
  assert.ok(startedAfter !== null, "she starts before the line is complete");
  assert.ok(startedAfter! >= 1000, "but not at the first sound");
  player.stop();
  assert.equal(player.isBusy(), false);
  assert.equal(preparing[preparing.length - 1] ?? null, null, "stopping clears the bar");

  // -- a line that cannot be fetched -----------------------------------------
  fetchStatus = 502;
  await assert.rejects(player.play("http://speech/broken"));
  assert.equal(player.isBusy(), false);
  fetchStatus = 200;

  player.destroy();
  player.destroy();

  // -- destroyed while the browser unlocks audio: nothing plays later -------
  deferResume = true;
  const pending = new SpeechPlayer({ onSpeakingChange() {}, onAmplitude() {} });
  let fetched = false;
  const realFetch = globals.fetch;
  globals.fetch = async (...args: unknown[]) => { fetched = true; return realFetch(...args); };
  const playing = pending.play("http://speech/stale");
  pending.destroy();
  resume?.();
  await playing;
  assert.equal(fetched, false, "destroy prevents delayed playback from starting");
  console.log("PASS: speech pacing (right away / whole / smooth), dry spells, bar, stop, failure, destruction during audio unlock");
} finally {
  globals.AudioContext = original.AudioContext;
  globals.fetch = original.fetch;
  globals.requestAnimationFrame = original.raf;
  globals.cancelAnimationFrame = original.caf;
  if (original.performance) Object.defineProperty(globalThis, "performance", original.performance);
}

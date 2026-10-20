import assert from "node:assert/strict";
import { readVoiceFirstStream } from "../src/api";

// Voice-first replies stream two events: "voice" (start her voice) and later
// "text" (the English line). The turn must be handed back at the voice event,
// not when the whole stream ends (2026-10-20: the voice waited for English).
const frame = (event: object) => `data: ${JSON.stringify(event)}\n\n`;
const encoder = new TextEncoder();

function slowStream(parts: Array<[number, string]>, failAtEnd = false): Response {
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const [delay, text] of parts) {
        await new Promise((r) => setTimeout(r, delay));
        controller.enqueue(encoder.encode(text));
      }
      if (failAtEnd) controller.error(new Error("connection dropped"));
      else controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

const voice = { phase: "voice", speech_id: "abc", user_id: 1, assistant_id: 2,
  conversation_id: 3, japanese: "こんにちは" };

// 1. The turn arrives long before the English line.
{
  const started = Date.now();
  const turn = await readVoiceFirstStream(slowStream([
    [0, frame(voice)],
    [400, frame({ phase: "text", assistant_id: 2, response: "Hello" })],
  ]));
  const handedBack = Date.now() - started;
  assert.ok(handedBack < 300, `turn came back after ${handedBack} ms`);
  assert.equal(turn.reply.japanese, "こんにちは");
  assert.ok(turn.reply.speechUrl?.endsWith("/speech/abc"));
  assert.equal(await turn.text, "Hello");
}

// 2. Stream drops after the voice event: the English box gets her Japanese line.
{
  const turn = await readVoiceFirstStream(slowStream([[0, frame(voice)]], true));
  assert.equal(await turn.text, "こんにちは");
}

// 3. No voice event at all: an error, as before.
await assert.rejects(readVoiceFirstStream(slowStream([[0, ": nothing\n\n"]])),
  /ended before the voice event/);

console.log("voiceFirst.test.ts: ok");

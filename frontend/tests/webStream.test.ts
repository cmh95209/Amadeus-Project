import assert from "node:assert/strict";
import { readVoiceFirstStream } from "../src/api";
import type { MessageReply } from "../src/api";

// Web-ON replies stream (2026-10-25): status steps and her hold-on line go to
// the hooks as they happen; "reply" carries the whole reply + sources; an
// "error" event fails the turn.
const frame = (event: object) => `data: ${JSON.stringify(event)}\n\n`;
const encoder = new TextEncoder();

function stream(parts: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const text of parts) {
        await new Promise((r) => setTimeout(r, 5));
        controller.enqueue(encoder.encode(text));
      }
      controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

// 1. Steps, the hold-on line, then the reply - in order; split frames survive.
{
  const steps: string[] = [];
  const asides: MessageReply[] = [];
  const reply = frame({
    phase: "reply", response: "Skirk and Escoffier.", japanese: "スカークとエスコフィエよ。",
    speech_id: "r1", user_id: 7, assistant_id: 9, conversation_id: 1,
    sources: [{ kind: "search", query: "Genshin banner", read: "https://a.example/",
      used: ["https://a.example/"], results: [{ title: "A", url: "https://a.example/" },
        { title: "Evil", url: "javascript:alert(1)" }] }],
  });
  const turn = await readVoiceFirstStream(stream([
    frame({ phase: "status", step: "searching", text: 'Searching the web: "Genshin banner"' }),
    ": keep-alive\n\n",
    frame({ phase: "aside", response: "Hang on, let me check.", japanese: "ちょっと待ってて。",
      speech_id: "a1", assistant_id: 8 }),
    frame({ phase: "status", step: "reading", text: "Reading a.example..." }),
    reply.slice(0, 30), reply.slice(30),
  ]), {
    onStatus: (text) => steps.push(text),
    onAside: (aside) => asides.push(aside),
  });
  assert.deepEqual(steps, ['Searching the web: "Genshin banner"', "Reading a.example..."]);
  assert.equal(asides.length, 1);
  assert.equal(asides[0].response, "Hang on, let me check.");
  assert.equal(asides[0].assistantId, 8);
  assert.match(asides[0].speechUrl ?? "", /\/speech\/a1$/);
  assert.equal(turn.reply.response, "Skirk and Escoffier.");
  assert.equal(turn.reply.userId, 7);
  assert.equal(turn.reply.assistantId, 9);
  assert.equal(await turn.text, "Skirk and Escoffier.");
  const sources = turn.reply.sources ?? [];
  assert.equal(sources.length, 1);
  assert.ok(sources[0].kind === "search" && sources[0].results.length === 1);   // bad link dropped
}

// 2. An error event fails the turn with the server's message.
{
  await assert.rejects(
    readVoiceFirstStream(stream([
      frame({ phase: "status", step: "searching", text: "Searching..." }),
      frame({ phase: "error", message: "Your model server did not answer." }),
    ])),
    /Your model server did not answer/,
  );
}

// 3. A stream that ends without a reply fails too (never a silent empty bubble).
{
  await assert.rejects(readVoiceFirstStream(stream([
    frame({ phase: "status", step: "searching", text: "Searching..." }),
  ])));
}

console.log("webStream.test.ts: ok");

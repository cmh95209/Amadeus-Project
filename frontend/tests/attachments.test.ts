import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  DESK_TOOLTIP,
  PASTE_MAX_CHARS,
  PRIVACY_NOTE,
  absoluteUrl,
  canSend,
  filesFrom,
  herReplyIds,
  infoLines,
  isDocument,
  isVideo,
  isVideoFile,
  namedFile,
  pagesText,
  pastedTextFile,
  quickProblem,
  readsLine,
  readyIds,
  speechNote,
  videoCardState,
  withKnownPictures,
} from "../src/attachments";
import type { Capabilities, PictureInfo, TrayCard } from "../src/attachments";
import { AttachmentTray, trayState } from "../src/components/AttachmentTray";
import { cardFrom } from "../src/useAttachments";
import { PictureStrip } from "../src/components/PictureStrip";
import { AttachMenu } from "../src/components/AttachMenu";

// Pictures you show her (attachments Phase 2, 2026-10-26): the tray rules,
// the info panel, and what the tray / desk / bubble show.

const card = (state: TrayCard["state"], id?: number): TrayCard =>
  ({ key: `k${id ?? state}`, name: "cat.jpg", state, ...(id ? { id } : {}) });

// --- sending: words OR a ready picture, never while one is still preparing.
assert.equal(canSend("", []), false);
assert.equal(canSend("  ", [card("error")]), false);
assert.equal(canSend("hi", []), true);
assert.equal(canSend("", [card("ready", 4)]), true);
assert.equal(canSend("hi", [card("ready", 4), card("preparing")]), false);
assert.deepEqual(readyIds([card("ready", 4), card("error"), card("ready", 7), card("preparing")]), [4, 7]);

// --- what the screen can say at once (no upload needed).
assert.match(quickProblem({ name: "IMG_1.HEIC", size: 10 }) ?? "", /JPG or PNG/);
assert.match(quickProblem({ name: "big.jpg", size: 21 * 1024 * 1024 }) ?? "", /20 MB/);
assert.equal(quickProblem({ name: "empty.png", size: 0 }), "This file is empty.");
assert.equal(quickProblem({ name: "cat.jpg", size: 1000 }), null);
// Videos are taken now (video plan Phase 2) - up to 500 MB, not the pictures'
// 20 MB; audio is still refused at once; a TypeScript file Windows calls
// "video/mp2t" is still a document.
assert.equal(quickProblem({ name: "VID_2026.mp4", size: 300 * 1024 * 1024, type: "video/mp4" }), null);
assert.equal(quickProblem({ name: "clip", size: 5000, type: "video/quicktime" }), null);
assert.match(quickProblem({ name: "IMG_0001.MOV", size: 501 * 1024 * 1024 }) ?? "", /over 500 MB/);
assert.match(quickProblem({ name: "voice.m4a", size: 5000 }) ?? "", /audio/);
assert.match(quickProblem({ name: "old.wmv", size: 5000, type: "video/x-ms-wmv" }) ?? "", /can't be opened/);
assert.equal(quickProblem({ name: "app.ts", size: 5000, type: "video/mp2t" }), null);
assert.ok(isVideoFile({ name: "IMG_0001.MOV" }) && isVideoFile({ name: "x", type: "video/webm" }));
assert.ok(!isVideoFile({ name: "app.ts", type: "video/mp2t" }) && !isVideoFile({ name: "cat.jpg" }));

// --- the info panel beside the "+" menu.
const caps = (over: Partial<Capabilities> = {}): Capabilities => ({
  pictures: true, reason: "Her current model can see pictures.", max_mb: 20,
  formats: "JPG, PNG, WebP, GIF or BMP", documents: false, cloud: false, room: 9, ...over,
});
assert.ok(infoLines(caps()).some((l) => l === "About 9 more pictures fit with her current model."));
assert.ok(infoLines(caps({ room: 1 })).some((l) => l === "About 1 more picture fit with her current model."));
assert.ok(infoLines(caps({ room: 0 })).some((l) => l.includes("take some off her desk")));
const blind = infoLines(caps({ pictures: false, reason: "Her current model can't see pictures." }));
assert.ok(blind.includes("Her current model can't see pictures."));
assert.ok(infoLines(null)[0].startsWith("Pictures: JPG"));
assert.ok(infoLines(null).some((l) => l.startsWith("Videos: MP4, MOV") && l.includes("10 minutes")));

// --- pasted screenshots get a name; real names stay.
const pasted = namedFile(new File(["x"], "image.png", { type: "image/png" }), new Date("2026-10-26T12:34:56Z"));
assert.equal(pasted.name, "pasted-2026-10-26-12-34-56.png");
assert.equal(namedFile(new File(["x"], "cat.jpg", { type: "image/jpeg" })).name, "cat.jpg");
assert.equal(filesFrom(null).length, 0);
assert.equal(filesFrom([new File(["x"], "a.png")]).length, 1);

// --- links to the backend.
assert.equal(absoluteUrl("/attachments/3/thumb"), "http://127.0.0.1:5050/attachments/3/thumb");
assert.equal(absoluteUrl("https://x.test/a"), "https://x.test/a");
assert.equal(absoluteUrl(undefined), undefined);

// --- after sending, the desk tells the bubble each picture's number.
const sentPic: PictureInfo = { id: 5, kind: "picture", name: "cat.jpg", caption: "", on_desk: true,
  number: null, turn: null, thumb_url: "/attachments/5/thumb", url: "" };
const known = withKnownPictures([sentPic], [{ ...sentPic, number: 3, turn: 7, caption: "a cat" }]);
assert.equal(known?.[0].number, 3);
assert.equal(known?.[0].caption, "a cat");

// --- the tray, her desk and the privacy note.
const desk: PictureInfo[] = [{ ...sentPic, number: 2, caption: "a sunset" }];
const html = renderToStaticMarkup(createElement(AttachmentTray, {
  cards: [card("preparing"), { ...card("error"), message: "This file type can't be shown to her." },
    { ...card("ready", 9), thumbUrl: "http://127.0.0.1:5050/attachments/9/thumb" }],
  desk, privacyNote: true,
  onRemoveCard: () => undefined, onRemoveFromDesk: () => undefined,
  onOpenPicture: () => undefined, onClosePrivacyNote: () => undefined,
}));
assert.ok(html.includes("Preparing..."));
assert.ok(html.includes("This file type can&#x27;t be shown to her."));
assert.ok(html.includes("Ready"));
assert.ok(html.includes("On her desk"));
assert.ok(html.includes(DESK_TOOLTIP.replace(/'/g, "&#x27;")));
assert.ok(html.includes('<span class="picture-number">2</span>'));
assert.ok(html.includes(PRIVACY_NOTE));
assert.equal(renderToStaticMarkup(createElement(AttachmentTray, {
  cards: [], desk: [], privacyNote: false, onRemoveCard: () => undefined,
  onRemoveFromDesk: () => undefined, onOpenPicture: () => undefined, onClosePrivacyNote: () => undefined,
})), "");

// --- your bubble's thumbnails carry their number (U1).
const strip = renderToStaticMarkup(createElement(PictureStrip, {
  pictures: [{ ...sentPic, number: 4 }], onOpen: () => undefined }));
assert.ok(strip.includes('<span class="picture-number">4</span>'));
assert.ok(strip.includes("http://127.0.0.1:5050/attachments/5/thumb"));

// --- L4: "also delete her reply about it" - her lines up to your next message
// (hold-on line, the reply with all its versions), never a greeting / reaction.
const chatRows = [
  { id: 1, role: "user" },
  { id: 2, role: "assistant", is_aside: true },
  { id: 4, role: "assistant", version_ids: [3, 4] },
  { id: 5, role: "user" },
  { id: 6, role: "assistant" },
];
assert.deepEqual(herReplyIds(chatRows, 0), [2, 3, 4]);
assert.deepEqual(herReplyIds(chatRows, 3), [6]);
assert.deepEqual(herReplyIds([{ id: 1, role: "user" }, { id: 2, role: "assistant", is_greeting: true }], 0), []);
assert.deepEqual(herReplyIds([{ id: 1, role: "user" }], 0), []);

// --- documents (Phase 4): old Office files refused at once, long paste -> card,
// the chip in your bubble, the tray warning when she reads only part.
assert.match(quickProblem({ name: "budget.xlsx", size: 10 }) ?? "", /PDF, Word \.docx or CSV/);
assert.equal(quickProblem({ name: "notes.pdf", size: 10 }), null);
assert.equal(PASTE_MAX_CHARS, 4000);
assert.equal(pastedTextFile("x".repeat(5000)).name, "Pasted text.txt");
const doc: PictureInfo = { ...sentPic, id: 8, kind: "document", name: "thesis.pdf", thumb_url: null,
  number: 1, pages: 30, read_pages: 3, cut: true, text_url: "/attachments/8/text" };
assert.ok(isDocument(doc) && !isDocument(sentPic));
assert.equal(pagesText(doc), "first 3 of 30 pages");
assert.equal(pagesText({ ...doc, cut: false, pages: 1 }), "1 page");
assert.equal(pagesText({ ...doc, notes: { state: "done", done: 15, total: 15 } }), "30 pages, notes on all of it");
const docStrip = renderToStaticMarkup(createElement(PictureStrip, { pictures: [doc], onOpen: () => undefined }));
assert.ok(docStrip.includes("document-chip") && docStrip.includes("thesis.pdf"));
assert.ok(!docStrip.includes("<img"));
const docTray = renderToStaticMarkup(createElement(AttachmentTray, {
  cards: [{ key: "d", name: "thesis.pdf", state: "ready", id: 8, kind: "document",
    warning: "Long document: she will read the first 3 of 30 pages." }],
  desk: [doc], privacyNote: false, onRemoveCard: () => undefined,
  onRemoveFromDesk: () => undefined, onOpenPicture: () => undefined, onClosePrivacyNote: () => undefined,
}));
assert.ok(docTray.includes("first 3 of 30 pages"));
assert.ok(docTray.includes("Take document 1 off her desk"));
assert.ok(infoLines(caps({ doc_formats: "PDF, Word (.docx) or text files", doc_pages: 9 }))
  .some((l) => l.startsWith("She can read about 9 pages")));

// --- the closed "+" shows only its hover label.
const menu = renderToStaticMarkup(createElement(AttachMenu, { caps: caps(), onFiles: () => undefined }));
assert.ok(menu.includes("Add pictures, videos or files"));
assert.ok(!menu.includes("Paste from clipboard"));

// --- videos (video plan Phase 2): a preparing video never holds the chat up;
// the card says how far it is; ▶ and the length over its picture.
const vcard = (state: TrayCard["state"], extra: Partial<TrayCard> = {}): TrayCard =>
  ({ key: `v${state}`, name: "IMG_0001.MOV", state, kind: "video", ...extra });
assert.equal(canSend("hi", [vcard("preparing", { progress: "Uploading 40%" })]), true);
assert.equal(canSend("", [vcard("preparing", { id: 3 })]), false);
assert.equal(canSend("", [vcard("ready", { id: 3 })]), true);
assert.equal(trayState(vcard("preparing", { progress: "Uploading 40%" })), "Uploading 40%");
assert.equal(trayState(vcard("preparing", { warning: "Picking frames... 60%" })), "Picking frames... 60%");
assert.equal(trayState(vcard("error", { message: "This video is over 10 minutes." })), "This video is over 10 minutes.");
assert.equal(trayState(card("preparing")), "Preparing...");
const vinfo: PictureInfo = { ...sentPic, id: 12, kind: "video", name: "boat.mp4", length: "0:10",
  video: { state: "preparing", step: "watch", pct: 20, message: "", speech: "writing", frames: 0 },
  warning: "Preparing... 20%" };
assert.equal(videoCardState(vinfo), "preparing");
assert.equal(videoCardState({ ...vinfo, video: { ...vinfo.video!, state: "ready" } }), "ready");
assert.equal(videoCardState({ ...vinfo, video: { ...vinfo.video!, state: "failed" } }), "error");
assert.deepEqual(cardFrom(vinfo, "k"), { key: "k", name: "boat.mp4", state: "preparing", id: 12,
  kind: "video", thumbUrl: "http://127.0.0.1:5050/attachments/5/thumb", length: "0:10",
  warning: "Preparing... 20%" });
assert.equal(cardFrom({ ...vinfo, video: { ...vinfo.video!, state: "failed" },
  warning: "This video could not be prepared." }, "k").message, "This video could not be prepared.");
assert.ok(isVideo(vinfo) && !isVideo(sentPic));
const vstrip = renderToStaticMarkup(createElement(PictureStrip, {
  pictures: [{ ...vinfo, number: 1 }], onOpen: () => undefined }));
assert.ok(vstrip.includes("play-mark") && vstrip.includes('<span class="video-length">0:10</span>'));
assert.ok(vstrip.includes("Play video 1 (0:10)"));
const vtray = renderToStaticMarkup(createElement(AttachmentTray, {
  cards: [vcard("preparing", { id: 12, thumbUrl: "http://x.test/t.jpg", length: "0:42",
    warning: "Preparing... 20%" })],
  desk: [], privacyNote: false, onRemoveCard: () => undefined,
  onRemoveFromDesk: () => undefined, onOpenPicture: () => undefined, onClosePrivacyNote: () => undefined,
}));
assert.ok(vtray.includes("Preparing... 20%") && vtray.includes("0:42") && vtray.includes("play-mark"));
assert.equal(speechNote({ speech_state: "none" }), "This video has no sound.");
assert.equal(speechNote({ speech_state: "done" }), "");
assert.match(speechNote({ speech_state: "waiting" }), /Writing down/);
assert.ok(PRIVACY_NOTE.includes("videos (with their sound)"));
// Phase 3: the line under her reply.
assert.equal(readsLine("Read: Document 2: pages 4-5"), "📄 Read: Document 2: pages 4-5");
assert.equal(readsLine("Watched: Video 1 (16 stills + what is said) · Read: Document 2: page 3"),
  "🎬 Watched: Video 1 (16 stills + what is said) · 📄 Read: Document 2: page 3");

console.log("attachments.test.ts ok");

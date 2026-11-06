// Pictures you show her (attachments Phase 2, 2026-10-26): the backend calls
// and the small rules the screen follows. A picture is uploaded the moment it
// is attached (a "card" in the tray), sent by id with the message, and then
// sits "on her desk" until you take it off. See backend/attachments.py.
import { API_BASE } from "./api";

/** One picture as the backend describes it (a sent one has a number + turn). */
export type PictureInfo = {
  id: number;
  kind: string;
  name: string;
  caption: string;
  on_desk: boolean;
  /** Its number in this chat ("picture 2"); null until it is sent. */
  number: number | null;
  turn: number | null;
  width?: number | null;
  height?: number | null;
  thumb_url: string | null;
  url: string;
  original_url?: string;
  /** Documents (Phase 4): what she read, as text. */
  text_url?: string;
  pages?: number;
  /** Pages she reads (null before it is sent); fewer than pages = cut. */
  read_pages?: number | null;
  cut?: boolean;
  /** On a card not sent yet: she can read only part of it, or none. */
  warning?: string;
  /** A long document's reading notes (Phase 4b). */
  notes?: { state: string; done: number; total: number };
  /** Videos (video plan Phase 2): "0:42", the player's copy, its preparing. */
  length?: string;
  duration?: number | null;
  has_audio?: boolean;
  video_url?: string | null;
  info_url?: string;
  video?: VideoProgress;
};

export type VideoProgress = {
  state: "preparing" | "ready" | "failed";
  step: string;
  pct: number;
  message: string;
  /** none (no sound) / writing / waiting / done / failed / unavailable */
  speech: string;
  frames: number;
};

export type CardState = "preparing" | "ready" | "error";

/** A card in the tray above the message box (not sent yet). */
export type TrayCard = {
  key: string;
  name: string;
  state: CardState;
  /** The backend's id once uploaded. */
  id?: number;
  thumbUrl?: string;
  /** Why it can't be used (state "error"). */
  message?: string;
  kind?: string;
  /** A document she can read only part of (or none). */
  warning?: string;
  /** A video: its length ("0:42"), and "Uploading 45%" while it goes up. */
  length?: string;
  progress?: string;
};

export type Capabilities = {
  /** true / false / null (not sure - she says so if not). */
  pictures: boolean | null;
  reason: string;
  max_mb: number;
  formats: string;
  documents: boolean;
  /** Documents she reads, and about how many pages of one fit right now. */
  doc_formats?: string;
  doc_pages?: number;
  /** Her replies come from a cloud service (pictures leave this PC). */
  cloud: boolean;
  /** About how many more pictures fit with her current model. */
  room: number;
};

export type ReactionChoice = "always" | "quiet" | "once" | "off";

export const REACTION_CHOICES: { value: ReactionChoice; label: string }[] = [
  { value: "always", label: "Every time" },
  { value: "quiet", label: "Every time, then quiet for ~5 minutes" },
  { value: "once", label: "Once per conversation" },
  { value: "off", label: "Off" },
];

/** Her reaction waits this long after the last card is ready (a batch = one line). */
export const REACTION_DELAY_MS = 1500;

export const MAX_FILE_MB = 20;
/** Videos (video plan V6a / V4a): the backend checks the length. */
export const MAX_VIDEO_MB = 500;
export const VIDEO_PIECE_BYTES = 4 * 1024 * 1024;
const VIDEO_NAME = /\.(mp4|m4v|mov|webm|mkv|avi|3gp|3g2)$/i;
const AUDIO_NAME = /\.(mp3|m4a|wav|ogg|oga|opus|flac|aac|wma|amr)$/i;
export const VIDEO_ACCEPT = "video/*,.mp4,.m4v,.mov,.webm,.mkv,.avi,.3gp,.3g2";

export const DESK_TOOLTIP =
  "Files on her desk are readily accessible by Amadeus, but her replies get slower the more files are in her memory.";

export const PRIVACY_NOTE =
  "Pictures, videos (with their sound) and files you attach are sent to your cloud model. With the Built-in model they never leave this PC. Location data in photos is always removed.";

export const PRIVACY_SEEN_KEY = "amadeus.picturePrivacySeen";

export function absoluteUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  return /^https?:\/\//.test(path) ? path : `${API_BASE}${path}`;
}

/** A video file (by name, or by its type when the name says nothing). */
export function isVideoFile(file: { name: string; type?: string }): boolean {
  const documentName = DOCUMENT_ACCEPT.split(",").some((ext) => file.name.toLowerCase().endsWith(ext));
  return VIDEO_NAME.test(file.name) || (!documentName && !OTHER_VIDEO.test(file.name)
    && /^video\//.test(file.type ?? ""));
}

export function isVideo(item: { kind?: string } | null | undefined): boolean {
  return item?.kind === "video";
}

/** A reason the screen can give at once (no upload needed), or null. */
export function quickProblem(file: { name: string; size: number; type?: string }): string | null {
  if (file.size === 0) return "This file is empty.";
  if (isVideoFile(file)) {
    if (file.size > MAX_VIDEO_MB * 1024 * 1024) {
      return `This video is over ${MAX_VIDEO_MB} MB. Trim it in your gallery first, then attach that.`;
    }
    return null;
  }
  if (file.size > MAX_FILE_MB * 1024 * 1024) return `This file is over ${MAX_FILE_MB} MB - too big to show her.`;
  if (/\.(heic|heif)$/i.test(file.name)) {
    return "This is an iPhone HEIC picture. Save it as JPG or PNG first, then attach that.";
  }
  if (OFFICE_LATER.test(file.name)) {
    return "She can't read this file type yet. Save it as PDF, Word .docx or CSV and attach that.";
  }
  // Refused here rather than after a long upload over the phone's data.
  // (Windows calls a TypeScript .ts file "video/mp2t": a document name wins.)
  const documentName = DOCUMENT_ACCEPT.split(",").some((ext) => file.name.toLowerCase().endsWith(ext));
  if (!documentName && (/^audio\//.test(file.type ?? "") || AUDIO_NAME.test(file.name))) {
    return "She can't listen to audio files yet. Attach a picture, a video or a document instead.";
  }
  if (!documentName && OTHER_VIDEO.test(file.name)) {
    return "This video type can't be opened. Videos: MP4, MOV, WebM, M4V, MKV, AVI or 3GP.";
  }
  return null;
}

const OTHER_VIDEO = /\.(wmv|flv|mpg|mpeg|mts|m2ts|vob)$/i;

/** Documents (Phase 4, 2026-10-28). */
const OFFICE_LATER = /\.(doc|rtf|odt|xls|xlsx|ods|ppt|pptx|odp|pages|numbers|key|epub)$/i;

export const DOCUMENT_ACCEPT = ".pdf,.docx,.txt,.text,.md,.markdown,.csv,.tsv,.json,.log,.xml,.yaml,.yml,"
  + ".ini,.toml,.cfg,.conf,.srt,.vtt,.tex,.rst,.html,.htm,.css,.py,.js,.jsx,.ts,.tsx,.c,.cpp,.h,.hpp,"
  + ".cs,.java,.kt,.go,.rs,.rb,.php,.swift,.lua,.r,.sql,.sh,.bat,.ps1";

/** E3: pasted text longer than this becomes a "Pasted text" card. */
export const PASTE_MAX_CHARS = 4000;

export function pastedTextFile(text: string): File {
  return new File([text], "Pasted text.txt", { type: "text/plain" });
}

export function isDocument(item: { kind?: string } | null | undefined): boolean {
  return item?.kind === "document";
}

/** "4 pages", or "first 3 of 12 pages" when she reads only part of it. */
export function pagesText(doc: PictureInfo): string {
  const pages = doc.pages ?? 1;
  if (doc.notes?.state === "done") return `${pages} pages, notes on all of it`;
  if (doc.cut && doc.read_pages != null) return `first ${doc.read_pages} of ${pages} pages`;
  return `${pages} page${pages === 1 ? "" : "s"}`;
}

export type DocumentView = PictureInfo & { text: string; notes_text?: string };

export async function getDocumentText(doc: PictureInfo): Promise<DocumentView> {
  return parse(await fetch(absoluteUrl(doc.text_url) ?? "", { cache: "no-store" }));
}

/** The ids of the cards ready to be sent, in tray order. */
export function readyIds(cards: TrayCard[]): number[] {
  return cards.filter((c) => c.state === "ready" && typeof c.id === "number").map((c) => c.id as number);
}

/** Send is allowed with words OR a ready card - never while a card is still
 *  being prepared. A video can take minutes: it never holds the chat up - it
 *  stays in the tray until it is ready. */
export function canSend(text: string, cards: TrayCard[]): boolean {
  if (cards.some((c) => c.state === "preparing" && c.kind !== "video")) return false;
  return text.trim().length > 0 || readyIds(cards).length > 0;
}

/** Picture files from a paste / drop (anything else stays text). */
export function filesFrom(list: FileList | DataTransferItemList | File[] | null | undefined): File[] {
  if (!list) return [];
  const out: File[] = [];
  for (const entry of Array.from(list as ArrayLike<File | DataTransferItem>)) {
    if (typeof (entry as DataTransferItem).getAsFile === "function") {
      const item = entry as DataTransferItem;
      if (item.kind !== "file") continue;
      const file = item.getAsFile();
      if (file) out.push(file);
    } else {
      out.push(entry as File);
    }
  }
  return out;
}

/** A pasted screenshot has no real name: give it one. */
export function namedFile(file: File, now = new Date()): File {
  if (file.name && file.name !== "image.png") return file;
  const stamp = now.toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const ext = (file.type.split("/")[1] || "png").replace("jpeg", "jpg");
  return new File([file], `pasted-${stamp}.${ext}`, { type: file.type });
}

/** Text for the info panel next to the "+" menu. */
export function infoLines(caps: Capabilities | null): string[] {
  const lines = [
    `Pictures: ${caps?.formats ?? "JPG, PNG, WebP, GIF or BMP"} - up to ${caps?.max_mb ?? MAX_FILE_MB} MB each.`,
    `Documents: ${caps?.doc_formats ?? "PDF, Word (.docx) or text files"}.`,
    `Videos: MP4, MOV, WebM and more - up to 10 minutes and ${MAX_VIDEO_MB} MB. Long phone videos upload faster trimmed.`,
  ];
  if (caps?.doc_pages != null) {
    lines.push(caps.doc_pages > 0
      ? `She can read about ${caps.doc_pages} page${caps.doc_pages === 1 ? "" : "s"} of a document right now (the rest is cut).`
      : "No room for a document right now - take something off her desk first.");
  }
  if (caps) {
    lines.push(caps.room > 0
      ? `About ${caps.room} more picture${caps.room === 1 ? "" : "s"} fit with her current model.`
      : "Her current model has no room for more pictures - take some off her desk first.");
    if (caps.pictures === false) lines.push(caps.reason);
  }
  lines.push("You can also paste a picture or a long text, or drop files anywhere in the window.");
  return lines;
}

async function parse(response: Response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data?.message || `Request failed (${response.status})`);
  return data;
}

export type UploadResult = { ok: true; picture: PictureInfo } | { ok: false; message: string };

export async function uploadPicture(file: File, conversationId: number | null): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  if (conversationId != null) form.append("conversation_id", String(conversationId));
  const data = await parse(await fetch(`${API_BASE}/attachments`, { method: "POST", body: form }));
  const item = Array.isArray(data.items) ? data.items[0] : null;
  if (!item) return { ok: false, message: "This file could not be saved." };
  if (!item.ok) return { ok: false, message: String(item.message || "This file can't be used.") };
  return { ok: true, picture: item as PictureInfo };
}

/** A video card: "preparing" until the PC has made its copies, then "ready"
 *  ("error" when it could not be prepared). */
export function videoCardState(p: PictureInfo): CardState {
  if (p.video?.state === "failed") return "error";
  return p.video?.state === "ready" ? "ready" : "preparing";
}

export class UploadStopped extends Error {}

/** A video goes up in pieces (resumable): after a dropped connection it asks
 *  how much arrived and goes on from there. onProgress gets 0-100. */
export async function uploadVideo(
  file: File, conversationId: number | null, onProgress: (pct: number) => void,
  signal?: AbortSignal, onUploadId?: (id: string) => void,
): Promise<UploadResult> {
  const start = await fetch(`${API_BASE}/attachments/video`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: file.name, size: file.size, conversation_id: conversationId }),
    signal,
  });
  const begun = await start.json().catch(() => ({}));
  if (!begun.ok) return { ok: false, message: String(begun.message || "This video can't be used.") };
  const id = String(begun.upload_id);
  onUploadId?.(id);
  const piece = Math.min(VIDEO_PIECE_BYTES, Number(begun.piece) || VIDEO_PIECE_BYTES);
  let sent = 0;
  let failures = 0;
  while (sent < file.size) {
    if (signal?.aborted) throw new UploadStopped();
    try {
      const response = await fetch(`${API_BASE}/attachments/video/${id}?offset=${sent}`, {
        method: "PUT",
        headers: { "Content-Type": "application/octet-stream" },
        body: file.slice(sent, sent + piece),
        signal,
      });
      const data = await response.json().catch(() => ({}));
      if (typeof data.received !== "number") {
        return { ok: false, message: String(data.message || "The upload stopped - attach the video again.") };
      }
      sent = data.received;
      failures = 0;
    } catch (error) {
      if (signal?.aborted) throw new UploadStopped();
      // Mobile data dropped: wait a little, ask how much arrived, go on.
      failures += 1;
      if (failures > 8) throw error;
      await new Promise((resolve) => setTimeout(resolve, Math.min(15000, 1000 * 2 ** failures)));
      try {
        const status = await (await fetch(`${API_BASE}/attachments/video/${id}`, { signal })).json();
        if (typeof status.received === "number") sent = status.received;
      } catch {
        // Still offline - the next round tries again.
      }
    }
    onProgress(Math.floor((100 * sent) / file.size));
  }
  const done = await fetch(`${API_BASE}/attachments/video/${id}/done`, { method: "POST", signal });
  const item = await done.json().catch(() => ({}));
  if (!item.ok) return { ok: false, message: String(item.message || "This video could not be saved.") };
  return { ok: true, picture: item as PictureInfo };
}

export async function cancelVideoUpload(uploadId: string): Promise<void> {
  await fetch(`${API_BASE}/attachments/video/${uploadId}`, { method: "DELETE" });
}

export type VideoView = PictureInfo & {
  speech_state: string; speech_text: string; language: string; notes_text?: string;
};

export async function getVideoInfo(item: PictureInfo): Promise<VideoView> {
  return parse(await fetch(absoluteUrl(item.info_url ?? `/attachments/${item.id}/watch`) ?? "",
    { cache: "no-store" }));
}

/** What the player says about the speech in a video. */
export function speechNote(view: { speech_state?: string; has_audio?: boolean }): string {
  switch (view.speech_state) {
    case "none": return "This video has no sound.";
    case "done": return "";
    case "failed": return "What is said in it could not be written down.";
    case "unavailable": return "Her voice engine is missing, so what is said can't be written down.";
    default: return "Writing down what is said...";
  }
}

/** The line under her reply: "🎬 Watched: Video 1 (16 stills + what is said)
 *  · 📄 Read: Document 2: pages 4-5" (the backend joins the parts with " · "). */
export function readsLine(text: string): string {
  return text.split(" · ").map((part) => (part.startsWith("Watched:") ? "🎬 " : "📄 ") + part)
    .join(" · ");
}

export async function removeAttachment(id: number): Promise<"deleted" | "off_desk"> {
  const data = await parse(await fetch(`${API_BASE}/attachments/${id}`, { method: "DELETE" }));
  return data.result;
}

/** Take back a card not sent yet; her line about it when she had already
 *  reacted to it (null otherwise). */
export async function withdrawAttachment(id: number): Promise<ReactionReply | null> {
  const data = await parse(await fetch(`${API_BASE}/attachments/${id}?react=1`, { method: "DELETE" }));
  return reactionFrom(data.reaction);
}

function reactionFrom(data: Record<string, unknown> | undefined | null): ReactionReply | null {
  if (!data || typeof data.response !== "string") return null;
  return {
    response: data.response,
    japanese: typeof data.japanese === "string" ? data.japanese : undefined,
    speechUrl: typeof data.speech_id === "string"
      ? `${API_BASE}/speech/${encodeURIComponent(data.speech_id)}` : undefined,
    assistantId: typeof data.assistant_id === "number" ? data.assistant_id : undefined,
    conversationId: typeof data.conversation_id === "number" ? data.conversation_id : undefined,
  };
}

/** The ids to delete with "also delete her reply about it": her lines right
 *  after your message (hold-on line, the reply and all its versions) up to
 *  your next message. Greetings / attach reactions are their own lines. */
export function herReplyIds(messages: { id?: number; role: string; is_greeting?: boolean;
  version_ids?: number[] }[], index: number): number[] {
  const ids: number[] = [];
  for (let i = index + 1; i < messages.length; i++) {
    const m = messages[i];
    if (m.role === "user" || m.is_greeting) break;
    for (const id of m.version_ids?.length ? m.version_ids : m.id != null ? [m.id] : []) {
      if (!ids.includes(id)) ids.push(id);
    }
  }
  return ids;
}

/** Cards not sent yet, worked out again (a document's warning follows her desk). */
export async function getCards(ids: number[], conversationId: number | null): Promise<PictureInfo[]> {
  const query = new URLSearchParams({ ids: ids.join(",") });
  if (conversationId != null) query.set("conversation_id", String(conversationId));
  const data = await parse(await fetch(`${API_BASE}/attachments/cards?${query}`, { cache: "no-store" }));
  return Array.isArray(data.items) ? data.items : [];
}

export async function getDesk(conversationId: number | null): Promise<PictureInfo[]> {
  const query = conversationId != null ? `?conversation_id=${conversationId}` : "";
  const data = await parse(await fetch(`${API_BASE}/attachments/desk${query}`, { cache: "no-store" }));
  return Array.isArray(data.items) ? data.items : [];
}

export async function getCapabilities(conversationId: number | null): Promise<Capabilities> {
  const query = conversationId != null ? `?conversation_id=${conversationId}` : "";
  return parse(await fetch(`${API_BASE}/attachments/capabilities${query}`, { cache: "no-store" }));
}

export type ReactionReply = {
  response: string;
  japanese?: string;
  speechUrl?: string;
  assistantId?: number;
  conversationId?: number;
};

/** Her line about cards waiting to be sent; null when she stays quiet (the
 *  setting, or you sent first). */
export async function reactToAttachments(
  ids: number[], conversationId: number | null, signal?: AbortSignal,
): Promise<ReactionReply | null> {
  const response = await fetch(`${API_BASE}/attachments/react`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ attachment_ids: ids, conversation_id: conversationId }),
    signal,
  });
  if (response.status === 409) return null;   // you sent first - the line was dropped
  const data = await parse(response);
  if (data.status !== "ok") return null;
  return reactionFrom(data);
}

export async function getReactionSetting(): Promise<ReactionChoice> {
  const data = await parse(await fetch(`${API_BASE}/attachments/reactionSetting`, { cache: "no-store" }));
  return data.value as ReactionChoice;
}

export async function setReactionSetting(value: ReactionChoice): Promise<ReactionChoice> {
  const data = await parse(await fetch(`${API_BASE}/attachments/reactionSetting`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  }));
  return data.value as ReactionChoice;
}

/** Fill in numbers / captions the backend gave the pictures after sending. */
export function withKnownPictures(pictures: PictureInfo[] | undefined, known: PictureInfo[]): PictureInfo[] | undefined {
  if (!pictures) return pictures;
  const byId = new Map(known.map((p) => [p.id, p]));
  return pictures.map((p) => ({ ...p, ...(byId.get(p.id) ?? {}) }));
}

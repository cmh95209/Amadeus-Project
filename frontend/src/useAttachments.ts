// The tray, her desk and her attach reaction (attachments Phase 2). Kept out
// of App.tsx: App only adds files, takes the ready ids when you send, and
// shows what this hook holds.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  PRIVACY_SEEN_KEY,
  REACTION_DELAY_MS,
  UploadStopped,
  absoluteUrl,
  cancelVideoUpload,
  getCapabilities,
  getCards,
  getDesk,
  isVideoFile,
  quickProblem,
  reactToAttachments,
  readyIds,
  removeAttachment,
  uploadPicture,
  uploadVideo,
  videoCardState,
  withdrawAttachment,
} from "./attachments";
import type { Capabilities, PictureInfo, ReactionReply, TrayCard } from "./attachments";

type Options = {
  /** The chat the cards belong to right now. */
  conversationId: () => number | null;
  /** May she react now? (not while she answers / during setup). */
  mayReact: () => boolean;
  /** Her reaction line arrived (show it, speak it). */
  onReaction: (reply: ReactionReply) => void;
};

let cardSeq = 0;
const NOTES_POLL_MS = 2500;
const VIDEO_POLL_MS = 2000;

/** A tray card from what the backend says about an uploaded attachment. */
export function cardFrom(p: PictureInfo, key: string): TrayCard {
  const video = p.kind === "video";
  const state = video ? videoCardState(p) : "ready";
  return {
    key, name: p.name, state, id: p.id, kind: p.kind,
    thumbUrl: absoluteUrl(p.thumb_url ?? undefined),
    ...(video && p.length ? { length: p.length } : {}),
    ...(state === "error" ? { message: p.warning || "This video could not be prepared." }
      : p.warning ? { warning: p.warning } : {}),
  };
}

// Android may close the app while you pick from the gallery or answer a call;
// it then reloads. The uploaded cards are kept here so the tray comes back
// (the backend still holds them until they are sent or removed).
const TRAY_KEY = "amadeus.trayCards";
const TRAY_KEEP_MS = 12 * 60 * 60 * 1000;
type SavedTray = { at: number; conv: number | null; ids: number[] };

function readSavedTray(): SavedTray | null {
  try {
    const saved = JSON.parse(localStorage.getItem(TRAY_KEY) ?? "null") as SavedTray | null;
    if (!saved || !Array.isArray(saved.ids) || !saved.ids.length) return null;
    return Date.now() - saved.at < TRAY_KEEP_MS ? saved : null;
  } catch {
    return null;
  }
}

function seenPrivacyNote(): boolean {
  try {
    return localStorage.getItem(PRIVACY_SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function useAttachments({ conversationId, mayReact, onReaction }: Options) {
  const [cards, setCards] = useState<TrayCard[]>([]);
  const [desk, setDesk] = useState<PictureInfo[]>([]);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [privacyNote, setPrivacyNote] = useState(false);
  const cardsRef = useRef<TrayCard[]>([]);
  cardsRef.current = cards;
  const capsRef = useRef<Capabilities | null>(null);
  capsRef.current = caps;
  const reactTimer = useRef<number | null>(null);
  const reactAbort = useRef<AbortController | null>(null);
  const reacted = useRef<Set<number>>(new Set());
  const optionsRef = useRef({ conversationId, mayReact, onReaction });
  optionsRef.current = { conversationId, mayReact, onReaction };

  const cancelReaction = useCallback(() => {
    if (reactTimer.current != null) window.clearTimeout(reactTimer.current);
    reactTimer.current = null;
    reactAbort.current?.abort();
    reactAbort.current = null;
  }, []);

  const scheduleReaction = useCallback(() => {
    cancelReaction();
    reactTimer.current = window.setTimeout(async () => {
      reactTimer.current = null;
      // A video: once it has arrived (she reacts to its name, like a picture's;
      // the PC may still be preparing it).
      const ready = cardsRef.current.filter((c) => typeof c.id === "number"
        && (c.state === "ready" || (c.kind === "video" && c.state === "preparing")));
      const fresh = ready.map((c) => c.id as number).filter((id) => !reacted.current.has(id));
      // Still uploading? The last one to finish schedules it again.
      if (!fresh.length || cardsRef.current.some((c) => c.state === "preparing"
        && (c.kind !== "video" || c.id == null))) return;
      if (!optionsRef.current.mayReact()) return;
      fresh.forEach((id) => reacted.current.add(id));
      const abort = new AbortController();
      reactAbort.current = abort;
      try {
        const reply = await reactToAttachments(fresh, optionsRef.current.conversationId(), abort.signal);
        if (reply && !abort.signal.aborted) optionsRef.current.onReaction(reply);
      } catch {
        // Her reaction is a nicety: a failure never gets in the way.
      } finally {
        if (reactAbort.current === abort) reactAbort.current = null;
      }
    }, REACTION_DELAY_MS);
  }, [cancelReaction]);

  useEffect(() => cancelReaction, [cancelReaction]);

  // Bring back the cards from before a reload (still unsent on the backend).
  // She already had her chance to react to them, so she stays quiet.
  const trayRestored = useRef(false);
  useEffect(() => {
    const saved = readSavedTray();
    if (!saved) {
      trayRestored.current = true;
      return;
    }
    void getCards(saved.ids, saved.conv)
      .then((items) => {
        const back: TrayCard[] = items.map((p) => cardFrom(p, `card-${++cardSeq}`));
        back.forEach((c) => reacted.current.add(c.id as number));
        setCards((current) => {
          const known = new Set(current.map((c) => c.id));
          return [...back.filter((c) => !known.has(c.id) && known.add(c.id)), ...current];
        });
      })
      .catch(() => undefined)
      .finally(() => { trayRestored.current = true; });
  }, []);

  // Kept: every uploaded card (a video still being prepared too - the PC
  // goes on with it while the phone app is reloaded).
  const keptIds = (list: TrayCard[]) => list
    .filter((c) => typeof c.id === "number" && c.state !== "error").map((c) => c.id as number);
  const readyKey = keptIds(cards).join(",");
  useEffect(() => {
    if (!trayRestored.current) return;
    try {
      const ids = keptIds(cardsRef.current);
      if (ids.length) {
        const saved: SavedTray = { at: Date.now(), conv: optionsRef.current.conversationId(), ids };
        localStorage.setItem(TRAY_KEY, JSON.stringify(saved));
      } else {
        localStorage.removeItem(TRAY_KEY);
      }
    } catch {
      // Not kept across a reload - the tray just starts empty.
    }
  }, [readyKey]);

  const patchCard = (key: string, patch: Partial<TrayCard>) =>
    setCards((current) => current.map((c) => (c.key === key ? { ...c, ...patch } : c)));

  // Videos (video plan Phase 2): uploaded in pieces with a progress line;
  // taking the card off stops the upload. Then the PC prepares it (the card
  // says how far) - see the check-in below.
  const uploads = useRef(new Map<string, { abort: AbortController; uploadId?: string }>());
  const addVideo = (file: File, key: string) => {
    const job: { abort: AbortController; uploadId?: string } = { abort: new AbortController() };
    uploads.current.set(key, job);
    void uploadVideo(file, optionsRef.current.conversationId(),
      (pct) => patchCard(key, { progress: `Uploading ${pct}%` }), job.abort.signal,
      (id) => { job.uploadId = id; })
      .then((result) => {
        if (!cardsRef.current.some((c) => c.key === key)) {
          if (result.ok) void removeAttachment(result.picture.id).catch(() => undefined);
          return;
        }
        if (result.ok) {
          patchCard(key, { ...cardFrom(result.picture, key), progress: undefined });
        } else {
          patchCard(key, { state: "error", message: result.message, progress: undefined });
        }
        scheduleReaction();
      })
      .catch((error) => {
        if (error instanceof UploadStopped || job.abort.signal.aborted) return;
        patchCard(key, {
          state: "error", progress: undefined,
          message: "The upload stopped (no connection) - attach the video again.",
        });
        scheduleReaction();
      })
      .finally(() => { uploads.current.delete(key); });
  };

  const addFiles = useCallback((files: File[]) => {
    if (!files.length) return;
    if (capsRef.current?.cloud && !seenPrivacyNote()) setPrivacyNote(true);
    for (const file of files) {
      const key = `card-${++cardSeq}`;
      const problem = quickProblem(file);
      const video = isVideoFile(file);
      setCards((current) => [...current, {
        key, name: file.name || "picture", state: problem ? "error" : "preparing",
        ...(video ? { kind: "video", progress: "Uploading 0%" } : {}),
        ...(problem ? { message: problem } : {}),
      }]);
      if (problem) continue;
      if (video) {
        addVideo(file, key);
        continue;
      }
      void uploadPicture(file, optionsRef.current.conversationId())
        .then((result) => {
          if (!cardsRef.current.some((c) => c.key === key)) {
            // Taken off the tray while it uploaded: delete the upload too.
            if (result.ok) void removeAttachment(result.picture.id).catch(() => undefined);
            return;
          }
          if (result.ok) {
            patchCard(key, { state: "ready", id: result.picture.id, kind: result.picture.kind,
              thumbUrl: absoluteUrl(result.picture.thumb_url ?? undefined),
              ...(result.picture.warning ? { warning: result.picture.warning } : {}) });
            scheduleReaction();
          } else {
            patchCard(key, { state: "error", message: result.message });
            // Her reaction waits for the last upload; a refused one (an MP4
            // in the batch, say) must still let her react to the rest.
            scheduleReaction();
          }
        })
        .catch((error) => {
          patchCard(key, {
            state: "error", message: error instanceof Error ? error.message : "Upload failed",
          });
          scheduleReaction();
        });
    }
  }, [scheduleReaction]);

  const removeCard = useCallback((key: string) => {
    const card = cardsRef.current.find((c) => c.key === key);
    setCards((current) => current.filter((c) => c.key !== key));
    const upload = uploads.current.get(key);
    if (upload) {
      // A video still going up: stop it, and drop what arrived.
      upload.abort.abort();
      if (upload.uploadId) void cancelVideoUpload(upload.uploadId).catch(() => undefined);
    }
    if (!cardsRef.current.some((c) => c.key !== key && c.state === "ready")) cancelReaction();
    if (card?.id == null) return;
    if (reacted.current.has(card.id) && optionsRef.current.mayReact()) {
      // She already said a line about it: she notices it was taken back
      // (the backend decides whether it is the moment - e.g. the last card).
      void withdrawAttachment(card.id)
        .then((reply) => { if (reply) optionsRef.current.onReaction(reply); })
        .catch(() => undefined);
    } else {
      void removeAttachment(card.id).catch(() => undefined);
    }
  }, [cancelReaction]);

  /** Sending: the ready cards leave the tray (cards still preparing or
   *  refused stay); her reaction, if not said yet, is dropped. */
  const takeForSend = useCallback((): { ids: number[]; pictures: PictureInfo[]; taken: TrayCard[] } => {
    cancelReaction();
    const taken = cardsRef.current.filter((c) => c.state === "ready" && typeof c.id === "number");
    setCards((current) => current.filter((c) => !taken.includes(c)));
    return {
      ids: taken.map((c) => c.id as number),
      taken,
      pictures: taken.map((c) => ({
        id: c.id as number, kind: c.kind ?? "picture", name: c.name, caption: "", on_desk: true,
        number: null, turn: null, thumb_url: c.thumbUrl ?? (c.kind === "document" ? null : ""), url: "",
        ...(c.kind === "document" ? { text_url: `/attachments/${c.id}/text` } : {}),
        ...(c.kind === "video" ? { length: c.length, info_url: `/attachments/${c.id}/watch`,
          video_url: `/attachments/${c.id}/file` } : {}),
      })),
    };
  }, [cancelReaction]);

  /** Her reply failed: the backend put the pictures back in the tray - so does the screen. */
  const restore = useCallback((taken: TrayCard[]) => {
    taken.forEach((c) => typeof c.id === "number" && reacted.current.add(c.id));
    setCards((current) => [...taken, ...current]);
  }, []);

  const refreshDesk = useCallback(async (conv?: number | null): Promise<PictureInfo[]> => {
    try {
      const items = await getDesk(conv ?? optionsRef.current.conversationId());
      setDesk(items);
      return items;
    } catch {
      return [];
    }
  }, []);

  const removeFromDesk = useCallback(async (id: number) => {
    setDesk((current) => current.filter((p) => p.id !== id));
    try {
      await removeAttachment(id);
    } catch {
      void refreshDesk();
    }
  }, [refreshDesk]);

  const refreshCaps = useCallback(async (conv?: number | null) => {
    try {
      setCaps(await getCapabilities(conv ?? optionsRef.current.conversationId()));
    } catch {
      // The menu then shows its plain defaults.
    }
  }, []);

  // A document card's warning ("she will read the first 3 of 30 pages" /
  // "no room") depends on her desk and the other cards: ask again whenever
  // either changes (live check 2026-10-28: it stayed "no room" after the
  // desk was cleared).
  const readyDocs = cards.filter((c) => c.kind === "document" && c.state === "ready" && c.id != null)
    .map((c) => c.id as number);
  const allIds = cards.filter((c) => c.id != null).map((c) => c.id as number);
  const roomKey = `${desk.map((p) => p.id).join(",")}|${allIds.join(",")}`;
  useEffect(() => {
    if (!readyDocs.length) return;
    let live = true;
    void getCards(readyDocs, optionsRef.current.conversationId())
      .then((items) => {
        if (!live) return;
        const byId = new Map(items.map((p) => [p.id, p]));
        setCards((current) => current.map((c) => {
          const fresh = c.id != null ? byId.get(c.id) : undefined;
          return fresh ? { ...c, warning: fresh.warning || undefined } : c;
        }));
      })
      .catch(() => undefined);
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomKey]);

  // While she is making reading notes on a long document (Phase 4b) its card
  // says "Reading... 40%": ask again every few seconds until it is done.
  const reading = cards.filter((c) => c.kind === "document" && c.id != null
    && (c.warning ?? "").startsWith("Reading...")).map((c) => c.id as number);
  const readingKey = reading.join(",");
  useEffect(() => {
    if (!reading.length) return;
    const timer = window.setInterval(() => {
      void getCards(reading, optionsRef.current.conversationId())
        .then((items) => {
          const byId = new Map(items.map((p) => [p.id, p]));
          setCards((current) => current.map((c) => {
            const fresh = c.id != null ? byId.get(c.id) : undefined;
            return fresh ? { ...c, warning: fresh.warning || undefined } : c;
          }));
        })
        .catch(() => undefined);
    }, NOTES_POLL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readingKey]);

  // A video the PC is preparing (copy, frames, speech): its card says how far
  // ("Preparing... 40%") - ask again every few seconds until it is ready.
  const preparingVideos = cards.filter((c) => c.kind === "video" && c.id != null && c.state === "preparing")
    .map((c) => c.id as number);
  const preparingKey = preparingVideos.join(",");
  useEffect(() => {
    if (!preparingVideos.length) return;
    const timer = window.setInterval(() => {
      void getCards(preparingVideos, optionsRef.current.conversationId())
        .then((items) => {
          const byId = new Map(items.map((p) => [p.id, p]));
          setCards((current) => current.map((c) => {
            const fresh = c.id != null ? byId.get(c.id) : undefined;
            if (!fresh) return c;
            const next = cardFrom(fresh, c.key);
            return { ...c, ...next, warning: next.warning, message: next.message };
          }));
        })
        .catch(() => undefined);
    }, VIDEO_POLL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preparingKey]);

  const closePrivacyNote = useCallback(() => {
    setPrivacyNote(false);
    try {
      localStorage.setItem(PRIVACY_SEEN_KEY, "1");
    } catch {
      // Shown again next time - harmless.
    }
  }, []);

  return {
    cards, desk, caps, privacyNote,
    addFiles, removeCard, takeForSend, restore, refreshDesk, removeFromDesk, refreshCaps,
    closePrivacyNote, cancelReaction,
  };
}

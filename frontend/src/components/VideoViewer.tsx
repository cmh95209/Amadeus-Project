import { useEffect, useRef, useState } from "react";

import { absoluteUrl, getVideoInfo, speechNote } from "../attachments";
import type { PictureInfo, VideoView } from "../attachments";

/** One video (video plan Phase 2): the player (the PC's copy - it plays in
 *  every browser, phone .mov too) and what is said in it, with times. Esc or
 *  a click outside closes it. */
export function VideoViewer({ video, onClose }: { video: PictureInfo; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const [view, setView] = useState<VideoView | null>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

  // Still being prepared / speech still being written: ask again until done.
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const load = () => {
      getVideoInfo(video)
        .then((data) => {
          if (!live) return;
          setView(data);
          const busy = data.video?.state === "preparing" || ["writing", "waiting"].includes(data.speech_state)
            || data.notes?.state === "writing";
          if (busy) timer = window.setTimeout(load, 2500);
        })
        .catch((error) => { if (live) setProblem(error instanceof Error ? error.message : "Could not open it."); });
    };
    load();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [video]);

  const shown = view ?? video;
  const title = [shown.number != null ? `Video ${shown.number}` : "", shown.name, shown.length]
    .filter(Boolean).join(" - ");
  const src = absoluteUrl(shown.video_url ?? undefined);
  const preparing = view?.video?.state === "preparing";
  const failed = view?.video?.state === "failed";
  const note = view ? speechNote(view) : "";
  return (
    <div className="picture-viewer document-viewer video-viewer" role="dialog" aria-modal="true"
      aria-label={title || "Video"}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <article>
        <header>
          <h2>{title || "Video"}</h2>
          {shown.caption && <p className="document-meta">{shown.caption}</p>}
        </header>
        <div className="video-body">
          {src && !failed
            ? <video src={src} controls playsInline preload="metadata"
              poster={absoluteUrl(shown.thumb_url ?? undefined)} />
            : (
              <p className="document-problem">
                {problem || (failed ? (view?.video?.message || "This video could not be prepared.")
                  : preparing ? `Preparing the video... ${view?.video?.pct ?? 0}%` : "Opening...")}
              </p>
            )}
          {note && <p className="document-meta">{note}</p>}
          {view?.notes?.state === "writing" && (
            <p className="document-meta">
              She is making watch notes: {view.notes.done} of {view.notes.total} parts so far.
            </p>
          )}
          {view?.notes_text && (
            <details className="document-notes" open>
              <summary>Her watch notes</summary>
              <pre>{view.notes_text}</pre>
            </details>
          )}
          {view?.speech_text && (
            <details className="document-notes" open={!view.notes_text}>
              <summary>What is said (written down - names can be wrong)</summary>
              <pre>{view.speech_text}</pre>
            </details>
          )}
        </div>
      </article>
      <button ref={closeButton} type="button" className="close-button picture-viewer-close"
        aria-label="Close" onClick={onClose}>✕</button>
    </div>
  );
}

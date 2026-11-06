import { useEffect, useRef, useState } from "react";

import { absoluteUrl, getDocumentText, pagesText } from "../attachments";
import type { DocumentView, PictureInfo } from "../attachments";

/** One document as she read it (E4 a): its text - only the part she was
 *  given when it was cut - and a link to the original file. Esc or a click
 *  outside closes it. */
export function DocumentViewer({ doc, onClose }: { doc: PictureInfo; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const [view, setView] = useState<DocumentView | null>(null);
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

  useEffect(() => {
    let live = true;
    getDocumentText(doc)
      .then((data) => { if (live) setView(data); })
      .catch((error) => { if (live) setProblem(error instanceof Error ? error.message : "Could not open it."); });
    return () => { live = false; };
  }, [doc]);

  const shown = view ?? doc;
  const title = [shown.number != null ? `Document ${shown.number}` : "", shown.name].filter(Boolean).join(" - ");
  return (
    <div className="picture-viewer document-viewer" role="dialog" aria-modal="true" aria-label={title || "Document"}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <article>
        <header>
          <h2>{title || "Document"}</h2>
          {shown.pages != null && (
            <p className="document-meta">
              {shown.notes?.state === "done"
                ? `${pagesText(shown)} - she looks up the pages you ask about.`
                : shown.notes?.state === "writing"
                  ? `She is making notes: ${shown.notes.done} of ${shown.notes.total} sections so far.`
                  : shown.cut
                    ? `She read the ${pagesText(shown)} - the rest did not fit.`
                    : pagesText(shown)}
              {shown.caption ? ` · ${shown.caption}` : ""}
            </p>
          )}
          {shown.original_url && (
            <a href={absoluteUrl(shown.original_url)} target="_blank" rel="noreferrer">Open the original</a>
          )}
          {view?.notes_text && (
            <details className="document-notes">
              <summary>Her reading notes</summary>
              <pre>{view.notes_text}</pre>
            </details>
          )}
        </header>
        {problem
          ? <p className="document-problem">{problem}</p>
          : <pre className="document-text">{view ? view.text : "Opening..."}</pre>}
      </article>
      <button ref={closeButton} type="button" className="close-button picture-viewer-close"
        aria-label="Close" onClick={onClose}>✕</button>
    </div>
  );
}

import { absoluteUrl, isDocument, isVideo, pagesText } from "../attachments";
import type { PictureInfo } from "../attachments";

/** ▶ and the length ("0:42") over a video's picture. */
export function PlayMark({ length }: { length?: string }) {
  return (
    <>
      <span className="play-mark" aria-hidden="true">▶</span>
      {length && <span className="video-length">{length}</span>}
    </>
  );
}

/** A document as a small chip (icon, number, name); click it to read it. */
export function DocumentChip({ doc, onOpen }: { doc: PictureInfo; onOpen: (p: PictureInfo) => void }) {
  return (
    <button type="button" className="document-chip" onClick={() => onOpen(doc)}
      title={[doc.name, doc.pages != null ? pagesText(doc) : "", doc.caption].filter(Boolean).join(" - ")}
      aria-label={`Open document ${doc.number ?? ""} ${doc.name}`}>
      <span className="document-icon" aria-hidden="true">📄</span>
      {doc.number != null && <span className="picture-number">{doc.number}</span>}
      <span className="document-name">{doc.name}</span>
    </button>
  );
}

/** The pictures and documents in your message bubble, each with its number
 *  in the chat (U1: so you can say "picture 2" / "document 1"). Click one to
 *  see it full size / read it. */
export function PictureStrip({ pictures, onOpen }: { pictures: PictureInfo[]; onOpen: (p: PictureInfo) => void }) {
  if (!pictures.length) return null;
  return (
    <span className="picture-strip">
      {pictures.map((p) => (isDocument(p)
        ? <DocumentChip key={p.id} doc={p} onOpen={onOpen} />
        : (
          <button key={p.id} type="button" className="picture-thumb" onClick={() => onOpen(p)}
            title={[p.caption || p.name, p.length].filter(Boolean).join(" - ")}
            aria-label={isVideo(p) ? `Play video ${p.number ?? ""} (${p.length ?? ""})`
              : `Open picture ${p.number ?? ""} full size`}>
            {p.thumb_url
              ? <img src={absoluteUrl(p.thumb_url)} alt={p.caption || p.name} loading="lazy" />
              : <span className="tray-placeholder" aria-hidden="true">…</span>}
            {isVideo(p) && <PlayMark length={p.length} />}
            {p.number != null && <span className="picture-number">{p.number}</span>}
          </button>
        )))}
    </span>
  );
}

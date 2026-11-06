import { useEffect, useRef } from "react";

import { absoluteUrl } from "../attachments";
import type { PictureInfo } from "../attachments";

/** One picture full size (the kept original). Esc or a click outside closes it. */
export function PictureViewer({ picture, onClose }: { picture: PictureInfo; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
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

  const src = absoluteUrl(picture.original_url || picture.url || picture.thumb_url);
  const title = [picture.number != null ? `Picture ${picture.number}` : "", picture.caption || picture.name]
    .filter(Boolean).join(" - ");
  return (
    <div className="picture-viewer" role="dialog" aria-modal="true" aria-label={title || "Picture"}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <figure>
        <img src={src} alt={picture.caption || picture.name} />
        {title && <figcaption>{title}</figcaption>}
      </figure>
      <button ref={closeButton} type="button" className="close-button picture-viewer-close"
        aria-label="Close" onClick={onClose}>✕</button>
    </div>
  );
}

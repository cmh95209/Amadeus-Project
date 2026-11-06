import { useEffect, useRef, useState } from "react";

import { DOCUMENT_ACCEPT, VIDEO_ACCEPT, filesFrom, infoLines, namedFile } from "../attachments";
import type { Capabilities } from "../attachments";

type Props = {
  caps: Capabilities | null;
  disabled?: boolean;
  onFiles: (files: File[]) => void;
  /** The menu opened: a good moment to refresh "how many fit". */
  onOpen?: () => void;
  /** "Paste from clipboard" found nothing it could use. */
  onNote?: (text: string) => void;
};

/** The "+" left of the message box (D1): its menu opens upward, with an
 *  info panel beside it (D1c). */
export function AttachMenu({ caps, disabled, onFiles, onOpen, onNote }: Props) {
  const [open, setOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [open]);

  function toggle() {
    if (!open) onOpen?.();
    setOpen(!open);
  }

  async function pasteFromClipboard() {
    setOpen(false);
    try {
      const items = await navigator.clipboard.read();
      const files: File[] = [];
      for (const item of items) {
        const type = item.types.find((t) => t.startsWith("image/"));
        if (type) files.push(namedFile(new File([await item.getType(type)], "image.png", { type })));
      }
      if (files.length) onFiles(files);
      else onNote?.("There is no picture on the clipboard.");
    } catch {
      onNote?.("The clipboard can't be read here - press Ctrl+V in the message box instead.");
    }
  }

  return (
    <div className={`attach${open ? " open" : ""}`} ref={root}>
      <button type="button" className="attach-button" disabled={disabled}
        aria-haspopup="menu" aria-expanded={open} aria-label="Add pictures, videos or files"
        onClick={toggle}>
        +
      </button>
      {!open && <span className="attach-hint" role="tooltip">Add pictures, videos or files</span>}
      {open && (
        <div className="attach-pop">
          <div className="attach-menu" role="menu">
            <button type="button" role="menuitem"
              onClick={() => { setOpen(false); fileInput.current?.click(); }}>
              🖼️ Pictures, videos or files
            </button>
            <button type="button" role="menuitem" onClick={() => void pasteFromClipboard()}>
              📋 Paste from clipboard
            </button>
          </div>
          <div className="attach-info" role="note">
            {infoLines(caps).map((line) => (
              <p key={line} className={caps?.pictures === false && line === caps.reason ? "warn" : ""}>
                {line}
              </p>
            ))}
          </div>
        </div>
      )}
      <input ref={fileInput} type="file" multiple hidden
        accept={"image/jpeg,image/png,image/webp,image/gif,image/bmp,.heic,.heif," + VIDEO_ACCEPT + ","
          + DOCUMENT_ACCEPT}
        onChange={(event) => {
          onFiles(filesFrom(event.currentTarget.files));
          event.currentTarget.value = "";
        }} />
    </div>
  );
}

import { DESK_TOOLTIP, PRIVACY_NOTE, absoluteUrl, isDocument, isVideo } from "../attachments";
import type { PictureInfo, TrayCard } from "../attachments";
import { DocumentChip, PlayMark } from "./PictureStrip";

const STATE_LABEL = { preparing: "Preparing...", ready: "Ready", error: "Can't use" } as const;

type Props = {
  cards: TrayCard[];
  desk: PictureInfo[];
  privacyNote: boolean;
  onRemoveCard: (key: string) => void;
  onRemoveFromDesk: (id: number) => void;
  onOpenPicture: (picture: PictureInfo) => void;
  onClosePrivacyNote: () => void;
};

/** The line under a card's name: why it can't be used, how far a video is
 *  (uploading / preparing), a document's warning, or its state. */
export function trayState(card: TrayCard): string {
  if (card.state === "error") return card.message ?? STATE_LABEL.error;
  if (card.progress) return card.progress;
  if (card.warning && (card.state === "ready" || card.kind === "video")) return card.warning;
  return STATE_LABEL[card.state];
}

/** Above the message box: the privacy note (once), the tray of cards not
 *  sent yet, and the pictures on her desk (D3). */
export function AttachmentTray({ cards, desk, privacyNote, onRemoveCard, onRemoveFromDesk,
  onOpenPicture, onClosePrivacyNote }: Props) {
  if (!cards.length && !desk.length && !privacyNote) return null;
  return (
    <div className="attachments-area">
      {privacyNote && (
        <div className="attach-privacy" role="status">
          <span>{PRIVACY_NOTE}</span>
          <button type="button" onClick={onClosePrivacyNote}>Got it</button>
        </div>
      )}
      {desk.length > 0 && (
        <div className="desk-row" aria-label="On her desk">
          <span className="desk-label">
            On her desk
            <span className="desk-info" tabIndex={0} title={DESK_TOOLTIP} aria-label={DESK_TOOLTIP}>i</span>
          </span>
          {desk.map((p) => (
            <span key={p.id} className="desk-item">
              {isDocument(p)
                ? <DocumentChip doc={p} onOpen={onOpenPicture} />
                : (
                  <button type="button" className="desk-thumb" title={p.caption || p.name}
                    onClick={() => onOpenPicture(p)}>
                    <img src={absoluteUrl(p.thumb_url ?? undefined)} alt={p.caption || p.name} />
                    {isVideo(p) && <PlayMark length={p.length} />}
                    {p.number != null && <span className="picture-number">{p.number}</span>}
                  </button>
                )}
              <button type="button" className="card-remove"
                aria-label={`Take ${isDocument(p) ? "document" : isVideo(p) ? "video" : "picture"} ${p.number ?? ""} off her desk`}
                title="Take it off her desk" onClick={() => onRemoveFromDesk(p.id)}>✕</button>
            </span>
          ))}
        </div>
      )}
      {cards.length > 0 && (
        <div className="tray" aria-label="Attached, not sent yet">
          {cards.map((card) => (
            <span key={card.key} className={`tray-card ${card.state}${card.warning ? " warn" : ""}`}
              title={card.message || card.warning || card.name}>
              {card.thumbUrl
                ? (
                  <span className="tray-thumb">
                    <img src={card.thumbUrl} alt="" />
                    {card.kind === "video" && <PlayMark length={card.length} />}
                  </span>
                )
                : <span className="tray-placeholder" aria-hidden="true">
                  {card.state === "error" ? "!" : card.kind === "document" ? "📄" : card.kind === "video" ? "▶" : "…"}
                </span>}
              <span className="tray-text">
                <span className="tray-name">{card.name}</span>
                <span className="tray-state">
                  {trayState(card)}
                </span>
              </span>
              <button type="button" className="card-remove" aria-label={`Remove ${card.name}`}
                onClick={() => onRemoveCard(card.key)}>✕</button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

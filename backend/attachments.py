"""Pictures the user shows Amadeus (attachments Phase 1, 2026-10-26).

The flow: a picture is uploaded the moment it is attached (POST /attachments):
checked, shrunk (longest side <= 1568 px), its metadata (GPS, camera, ...)
removed, a thumbnail made, saved as user data next to memory.db - a "card"
not sent yet. On send its id rides with the message (attachment_ids) and the
card is linked to the user's message and put on her desk. Only her REPLY
calls see pictures for real (with_pictures): the newest message's pictures
plus the ones on her desk; every helper call and the stored history stay
text, where a picture is a label "[Picture 3 (turn 5) from the user:
<caption>]" (its number in the chat + the turn it came in). After her reply
one short background call writes that caption. She also gets a short list of
the chat's pictures, and an earlier picture the user clearly asks about
("picture 2", "yesterday's photo") is shown to her again for that reply.

Documents (Phase 4, 2026-10-28; MH E1 B, E2 a, E3 4,000, E4 a, E5 a): a PDF,
Word .docx or text/code file (and long pasted text, sent as a "Pasted text"
file by the screen) is read into plain text on upload. While it is on her
desk her reply calls get that text as its own message right before the one
it came with: "[Document 2 (turn 5) from the user: "report.pdf", 4 pages]
... [End of document 2]" (that message keeps the label). A document longer than the room left is cut
when it is sent: she reads what fits and the label says "first 3 of 12
pages". After her reply one short call writes its one-line summary (the
label once it is off her desk, her list, her long-term memory).
"""
import base64
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

import requests

import memory as store

try:  # Pillow: installed with the app (both installers); tests need it too
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - the upload route answers honestly
    Image = None


MAX_FILE_BYTES = 20 * 1024 * 1024      # per file (MH P2: no count limit)
MAX_SIDE = 1568                        # longest side she is shown
THUMB_SIDE = 320
MAX_PIXELS = 80_000_000                # a bigger "picture" is refused unopened
UNSENT_MAX_AGE = timedelta(days=1)     # cards never sent are cleaned at startup
PICTURE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp",
                   "GIF": "image/gif", "BMP": "image/bmp"}
FORMAT_NAMES = "JPG, PNG, WebP, GIF or BMP"
_HEIC_EXT = (".heic", ".heif")
_HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1")
TEXT_EXT = (".txt", ".text", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".xml",
            ".yaml", ".yml", ".ini", ".toml", ".cfg", ".conf", ".srt", ".vtt", ".tex", ".rst",
            ".html", ".htm", ".css", ".py", ".js", ".jsx", ".ts", ".tsx", ".c", ".cpp", ".h",
            ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".swift", ".lua", ".r",
            ".sql", ".sh", ".bat", ".ps1")
_LATER_EXT = (".doc", ".rtf", ".odt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp",
              ".pages", ".numbers", ".key", ".epub")
DOCUMENT_EXT = (".pdf", ".docx") + TEXT_EXT
DOC_FORMAT_NAMES = "PDF, Word (.docx) or text files"
DOC_MAX_TOKENS = 10_000                # one document's ceiling (~20 pages; cloud cost)
DOC_MIN_TOKENS = 250                   # less room than this = no room for a document
DOC_MAX_CHARS = 1_000_000              # text kept from a huge file

MESSAGES = {
    "too_big": "This file is over 20 MB - too big to show her.",
    "too_many_pixels": "This picture is far too large to open. Try a smaller copy of it.",
    "heic": "This is an iPhone HEIC picture. Save it as JPG or PNG first, then attach that.",
    "unsupported": ("This file type can't be shown to her. Pictures: " + FORMAT_NAMES
                    + ". Documents: " + DOC_FORMAT_NAMES + "."),
    "broken": "This picture could not be opened (the file may be damaged).",
    "empty": "This file is empty.",
    "no_pillow": "The picture tools are missing - run the installer again to add them.",
    "later": ("She can't read this file type yet. Save it as PDF, Word .docx or CSV "
              "and attach that."),
    "scanned": "This PDF has no text she can read (it looks scanned). She can't read scans yet.",
    "password": "This PDF is locked with a password. Save an unlocked copy and attach that.",
    "no_text": "There is no text in this file she could read.",
    "doc_broken": "This document could not be opened (the file may be damaged).",
    "no_doc_tools": "The document tools are missing - run the installer again to add them.",
    "video_too_big": "This video is over 500 MB. Trim it in your gallery first, then attach that.",
    "video_too_long": "This video is over 10 minutes. Trim it in your gallery first, then attach that.",
    "video_unsupported": "This video type can't be opened. Videos: MP4, MOV, WebM, M4V, MKV, AVI or 3GP.",
    "video_broken": "This video could not be opened (the file may be damaged or unfinished).",
    "not_video": "There is no video in this file.",
    "no_ffmpeg": "The video tools (FFmpeg) are missing - run the installer again to add them.",
    "upload_gone": "This upload was lost - attach the video again.",
    "upload_incomplete": "The video did not arrive in full - attach it again.",
}


class AttachmentError(ValueError):
    def __init__(self, code: str):
        super().__init__(MESSAGES.get(code, code))
        self.code = code


# ---------- Upload: check, shrink, strip, store ----------

def _clean_name(name) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:120] or "picture"


def _is_heic(name: str, data: bytes) -> bool:
    return name.lower().endswith(_HEIC_EXT) or (data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS)


def _flatten(img):
    """One RGB frame: the first frame of an animation, transparency on white."""
    try:
        img.seek(0)
    except (AttributeError, EOFError):
        pass
    img = ImageOps.exif_transpose(img)  # turn phone photos upright, then drop EXIF
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        back = Image.new("RGB", rgba.size, (255, 255, 255))
        back.paste(rgba, mask=rgba.split()[-1])
        return back
    return img.convert("RGB")


def _encode(img, lossless: bool, big: bool = False) -> tuple:
    """(bytes, mime). Screenshots / drawings stay PNG (sharp text); photos JPEG.
    A fresh encode writes no metadata: GPS, camera, dates are gone. big = the
    kept full-size original (a higher size limit, a higher JPEG quality)."""
    out = io.BytesIO()
    if lossless:
        img.save(out, "PNG", optimize=not big)
        if out.tell() <= (24 if big else 6) * 1024 * 1024:
            return out.getvalue(), "image/png"
        out = io.BytesIO()
    img.save(out, "JPEG", quality=95 if big else 90, optimize=True)
    return out.getvalue(), "image/jpeg"


def prepare_picture(name: str, data: bytes) -> dict:
    """Check and shrink one uploaded file. Returns {"bytes", "mime", "thumb",
    "width", "height", "name"}; raises AttachmentError with a plain reason."""
    name = _clean_name(name)
    if not data:
        raise AttachmentError("empty")
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentError("too_big")
    if _is_heic(name, data):
        raise AttachmentError("heic")
    if Image is None:
        raise AttachmentError("no_pillow")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise AttachmentError("later" if name.lower().endswith(_LATER_EXT) else "unsupported")
    if fmt not in PICTURE_FORMATS:
        raise AttachmentError("unsupported")
    if img.width * img.height > MAX_PIXELS:
        raise AttachmentError("too_many_pixels")
    try:
        img.load()
        flat = _flatten(img)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise AttachmentError("broken")
    lossless = fmt in ("PNG", "GIF", "BMP")
    original = None
    if max(flat.size) > MAX_SIDE:
        # The full-size picture is kept too (metadata removed the same way):
        # the screen's full-size viewer, and later models that can use more
        # detail. She is shown the shrunk copy - her model shrinks anything
        # bigger itself (it takes ~500-1,100 pieces per picture at most).
        original = _encode(flat, lossless=lossless, big=True)
    flat.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    body, mime = _encode(flat, lossless=lossless)
    thumb = flat.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    tb = io.BytesIO()
    thumb.save(tb, "JPEG", quality=82)
    return {"bytes": body, "mime": mime, "thumb": tb.getvalue(),
            "original": original,
            "width": flat.width, "height": flat.height, "name": name}


def is_document_name(name: str) -> bool:
    return _clean_name(name).lower().endswith(DOCUMENT_EXT)


def _tidy_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()[:DOC_MAX_CHARS]


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise AttachmentError("unsupported")   # a binary file named .txt
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
    except Exception:
        best = None
    if best is None:
        raise AttachmentError("unsupported")
    return str(best)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise AttachmentError("no_doc_tools")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise AttachmentError("password")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except AttachmentError:
        raise
    except Exception as exc:
        if "password" in str(exc).lower() or "decrypt" in str(exc).lower():
            raise AttachmentError("password")
        raise AttachmentError("doc_broken")
    text = "\n\n".join(p for p in pages if p)
    # A scan has pages but (almost) no text on them.
    if pages and len(re.sub(r"\s", "", text)) < 5 * len(pages):
        raise AttachmentError("scanned")
    return text


def _docx_text(data: bytes) -> str:
    try:
        import docx
    except ImportError:
        raise AttachmentError("no_doc_tools")
    try:
        document = docx.Document(io.BytesIO(data))
        lines = []
        for block in document.iter_inner_content():   # paragraphs and tables, in order
            if hasattr(block, "rows"):
                for row in block.rows:
                    cells = []
                    for cell in row.cells:
                        value = " ".join(cell.text.split())
                        if not cells or cells[-1] != value:   # merged cells repeat
                            cells.append(value)
                    lines.append(" | ".join(cells))
                lines.append("")
            else:
                lines.append(block.text)
    except Exception:
        raise AttachmentError("doc_broken")
    return "\n".join(lines)


def prepare_document(name: str, data: bytes) -> dict:
    """Read one uploaded document into plain text. Returns {"text", "name",
    "mime", "ext"}; raises AttachmentError with a plain reason."""
    name = _clean_name(name)
    if not data:
        raise AttachmentError("empty")
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentError("too_big")
    ext = os.path.splitext(name.lower())[1]
    if ext == ".pdf":
        text, mime = _pdf_text(data), "application/pdf"
    elif ext == ".docx":
        text = _docx_text(data)
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        text, mime = _decode_text(data), "text/plain"
    text = _tidy_text(text)
    if not text:
        raise AttachmentError("no_text")
    return {"text": text, "name": name, "mime": mime, "ext": ext or ".txt"}


def _save_document(name: str, data: bytes, conv: int) -> dict:
    prepared = prepare_document(name, data)
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("INSERT INTO attachments (conversation_id, kind, name, mime, bytes, tokens, "
                  "created_at) VALUES (?, 'document', ?, ?, ?, ?, ?)",
                  (conv, prepared["name"], prepared["mime"], len(data),
                   store.estimate_tokens(prepared["text"]),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        new_id = c.lastrowid
        rel = f"{conv}/{new_id}.txt"
        rel_orig = f"{conv}/{new_id}_original{prepared['ext']}"
        os.makedirs(os.path.join(store.attachments_dir(), str(conv)), exist_ok=True)
        with open(os.path.join(store.attachments_dir(), rel), "w", encoding="utf-8", newline="\n") as f:
            f.write(prepared["text"])
        with open(os.path.join(store.attachments_dir(), rel_orig), "wb") as f:
            f.write(data)
        c.execute("UPDATE attachments SET path = ?, original = ? WHERE id = ?",
                  (rel, rel_orig, new_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get(new_id)


def save_upload(name: str, data: bytes, conversation_id=None) -> dict:
    """prepare_picture / prepare_document + store it as a card not sent yet.
    Returns the row."""
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    if is_document_name(name):
        return _save_document(name, data, conv)
    prepared = prepare_picture(name, data)
    ext = ".png" if prepared["mime"] == "image/png" else ".jpg"
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("INSERT INTO attachments (conversation_id, kind, name, mime, bytes, width, "
                  "height, created_at) VALUES (?, 'picture', ?, ?, ?, ?, ?, ?)",
                  (conv, prepared["name"], prepared["mime"], len(prepared["bytes"]),
                   prepared["width"], prepared["height"],
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        new_id = c.lastrowid
        rel = f"{conv}/{new_id}{ext}"
        rel_thumb = f"{conv}/{new_id}_thumb.jpg"
        folder = os.path.join(store.attachments_dir(), str(conv))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(store.attachments_dir(), rel), "wb") as f:
            f.write(prepared["bytes"])
        with open(os.path.join(store.attachments_dir(), rel_thumb), "wb") as f:
            f.write(prepared["thumb"])
        rel_orig = None
        if prepared.get("original"):
            body, mime = prepared["original"]
            rel_orig = f"{conv}/{new_id}_original" + (".png" if mime == "image/png" else ".jpg")
            with open(os.path.join(store.attachments_dir(), rel_orig), "wb") as f:
                f.write(body)
        c.execute("UPDATE attachments SET path = ?, thumb = ?, original = ? WHERE id = ?",
                  (rel, rel_thumb, rel_orig, new_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get(new_id)


def get(attachment_id):
    rows = store.attachments_where("id = ?", (int(attachment_id),))
    return rows[0] if rows else None


def file_path(attachment_id, thumb: bool = False, original: bool = False):
    """The stored file of an attachment (None when missing). original = the
    full-size copy (the copy she sees when the picture was small already)."""
    row = get(attachment_id)
    rel = row and (row["thumb"] if thumb else (row.get("original") or row["path"]) if original
                   else row["path"])
    if not rel:
        return None
    root = os.path.realpath(store.attachments_dir())
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    return full


def card(row, conversation_id=None) -> dict:
    """What the screen gets back for an uploaded card."""
    conv = conversation_id if conversation_id is not None else row.get("conversation_id")
    item = store.attachments_for_ui([row])[0]
    item["room"] = room_left(conv)
    if row.get("kind") == "video":
        import video   # late import: video imports this module
        item.update(video.card_info(row))
    if row.get("kind") == "document":
        # E2 a: the card warns before sending when she can read only part.
        total = int(row.get("tokens") or 0)
        can = min(total, DOC_MAX_TOKENS, max(0, room_tokens(conv, skip=(row["id"],))))
        item["read_pages"] = store.document_pages(can) if can >= min(total, DOC_MIN_TOKENS) else 0
        progress = notes_progress(row)
        if progress:
            item["notes"] = progress
        if can < min(total, DOC_MIN_TOKENS):
            item["warning"] = ("No room for this document with what is on her desk - "
                               "take something off her desk first.")
        elif progress.get("state") == "done":
            item["warning"] = (f"Long document: she has notes on all {item['pages']} pages and "
                               "looks up the pages you ask about.")
        elif progress.get("state") == "writing":
            pct = int(100 * progress["done"] / max(1, progress["total"]))
            item["warning"] = (f"Reading... {pct}% - she is making notes on all {item['pages']} "
                               f"pages (until then she reads the first {item['read_pages']}).")
        elif can < total:
            item["warning"] = (f"Long document: she will read the first {item['read_pages']} "
                               f"of {item['pages']} pages.")
    return item


# ---------- Send: link cards to the message, desk, failures ----------

def pending(ids) -> list:
    """The cards among ids that are still waiting to be sent (in id order)."""
    clean = sorted({int(i) for i in ids or [] if isinstance(i, int) and not isinstance(i, bool)})
    if not clean:
        return []
    return store.attachments_where("message_id IS NULL AND id IN (" + ",".join("?" * len(clean))
                                   + ")", clean)


def claim(ids, message_id: int, conversation_id: int) -> list:
    """Link the waiting cards to the user's message and put them on her desk
    (a card attached in another tab follows the message). Each gets its
    number in this chat (picture 1, 2, ... and document 1, 2, ... apart) and
    the message's turn. A document's readable part is fixed now: what fits
    next to her desk (E2 a) - it never changes afterwards, so the message
    stays the same on every later turn."""
    rows = pending(ids)
    if not rows:
        return []
    turn = store.message_turn(message_id)
    conv = int(conversation_id)
    # Room for this message's documents: her desk only (other cards still
    # in the tray are not sent), minus this message's pictures.
    avail = (room_tokens(conv, waiting=False, skip=[r["id"] for r in rows])
             - sum(_cost(r) for r in rows if r["kind"] != "document"))
    reads = {}
    for r in rows:
        if r["kind"] == "document":
            text = document_text(r)
            total = int(r.get("tokens") or 0)
            allowed = max(0, min(total, DOC_MAX_TOKENS, avail))
            part = cut_text(text, allowed) if allowed >= min(total, DOC_MIN_TOKENS) else ""
            used = store.estimate_tokens(part) if part else 0
            reads[r["id"]] = (len(part), used if part != text else int(r.get("tokens") or used))
            avail -= used
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("BEGIN IMMEDIATE")
        top = {}
        for kind in {r["kind"] for r in rows}:
            (top[kind],) = c.execute("SELECT COALESCE(MAX(chat_no), 0) FROM attachments "
                                     "WHERE conversation_id = ? AND kind = ?", (conv, kind)).fetchone()
        for r in rows:
            top[r["kind"]] = int(top[r["kind"]]) + 1
            chars, tokens = reads.get(r["id"], (None, None))
            c.execute("UPDATE attachments SET message_id = ?, conversation_id = ?, on_desk = 1, "
                      "chat_no = ?, turn = ?, read_chars = ?, read_tokens = ? "
                      "WHERE id = ? AND message_id IS NULL",
                      (int(message_id), conv, top[r["kind"]], turn, chars, tokens, r["id"]))
        conn.commit()
    finally:
        conn.close()
    claimed = store.attachments_where("message_id = ?", (int(message_id),))
    for r in claimed:
        maybe_start_notes(r, conv)     # cut when sent: her notes cover the rest
    return claimed


def release(message_id: int) -> None:
    """Her reply failed and the user's message is being removed: its pictures
    go back to the tray (not sent), so trying again needs no new upload."""
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("UPDATE attachments SET message_id = NULL, on_desk = 0, chat_no = NULL, "
                  "turn = NULL, read_chars = NULL, read_tokens = NULL WHERE message_id = ?",
                  (int(message_id),))
        conn.commit()
    finally:
        conn.close()


def remove(attachment_id) -> str:
    """The user's X: a card not sent yet is deleted ("deleted"); a sent one
    only leaves her desk ("off_desk") - it stays in the message it came with.
    "" when there is no such attachment."""
    row = get(attachment_id)
    if row is None:
        return ""
    conn = store._connect_db()
    try:
        c = conn.cursor()
        if row["message_id"] is None:
            files = store._delete_attachments(c, "id = ?", (row["id"],))
            conn.commit()
            store.remove_attachment_files(files)
            return "deleted"
        c.execute("UPDATE attachments SET on_desk = 0 WHERE id = ?", (row["id"],))
        conn.commit()
        return "off_desk"
    finally:
        conn.close()


def desk(conversation_id=None) -> list:
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    return store.attachments_where("conversation_id = ? AND on_desk = 1 AND message_id IS NOT NULL",
                                   (conv,))


def cleanup_unsent(now=None) -> int:
    """Startup: delete cards that were never sent and are over a day old, and
    stray files no row points at. Returns how many cards went."""
    cutoff = ((now or datetime.now()) - UNSENT_MAX_AGE).strftime("%Y-%m-%d %H:%M:%S")
    conn = store._connect_db()
    try:
        c = conn.cursor()
        files = store._delete_attachments(c, "message_id IS NULL AND created_at < ?", (cutoff,))
        gone = c.rowcount
        known = set()
        for row in c.execute("SELECT path, thumb, original, kind, id FROM attachments").fetchall():
            known.update(p for p in row[:3] if p)
            if row[3] == "document" and row[0]:
                known.add(store.document_notes_file(row[0]))
            if row[3] == "video":
                known.update(store.video_files(next((p for p in row[:3] if p), ""), row[4]))
        conn.commit()
    finally:
        conn.close()
    store.remove_attachment_files(files)
    root = store.attachments_dir()
    stray = []
    if os.path.isdir(root):
        for folder in os.listdir(root):
            sub = os.path.join(root, folder)
            if not os.path.isdir(sub):
                continue
            for fname in os.listdir(sub):
                rel = f"{folder}/{fname}"
                full = os.path.join(sub, fname)
                try:
                    old = datetime.fromtimestamp(os.path.getmtime(full)) < (now or datetime.now()) - UNSENT_MAX_AGE
                except OSError:
                    continue
                if rel not in known and old:
                    stray.append(rel)
    store.remove_attachment_files(stray)
    return max(0, gone or 0)


# ---------- Room in her context (MH P2: no count limit, only what fits) ----------

def _text_floor(budget: int) -> int:
    """Room always kept for words (her rules + the recent talk)."""
    return max(1500, budget // 4)


def _cost(row) -> int:
    """The room one attachment takes in her prompt: a picture about
    PICTURE_TOKENS, a document the part she reads (a card not sent yet: as
    much as she could read of it)."""
    if row.get("kind") == "video":
        return int(row.get("tokens") or 0)
    if row.get("kind") != "document":
        return store.PICTURE_TOKENS
    if row.get("read_tokens") is not None:
        return int(row["read_tokens"])
    return min(int(row.get("tokens") or 0), DOC_MAX_TOKENS)


def room_tokens(conversation_id=None, waiting: bool = True, skip=()) -> int:
    """Tokens left for attachments with her current model, after her desk
    (and the cards waiting in the tray, unless waiting=False); rows in skip
    are not counted. Can be negative."""
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    budget = store.effective_context_budget()
    rows = desk(conv) + (store.attachments_where("conversation_id = ? AND message_id IS NULL",
                                                 (conv,)) if waiting else [])
    skip = set(skip or ())
    return budget - _text_floor(budget) - sum(_cost(r) for r in rows if r["id"] not in skip)


def room_left(conversation_id=None, extra: int = 0) -> int:
    """About how many more pictures fit with her current model, counting the
    desk and the cards waiting in the tray (+ extra not counted yet)."""
    left = room_tokens(conversation_id) // store.PICTURE_TOKENS - int(extra)
    return max(0, left)


def fits(ids, conversation_id=None) -> tuple:
    """(ok, how_many_pictures_fit) for sending these cards with today's desk.
    A document needs room for at least a little of it (the rest is cut)."""
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    budget = store.effective_context_budget()
    rows = pending(ids)
    need = sum(min(int(r.get("tokens") or 0), DOC_MIN_TOKENS) if r["kind"] == "document"
               else _cost(r) for r in rows)
    limit = (budget - _text_floor(budget)) // store.PICTURE_TOKENS
    return room_tokens(conv, waiting=False, skip=[r["id"] for r in rows]) >= need, max(0, limit)


# ---------- A document's text ----------

_DOC_TEXTS = {}       # id -> (mtime, text); a few, so desk documents are not re-read
_DOC_TEXTS_MAX = 8


def document_text(row) -> str:
    """The whole text read out of a document ("" when its file is missing)."""
    full = file_path(row["id"])
    if not full:
        return ""
    mtime = os.path.getmtime(full)
    hit = _DOC_TEXTS.get(row["id"])
    if hit and hit[0] == mtime:
        return hit[1]
    with open(full, encoding="utf-8") as f:
        text = f.read()
    if len(_DOC_TEXTS) >= _DOC_TEXTS_MAX:
        _DOC_TEXTS.pop(next(iter(_DOC_TEXTS)))
    _DOC_TEXTS[row["id"]] = (mtime, text)
    return text


def read_part(row) -> str:
    """The part of a document she reads (all of it before it is sent)."""
    text = document_text(row)
    chars = row.get("read_chars")
    return text if chars is None else text[:int(chars)]


def cut_text(text: str, max_tokens: int) -> str:
    """The longest start of text within max_tokens (store.estimate_tokens
    counting), ended at a line or sentence break when one is close."""
    if store.estimate_tokens(text) <= max_tokens:
        return text
    budget = max(0, max_tokens - 1)
    used = 0.0
    end = 0
    for ch in text:
        o = ord(ch)
        used += (1 / 1.5) if ((0x3040 <= o <= 0x30FF) or (0x3400 <= o <= 0x9FFF)
                              or (0xFF00 <= o <= 0xFFEF)) else (1 / 3.5)
        if used > budget:
            break
        end += 1
    part = text[:end]
    floor = int(end * 0.85)
    for mark in ("\n\n", "\n", "。", ". "):
        at = part.rfind(mark)
        if at >= floor:
            return part[:at + len(mark)].rstrip()
    return part.rstrip()


# ---------- Long documents: pages, search, reading notes (Phase 4b) ----------
# MH 2026-10-29 (A + C, picks G1-G6 all a): a document longer than she can
# read in full is cut into pages (~500 tokens, at paragraph breaks). In the
# background, after it is attached, one short note per section (2 pages) is
# written - exact numbers, dates and names copied word for word (Hermes'
# rule) - and, for long ones, one note per part (8 sections). Her prompt then
# holds the notes of the whole document (stable: reused every turn) and, in
# the changing part, the pages that match THIS message (keyword search with
# Japanese-aware matching, the section notes added to each page - the
# "contextual retrieval" idea) or the pages the message names ("page 54").

SECTION_PAGES = 2
PART_SECTIONS = 8
FOUND_PAGES_MAX = 3
NOTES_SHARE = 0.6          # of the document's room, at most, for the notes
NOTE_MAX_CHARS = 400


def _page_spans(text: str) -> list:
    """[(start, end)] character spans of the pages of a text."""
    spans = []
    at = 0
    window = store.DOCUMENT_PAGE_TOKENS * 8      # plenty of characters for one page
    while at < len(text):
        while at < len(text) and text[at].isspace():
            at += 1
        if at >= len(text):
            break
        part = cut_text(text[at:at + window], store.DOCUMENT_PAGE_TOKENS)
        if not part:
            part = text[at:at + window]
        spans.append((at, at + len(part)))
        at += len(part)
    return spans


_PAGES = {}          # id -> (mtime, spans)


def pages_of(row) -> list:
    """The pages of a document, as text (page 1 first)."""
    text = document_text(row)
    full = file_path(row["id"])
    key = os.path.getmtime(full) if full else 0
    hit = _PAGES.get(row["id"])
    if not hit or hit[0] != key:
        if len(_PAGES) >= _DOC_TEXTS_MAX:
            _PAGES.pop(next(iter(_PAGES)))
        hit = _PAGES[row["id"]] = (key, _page_spans(text))
    return [text[a:b] for a, b in hit[1]]


def is_long(row) -> bool:
    """A document she can't read in full: it gets pages + notes."""
    return row.get("kind") == "document" and (
        row.get("notes") or (row.get("read_tokens") is not None
                             and int(row["read_tokens"]) < int(row.get("tokens") or 0)))


# --- Reading notes: a JSON file next to the text, written in the background --

def _notes_file(row):
    return os.path.join(store.attachments_dir(), store.document_notes_file(row["path"])) \
        if row.get("path") else None


def load_notes(row) -> dict:
    """{"sections": [{"from", "to", "note"}], "parts": [...], "total": n} or {}."""
    path = _notes_file(row)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_notes(row, data) -> None:
    path = _notes_file(row)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _set_notes_state(attachment_id, state) -> None:
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("UPDATE attachments SET notes = ? WHERE id = ?", (state, int(attachment_id)))
        conn.commit()
    finally:
        conn.close()


def notes_progress(row) -> dict:
    """{"state": "writing" / "done" / "failed", "done": k, "total": n} or {}."""
    state = row.get("notes")
    if not state:
        return {}
    data = load_notes(row)
    total = int(data.get("total") or 0) or max(1, -(-len(pages_of(row)) // SECTION_PAGES))
    name = {store.NOTES_WRITING: "writing", store.NOTES_DONE: "done",
            store.NOTES_FAILED: "failed"}.get(state, "writing")
    return {"state": name, "done": len(data.get("sections") or []), "total": total}


NOTE_PROMPT = (
    "You are making reading notes on a long document so you can answer questions about it "
    "later. Write ONE note of at most 45 words on this part of the document \"{name}\" "
    "(pages {a}-{b}): what it contains. Copy exact numbers, dates, names, places and other "
    "identifiers word for word, in their original script. No quotes, no 'This section'.")

PART_PROMPT = (
    "Combine these reading notes on pages {a}-{b} of the document \"{name}\" into ONE note "
    "of at most 60 words. Keep the most important exact numbers, dates and names word for "
    "word. No quotes.")


def _clean_note(reply) -> str:
    text = reply.content if isinstance(reply.content, str) else _text(reply.content)
    text = re.sub(r"<[^>]*>", " ", text or "")
    text = re.sub(r"[\[\]]", " ", text)      # her prompt's brackets belong to labels
    return re.sub(r"\s+", " ", text).strip().strip('"').strip()[:NOTE_MAX_CHARS]


def write_notes(llm, row) -> bool:
    """Write (or finish) a document's reading notes, one call per section,
    then the part notes and a new one-line summary from them. Stops quietly
    when the document is deleted meanwhile. True when finished."""
    pages = pages_of(row)
    sections = [(a, min(a + SECTION_PAGES - 1, len(pages)))
                for a in range(1, len(pages) + 1, SECTION_PAGES)]
    data = load_notes(row)
    data["total"] = len(sections)
    done = data.setdefault("sections", [])
    name = row.get("name") or "document"
    for a, b in sections[len(done):]:
        if get(row["id"]) is None:
            return False
        text = "\n\n".join(pages[a - 1:b])
        note = _clean_note(llm.invoke([{"role": "user", "content": (
            NOTE_PROMPT.format(name=name, a=a, b=b) + "\n\n" + text)}]))
        done.append({"from": a, "to": b, "note": note})
        _save_notes(row, data)
    if len(done) > PART_SECTIONS and len(data.get("parts") or []) * PART_SECTIONS < len(done):
        parts = []
        for i in range(0, len(done), PART_SECTIONS):
            group = done[i:i + PART_SECTIONS]
            a, b = group[0]["from"], group[-1]["to"]
            lines = "\n".join(f"- pages {s['from']}-{s['to']}: {s['note']}" for s in group)
            parts.append({"from": a, "to": b, "note": _clean_note(llm.invoke([{
                "role": "user", "content": PART_PROMPT.format(name=name, a=a, b=b) + "\n\n" + lines}]))})
        data["parts"] = parts
        _save_notes(row, data)
    if get(row["id"]) is None:
        return False
    _set_notes_state(row["id"], store.NOTES_DONE)
    try:   # her one-line summary, now from the whole document
        overview = "\n".join(s["note"] for s in (data.get("parts") or done))
        save_caption(row["id"], _clean_note(llm.invoke([{"role": "user", "content": (
            SUMMARY_PROMPT + f'\n\nReading notes on the whole document "{name}":\n' + overview)}])))
    except Exception as exc:
        print(f"[Amadeus] Document #{row['id']}: summary from notes failed:", repr(exc))
    return True


_NOTES_QUEUE = []
_NOTES_LOCK = threading.Lock()
_NOTES_WAKE = threading.Event()
_NOTES_THREAD = [None]


def _notes_llm():
    import chat   # late import: chat imports this module
    return chat._translation_llm()


def notes_job(attachment_id) -> None:
    """One document's reading notes, or a video's watch notes (the worker's
    job)."""
    row = get(attachment_id)
    if row is None or row.get("notes") == store.NOTES_DONE:
        return
    try:
        if row.get("kind") == "video":
            import video   # late import: video imports this module
            video.write_watch_notes(_notes_llm(), row)
        else:
            write_notes(_notes_llm(), row)
    except Exception as exc:
        # Her model was busy / away: what is written stays; the start of
        # the document is read in full as before (a video: its stills).
        print(f"[Amadeus] Attachment #{attachment_id}: notes stopped:", repr(exc))
        if get(attachment_id) is not None:
            _set_notes_state(attachment_id, store.NOTES_FAILED)


def _notes_worker() -> None:
    while True:
        _NOTES_WAKE.wait()
        with _NOTES_LOCK:
            if not _NOTES_QUEUE:
                _NOTES_WAKE.clear()
                continue
            attachment_id = _NOTES_QUEUE.pop(0)
        notes_job(attachment_id)


def start_notes(row) -> None:
    """Queue a document's reading notes or a video's watch notes (one
    worker: her model runs one note at a time next to her replies)."""
    if row.get("notes") == store.NOTES_DONE:
        return
    if row.get("notes") != store.NOTES_WRITING:
        _set_notes_state(row["id"], store.NOTES_WRITING)
    with _NOTES_LOCK:
        if row["id"] not in _NOTES_QUEUE:
            _NOTES_QUEUE.append(row["id"])
        if _NOTES_THREAD[0] is None or not _NOTES_THREAD[0].is_alive():
            _NOTES_THREAD[0] = threading.Thread(target=_notes_worker, daemon=True,
                                                name="document-notes")
            _NOTES_THREAD[0].start()
    _NOTES_WAKE.set()


def maybe_start_notes(row, conversation_id=None) -> bool:
    """G1 a: notes only for a document too long for her to read in full."""
    if not row or row.get("kind") != "document" or row.get("notes") == store.NOTES_DONE:
        return False
    conv = conversation_id if conversation_id is not None else row.get("conversation_id")
    if row.get("read_tokens") is not None:
        can = int(row["read_tokens"])
    else:
        # This document alone next to her desk (other cards in the tray are
        # not counted: they may never be sent).
        can = min(DOC_MAX_TOKENS, max(0, room_tokens(conv, waiting=False, skip=(row["id"],))))
    if int(row.get("tokens") or 0) <= can:
        return False
    start_notes(row)
    return True


def resume_notes() -> int:
    """Startup: notes that were being written when the app closed go on."""
    rows = store.attachments_where("kind IN ('document', 'video') AND notes = ?",
                                   (store.NOTES_WRITING,))
    for row in rows:
        start_notes(row)
    return len(rows)


# --- Which pages does this message need? -------------------------------------
_PAGE_REF_RES = [
    re.compile(r"\b(?:pages?|pp?\.)\s*(\d{1,4})(?:\s*(?:-|–|~|to|and)\s*(\d{1,4}))?", re.IGNORECASE),
    re.compile(r"(\d{1,4})\s*(?:[-–~〜]\s*(\d{1,4})\s*)?(?:ページ|頁)"),
]
# A message that points back to the one before ("the weather that day?").
_POINTS_BACK_RE = re.compile(r"\b(?:that|those|it|its|then|same|there)\b|その|それ|あの|そこ|同じ",
                             re.IGNORECASE)
_CJK_RUN = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]+")


def page_refs(text: str, total: int) -> list:
    """Page numbers the message names ("page 54", "pages 50-52", "54ページ")."""
    out = []
    for rx in _PAGE_REF_RES:
        for m in rx.finditer(text or ""):
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else a
            for n in range(min(a, b), min(max(a, b), min(a, b) + 20) + 1):
                if 1 <= n <= total and n not in out:
                    out.append(n)
    return out


def _terms(text: str) -> list:
    """Search words: Latin words / numbers, and Japanese as 2-character pieces."""
    low = (text or "").lower()
    out = [w for w in re.findall(r"[a-z0-9]+", low) if (len(w) > 2 or w.isdigit()) and w not in _STOP]
    for run in _CJK_RUN.findall(text or ""):
        out += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
    return out


def search_pages(row, question: str, context: str = "", limit: int = FOUND_PAGES_MAX) -> list:
    """The page numbers (best first) whose text - plus its section note -
    matches the question best (BM25). context = the user's previous message,
    at half weight ("and the weather that day?" after "day 110?"). A word on
    most pages ("day" in a diary) says nothing about which page is meant and
    is left out (live check 2026-10-29: small talk pulled pages)."""
    pages = pages_of(row)
    if not pages:
        return []
    notes = {n: s["note"] for s in load_notes(row).get("sections") or []
             for n in range(s["from"], s["to"] + 1)}
    docs = [_terms(p + " " + notes.get(i, "")) for i, p in enumerate(pages, start=1)]
    scores = best_matches(docs, question, context, _DOC_STOP)
    if not scores:
        return []
    best = scores[0][0]
    return [i + 1 for s, i in scores[:limit] if s >= best * 0.35]


def best_matches(docs, question: str, context: str = "", stop=frozenset()) -> list:
    """[(score, index)] best first, of the docs (lists of search words) that
    match the question (BM25; the context at half weight; a word in most docs
    is left out when there are 4 or more)."""
    import math
    weight = {t: 0.5 for t in set(_terms(context)) - stop}
    weight.update({t: 1.0 for t in set(_terms(question)) - stop})
    if not docs or not weight:
        return []
    avg = sum(len(d) for d in docs) / len(docs) or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in weight}
    if len(docs) >= 4:
        weight = {t: w for t, w in weight.items() if df[t] <= len(docs) / 2}
    scores = []
    for i, d in enumerate(docs):
        score = 0.0
        for t, w in weight.items():
            tf = d.count(t)
            if tf:
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                score += w * idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(d) / avg))
        if score > 0:
            scores.append((score, i))
    scores.sort(reverse=True)
    return scores


def _notes_lines(entries) -> str:
    return "\n".join(f"- pages {e['from']}-{e['to']}: {e['note']}" for e in entries)


def notes_view(row) -> tuple:
    """(the notes her stable document message shows, "sections" / "parts").
    Section notes when they fit in NOTES_SHARE of the document's room, else
    the part notes (the matching parts' section notes come with the found
    pages)."""
    data = load_notes(row)
    sections = data.get("sections") or []
    budget = int(int(row.get("read_tokens") or DOC_MAX_TOKENS) * NOTES_SHARE)
    text = _notes_lines(sections)
    if store.estimate_tokens(text) <= budget or not data.get("parts"):
        return text, "sections"
    return _notes_lines(data["parts"]), "parts"


def found_pages(row, question: str, note: str = "", context: str = "") -> tuple:
    """(the block of pages this message needs - "" when none, the reads line).
    Room: what the document's room has left after its notes."""
    pages = pages_of(row)
    notes, level = notes_view(row)
    room = int(row.get("read_tokens") or DOC_MAX_TOKENS) - store.estimate_tokens(notes)
    wanted = page_refs(question, len(pages)) or search_pages(row, question, context)
    number = row.get("chat_no") or row["id"]
    head = (f'[Document {int(number)} from the user{note}: "{row.get("name") or "document"}" - '
            "pages found for this message]")
    bits, shown, used = [], [], 0
    if level == "parts" and wanted:
        sections = load_notes(row).get("sections") or []
        near = [s for s in sections if any(s["from"] - SECTION_PAGES * 4 <= n <= s["to"] + SECTION_PAGES * 4
                                           for n in wanted)]
        detail = _notes_lines(near)
        if detail and store.estimate_tokens(detail) < room // 3:
            bits.append("Your detailed notes around them:\n" + detail)
            used += store.estimate_tokens(detail)
    for n in sorted(wanted):
        page = f"--- page {n} ---\n" + pages[n - 1]
        cost = store.estimate_tokens(page)
        if used + cost > room:
            break
        bits.append(page)
        shown.append(n)
        used += cost
    if not shown and not bits:
        return "", f"Document {int(number)}: notes"
    pages_txt = _page_list(shown)
    return (head + "\n" + "\n\n".join(bits) + f"\n[End of document {int(number)}]",
            f"Document {int(number)}: notes" + (f" + {pages_txt}" if shown else ""))


def _page_list(numbers) -> str:
    """[54, 55, 60] -> "pages 54-55, 60"."""
    out = []
    for n in sorted(numbers):
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    words = [f"{a}-{b}" if a != b else str(a) for a, b in out]
    return ("page " if len(numbers) == 1 else "pages ") + ", ".join(words)


# --- What she read of long documents this turn (shown under her reply) ------
_turn = threading.local()


def take_turn_reads() -> str:
    """The "Read: ..." line of the reply just built, once ("" = none)."""
    text = getattr(_turn, "reads", "") or ""
    _turn.reads = ""
    return text


# ---------- Can her model see pictures? ----------

_CAPS = {}            # (server, model) -> (checked_at, True / False / None)
_CAPS_TTL = 600.0
_CAPS_LOCK = threading.Lock()


def _brain():
    """(server url, model) her replies use right now."""
    import llm as llm_module   # late imports: both import this module's neighbours
    import chat
    return llm_module._configured_server_url().rstrip("/"), str(chat.getLLMModel() or "")


def _probe(url: str, model: str):
    """True / False when the server says so, None when it can't tell."""
    root = re.sub(r"/v1$", "", url)
    if "openrouter.ai" in url:
        try:
            data = requests.get("https://openrouter.ai/api/v1/models", timeout=8).json()
            for item in data.get("data") or []:
                if item.get("id") == model:
                    mods = (item.get("architecture") or {}).get("input_modalities") or []
                    return "image" in mods
        except Exception:
            return None
        return None
    try:  # llama-server (her built-in model, or the user's own)
        r = requests.get(root + "/props", timeout=3)
        if r.ok:
            mods = (r.json() or {}).get("modalities")
            if isinstance(mods, dict) and "vision" in mods:
                return bool(mods["vision"])
    except Exception:
        pass
    return None


def can_see_pictures():
    """True / False / None (unknown: try, and an image error says no)."""
    try:
        key = _brain()
    except Exception:
        return None
    with _CAPS_LOCK:
        hit = _CAPS.get(key)
        if hit and time.monotonic() - hit[0] < _CAPS_TTL:
            return hit[1]
    answer = _probe(*key)
    with _CAPS_LOCK:
        _CAPS[key] = (time.monotonic(), answer)
    return answer


def mark_cannot_see() -> None:
    """Her server refused a picture: remember it (this session)."""
    try:
        key = _brain()
    except Exception:
        return
    with _CAPS_LOCK:
        _CAPS[key] = (time.monotonic(), False)


def reset_capabilities() -> None:
    with _CAPS_LOCK:
        _CAPS.clear()
        _WATCH_CAPS.clear()


# Videos (video plan Phase 3, V3a): a cloud model that takes video (Gemini)
# gets the real video; every other model the stills. Her built-in model and
# other local servers always get the stills (V1a: Phase 0 - llama.cpp's own
# video input cost ~260 pieces per second and had no speech).
_WATCH_CAPS = {}      # (server, model) -> (checked_at, True / False)


def _probe_video(url: str, model: str) -> bool:
    if "openrouter.ai" not in url:
        return False
    try:
        data = requests.get("https://openrouter.ai/api/v1/models", timeout=8).json()
        for item in data.get("data") or []:
            if item.get("id") == model:
                return "video" in ((item.get("architecture") or {}).get("input_modalities") or [])
    except Exception:
        pass
    return False


def can_watch_videos() -> bool:
    try:
        key = _brain()
    except Exception:
        return False
    with _CAPS_LOCK:
        hit = _WATCH_CAPS.get(key)
        if hit and time.monotonic() - hit[0] < _CAPS_TTL:
            return hit[1]
    answer = _probe_video(*key)
    with _CAPS_LOCK:
        _WATCH_CAPS[key] = (time.monotonic(), answer)
    return answer


def mark_cannot_watch() -> None:
    """Her server refused a video: the stills from now on (this session)."""
    try:
        key = _brain()
    except Exception:
        return
    with _CAPS_LOCK:
        _WATCH_CAPS[key] = (time.monotonic(), False)


def is_video_rejection(exc) -> bool:
    """True when a server error refuses a video (or says the call is too big).
    OpenRouter also refuses videos with a 402 while the balance is under $1."""
    text = str(exc or "").lower()
    return ("413" in text or "too large" in text
            or ("video" in text and any(w in text for w in _REJECT_WORDS + _CREDIT_WORDS)))


def took_video() -> bool:
    """True when the reply call just built carries a real video."""
    return bool(getattr(_turn, "video", False))


_IMAGE_WORDS = ("image", "vision", "multimodal", "image_url", "mmproj", "picture")
_REJECT_WORDS = ("not support", "unsupported", "does not support", "cannot", "can't",
                 "invalid", "not enabled", "no support", "400", "422")
_CREDIT_WORDS = ("402", "balance", "credits")


def is_image_rejection(exc) -> bool:
    """True when a server error says it can't take pictures."""
    text = str(exc or "").lower()
    return any(w in text for w in _IMAGE_WORDS) and any(w in text for w in _REJECT_WORDS)


def _cloud_brain() -> bool:
    """True when her replies come from a cloud service (pictures leave this PC)."""
    try:
        import llm as llm_module
        return not llm_module._is_local_host(_brain()[0])
    except Exception:
        return False


def capabilities(conversation_id=None) -> dict:
    see = can_see_pictures()
    reason = {True: "Her current model can see pictures.",
              False: "Her current model can't see pictures. Pick one that can in "
                     "Settings -> Connection.",
              None: "Not sure her current model can see pictures - she will say so if not."}[see]
    room = room_tokens(conversation_id)
    return {"pictures": see, "reason": reason, "max_mb": MAX_FILE_BYTES // (1024 * 1024),
            "formats": FORMAT_NAMES, "documents": True, "doc_formats": DOC_FORMAT_NAMES,
            "doc_pages": (store.document_pages(min(room, DOC_MAX_TOKENS))
                          if room >= DOC_MIN_TOKENS else 0),
            "cloud": _cloud_brain(), "room": max(0, room // store.PICTURE_TOKENS)}


# ---------- Her prompt: pictures only in her reply calls ----------
# Every picture text in her prompt starts with "[Picture" (labels, notes, the
# rule), so chat._content_text can hand helpers the user's own words.

PICTURE_RULE = (
    "[Picture rule: you can really see the picture(s) above. React as yourself, "
    "in character. Describe only what is really there; if something is too "
    "small, blurry or unclear, say so instead of guessing. Pictures marked 'on "
    "your desk' are ones the user showed earlier and left with you - bring them "
    "up only when they matter. Vary how you begin: do not open with ふむ or the "
    "same word you used before.]")

# A turn whose message brings no picture of its own: the desk pictures are
# there for reference only (the live check: with the full rule she talked
# about them when asked how her day was).
DESK_RULE = (
    "[Picture rule: the pictures on your desk are there in case the user asks about "
    "them. Talk about them only when this message is about them; otherwise do not "
    "bring them up. Vary how you begin: do not open with ふむ or the same word you "
    "used before.]")

CANT_SEE_NOTE = (
    "[Picture note: the user attached {n} picture(s), but your current model cannot "
    "see pictures. Say so honestly and briefly (they can pick a model that sees "
    "pictures in Settings -> Connection). Never guess what is in them.]")

NO_PICTURE_NOTE = (
    "[Picture note: no picture is attached to this message. If the user talks as if "
    "they sent or showed you one, say plainly that you can't see it - it did not "
    "arrive. Never describe a picture you have not seen.]")

NOT_SENT_NOTE = (
    "[Picture note: the user attached a picture but it was NOT sent with this "
    "message - you have not seen it. If they talk about it, say you can't see it "
    "yet. Never describe it.]")

# Documents (Phase 4). E1 B: up to ~6 sentences when the message is about one.
DOCUMENT_RULE = (
    "[Document rule: you can read the document(s) above - the text between a "
    "document's label and its End of document mark. Answer from what is really written "
    "there; quote briefly, never invent content. If a document was cut short (its label "
    "says 'first N of M pages') and the user asks about a part you were not given, say "
    "you only read the first pages. When the message is about a document you may answer "
    "in up to about 6 sentences.]")

LONG_DOC_RULE = (
    "[Document rule: for a long document you have your reading notes on the whole document, "
    "and below them the pages found for this message (with their page numbers). Answer "
    "details from those pages; for an overview or a summary use your notes. If neither "
    "covers what is asked, say so plainly - the user can name a page (\"page 54\") for you "
    "to look at. Quote only words that are really on the pages shown; never invent or "
    "guess what a page says.]")

DOC_DESK_RULE = (
    "[Document rule: the documents on your desk are there in case the user asks about "
    "them. Talk about them only when this message is about them (then you may answer in "
    "up to about 6 sentences); otherwise do not bring them up.]")

# Videos (video plan Phase 3). Phase 0: she read a camera's clock overlay as
# the video's times and invented a boat "passing under the bridge" between
# two stills - the rule says both plainly.
VIDEO_RULE = (
    "[Video rule: you watched the video(s) above as stills, each marked with its time from "
    "the start of the video (1:05 = 1 minute 5 seconds in), and read what is said in it. "
    "Times are those marks - never a clock or date shown inside the picture. You did not hear "
    "music, sounds or anyone's tone, and the written speech can mishear words and names and "
    "does not say who speaks. Describe only what the stills and the speech really show; never "
    "invent a movement or an event between two stills - if a moment could have fallen between "
    "them, or what they ask about is not in your stills, say you may have missed it (you only "
    "saw stills) instead of saying it is not there. React as yourself, in character; give details only when "
    "asked. Vary how you begin: do not open with ふむ or the same word you used before.]")

VIDEO_WATCH_RULE = (
    "[Video rule: you really watched the video(s) above, with their sound. Times are counted "
    "from the start of the video (1:05 = 1 minute 5 seconds in) - never a clock or date shown "
    "inside the picture. Describe only what is really in it; if something is unclear, say so "
    "instead of guessing. React as yourself, in character; give details only when asked. Vary "
    "how you begin: do not open with ふむ or the same word you used before.]")

VIDEO_TEXT_RULE = (
    "[Video rule: your current model can't see pictures, so you could not see the video(s) - "
    "you only have what is said in them, written down by speech recognition (it can mishear). "
    "Say so honestly and briefly (they can pick a model that sees pictures in Settings -> "
    "Connection). Never guess what is shown.]")

VIDEO_DESK_RULE = (
    "[Video rule: the videos on your desk are there in case the user asks about them. Talk "
    "about them only when this message is about them; otherwise do not bring them up. You saw "
    "only stills of them (times from the start of the video) and read what is said - never "
    "invent what happened between two stills, and if what they ask about is not in your stills, "
    "say you may have missed it (you only saw stills) instead of saying it is not there. Vary "
    "how you begin: do not open with ふむ or the same word you used before.]")

CLOSE_LOOK_RULE = (
    "[Video rule: for this message you also got a closer look at the part of the video it asks "
    "about - more stills (or a short clip) from that stretch and what is said then; times are "
    "from the start of the video. Answer from it. If what they ask about is not in it, say you "
    "may have missed it instead of saying it is not there.]")

_DATA_URLS = {}       # id -> (mtime, data url); a few, so desk pictures are not re-read
_DATA_URLS_MAX = 24
SHOW_AGAIN_MAX = 2    # earlier pictures re-shown in one reply


def _data_url(row):
    full = file_path(row["id"])
    if not full:
        return None
    mtime = os.path.getmtime(full)
    hit = _DATA_URLS.get(row["id"])
    if hit and hit[0] == mtime:
        return hit[1]
    with open(full, "rb") as f:
        url = "data:" + (row.get("mime") or "image/jpeg") + ";base64," + base64.b64encode(f.read()).decode()
    if len(_DATA_URLS) >= _DATA_URLS_MAX:
        _DATA_URLS.pop(next(iter(_DATA_URLS)))
    _DATA_URLS[row["id"]] = (mtime, url)
    return url


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or []
                    if isinstance(p, dict) and p.get("type") == "text")


# --- Which earlier picture does the user mean? -----------------------------
# Only CLEAR references bring a picture back (a loose guess could show her the
# wrong one; her picture list lets her ask instead). Matched on: its number in
# this chat ("picture 3", "写真3", "3枚目"), first / last ("the first photo",
# "さっきの写真"), the day it was sent ("yesterday's photo", "昨日の写真") or
# words of its caption right next to a picture word ("the sunset picture").
_PIC_WORD = (r"(?:pictures?|pics?|photos?|photographs?|images?|screenshots?|screen ?shots?|"
             r"snapshots?|selfies?|drawings?|写真|画像|スクショ|スクリーンショット|イラスト|自撮り)")
_PIC_WORD_RE = re.compile(_PIC_WORD, re.IGNORECASE)
_KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUMBER_RES = [
    re.compile(_PIC_WORD + r"\s*(?:no\.?|number|#)?\s*(\d{1,3})(?!\d)", re.IGNORECASE),
    re.compile(r"(?:#|no\.?\s*)(\d{1,3})\s*" + _PIC_WORD, re.IGNORECASE),
    re.compile(r"(\d{1,3}|[一二三四五六七八九十])\s*枚目"),
]
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}
_ORDINAL_RE = re.compile(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
                         r"(?:\w+\s+)?" + _PIC_WORD, re.IGNORECASE)
_LAST_RE = re.compile(r"\b(?:last|previous|latest|earlier|other)\s+(?:\w+\s+)?" + _PIC_WORD
                      + r"|(?:さっき|前|最後|この前|先ほど)の" + _PIC_WORD, re.IGNORECASE)
_FIRST_JA_RE = re.compile(r"(?:最初|一番目|一枚目)の?" + _PIC_WORD)
_DAY_RES = {
    "yesterday": re.compile(r"\byesterday'?s?\b|昨日", re.IGNORECASE),
    "today": re.compile(r"\btoday'?s?\b|\bthis morning\b|今日|今朝", re.IGNORECASE),
}
_SHOWING_RE = re.compile(r"\b(?:this|these|here(?:'s| is| are)|look at|check (?:this|it) out)\b"
                         r"|この|これ|見て|ほら", re.IGNORECASE)
_STOP = {"the", "a", "an", "of", "on", "in", "at", "with", "and", "to", "is", "it", "that",
         "this", "my", "your", "you", "me", "what", "was", "were", "one", "from", "for",
         "picture", "pictures", "photo", "photos", "image", "screenshot", "pic", "about", "did",
         "do", "does", "can", "show", "showed", "sent", "again", "look", "there", "its", "how"}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}


PICTURE_AGAIN_NOTE = (" (sent earlier; put back here because this message seems to be about it"
                      " - the user did not send it again)")
_PIC_TOKENS = {"picture", "pictures", "pic", "pics", "photo", "photos", "photograph",
               "photographs", "image", "images", "screenshot", "screenshots", "snapshot",
               "snapshots", "selfie", "selfies", "drawing", "drawings"}
_NEAR = 4   # words either side of "photo" that can name what is in it


def _words_near_pictures(text: str) -> set:
    """The words close to a picture word, in the same sentence ("the photo
    of the noodles", "that cake picture"). Only these can match a caption:
    "it sent an image to process ... the glasses" named no photo of glasses
    (2026-11-06: it brought back an old photo of drinking glasses and cake)."""
    near = set()
    for sentence in re.split(r"[.!?\n。！？]+", (text or "").lower()):
        tokens = re.findall(r"[a-z]+", sentence)
        for i, w in enumerate(tokens):
            if w in _PIC_TOKENS:
                near |= {t for t in tokens[max(0, i - _NEAR):i + _NEAR + 1]
                         if len(t) >= 3 and t not in _STOP and t not in _PIC_TOKENS}
    return near


def _sent_pictures(conv: int) -> list:
    return store.attachments_where("conversation_id = ? AND message_id IS NOT NULL AND "
                                   "kind = 'picture'", (int(conv),))


def _newest_user(conv: int):
    """(id, text) of the chat's newest user message."""
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_messages_table(c)
        row = c.execute("SELECT id, content FROM messages WHERE conversation_id = ? AND "
                        "role = 'user' ORDER BY id DESC LIMIT 1", (int(conv),)).fetchone()
    finally:
        conn.close()
    return (row[0], row[1] or "") if row else (0, "")


def referenced(text: str, conv: int, newest_id: int, now=None) -> list:
    """Earlier pictures of this chat the message clearly asks about (at most
    SHOW_AGAIN_MAX, oldest first); [] when none or when it is not clear."""
    text = text or ""
    earlier = [r for r in _sent_pictures(conv) if r["message_id"] != newest_id]
    if not earlier or not text.strip():
        return []
    by_no = {r.get("chat_no"): r for r in earlier if r.get("chat_no")}
    picked = []
    for rx in _NUMBER_RES:
        for m in rx.finditer(text):
            raw = m.group(1)
            n = _KANJI_NUM.get(raw) or (int(raw) if raw.isdigit() else 0)
            if n in by_no and by_no[n] not in picked:
                picked.append(by_no[n])
    for m in _ORDINAL_RE.finditer(text):
        n = _ORDINALS[m.group(1).lower()]
        if n in by_no and by_no[n] not in picked:
            picked.append(by_no[n])
    if _FIRST_JA_RE.search(text) and 1 in by_no and by_no[1] not in picked:
        picked.append(by_no[1])
    if not picked and _LAST_RE.search(text):
        picked.append(earlier[-1])
    if not picked and _PIC_WORD_RE.search(text):
        now = now or datetime.now()
        for day, rx in _DAY_RES.items():
            if not rx.search(text):
                continue
            want = (now - timedelta(days=1 if day == "yesterday" else 0)).strftime("%Y-%m-%d")
            hits = [r for r in earlier if str(r.get("created_at", "")).startswith(want)]
            if 0 < len(hits) <= SHOW_AGAIN_MAX:
                picked += hits
        if not picked:
            asked = _words_near_pictures(text)
            scored = sorted(((len(asked & _words(r.get("caption"))), r) for r in earlier),
                            key=lambda x: -x[0])
            if scored and scored[0][0] >= 1 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
                picked.append(scored[0][1])
    return sorted(picked[:SHOW_AGAIN_MAX], key=lambda r: r["id"])


def _picture_plan(conv: int, newest_text=None):
    """(own, desk, again, newest_id): the newest message's pictures, the desk
    ones, and earlier ones it asks about (not already in the first two)."""
    base = store.prompt_attachments(conv)
    newest_id, db_text = _newest_user(conv)
    own = [a for a in base if a["message_id"] == newest_id]
    desk = [a for a in base if a["message_id"] != newest_id]
    seen = {a["id"] for a in base}
    again = [r for r in referenced(db_text if newest_text is None else newest_text, conv, newest_id)
             if r["id"] not in seen]
    return own, desk, again, newest_id


def prompt_pictures(conversation_id=None) -> list:
    """Every picture her next reply sees for real (for the room they take)."""
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    own, desk, again, _ = _picture_plan(conv)
    return desk + own + again


# --- Documents in her prompt ------------------------------------------------
# A document asked about again: its number ("document 2", "資料2"), first /
# last ("the last file", "さっきの資料") or its file name ("the budget
# report", "budget.pdf"). At most one, and only when it fits.
_DOC_WORD = (r"(?:documents?|docs?|files?|pdfs?|reports?|papers?|essays?|articles?|"
             r"資料|文書|書類|ドキュメント|ファイル|論文|レポート)")
_DOC_WORD_RE = re.compile(_DOC_WORD, re.IGNORECASE)
_DOC_NUMBER_RES = [
    re.compile(_DOC_WORD + r"\s*(?:no\.?|number|#)?\s*(\d{1,3})(?!\d)", re.IGNORECASE),
    re.compile(r"(?:#|no\.?\s*)(\d{1,3})\s*" + _DOC_WORD, re.IGNORECASE),
]
_DOC_ORDINAL_RE = re.compile(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
                             r"(?:\w+\s+)?" + _DOC_WORD, re.IGNORECASE)
_DOC_LAST_RE = re.compile(r"\b(?:last|previous|latest|earlier|other)\s+(?:\w+\s+)?" + _DOC_WORD
                          + r"|(?:さっき|前|最後|この前|先ほど)の" + _DOC_WORD, re.IGNORECASE)
_DOC_STOP = _STOP | {"document", "documents", "doc", "docs", "file", "files", "pdf", "pdfs",
                     "report", "paper", "text", "docx", "txt", "final", "copy", "draft"}


def _sent_documents(conv: int) -> list:
    return store.attachments_where("conversation_id = ? AND message_id IS NOT NULL AND "
                                   "kind = 'document'", (int(conv),))


def referenced_documents(text: str, conv: int, newest_id: int) -> list:
    """The one earlier document of this chat the message clearly asks about
    ([] when none or not clear)."""
    text = text or ""
    earlier = [r for r in _sent_documents(conv) if r["message_id"] != newest_id]
    if not earlier or not text.strip():
        return []
    by_no = {r.get("chat_no"): r for r in earlier if r.get("chat_no")}
    for rx in _DOC_NUMBER_RES:
        for m in rx.finditer(text):
            if int(m.group(1)) in by_no:
                return [by_no[int(m.group(1))]]
    for m in _DOC_ORDINAL_RE.finditer(text):
        if _ORDINALS[m.group(1).lower()] in by_no:
            return [by_no[_ORDINALS[m.group(1).lower()]]]
    if _DOC_LAST_RE.search(text):
        return [earlier[-1]]
    lowered = text.lower()
    named = [r for r in earlier if (r.get("name") or "").lower() in lowered]
    if len(named) == 1:
        return named
    if _DOC_WORD_RE.search(text):
        asked = {w for w in _words(text) if w not in _DOC_STOP}
        scored = sorted(((len(asked & (_words(re.sub(r"[_\-.]", " ", r.get("name") or ""))
                                       - _DOC_STOP)), r) for r in earlier), key=lambda x: -x[0])
        if scored and scored[0][0] >= 1 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return [scored[0][1]]
    return []


def _document_plan(conv: int, newest_id: int, newest_text: str):
    """(own, desk, again): the newest message's documents, the ones on her
    desk, and an earlier one the message asks about (when it fits)."""
    base = store.attachments_where(
        "conversation_id = ? AND message_id IS NOT NULL AND kind = 'document' "
        "AND (on_desk = 1 OR message_id = ?)", (int(conv), int(newest_id)))
    own = [a for a in base if a["message_id"] == newest_id]
    desk = [a for a in base if a["message_id"] != newest_id]
    seen = {a["id"] for a in base}
    again = [r for r in referenced_documents(newest_text, conv, newest_id) if r["id"] not in seen]
    if again:
        budget = store.effective_context_budget()
        videos = store.attachments_where(
            "conversation_id = ? AND message_id IS NOT NULL AND kind = 'video' "
            "AND (on_desk = 1 OR message_id = ?)", (int(conv), int(newest_id)))
        used = (sum(_cost(r) for r in base + videos)
                + store.PICTURE_TOKENS * len(prompt_pictures(conv)))
        if _cost(again[0]) > budget - _text_floor(budget) - used:
            again = []
    return own, desk, again


def prompt_documents(conversation_id=None) -> list:
    """Every document her next reply reads (for the room they take)."""
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    newest_id, text = _newest_user(conv)
    own, desk, again = _document_plan(conv, newest_id, text)
    return desk + own + again


def document_block(row, note: str = "") -> str:
    """A document as her reply calls read it: its label (no summary - the
    same on every turn), the part she reads, and an end mark."""
    label = store.document_label({**row, "caption": None}, note)
    end = f"\n[End of document {row.get('chat_no') or row['id']}]"
    if row.get("notes") == store.NOTES_DONE:
        # A long document (Phase 4b): her notes on all of it; the pages a
        # message needs come in the changing part (found_pages).
        notes, _level = notes_view(row)
        return label + "\nYOUR READING NOTES ON THE WHOLE DOCUMENT:\n" + notes + end
    part = read_part(row)
    if not part:
        return label
    if row.get("notes") == store.NOTES_WRITING:
        progress = notes_progress(row)
        part += (f"\n(You have read this start; you are still reading the rest and making notes - "
                 f"{progress.get('done', 0)} of {progress.get('total', 0)} sections so far.)")
    return label + "\n" + part + end


def _place_documents(out, users, own, desk, again) -> dict:
    """Where the documents' text goes in her prompt: {message index: [blocks]}
    - each block goes into its OWN user message right BEFORE that message
    (_insert_documents, after the picture steps). Her built-in server can
    only resume a prompt from where the previous call's newest user message
    began: text inside that message is read again on the next turn (live
    check 2026-10-28: a 5,000-piece document re-read once). As its own
    message before the words, the next turn reuses it.
    A desk document goes before the message it came with (whose words keep
    its label); one whose message left the window before the window's first
    user message; the newest message's own ones (the label is added to its
    words now, as the history will show them next turn) and one asked about
    again before the newest message."""
    last = users[-1]
    plan = {}
    for a in desk:
        label = store.document_label({**a, "caption": None})
        home = next((i for i in users[:-1] if label in _text(out[i].get("content"))), None)
        if home is not None:
            plan.setdefault(home, []).append(document_block(a))
        else:
            plan.setdefault(users[0], []).append(document_block(a, " (on your desk, shown earlier)"))
    if own:
        labels = [store.document_label({**a, "caption": None}) for a in own]
        out[last]["content"] = " ".join(
            t for t in [_text(out[last].get("content")).strip()] + labels if t)
        plan.setdefault(last, []).extend(document_block(a) for a in own)
    if again:
        plan.setdefault(last, []).extend(
            document_block(a, " (shown to you again now)") for a in again)
    return plan


def _insert_documents(out, plan) -> list:
    """The prompt with each planned document / video message put in place
    (a video brings stills or the video itself: that message is then parts)."""
    for i in sorted(plan, reverse=True):
        blocks = plan[i]
        if all(isinstance(b, str) for b in blocks):
            content = "\n\n".join(blocks)
        else:
            content = []
            for b in blocks:
                content += b if isinstance(b, list) else [{"type": "text", "text": b}]
        out.insert(i, {"role": "user", "content": content})
    return out


# --- Videos in her prompt (video plan Phase 3) --------------------------------
# A video asked about again: its number ("video 2", "動画2"), first / last
# ("the last clip", "さっきの動画") or its file name. At most one, and only
# when it fits. P1a: while it is on her desk she keeps it (prompt reuse makes
# that nearly free); her watch notes and closer looks at moments: Phase 4
# (video_moments, video.close_block).
_VID_WORD = (r"(?:videos?|clips?|movies?|recordings?|footage|vids?|"
             r"動画|ビデオ|映像|ムービー|録画)")
_VID_WORD_RE = re.compile(_VID_WORD, re.IGNORECASE)
_VID_NUMBER_RES = [
    re.compile(_VID_WORD + r"\s*(?:no\.?|number|#)?\s*(\d{1,3})(?!\d)", re.IGNORECASE),
    re.compile(r"(?:#|no\.?\s*)(\d{1,3})\s*" + _VID_WORD, re.IGNORECASE),
]
_VID_ORDINAL_RE = re.compile(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
                             r"(?:\w+\s+)?" + _VID_WORD, re.IGNORECASE)
_VID_LAST_RE = re.compile(r"\b(?:last|previous|latest|earlier|other)\s+(?:\w+\s+)?" + _VID_WORD
                          + r"|(?:さっき|前|最後|この前|先ほど)の" + _VID_WORD, re.IGNORECASE)
_VID_STOP = _STOP | {"video", "videos", "clip", "clips", "movie", "mp4", "mov", "vid"}


def _sent_videos(conv: int) -> list:
    return store.attachments_where("conversation_id = ? AND message_id IS NOT NULL AND "
                                   "kind = 'video'", (int(conv),))


def referenced_videos(text: str, conv: int, newest_id: int) -> list:
    """The one earlier video of this chat the message clearly asks about
    ([] when none or not clear)."""
    text = text or ""
    earlier = [r for r in _sent_videos(conv) if r["message_id"] != newest_id]
    if not earlier or not text.strip():
        return []
    by_no = {r.get("chat_no"): r for r in earlier if r.get("chat_no")}
    for rx in _VID_NUMBER_RES:
        for m in rx.finditer(text):
            if int(m.group(1)) in by_no:
                return [by_no[int(m.group(1))]]
    for m in _VID_ORDINAL_RE.finditer(text):
        if _ORDINALS[m.group(1).lower()] in by_no:
            return [by_no[_ORDINALS[m.group(1).lower()]]]
    if _VID_LAST_RE.search(text):
        return [earlier[-1]]
    lowered = text.lower()
    named = [r for r in earlier if (r.get("name") or "").lower() in lowered]
    if len(named) == 1:
        return named
    if _VID_WORD_RE.search(text):
        asked = {w for w in _words(text) if w not in _VID_STOP}
        scored = sorted(((len(asked & ((_words(re.sub(r"[_\-.]", " ", r.get("name") or ""))
                                        | _words(r.get("caption"))) - _VID_STOP)), r)
                         for r in earlier), key=lambda x: -x[0])
        if scored and scored[0][0] >= 1 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return [scored[0][1]]
    return []


def _video_plan(conv: int, newest_id: int, newest_text: str):
    """(own, desk, again): the newest message's videos, the ones on her desk,
    and an earlier one the message asks about (when it fits)."""
    base = store.attachments_where(
        "conversation_id = ? AND message_id IS NOT NULL AND kind = 'video' "
        "AND (on_desk = 1 OR message_id = ?)", (int(conv), int(newest_id)))
    own = [a for a in base if a["message_id"] == newest_id]
    desk = [a for a in base if a["message_id"] != newest_id]
    seen = {a["id"] for a in base}
    again = [r for r in referenced_videos(newest_text, conv, newest_id) if r["id"] not in seen]
    if again:
        budget = store.effective_context_budget()
        d_own, d_desk, _again = _document_plan(conv, newest_id, newest_text)
        used = (sum(_cost(r) for r in base + d_own + d_desk)
                + store.PICTURE_TOKENS * len(prompt_pictures(conv)))
        if _cost(again[0]) > budget - _text_floor(budget) - used:
            again = []
    return own, desk, again


def prompt_videos(conversation_id=None) -> list:
    """Every video her next reply gets (for the room they take)."""
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    newest_id, text = _newest_user(conv)
    own, desk, again = _video_plan(conv, newest_id, text)
    return desk + own + again


def _place_videos(out, users, plan, own, desk, again, see) -> list:
    """Like _place_documents (same places, same reasons), into the same plan.
    Returns [(row, mode)] of the videos this message brings or asks about
    (for the rule and the "Watched:" line)."""
    import video   # late import: video imports this module
    watch = bool(own or again) and see is not False and can_watch_videos()
    last = users[-1]
    for a in desk:
        label = store.video_label({**a, "caption": None})
        mode = video.mode_for(a, False, see, False)
        home = next((i for i in users[:-1] if label in _text(out[i].get("content"))), None)
        if home is not None:
            plan.setdefault(home, []).append(video.video_block(a, mode))
        else:
            plan.setdefault(users[0], []).append(
                video.video_block(a, mode, " (on your desk, shown earlier)"))
    fresh = []
    if own:
        labels = [store.video_label({**a, "caption": None}) for a in own]
        out[last]["content"] = " ".join(
            t for t in [_text(out[last].get("content")).strip()] + labels if t)
        for a in own:
            mode = video.mode_for(a, True, see, watch)
            plan.setdefault(last, []).append(video.video_block(a, mode))
            fresh.append((a, mode))
    for a in again:
        mode = video.mode_for(a, True, see, watch)
        plan.setdefault(last, []).append(video.video_block(a, mode, " (shown to you again now)"))
        fresh.append((a, mode))
    return fresh


# Phase 4 (MH 2a, 3a): the moment this message asks about gets a closer look.
# A search by words only when the message asks for a moment or what is said
# or shown ("when does the dog...", "what does he say about Noah") - not "what
# is it about?" (live check 2026-11-03: that pulled a random stretch).
_MOMENT_CUE_RE = re.compile(
    r"\b(?:when|where|which part|what part|at what point|what time|says?|said|talks?|talked|"
    r"talking|mentions?|mentioned|shows?|showed|shown|appears?|appeared|happens?|happened)\b|"
    r"いつ|どこ|どの辺|どのあたり|どの部分|何分|何秒|言って|言った|話して|話した|出て|映って|"
    r"起き|登場", re.IGNORECASE)


def video_moments(text: str, context: str, conv: int, newest_id: int, own, desk, again) -> list:
    """[(row, seconds, how)] - the moments of her videos this message asks a
    closer look at: the times it names (how = "time"), else - when it asks
    something - the stretch whose notes + speech match its words ("words")."""
    import video   # late import: video imports this module
    pool = list(again) + list(own) + sorted(desk, key=lambda r: -int(r["message_id"] or 0))
    pool = [r for r in pool if video.needs_close_looks(r)]
    if not pool or not (text or "").strip():
        return []
    named = referenced_videos(text, conv, newest_id)
    named = [r for r in pool if named and r["id"] == named[0]["id"]]
    target = (named or pool)[0]
    fresh = {r["id"] for r in list(own) + list(again)}
    speaks_of = bool(_VID_WORD_RE.search(text)) or target["id"] in fresh
    times = video.moment_times(text, video.load_meta(target).get("duration"), ends=speaks_of)
    if times:
        out = []
        for t in times:
            if all(abs(t - u) >= video.CLOSE_HALF for _r, u, _h in out):
                out.append((target, t, "time"))
        return out[:video.CLOSE_MAX]
    if not _MOMENT_CUE_RE.search(text):
        return []
    hit = video.search_moment(named or pool, text, context)
    return [(hit[0], (hit[1] + hit[2]) / 2, "words")] if hit else []


def prompt_close_tokens(conversation_id=None) -> int:
    """The room her closer looks at moments take in her next prompt."""
    import video
    conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
    newest_id, text = _newest_user(conv)
    own, desk, again = _video_plan(conv, newest_id, text)
    return video.CLOSE_TOKENS * len(video_moments(text, "", conv, newest_id, own, desk, again))


def _video_rules(fresh, desk) -> list:
    import video
    modes = {m for _a, m in fresh}
    rules = [rule for mode, rule in ((video.FRAMES, VIDEO_RULE), (video.WATCH, VIDEO_WATCH_RULE),
                                     (video.SPEECH_ONLY, VIDEO_TEXT_RULE)) if mode in modes]
    return rules or ([VIDEO_DESK_RULE] if desk else [])


def _last_line_is_reaction(conv: int) -> bool:
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_messages_table(c)
        row = c.execute("SELECT greeting_kind FROM messages WHERE conversation_id = ? AND "
                        "role = 'assistant' AND COALESCE(active, 1) = 1 ORDER BY id DESC LIMIT 1",
                        (int(conv),)).fetchone()
    finally:
        conn.close()
    return bool(row and row[0] == store.GREETING_KIND_ATTACH)


def missing_picture_note(text: str, conv: int, has_any_shown: bool):
    """The note for a message that talks as if it shows a picture but has
    none (F1, 2026-10-26), or None."""
    if has_any_shown:
        return None
    text = text or ""
    pic_word = bool(_PIC_WORD_RE.search(text))
    showing = bool(_SHOWING_RE.search(text))
    waiting = bool(store.attachments_where("conversation_id = ? AND message_id IS NULL", (int(conv),)))
    if waiting and (pic_word or showing):
        return NOT_SENT_NOTE
    if (pic_word or showing) and _last_line_is_reaction(conv):
        return NO_PICTURE_NOTE
    if pic_word and showing and not _sent_pictures(conv):
        return NO_PICTURE_NOTE
    return None


def _ago(created_at, now=None) -> str:
    try:
        then = datetime.strptime(str(created_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    now = now or datetime.now()
    mins = max(0, int((now - then).total_seconds() // 60))
    if mins < 2:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if then.date() == now.date():
        return f"{mins // 60} h ago"
    if (now.date() - then.date()).days == 1:
        return "yesterday"
    return f"{(now.date() - then.date()).days} days ago"


INDEX_MAX = 8


def picture_index_message(conversation_id=None, now=None):
    """Her short list of this chat's pictures (number, turn, when, on her desk
    or not, caption), so she can refer to them and ask which one is meant.
    None when the chat has none (her prompt is then unchanged)."""
    try:
        conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
        rows = _sent_pictures(conv)
        docs = _sent_documents(conv)
        videos = _sent_videos(conv)
    except Exception:
        return None
    if not (rows or docs or videos):
        return None

    def bits_of(r):
        bits = [f"turn {r['turn']}" if r.get("turn") else "", _ago(r.get("created_at"), now),
                "on your desk" if r.get("on_desk") else ""]
        return "(" + ", ".join(b for b in bits if b) + ")"

    parts = []
    if rows:
        lines = []
        for r in rows[-INDEX_MAX:]:
            head = f"- Picture {r.get('chat_no') or r['id']} {bits_of(r)}"
            lines.append(head + (f": {r['caption']}" if r.get("caption") else ""))
        more = len(rows) - INDEX_MAX
        parts.append(
            "PICTURES THE USER SHOWED YOU IN THIS CHAT (newest last"
            + (f"; {more} older not listed" if more > 0 else "") + "):\n" + "\n".join(lines)
            + "\nYou see the ones on your desk, and an earlier one again when the user asks "
              "about it clearly. For the others you only have these notes - if it is unclear "
              "which picture they mean, ask (by its number).")
    if docs:
        lines = []
        for r in docs[-INDEX_MAX:]:
            name = re.sub(r"[\[\]\"\s]+", " ", r.get("name") or "").strip()
            line = (f'- Document {r.get("chat_no") or r["id"]} {bits_of(r)}: "{name}", '
                    + store.document_size(r))
            lines.append(line + (f": {r['caption']}" if r.get("caption") else ""))
        more = len(docs) - INDEX_MAX
        parts.append(
            "DOCUMENTS THE USER GAVE YOU IN THIS CHAT (newest last"
            + (f"; {more} older not listed" if more > 0 else "") + "):\n" + "\n".join(lines)
            + "\nYou read the ones on your desk, and an earlier one again when the user asks "
              "about it clearly. For the others you only have these notes - if it is unclear "
              "which document they mean, ask (by its number).")
    if videos:
        import video   # late import: video imports this module
        lines = []
        for r in videos[-INDEX_MAX:]:
            name = re.sub(r"[\[\]\"\s]+", " ", r.get("name") or "").strip()
            line = (f'- Video {r.get("chat_no") or r["id"]} {bits_of(r)}: "{name}", '
                    + video.length_text(video.load_meta(r).get("duration")))
            lines.append(line + (f": {r['caption']}" if r.get("caption") else ""))
        more = len(videos) - INDEX_MAX
        parts.append(
            "VIDEOS THE USER SHOWED YOU IN THIS CHAT (newest last"
            + (f"; {more} older not listed" if more > 0 else "") + "):\n" + "\n".join(lines)
            + "\nYou have the ones on your desk, and an earlier one again when the user asks "
              "about it clearly. For the others you only have these notes - if it is unclear "
              "which video they mean, ask (by its number).")
    return {"role": "system", "content": "\n\n".join(parts)}


def with_pictures(messages, conversation_id=None, see=None):
    """Her prompt with the real pictures added: the newest message's own
    pictures and earlier ones it clearly asks about go into that message;
    desk pictures stay in the message they came with (the history stays
    byte-for-byte the same, so her built-in model can reuse it), or - when
    that message has left the window - go into the first user message of the
    window, marked "on your desk". When any picture is shown the newest
    message also gets the picture rule. A message that talks as if it shows
    a picture but has none gets an honest note instead. A model that can't
    see pictures gets text labels + a note. Nothing to add -> the very same
    list."""
    _turn.reads = ""
    _turn.video = False
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not users:
        return messages
    last = users[-1]
    text_of_newest = _text(messages[last].get("content"))
    try:
        conv = store.load_active_conversation() if conversation_id is None else int(conversation_id)
        own, desk, again, newest = _picture_plan(conv, text_of_newest)
        d_own, d_desk, d_again = _document_plan(conv, newest, text_of_newest)
        v_own, v_desk, v_again = _video_plan(conv, newest, text_of_newest)
    except Exception as exc:
        print("[Amadeus] Pictures skipped (could not read them):", repr(exc))
        return messages
    out = [dict(m) for m in messages]
    # Documents are plain text and every model reads them: planned first
    # (their messages are put in at the end, so the indexes below hold).
    docs = _place_documents(out, users, d_own, d_desk, d_again)
    doc_text = ([DOCUMENT_RULE if (d_own or d_again) else DOC_DESK_RULE] if docs else [])
    # Videos go the same way (Phase 3): their own message before the one they
    # came with - the stills (or the real video) + what is said.
    # The user's previous message helps the searches ("...that day?"); labels
    # and notes in brackets are not their words.
    before = re.sub(r"\[[^\]]*\]", " ", _text(messages[users[-2]].get("content"))) \
        if len(users) > 1 and _POINTS_BACK_RE.search(text_of_newest) else ""
    watched = []
    if v_own or v_desk or v_again:
        import video   # late import: video imports this module
        see = can_see_pictures() if see is None else see
        try:
            fresh = _place_videos(out, users, docs, v_own, v_desk, v_again, see)
            # Phase 4: a closer look at the moment this message asks about
            # (not of a video she gets whole, with its sound, this turn).
            whole = {a["id"] for a, m in fresh if m == video.WATCH}
            moments = [] if see is False else [
                m for m in video_moments(text_of_newest, before, conv, newest, v_own, v_desk, v_again)
                if m[0]["id"] not in whole]
            looks = []
            watch = bool(moments) and can_watch_videos()
            for row, t, how in moments:
                parts, words, clip = video.close_block(row, t, how, watch)
                # A message of its own after the words: the video's own
                # message stays the same next turn (prompt reuse).
                docs.setdefault(last + 1, []).append(parts)
                looks.append(words)
                _turn.video = _turn.video or clip
        except Exception as exc:
            print("[Amadeus] Videos skipped (could not read them):", repr(exc))
            return messages
        doc_text = doc_text + _video_rules(fresh, v_desk) + ([CLOSE_LOOK_RULE] if looks else [])
        watched = [video.watched_words(a, m) for a, m in fresh] + looks
        _turn.video = _turn.video or any(m == video.WATCH for _a, m in fresh)
    # Long documents with their notes done: the pages THIS message needs, in
    # the changing part (Phase 4b), and the line shown under her reply.
    reads, found = [], []
    for a, note in ([(a, "") for a in d_own + d_desk]
                    + [(a, " (shown to you again now)") for a in d_again]):
        if a.get("notes") == store.NOTES_DONE:
            block, line = found_pages(a, text_of_newest, note, before)
            if block:
                found.append(block)
            reads.append(line)
        elif a.get("notes") == store.NOTES_WRITING:
            reads.append(f"Document {a.get('chat_no') or a['id']}: the start (still making notes)")
    # Shown under her reply: "Watched: Video 1 (16 stills + what is said) · Read: ..."
    _turn.reads = " · ".join((["Watched: " + "; ".join(watched)] if watched else [])
                             + (["Read: " + "; ".join(reads)] if reads else []))
    if found or any(a.get("notes") == store.NOTES_DONE for a in d_own + d_desk + d_again):
        doc_text = doc_text + [LONG_DOC_RULE] + found
    if not (own or desk or again):
        # A message bringing a document is not one that talks about a
        # missing picture ("look at this").
        note = (None if (d_own or d_again or v_own or v_again)
                else missing_picture_note(text_of_newest, conv, False))
        if not (note or docs):
            return messages
        _add_turn_text(out, last, doc_text + ([note] if note else []))
        return _insert_documents(out, docs)
    see = can_see_pictures() if see is None else see
    if see is False:
        text = _text(out[last].get("content")).strip()
        bits = [text] if text else []
        bits += [store.picture_label(a) for a in own + again]
        bits.append(CANT_SEE_NOTE.format(n=max(1, len(own) or len(own + desk + again))))
        out[last]["content"] = "\n".join(bits)
        if doc_text:
            _add_turn_text(out, last, doc_text)
        return _insert_documents(out, docs)
    # One shape for a picture message, the turn it is sent AND every later
    # turn while the picture is on her desk: [the words] [label] [picture]...
    # with the label exactly as at sending (no caption) - so the message is
    # byte-for-byte the same and her built-in model reuses it (F4, 2026-10-26).
    placed = {}   # message index -> [(row, label)]
    words = {}    # message index -> its words without those labels
    for a in own:
        placed.setdefault(last, []).append((a, _sent_label(a)))
    for a in desk:
        label = _sent_label(a)
        home = next((i for i in users[:-1] if label in _text(out[i].get("content"))), None)
        if home is not None:
            placed.setdefault(home, []).append((a, label))
            words[home] = words.get(home, _text(out[home].get("content"))).replace(" " + label, "") \
                .replace(label, "")
        else:
            placed.setdefault(users[0], []).append(
                (a, store.picture_label(a, " (on your desk, shown earlier)")))
    for a in again:
        # Not "shown again": a picture brought back by a wrong guess made her
        # say "why did you send this?" (2026-11-06).
        placed.setdefault(last, []).append((a, store.picture_label(a, PICTURE_AGAIN_NOTE)))
    shown = 0
    for i, entries in placed.items():
        text = words.get(i, _text(out[i].get("content"))).strip()
        parts = [{"type": "text", "text": text}] if text else []
        for row, label in entries:
            url = _data_url(row)
            if not url:
                continue
            if label:
                parts.append({"type": "text", "text": label})
            parts.append({"type": "image_url", "image_url": {"url": url}})
            shown += 1
        if any(p["type"] == "image_url" for p in parts):
            out[i]["content"] = parts
    if not shown:
        if docs:
            _add_turn_text(out, last, doc_text)
            return _insert_documents(out, docs)
        return messages
    # The rule comes whenever she sees any picture - desk-only turns too (F3:
    # "ふむ" openings, 2026-10-26). A desk picture is not THIS message's
    # picture: the missing-picture note still applies.
    turn_text = [PICTURE_RULE if (own or again) else DESK_RULE] + doc_text
    note = (None if (own or again or d_own or d_again or v_own or v_again)
            else missing_picture_note(text_of_newest, conv, False))
    if note:
        turn_text.append(note)
    _add_turn_text(out, last, turn_text)
    return _insert_documents(out, docs)


def _sent_label(row) -> str:
    """A picture's label as it is when sent (no caption yet) - the same on
    every turn it stays on her desk (memory.load_memory_for_prompt agrees)."""
    return store.picture_label({**row, "caption": None})


def _add_turn_text(out, last, texts) -> None:
    """Text for THIS turn only (the picture rule, a missing-picture note):
    into her RIGHT NOW note when her prompt ends with one (her built-in
    model) - the user's message itself then looks the same next turn; else
    after the newest message's own content."""
    if len(out) - 1 > last and out[-1].get("role") == "system" \
            and isinstance(out[-1].get("content"), str):
        out[-1] = {**out[-1], "content": out[-1]["content"] + "\n\n" + "\n".join(texts)}
        return
    content = out[last].get("content")
    if isinstance(content, list):
        out[last]["content"] = content + [{"type": "text", "text": t} for t in texts]
    else:
        out[last]["content"] = "\n".join([t for t in [_text(content).strip()] if t] + list(texts))


def has_pictures(messages) -> bool:
    return any(isinstance(m.get("content"), list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
        for m in messages or [])


def newest_has_pictures(messages) -> bool:
    """True when the newest user message carries pictures (her own or shown again)."""
    last = next((m for m in reversed(messages or []) if m.get("role") == "user"), None)
    return bool(last) and has_pictures([last])


# ---------- Caption: one short background call after her reply ----------

CAPTION_PROMPT = ("Describe this picture in ONE short English phrase (at most 15 words) for a "
                  "note: what it shows, plainly. Only what is visible. No quotes, no 'This "
                  "picture shows'.")


def write_caption(llm, row) -> str:
    url = _data_url(row)
    if not url:
        return ""
    reply = llm.invoke([{"role": "user", "content": [
        {"type": "text", "text": CAPTION_PROMPT},
        {"type": "image_url", "image_url": {"url": url}}]}])
    text = reply.content if isinstance(reply.content, str) else _text(reply.content)
    text = re.sub(r"<[^>]*>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip()
    return text[:200]


def save_caption(attachment_id, caption: str) -> None:
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("UPDATE attachments SET caption = ? WHERE id = ?", (caption or None, int(attachment_id)))
        conn.commit()
    finally:
        conn.close()


SUMMARY_PROMPT = ("Summarize this document in ONE short English sentence (at most 25 words) "
                  "for a note: what kind of document it is and what it is about. Plain, no "
                  "quotes, no 'This document'.")
SUMMARY_CHARS = 8000   # the start of the document the summary call reads


def write_summary(llm, row) -> str:
    part = read_part(row)[:SUMMARY_CHARS]
    if not part:
        return ""
    reply = llm.invoke([{"role": "user", "content": (
        SUMMARY_PROMPT + f'\n\nDocument "{row.get("name") or "document"}":\n' + part)}])
    text = reply.content if isinstance(reply.content, str) else _text(reply.content)
    text = re.sub(r"<[^>]*>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip()
    return text[:200]


def caption_message(llm, message_id: int) -> list:
    """Caption every picture of one message that has none yet, and write
    each document's one-line summary (E5). Returns the message's rows
    afterwards. A failure leaves that caption empty."""
    rows = store.attachments_where("message_id = ?", (int(message_id),))
    if not rows or llm is None:
        return rows
    sees = None
    for row in rows:
        if row.get("caption"):
            continue
        try:
            if row["kind"] == "document":
                row["caption"] = write_summary(llm, row)
            elif row["kind"] == "video":
                import video   # late import: video imports this module
                sees = can_see_pictures() if sees is None else sees
                row["caption"] = video.write_caption(llm, row, sees)
            else:
                sees = can_see_pictures() if sees is None else sees
                if sees is False:
                    continue
                row["caption"] = write_caption(llm, row)
            save_caption(row["id"], row["caption"])
        except Exception as exc:
            print(f"[Amadeus] Attachment #{row['id']}: caption failed:", repr(exc))
    return rows


# ---------- Her reaction when something is attached (D8) ----------
# How often (MH D8c; its Settings control comes with the screen, Phase 2):
REACT_ALWAYS = "always"        # every time (default)
REACT_QUIET = "quiet"          # every time, then quiet ~5 min
REACT_ONCE = "once"            # once per conversation
REACT_OFF = "off"
REACT_CHOICES = (REACT_ALWAYS, REACT_QUIET, REACT_ONCE, REACT_OFF)
REACT_QUIET_SECONDS = 300
_LAST_REACTION = {}            # conversation -> time.monotonic() of her last one
_REACTED_IN = set()            # conversations she reacted in (this session)


def reaction_setting_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(store.PATH_TO_MEMORY)),
                        "attach_reaction.txt")


def load_reaction_setting() -> str:
    try:
        with open(reaction_setting_path(), encoding="utf-8") as f:
            value = f.read().strip().lower()
    except OSError:
        return REACT_ALWAYS
    return value if value in REACT_CHOICES else REACT_ALWAYS


def save_reaction_setting(value: str) -> str:
    value = str(value or "").strip().lower()
    if value not in REACT_CHOICES:
        raise ValueError("Unknown choice")
    with open(reaction_setting_path(), "w", encoding="utf-8") as f:
        f.write(value)
    return value


def _attach_lines(conv: int) -> int:
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_messages_table(c)
        (n,) = c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND "
                         "greeting_kind = ?", (int(conv), store.GREETING_KIND_ATTACH)).fetchone()
        return int(n or 0)
    finally:
        conn.close()


def should_react(conversation_id) -> bool:
    conv = int(conversation_id)
    choice = load_reaction_setting()
    if choice == REACT_OFF:
        return False
    if choice == REACT_ONCE:
        return not (conv in _REACTED_IN or _attach_lines(conv))
    if choice == REACT_QUIET:
        last = _LAST_REACTION.get(conv)
        return last is None or time.monotonic() - last >= REACT_QUIET_SECONDS
    return True


def note_reaction(conversation_id) -> None:
    conv = int(conversation_id)
    _LAST_REACTION[conv] = time.monotonic()
    _REACTED_IN.add(conv)


# A phone / camera / screenshot tool's own file name says nothing about the
# picture (D8b): she is told only "a picture" then.
_CAMERA_NAME_RE = re.compile(
    r"^(img|image|dsc|dscn|dcim|pxl|mvimg|photo|pic|picture|screenshot|screen shot|"
    r"capture|snap|wp|wa|signal|gopr|dji|p|sam|cam|bild|foto|スクリーンショット|"
    r"vid|video|mov|mvi|rec|recording|screen recording|screenrecording|screenrecorder|trim)"
    r"[\s_\-]*[\d\s_\-.()]*(?:at[\s\d_\-.]*)?(?:am|pm)?[\s_\-]*\d*$",
    re.IGNORECASE)


def meaningful_name(name: str) -> str:
    """The file name without its extension when it says something, else ""."""
    stem = os.path.splitext(_clean_name(name))[0].strip()
    if not stem or _CAMERA_NAME_RE.match(stem) or re.fullmatch(r"[\d\s_\-.()]+", stem):
        return ""
    if re.fullmatch(r"[0-9a-f\-]{16,}", stem.lower()):
        return ""   # a random id (chat apps)
    return stem[:80]


def reaction_event(rows, history_text: str = "") -> str:
    """The prompt line for her reaction: what is attached (type + name only)."""
    named = [_what(r) for r in rows]
    if len(named) == 1:
        what = named[0]
    else:
        kinds = {r.get("kind") or "picture" for r in rows}
        noun = ("pictures" if kinds == {"picture"} else "documents" if kinds == {"document"}
                else "videos" if kinds == {"video"} else "files")
        what = f"{len(named)} {noun} (" + "; ".join(named) + ")"
    mentioned = any(meaningful_name(r.get("name")) and meaningful_name(r.get("name")).lower()
                    in (history_text or "").lower() for r in rows)
    kinds = {r.get("kind") for r in rows}
    seen = ("watched" if kinds == {"video"} else "seen or read" if "document" in kinds
            else "seen")
    return ("[Attachment event: the user just attached " + what + ". It is NOT sent yet and "
            f"you have not {seen} it." + ("" if mentioned else " It was not mentioned "
                                           "before in this conversation.") + "]")


def _what(row, article: str = "a") -> str:
    """"a picture named "x"", "a document named "x"", "a long pasted text"."""
    if row.get("kind") == "document":
        if (row.get("name") or "").lower().startswith("pasted text"):
            return f"{article} long pasted text"
        stem = os.path.splitext(_clean_name(row.get("name")))[0].strip()[:80]
        return f'{article} document named "{stem}"' if stem else f"{article} document"
    if row.get("kind") == "video":
        import video   # late import: video imports this module
        stem = meaningful_name(row.get("name"))
        length = video.length_text(video.load_meta(row).get("duration"))
        return (f'{article} video named "{stem}" ({length} long)' if stem
                else f"{article} video ({length} long)")
    stem = meaningful_name(row.get("name"))
    return f'{article} picture named "{stem}"' if stem else f"{article} picture"


def title_from_picture(conversation_id, message_id, text: str, rows) -> str:
    """A new chat whose first message is only a picture has no words to be
    named after: once the caption is written it is named "Picture: <caption>"
    (MH U2, 2026-10-26). Returns the new title, "" when nothing changed."""
    first_row = next((r for r in rows or [] if r.get("caption")), None)
    caption = first_row.get("caption") if first_row else ""
    if (text or "").strip() or not caption:
        return ""
    prefix = {"document": "Document: ", "video": "Video: "}.get(first_row.get("kind"), "Picture: ")
    conv = int(conversation_id)
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_messages_table(c)
        row = c.execute("SELECT title FROM conversations WHERE id = ?", (conv,)).fetchone()
        # Her own lines (an attach reaction, a greeting) may come first: it is
        # the user's FIRST message that names a chat.
        (first,) = c.execute("SELECT MIN(id) FROM messages WHERE conversation_id = ? "
                             "AND role = 'user'", (conv,)).fetchone()
        if not row or row[0] != "New chat" or first != int(message_id):
            return ""
        title = prefix + " ".join(caption.split())
        if len(title) > 40:   # the 40-letter title limit, cut at a whole word
            title = title[:41].rsplit(" ", 1)[0]
        title = title.rstrip(" ,.;:-")
        c.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv))
        conn.commit()
        return title
    finally:
        conn.close()


def picture_titled_chat(message_id):
    """The chat whose title came from this message's picture (a picture-only
    first message, title "Picture: ..."), else None - deleting that message
    gives the chat its "New chat" title back (MH L2, 2026-10-27)."""
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_messages_table(c)
        row = c.execute("SELECT conversation_id, role, COALESCE(content, '') FROM messages "
                        "WHERE id = ?", (int(message_id),)).fetchone()
        if not row or row[1] != "user" or row[2].strip():
            return None
        conv = int(row[0])
        (first,) = c.execute("SELECT MIN(id) FROM messages WHERE conversation_id = ? AND "
                             "role = 'user'", (conv,)).fetchone()
        title = c.execute("SELECT title FROM conversations WHERE id = ?", (conv,)).fetchone()
    finally:
        conn.close()
    if first != int(message_id) or not title or not str(title[0]).startswith(TITLE_PREFIXES):
        return None
    if not store.attachments_where("message_id = ?", (int(message_id),)):
        return None
    return conv


TITLE_PREFIXES = ("Picture: ", "Document: ", "Video: ")


def untitle_chat(conversation_id) -> None:
    conn = store._connect_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE conversations SET title = 'New chat' WHERE id = ? AND "
                  "(title LIKE 'Picture: %' OR title LIKE 'Document: %' OR title LIKE 'Video: %')",
                  (int(conversation_id),))
        conn.commit()
    finally:
        conn.close()


def may_comment_on_withdraw(conversation_id) -> bool:
    """She says a line about a taken-back card only when (a) her reaction
    setting is not Off, (b) her last line in the chat is her attach reaction
    with no user message after it, and (c) nothing else is still waiting to
    be sent (several cards removed at once = one line, after the last)."""
    conv = int(conversation_id)
    if load_reaction_setting() == REACT_OFF:
        return False
    if store.attachments_where("conversation_id = ? AND message_id IS NULL", (conv,)):
        return False
    conn = store._connect_db()
    try:
        c = conn.cursor()
        store._ensure_messages_table(c)
        row = c.execute("SELECT role, greeting_kind FROM messages WHERE conversation_id = ? "
                        "AND COALESCE(active, 1) = 1 ORDER BY id DESC LIMIT 1", (conv,)).fetchone()
    finally:
        conn.close()
    return bool(row) and row[0] == "assistant" and row[1] == store.GREETING_KIND_ATTACH


def withdraw_event(row) -> str:
    row = row or {}
    what = _what(row, "the")
    saw = {"document": "read", "video": "watched"}.get(row.get("kind"), "saw")
    return (f"[Attachment event: the user removed {what} they had attached - they took it "
            f"back before sending it. You never {saw} it.]")


def labelled_line(text: str, rows) -> str:
    """The user's words plus their pictures, for her LONG-TERM memory. No
    picture number or turn: those belong to one chat, and her memory recalls
    lines in OTHER chats, where "picture 3" is a different picture."""
    labels = []
    for r in rows or []:
        caption = re.sub(r"[\[\]\s]+", " ", str(r.get("caption") or "")).strip()[:200]
        what = "a picture"
        if r.get("kind") == "document":
            name = re.sub(r"[\[\]\"\s]+", " ", str(r.get("name") or "")).strip()[:120]
            what = f'a document "{name}"' if name else "a document"
        elif r.get("kind") == "video":
            name = meaningful_name(r.get("name"))
            what = f'a video "{name}"' if name else "a video"
        labels.append(f"[The user showed you {what}" + (f": {caption}]" if caption else "]"))
    return ((text or "").strip() + " " + " ".join(labels)).strip()

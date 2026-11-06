"""Pictures the user shows her (attachments Phase 1, 2026-10-26).

Covers: the upload checks (types, size, HEIC, documents, broken files), the
shrink + metadata strip, storage + clean-up (message / chat / reset / unsent
cards), the send (claim, release on failure, room check), her prompt (labels
in the history, real pictures only in her reply calls, the desk, a model
that can't see), the helpers that must keep reading text, the caption and
her attach reaction. Tiny pictures made with Pillow; a temp database.
"""
import io
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

import api
import attachments
import chat
import cm_bridge
import memory as store
from chat import AmadeusPack, AmadeusReply


def picture(fmt="JPEG", size=(64, 48), color=(200, 30, 30), mode="RGB", **save):
    img = Image.new(mode, size, color if mode != "RGBA" else color + (128,))
    out = io.BytesIO()
    img.save(out, fmt, **save)
    return out.getvalue()


def jpeg_with_gps():
    img = Image.new("RGB", (80, 60), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"          # camera make
    exif[0x0110] = "Model X"
    gps = {1: "N", 2: (35.0, 39.0, 29.0), 3: "E", 4: (139.0, 41.0, 30.0)}
    exif[0x8825] = gps                   # GPS block
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


class TempStore(unittest.TestCase):
    """A temp memory.db (attachments live next to it)."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._dir.name, "memory.db")
        self._patches = [patch.object(store, "PATH_TO_MEMORY", self.db),
                         patch.object(store, "load_active_conversation", return_value=1),
                         patch.object(store, "effective_context_budget", return_value=16000)]
        for p in self._patches:
            p.start()
        attachments._DATA_URLS.clear()
        attachments._LAST_REACTION.clear()
        attachments._REACTED_IN.clear()
        attachments.reset_capabilities()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._dir.cleanup()

    def upload(self, data=None, name="cat on the sofa.jpg", conv=1):
        return attachments.save_upload(name, data or picture(), conv)

    def user_msg(self, text="look", conv=1):
        return store.append_message("user", text, conversation_id=conv)

    def her_msg(self, text="nice", conv=1):
        return store.append_message("assistant", text, conversation_id=conv, japanese="いいね")

    def files(self):
        root = store.attachments_dir()
        out = []
        for base, _dirs, names in os.walk(root):
            out += [os.path.relpath(os.path.join(base, n), root).replace("\\", "/") for n in names]
        return sorted(out)


# ---------------------------------------------------------------- upload checks

class PrepareTests(unittest.TestCase):
    def test_jpeg_ok(self):
        p = attachments.prepare_picture("a.jpg", picture())
        self.assertEqual(p["mime"], "image/jpeg")
        self.assertEqual((p["width"], p["height"]), (64, 48))
        self.assertTrue(p["thumb"].startswith(b"\xff\xd8"))

    def test_big_picture_shrunk_to_1568(self):
        p = attachments.prepare_picture("big.jpg", picture(size=(4000, 3000)))
        self.assertEqual(max(p["width"], p["height"]), attachments.MAX_SIDE)
        self.assertEqual(p["width"], 1568)

    def test_small_picture_not_enlarged(self):
        p = attachments.prepare_picture("s.png", picture("PNG", size=(20, 10)))
        self.assertEqual((p["width"], p["height"]), (20, 10))

    def test_gps_and_camera_removed(self):
        raw = jpeg_with_gps()
        self.assertIn(0x8825, Image.open(io.BytesIO(raw)).getexif())
        p = attachments.prepare_picture("trip.jpg", raw)
        out = Image.open(io.BytesIO(p["bytes"]))
        self.assertEqual(len(out.getexif()), 0)
        self.assertNotIn(b"PhoneMaker", p["bytes"])

    def test_phone_photo_turned_upright(self):
        img = Image.new("RGB", (60, 20), (0, 0, 0))
        exif = Image.Exif()
        exif[0x0112] = 6                 # "rotate 90" (portrait phone photo)
        out = io.BytesIO()
        img.save(out, "JPEG", exif=exif)
        p = attachments.prepare_picture("p.jpg", out.getvalue())
        self.assertEqual((p["width"], p["height"]), (20, 60))

    def test_png_screenshot_stays_png(self):
        p = attachments.prepare_picture("shot.png", picture("PNG"))
        self.assertEqual(p["mime"], "image/png")

    def test_transparent_png_on_white(self):
        p = attachments.prepare_picture("t.png", picture("PNG", mode="RGBA", color=(0, 0, 0)))
        img = Image.open(io.BytesIO(p["bytes"]))
        self.assertEqual(img.mode, "RGB")
        r, g, b = img.getpixel((1, 1))
        self.assertGreater(r, 100)       # half-transparent black over white = grey

    def test_webp_gif_bmp_accepted(self):
        for fmt in ("WEBP", "GIF", "BMP"):
            p = attachments.prepare_picture("x." + fmt.lower(), picture(fmt))
            self.assertIn(p["mime"], ("image/jpeg", "image/png"), fmt)

    def test_animated_gif_first_frame(self):
        frames = [Image.new("P", (16, 16), i) for i in (1, 2, 3)]
        out = io.BytesIO()
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
        p = attachments.prepare_picture("a.gif", out.getvalue())
        self.assertEqual((p["width"], p["height"]), (16, 16))

    def test_heic_by_name_and_by_content(self):
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_picture("IMG_0001.HEIC", b"xxxx")
        self.assertEqual(ctx.exception.code, "heic")
        self.assertIn("JPG or PNG", str(ctx.exception))
        fake = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_picture("photo.bin", fake)
        self.assertEqual(ctx.exception.code, "heic")

    def test_office_files_not_yet(self):
        for name in ("old.doc", "sheet.xlsx", "slides.pptx", "letter.rtf"):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                attachments.prepare_picture(name, b"PK\x03\x04 not a picture")
            self.assertEqual(ctx.exception.code, "later", name)
            self.assertIn("PDF", str(ctx.exception))
        for name in ("notes.pdf", "essay.docx", "list.txt", "data.csv", "app.py"):
            self.assertTrue(attachments.is_document_name(name), name)

    def test_unknown_file_unsupported(self):
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_picture("thing.xyz", b"\x01\x02\x03 not a picture")
        self.assertEqual(ctx.exception.code, "unsupported")

    def test_other_image_format_unsupported(self):
        out = io.BytesIO()
        Image.new("RGB", (8, 8)).save(out, "TIFF")
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_picture("scan.tiff", out.getvalue())
        self.assertEqual(ctx.exception.code, "unsupported")

    def test_empty_and_too_big(self):
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_picture("a.jpg", b"")
        self.assertEqual(ctx.exception.code, "empty")
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_picture("a.jpg", b"\xff" * (attachments.MAX_FILE_BYTES + 1))
        self.assertEqual(ctx.exception.code, "too_big")
        self.assertIn("20 MB", str(ctx.exception))

    def test_too_many_pixels_refused_unopened(self):
        with patch.object(attachments, "MAX_PIXELS", 100):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                attachments.prepare_picture("a.png", picture("PNG", size=(20, 20)))
        self.assertEqual(ctx.exception.code, "too_many_pixels")

    def test_cut_off_file_is_broken(self):
        data = picture(size=(200, 200))
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_picture("cut.jpg", data[:len(data) // 3])
        self.assertIn(ctx.exception.code, ("broken", "unsupported"))

    def test_name_cleaned(self):
        p = attachments.prepare_picture("..\\..\\evil\x07 name.jpg", picture())
        self.assertEqual(p["name"], "evil name.jpg")


# ---------------------------------------------------------------- storage

class StorageTests(TempStore):
    def test_upload_saves_row_and_files(self):
        row = self.upload()
        self.assertEqual(row["kind"], "picture")
        self.assertIsNone(row["message_id"])
        self.assertFalse(row["on_desk"])
        self.assertEqual(self.files(), [f"1/{row['id']}.jpg", f"1/{row['id']}_thumb.jpg"])
        self.assertTrue(os.path.isfile(attachments.file_path(row["id"])))
        self.assertTrue(os.path.isfile(attachments.file_path(row["id"], thumb=True)))

    def test_card_for_screen(self):
        row = self.upload()
        card = attachments.card(row, 1)
        self.assertEqual(card["thumb_url"], f"/attachments/{row['id']}/thumb")
        self.assertEqual(card["name"], "cat on the sofa.jpg")
        self.assertGreater(card["room"], 0)

    def test_claim_links_and_puts_on_desk(self):
        a, b = self.upload(), self.upload()
        uid = self.user_msg()
        rows = attachments.claim([a["id"], b["id"]], uid, 1)
        self.assertEqual([r["id"] for r in rows], [a["id"], b["id"]])
        self.assertTrue(all(r["on_desk"] and r["message_id"] == uid for r in rows))
        self.assertEqual(attachments.pending([a["id"]]), [])
        self.assertEqual(len(attachments.desk(1)), 2)

    def test_claim_ignores_unknown_and_sent(self):
        a = self.upload()
        uid = self.user_msg()
        attachments.claim([a["id"]], uid, 1)
        uid2 = self.user_msg("again")
        self.assertEqual(attachments.claim([a["id"], 999, "x", True], uid2, 1), [])

    def test_release_returns_card_to_tray(self):
        a = self.upload()
        uid = self.user_msg()
        attachments.claim([a["id"]], uid, 1)
        attachments.release(uid)
        self.assertEqual([r["id"] for r in attachments.pending([a["id"]])], [a["id"]])
        self.assertEqual(attachments.desk(1), [])

    def test_remove_unsent_deletes(self):
        a = self.upload()
        self.assertEqual(attachments.remove(a["id"]), "deleted")
        self.assertIsNone(attachments.get(a["id"]))
        self.assertEqual(self.files(), [])
        self.assertEqual(attachments.remove(a["id"]), "")

    def test_remove_sent_only_leaves_desk(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg(), 1)
        self.assertEqual(attachments.remove(a["id"]), "off_desk")
        row = attachments.get(a["id"])
        self.assertFalse(row["on_desk"])
        self.assertEqual(len(self.files()), 2)

    def test_delete_message_removes_its_pictures(self):
        a = self.upload()
        uid = self.user_msg()
        attachments.claim([a["id"]], uid, 1)
        store.delete_message(uid)
        self.assertIsNone(attachments.get(a["id"]))
        self.assertEqual(self.files(), [])

    def test_delete_conversation_and_reset(self):
        conv2 = store.create_conversation()
        a = self.upload(conv=1)
        b = self.upload(conv=conv2)
        attachments.claim([a["id"]], self.user_msg(conv=1), 1)
        attachments.claim([b["id"]], self.user_msg(conv=conv2), conv2)
        with patch.object(store, "load_active_conversation_raw", return_value="1"):
            store.delete_conversation(conv2)
        self.assertIsNone(attachments.get(b["id"]))
        self.assertIsNotNone(attachments.get(a["id"]))
        store.reset_memory(1)
        self.assertIsNone(attachments.get(a["id"]))
        self.assertEqual(self.files(), [])

    def test_cleanup_unsent(self):
        old, fresh, sent = self.upload(), self.upload(), self.upload()
        attachments.claim([sent["id"]], self.user_msg(), 1)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE attachments SET created_at = '2020-01-01 00:00:00' WHERE id IN (?, ?)",
                     (old["id"], sent["id"]))
        conn.commit()
        conn.close()
        stray = os.path.join(store.attachments_dir(), "1", "777.jpg")
        with open(stray, "wb") as f:
            f.write(b"x")
        past = time.time() - 3 * 86400
        os.utime(stray, (past, past))
        self.assertEqual(attachments.cleanup_unsent(), 1)
        self.assertIsNone(attachments.get(old["id"]))
        self.assertIsNotNone(attachments.get(fresh["id"]))
        self.assertIsNotNone(attachments.get(sent["id"]))
        self.assertFalse(os.path.exists(stray))

    def test_file_path_never_leaves_folder(self):
        a = self.upload()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE attachments SET path = '../../memory.db' WHERE id = ?", (a["id"],))
        conn.commit()
        conn.close()
        self.assertIsNone(attachments.file_path(a["id"]))
        store.remove_attachment_files(["../memory.db"])
        self.assertTrue(os.path.exists(self.db))

    def test_get_memory_rows_carry_attachments(self):
        a = self.upload()
        uid = self.user_msg("what is this?")
        attachments.claim([a["id"]], uid, 1)
        self.her_msg()
        rows = store.load_memory_raw(1)
        user_row = next(r for r in rows if r["id"] == uid)
        self.assertEqual(user_row["attachments"][0]["id"], a["id"])
        self.assertEqual(user_row["attachments"][0]["url"], f"/attachments/{a['id']}/file")
        her_row = next(r for r in rows if r["role"] == "assistant")
        self.assertNotIn("attachments", her_row)

    def test_room_and_fits(self):
        with patch.object(store, "effective_context_budget", return_value=4000):
            # (4000 - 1500) // 500 = 5 pictures at most
            self.assertEqual(attachments.room_left(1), 5)
            ids = [self.upload()["id"] for _ in range(5)]
            self.assertEqual(attachments.room_left(1), 0)
            self.assertEqual(attachments.fits(ids, 1), (True, 5))
            ids.append(self.upload()["id"])
            self.assertEqual(attachments.fits(ids, 1), (False, 5))


# ---------------------------------------------------------------- her prompt (text side)

class PromptTextTests(TempStore):
    def test_newest_message_has_no_label_older_ones_do(self):
        a = self.upload()
        u1 = self.user_msg("look at this")
        attachments.claim([a["id"]], u1, 1)
        attachments.save_caption(a["id"], "a red square [test]")
        self.her_msg()
        self.user_msg("and now?")
        texts = [m["content"] for m in store.load_memory_for_prompt(1)]
        # On her desk: the label exactly as when it was sent (she sees the picture itself)
        self.assertEqual(texts[0], "look at this [Picture 1 (turn 1) from the user]")
        self.assertEqual(texts[-1], "and now?")
        attachments.remove(a["id"])          # off her desk: only the caption is left
        texts = [m["content"] for m in store.load_memory_for_prompt(1)]
        self.assertEqual(texts[0], "look at this [Picture 1 (turn 1) from the user: a red square test]")

    def test_picture_only_message_label(self):
        a = self.upload()
        u1 = self.user_msg("")
        attachments.claim([a["id"]], u1, 1)
        self.her_msg()
        self.user_msg("next")
        first = store.load_memory_for_prompt(1)[0]["content"]
        self.assertEqual(first, "[Picture 1 (turn 1) from the user]")

    def test_budget_makes_room_for_pictures(self):
        self.assertEqual(store.text_context_budget(1), 16000)
        a, b = self.upload(), self.upload()
        attachments.claim([a["id"], b["id"]], self.user_msg(), 1)
        self.assertEqual(store.text_context_budget(1), 15000)
        attachments.remove(a["id"])          # off her desk, but still in the newest message
        self.assertEqual(store.text_context_budget(1), 15000)
        self.her_msg()
        self.user_msg("next")                # a's message is no longer the newest
        self.assertEqual(store.text_context_budget(1), 15500)

    def test_content_text_strips_labels_and_rule(self):
        parts = [{"type": "text", "text": "what anime is this?"},
                 {"type": "text", "text": "[Picture 4 (turn 2) from the user]"},
                 {"type": "image_url", "image_url": {"url": "data:x"}},
                 {"type": "text", "text": attachments.PICTURE_RULE},
                 {"type": "text", "text": attachments.NO_PICTURE_NOTE}]
        self.assertEqual(chat._content_text(parts), "what anime is this?")
        self.assertEqual(chat._content_text("hi [Picture 3 (turn 1) from the user: a cat]"), "hi")
        self.assertEqual(chat._content_text("hi\n" + attachments.NOT_SENT_NOTE), "hi")
        self.assertEqual(chat._content_text("[Picture this] ok"), "[Picture this] ok")   # user's own words
        self.assertEqual(chat._content_text("plain"), "plain")
        self.assertEqual(chat._content_text(None), "")


# ---------------------------------------------------------------- her prompt (real pictures)

def prompt(*history, right_now=True):
    msgs = [{"role": "system", "content": "RULES"}] + list(history)
    if right_now:
        msgs.append({"role": "system", "content": chat._RIGHT_NOW_LABEL + "NOW"})
    return msgs


def images(message):
    c = message.get("content")
    return [p for p in c if p.get("type") == "image_url"] if isinstance(c, list) else []


class WithPicturesTests(TempStore):
    def test_no_pictures_same_list(self):
        msgs = prompt({"role": "user", "content": "hi"})
        self.user_msg("hi")
        self.assertIs(attachments.with_pictures(msgs, 1, see=True), msgs)

    def test_newest_pictures_become_parts(self):
        a, b = self.upload(), self.upload(picture("PNG"), "chart.png")
        attachments.claim([a["id"], b["id"]], self.user_msg("which is better?"), 1)
        msgs = prompt({"role": "user", "content": "which is better?"})
        out = attachments.with_pictures(msgs, 1, see=True)
        parts = out[1]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "which is better?"})
        self.assertEqual(parts[1]["text"], "[Picture 1 (turn 1) from the user]")
        self.assertTrue(parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(parts[3]["text"], "[Picture 2 (turn 1) from the user]")
        self.assertTrue(parts[4]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(len(parts), 5)                       # the rule is not in her message...
        self.assertEqual(out[-1]["content"],                  # ...but in the RIGHT NOW note (last)
                         chat._RIGHT_NOW_LABEL + "NOW\n\n" + attachments.PICTURE_RULE)
        self.assertEqual(msgs[1]["content"], "which is better?")              # original untouched
        self.assertEqual(msgs[-1]["content"], chat._RIGHT_NOW_LABEL + "NOW")

    def test_rule_in_the_message_without_a_right_now_note(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg("look"), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": "look"}, right_now=False),
                                        1, see=True)
        self.assertEqual(out[1]["content"][-1]["text"], attachments.PICTURE_RULE)

    def test_picture_message_is_the_same_on_the_next_turn(self):
        # F4: the message a picture came with must be byte-for-byte the same
        # the turn it is sent and the turn after (her built-in model then
        # reuses everything up to the newest message).
        a, b = self.upload(), self.upload()
        attachments.claim([a["id"], b["id"]], self.user_msg("  two of them  "), 1)
        turn_one = attachments.with_pictures(
            prompt(*[{"role": m["role"], "content": m["content"]} for m in store.load_memory_for_prompt(1)]),
            1, see=True)
        attachments.save_caption(a["id"], "a red square")          # written after her reply
        self.her_msg()
        self.user_msg("which one is redder?")
        history = [{"role": m["role"], "content": m["content"]} for m in store.load_memory_for_prompt(1)]
        turn_two = attachments.with_pictures(prompt(*history), 1, see=True)
        self.assertEqual(turn_two[1], turn_one[1])
        self.assertEqual(turn_two[3]["content"], "which one is redder?")

    def test_picture_only_message(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg(""), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": ""}), 1, see=True)
        parts = out[1]["content"]
        self.assertEqual(parts[0]["text"], "[Picture 1 (turn 1) from the user]")
        self.assertEqual(len(images(out[1])), 1)

    def test_desk_picture_stays_in_its_message(self):
        a = self.upload()
        u1 = self.user_msg("my cat")
        attachments.claim([a["id"]], u1, 1)
        self.her_msg()
        self.user_msg("what should I feed her?")
        history = [{"role": "user", "content": "my cat [Picture 1 (turn 1) from the user]"},
                   {"role": "assistant", "content": "かわいい"},
                   {"role": "user", "content": "what should I feed her?"}]
        out = attachments.with_pictures(prompt(*history), 1, see=True)
        self.assertEqual(len(images(out[1])), 1)
        self.assertEqual([p.get("text") for p in out[1]["content"]],
                         ["my cat", "[Picture 1 (turn 1) from the user]", None])
        # desk turns get the quieter desk rule (F3), in the RIGHT NOW note
        self.assertEqual(out[3]["content"], "what should I feed her?")
        self.assertTrue(out[-1]["content"].endswith(attachments.DESK_RULE))

    def test_desk_picture_numbers_do_not_collide(self):
        rows = [self.upload() for _ in range(12)]
        attachments.claim([r["id"] for r in rows], self.user_msg("many"), 1)
        for r in rows:
            if r is not rows[0]:
                attachments.remove(r["id"])          # only picture 1 stays on the desk
        self.her_msg()
        self.user_msg("hello")
        history = [{"role": "user", "content": "many [Picture 1 (turn 1) from the user] "
                                               "[Picture 12 (turn 1) from the user]"},
                   {"role": "assistant", "content": "ok"}, {"role": "user", "content": "hello"}]
        out = attachments.with_pictures(prompt(*history), 1, see=True)
        self.assertEqual(len(images(out[1])), 1)

    def test_desk_picture_out_of_window_goes_first(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg("old"), 1)
        attachments.save_caption(a["id"], "a cat")
        self.her_msg()
        self.user_msg("later")
        self.her_msg()
        self.user_msg("now")
        history = [{"role": "user", "content": "later"},
                   {"role": "assistant", "content": "ok"},
                   {"role": "user", "content": "now"}]
        out = attachments.with_pictures(prompt(*history), 1, see=True)
        first = out[1]["content"]
        self.assertEqual(first[0]["text"], "later")
        self.assertEqual(first[1]["text"],
                         "[Picture 1 (turn 1) from the user (on your desk, shown earlier): a cat]")
        self.assertEqual(len(images(out[1])), 1)

    def test_off_desk_old_picture_not_shown(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg("old"), 1)
        attachments.remove(a["id"])
        self.her_msg()
        self.user_msg("now")
        msgs = prompt({"role": "user", "content": "old [Picture 1 (turn 1) from the user]"},
                      {"role": "assistant", "content": "ok"}, {"role": "user", "content": "now"})
        self.assertIs(attachments.with_pictures(msgs, 1, see=True), msgs)

    def test_model_that_cannot_see(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg("look"), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": "look"}), 1, see=False)
        text = out[1]["content"]
        self.assertIsInstance(text, str)
        self.assertIn("[Picture 1 (turn 1) from the user]", text)
        self.assertIn("cannot see", text)

    def test_missing_file_is_skipped(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg("look"), 1)
        os.remove(attachments.file_path(a["id"]))
        msgs = prompt({"role": "user", "content": "look"})
        self.assertIs(attachments.with_pictures(msgs, 1, see=True), msgs)

    def test_has_pictures(self):
        self.assertFalse(attachments.has_pictures([{"role": "user", "content": "x"}]))
        self.assertTrue(attachments.has_pictures([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:x"}}]}]))


# ---------------------------------------------------------------- numbers, turns, original

class NumberTurnTests(TempStore):
    def test_chat_number_and_turn_on_send(self):
        a, b = self.upload(), self.upload()
        attachments.claim([a["id"], b["id"]], self.user_msg("two"), 1)
        self.her_msg()
        c = self.upload()
        attachments.claim([c["id"]], self.user_msg("one more"), 1)
        got = {r["id"]: (r["chat_no"], r["turn"]) for r in store.attachments_where("1 = 1", ())}
        self.assertEqual(got, {a["id"]: (1, 1), b["id"]: (2, 1), c["id"]: (3, 2)})

    def test_numbers_are_per_chat(self):
        conv2 = store.create_conversation()
        a = self.upload(conv=1)
        attachments.claim([a["id"]], self.user_msg(conv=1), 1)
        b = self.upload(conv=conv2)
        attachments.claim([b["id"]], self.user_msg(conv=conv2), conv2)
        self.assertEqual(attachments.get(b["id"])["chat_no"], 1)

    def test_release_clears_number(self):
        a = self.upload()
        uid = self.user_msg()
        attachments.claim([a["id"]], uid, 1)
        attachments.release(uid)
        row = attachments.get(a["id"])
        self.assertIsNone(row["chat_no"])
        self.assertIsNone(row["turn"])

    def test_screen_gets_number_and_turn(self):
        a = self.upload()
        uid = self.user_msg("x")
        attachments.claim([a["id"]], uid, 1)
        item = next(r for r in store.load_memory_raw(1) if r["id"] == uid)["attachments"][0]
        self.assertEqual((item["number"], item["turn"]), (1, 1))
        self.assertEqual(item["original_url"], f"/attachments/{a['id']}/original")

    def test_big_picture_keeps_full_size_original(self):
        row = self.upload(picture(size=(3000, 2000)), "big.jpg")
        self.assertTrue(row["original"].endswith("_original.jpg"))
        full = Image.open(attachments.file_path(row["id"], original=True))
        self.assertEqual(full.size, (3000, 2000))
        self.assertEqual(len(full.getexif()), 0)
        shown = Image.open(attachments.file_path(row["id"]))
        self.assertEqual(max(shown.size), 1568)

    def test_small_picture_has_no_separate_original(self):
        row = self.upload()
        self.assertIsNone(row["original"])
        self.assertEqual(attachments.file_path(row["id"], original=True), attachments.file_path(row["id"]))

    def test_original_deleted_with_message(self):
        row = self.upload(picture(size=(2000, 1600)), "big.jpg")
        uid = self.user_msg()
        attachments.claim([row["id"]], uid, 1)
        self.assertEqual(len(self.files()), 3)
        store.delete_message(uid)
        self.assertEqual(self.files(), [])

    def test_gps_removed_from_original_too(self):
        img = Image.new("RGB", (2400, 1800), (10, 120, 200))
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (35.0, 39.0, 29.0)}
        out = io.BytesIO()
        img.save(out, "JPEG", exif=exif)
        row = self.upload(out.getvalue(), "trip.jpg")
        full = Image.open(attachments.file_path(row["id"], original=True))
        self.assertEqual(len(full.getexif()), 0)

    def test_original_route(self):
        row = self.upload(picture(size=(2000, 1600)), "big.jpg")
        client = api.application.test_client()
        r = client.get(f"/attachments/{row['id']}/original")
        self.assertEqual(Image.open(io.BytesIO(r.data)).size, (2000, 1600))
        r.close()


# ---------------------------------------------------------------- other tabs

class OtherTabsTests(TempStore):
    def test_long_term_memory_line_has_no_chat_number(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg("look"), 1)
        attachments.save_caption(a["id"], "a sunset [over] a lake")
        line = attachments.labelled_line("look", [attachments.get(a["id"])])
        self.assertEqual(line, "look [The user showed you a picture: a sunset over a lake]")
        self.assertNotIn("Picture 1", line)
        self.assertNotIn("turn", line)

    def test_reply_reads_the_chat_once(self):
        conv2 = store.create_conversation()
        a = self.upload(conv=1)
        attachments.claim([a["id"]], self.user_msg("tab one picture", conv=1), 1)
        self.user_msg("tab two words", conv=conv2)
        msgs = prompt({"role": "user", "content": "tab one picture"})
        # the active tab changes while she answers: her pictures stay tab one's
        with patch.object(store, "load_active_conversation", return_value=conv2):
            out = attachments.with_pictures(msgs, 1, see=True)
        self.assertEqual(len(images(out[1])), 1)

    def test_same_number_in_two_tabs_stays_apart(self):
        conv2 = store.create_conversation()
        a, b = self.upload(conv=1), self.upload(conv=conv2)
        attachments.claim([a["id"]], self.user_msg("one", conv=1), 1)
        attachments.save_caption(a["id"], "a sunset")
        attachments.claim([b["id"]], self.user_msg("two", conv=conv2), conv2)
        attachments.save_caption(b["id"], "a cat")
        newest = self.user_msg("what was in picture 1?", conv=conv2)
        got = attachments.referenced("what was in picture 1?", conv2, newest)
        self.assertEqual([r["id"] for r in got], [b["id"]])
        self.assertIn("a cat", attachments.picture_index_message(conv2)["content"])
        self.assertNotIn("a sunset", attachments.picture_index_message(conv2)["content"])


class TitleAndCloudTests(TempStore):
    def test_picture_only_first_message_names_the_chat(self):
        conv = store.create_conversation()
        a = self.upload(conv=conv)
        uid = self.user_msg("", conv=conv)
        attachments.claim([a["id"]], uid, conv)
        attachments.save_caption(a["id"], "A sunset over a snowy mountain lake with trees and reflections")
        title = attachments.title_from_picture(conv, uid, "", [attachments.get(a["id"])])
        self.assertEqual(title, "Picture: A sunset over a snowy mountain")
        titles = {c["id"]: c["title"] for c in store.list_conversations()}
        self.assertEqual(titles[conv], title)

    def test_her_reaction_first_still_names_the_chat(self):
        conv = store.create_conversation()
        store.append_greeting_if_quiet(conv, "Show me!", "見せて！", None, kind=store.GREETING_KIND_ATTACH)
        a = self.upload(conv=conv)
        uid = self.user_msg("", conv=conv)
        attachments.claim([a["id"]], uid, conv)
        attachments.save_caption(a["id"], "a cat in a field")
        self.assertEqual(attachments.title_from_picture(conv, uid, "", [attachments.get(a["id"])]),
                         "Picture: a cat in a field")

    def test_title_left_alone_otherwise(self):
        conv = store.create_conversation()
        a = self.upload(conv=conv)
        uid = self.user_msg("words first", conv=conv)
        attachments.claim([a["id"]], uid, conv)
        attachments.save_caption(a["id"], "a cat")
        row = attachments.get(a["id"])
        self.assertEqual(attachments.title_from_picture(conv, uid, "words first", [row]), "")
        conv2 = store.create_conversation()
        self.user_msg("hello", conv=conv2)
        uid2 = self.user_msg("", conv=conv2)       # not the first message
        self.assertEqual(attachments.title_from_picture(conv2, uid2, "", [row]), "")

    # --- L2 (2026-10-27): deleting the picture that named the chat
    def test_deleting_the_naming_picture_gives_back_new_chat(self):
        conv = store.create_conversation()
        a = self.upload(conv=conv)
        uid = self.user_msg("", conv=conv)
        attachments.claim([a["id"]], uid, conv)
        attachments.save_caption(a["id"], "a cat in a field")
        attachments.title_from_picture(conv, uid, "", [attachments.get(a["id"])])
        client = api.application.test_client()
        with patch.object(api, "_unlink_voice", lambda mid: None):
            self.assertEqual(client.delete(f"/memory/{uid}").status_code, 200)
        titles = {c["id"]: c["title"] for c in store.list_conversations()}
        self.assertEqual(titles[conv], "New chat")

    def test_other_deletes_keep_the_title(self):
        conv = store.create_conversation()
        a = self.upload(conv=conv)
        uid = self.user_msg("", conv=conv)
        attachments.claim([a["id"]], uid, conv)
        attachments.save_caption(a["id"], "a cat")
        attachments.title_from_picture(conv, uid, "", [attachments.get(a["id"])])
        her = self.her_msg(conv=conv)
        self.assertIsNone(attachments.picture_titled_chat(her))      # her reply: title stays
        conv2 = store.create_conversation()
        u2 = self.user_msg("hello", conv=conv2)
        store.rename_conversation(conv2, "Picture: my own name")     # renamed by hand, words
        self.assertIsNone(attachments.picture_titled_chat(u2))

    def test_cloud_flag(self):
        with patch.object(attachments, "_brain", return_value=("https://openrouter.ai/api/v1", "m")):
            self.assertTrue(attachments._cloud_brain())
        with patch.object(attachments, "_brain", return_value=("http://127.0.0.1:8091/v1", "m")):
            self.assertFalse(attachments._cloud_brain())


# ---------------------------------------------------------------- her picture list

class PictureIndexTests(TempStore):
    def test_none_without_pictures(self):
        self.user_msg("hi")
        self.assertIsNone(attachments.picture_index_message(1))

    def test_list_lines(self):
        a, b = self.upload(), self.upload()
        attachments.claim([a["id"]], self.user_msg("one"), 1)
        attachments.save_caption(a["id"], "a sunset over a lake")
        self.her_msg()
        attachments.claim([b["id"]], self.user_msg("two"), 1)
        attachments.remove(a["id"])
        conn = sqlite3.connect(self.db)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d 10:00:00")
        conn.execute("UPDATE attachments SET created_at = ? WHERE id = ?", (yesterday, a["id"]))
        conn.commit()
        conn.close()
        text = attachments.picture_index_message(1)["content"]
        self.assertIn("- Picture 1 (turn 1, yesterday): a sunset over a lake", text)
        self.assertIn("- Picture 2 (turn 2, just now, on your desk)", text)
        self.assertIn("ask (by its number)", text)

    def test_long_list_is_cut(self):
        rows = [self.upload() for _ in range(attachments.INDEX_MAX + 3)]
        attachments.claim([r["id"] for r in rows], self.user_msg("many"), 1)
        text = attachments.picture_index_message(1)["content"]
        self.assertIn("3 older not listed", text)
        self.assertNotIn("- Picture 3 (", text)
        self.assertIn(f"- Picture {attachments.INDEX_MAX + 3} (", text)


# ---------------------------------------------------------------- "look again"

class ReferenceTests(TempStore):
    def setUp(self):
        super().setUp()
        self.rows = []
        for caption in ("a sunset over a lake", "a cat on a sofa", "a screenshot of a chat app"):
            r = self.upload()
            attachments.claim([r["id"]], self.user_msg("look"), 1)
            attachments.save_caption(r["id"], caption)
            attachments.remove(r["id"])            # off her desk: only a caption left
            self.her_msg()
            self.rows.append(attachments.get(r["id"]))
        self.newest = self.user_msg("placeholder")

    def ref(self, text, now=None):
        return [r["chat_no"] for r in attachments.referenced(text, 1, self.newest, now)]

    def test_by_number(self):
        self.assertEqual(self.ref("what was in picture 2?"), [2])
        self.assertEqual(self.ref("compare photo #1 and pic 3"), [1, 3])
        self.assertEqual(self.ref("写真2をもう一回見て"), [2])
        self.assertEqual(self.ref("2枚目の猫"), [2])
        self.assertEqual(self.ref("三枚目は？"), [3])
        self.assertEqual(self.ref("picture 9 please"), [])          # no such picture

    def test_first_and_last(self):
        self.assertEqual(self.ref("the first photo was nicer"), [1])
        self.assertEqual(self.ref("the second picture"), [2])
        self.assertEqual(self.ref("look at the last picture again"), [3])
        self.assertEqual(self.ref("さっきの写真、もう一度見て"), [3])
        self.assertEqual(self.ref("最初の写真は？"), [1])

    def test_by_day(self):
        conn = sqlite3.connect(self.db)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d 09:00:00")
        conn.execute("UPDATE attachments SET created_at = ? WHERE id = ?", (yesterday, self.rows[1]["id"]))
        conn.commit()
        conn.close()
        self.assertEqual(self.ref("remember yesterday's photo?"), [2])
        self.assertEqual(self.ref("昨日の写真覚えてる？"), [2])

    def test_by_caption_words(self):
        self.assertEqual(self.ref("what colour was the sunset in that photo?"), [1])
        self.assertEqual(self.ref("the cat picture"), [2])

    def test_unclear_shows_nothing(self):
        self.assertEqual(self.ref("that photo was nice"), [])        # which one? she can ask
        self.assertEqual(self.ref("how was your day?"), [])
        self.assertEqual(self.ref("the a picture"), [])              # only stop words
        # 2026-11-06: "image" and "cat" in the message, but not together -
        # it is about AR glasses, not the cat photo.
        self.assertEqual(self.ref("The glasses sent an image to process. My cat would love "
                                  "them"), [])
        self.assertEqual(self.ref("it sent an image for it to process and the cat sat there"), [])

    def test_at_most_two(self):
        self.assertEqual(len(attachments.referenced("pictures 1, picture 2 and picture 3", 1,
                                                    self.newest)), 2)

    def test_shown_again_in_newest_message(self):
        msgs = prompt({"role": "user", "content": "what was in picture 2?"})
        with patch.object(attachments, "_newest_user", return_value=(self.newest, "what was in picture 2?")):
            out = attachments.with_pictures(msgs, 1, see=True)
            self.assertEqual(store.text_context_budget(1), 15500)    # its room is kept free
        parts = out[1]["content"]
        self.assertEqual(parts[1]["text"],
                         "[Picture 2 (turn 2) from the user" + attachments.PICTURE_AGAIN_NOTE
                         + ": a cat on a sofa]")
        self.assertEqual(len(images(out[1])), 1)
        self.assertTrue(out[-1]["content"].endswith(attachments.PICTURE_RULE))

    def test_desk_picture_not_shown_twice(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE attachments SET on_desk = 1 WHERE id = ?", (self.rows[1]["id"],))
        conn.commit()
        conn.close()
        own, desk, again, _ = attachments._picture_plan(1, "what was in picture 2?")
        self.assertEqual([r["chat_no"] for r in desk], [2])
        self.assertEqual(again, [])


# ---------------------------------------------------------------- no picture came (F1)

class MissingPictureTests(TempStore):
    def note(self, text):
        return attachments.missing_picture_note(text, 1, False)

    def test_after_her_reaction(self):
        store.append_greeting_if_quiet(1, "What are you showing me?", "なに見せてくれるの？", None,
                                       kind=store.GREETING_KIND_ATTACH)
        self.assertEqual(self.note("this is my new desk"), attachments.NO_PICTURE_NOTE)
        self.assertIsNone(self.note("never mind, how are you?"))

    def test_card_still_waiting(self):
        self.upload()
        self.assertEqual(self.note("これが新しい机まわり。"), attachments.NOT_SENT_NOTE)

    def test_talks_as_if_showing_with_no_pictures_ever(self):
        self.assertEqual(self.note("look at this photo!"), attachments.NO_PICTURE_NOTE)
        self.assertIsNone(self.note("I took a photo yesterday"))   # telling, not showing
        self.assertIsNone(self.note("look at this idea"))          # no picture word

    def test_note_in_her_prompt(self):
        self.upload()
        self.user_msg("見て、この写真")
        out = attachments.with_pictures(prompt({"role": "user", "content": "見て、この写真"}), 1, see=True)
        self.assertEqual(out[1]["content"], "見て、この写真")              # her message as it is
        self.assertTrue(out[-1]["content"].endswith(attachments.NOT_SENT_NOTE))
        # no RIGHT NOW note (a cloud model): the note follows her words
        out = attachments.with_pictures(prompt({"role": "user", "content": "見て、この写真"},
                                               right_now=False), 1, see=True)
        self.assertIn(attachments.NOT_SENT_NOTE, out[1]["content"])
        self.assertEqual(chat._content_text(out[1]["content"]), "見て、この写真")

    def test_note_with_desk_pictures_shown(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg("old"), 1)
        self.her_msg()
        store.append_greeting_if_quiet(1, "Another one?", "また？", None, kind=store.GREETING_KIND_ATTACH)
        self.user_msg("this is my new desk")
        history = [{"role": "user", "content": "old [Picture 1 (turn 1) from the user]"},
                   {"role": "assistant", "content": "ok"},
                   {"role": "user", "content": "this is my new desk"}]
        out = attachments.with_pictures(prompt(*history), 1, see=True)
        self.assertTrue(out[-1]["content"].endswith(attachments.DESK_RULE + "\n" + attachments.NO_PICTURE_NOTE))

    def test_ordinary_message_untouched(self):
        self.user_msg("hello")
        msgs = prompt({"role": "user", "content": "hello"})
        self.assertIs(attachments.with_pictures(msgs, 1, see=True), msgs)


# ---------------------------------------------------------------- picture-aware search (F2)

class PictureSearchTests(TempStore):
    def run_loop(self, content):
        seen = {"searched": [], "convo": None}
        reply = SimpleNamespace(content="", tool_calls=[{"name": "AmadeusPack", "args": {
            "assistant_reply_JPS": "アプリね。", "assistant_reply_ENG": "An app."}}])

        def forced(bound, convo):
            seen["convo"] = convo
            return reply
        llm = SimpleNamespace(bind_tools=lambda tools, tool_choice=None: llm)
        with patch.object(chat, "_web_triage_candidate", lambda *a: True), \
             patch.object(chat, "_web_triage", lambda *a: {"search": True, "topic": "identify the app"}), \
             patch.object(chat, "_web_search_with_content",
                          lambda q, **k: seen["searched"].append(q) or "results"), \
             patch.object(chat, "_invoke_forced", forced), \
             patch.object(chat, "_recent_lookups", lambda: []):
            chat._web_search_loop(llm, [{"role": "system", "content": "S"},
                                        {"role": "user", "content": content}])
        return seen

    def test_picture_skips_the_words_only_search(self):
        seen = self.run_loop([{"type": "text", "text": "このスクショのアプリ、調べてみて"},
                              {"type": "image_url", "image_url": {"url": "data:x"}}])
        self.assertEqual(seen["searched"], [])
        notes = [m["content"] for m in seen["convo"] if isinstance(m["content"], str)]
        self.assertTrue(any("what you actually SEE" in n for n in notes))
        self.assertTrue(attachments.has_pictures(seen["convo"]))

    def test_words_only_message_keeps_the_quick_search(self):
        seen = self.run_loop("search for the Amadeus app")
        self.assertEqual(seen["searched"], ["identify the app"])


# ---------------------------------------------------------------- capability

class CapabilityTests(TempStore):
    def _resp(self, payload, ok=True):
        return SimpleNamespace(ok=ok, json=lambda: payload)

    def test_llama_server_props(self):
        with patch.object(attachments, "_brain", return_value=("http://127.0.0.1:8091/v1", "m")), \
             patch.object(attachments.requests, "get",
                          return_value=self._resp({"modalities": {"vision": True}})) as get:
            self.assertTrue(attachments.can_see_pictures())
            self.assertTrue(attachments.can_see_pictures())       # cached
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args[0][0], "http://127.0.0.1:8091/props")

    def test_openrouter_models(self):
        data = {"data": [{"id": "a/text", "architecture": {"input_modalities": ["text"]}},
                         {"id": "b/vision", "architecture": {"input_modalities": ["text", "image"]}}]}
        with patch.object(attachments.requests, "get", return_value=self._resp(data)):
            self.assertTrue(attachments._probe("https://openrouter.ai/api/v1", "b/vision"))
            self.assertFalse(attachments._probe("https://openrouter.ai/api/v1", "a/text"))
            self.assertIsNone(attachments._probe("https://openrouter.ai/api/v1", "c/unknown"))

    def test_unknown_server(self):
        with patch.object(attachments.requests, "get", side_effect=OSError("no")):
            self.assertIsNone(attachments._probe("http://192.168.0.9:8080/v1", "m"))

    def test_rejection_marks_cannot_see(self):
        with patch.object(attachments, "_brain", return_value=("http://x/v1", "m")), \
             patch.object(attachments, "_probe", return_value=None):
            self.assertIsNone(attachments.can_see_pictures())
            attachments.mark_cannot_see()
            self.assertFalse(attachments.can_see_pictures())

    def test_is_image_rejection(self):
        self.assertTrue(attachments.is_image_rejection(
            Exception("Error code: 400 - image input is not supported by this model")))
        self.assertTrue(attachments.is_image_rejection(Exception("mmproj not loaded, cannot use image_url")))
        self.assertFalse(attachments.is_image_rejection(Exception("Connection refused")))
        self.assertFalse(attachments.is_image_rejection(Exception("400 invalid temperature")))

    def test_capabilities_answer(self):
        with patch.object(attachments, "can_see_pictures", return_value=False):
            caps = attachments.capabilities(1)
        self.assertIs(caps["pictures"], False)
        self.assertIn("Settings", caps["reason"])
        self.assertEqual(caps["max_mb"], 20)
        self.assertTrue(caps["documents"])          # documents need no vision (Phase 4)
        self.assertGreater(caps["doc_pages"], 0)


# ---------------------------------------------------------------- the reply flow

class FakeLLM:
    """Records every prompt; structured calls answer a pack."""

    def __init__(self, fail_first=None):
        self.prompts = []
        self.fail_first = fail_first
        self.plain = []

    def with_structured_output(self, schema, method=None):
        llm = self

        class S:
            def invoke(self, messages):
                llm.prompts.append(messages)
                if llm.fail_first is not None:
                    exc, llm.fail_first = llm.fail_first, None
                    raise exc
                if schema is AmadeusReply:
                    return AmadeusReply(assistant_reply_JPS="かわいい猫ね。")
                return AmadeusPack(assistant_reply_JPS="かわいい猫ね。", assistant_reply_ENG="What a cute cat.")
        return S()

    def bind_tools(self, tools, tool_choice=None):
        llm = self

        class B:
            def invoke(self, messages):
                llm.prompts.append(messages)
                raise RuntimeError("no forced calls in this test")
        return B()

    def invoke(self, messages):
        self.plain.append(messages)
        return SimpleNamespace(content="A small red square on a plain background.")


class ReplyFlowTests(TempStore):
    def setUp(self):
        super().setUp()
        self.fake = FakeLLM()
        fake_ja = SimpleNamespace(build_closeness_context=lambda t: {"role": "system", "content": "CLOSE"},
                                  build_voice_rules=lambda: {"role": "system", "content": "VOICE"})
        self.saved = []
        more = [patch.object(chat, "get_llm", lambda *a, **k: self.fake),
                patch.object(chat, "reset_llm", lambda: None),
                patch.object(chat, "ja_voice", fake_ja),
                patch.object(chat, "stats", SimpleNamespace(load_stat=lambda k: 50.0)),
                patch.object(chat, "_is_local", lambda c: False),
                patch.object(chat, "_right_now_note", lambda: False),
                patch.object(chat, "maybe_schedule_trust_rescore", lambda: None),
                patch.object(store, "load_default_personality_messages",
                             return_value=[{"role": "system", "content": "PERSONA"}]),
                patch.object(store, "load_web_access", return_value=False),
                patch.object(store, "maybe_roll_summary", lambda *a: None),
                patch.object(cm_bridge, "fetch_memory_block", lambda *a: None),
                patch.object(cm_bridge, "save_turn", lambda conv, role, text: self.saved.append((role, text))),
                patch.object(attachments, "can_see_pictures", return_value=True)]
        for p in more:
            p.start()
        self._patches += more

    def wait_threads(self):
        import threading
        for t in threading.enumerate():
            if t.name in ("picture-caption", "voice-first-en"):
                t.join(timeout=5)

    def test_single_call_reply_sees_the_picture(self):
        a = self.upload()
        pack, uid, aid, conv = chat.getOutputPacked("what is this?", [a["id"]])
        self.wait_threads()
        last_user = [m for m in self.fake.prompts[0] if m["role"] == "user"][-1]
        self.assertEqual(len(images(last_user)), 1)
        self.assertEqual(attachments.get(a["id"])["message_id"], uid)
        # caption written in the background, then her memory gets both lines
        self.assertEqual(attachments.get(a["id"])["caption"], "A small red square on a plain background.")
        self.assertEqual(self.saved[0][0], "user")
        self.assertIn("A small red square", self.saved[0][1])
        self.assertEqual(self.saved[1], ("assistant", "かわいい猫ね。"))

    def test_deleted_meanwhile_never_reaches_long_term_memory(self):
        # L3 (2026-10-27): the caption takes a few seconds; a message deleted
        # in that time must not be sent to her long-term memory afterwards.
        a = self.upload()
        uid = self.user_msg("look")
        attachments.claim([a["id"]], uid, 1)
        her = self.her_msg()
        real_caption = attachments.caption_message

        def caption_then_delete(client, message_id):
            rows = real_caption(client, message_id)
            store.delete_message(message_id)                  # deleted while captioning
            return rows
        with patch.object(attachments, "caption_message", caption_then_delete):
            chat._after_picture_turn(1, uid, "look", "かわいい", her)
            self.wait_threads()
        self.assertEqual(self.saved, [])

    def test_her_reply_undone_meanwhile_is_not_saved(self):
        a = self.upload()
        uid = self.user_msg("look")
        attachments.claim([a["id"]], uid, 1)
        her = self.her_msg()
        store.delete_message(her)                             # undone before the caption
        chat._after_picture_turn(1, uid, "look", "かわいい", her)
        self.wait_threads()
        self.assertEqual([r for r, _ in self.saved], ["user"])

    def test_voice_first_reply_sees_the_picture(self):
        a = self.upload()
        turn = chat.getOutputPackedVoiceFirst("", [a["id"]])
        turn.wait_en(timeout=5)
        self.wait_threads()
        last_user = [m for m in self.fake.prompts[0] if m["role"] == "user"][-1]
        self.assertEqual(len(images(last_user)), 1)
        self.assertEqual(turn.pack.assistant_reply_JPS, "かわいい猫ね。")

    def test_helpers_get_text_only(self):
        a = self.upload()
        chat.getOutputPacked("look", [a["id"]])
        self.wait_threads()
        # caption call: the only plain call with a picture
        caption_calls = [m for m in self.fake.plain if isinstance(m[0]["content"], list)]
        self.assertEqual(len(caption_calls), 1)

    def test_next_turn_sees_label_and_desk(self):
        a = self.upload()
        chat.getOutputPacked("my cat", [a["id"]])
        self.wait_threads()
        self.fake.prompts.clear()
        chat.getOutputPacked("what should I feed her?")
        self.wait_threads()
        users = [m for m in self.fake.prompts[0] if m["role"] == "user"]
        self.assertEqual([p.get("text") for p in users[0]["content"]],
                         ["my cat", "[Picture 1 (turn 1) from the user]", None])   # still on her desk
        self.assertEqual(chat._content_text(users[-1]["content"]), "what should I feed her?")
        # her picture list rides in the changing part
        system = " ".join(m["content"] for m in self.fake.prompts[0] if m["role"] == "system")
        self.assertIn("PICTURES THE USER SHOWED YOU", system)
        self.assertIn("- Picture 1 (turn 1, just now, on your desk): A small red square", system)

    def test_failure_returns_cards_to_tray(self):
        a = self.upload()
        self.fake.fail_first = RuntimeError("server down")
        with patch.object(chat, "_invoke_forced", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                chat.getOutputPacked("look", [a["id"]])
        self.assertEqual([r["id"] for r in attachments.pending([a["id"]])], [a["id"]])
        self.assertEqual(store.load_memory_raw(1), [])

    def test_image_rejection_answers_without_pictures(self):
        a = self.upload()
        self.fake.fail_first = Exception("Error code: 400 - this model does not support image input")
        with patch.object(attachments, "can_see_pictures", side_effect=[True, False]), \
             patch.object(attachments, "mark_cannot_see") as mark, \
             patch.object(chat, "_invoke_forced", side_effect=Exception(
                 "Error code: 400 - this model does not support image input")):
            pack, uid, aid, conv = chat.getOutputPacked("look", [a["id"]])
        self.wait_threads()
        mark.assert_called_once()
        retry_user = [m for m in self.fake.prompts[-1] if m["role"] == "user"][-1]
        self.assertIsInstance(retry_user["content"], str)
        self.assertIn("cannot see", retry_user["content"])

    def test_regenerate_resends_same_pictures(self):
        a = self.upload()
        chat.getOutputPacked("look", [a["id"]])
        self.wait_threads()
        self.fake.prompts.clear()
        chat.regenerateReply()
        last_user = [m for m in self.fake.prompts[0] if m["role"] == "user"][-1]
        self.assertEqual(len(images(last_user)), 1)

    def test_web_on_decision_sees_picture_and_triage_text(self):
        # Web ON: the search decision (tool loop) gets the picture; the cheap
        # prefilter on a picture-only message makes no triage call.
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg(""), 1)
        seen = {}

        def loop(llm, messages):
            seen["messages"] = messages
            return AmadeusPack(assistant_reply_JPS="猫ね。", assistant_reply_ENG="A cat.")
        with patch.object(store, "load_web_access", return_value=True), \
             patch.object(chat, "_getResponsePackedWithWebSearch", loop), \
             patch.object(chat, "_lookup_index_message", lambda *a: None):
            chat.getResponsePacked(store.build_prompt_messages())
        last_user = [m for m in seen["messages"] if m["role"] == "user"][-1]
        self.assertEqual(len(images(last_user)), 1)
        self.assertFalse(chat._web_triage_candidate(chat._content_text(last_user["content"]),
                                                    seen["messages"]))

    def test_explicit_search_read_from_parts(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "search for this anime"},
            {"type": "image_url", "image_url": {"url": "data:x"}}]}]
        self.assertEqual(chat._user_explicitly_asks_for_search(msgs), "search for this anime")
        self.assertEqual(chat._find_prior_search_query(msgs), "this anime")

    def test_triage_sees_words_not_pictures(self):
        captured = {}

        class T:
            def bind_tools(self, tools, tool_choice=None):
                return self

            def invoke(self, convo):
                captured["convo"] = convo
                raise RuntimeError("stop here")
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "is it raining there?"},
            {"type": "image_url", "image_url": {"url": "data:x"}}]}]
        with patch.object(chat, "_invoke_forced", lambda llm, convo: T().invoke(convo)):
            chat._web_triage(T(), msgs)
        user_lines = [m["content"] for m in captured["convo"] if m["role"] == "user"]
        self.assertIn("is it raining there?", user_lines)
        self.assertTrue(all(isinstance(c, str) for c in user_lines))


# ---------------------------------------------------------------- send route

class SendRouteTests(TempStore):
    def setUp(self):
        super().setUp()
        self.client = api.application.test_client()
        more = [patch.object(api, "has_api_key", return_value=True)]
        for p in more:
            p.start()
        self._patches += more

    def test_upload_route(self):
        data = {"file": [(io.BytesIO(picture()), "cat.jpg"), (io.BytesIO(b"%PDF"), "a.pdf"),
                         (io.BytesIO(b"x"), "b.heic")], "conversation_id": "1"}
        r = self.client.post("/attachments", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        items = r.get_json()["items"]
        self.assertTrue(items[0]["ok"])
        self.assertEqual((items[1]["ok"], items[1]["error"]), (False, "doc_broken"))
        self.assertEqual(items[2]["error"], "heic")
        thumb = self.client.get(items[0]["thumb_url"])
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.data[:2], b"\xff\xd8")
        thumb.close()

    def test_upload_needs_a_file(self):
        self.assertEqual(self.client.post("/attachments", data={}).status_code, 400)

    def test_send_refuses_gone_cards(self):
        r = self.client.post("/", json={"user_input": "hi", "attachment_ids": [12345]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no longer available", r.get_json()["message"])

    def test_send_refuses_bad_list_and_empty(self):
        r = self.client.post("/", json={"user_input": "", "attachment_ids": ["1"]})
        self.assertEqual(r.status_code, 400)

    def test_send_refuses_what_does_not_fit(self):
        with patch.object(store, "effective_context_budget", return_value=2000):
            ids = [self.upload()["id"] for _ in range(2)]       # (2000-1500)//500 = 1
            r = self.client.post("/", json={"user_input": "", "attachment_ids": ids})
        self.assertEqual(r.status_code, 400)
        self.assertIn("about 1", r.get_json()["message"])

    def test_send_passes_ids(self):
        a = self.upload()
        seen = {}

        def voice_first(text, ids=None):
            seen["args"] = (text, ids)
            raise RuntimeError("stop")
        with patch.object(chat, "getOutputPackedVoiceFirst", voice_first), \
             patch.object(chat.store, "load_web_access", return_value=False):
            r = self.client.post("/", json={"user_input": "", "attachment_ids": [a["id"]]})
        self.assertEqual(seen["args"], ("", [a["id"]]))
        self.assertEqual(r.status_code, 502)

    def test_delete_route(self):
        a = self.upload()
        self.assertEqual(self.client.delete(f"/attachments/{a['id']}").get_json()["result"], "deleted")
        self.assertEqual(self.client.delete(f"/attachments/{a['id']}").status_code, 404)

    def test_desk_and_capabilities_routes(self):
        a = self.upload()
        attachments.claim([a["id"]], self.user_msg(), 1)
        desk = self.client.get("/attachments/desk?conversation_id=1").get_json()["items"]
        self.assertEqual([d["id"] for d in desk], [a["id"]])
        with patch.object(attachments, "can_see_pictures", return_value=True):
            caps = self.client.get("/attachments/capabilities").get_json()
        self.assertIs(caps["pictures"], True)

    def test_reaction_setting_routes(self):
        self.assertEqual(self.client.get("/attachments/reactionSetting").get_json()["value"], "always")
        r = self.client.post("/attachments/reactionSetting", json={"value": "once"})
        self.assertEqual(r.get_json()["value"], "once")
        self.assertEqual(attachments.load_reaction_setting(), "once")
        self.assertEqual(self.client.post("/attachments/reactionSetting",
                                          json={"value": "loud"}).status_code, 400)


# ---------------------------------------------------------------- attach reaction

class ReactionTests(TempStore):
    def test_event_line_type_and_name_only(self):
        rows = [{"name": "my thesis draft.png"}]
        line = attachments.reaction_event(rows, "")
        self.assertIn('a picture named "my thesis draft"', line)
        self.assertIn("NOT sent yet", line)
        self.assertIn("not mentioned before", line)
        line = attachments.reaction_event(rows, "remember my thesis draft?")
        self.assertNotIn("not mentioned before", line)

    def test_camera_names_ignored(self):
        for name in ("IMG_20261026_101112.jpg", "DSC01234.JPG", "PXL_20261026_123456789.jpg",
                     "Screenshot 2026-10-26 at 10.11.12 PM.png", "1698311111.png",
                     "3f2a9c1e-77b0-4c2e-9f5e-0a1b2c3d4e5f.jpg", "photo.jpg", "image (3).png"):
            self.assertEqual(attachments.meaningful_name(name), "", name)
        self.assertEqual(attachments.meaningful_name("kurisu fanart.png"), "kurisu fanart")
        line = attachments.reaction_event([{"name": "IMG_0001.jpg"}, {"name": "cat.jpg"}])
        self.assertIn('2 pictures (a picture; a picture named "cat")', line)

    def test_frequency_choices(self):
        self.assertTrue(attachments.should_react(1))
        attachments.save_reaction_setting("off")
        self.assertFalse(attachments.should_react(1))
        attachments.save_reaction_setting("quiet")
        self.assertTrue(attachments.should_react(1))
        attachments.note_reaction(1)
        self.assertFalse(attachments.should_react(1))
        self.assertTrue(attachments.should_react(2))
        attachments.save_reaction_setting("once")
        self.assertFalse(attachments.should_react(1))
        self.assertTrue(attachments.should_react(3))

    def _react(self, ids, **kw):
        fake = FakeLLM()
        fake_ja = SimpleNamespace(build_closeness_context=lambda t: {"role": "system", "content": "CLOSE"},
                                  build_voice_rules=lambda: {"role": "system", "content": "VOICE"})
        with patch.object(chat, "get_llm", lambda *a, **k: fake), \
             patch.object(chat, "ja_voice", fake_ja), \
             patch.object(chat, "stats", SimpleNamespace(load_stat=lambda k: 50.0)), \
             patch.object(chat, "_is_local", lambda c: False), \
             patch.object(chat, "_right_now_note", lambda: False), \
             patch.object(store, "load_default_personality_messages",
                          return_value=[{"role": "system", "content": "PERSONA"}]):
            return chat.generate_attach_reaction(ids, 1), fake

    def test_reaction_stored_as_her_own_line(self):
        a = self.upload(name="my new desk.jpg")
        (pack, aid, conv), fake = self._react([a["id"]])
        self.assertEqual(pack.assistant_reply_ENG, "What a cute cat.")
        row = store.load_greeting_row(aid)
        self.assertEqual(row["kind"], store.GREETING_KIND_ATTACH)
        event = [m for m in fake.prompts[0] if m["role"] == "user"][-1]["content"]
        self.assertIn('"my new desk"', event)
        self.assertFalse(attachments.has_pictures(fake.prompts[0]))   # she has NOT seen it
        items = store.load_memory_raw(1)
        self.assertFalse(items[-1]["greeting_regenerable"])
        with self.assertRaises(ValueError):
            chat.regenerate_greeting(aid)
        # never an "unanswered greeting" / a repeat greeting
        self.assertFalse(store.load_last_assistant_line(1)["is_greeting"])
        self.assertEqual(store.greeting_row_ids(1), [])

    def test_reaction_dropped_when_user_sent_first(self):
        a = self.upload()
        real = store.latest_user_message_id
        with patch.object(store, "latest_user_message_id", lambda conv: real(conv) - 1):
            self.user_msg("sent already")
            with self.assertRaises(chat.GreetingSuperseded):
                self._react([a["id"]])

    def test_reaction_needs_waiting_cards(self):
        with self.assertRaises(ValueError):
            self._react([999])

    # --- L1 (MH 2026-10-27): taking a card back after her reaction
    def test_withdraw_after_her_reaction(self):
        a = self.upload(name="my new desk.jpg")
        self._react([a["id"]])
        attachments.remove(a["id"])
        self.assertTrue(attachments.may_comment_on_withdraw(1))
        fake = FakeLLM()
        fake_ja = SimpleNamespace(build_closeness_context=lambda t: {"role": "system", "content": "CLOSE"},
                                  build_voice_rules=lambda: {"role": "system", "content": "VOICE"})
        with patch.object(chat, "get_llm", lambda *a, **k: fake), \
             patch.object(chat, "ja_voice", fake_ja), \
             patch.object(chat, "stats", SimpleNamespace(load_stat=lambda k: 50.0)), \
             patch.object(chat, "_is_local", lambda c: False), \
             patch.object(chat, "_right_now_note", lambda: False), \
             patch.object(store, "load_default_personality_messages",
                          return_value=[{"role": "system", "content": "PERSONA"}]):
            pack, aid, conv = chat.generate_withdraw_reaction(a, 1)
        event = [m for m in fake.prompts[0] if m["role"] == "user"][-1]["content"]
        self.assertIn('removed the picture named "my new desk"', event)
        system = " ".join(m["content"] for m in fake.prompts[0] if m["role"] == "system")
        self.assertIn("took it back before sending", system)
        self.assertEqual(store.load_greeting_row(aid)["kind"], store.GREETING_KIND_ATTACH)
        lines = [m for m in store.load_memory_raw(1) if m["role"] == "assistant"]
        self.assertEqual(len(lines), 2)                          # her first line stays

    def test_withdraw_not_the_moment(self):
        a, b = self.upload(), self.upload()
        self.assertFalse(attachments.may_comment_on_withdraw(1))   # she never reacted
        self._react([a["id"], b["id"]])
        attachments.remove(a["id"])
        self.assertFalse(attachments.may_comment_on_withdraw(1))   # another card still waits
        attachments.remove(b["id"])
        self.assertTrue(attachments.may_comment_on_withdraw(1))
        attachments.save_reaction_setting("off")
        self.assertFalse(attachments.may_comment_on_withdraw(1))
        attachments.save_reaction_setting("always")
        self.user_msg("never mind")                                # you spoke since
        self.assertFalse(attachments.may_comment_on_withdraw(1))

    def test_withdraw_route(self):
        a = self.upload()
        self._react([a["id"]])
        client = api.application.test_client()
        pack = AmadeusPack(assistant_reply_JPS="あれ、見せてくれないの？", assistant_reply_ENG="Huh, not showing me?")
        with patch.object(api, "has_api_key", return_value=True), \
             patch.object(chat, "generate_withdraw_reaction", return_value=(pack, 77, 1)) as gen:
            r = client.delete(f"/attachments/{a['id']}?react=1").get_json()
        self.assertEqual(r["result"], "deleted")
        self.assertEqual(r["reaction"]["response"], "Huh, not showing me?")
        self.assertEqual(gen.call_args[0][0]["id"], a["id"])
        b = self.upload()
        with patch.object(chat, "generate_withdraw_reaction") as gen:
            r = client.delete(f"/attachments/{b['id']}").get_json()     # no ?react=1
        gen.assert_not_called()
        self.assertNotIn("reaction", r)

    def test_reaction_quiet_setting(self):
        a = self.upload()
        attachments.save_reaction_setting("off")
        result, fake = self._react([a["id"]])
        self.assertIsNone(result)
        self.assertEqual(fake.prompts, [])


if __name__ == "__main__":
    unittest.main()

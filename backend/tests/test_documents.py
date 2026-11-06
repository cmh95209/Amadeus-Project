"""Documents the user gives her (attachments Phase 4, 2026-10-28).

Covers: reading PDF / Word / text files (and the refusals: scans, password,
old Office files, binary "text"), storage, the room check + cut (E2 a), the
numbering apart from pictures, her prompt (the text right after the label in
the message it came with, the same on the next turn, the rule E1 B), off her
desk (label + summary E5), asking about one again, helpers reading only the
user's words, the summary call, long-term memory line, the routes (text view
+ original, E4 a) and her reaction.
"""
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import docx
from pypdf import PdfReader, PdfWriter

import api
import attachments
import chat
import memory as store
from test_attachments import TempStore, images, picture, prompt


def text_pdf(*pages) -> bytes:
    """A small real PDF with one text line per page."""
    objs = ["<</Type/Catalog/Pages 2 0 R>>", None,
            "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<</Length {len(stream)}>>stream\n{stream}\nendstream")
        objs.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {len(objs)} 0 R"
                    "/Resources<</Font<</F1 3 0 R>>>>>>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<</Type/Pages/Kids[{' '.join(kids)}]/Count {len(kids)}>>"
    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def locked_pdf() -> bytes:
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(text_pdf("secret plans"))))
    writer.encrypt("pw")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def scanned_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.add_blank_page(612, 792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def word_file() -> bytes:
    d = docx.Document()
    d.add_paragraph("Trip plan for the weekend")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Day", "Place"
    table.cell(1, 0).text, table.cell(1, 1).text = "Saturday", "Akihabara"
    d.add_paragraph("Bring the camera.")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


LONG = "\n\n".join(f"Paragraph {n}. " + "The experiment went well again today. " * 12
                   for n in range(1, 120))    # ~16,000 tokens: longer than any room here


# ---------------------------------------------------------------- reading files

class ReadTests(unittest.TestCase):
    def test_text_utf8_and_japanese_codepage(self):
        p = attachments.prepare_document("notes.txt", "line one\r\nline two  \r\n".encode("utf-8"))
        self.assertEqual(p["text"], "line one\nline two")
        self.assertEqual(p["mime"], "text/plain")
        sjis = ("今日の実験メモ。結果はとても良かった。明日はもう一度測定する予定です。" * 4).encode("shift_jis")
        self.assertIn("実験メモ", attachments.prepare_document("memo.txt", sjis)["text"])

    def test_binary_named_txt_refused(self):
        with self.assertRaises(attachments.AttachmentError) as ctx:
            attachments.prepare_document("x.txt", b"\x00\x01\x02\x03" * 50)
        self.assertEqual(ctx.exception.code, "unsupported")

    def test_pdf_text_all_pages(self):
        p = attachments.prepare_document("report.pdf", text_pdf("Budget overview", "Second page"))
        self.assertIn("Budget overview", p["text"])
        self.assertIn("Second page", p["text"])
        self.assertEqual(p["mime"], "application/pdf")

    def test_scanned_and_locked_pdf_say_so(self):
        for data, code in ((scanned_pdf(), "scanned"), (locked_pdf(), "password"),
                           (b"%PDF-1.4 broken", "doc_broken")):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                attachments.prepare_document("a.pdf", data)
            self.assertEqual(ctx.exception.code, code)
        self.assertIn("scan", attachments.MESSAGES["scanned"])

    def test_word_paragraphs_and_table_in_order(self):
        text = attachments.prepare_document("trip.docx", word_file())["text"]
        self.assertLess(text.index("Trip plan"), text.index("Day | Place"))
        self.assertIn("Saturday | Akihabara", text)
        self.assertLess(text.index("Akihabara"), text.index("Bring the camera."))

    def test_empty_and_too_big(self):
        for data, code in ((b"", "empty"), (b"   \n\n ", "no_text"),
                           (b"x" * (attachments.MAX_FILE_BYTES + 1), "too_big")):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                attachments.prepare_document("a.txt", data)
            self.assertEqual(ctx.exception.code, code)

    def test_cut_text_ends_at_a_break(self):
        part = attachments.cut_text(LONG, 1000)
        self.assertLessEqual(store.estimate_tokens(part), 1000)
        self.assertGreater(store.estimate_tokens(part), 800)
        self.assertTrue(part.endswith("today."))
        self.assertEqual(attachments.cut_text("short", 1000), "short")


# ---------------------------------------------------------------- storage + room

class DocStore(TempStore):
    def setUp(self):
        super().setUp()
        # Reading notes never start a background worker in tests (it would call
        # a real model and could outlive this test's temporary database).
        self.queued = []
        more = [patch.object(attachments, "start_notes", side_effect=lambda row: (
            attachments._set_notes_state(row["id"], store.NOTES_WRITING), self.queued.append(row["id"])))]
        for p in more:
            p.start()
        self._patches += more

    def doc(self, text="Shopping list:\n- milk\n- bread", name="list.txt", conv=1):
        return attachments.save_upload(name, text.encode("utf-8"), conv)


class StoreTests(DocStore):

    def test_saved_with_its_text_and_original(self):
        row = self.doc()
        self.assertEqual(row["kind"], "document")
        self.assertEqual(sorted(self.files()), [f"1/{row['id']}.txt", f"1/{row['id']}_original.txt"])
        self.assertEqual(attachments.document_text(row), "Shopping list:\n- milk\n- bread")
        self.assertEqual(row["tokens"], store.estimate_tokens(attachments.document_text(row)))
        self.assertIsNone(row["thumb"])
        item = attachments.card(row, 1)
        self.assertEqual((item["pages"], item["read_pages"]), (1, 1))
        self.assertNotIn("warning", item)
        self.assertIsNone(item["thumb_url"])
        self.assertEqual(item["text_url"], f"/attachments/{row['id']}/text")

    def test_long_document_card_warns(self):
        row = self.doc(LONG, "thesis.txt")
        item = attachments.card(row, 1)
        self.assertGreater(item["pages"], item["read_pages"])
        self.assertIn(f"first {item['read_pages']} of {item['pages']} pages", item["warning"])

    def test_numbered_apart_from_pictures_and_cut_on_send(self):
        pic = self.upload()
        doc = self.doc(LONG, "thesis.txt")
        rows = attachments.claim([pic["id"], doc["id"]], self.user_msg("both"), 1)
        by_kind = {r["kind"]: r for r in rows}
        self.assertEqual((by_kind["picture"]["chat_no"], by_kind["document"]["chat_no"]), (1, 1))
        d = by_kind["document"]
        room = 16000 - 4000 - store.PICTURE_TOKENS              # budget - text floor - the picture
        self.assertLessEqual(d["read_tokens"], min(room, attachments.DOC_MAX_TOKENS))
        self.assertGreater(d["read_tokens"], 5000)
        self.assertEqual(len(attachments.read_part(d)), d["read_chars"])
        label = store.document_label(d)
        self.assertRegex(label, r'^\[Document 1 \(turn 1\) from the user: "thesis.txt", '
                                r'first \d+ of \d+ pages\]$')
        doc2 = self.doc()
        self.assertEqual(attachments.claim([doc2["id"]], self.user_msg("one more"), 1)[0]["chat_no"], 2)

    def test_room_counts_what_she_reads(self):
        before = attachments.room_tokens(1)
        doc = self.doc(LONG, "thesis.txt")
        attachments.claim([doc["id"]], self.user_msg("read"), 1)
        d = attachments.get(doc["id"])
        self.assertEqual(attachments.room_tokens(1), before - d["read_tokens"])
        self.assertEqual(store.picture_reserve_tokens(1), d["read_tokens"])
        self.assertFalse(attachments.fits([self.doc(LONG, "more.txt")["id"]], 1)[0]
                         and attachments.room_tokens(1, waiting=False) < attachments.DOC_MIN_TOKENS)

    def test_no_room_refused_on_send(self):
        with patch.object(store, "effective_context_budget", return_value=2000):
            big = self.doc(LONG, "a.txt")
            attachments.claim([big["id"]], self.user_msg("a"), 1)
            other = self.doc(LONG, "b.txt")
            self.assertFalse(attachments.fits([other["id"]], 1)[0])
            with patch.object(api, "has_api_key", return_value=True):
                r = api.application.test_client().post(
                    "/", json={"user_input": "and this", "attachment_ids": [other["id"]]})
            self.assertIn("no room", r.get_json().get("message", "").lower())

    def test_release_forgets_the_cut(self):
        doc = self.doc(LONG, "a.txt")
        mid = self.user_msg("a")
        attachments.claim([doc["id"]], mid, 1)
        attachments.release(mid)
        row = attachments.get(doc["id"])
        self.assertIsNone(row["read_chars"])
        self.assertIsNone(row["chat_no"])

    def test_deleting_the_message_removes_both_files(self):
        doc = self.doc()
        mid = self.user_msg("here")
        attachments.claim([doc["id"]], mid, 1)
        store.delete_message(mid)
        self.assertEqual(self.files(), [])


# ---------------------------------------------------------------- her prompt

class PromptTests(DocStore):
    def history(self):
        return [{"role": m["role"], "content": m["content"]} for m in store.load_memory_for_prompt(1)]

    def test_newest_document_own_message_before_the_words(self):
        # Her server resumes a prompt only up to where the newest user message
        # began: the text as its own message before the words is reused next turn.
        doc = self.doc()
        attachments.claim([doc["id"]], self.user_msg("what should I buy?"), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": "what should I buy?"}),
                                        1, see=True)
        label = '[Document 1 (turn 1) from the user: "list.txt", 1 page]'
        self.assertEqual([m["role"] for m in out], ["system", "user", "user", "system"])
        self.assertEqual(out[1]["content"], label + "\nShopping list:\n- milk\n- bread\n[End of document 1]")
        self.assertEqual(out[2]["content"], "what should I buy? " + label)
        self.assertTrue(out[-1]["content"].endswith(attachments.DOCUMENT_RULE))
        self.assertIn("6 sentences", attachments.DOCUMENT_RULE)

    def test_same_message_on_the_next_turn(self):
        doc, pic = self.doc(), self.upload()
        attachments.claim([doc["id"], pic["id"]], self.user_msg("  both  "), 1)
        turn_one = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        attachments.save_caption(doc["id"], "A short shopping list.")   # written after her reply
        self.her_msg()
        self.user_msg("anything else?")
        turn_two = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertEqual(turn_two[1], turn_one[1])          # the document message
        self.assertEqual(turn_two[2], turn_one[2])          # the words + label + picture
        self.assertIn("[End of document 1]", turn_two[1]["content"])
        self.assertEqual(len(images(turn_two[2])), 1)
        self.assertTrue(turn_two[-1]["content"].endswith(attachments.DOC_DESK_RULE))

    def test_off_desk_label_with_summary_only(self):
        doc = self.doc()
        attachments.claim([doc["id"]], self.user_msg("here"), 1)
        attachments.save_caption(doc["id"], "A short shopping list.")
        attachments.remove(doc["id"])
        self.her_msg()
        self.user_msg("thanks")
        out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertEqual(out[1]["content"],
                         'here [Document 1 (turn 1) from the user: "list.txt", 1 page: '
                         'A short shopping list.]')
        self.assertNotIn("milk", str(out))

    def test_asked_about_again(self):
        doc = self.doc(name="groceries.txt")
        attachments.claim([doc["id"]], self.user_msg("here"), 1)
        attachments.remove(doc["id"])
        self.her_msg()
        for text in ("what was in document 1?", "can you check the groceries file again?",
                     "さっきの資料をもう一度見て"):
            self.user_msg(text)
            out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
            self.assertIn("(shown to you again now)", out[-3]["content"], text)
            self.assertIn("milk", out[-3]["content"], text)
            self.assertEqual(out[-2]["content"], text)
            self.her_msg()
        self.user_msg("how was your day?")
        self.assertNotIn("milk", str(attachments.with_pictures(prompt(*self.history()), 1, see=True)))

    def test_works_when_her_model_cannot_see(self):
        doc = self.doc()
        attachments.claim([doc["id"]], self.user_msg("read this"), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": "read this"}), 1, see=False)
        self.assertIn("milk", out[1]["content"])
        self.assertTrue(out[2]["content"].startswith("read this"))

    def test_no_missing_picture_note_for_a_document(self):
        doc = self.doc()
        attachments.claim([doc["id"]], self.user_msg("look at this"), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": "look at this"}), 1, see=True)
        self.assertNotIn(attachments.NO_PICTURE_NOTE, str(out))

    def test_helpers_read_only_the_words(self):
        doc = self.doc()
        attachments.claim([doc["id"]], self.user_msg("what should I buy?"), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": "what should I buy?"}),
                                        1, see=True)
        self.assertEqual(chat._content_text(out[1]["content"]), "")          # the document
        self.assertEqual(chat._content_text(out[2]["content"]), "what should I buy?")
        self.assertEqual(chat._content_text(
            'ok [Document 2 (turn 3) from the user: "a.pdf", 4 pages: a report]'), "ok")
        self.assertEqual(chat._content_text("hi\n" + attachments.DOCUMENT_RULE), "hi")

    def test_her_list_names_documents(self):
        doc = self.doc()
        attachments.claim([doc["id"]], self.user_msg("here"), 1)
        attachments.save_caption(doc["id"], "A short shopping list.")
        index = attachments.picture_index_message(1)["content"]
        self.assertIn("DOCUMENTS THE USER GAVE YOU", index)
        self.assertIn('- Document 1 (turn 1, just now, on your desk): "list.txt", 1 page: '
                      "A short shopping list.", index)
        self.assertNotIn("PICTURES", index)


# ---------------------------------------------------------------- after her reply

class AfterReplyTests(DocStore):
    def test_summary_written_and_used(self):
        conv = store.create_conversation()
        doc = self.doc(conv=conv)
        mid = self.user_msg("", conv=conv)
        attachments.claim([doc["id"]], mid, conv)
        seen = []

        class LLM:
            def invoke(self, msgs):
                seen.append(msgs)
                return SimpleNamespace(content='"A shopping list: milk and bread."')

        with patch.object(attachments, "can_see_pictures", return_value=False):
            rows = attachments.caption_message(LLM(), mid)
        self.assertEqual(rows[0]["caption"], "A shopping list: milk and bread.")
        self.assertIn("milk", seen[0][0]["content"])
        self.assertEqual(attachments.labelled_line("", rows),
                         '[The user showed you a document "list.txt": A shopping list: milk and bread.]')
        self.assertTrue(attachments.title_from_picture(conv, mid, "", rows).startswith("Document: "))
        self.assertEqual(attachments.picture_titled_chat(mid), conv)

    def test_reaction_lines(self):
        doc = {"kind": "document", "name": "budget_2026.pdf"}
        pasted = {"kind": "document", "name": "Pasted text.txt"}
        self.assertIn('a document named "budget_2026"', attachments.reaction_event([doc]))
        self.assertIn("seen or read it", attachments.reaction_event([doc]))
        self.assertIn("a long pasted text", attachments.reaction_event([pasted]))
        self.assertIn("2 files", attachments.reaction_event([doc, {"kind": "picture", "name": "x.jpg"}]))
        self.assertIn("You never read it", attachments.withdraw_event(doc))


# ---------------------------------------------------------------- routes

class RouteTests(DocStore):
    def setUp(self):
        super().setUp()
        self.client = api.application.test_client()

    def test_upload_text_view_and_original(self):
        data = {"file": [(io.BytesIO(text_pdf("Budget overview")), "budget.pdf"),
                         (io.BytesIO(b"<script>alert(1)</script>"), "page.html"),
                         (io.BytesIO(b"x"), "old.doc")], "conversation_id": "1"}
        items = self.client.post("/attachments", data=data,
                                 content_type="multipart/form-data").get_json()["items"]
        self.assertTrue(items[0]["ok"] and items[1]["ok"])
        self.assertEqual(items[2]["error"], "later")
        view = self.client.get(items[0]["text_url"]).get_json()
        self.assertIn("Budget overview", view["text"])
        self.assertEqual(view["pages"], 1)
        pdf = self.client.get(items[0]["original_url"])
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        pdf.close()
        html = self.client.get(items[1]["original_url"])
        self.assertIn("attachment", html.headers["Content-Disposition"])   # downloaded, never run
        self.assertEqual(html.headers["X-Content-Type-Options"], "nosniff")
        html.close()
        plain = self.client.get(items[1]["url"])
        self.assertEqual(plain.mimetype, "text/plain")
        plain.close()
        self.assertEqual(self.client.get(f"/attachments/{items[0]['id']}/thumb").status_code, 404)

    def test_capabilities_say_documents(self):
        caps = self.client.get("/attachments/capabilities?conversation_id=1").get_json()
        self.assertTrue(caps["documents"])
        self.assertIn("PDF", caps["doc_formats"])


class CardRefreshTests(DocStore):
    def test_cards_follow_her_desk(self):
        # Live check 2026-10-28: the "no room" warning stayed after the desk was cleared.
        client = api.application.test_client()
        with patch.object(store, "effective_context_budget", return_value=6000):
            big = self.doc(LONG, "a.txt")
            attachments.claim([big["id"]], self.user_msg("a"), 1)
            waiting = self.doc(LONG, "b.txt")
            url = f"/attachments/cards?ids={waiting['id']},999&conversation_id=1"
            first = client.get(url).get_json()["items"]
            self.assertEqual([i["id"] for i in first], [waiting["id"]])
            self.assertIn("No room", first[0]["warning"])
            attachments.remove(big["id"])                        # off her desk
            again = client.get(url).get_json()["items"][0]
            self.assertIn("Long document: she will read the first", again["warning"])


# ---------------------------------------------------------------- long documents (Phase 4b)

FIELD_LOG = "\n\n".join(
    f"Day {d} field log. Temperature {10 + d % 17} C, seedlings counted: {d * 7 + 3}. Weather was "
    + ("rainy" if d % 3 == 0 else "clear") + ". "
    + "Notes: watered the east beds, checked the soil, wrote the numbers down. " * 3
    for d in range(1, 121))


class NoteLLM:
    """Writes a note naming the days a section covers; counts its calls."""

    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        import re
        text = messages[0]["content"]
        self.calls.append(text)
        days = re.findall(r"Day (\d+)", text)
        return SimpleNamespace(content=f"[Field log days {days[0]}-{days[-1]}]" if days else "Notes.")


class LongDocTests(DocStore):
    def long_doc(self, conv=1, budget=None):
        doc = self.doc(FIELD_LOG, "field_log.txt", conv=conv)
        mid = self.user_msg("This is my field log.", conv=conv)
        attachments.claim([doc["id"]], mid, conv)
        row = attachments.get(doc["id"])
        attachments.write_notes(NoteLLM(), row)
        return attachments.get(doc["id"]), mid

    def test_pages_search_and_page_numbers(self):
        row = self.doc(FIELD_LOG, "field_log.txt")
        pages = attachments.pages_of(row)
        self.assertGreater(len(pages), 20)
        self.assertTrue(all(store.estimate_tokens(p) <= store.DOCUMENT_PAGE_TOKENS for p in pages))
        self.assertEqual("".join(p.replace("\n", "") for p in pages).replace(" ", ""),
                         FIELD_LOG.replace("\n", "").replace(" ", ""))   # nothing lost
        found = attachments.search_pages(row, "What was the weather on day 110?")
        self.assertIn("Day 110 field log", pages[found[0] - 1])
        # Live check 2026-10-29: a follow-up names no day; small talk pulled pages.
        follow = attachments.search_pages(row, "And what was the weather that day?",
                                          "How many seedlings on day 110?")
        self.assertIn("Day 110 field log", pages[follow[0] - 1])
        self.assertEqual(attachments.search_pages(row, "How was your day?"), [])
        self.assertEqual(attachments.page_refs("look at page 4 and pages 7-8", 30), [4, 7, 8])
        self.assertEqual(attachments.page_refs("12ページを見て", 30), [12])
        self.assertEqual(attachments.page_refs("page 99", 30), [])
        self.assertIn("実験", attachments._terms("今日の実験メモ"))

    def test_notes_written_with_parts_and_summary(self):
        row, _mid = self.long_doc()
        data = attachments.load_notes(row)
        sections = len(range(1, len(attachments.pages_of(row)) + 1, attachments.SECTION_PAGES))
        self.assertEqual((data["total"], len(data["sections"])), (sections, sections))
        self.assertTrue(data["sections"][0]["note"].startswith("Field log days 1-"))   # brackets gone
        self.assertEqual(len(data["parts"]), -(-sections // attachments.PART_SECTIONS))
        self.assertEqual(row["notes"], store.NOTES_DONE)
        self.assertTrue(row["caption"])                        # summary written from the notes
        self.assertEqual(store.document_size(row), f"{store.document_pages(row['tokens'])} pages, "
                                                   "notes on all of it")
        self.assertEqual(attachments.notes_progress(row)["state"], "done")

    def test_prompt_notes_stable_found_pages_changing(self):
        row, _mid = self.long_doc()
        self.her_msg()
        self.user_msg("What was the weather on day 110?")
        history = [{"role": m["role"], "content": m["content"]} for m in store.load_memory_for_prompt(1)]
        out = attachments.with_pictures(prompt(*history), 1, see=True)
        reads = attachments.take_turn_reads()
        self.assertIn("YOUR READING NOTES ON THE WHOLE DOCUMENT", out[1]["content"])
        self.assertNotIn("Day 110 field log", out[1]["content"])      # pages are not in the stable part
        right_now = out[-1]["content"]
        self.assertIn(attachments.LONG_DOC_RULE, right_now)
        self.assertIn("Day 110 field log", right_now)
        self.assertRegex(reads, r"^Read: Document 1: notes \+ pages? \d")
        self.assertEqual(chat._content_text(right_now.split(attachments.LONG_DOC_RULE)[1]), "")
        # Next question: the notes message is the same; "summarize it" brings no pages.
        self.her_msg()
        self.user_msg("Can you summarize the whole thing?")
        history = [{"role": m["role"], "content": m["content"]} for m in store.load_memory_for_prompt(1)]
        again = attachments.with_pictures(prompt(*history), 1, see=True)
        self.assertEqual(again[1], out[1])
        self.assertNotIn("--- page", again[-1]["content"])
        self.assertEqual(attachments.take_turn_reads(), "Read: Document 1: notes")
        self.assertEqual(attachments.take_turn_reads(), "")                # handed over once

    def test_named_page_is_shown(self):
        self.long_doc()
        self.her_msg()
        self.user_msg("what is on page 3?")
        history = [{"role": m["role"], "content": m["content"]} for m in store.load_memory_for_prompt(1)]
        out = attachments.with_pictures(prompt(*history), 1, see=True)
        self.assertIn("--- page 3 ---", out[-1]["content"])
        self.assertEqual(attachments.take_turn_reads(), "Read: Document 1: notes + page 3")

    def test_start_on_attach_progress_and_worker(self):
        with patch.object(store, "effective_context_budget", return_value=6000), \
                patch.object(attachments, "_notes_llm", return_value=NoteLLM()):
            row = self.doc(FIELD_LOG, "field_log.txt")
            short = self.doc()
            self.assertFalse(attachments.maybe_start_notes(short, 1))        # she reads it all
            self.assertTrue(attachments.maybe_start_notes(row, 1))
            self.assertEqual(self.queued, [row["id"]])
            attachments.notes_job(row["id"])                             # the worker's job
            card = attachments.card(attachments.get(row["id"]), 1)
        self.assertEqual(card["notes"]["state"], "done")
        self.assertIn("notes on all", card["warning"])
        self.assertIn(f"1/{row['id']}_notes.json", self.files())

    def test_card_while_writing(self):
        row = self.doc(FIELD_LOG, "field_log.txt")
        attachments._set_notes_state(row["id"], store.NOTES_WRITING)
        attachments._save_notes(row, {"total": 10, "sections": [{"from": 1, "to": 2, "note": "x"}] * 4})
        with patch.object(store, "effective_context_budget", return_value=6000):
            card = attachments.card(attachments.get(row["id"]), 1)
        self.assertTrue(card["warning"].startswith("Reading... 40%"))

    def test_notes_file_goes_with_the_message_and_survives_cleanup(self):
        row, mid = self.long_doc()
        attachments.cleanup_unsent(now=__import__("datetime").datetime.now()
                                   + __import__("datetime").timedelta(days=3))
        self.assertIn(f"1/{row['id']}_notes.json", self.files())
        store.delete_message(mid)
        self.assertEqual(self.files(), [])

    def test_deleted_while_writing_stops(self):
        row = self.doc(FIELD_LOG, "field_log.txt")

        class Deleting(NoteLLM):
            def invoke(self_, messages):
                if len(self_.calls) == 2:
                    store.delete_message(0)
                    attachments.remove(row["id"])     # a card not sent: deleted
                return super().invoke(messages)

        self.assertFalse(attachments.write_notes(Deleting(), row))
        self.assertIsNone(attachments.get(row["id"]))

    def test_reads_saved_with_her_reply(self):
        her = self.her_msg()
        store.set_message_doc_reads(her, "Read: Document 1: notes + pages 54-55")
        rows = {r["id"]: r for r in store.load_memory_raw(1)}
        self.assertEqual(rows[her]["doc_reads"], "Read: Document 1: notes + pages 54-55")
        self.assertEqual(store.load_message_doc_reads(her), "Read: Document 1: notes + pages 54-55")


if __name__ == "__main__":
    unittest.main()

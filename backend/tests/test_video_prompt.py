"""Videos - Phase 3: she watches (video plan, 2026-11-03).

Covers: a video in her reply call (its own message before the words: label,
stills with their times, what is said, end mark), the same message on later
turns while it is on her desk (P1a, prompt reuse), the real video for a cloud
model that watches videos (V3a) and the stills when it can't or refuses,
speech only when her model can't see pictures, a video asked about again,
her list, the rules, the send check, her reaction / caption / chat title /
long-term memory line, and the "Watched:" line under her reply.
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api
import attachments
import chat
import memory as store
import video
from test_attachments import TempStore, images, picture, prompt

SAID = [{"start": 1.2, "end": 3.0, "text": "Look at the fireworks!"},
        {"start": 65.9, "end": 67.0, "text": "Oh habis? Itu saja aja"}]


class VideoStore(TempStore):
    def setUp(self):
        super().setUp()
        video._URLS.clear()
        p = patch.object(attachments, "_probe_video", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def vid(self, name="boat trip.mp4", duration=42.0, frames=3, speech="done", segments=None,
            cloud=False, state="ready", conv=1, has_audio=True):
        conn = store._connect_db()
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("INSERT INTO attachments (conversation_id, kind, name, mime, created_at, tokens) "
                  "VALUES (?, 'video', ?, 'video/mp4', '2026-11-03 10:00:00', 0)", (conv, name))
        vid = c.lastrowid
        conn.commit()
        conn.close()
        base = f"{conv}/{vid}_"
        os.makedirs(os.path.join(store.attachments_dir(), str(conv)), exist_ok=True)
        stills = []
        times = video.frame_times(duration)[:frames] if frames else []
        for i, t in enumerate(times, start=1):
            rel = f"{base}f{i:02d}.jpg"
            Path(store.attachments_dir(), rel).write_bytes(picture())
            stills.append({"t": t, "file": rel})
        Path(store.attachments_dir(), base + "watch.mp4").write_bytes(b"watch")
        Path(store.attachments_dir(), base + "thumb.jpg").write_bytes(picture())
        meta = {"state": state, "duration": duration, "has_audio": has_audio, "frames": stills,
                "watch": base + "watch.mp4", "cloud": None,
                "speech": {"state": speech if has_audio else "none"}}
        if speech == "done" and has_audio:
            meta["speech"]["segments"] = SAID if segments is None else segments
        if cloud:
            Path(store.attachments_dir(), base + "cloud.mp4").write_bytes(b"cloud-video")
            meta["cloud"] = {"file": base + "cloud.mp4", "bytes": 11}
        row = {"id": vid, "conversation_id": conv, "thumb": base + "thumb.jpg"}
        video._save_meta(row, meta)
        video._update_row(vid, path=base + "watch.mp4", thumb=base + "thumb.jpg",
                          tokens=video._tokens(meta) if state == "ready" else 0)
        return attachments.get(vid)

    def history(self):
        return [{"role": m["role"], "content": m["content"]} for m in store.load_memory_for_prompt(1)]

    def texts(self, message):
        c = message["content"]
        return [p["text"] for p in c if p.get("type") == "text"] if isinstance(c, list) else [c]


LABEL = '[Video 1 (turn 1) from the user: "boat trip.mp4", 0:42]'


class PromptTests(VideoStore):
    def test_newest_video_own_message_before_the_words(self):
        v = self.vid()
        attachments.claim([v["id"]], self.user_msg("what do you think?"), 1)
        out = attachments.with_pictures(prompt({"role": "user", "content": "what do you think?"}),
                                        1, see=True)
        self.assertEqual([m["role"] for m in out], ["system", "user", "user", "system"])
        texts = self.texts(out[1])
        self.assertEqual(texts[0], LABEL)
        self.assertIn("each marked with its time from the start", texts[1])
        self.assertEqual(texts[2:5], ["[0:01]", "[0:03]", "[0:06]"])     # 42 s / 16 stills
        self.assertEqual(len(images(out[1])), 3)
        self.assertIn("[0:01] Look at the fireworks!\n[1:05] Oh habis? Itu saja aja", texts[-2])
        self.assertIn("can mishear", texts[-2])
        self.assertEqual(texts[-1], "[End of video 1]")
        self.assertEqual(out[2]["content"], "what do you think? " + LABEL)
        self.assertTrue(out[-1]["content"].endswith(attachments.VIDEO_RULE))
        self.assertEqual(attachments.take_turn_reads(), "Watched: Video 1 (3 stills + what is said)")
        self.assertFalse(attachments.took_video())

    def test_same_message_on_the_next_turn(self):
        # P1a: the stills stay while the video is on her desk - the message
        # is byte-for-byte the same, so her built-in model reuses it.
        v = self.vid()
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        turn_one = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        attachments.take_turn_reads()
        attachments.save_caption(v["id"], "A boat trip with fireworks.")   # after her reply
        self.her_msg()
        self.user_msg("what was said at the end?")
        turn_two = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertEqual(turn_two[1], turn_one[1])
        self.assertEqual(turn_two[2]["content"], "look " + LABEL)
        self.assertTrue(turn_two[-1]["content"].endswith(attachments.VIDEO_DESK_RULE))
        self.assertEqual(attachments.take_turn_reads(), "")     # nothing new watched

    def test_off_desk_label_with_caption_only(self):
        v = self.vid()
        attachments.claim([v["id"]], self.user_msg("here"), 1)
        attachments.save_caption(v["id"], "A boat trip with fireworks.")
        attachments.remove(v["id"])
        self.her_msg()
        self.user_msg("thanks")
        out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertEqual(out[1]["content"],
                         'here [Video 1 (turn 1) from the user: "boat trip.mp4", 0:42: '
                         'A boat trip with fireworks.]')
        self.assertNotIn("fireworks!", str(out))

    def test_asked_about_again(self):
        v = self.vid(name="sunset_boat.mp4")
        attachments.claim([v["id"]], self.user_msg("here"), 1)
        attachments.remove(v["id"])
        self.her_msg()
        for text in ("what happened in video 1?", "the last clip - did he wave?",
                     "can you look at the sunset boat video again?", "さっきの動画をもう一度見て"):
            self.user_msg(text)
            out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
            self.assertIn("(shown to you again now)", self.texts(out[-3])[0], text)
            self.assertEqual(len(images(out[-3])), 3, text)
            self.assertEqual(out[-2]["content"], text)
            self.assertTrue(out[-1]["content"].endswith(attachments.VIDEO_RULE), text)
            self.her_msg()
        self.user_msg("how was your day?")
        self.assertNotIn("fireworks", str(attachments.with_pictures(prompt(*self.history()), 1,
                                                                     see=True)))

    def test_cloud_model_that_watches_gets_the_real_video(self):
        v = self.vid(cloud=True)
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        with patch.object(attachments, "_probe_video", return_value=True):
            out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
            parts = out[1]["content"]
            sent = [p for p in parts if p["type"] == "video_url"]
            self.assertEqual(sent[0]["video_url"]["url"],
                             "data:video/mp4;base64,Y2xvdWQtdmlkZW8=")
            self.assertEqual(images(out[1]), [])
            self.assertIn("Look at the fireworks!", str(parts))      # the speech still helps
            self.assertTrue(out[-1]["content"].endswith(attachments.VIDEO_WATCH_RULE))
            self.assertTrue(attachments.took_video())
            self.assertIn("the whole video with its sound", attachments.take_turn_reads())
            # Later turns (V7a): the stills while it stays on her desk.
            self.her_msg()
            self.user_msg("and the end?")
            out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
            self.assertEqual(len(images(out[1])), 3)
            self.assertFalse(attachments.took_video())

    def test_stills_when_no_cloud_copy_or_it_refused(self):
        v = self.vid(cloud=False)
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        with patch.object(attachments, "_probe_video", return_value=True):
            out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
            self.assertEqual(len(images(out[1])), 3)            # no cloud copy: the stills
        w = self.vid(cloud=True)
        self.her_msg()
        attachments.claim([w["id"]], self.user_msg("and this one"), 1)
        with patch.object(attachments, "_probe_video", return_value=True):
            attachments.mark_cannot_watch()                     # it refused once
            out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertFalse(attachments.took_video())
        self.assertNotIn("video_url", str(out))

    def test_speech_only_when_her_model_cannot_see(self):
        v = self.vid()
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        out = attachments.with_pictures(prompt(*self.history()), 1, see=False)
        self.assertEqual(images(out[1]), [])
        self.assertIn("could not see this video", str(out[1]["content"]))
        self.assertIn("Look at the fireworks!", str(out[1]["content"]))
        self.assertTrue(out[-1]["content"].endswith(attachments.VIDEO_TEXT_RULE))
        self.assertIn("only what is said", attachments.take_turn_reads())

    def test_no_sound_and_speech_not_written_yet(self):
        a = self.vid(name="a.mp4", has_audio=False)
        b = self.vid(name="b.mp4", speech="waiting")
        c = self.vid(name="c.mp4", segments=[])
        attachments.claim([a["id"], b["id"], c["id"]], self.user_msg("three"), 1)
        out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        blocks = self.texts(out[1])
        self.assertIn("(The video has no sound.)", blocks)
        self.assertIn("(What is said in it is still being written down - you don't have it yet.)",
                      blocks)
        self.assertIn("(Nobody speaks in it - any music or sounds were not written down.)", blocks)
        self.assertEqual(attachments.take_turn_reads(),
                         "Watched: Video 1 (3 stills); Video 2 (3 stills); Video 3 (3 stills)")

    def test_no_missing_picture_note_and_helpers_read_only_the_words(self):
        v = self.vid()
        attachments.claim([v["id"]], self.user_msg("look at this"), 1)
        out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertNotIn(attachments.NO_PICTURE_NOTE, str(out))
        self.assertEqual(chat._content_text(out[1]["content"]), "")      # the video
        self.assertEqual(chat._content_text(out[2]["content"]), "look at this")
        self.assertEqual(chat._content_text("hi\n" + attachments.VIDEO_RULE), "hi")

    def test_with_a_document_and_a_picture(self):
        v, pic = self.vid(), self.upload()
        doc = attachments.save_upload("list.txt", b"Shopping list:\n- milk\n", 1)
        attachments.claim([pic["id"], doc["id"], v["id"]], self.user_msg("all three"), 1)
        out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertEqual(self.texts(out[1])[0].split("\n")[0],
                         '[Document 1 (turn 1) from the user: "list.txt", 1 page]')
        self.assertIn(LABEL, self.texts(out[1]))
        self.assertEqual(len(images(out[1])), 3)
        self.assertEqual(len(images(out[2])), 1)                 # the picture with the words
        note = out[-1]["content"]
        for rule in (attachments.PICTURE_RULE, attachments.DOCUMENT_RULE, attachments.VIDEO_RULE):
            self.assertIn(rule, note)

    def test_her_list_names_videos(self):
        v = self.vid()
        attachments.claim([v["id"]], self.user_msg("here"), 1)
        attachments.save_caption(v["id"], "A boat trip.")
        listing = attachments.picture_index_message(1)["content"]
        self.assertIn('VIDEOS THE USER SHOWED YOU', listing)
        self.assertIn('- Video 1 (turn 1, ', listing)
        self.assertIn('"boat trip.mp4", 0:42: A boat trip.', listing)

    def test_room_counts_videos(self):
        v = self.vid()
        tokens = attachments.get(v["id"])["tokens"]
        self.assertEqual(tokens, video.estimate_tokens(42.0, True, video.speech_text(SAID)))
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        self.assertEqual(store.picture_reserve_tokens(1), tokens)
        self.assertEqual(attachments.room_tokens(1), 16000 - 4000 - tokens)


class SendTests(VideoStore):
    def test_send_check(self):
        ready, busy, broken = self.vid(), self.vid(state="preparing"), self.vid(state="failed")
        self.assertIsNone(api._check_attachments_for_send([ready["id"]], "look"))
        self.assertIn("still being prepared", api._check_attachments_for_send([busy["id"]], ""))
        self.assertIn("could not be prepared", api._check_attachments_for_send([broken["id"]], ""))

    def test_no_room(self):
        v = self.vid()
        video._update_row(v["id"], tokens=20000)
        self.assertIn("no room for this video", api._check_attachments_for_send([v["id"]], ""))


class AroundTheVideoTests(VideoStore):
    def test_reaction_words(self):
        v = self.vid(name="VID_20261103_101500.mp4", duration=95)
        w = self.vid(name="fireworks at the harbour.mov")
        self.assertEqual(attachments._what(v), "a video (1:35 long)")
        event = attachments.reaction_event([w])
        self.assertIn('a video named "fireworks at the harbour" (0:42 long)', event)
        self.assertIn("you have not watched it", event)
        self.assertIn("2 videos (", attachments.reaction_event([v, w]))
        self.assertIn("You never watched it.", attachments.withdraw_event(w))

    def test_she_reacts_to_a_video_once_it_has_arrived(self):
        v = self.vid(state="preparing")
        with patch.object(chat, "_attach_line", return_value={"ok": True}) as said:
            self.assertEqual(chat.generate_attach_reaction([v["id"]], 1), {"ok": True})
        self.assertIn("a video named", said.call_args[0][2])

    def test_caption_from_stills_and_speech(self):
        seen = []

        class FakeLLM:
            def invoke(self, messages):
                seen.append(messages[0]["content"])
                return type("R", (), {"content": ' "Fireworks over a harbour seen from a boat." '})()
        v = self.vid(frames=16)
        self.assertEqual(video.write_caption(FakeLLM(), v),
                         "Fireworks over a harbour seen from a boat.")
        parts = seen[-1]
        self.assertEqual(len([p for p in parts if p["type"] == "image_url"]), 4)
        self.assertIn("Oh habis?", str(parts))
        video.write_caption(FakeLLM(), v, see=False)
        self.assertEqual([p for p in seen[-1] if p["type"] == "image_url"], [])
        silent = self.vid(frames=0, has_audio=False)
        self.assertEqual(video.write_caption(FakeLLM(), silent, see=False), "")

    def test_caption_names_a_chat_and_her_memory_line(self):
        conv = store.create_conversation()
        v = self.vid(conv=conv)
        mid = self.user_msg("", conv=conv)
        rows = attachments.claim([v["id"]], mid, conv)
        rows[0]["caption"] = "Fireworks over a harbour"
        self.assertEqual(attachments.title_from_picture(conv, mid, "", rows),
                         "Video: Fireworks over a harbour")
        self.assertEqual(attachments.picture_titled_chat(mid), conv)
        attachments.untitle_chat(conv)
        self.assertEqual(attachments.picture_titled_chat(mid), None)
        self.assertEqual(attachments.labelled_line("wow", rows),
                         'wow [The user showed you a video "boat trip": Fireworks over a harbour]')

    def test_refused_video_retries_with_stills(self):
        calls = []

        def reply():
            calls.append(1)
            if len(calls) == 1:
                attachments._turn.video = True
                raise RuntimeError("Error code: 413 - request entity too large")
            attachments._turn.video = False
            return "ok"
        with patch.object(attachments, "mark_cannot_watch") as marked, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(chat._with_picture_retry(reply), "ok")
        marked.assert_called_once()
        self.assertEqual(len(calls), 2)

    def test_rejection_words(self):
        self.assertTrue(attachments.is_video_rejection(Exception("Error code: 413")))
        self.assertTrue(attachments.is_video_rejection(Exception("video input is not supported")))
        self.assertFalse(attachments.is_video_rejection(Exception("Connection error.")))
        # OpenRouter with less than $1 on the account.
        self.assertTrue(attachments.is_video_rejection(Exception(
            "Error code: 402 - {'error': {'message': 'This request requires at least $1.00 "
            "in balance for video', 'code': 402}}")))
        self.assertFalse(attachments.is_video_rejection(Exception(
            "Error code: 402 - insufficient credits")))

    def test_stamp_rounds_down(self):
        self.assertEqual([video.stamp(t) for t in (0.5, 1.5, 2.5, 65.99, 600)],
                         ["0:00", "0:01", "0:02", "1:05", "10:00"])


class ProbeVideoTests(unittest.TestCase):
    def test_openrouter_input_modalities(self):
        listing = {"data": [{"id": "google/gemini-3.8-flash",
                             "architecture": {"input_modalities": ["text", "image", "video"]}},
                            {"id": "openai/gpt-x", "architecture": {"input_modalities": ["image"]}}]}

        class R:
            def json(self):
                return listing
        url = "https://openrouter.ai/api/v1"
        with patch.object(attachments.requests, "get", return_value=R()):
            self.assertTrue(attachments._probe_video(url, "google/gemini-3.8-flash"))
            self.assertFalse(attachments._probe_video(url, "openai/gpt-x"))
        # Her built-in model / other local servers: always the stills (V1a).
        self.assertFalse(attachments._probe_video("http://127.0.0.1:8080/v1", "gemma"))


if __name__ == "__main__":
    unittest.main()

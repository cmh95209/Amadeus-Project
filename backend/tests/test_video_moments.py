"""Videos - Phase 4: watch notes and closer looks (video plan, 2026-11-03).

Covers: the stretches (~30 s), her watch notes (one call per stretch from its
stills + what is said, every video - MH 1b; a new caption for videos over a
minute; they start once the video is ready and its speech written, and go on
after a restart), the notes in her prompt and in the player, the moments a
message names ("1:30", "1分30秒", "the end"), the stretch found by its words
when it names none (MH 2a), the closer look in her prompt (stills, or a clip
with its sound for a model that watches videos - MH 3a), the room it takes
and the "Watched:" line.
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api
import attachments
import memory as store
import video
from test_attachments import images, picture, prompt
from test_video_prompt import VideoStore

LONG_SAID = [{"start": 3.0, "end": 5.0, "text": "Okay, we're recording."},
             {"start": 91.0, "end": 94.0, "text": "Watch the bridge coming up."},
             {"start": 400.0, "end": 403.0, "text": "That was the last lock."}]
NOTES = [f"boats and water, nothing special {i}" for i in range(20)]
NOTES[4] = "a man walks a white dog along the path, then waves"


class FakeLLM:
    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=f"note {len(self.calls)}")


class Base(VideoStore):
    def setUp(self):
        super().setUp()
        p = patch.object(attachments, "start_notes")
        self.queued = p.start()
        self.addCleanup(p.stop)
        p = patch.object(video, "_still", side_effect=self.fake_still)
        p.start()
        self.addCleanup(p.stop)

    def fake_still(self, row, meta, t):
        rel = f"{video._rel_base(row)}s{int(round(t * 10)):05d}.jpg"
        Path(store.attachments_dir(), rel).write_bytes(picture())
        return rel

    def long(self, notes=True, **kw):
        kw.setdefault("segments", LONG_SAID)
        v = self.vid(name="canal.mp4", duration=600.0, frames=16, **kw)
        if notes:
            video._save_watch_notes(v, {"total": 20, "stretches": [
                {"from": a, "to": b, "note": NOTES[i]} for i, (a, b) in enumerate(video.stretches(600))]})
            attachments._set_notes_state(v["id"], store.NOTES_DONE)
        return attachments.get(v["id"])

    def on_desk(self, v, words):
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        self.her_msg()
        self.user_msg(words)
        out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        return out, attachments.take_turn_reads()


class StretchTests(unittest.TestCase):
    def test_stretches(self):
        self.assertEqual(len(video.stretches(600)), 20)
        self.assertEqual(video.stretches(600)[3], (90.0, 120.0))
        self.assertEqual(video.stretches(42), [(0.0, 21.0), (21.0, 42.0)])
        self.assertEqual(video.stretches(10), [(0.0, 10.0)])

    def test_windows_stay_inside_the_video(self):
        self.assertEqual(video.window(90, 600), (80.0, 100.0))
        self.assertEqual(video.window(3, 600), (0.0, 20.0))
        self.assertEqual(video.window(600, 600), (580.0, 600.0))
        self.assertEqual(video.window(90, 600, video.CLIP_HALF), (75.0, 105.0))

    def test_speech_between(self):
        meta = {"speech": {"state": "done", "segments": LONG_SAID}}
        self.assertEqual([s["start"] for s in video.speech_between(meta, 80, 100)], [91.0])
        self.assertEqual([s["start"] for s in video.speech_between(meta, 4, 20)], [3.0])  # still going
        self.assertEqual(video.speech_between({"speech": {"state": "waiting"}}, 0, 600), [])


class MomentTimeTests(unittest.TestCase):
    def t(self, text, duration=600, ends=True):
        return video.moment_times(text, duration, ends)

    def test_times_named(self):
        self.assertEqual(self.t("what happens at 1:30?"), [90.0])
        self.assertEqual(self.t("1分30秒のところで何が起きた？"), [90.0])
        self.assertEqual(self.t("2分ごろの人は誰？"), [120.0])
        self.assertEqual(self.t("30秒あたりで何て言った？"), [30.0])
        self.assertEqual(self.t("what's going on 90 seconds in"), [90.0])
        self.assertEqual(self.t("what about minute 3"), [180.0])
        self.assertEqual(self.t("at 2m15s he turns"), [135.0])
        self.assertEqual(self.t("compare 0:30 and 4:10"), [30.0, 250.0])

    def test_not_moments(self):
        self.assertEqual(self.t("let's meet at 14:30"), [])           # past its end
        self.assertEqual(self.t("I took a 5 minute break"), [])
        self.assertEqual(self.t("in the end I liked it"), [])
        self.assertEqual(self.t("最後の動画どうだった？"), [])

    def test_start_and_end(self):
        self.assertEqual(self.t("what happens at the end?"), [600.0])
        self.assertEqual(self.t("冒頭で何て言ってた？"), [0.0])
        self.assertEqual(self.t("最後に何が起きた？"), [600.0])
        self.assertEqual(self.t("what happens at the end?", ends=False), [])


class WatchNoteTests(Base):
    def test_one_note_per_stretch_from_its_stills_and_speech(self):
        v = self.vid(name="canal.mp4", duration=95.0, frames=16, segments=LONG_SAID)
        llm = FakeLLM()
        self.assertTrue(video.write_watch_notes(llm, v, see=True))
        self.assertEqual(len(llm.calls), 5)                  # 4 stretches of 23.75 s + the caption
        parts = llm.calls[3][0]["content"]
        self.assertIn('"canal.mp4" (1:11 to 1:35)', parts[0]["text"])
        self.assertEqual(sum(1 for p in parts if p["type"] == "image_url"), 8)
        self.assertIn("[1:31] Watch the bridge coming up.", parts[-1]["text"])
        self.assertEqual(llm.calls[1][0]["content"][-1]["text"], "(Nobody speaks then.)")
        row = attachments.get(v["id"])
        self.assertEqual(row["notes"], store.NOTES_DONE)
        self.assertEqual(video.notes_progress(row), {"state": "done", "done": 4, "total": 4})
        self.assertEqual(video.notes_lines(video.load_watch_notes(row)).splitlines()[0],
                         "- 0:00-0:23: note 1")
        self.assertGreater(row["tokens"], v["tokens"])       # her room counts the notes

    def test_long_video_gets_a_caption_from_its_notes(self):
        v = self.vid(name="canal.mp4", duration=100.0, frames=16)
        llm = FakeLLM()
        video.write_watch_notes(llm, v, see=True)
        self.assertEqual(len(llm.calls), 5)
        self.assertIn("from your watch notes", llm.calls[-1][0]["content"])
        self.assertEqual(attachments.get(v["id"])["caption"], "note 5")

    def test_short_video_uses_the_stills_it_has(self):
        v = self.vid(duration=20.0, frames=16)
        llm = FakeLLM()
        video.write_watch_notes(llm, v, see=True)
        self.assertEqual(len(llm.calls), 1)                  # one stretch, its caption stays
        stills = [p for p in llm.calls[0][0]["content"] if p["type"] == "text" and p["text"].startswith("[")]
        self.assertEqual(len(stills), 8)
        self.assertFalse(any("_s" in f for f in self.files()))   # no new stills made

    def test_model_that_cant_see_writes_from_the_speech(self):
        v = self.vid(name="talk.mp4", duration=40.0, frames=16)
        llm = FakeLLM()
        video.write_watch_notes(llm, v, see=False)
        parts = llm.calls[0][0]["content"]
        self.assertFalse(any(p["type"] == "image_url" for p in parts))
        self.assertIn("only what is said", parts[1]["text"])

    def test_goes_on_where_it_stopped_and_stops_when_deleted(self):
        v = self.vid(name="canal.mp4", duration=90.0, frames=16)
        video._save_watch_notes(v, {"total": 3, "stretches": [{"from": 0.0, "to": 30.0, "note": "old"}]})
        llm = FakeLLM()
        video.write_watch_notes(llm, v, see=True)
        self.assertEqual(len(llm.calls), 3)                  # 2 stretches left + the caption
        self.assertEqual(video.load_watch_notes(v)["stretches"][0]["note"], "old")
        w = self.vid(name="gone.mp4", duration=90.0, frames=16)
        with patch.object(attachments, "get", return_value=None):
            self.assertFalse(video.write_watch_notes(FakeLLM(), w, see=True))
        self.assertIsNone(attachments.get(w["id"])["notes"])

    def test_they_start_when_ready_and_spoken(self):
        v = self.vid()
        self.assertTrue(video.start_notes(v))
        self.queued.assert_called_once()
        self.queued.reset_mock()
        self.assertFalse(video.start_notes(self.vid(speech="waiting")))      # after the speech
        self.assertFalse(video.start_notes(self.vid(state="preparing")))
        self.assertTrue(video.start_notes(self.vid(has_audio=False)))
        self.queued.assert_called_once()

    def test_restart_starts_missing_notes(self):
        v = self.vid()
        done = self.long()
        with patch.object(video, "start_notes") as start:
            video.resume()
        self.assertEqual([c.args[0]["id"] for c in start.call_args_list], [v["id"]])
        self.assertNotEqual(v["id"], done["id"])

    def test_the_notes_worker_writes_video_notes(self):
        v = self.vid()
        with patch.object(attachments, "_notes_llm", return_value="llm"), \
                patch.object(video, "write_watch_notes") as write:
            attachments.notes_job(v["id"])
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], "llm")

    def test_notes_in_her_prompt_and_the_player(self):
        v = self.long(cloud=True)
        block = video.video_block(v, video.FRAMES)
        notes = next(p["text"] for p in block if p.get("text", "").startswith("YOUR WATCH NOTES"))
        self.assertIn("- 2:00-2:30: a man walks a white dog", notes)
        self.assertFalse(any(p.get("text", "").startswith("YOUR WATCH NOTES")
                             for p in video.video_block(v, video.WATCH) if p["type"] == "text"))
        data = api.application.test_client().get(f"/attachments/{v['id']}/watch").get_json()
        self.assertEqual(data["notes"]["state"], "done")
        self.assertIn("- 0:00-0:30: boats and water", data["notes_text"])


class CloseLookTests(Base):
    def close(self, out):
        for m in out:
            for p in m["content"] if isinstance(m["content"], list) else []:
                if p.get("type") == "text" and "a closer look at" in p["text"]:
                    return m
        return None

    def test_a_named_time_gets_a_closer_look(self):
        out, reads = self.on_desk(self.long(), "what happens at 1:30?")
        m = self.close(out)
        texts = self.texts(m)
        self.assertIn("[Video 1, a closer look at 1:20-1:40 for this message]", texts)
        self.assertEqual(len(images(m)), 8)                    # its own message, before the words
        self.assertIn("[1:31] Watch the bridge coming up.", "\n".join(texts))
        self.assertEqual(out[out.index(m) - 1]["content"], "what happens at 1:30?")   # after the words
        self.assertIn(attachments.CLOSE_LOOK_RULE, out[-1]["content"])
        self.assertIn("Video 1 (a closer look at 1:20-1:40)", reads)

    def test_words_find_the_stretch(self):
        out, reads = self.on_desk(self.long(), "when does the dog show up?")
        texts = self.texts(self.close(out))
        head = next(t for t in texts if "a closer look" in t)
        self.assertIn("a closer look at 2:05-2:25", head)
        self.assertIn("found by matching words", head)

    def test_small_talk_gets_none(self):
        out, reads = self.on_desk(self.long(), "I'm going to make dinner now")
        self.assertIsNone(self.close(out))
        self.assertNotIn("closer look", reads)

    def test_a_question_about_all_of_it_gets_none(self):
        # Live check 2026-11-03: "what is it about?" pulled a random stretch.
        out, _r = self.on_desk(self.long(), "Here's a long video about a dog. What is it about?")
        self.assertIsNone(self.close(out))

    def test_the_closer_look_leaves_the_video_message_alone(self):
        v = self.long()
        attachments.claim([v["id"]], self.user_msg("what happens at 1:30?"), 1)
        one = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        attachments.take_turn_reads()
        self.her_msg()
        self.user_msg("nice")
        two = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertEqual(two[1], one[1])                      # the video's own message
        self.assertIsNotNone(self.close(one))
        self.assertIsNone(self.close(two))

    def test_short_video_needs_none(self):
        out, _reads = self.on_desk(self.vid(duration=30.0, frames=16), "what happens at 0:20?")
        self.assertIsNone(self.close(out))

    def test_end_needs_the_video_spoken_of(self):
        out, _r = self.on_desk(self.long(), "what happens at the end?")
        self.assertIsNone(self.close(out))
        self.user_msg("ok")
        out = attachments.with_pictures(prompt(*self.history() + [
            {"role": "user", "content": "what happens at the end of the video?"}]), 1, see=True)
        self.assertIsNotNone(self.close(out))

    def test_a_model_that_watches_gets_a_clip(self):
        v = self.long()

        def clip(row, meta, a, b):
            rel = f"{video._rel_base(row)}clip.mp4"
            Path(store.attachments_dir(), rel).write_bytes(b"clip")
            return rel
        with patch.object(attachments, "_probe_video", return_value=True), \
                patch.object(video, "_clip", side_effect=clip) as made:
            out, reads = self.on_desk(v, "what happens at 1:30?")
        self.assertEqual(made.call_args.args[2:], (75.0, 105.0))
        m = self.close(out)
        self.assertEqual(sum(1 for p in m["content"] if p["type"] == "video_url"), 1)
        self.assertIn("add 1:15 to the clip's own times", "\n".join(self.texts(m)))
        self.assertTrue(attachments.took_video())
        self.assertIn("Video 1 (the clip 1:15-1:45 with its sound)", reads)

    def test_none_when_she_watches_it_whole(self):
        v = self.long(cloud=True)
        attachments.claim([v["id"]], self.user_msg("what happens at 1:30?"), 1)
        with patch.object(attachments, "_probe_video", return_value=True):
            out = attachments.with_pictures(prompt(*self.history()), 1, see=True)
        self.assertIsNone(self.close(out))

    def test_none_when_she_cant_see(self):
        v = self.long()
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        self.her_msg()
        self.user_msg("what happens at 1:30?")
        out = attachments.with_pictures(prompt(*self.history()), 1, see=False)
        self.assertIsNone(self.close(out))

    def test_room_is_counted(self):
        v = self.long()
        attachments.claim([v["id"]], self.user_msg("look"), 1)
        self.her_msg()
        self.user_msg("what happens at 1:30 and at 5:00?")
        self.assertEqual(attachments.prompt_close_tokens(1), 2 * video.CLOSE_TOKENS)
        self.user_msg("nice")
        self.assertEqual(attachments.prompt_close_tokens(1), 0)


if __name__ == "__main__":
    unittest.main()

"""Videos - Phase 1: preparing (video plan, 2026-11-03).

Covers: the upload in pieces (refusals, a piece at the wrong place, going on
after a drop, cancel), the check (length, rotation, no video, too long), the
encoder choice (MH P3: the graphics card first, the processor when it fails,
plain colour for every encoder - the ProRes clip of Phase 0), frame times,
the room a video takes, preparing a real clip end to end with FFmpeg (watch
copy, frames, card picture, original deleted, speech waiting for its model),
deleting a video with all its files, the clean-up of old unsent videos and
broken-off uploads, the send refusal until it is prepared, and the speech
helper (its Python, the model download, card or processor).
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api
import attachments
import memory as store
import transcribe
import video
from test_attachments import TempStore


def _proc(code=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], code, out, err)


def _probe_json(**video_stream):
    stream = {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080}
    stream.update(video_stream)
    return json.dumps({"format": {"duration": "42.5"},
                       "streams": [stream, {"codec_type": "audio"}]}).encode()


HAVE_FFMPEG = video.tools_ready()


def make_clip(path, seconds=3, size="320x240", audio=True, pix_fmt="yuv420p", codec="libx264"):
    args = [video._tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc=duration={seconds}:size={size}:rate=10"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", codec, "-pix_fmt", pix_fmt]
    if audio:
        args += ["-c:a", "aac" if not str(path).endswith(".mov") else "pcm_s16le"]
    subprocess.run(args + [str(path)], check=True, capture_output=True)
    return Path(path).read_bytes()


class Basics(unittest.TestCase):
    def test_frame_times(self):
        self.assertEqual(len(video.frame_times(600)), 16)
        self.assertEqual(video.frame_times(160)[0], 5.0)          # the middle of each stretch
        self.assertEqual(len(video.frame_times(10)), 10)          # short: one per second
        self.assertEqual(len(video.frame_times(0.4)), 1)
        self.assertTrue(all(t < 10 for t in video.frame_times(10)))

    def test_length_text(self):
        self.assertEqual(video.length_text(42.4), "0:42")
        self.assertEqual(video.length_text(600), "10:00")
        self.assertEqual(video.length_text(3725), "1:02:05")

    def test_room_estimate(self):
        self.assertEqual(video.estimate_tokens(60, False), 16 * video.FRAME_TOKENS)
        self.assertEqual(video.estimate_tokens(60, True), 16 * video.FRAME_TOKENS + 300)
        self.assertEqual(video.estimate_tokens(5, True, speech=""), 5 * video.FRAME_TOKENS)

    def test_every_encoder_gets_plain_colour(self):
        # Phase 0: the card refused the ProRes clip's 10-bit 4:2:2 colour.
        self.assertIn("format=yuv420p", video._scale_short(720))

    def test_speech_text(self):
        self.assertEqual(video.speech_text([{"start": 31.2, "end": 33, "text": "Come back!"},
                                            {"start": 62, "end": 63, "text": "おい"}]),
                         "[0:31] Come back!\n[1:02] おい")


class ProbeTests(unittest.TestCase):
    def run_probe(self, out, code=0):
        with patch.object(video, "_tool", return_value="ffprobe"), \
                patch.object(video, "_run", return_value=_proc(code, out)):
            return video.probe("x.mp4")

    def test_reads_length_size_sound(self):
        found = self.run_probe(_probe_json())
        self.assertEqual(found, {"duration": 42.5, "width": 1920, "height": 1080,
                                 "has_audio": True, "codec": "hevc"})

    def test_phone_rotation_turns_the_size(self):
        found = self.run_probe(_probe_json(side_data_list=[{"rotation": -90}]))
        self.assertEqual((found["width"], found["height"]), (1080, 1920))
        found = self.run_probe(_probe_json(tags={"rotate": "90"}))
        self.assertEqual((found["width"], found["height"]), (1080, 1920))

    def test_refusals(self):
        no_video = json.dumps({"format": {"duration": "3"},
                               "streams": [{"codec_type": "audio"}]}).encode()
        cover_only = json.dumps({"format": {"duration": "3"}, "streams": [
            {"codec_type": "video", "disposition": {"attached_pic": 1}}]}).encode()
        too_long = _probe_json().replace(b'"42.5"', b'"900"')
        for out, code in ((no_video, "not_video"), (cover_only, "not_video"),
                          (too_long, "video_too_long"), (b"", "video_broken"),
                          (_probe_json().replace(b'"42.5"', b'"0"'), "video_broken")):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                self.run_probe(out, 1 if not out else 0)
            self.assertEqual(ctx.exception.code, code)

    def test_no_ffmpeg(self):
        with patch.object(video, "_tool", return_value=None):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                video.probe("x.mp4")
        self.assertEqual(ctx.exception.code, "no_ffmpeg")


class EncoderTests(unittest.TestCase):
    def setUp(self):
        video._ENCODER[0] = None
        self.addCleanup(lambda: video._ENCODER.__setitem__(0, None))

    def pick(self, listed, works):
        def run(cmd, timeout):
            if "-encoders" in cmd:
                return _proc(0, listed.encode())
            return _proc(0 if cmd[cmd.index("-c:v") + 1] in works else 1)
        with patch.object(video, "_tool", return_value="ffmpeg"), \
                patch.object(video, "_run", side_effect=run), \
                patch.dict(os.environ, {"AMADEUS_VIDEO_ENCODER": ""}):
            return video.encoder()

    def test_card_first(self):
        self.assertEqual(self.pick("V..... h264_nvenc\nV..... libx264", {"h264_nvenc"}), "h264_nvenc")

    def test_processor_when_the_card_fails_its_test(self):
        self.assertEqual(self.pick("V..... h264_nvenc\nV..... h264_qsv", set()), "libx264")
        video._ENCODER[0] = None
        self.assertEqual(self.pick("V..... h264_nvenc\nV..... h264_amf", {"h264_amf"}), "h264_amf")

    def test_remembered_and_switch(self):
        video._ENCODER[0] = "h264_nvenc"
        self.assertEqual(video.encoder(), "h264_nvenc")
        with patch.dict(os.environ, {"AMADEUS_VIDEO_ENCODER": "cpu"}):
            self.assertEqual(video.encoder(), "libx264")

    def test_card_failure_on_a_real_video_falls_back(self):
        video._ENCODER[0] = "h264_nvenc"
        used = []

        def fake(args, duration=0.0, on_pct=None, timeout=0.0):
            enc = args[args.index("-c:v") + 1]
            used.append(enc)
            if enc != "libx264":
                raise RuntimeError("FFmpeg failed: CreateInputBuffer failed")
        with patch.object(video, "ffmpeg", side_effect=fake):
            got = video.encode("in.mov", "out.mp4", 720, 10, True, 1500, 2500, 26)
        self.assertEqual((got, used), ("libx264", ["h264_nvenc", "libx264"]))
        self.assertEqual(video._ENCODER[0], "libx264")   # the rest of this run too

    def test_processor_failure_is_not_retried(self):
        video._ENCODER[0] = "libx264"
        with patch.object(video, "ffmpeg", side_effect=RuntimeError("bad")) as run:
            with self.assertRaises(RuntimeError):
                video.encode("in.mp4", "out.mp4", 720, 10, False, 1500, 2500, 26)
        self.assertEqual(run.call_count, 1)


class UploadTests(TempStore):
    def setUp(self):
        super().setUp()
        p = patch.object(video, "tools_ready", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_refusals_at_start(self):
        for name, size, code in (("notes.pdf", 10, "video_unsupported"),
                                 ("clip.mp4", 0, "empty"), ("clip.mp4", "x", "empty"),
                                 ("clip.mp4", video.MAX_VIDEO_BYTES + 1, "video_too_big")):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                video.start_upload(name, size, 1)
            self.assertEqual(ctx.exception.code, code)
        with patch.object(video, "tools_ready", return_value=False):
            with self.assertRaises(attachments.AttachmentError) as ctx:
                video.start_upload("clip.mov", 10, 1)
        self.assertEqual(ctx.exception.code, "no_ffmpeg")

    def test_pieces_and_going_on_after_a_drop(self):
        up = video.start_upload("IMG_0001.MOV", 10, 1)
        uid = up["upload_id"]
        self.assertTrue(video.add_piece(uid, 0, b"abcd")["ok"])
        again = video.add_piece(uid, 0, b"abcd")           # a retry after a drop
        self.assertEqual((again["ok"], again["received"]), (False, 4))
        self.assertFalse(video.add_piece(uid, 4, b"x" * 7)["ok"])   # past the size
        self.assertEqual(video.upload_status(uid)["received"], 4)
        self.assertTrue(video.add_piece(uid, 4, b"efghij")["ok"])
        self.assertEqual(video.upload_status(uid)["received"], 10)

    def test_finish_needs_everything(self):
        uid = video.start_upload("clip.mp4", 10, 1)["upload_id"]
        video.add_piece(uid, 0, b"abc")
        with self.assertRaises(attachments.AttachmentError) as ctx:
            video.finish_upload(uid)
        self.assertEqual(ctx.exception.code, "upload_incomplete")

    def test_not_a_video_is_refused_and_removed(self):
        uid = video.start_upload("clip.mp4", 3, 1)["upload_id"]
        video.add_piece(uid, 0, b"abc")
        with patch.object(video, "probe", side_effect=attachments.AttachmentError("not_video")):
            with self.assertRaises(attachments.AttachmentError):
                video.finish_upload(uid)
        self.assertEqual(self.files(), [])
        with self.assertRaises(attachments.AttachmentError) as ctx:
            video.upload_status(uid)
        self.assertEqual(ctx.exception.code, "upload_gone")

    def test_bad_ids_and_cancel(self):
        for bad in ("../../memory", "", "ABC", None):
            with self.assertRaises(attachments.AttachmentError):
                video.upload_status(bad)
        uid = video.start_upload("clip.webm", 5, 1)["upload_id"]
        video.cancel_upload(uid)
        self.assertEqual(self.files(), [])

    def test_routes(self):
        client = api.application.test_client()
        r = client.post("/attachments/video", json={"name": "a.mp4", "size": 6, "conversation_id": 1})
        uid = r.get_json()["upload_id"]
        r = client.put(f"/attachments/video/{uid}?offset=0", data=b"abc")
        self.assertEqual((r.status_code, r.get_json()["received"]), (200, 3))
        r = client.put(f"/attachments/video/{uid}?offset=0", data=b"abc")
        self.assertEqual((r.status_code, r.get_json()["received"]), (409, 3))
        self.assertEqual(client.get(f"/attachments/video/{uid}").get_json()["received"], 3)
        r = client.post("/attachments/video", json={"name": "a.txt", "size": 6})
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "video_unsupported"))
        with patch.object(video, "PIECE_MAX", 2):
            r = client.put(f"/attachments/video/{uid}?offset=3", data=b"def")
        self.assertEqual(r.status_code, 413)
        self.assertEqual(client.delete(f"/attachments/video/{uid}").status_code, 200)
        self.assertEqual(client.get(f"/attachments/video/{uid}").status_code, 400)


@unittest.skipUnless(HAVE_FFMPEG, "FFmpeg not found")
class RealVideoTests(TempStore):
    """A real clip through FFmpeg (made with FFmpeg's own test pattern)."""

    def setUp(self):
        super().setUp()
        self._env = patch.dict(os.environ, {"AMADEUS_VIDEO_ENCODER": "cpu"})
        self._env.start()
        self.addCleanup(self._env.stop)
        for name in ("start", "_wants_cloud_copy"):
            p = patch.object(video, name, return_value=False)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(transcribe, "status", return_value={"state": "missing", "gb": 0.5})
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(attachments, "start_notes")      # her model is not here
        self.notes = p.start()
        self.addCleanup(p.stop)
        p = patch.object(transcribe, "when_ready")
        self.when_ready = p.start()
        self.addCleanup(p.stop)
        p = patch.object(transcribe, "start_download")
        self.download = p.start()
        self.addCleanup(p.stop)

    def send(self, data, name="clip.mp4"):
        uid = video.start_upload(name, len(data), 1)["upload_id"]
        for at in range(0, len(data), 4096):
            self.assertTrue(video.add_piece(uid, at, data[at:at + 4096])["ok"])
        return video.finish_upload(uid)

    def test_prepared_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_clip(os.path.join(tmp, "c.mp4"))
        row = self.send(data)
        self.assertEqual((row["kind"], row["width"], row["height"]), ("video", 320, 240))
        info = attachments.card(row, 1)
        self.assertEqual((info["length"], info["video"]["state"]), ("0:03", "preparing"))
        self.assertIsNone(info["video_url"])
        self.assertIn("1_thumb.jpg", " ".join(self.files()))
        video.prepare_job(row["id"])
        row = attachments.get(row["id"])
        meta = video.load_meta(row)
        self.assertEqual(meta["state"], "ready")
        self.assertEqual(len(meta["frames"]), 3)
        self.assertEqual(meta["speech"], {"state": "waiting"})     # no speech model yet
        self.when_ready.assert_called_once()
        self.download.assert_called_once()          # fetched without asking (MH 2026-11-03)
        self.assertIsNone(row["original"])                        # V5a: deleted
        self.assertTrue(row["path"].endswith("_watch.mp4"))
        found = video.probe(attachments.file_path(row["id"]))
        self.assertEqual((found["width"], found["height"], found["has_audio"]), (320, 240, True))
        names = self.files()
        self.assertFalse(any("original" in n for n in names))
        self.assertEqual(sum("_f0" in n for n in names), 3)
        info = attachments.card(row, 1)
        self.assertEqual(info["video_url"], f"/attachments/{row['id']}/file")
        self.assertIn("Writing down the speech", info["warning"])
        # The player gets the watch copy (with ranges for seeking).
        client = api.application.test_client()
        r = client.get(f"/attachments/{row['id']}/file", headers={"Range": "bytes=0-99"})
        self.assertEqual((r.status_code, r.mimetype, len(r.data)), (206, "video/mp4", 100))
        r.close()
        # The player's info (Phase 2) and the chat history's item.
        info = client.get(f"/attachments/{row['id']}/watch").get_json()
        self.assertEqual((info["length"], info["speech_state"], info["speech_text"]),
                         ("0:03", "waiting", ""))
        item = store.attachments_for_ui([row])[0]
        self.assertEqual((item["length"], item["info_url"], item["video_url"]),
                         ("0:03", f"/attachments/{row['id']}/watch", f"/attachments/{row['id']}/file"))
        self.assertEqual(client.get("/attachments/999/watch").status_code, 404)
        # Deleting the card deletes every file of it.
        self.assertEqual(attachments.remove(row["id"]), "deleted")
        self.assertEqual(self.files(), [])

    def test_prores_10bit_portrait_without_sound(self):
        # Phase 0's camera clip: ProRes, 10-bit 4:2:2 colour (the card's
        # encoder refused it) - now plain colour for every encoder.
        video._ENCODER[0] = None
        self._env.stop()
        with tempfile.TemporaryDirectory() as tmp:
            data = make_clip(os.path.join(tmp, "c.mov"), seconds=2, size="240x320", audio=False,
                             pix_fmt="yuv422p10le", codec="prores_ks")
        try:
            row = self.send(data, "camera.mov")
            self.picked = video.encoder()
            video.prepare_job(row["id"])
        finally:
            self._env.start()
            video._ENCODER[0] = None
        row = attachments.get(row["id"])
        meta = video.load_meta(row)
        self.assertEqual((meta["state"], meta["speech"]), ("ready", {"state": "none"}))
        self.assertEqual(meta["encoder"], self.picked)    # no fallback needed
        found = video.probe(attachments.file_path(row["id"]))
        self.assertEqual((found["width"], found["height"], found["has_audio"]), (240, 320, False))

    def test_cloud_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_clip(os.path.join(tmp, "c.mp4"), seconds=4, size="1280x720")
        row = self.send(data)
        with patch.object(video, "_wants_cloud_copy", return_value=True):
            video.prepare_job(row["id"])
        meta = video.load_meta(attachments.get(row["id"]))
        cloud = os.path.join(store.attachments_dir(), meta["cloud"]["file"])
        found = video.probe(cloud)
        self.assertEqual((found["width"], found["height"]), (854, 480))

    def test_restart_goes_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_clip(os.path.join(tmp, "c.mp4"), seconds=2)
        row = self.send(data)
        with patch.object(video, "start") as queued:
            self.assertEqual(video.resume(), 1)
        queued.assert_called_once()

    def test_deleted_while_preparing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_clip(os.path.join(tmp, "c.mp4"), seconds=2)
        row = self.send(data)
        real = video.encode

        def encode_then_delete(*a, **k):
            attachments.remove(row["id"])
            return real(*a, **k)
        with patch.object(video, "encode", side_effect=encode_then_delete):
            video.prepare_job(row["id"])      # no error, nothing left behind
        store.remove_attachment_files([f for f in self.files() if f.startswith("1/")])
        self.assertIsNone(attachments.get(row["id"]))

    def test_broken_video_fails_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_clip(os.path.join(tmp, "c.mp4"), seconds=2)
        row = self.send(data)
        with patch.object(video, "encode", side_effect=RuntimeError("FFmpeg failed: x")):
            video.prepare_job(row["id"])
        row = attachments.get(row["id"])
        meta = video.load_meta(row)
        self.assertEqual(meta["state"], "failed")
        self.assertIn("could not be prepared", attachments.card(row, 1)["warning"])
        self.assertIsNone(row["original"])

    def test_cleanup_of_old_unsent_and_dropped_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_clip(os.path.join(tmp, "c.mp4"), seconds=2)
        row = self.send(data)
        uid = video.start_upload("half.mp4", 100, 1)["upload_id"]
        video.add_piece(uid, 0, b"x" * 10)
        later = datetime.now() + timedelta(days=2)
        self.assertEqual(attachments.cleanup_unsent(now=later), 1)
        self.assertIsNone(attachments.get(row["id"]))
        self.assertEqual(self.files(), [])


class SendTests(TempStore):
    def test_video_not_prepared_cant_be_sent(self):
        # (Phase 3: a ready one can - test_video_prompt.)
        conn = store._connect_db()
        c = conn.cursor()
        store._ensure_attachments_table(c)
        c.execute("INSERT INTO attachments (conversation_id, kind, name, created_at, tokens) "
                  "VALUES (1, 'video', 'a.mp4', '2026-11-03 10:00:00', 2400)")
        vid = c.lastrowid
        conn.commit()
        conn.close()
        self.assertIn("still being prepared", api._check_attachments_for_send([vid], "look"))
        self.assertEqual(attachments._cost(attachments.get(vid)), 2400)


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        p = patch.dict(os.environ, {"AMADEUS_SPEECH_MODEL_DIR": str(self.root / "model"),
                                    "AMADEUS_SPEECH_PYTHON": "", "AMADEUS_SPEECH_DEVICE": ""})
        p.start()
        self.addCleanup(p.stop)
        transcribe._STATE.update(state="", done=0, message="")

    def fake_env(self, backend_env, others, mac=False):
        envs = self.root / "envs"
        for name in [backend_env] + others:
            exe = envs / name / ("bin/python" if mac else "python.exe")
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("")
        return str(envs / backend_env / ("bin/python" if mac else "python.exe"))

    def test_voice_python_next_to_the_backend(self):
        exe = self.fake_env("amadeus-test", ["GPTSoVits-test", "GPTSoVits"])
        with patch.object(sys, "executable", exe):
            self.assertTrue(transcribe.voice_python().replace("\\", "/").endswith(
                "envs/GPTSoVits-test/python.exe"))
        self.root = self.root / "mac"
        exe = self.fake_env("amadeus", ["GPTSoVITS"], mac=True)
        with patch.object(sys, "executable", exe):
            self.assertTrue(transcribe.voice_python().replace("\\", "/").lower().endswith(
                "gptsovits/bin/python"))
        with patch.object(sys, "executable", str(self.root / "python.exe")):
            self.assertIsNone(transcribe.voice_python())

    def files(self):
        return {name: (b"x" * size if name != "model.bin" else b"m" * 50)
                for name, size, _sha in transcribe.MODEL["files"]}

    def test_download_checks_and_goes_on(self):
        files = self.files()
        model = {"name": "small", "repo": "r", "revision": "v",
                 "files": [(n, len(b), hashlib.sha256(b).hexdigest()) for n, b in files.items()]}

        class Resp:
            def __init__(self, body, code):
                self.body, self.status_code = body, code

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_content(self, n):
                yield self.body

        asked = []

        class Session:
            @staticmethod
            def get(url, headers=None, stream=True, timeout=60):
                name = url.rsplit("/", 1)[1]
                start = int(headers["Range"][6:-1]) if headers else 0
                asked.append((name, start))
                return Resp(files[name][start:], 206 if start else 200)
        folder = self.root / "model"
        folder.mkdir()
        (folder / "model.bin.part").write_bytes(b"m" * 20)        # a broken-off download
        with patch.object(transcribe, "MODEL", model):
            transcribe._download(Session)
            self.assertTrue(transcribe.model_ready())
        self.assertIn(("model.bin", 20), asked)
        # A damaged file is thrown away, never marked ready.
        files["config.json"] = b"y" * len(files["config.json"])
        (folder / "config.json").unlink()
        with patch.object(transcribe, "MODEL", model):
            with self.assertRaises(RuntimeError):
                transcribe._download(Session)
            self.assertFalse(transcribe.model_ready())

    def test_status(self):
        with patch.object(transcribe, "voice_python", return_value=None):
            self.assertEqual(transcribe.status()["state"], "unavailable")
        with patch.object(transcribe, "voice_python", return_value="py"):
            self.assertEqual(transcribe.status()["state"], "missing")
            with patch.object(transcribe, "model_ready", return_value=True):
                self.assertEqual(transcribe.status()["state"], "ready")
        self.assertEqual(transcribe.status()["total"], transcribe.MODEL_BYTES)

    def test_card_or_processor(self):
        # The device choice asks timing.gpu_memory: patch THE object the
        # code under test really uses (transcribe.timing), not whatever
        # sys.modules happens to hold for "timing" when the suite re-
        # imported it.
        with patch.object(transcribe.timing, "gpu_memory", return_value=(5.5, 8.0)):
            self.assertEqual(transcribe.pick_device(), "cuda")
        with patch.object(transcribe.timing, "gpu_memory", return_value=(7.0, 8.0)):
            self.assertEqual(transcribe.pick_device(), "cpu")     # her model fills the card
        with patch.object(transcribe.timing, "gpu_memory", return_value=None):
            self.assertEqual(transcribe.pick_device(), "cpu")
        with patch.dict(os.environ, {"AMADEUS_SPEECH_DEVICE": "cpu"}), \
                patch.object(transcribe.timing, "gpu_memory", return_value=(0.0, 32.0)):
            self.assertEqual(transcribe.pick_device(), "cpu")

    def test_helper_result_and_card_fallback(self):
        good = json.dumps({"language": "ms", "segments": [
            {"start": 12.04, "end": 13.5, "text": " Oh habis? Itu saja aja"},
            {"start": 14, "end": 14.2, "text": "  "}]}).encode()
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[3])
            return _proc(1, b"", b"CUDA out of memory") if cmd[3] == "cuda" else _proc(0, b"log\n" + good)
        with patch.object(transcribe, "voice_python", return_value="py"), \
                patch.object(transcribe, "model_ready", return_value=True), \
                patch.object(transcribe, "pick_device", return_value="cuda"), \
                patch("subprocess.run", side_effect=run):
            out = transcribe.transcribe("a.wav", 14)
        self.assertEqual(calls, ["cuda", "cpu"])
        self.assertEqual((out["language"], out["device"]), ("ms", "cpu"))
        self.assertEqual(out["segments"], [{"start": 12.0, "end": 13.5, "text": "Oh habis? Itu saja aja"}])

    def test_installers_download_the_same_model(self):
        # MH 2026-11-03: the installers get the speech model, no question.
        rev = transcribe.MODEL["revision"]
        for script in ("install_windows.ps1", "install_macos.sh"):
            text = (ROOT.parent / "scripts" / script).read_text(encoding="utf-8")
            self.assertIn(rev, text, script)
            self.assertIn("Systran/faster-whisper-small", text, script)
            self.assertIn("speech" + ("\\" if script.endswith("ps1") else "/") + "faster-whisper-small",
                          text, script)
            for name, size, sha in transcribe.MODEL["files"]:
                self.assertIn(name, text, script)
                self.assertIn(str(size), text, script)
                self.assertIn(sha, text, script)

    def test_mac_installer_sets_up_ffmpeg(self):
        # 2026-11-06 (MH): the Mac installer only checked for FFmpeg - a Mac
        # without one could not prepare any video. (A) pinned signed build,
        # (B) conda-forge when that server is down; never fatal.
        text = (ROOT.parent / "scripts" / "install_macos.sh").read_text(encoding="utf-8")
        block = text[text.index("# --- 2e. FFmpeg"):text.index("# --- 2f.")]
        self.assertIn('FFMPEG_BIN="$TOOLS_DIR/ffmpeg/bin"', block)  # where video._tool looks
        for line in ("arm64/1789931890_9.0.2/ffmpeg.zip 28395699 "
                     "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924",
                     "arm64/1789931890_9.0.2/ffprobe.zip 28317701 "
                     "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6",
                     "amd64/1789931006_9.0.2/ffmpeg.zip 33816391 "
                     "7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4",
                     "amd64/1789931006_9.0.2/ffprobe.zip 33719233 "
                     "2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd"):
            self.assertIn(line, block)
        self.assertIn('shasum -a 256 "$ff_zip"', block)
        self.assertIn("-c conda-forge --override-channels", block)
        self.assertIn('ffmpeg_works "$FFMPEG_BIN"', block)
        self.assertNotIn("die ", block)

    def test_routes(self):
        client = api.application.test_client()
        with patch.object(transcribe, "voice_python", return_value=None):
            self.assertEqual(client.get("/attachments/speech-model").get_json()["state"], "unavailable")
            self.assertEqual(client.post("/attachments/speech-model").get_json()["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()

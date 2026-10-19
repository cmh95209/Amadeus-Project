# -*- coding: utf-8 -*-
"""Offline checks for the speed-work stopwatch (backend/timing.py,
local-llm-plan §10 Stage 1). Measure-only: these check the log lines and,
above all, that a broken timing report can never break a reply. Covers:
  1. llama-server "timings" and cloud "usage" blocks -> plain summaries;
  2. the WAV header -> header length + bytes per second;
  3. a voice line streamed in chunks: first sound after the header, seconds
     of audio, "Enter -> first sound" for a known reply;
  4. junk input everywhere -> no exception;
  5. the model client hands every raw answer to the stopwatch.
"""
import io
import struct
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import timing  # noqa: E402


def _wav_header(rate=32000, channels=1, bits=16):
    per_second = rate * channels * bits // 8
    return (b"RIFF" + struct.pack("<I", 0) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, per_second,
                                    channels * bits // 8, bits)
            + b"data" + struct.pack("<I", 0))


def _capture(fn, *args, **kwargs):
    out = io.StringIO()
    with redirect_stdout(out):
        fn(*args, **kwargs)
    return out.getvalue()


class ServerReportTests(unittest.TestCase):
    def test_llama_server_timings(self):
        r = timing.parse_server_report({"timings": {
            "cache_n": 3829, "prompt_n": 1204, "prompt_ms": 210.0,
            "predicted_n": 168, "predicted_ms": 1420.0}})
        self.assertEqual(r["kind"], "local")
        text = timing.describe_reports([r])
        self.assertIn("1,204 new + 3,829 remembered", text)
        self.assertIn("wrote 168 pieces in 1.42 s (118 per second)", text)

    def test_cloud_usage_with_and_without_cache(self):
        a = timing.parse_server_report({"usage": {
            "prompt_tokens": 5000, "completion_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 4096}}})
        b = timing.parse_server_report({"usage": {"prompt_tokens": 10, "completion_tokens": 2}})
        self.assertIn("4,096 cached", timing.describe_reports([a]))
        self.assertNotIn("cached", timing.describe_reports([b]))
        self.assertIn("over 2 model calls", timing.describe_reports([a, b]))

    def test_unknown_shapes(self):
        for junk in (None, "x", 5, {}, {"timings": "x"}, {"usage": None}):
            self.assertIsNone(timing.parse_server_report(junk))
        self.assertEqual(timing.describe_reports([]), "")

    def test_reports_are_per_stage(self):
        timing.start_reports()
        timing.note_server_report({"timings": {"prompt_n": 1, "predicted_n": 1}})
        timing.note_server_report(object())  # no model_dump -> ignored
        self.assertEqual(len(timing.take_reports()), 1)
        self.assertEqual(timing.take_reports(), [])


class WavTests(unittest.TestCase):
    def test_header(self):
        self.assertEqual(timing.wav_format(_wav_header()), (44, 64000))
        self.assertIsNone(timing.wav_format(b"not a wav at all, definitely not one"))
        self.assertIsNone(timing.wav_format(b""))


class VoiceWatchTests(unittest.TestCase):
    def test_line_streamed_in_chunks(self):
        timing.start_reports()
        started = timing.now() - 2.0
        _capture(timing.line_ready, 9001, started)
        out = io.StringIO()
        with redirect_stdout(out):
            watch = timing.VoiceWatch(9001)
            header = _wav_header()
            watch.feed(header[:20])
            watch.feed(header[20:])          # header only: no sound yet
            self.assertIsNone(watch.first)
            watch.feed(b"\0" * 64000)        # 1 s of audio
            self.assertIsNotNone(watch.first)
            watch.feed(b"\0" * 64000)
            watch.finish()
            watch.finish()                   # twice = one log
        text = out.getvalue()
        self.assertIn("browser asked for her voice", text)
        self.assertIn("for 2.0 s of audio", text)
        self.assertEqual(text.count("Enter -> first sound"), 1)

    def test_no_sound_and_unknown_reply(self):
        out = io.StringIO()
        with redirect_stdout(out):
            watch = timing.VoiceWatch(None)
            watch.feed(None)
            watch.finish()
        self.assertIn("reply ?: voice produced no sound", out.getvalue())


class NeverBreaksTests(unittest.TestCase):
    def test_junk_everywhere(self):
        out = io.StringIO()
        with redirect_stdout(out):
            timing.note_server_report(12)
            timing.line_ready(1, "not a number")
            timing.english_done(1, None)
            timing.model_loaded("x", "y")
            w = timing.VoiceWatch(object())
            w.feed(12345)
            w.finish()

    def test_gpu_memory_without_nvidia(self):
        with patch("timing.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(timing.gpu_memory())
        out = _capture(timing.model_loaded, timing.now() - 1, None)
        self.assertIn("her model loaded in", out)


class ClientHookTests(unittest.TestCase):
    def test_raw_answer_reaches_the_stopwatch(self):
        import llm
        client = llm._ThoughtFreeChatOpenAI(model="x", api_key="k", base_url="http://127.0.0.1:1/v1")
        raw = {"id": "1", "object": "chat.completion", "created": 0, "model": "x",
               "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "hi"}}],
               "timings": {"prompt_n": 7, "cache_n": 3, "prompt_ms": 1.0,
                           "predicted_n": 2, "predicted_ms": 1.0}}
        timing.start_reports()
        result = client._create_chat_result(raw)
        self.assertEqual(result.generations[0].message.content, "hi")
        reports = timing.take_reports()
        self.assertEqual(reports[0]["new"], 7)

    def test_stopwatch_failure_does_not_break_the_answer(self):
        import llm
        client = llm._ThoughtFreeChatOpenAI(model="x", api_key="k", base_url="http://127.0.0.1:1/v1")
        raw = {"id": "1", "object": "chat.completion", "created": 0, "model": "x",
               "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "hi"}}]}
        with patch("timing.parse_server_report", side_effect=RuntimeError("boom")):
            result = client._create_chat_result(raw)
        self.assertEqual(result.generations[0].message.content, "hi")


if __name__ == "__main__":
    unittest.main()

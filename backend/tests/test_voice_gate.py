"""Her voice and background model work take turns (2026-11-08).

A tester's 6 GB card runs her voice on the processor; a model call beside it
(English line, rolling summary, trust re-check) pushed the voice past real
time and she stuttered. voice_gate.py knows when a line is being made;
background jobs wait for silence, and in "early voice" mode the English line
waits until she has finished. Only with AMADEUS_VOICE_ON_CPU=1.
"""

from pathlib import Path
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = str(Path(__file__).resolve().parent.parent)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import voice_gate  # noqa: E402
from test_ceremony import _FreshAppTests  # noqa: E402 - the shared harness


class _GateTests(unittest.TestCase):
    def setUp(self):
        voice_gate._reset_for_tests()
        self.addCleanup(voice_gate._reset_for_tests)

    def processor(self, on=True):
        p = patch.dict(os.environ, {"AMADEUS_VOICE_ON_CPU": "1" if on else ""})
        p.start()
        self.addCleanup(p.stop)


class GateTests(_GateTests):
    def test_card_voice_never_waits(self):
        self.processor(False)
        voice_gate.expect("a")
        started = time.monotonic()
        self.assertTrue(voice_gate.wait_quiet(timeout=5, settle=5))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_waits_while_a_line_is_expected_or_made(self):
        self.processor()
        voice_gate.expect("a")
        self.assertTrue(voice_gate.busy())
        self.assertFalse(voice_gate.wait_quiet(timeout=0.2, settle=0))
        voice_gate.started("a")
        self.assertFalse(voice_gate.wait_quiet(timeout=0.2, settle=0))
        threading.Timer(0.2, voice_gate.finished, args=("a",)).start()
        self.assertTrue(voice_gate.wait_quiet(timeout=5, settle=0))
        self.assertFalse(voice_gate.busy())

    def test_a_line_never_asked_for_stops_counting(self):
        self.processor()
        with patch.object(voice_gate, "EXPECT_GRACE_S", 0.2):
            voice_gate.expect("lost")
            self.assertTrue(voice_gate.wait_quiet(timeout=5, settle=0))

    def test_a_line_being_made_does_not_expire(self):
        self.processor()
        with patch.object(voice_gate, "EXPECT_GRACE_S", 0.1):
            voice_gate.expect("a")
            voice_gate.started("a")
            time.sleep(0.2)
            self.assertTrue(voice_gate.busy())

    def test_wait_for_one_line(self):
        voice_gate.expect("a")
        voice_gate.expect("b")
        voice_gate.started("a")
        threading.Timer(0.2, voice_gate.finished, args=("a",)).start()
        self.assertTrue(voice_gate.wait_for("a", timeout=5))
        self.assertTrue(voice_gate.busy())          # "b" still expected
        self.assertFalse(voice_gate.wait_for("b", timeout=0.1))
        self.assertTrue(voice_gate.wait_for(None))


class EnglishLineTests(_GateTests):
    def turn(self):
        import chat
        return chat.VoiceFirstTurn(SimpleNamespace(assistant_reply_JPS="うん。"), 1, 2, 1)

    def waiter(self, turn):
        done = threading.Event()
        threading.Thread(target=lambda: (turn._wait_for_voice(), done.set()),
                         daemon=True).start()
        return done

    def test_early_voice_on_processor_holds_the_english_line(self):
        self.processor()
        turn = self.turn()
        voice_gate.expect("s1")
        done = self.waiter(turn)
        turn.voice_ready("s1", hold_english=True)
        voice_gate.started("s1")
        self.assertFalse(done.wait(0.3))
        voice_gate.finished("s1")
        self.assertTrue(done.wait(3))

    def test_voice_with_the_english_line_does_not_wait(self):
        # Default mode: the page asks for her voice only AFTER the English
        # line - holding it would deadlock until the cap.
        self.processor()
        turn = self.turn()
        voice_gate.expect("s1")
        done = self.waiter(turn)
        turn.voice_ready("s1", hold_english=False)
        self.assertTrue(done.wait(3))

    def test_card_voice_does_not_wait(self):
        self.processor(False)
        turn = self.turn()
        voice_gate.expect("s1")
        done = self.waiter(turn)          # voice_ready never called
        self.assertTrue(done.wait(1))


class SpeechRouteTests(_FreshAppTests):
    def setUp(self):
        super().setUp()
        voice_gate._reset_for_tests()
        self.addCleanup(voice_gate._reset_for_tests)

    def test_the_route_reports_length_and_marks_the_line_done(self):
        self.api._register_speech("sp1", "こんにちは。", None)
        self.assertTrue(voice_gate.busy())
        seen = []

        def chunks(text, save_path=None):
            seen.append(voice_gate.busy())
            yield b"RIFF....WAVE"
            yield b"\x00\x00" * 8

        with patch.object(self.api, "streamVoiceChunks", chunks):
            res = self.client.get("/speech/sp1")
            body = res.get_data()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["X-Amadeus-Voice-Chars"], "6")
        self.assertIn("X-Amadeus-Voice-Chars", res.headers["Access-Control-Expose-Headers"])
        self.assertTrue(body.startswith(b"RIFF"))
        self.assertEqual(seen, [True])    # counted as "being made" meanwhile
        res.close()
        self.assertFalse(voice_gate.busy())

    def test_a_failed_line_is_marked_done(self):
        self.api._register_speech("sp2", "こんにちは。", None)

        def broken(text, save_path=None):
            raise RuntimeError("engine down")
            yield b""  # pragma: no cover

        with patch.object(self.api, "streamVoiceChunks", broken):
            res = self.client.get("/speech/sp2")
        self.assertEqual(res.status_code, 502)
        self.assertFalse(voice_gate.busy())

    def test_the_page_says_when_her_voice_starts_early(self):
        calls = []
        turn = SimpleNamespace(
            pack=SimpleNamespace(assistant_reply_JPS="うん。"), user_id=1,
            assistant_id=2, conv_id=1, wait_en=lambda timeout=0: "Yes.",
            voice_ready=lambda sid, hold_english=False: calls.append((sid, hold_english)))
        with patch.object(self.api, "has_api_key", return_value=True), \
                patch.object(self.chat.store, "load_web_access", return_value=False), \
                patch.object(self.chat, "getOutputPackedVoiceFirst", return_value=turn), \
                patch.object(self.api.memory, "load_message_doc_reads", return_value=[]):
            for early in (True, False):
                res = self.client.post("/", json={"user_input": "hi", "voice_early": early})
                res.get_data()
        self.assertEqual([hold for _sid, hold in calls], [True, False])
        self.assertTrue(all(sid for sid, _hold in calls))


if __name__ == "__main__":
    unittest.main()

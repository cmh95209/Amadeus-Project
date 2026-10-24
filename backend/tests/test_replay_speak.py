"""Replay without a saved clip (2026-10-24, VM test).

An interrupted line is never saved (the stream is cut, tts.py throws the
partial recording away), and the old re-make route renders the WHOLE clip
before the first sound - minutes on a processor. /memory/<id>/speak hands
back the saved clip when there is one, else a single-use speech id for the
same streaming path her live lines use (heard to the end, it is saved).
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402 - the shared harness


class SpeakRouteTests(_FreshAppTests):
    def setUp(self):
        super().setUp()
        # Clips go to the temp folder, never the project's generated/.
        self.clips = Path(self.directory.name) / "generated"
        patcher = patch.object(self.api, "_GENERATED_DIR", self.clips)
        patcher.start()
        self.addCleanup(patcher.stop)
        pack = self.chat.AmadeusPack(assistant_reply_ENG="Hello.",
                                     assistant_reply_JPS="こんにちは。")
        self.line_id, _conv = self.api._store_greeting_line(pack)

    def speak(self, message_id):
        return self.client.post(f"/memory/{message_id}/speak")

    def test_no_clip_streams_the_saved_line(self):
        res = self.speak(self.line_id)
        self.assertEqual(res.status_code, 200)
        speech_id = res.json["speech_id"]
        self.assertNotIn("audio_url", res.json)
        _created, text, assistant_id = self.api._speech_requests[speech_id]
        self.assertEqual((text, assistant_id), ("こんにちは。", self.line_id))

    def test_saved_clip_is_played_as_is(self):
        self.clips.mkdir(parents=True)
        (self.clips / f"voice_{self.line_id}.wav").write_bytes(b"RIFF")
        res = self.speak(self.line_id)
        self.assertEqual(res.json["audio_url"],
                         f"/message_audio/generated/voice_{self.line_id}.wav")
        self.assertNotIn("speech_id", res.json)

    def test_a_line_without_japanese_is_refused(self):
        self.assertEqual(self.speak(self.line_id + 99).status_code, 400)


if __name__ == "__main__":
    unittest.main()

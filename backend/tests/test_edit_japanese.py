"""Her Japanese line in the UI + editing her replies (2026-10-14).

Her prompt history reads the JAPANESE column of her replies, so an edit only
steers her when that line is rewritten too. Covers:
  - the message list sends "japanese" for her rows (never for the user's);
  - editing a USER message stays a plain text update (no model call);
  - editing HER message rewrites her Japanese line, clears the stale voice
    link, and the next prompt reads the edited line;
  - a failed rewrite refuses the edit and leaves the row untouched;
  - voice_line_for_edit rejects a model answer with no Japanese in it.
Every test runs on a throwaway database; the real memory.db and voice files
are never touched (_unlink_voice is patched out).
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import api
import chat
import memory as store

OLD_JA = "勘違いしないで。ただ気になっただけよ。"
OLD_EN = "Don't get the wrong idea. I was just curious."
NEW_EN = "Fine, I admit it. I missed you."
NEW_JA = "わかったわよ、認める。会いたかった。"


class _EditJapaneseBase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.mem_db = str(Path(self._dir.name) / "memory.db")
        self.unlinked = []
        self._patches = [
            patch.object(store, "PATH_TO_MEMORY", self.mem_db),
            patch.object(store, "load_active_conversation", return_value=1),
            patch.object(api, "_unlink_voice", side_effect=self.unlinked.append),
        ]
        for p in self._patches:
            p.start()
        self.user_id = store.append_message("user", "Were you waiting for me?", conversation_id=1)
        self.her_id = store.append_message("assistant", OLD_EN, conversation_id=1, japanese=OLD_JA)
        store.set_message_audio(self.her_id, f"generated/voice_{self.her_id}.wav")
        self.client = api.application.test_client()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._dir.cleanup()

    def _row(self, message_id):
        conn = sqlite3.connect(self.mem_db)
        try:
            return conn.execute(
                "SELECT content, japanese, audio FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        finally:
            conn.close()


class MessageListTests(_EditJapaneseBase):
    def test_her_rows_carry_japanese_user_rows_do_not(self):
        rows = {m["id"]: m for m in store.load_memory_raw(1)}
        self.assertEqual(rows[self.her_id]["japanese"], OLD_JA)
        self.assertTrue(rows[self.her_id]["has_japanese"])
        self.assertNotIn("japanese", rows[self.user_id])

    def test_her_row_without_japanese_sends_empty_string(self):
        bare = store.append_message("assistant", "Old reply.", conversation_id=1)
        rows = {m["id"]: m for m in store.load_memory_raw(1)}
        self.assertEqual(rows[bare]["japanese"], "")
        self.assertNotIn("has_japanese", rows[bare])


class EditRouteTests(_EditJapaneseBase):
    def test_editing_user_message_makes_no_model_call(self):
        with patch.object(chat, "voice_line_for_edit") as rewrite:
            response = self.client.patch(f"/memory/{self.user_id}", json={"content": "Hi!"})
        self.assertEqual(response.status_code, 200)
        rewrite.assert_not_called()
        self.assertEqual(self._row(self.user_id)[0], "Hi!")
        self.assertEqual(self.unlinked, [])

    def test_editing_her_message_rewrites_japanese_and_steers_prompt(self):
        with patch.object(chat, "voice_line_for_edit", return_value=NEW_JA) as rewrite:
            response = self.client.patch(f"/memory/{self.her_id}", json={"content": NEW_EN})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["japanese"], NEW_JA)
        rewrite.assert_called_once_with(NEW_EN, "Were you waiting for me?")
        content, japanese, audio = self._row(self.her_id)
        self.assertEqual((content, japanese, audio), (NEW_EN, NEW_JA, None))
        self.assertEqual(self.unlinked, [self.her_id])
        # What she reads next turn is the edited line, not the old one.
        prompt = [m["content"] for m in store.load_memory_for_prompt(1)]
        self.assertIn(NEW_JA, prompt)
        self.assertNotIn(OLD_JA, prompt)

    def test_failed_rewrite_refuses_edit_and_keeps_row(self):
        with patch.object(chat, "voice_line_for_edit", side_effect=ValueError("down")):
            response = self.client.patch(f"/memory/{self.her_id}", json={"content": NEW_EN})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Edit not saved", response.get_json()["message"])
        content, japanese, audio = self._row(self.her_id)
        self.assertEqual((content, japanese), (OLD_EN, OLD_JA))
        self.assertIsNotNone(audio)
        self.assertEqual(self.unlinked, [])

    def test_missing_message_is_404(self):
        response = self.client.patch("/memory/9999", json={"content": "x"})
        self.assertEqual(response.status_code, 404)


class VoiceLineForEditTests(unittest.TestCase):
    def _fake_llm(self, text):
        return SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=text))

    def test_returns_cleaned_japanese(self):
        with patch.object(chat, "get_llm", return_value=self._fake_llm(NEW_JA)):
            self.assertEqual(chat.voice_line_for_edit(NEW_EN, None), NEW_JA)

    def test_rejects_answer_without_japanese(self):
        with patch.object(chat, "get_llm", return_value=self._fake_llm("Sorry, I can't.")):
            with self.assertRaises(ValueError):
                chat.voice_line_for_edit(NEW_EN, None)


if __name__ == "__main__":
    unittest.main()

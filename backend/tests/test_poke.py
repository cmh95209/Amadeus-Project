"""Poke (2026-11-01, MH).

The user pokes her (the Poke button or a tap on her shoulders) and she
starts talking with one line of her own. It is stored as a "poke" line: it
can be regenerated like a greeting, but an unanswered poke line is never an
ignored greeting (the user asked for it - no hurt feelings). Repeated pokes
tell her she is being poked again; her earlier lines stay visible and no
topic change is forced (MH: she may keep the fun going).
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_greeting_regenerate import _Base, _FakeLLM  # noqa: E402


class PokeLineTests(_Base):
    def poke(self, fake=None):
        with self.patched(fake or _FakeLLM(eng="Poke line.", jps="つんつん。")):
            return self.chat.generate_greeting(mode="poke", conversation_id=self.tab_a)

    def test_poke_is_stored_as_a_poke_line(self):
        fake = _FakeLLM(eng="What, bored already?", jps="なに、もう退屈？")
        pack, pid, conv = self.poke(fake)
        self.assertEqual(conv, self.tab_a)
        self.assertEqual(self.db_row(pid)[1:3], (1, "poke"))
        text = self.prompt_text(fake)
        self.assertIn("poked you to get you talking", text)
        self.assertIn("[The user poked you - they want you to start talking.", text)
        self.assertNotIn("poked you again", text)

    def test_repeated_poke_says_so_without_forcing_a_new_topic(self):
        self.poke(_FakeLLM(eng="FIRST-POKE-LINE", jps="いち"))
        fake = _FakeLLM(eng="Again?", jps="また？")
        self.poke(fake)
        text = self.prompt_text(fake)
        self.assertIn("poked you again", text)
        self.assertIn("talking once after a poke", text)
        self.assertIn("carry on with what you were saying", text)
        self.assertNotIn("completely different topic", text)
        # Her earlier poke line stays in her history.
        self.assertIn("FIRST-POKE-LINE", text)

    def test_counts_reset_when_the_user_answers(self):
        self.poke()
        self.poke()
        self.assertEqual(self.memory.unanswered_poke_count(self.tab_a), 2)
        self.memory.append_message("user", "ok ok", conversation_id=self.tab_a)
        self.assertEqual(self.memory.unanswered_poke_count(self.tab_a), 0)

    def test_poke_lines_are_never_ignored_greetings(self):
        self.poke()
        self.poke()
        self.assertEqual(self.memory.unanswered_greeting_count(self.tab_a), 0)
        _p, _sid, _c = self.greet()
        self.assertEqual(self.memory.unanswered_greeting_count(self.tab_a), 1)

    def test_poke_does_not_touch_her_feelings(self):
        with patch.object(self.chat.feelings, "note_ignored_greeting") as note:
            self.poke()
            self.poke()
        note.assert_not_called()

    def test_a_poke_line_regenerates_as_a_poke(self):
        _p, pid, _c = self.poke()
        fake = _FakeLLM(eng="Second try.", jps="もう一回。")
        _p2, nid, _c2 = self.regen(pid, fake)
        self.assertEqual(self.db_row(nid)[2], "poke")
        self.assertIn("[The user poked you - they want you to start talking.",
                      self.prompt_text(fake))


class PokeRouteTests(_Base):
    def post(self, conv=None, ready=True, fake=None):
        conv = self.tab_a if conv is None else conv
        with self.patched(fake or _FakeLLM(eng="Hey.", jps="ねえ。")), \
                patch.object(self.api, "model_ready",
                             return_value={"ready": ready, "reason": "down"}):
            return self.client.post(f"/conversations/{conv}/poke", json={})

    def test_route_returns_her_line(self):
        res = self.post()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json["ready"])
        self.assertEqual(res.json["response"], "Hey.")
        self.assertEqual(res.json["conversation_id"], self.tab_a)
        self.assertEqual(self.db_row(res.json["assistant_id"])[2], "poke")

    def test_route_not_ready_is_no_line(self):
        res = self.post(ready=False)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json["ready"])

    def test_route_during_first_launch_is_no_line(self):
        self.memory.save_ceremony_state("")
        res = self.post()
        self.assertFalse(res.json["ready"])
        self.assertEqual(res.json["reason"], "first launch in progress")

    def test_route_unknown_conversation(self):
        self.assertEqual(self.post(conv=99999).status_code, 404)

    def test_route_records_presence(self):
        with patch.object(self.memory, "record_presence") as rec:
            self.post()
        rec.assert_called_once()


if __name__ == "__main__":
    unittest.main()

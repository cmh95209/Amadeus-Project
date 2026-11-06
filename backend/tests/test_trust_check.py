"""The background trust check also reports her feelings (2026-10-30)."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import chat
import feelings

GOOD = """TRUST: +2
MOOD: +4
IRRITATION: 0
WORRY: -3
PLAYFULNESS: 25"""


class ParseTests(unittest.TestCase):
    def test_all_five_lines(self):
        trust, changes = chat._parse_check(GOOD)
        self.assertEqual(trust, 2)
        self.assertEqual(changes, {"mood": 4, "worry": -3, "playfulness": 25})

    def test_trust_is_capped(self):
        self.assertEqual(chat._parse_check("TRUST: 9")[0], chat.TRUST_STEP_MAX)

    def test_markdown_and_order_do_not_matter(self):
        trust, changes = chat._parse_check("**WORRY:** 5\n- TRUST: -1\nmood = -2")
        self.assertEqual(trust, -1)
        self.assertEqual(changes, {"worry": 5, "mood": -2})

    def test_garbled_falls_back_to_trust_only(self):
        self.assertEqual(chat._parse_check("+1 I think it was warmer"), (1, {}))
        self.assertEqual(chat._parse_check("no idea"), (None, {}))

    def test_thinking_is_ignored(self):
        trust, _ = chat._parse_check("<thinking>TRUST: 3</thinking>\nTRUST: 1")
        self.assertEqual(trust, 1)


class RunTests(unittest.TestCase):
    def run_check(self, reply):
        sets = {}
        llm = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=reply))
        with tempfile.TemporaryDirectory() as d, \
                patch.object(chat.store, "PATH_TO_MEMORY", os.path.join(d, "memory.db")), \
                patch.object(chat.store, "load_memory_raw", lambda: [
                    {"role": "user", "content": "haha you're funny"},
                    {"role": "assistant", "content": "Hmph."}]), \
                patch.object(chat, "get_llm", lambda *a, **k: llm), \
                patch.object(chat.stats, "load_stat", lambda key: 80), \
                patch.object(chat.stats, "set_stat",
                             lambda key, v: sets.setdefault(key, v)):
            chat.run_trust_rescore()
            mood = feelings.current(path=os.path.join(d, "feelings.txt"))
        return sets, mood

    def test_feelings_applied_with_trust(self):
        sets, mood = self.run_check(GOOD)
        self.assertEqual(sets, {"trust": 82})
        self.assertAlmostEqual(mood["playfulness"], feelings.CHECK_STEP_MAX, places=0)
        self.assertAlmostEqual(mood["mood"], 4, places=0)

    def test_level_note_opens_up_with_closeness(self):
        with patch.object(chat.stats, "tier_label", lambda key, v: "X"):
            low = chat._check_level_note(30)
            familiar = chat._check_level_note(65)
            close = chat._check_level_note(82)
        self.assertIn("PLAYFULNESS stays 0", low)
        self.assertIn("Flirting", low)
        self.assertNotIn("PLAYFULNESS stays 0", familiar)
        self.assertIn("Flirting", familiar)
        self.assertEqual(close, "Their closeness level now: X.")

    def test_old_style_reply_changes_trust_only(self):
        sets, mood = self.run_check("+1")
        self.assertEqual(sets, {"trust": 81})
        self.assertEqual(round(mood["playfulness"]), 0)


if __name__ == "__main__":
    unittest.main()

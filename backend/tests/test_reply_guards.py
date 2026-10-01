# -*- coding: utf-8 -*-
"""Offline checks for the reply-completion honesty guards (2026-10-01).

Live incident 2026-10-01: asked "do you remember X?" on a web-OFF turn, the
forced AmadeusPack fell back to plain text, the model answered "Wait, let me
check." (a mid-action check promise it can never complete in this one-shot
app) and the salvage path served that dangling sentence; the memory engine
then fossilized a fabricated "24 hours" gap as an episode. These checks pin:

1. _is_check_promise triggers on pure check promises (EN + JA) and on
   NOTHING else (complete answers, questions, long replies, done-checks);
2. the pack rules carry the standing honesty lines (no mid-action endings,
   no unmeasured time claims).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chat


class CheckPromiseTests(unittest.TestCase):
    def test_triggers_on_pure_check_promises(self):
        for t in ("Wait, let me check.",
                  "Hmm, hold on. Let me check.",
                  "Let me look into that for you.",
                  "I'll check - give me a second.",
                  "One moment, just a second.",
                  "ん、待って。確認してみる。",
                  "確認してみるね。"):
            with self.subTest(text=t):
                self.assertTrue(chat._is_check_promise(t), t)

    def test_complete_answers_are_never_promises(self):
        for t in ("I checked. It was about 24 hours.",
                  "Hold on, actually I remember - it was the 14th.",
                  "Let me check - which one did you mean?",
                  "That was a long time ago; honestly I do not remember the "
                  "exact date, but we were talking about the car insurance "
                  "claim and the dashcam footage proved the other driver "
                  "was at fault.",
                  ""):
            with self.subTest(text=t):
                self.assertFalse(chat._is_check_promise(t), t)


class PackRulesHonestyTests(unittest.TestCase):
    def test_pack_rules_forbid_mid_action_endings_and_time_guesses(self):
        content = chat._PACK_RULES["content"]
        self.assertIn("Never end a reply with a promise to check", content)
        self.assertIn("guessing a number", content)


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""Offline checks for the TTS-field sanitization (assistant_reply_JPS).

The voice reads exactly what is in the JPS field, so anything that is not
clean spoken Japanese (leaked English, pack scaffolding, runaway loops) must
be stripped before it reaches GPT-SoVITS. Covers, with no server and no
network:
  1. a whole-English gloss line under a Japanese line (the old leak shape);
  2. an inline English paragraph glued onto ONE Japanese line with no line
     break (the 2026-09-16 degeneration);
  3. legitimate mixed lines (numbers, acronyms, product names) stay intact;
  4. a pure-English field is kept intact for the translation pass;
  5. _ensure_japanese spends no model call when the field still has
     Japanese after cleaning.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chat  # noqa: E402


class CleanTtsTextTests(unittest.TestCase):
    def test_whole_english_line_under_japanese_is_dropped(self):
        text = ("今日はレングングの天気は晴れよ。\n"
                "The weather in Lenggong is clear today.")
        out = chat._clean_tts_text(text)
        self.assertNotIn("The weather", out)
        self.assertIn("晴れ", out)

    def test_inline_english_paragraph_on_one_line_is_stripped(self):
        # The 2026-09-16 incident shape: one single line, no line breaks,
        # Japanese first then a long English tail.
        text = ("うん、その判断で正解よ。エアコンをつけて、ドアや窓を完全に閉めなさい。"
                "AQI160のスモッグは室内に入れたくないわ。また話そう。我是这里 from  always here. "
                "You can talk to me anytime. I'm glad you're safe and sound now. "
                "Take care of yourself today. Rest well. Sweet dreams!")
        out = chat._clean_tts_text(text)
        self.assertNotIn("You can talk to me", out)
        self.assertNotIn("Take care of yourself", out)
        self.assertNotIn("always here", out)
        self.assertNotIn("Sweet dreams", out)
        self.assertIn("エアコンをつけて", out)
        self.assertIn("AQI160", out)  # legitimate acronym/number survives

    def test_legitimate_mixed_tokens_are_kept(self):
        text = "PM2.5が62マイクログラムで、AQIは160よ。AIの予測も合ってたわ。"
        self.assertEqual(chat._clean_tts_text(text), text)

    def test_short_latin_name_between_japanese_is_kept(self):
        text = "私はAmadeusよ。何か用があるなら言ってみて。"
        self.assertEqual(chat._clean_tts_text(text), text)

    def test_pure_english_field_is_kept_for_translation_pass(self):
        text = "The air quality is bad today, stay indoors."
        self.assertEqual(chat._clean_tts_text(text), text)

    def test_plain_japanese_is_untouched(self):
        text = "うん、わかってるわ。少し待っててね。"
        self.assertEqual(chat._clean_tts_text(text), text)


class EnsureJapaneseTests(unittest.TestCase):
    def test_mixed_field_does_not_spend_a_model_call(self):
        pack = chat.AmadeusPack(
            assistant_reply_JPS="雨の予報ね。The rain is coming soon, take an umbrella.",
            assistant_reply_ENG="Rain is forecast. Take an umbrella.")

        class _NoInvoke:
            def invoke(self, *a, **k):
                raise AssertionError("no model call expected")

        out = chat._ensure_japanese(pack, _NoInvoke())
        self.assertNotIn("The rain is coming soon", out.assistant_reply_JPS)
        self.assertIn("雨の予報", out.assistant_reply_JPS)


if __name__ == "__main__":
    unittest.main()

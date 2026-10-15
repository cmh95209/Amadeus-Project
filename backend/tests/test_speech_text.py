# -*- coding: utf-8 -*-
"""Offline checks for speech_text.to_spoken (layer 1: numbers).

GPT-SoVITS often reads a number in a Japanese line with its English model,
or turns a decimal point / thousands comma into a pause. to_spoken rewrites
numbers, units, times and dates into ordinary Japanese notation (digits +
kanji counters + katakana units) that the engine reads naturally - the
spelling the user approved by ear on 2026-10-15. Covers, with no server and
no network:
  1. the weather / time / date / money examples from the listening test;
  2. the engine-specific fixes (no commas, no decimal points, 零時);
  3. text that must stay untouched (no digits, numbers glued to English);
  4. tts._request_payload sends the rewritten text, and a rewriting error
     falls back to the raw text instead of silencing her.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import speech_text  # noqa: E402
import tts  # noqa: E402
from speech_text import to_spoken  # noqa: E402


class ListeningTestSentencesTests(unittest.TestCase):
    # The five sentences of the A/B listening test, shown -> spoken.
    CASES = {
        "今日の東京は最高23.5℃、最低は-3℃よ。降水確率は60%だから、傘を持っていきなさい。":
            "今日の東京は最高23点5度、最低はマイナス3度よ。降水確率は60パーセントだから、傘を持っていきなさい。",
        "風速5m/s、気圧は1013hPa。PM2.5の濃度も低いわね。":
            "風速5メートル、気圧は1013ヘクトパスカル。" + speech_text.PM25_SPOKEN + "の濃度も低いわね。",
        "会議は10月15日の14:30からよ。忘れないで。":
            "会議は10月15日の14時30分からよ。忘れないで。",
        "それ、1,500円もしたの?$20なら安いけど。":
            "それ、1500円もしたの?20ドルなら安いけど。",
        "2026年の4月1日に、3人で行ったでしょ。":
            "2026年の4月1日に、3人で行ったでしょ。",
    }

    def test_sentences(self):
        for before, after in self.CASES.items():
            with self.subTest(before=before):
                self.assertEqual(to_spoken(before), after)


class RewriteRulesTests(unittest.TestCase):
    CASES = {
        "14:30": "14時30分", "14:00": "14時", "0:05": "零時5分", "9:05:30": "9時5分30秒",
        "10/15": "10月15日", "3/40": "40ぶんの3",
        "2026-10-15": "2026年10月15日", "2026/4/1": "2026年4月1日",
        "23.5°C": "23点5度", "30°": "30度", "75°F": "華氏75度",
        "2.5%": "2点5パーセント", "60 %": "60パーセント",
        "1,234,567円": "1234567円", "-3℃": "マイナス3度",
        "10 km/h": "時速10キロ", "時速60km": "時速60キロ", "5m/s": "秒速5メートル",
        "3kg": "3キロ", "500mL": "500ミリリットル", "16GB": "16ギガバイト",
        "¥1500": "1500円", "€5": "5ユーロ",
        "20〜25℃": "20から25度", "10-20%": "10から20パーセント",
        "090-1234-5678": "ゼロキューゼロのイチニーサンヨンのゴーロクナナハチ",
        "２３．５℃": "23点5度", "１４：３０": "14時30分",
    }

    def test_rules(self):
        for before, after in self.CASES.items():
            with self.subTest(before=before):
                self.assertEqual(to_spoken(before), after)

    def test_kanji_counters_are_left_for_the_engine(self):
        # pyopenjtalk already reads these with the right sound changes.
        for text in ("6分", "300本", "毎月1日", "4人", "1.5万人"):
            with self.subTest(text=text):
                self.assertEqual(to_spoken(text), text.replace(".", "点"))

    def test_no_comma_or_decimal_point_reaches_the_engine(self):
        out = to_spoken("人口は1,234,567人、成長率は3.25%よ。")
        self.assertNotRegex(out, r"\d[.,]\d")


class UntouchedTextTests(unittest.TestCase):
    def test_no_digits_is_returned_as_is(self):
        for text in ("", "こんにちは。", "ＡＢＣ！", "今日は晴れよ。"):
            with self.subTest(text=text):
                self.assertEqual(to_spoken(text), text)

    def test_numbers_glued_to_english_words_stay(self):
        for text in ("GPT-4", "iPhone15", "COVID-19"):
            with self.subTest(text=text):
                self.assertEqual(to_spoken(text), text)


class TtsHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        self.ref = Path(tmp.name)
        self.addCleanup(self.ref.unlink)

    def test_payload_carries_the_spoken_form(self):
        with mock.patch.object(tts, "REF_WAV", self.ref):
            payload = tts._request_payload("気温は23.5℃よ。")
        self.assertEqual(payload["text"], "気温は23点5度よ。")

    def test_rewrite_error_falls_back_to_raw_text(self):
        def boom(text):
            raise RuntimeError("bug")
        with mock.patch.object(tts, "REF_WAV", self.ref), \
                mock.patch.object(tts, "to_spoken", boom):
            payload = tts._request_payload("気温は23℃よ。")
        self.assertEqual(payload["text"], "気温は23℃よ。")


if __name__ == "__main__":
    unittest.main()

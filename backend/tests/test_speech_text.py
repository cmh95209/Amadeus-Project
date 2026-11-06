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
  4. layer 2: the saved name is spoken from its kana reading;
  5. layer 3: English words -> katakana (pronunciation lists, acronyms, e2k);
  6. tts._request_payload sends the rewritten text, and a rewriting error
     falls back to the raw text instead of silencing her.
The module never touches the live data folder (the personal pronunciation
list points at a temp file) and stands in for e2k, so results do not depend
on whether it is installed; one test uses the real e2k when it is.
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

_REAL_E2K = speech_text._e2k_reading
_patches = []


def setUpModule():
    tmp = tempfile.mkdtemp()
    _patches.append(mock.patch.object(speech_text, "USER_LIST",
                                      str(Path(tmp) / "pronunciations.txt")))
    _patches.append(mock.patch.object(speech_text, "_e2k_reading", lambda word: ""))
    for p in _patches:
        p.start()


def tearDownModule():
    for p in reversed(_patches):
        p.stop()


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

    def test_numbers_glued_to_english_words_go_with_the_word(self):
        # Layer 3 converts the word, then the number is read with it.
        cases = {"GPT-4": "ジーピーティーフォー", "PS5": "ピーエスファイブ",
                 "iPhone15": "アイフォーン15", "COVID-19": "コビッド19"}
        for before, after in cases.items():
            with self.subTest(text=before):
                self.assertEqual(to_spoken(before), after)


class EnglishWordTests(unittest.TestCase):
    """Layer 3: English words in her line are spoken in katakana."""

    def test_built_in_list(self):
        self.assertEqual(to_spoken("Googleで調べたら、NASAの発表があったわ。"),
                         "グーグルで調べたら、ナサの発表があったわ。")
        self.assertEqual(to_spoken("GOOGLEとSteins;Gate"), "グーグルとシュタインズ・ゲート")
        # Whole words only: a longer word is not a list entry.
        self.assertEqual(to_spoken("Googlerの話"), "Googlerの話")

    def test_user_list_wins_and_template_is_created(self):
        path = Path(speech_text.USER_LIST)
        if path.exists():
            path.unlink()
        to_spoken("こんにちは、Googleさん。")
        self.assertTrue(path.exists())
        self.assertIn("word = カタカナ", path.read_text(encoding="utf-8"))
        with path.open("a", encoding="utf-8") as f:
            f.write("Google = ググル\nnotkana = abc\n")
        try:
            self.assertEqual(to_spoken("Googleの話"), "ググルの話")
            self.assertEqual(to_spoken("notkanaの話"), "notkanaの話")  # bad entry ignored
        finally:
            path.unlink()

    def test_acronyms_are_spelled(self):
        cases = {"GPUが熱い": "ジーピーユーが熱い", "Aランクよ": "エーランクよ",
                 "AIの話": "エーアイの話", "GPT-40": "ジーピーティー40", "OKよ": "オーケーよ"}
        for before, after in cases.items():
            with self.subTest(text=before):
                self.assertEqual(to_spoken(before), after)

    def test_e2k_for_other_words(self):
        fake = {"steam": "スチーム", "bogus": "not kana"}.get
        with mock.patch.object(speech_text, "_e2k_reading",
                               lambda w: (lambda r: r if r and speech_text.is_kana(r) else "")(fake(w.lower()))):
            self.assertEqual(to_spoken("Steamで買った"), "スチームで買った")
            self.assertEqual(to_spoken("Bogusの話"), "Bogusの話")

    def test_without_e2k_words_keep_their_spelling(self):
        self.assertEqual(to_spoken("Steamで買った"), "Steamで買った")

    def test_long_english_phrase_is_left_alone(self):
        self.assertEqual(to_spoken("それはI think it's fineって意味ね。"),
                         "それはI think it's fineって意味ね。")

    def test_units_and_pm25_stay_with_the_number_layer(self):
        self.assertEqual(to_spoken("16GBで、気温は23°C、PM2.5は低い。"),
                         "16ギガバイトで、気温は23度、" + speech_text.PM25_SPOKEN + "は低い。")
        self.assertEqual(to_spoken("10 km/hで5m/s"), "時速10キロで秒速5メートル")

    @unittest.skipUnless(__import__("importlib").util.find_spec("e2k"), "e2k not installed")
    def test_real_e2k(self):
        reading = _REAL_E2K("steam")
        self.assertTrue(speech_text.is_kana(reading), reading)
        self.assertEqual(_REAL_E2K("steam"), reading)          # cached, same answer


class NameReadingTests(unittest.TestCase):
    """Layer 2: the user's saved name is spoken from its kana reading."""

    def test_latin_name_is_swapped_as_a_whole_word(self):
        self.assertEqual(to_spoken("Alex、おかえり。Alexさんは元気?", "Alex", "アレックス"),
                         "アレックス、おかえり。アレックスさんは元気?")
        self.assertEqual(to_spoken("ALEXはどう?", "Alex", "アレックス"), "アレックスはどう?")
        # A longer word that merely contains the name is left alone.
        self.assertEqual(to_spoken("Alexanderの話", "Alex", "アレックス"), "Alexanderの話")

    def test_name_and_numbers_together(self):
        self.assertEqual(to_spoken("Alex、14:30に来て。", "Alex", "アレックス"),
                         "アレックス、14時30分に来て。")

    def test_no_reading_or_no_name_changes_nothing(self):
        self.assertEqual(to_spoken("Alex、おかえり。", "Alex", ""), "Alex、おかえり。")
        self.assertEqual(to_spoken("Alex、おかえり。", "", "アレックス"), "Alex、おかえり。")

    def test_kanji_name_is_swapped_too(self):
        self.assertEqual(to_spoken("健、おかえり。", "健", "アレックス"), "アレックス、おかえり。")

    def test_rule_reading(self):
        self.assertEqual(speech_text.rule_reading("JD"), "ジェーディー")
        self.assertEqual(speech_text.rule_reading("アレックス"), "アレックス")
        self.assertEqual(speech_text.rule_reading("ほん"), "ほん")
        for needs_model in ("Alex", "Sam", "健", "", "M"):
            with self.subTest(name=needs_model):
                self.assertEqual(speech_text.rule_reading(needs_model), "")

    def test_is_kana(self):
        self.assertTrue(speech_text.is_kana("アレックス"))
        self.assertTrue(speech_text.is_kana("アレックス・サム"))
        for bad in ("", "Alex", "アレックスAlex", "健", "アレックス。"):
            with self.subTest(text=bad):
                self.assertFalse(speech_text.is_kana(bad))


class MisreadJapaneseTests(unittest.TestCase):
    """Words pyopenjtalk misreads are sent in kana (言ってね -> ゲンッテネ)."""

    def test_itte_is_sent_in_kana(self):
        cases = {
            "言ってね。": "いってね。",
            "何かあったら言ってね。": "何かあったらいってね。",
            "ちゃんと言ってね。": "ちゃんといってね。",
            "時々言ってね": "時々いってね",
            "「言って」": "「いって」",
        }
        for shown, spoken in cases.items():
            with self.subTest(text=shown):
                self.assertEqual(to_spoken(shown), spoken)

    def test_kanji_compounds_are_left_alone(self):
        for text in ("発言ってなに？", "方言って面白い", "一言ってね", "伝言ってね"):
            with self.subTest(text=text):
                self.assertEqual(to_spoken(text), text)

    def test_other_forms_are_untouched(self):
        for text in ("言ったでしょ。", "言う", "言わないで"):
            with self.subTest(text=text):
                self.assertEqual(to_spoken(text), text)

    def test_works_with_numbers_and_english(self):
        self.assertEqual(to_spoken("14:30に言ってね。"), "14時30分にいってね。")
        self.assertEqual(to_spoken("OKなら言ってね。"), "オーケーならいってね。")


class TtsHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        self.ref = Path(tmp.name)
        self.addCleanup(self.ref.unlink)
        # Never read the live app's saved name from a test.
        patcher = mock.patch.object(tts, "_saved_name", return_value=("Alex", "アレックス"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_payload_carries_the_spoken_form(self):
        with mock.patch.object(tts, "REF_WAV", self.ref):
            payload = tts._request_payload("Alex、気温は23.5℃よ。")
        self.assertEqual(payload["text"], "アレックス、気温は23点5度よ。")

    def test_payload_uses_the_steadier_voice_settings(self):
        # Setting "B", picked by ear 2026-10-16; the seed stays random.
        with mock.patch.object(tts, "REF_WAV", self.ref):
            payload = tts._request_payload("こんにちは。")
        self.assertEqual((payload["temperature"], payload["top_k"]), (0.7, 10))
        self.assertEqual(payload["seed"], -1)

    def test_rewrite_error_falls_back_to_raw_text(self):
        def boom(*args):
            raise RuntimeError("bug")
        with mock.patch.object(tts, "REF_WAV", self.ref), \
                mock.patch.object(tts, "to_spoken", boom):
            payload = tts._request_payload("気温は23℃よ。")
        self.assertEqual(payload["text"], "気温は23℃よ。")


if __name__ == "__main__":
    unittest.main()

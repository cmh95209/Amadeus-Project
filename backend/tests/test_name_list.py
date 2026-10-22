# -*- coding: utf-8 -*-
"""Offline checks for the shared name list (option C, 2026-10-22).

One list - the built-in pronunciation list plus the user's own entries
(Settings -> Names she knows, data/pronunciations.txt) - keeps a name the same
everywhere. Covers, with no server and no network (the test_ceremony harness:
temp data dir, llm/tts stubbed):
  1. the file: save/load round trip, comment header kept, bad rows dropped;
  2. her written Japanese line: listed names in Latin letters become katakana,
     unlisted words and quoted English phrases stay;
  3. the translator hint: only the names her line contains, whole katakana
     words only (クリス, not クリスマス), the user's entry first;
  4. her prompt: the user's names in the fixed voice rules, nothing when empty;
  5. the routes: round trip, an empty row skipped, a bad reading refused.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402


class NameListTests(_FreshAppTests):
    def setUp(self):
        super().setUp()
        self.st = self.chat.speech_text

    def test_round_trip_keeps_header_and_drops_bad_rows(self):
        saved = self.st.save_user_names([("Ineffa", "イネファ"), ("Bad", "bad"),
                                         ("", "アイ"), ("A=B", "エー"), ("ineffa", "イネッファ")])
        self.assertEqual(saved, [("Ineffa", "イネファ")])
        self.assertEqual(self.st.load_user_names(), [("Ineffa", "イネファ")])
        text = Path(self.st.USER_LIST).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# How she says particular words"))
        self.assertIn("Ineffa = イネファ", text)

    def test_written_line_swaps_listed_names_only(self):
        self.st.save_user_names([("Ineffa", "イネファ")])
        clean = self.chat._clean_tts_text
        self.assertEqual(clean("INEFFAが焼いたの。"), "イネファが焼いたの。")
        self.assertEqual(clean("Python 3.14のf-stringsね。"), "パイソン 3.14のf-stringsね。")
        self.assertEqual(self.st.swap_listed_words("「I love Ineffa so much」とIneffa"),
                         "「I love Ineffa so much」とイネファ")

    def test_translator_hint_names_whole_words_in_her_line(self):
        self.st.save_user_names([("Ineffa", "イネファ")])
        hint = "\n".join(self.chat._user_hint_lines("イネファとクリスの話ね。"))
        self.assertIn("イネファ = Ineffa", hint)
        self.assertIn("クリス = Kurisu", hint)
        hint = "\n".join(self.chat._user_hint_lines("クリスマスね。"))
        self.assertNotIn("Names in her line", hint)
        self.assertEqual(self.st.names_in("セルンの話"), [("SERN", "セルン")])

    def test_user_entry_wins_for_a_shared_reading(self):
        self.st.save_user_names([("CERN", "セルン")])
        self.assertEqual(self.st.names_in("セルンの話"), [("CERN", "セルン")])

    def test_names_in_her_fixed_rules(self):
        ja = self.chat.ja_voice
        self.assertNotIn("NAMES:", ja.build_voice_rules()["content"])
        self.st.save_user_names([("Ineffa", "イネファ")])
        self.assertIn("NAMES: in assistant_reply_JPS write these names exactly so: Ineffa -> イネファ.",
                      ja.build_voice_rules()["content"])

    def test_routes(self):
        self.assertEqual(self.client.get("/getNames").get_json(), {"names": []})
        r = self.client.post("/setNames", json={"names": [
            {"word": "Ineffa", "reading": "イネファ"}, {"word": "", "reading": ""}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/getNames").get_json(),
                         {"names": [{"word": "Ineffa", "reading": "イネファ"}]})
        r = self.client.post("/setNames", json={"names": [{"word": "Aino", "reading": "aino"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Aino", r.get_json()["message"])
        self.assertEqual(self.client.post("/setNames", json={}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

"""Web links in her replies (2026-10-26).

They are clickable on screen, so they must reach it whole: the cleaning of
her Japanese line (which strips English that leaked into it) keeps them, and
the voice leaves them out (spelled out letter by letter otherwise). She is
told to write one only when the user asks, copied from her search results.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat  # noqa: E402
import speech_text  # noqa: E402

WIKI = "https://en.wikipedia.org/wiki/Steins;Gate_(TV_series)"


class VoiceTests(unittest.TestCase):
    def test_links_are_not_spoken(self):
        self.assertEqual(speech_text.drop_links("ここに載ってるわ：https://game8.co/a/1。見てみて。"),
                         "ここに載ってるわ。見てみて。")
        self.assertEqual(speech_text.drop_links(f"リンクはこれ（{WIKI}）。"), "リンクはこれ。")
        self.assertEqual(speech_text.drop_links("[Game8](https://game8.co/a) のページよ"), "Game8 のページよ")
        self.assertNotIn("http", speech_text.to_spoken(f"これよ {WIKI} 2026年10月26日"))

    def test_text_without_links_unchanged(self):
        for line in ["こんにちは。", "Game8のページよ。", "PM2.5は12よ。"]:
            self.assertEqual(speech_text.drop_links(line), line)

    def test_link_spans_trim_like_the_link_reader(self):
        text = f"(see {WIKI}). また https://a.com/x、"
        self.assertEqual([text[a:b] for a, b in speech_text.link_spans(text)],
                         [WIKI, "https://a.com/x"])


class JapaneseLineTests(unittest.TestCase):
    def test_links_survive_the_english_strip(self):
        line = ("Game8のページよ。https://game8.co/games/Genshin-Impact/archives/297500 を見てね。"
                "This English sentence leaked into her Japanese line and must go away.")
        out = chat._clean_tts_text(line)
        self.assertIn("https://game8.co/games/Genshin-Impact/archives/297500", out)
        self.assertNotIn("leaked", out)

    def test_link_in_brackets_and_on_its_own_line(self):
        self.assertEqual(chat._clean_tts_text(f"リンクはこれ（{WIKI}）。"), f"リンクはこれ（{WIKI}）。")
        self.assertEqual(chat._clean_tts_text("https://example.com/a\nこれよ。"),
                         "https://example.com/a\nこれよ。")

    def test_several_links_come_back_in_order(self):
        line = "一つ目 https://a.com/1 、二つ目 https://b.com/2 よ。"
        self.assertEqual(chat._clean_tts_text(line), line)


class PromptTests(unittest.TestCase):
    def test_link_rule(self):
        rule = chat.PROACTIVE_SEARCH_BLOCK["content"]
        self.assertIn("do not add links on your own", rule)
        self.assertIn("Only when the user asks for a link", rule)
        self.assertIn("never make one up", rule)
        self.assertIn("never spoken", rule)


if __name__ == "__main__":
    unittest.main()

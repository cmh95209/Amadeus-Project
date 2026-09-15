# -*- coding: utf-8 -*-
"""Offline checks for explicit-request topic extraction (web ON).

The fast path used to hand the user's ENTIRE message to DuckDuckGo as the
query - small talk included - so "Good afternoon... try a search for the
weather in Lenggong" searched the small talk (back: greeting-card quotes),
and "Could you try to search again?" searched the retry sentence (back:
tech-support articles about broken search). The query must be the extracted
TOPIC only; when there is no topic, the fast path reuses the previous
explicit topic or does not fire at all - it never searches the raw message.
No network: _run_web_search and the LLM are faked.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chat  # noqa: E402
from test_web_search_resilience import FakeLLM, FakeReply, pack_call  # noqa: E402


class ExtractSearchTopicTests(unittest.TestCase):
    def t(self, msg):
        return chat._extract_search_topic(msg)

    def test_topic_extracted_from_message_full_of_small_talk(self):
        self.assertEqual(self.t(
            "Good afternoon. I just had my breakfast and wanted to check in "
            "with you before i drive to hometown. Is your web access module "
            "working well? Try a search for the weather and air quality in "
            "Lenggong please."),
            "the weather and air quality in Lenggong")

    def test_plain_search_for(self):
        self.assertEqual(self.t("Search for the latest Genshin banner"),
                         "the latest Genshin banner")

    def test_search_about_strips_leading_filler(self):
        self.assertEqual(self.t("search about the new anime"), "the new anime")

    def test_search_the_web_about_strips_web_filler(self):
        self.assertEqual(
            self.t("Can you search the web about the character Alyosha?"),
            "the character Alyosha")

    def test_look_up(self):
        self.assertEqual(self.t("Can you look up the price of that phone?"),
                         "the price of that phone")

    def test_look_up_strips_trailing_for_me(self):
        self.assertEqual(
            self.t("look up the price of that phone for me please"),
            "the price of that phone")

    def test_look_it_up_has_no_topic(self):
        self.assertEqual(self.t("Can you look it up?"), "")
        self.assertEqual(self.t("can you look it up for me?"), "")

    def test_google(self):
        self.assertEqual(self.t("google the new trailer"), "the new trailer")

    def test_google_pronoun_has_no_topic(self):
        self.assertEqual(self.t("can you google that for me?"), "")

    def test_bare_search(self):
        self.assertEqual(self.t("search Lenggong weather"), "Lenggong weather")

    def test_bare_retry_sentence_has_no_topic(self):
        self.assertEqual(self.t("Could you try to search again?"), "")

    def test_pronoun_topic_has_no_topic(self):
        self.assertEqual(self.t("search for it"), "")

    def test_noun_use_is_not_a_request(self):
        self.assertEqual(self.t("Is your web search working well?"), "")

    def test_last_request_wins(self):
        self.assertEqual(self.t("what was that again? search for the banner"),
                         "the banner")
        self.assertEqual(
            self.t("search for the old banner. Search for the new one"),
            "the new one")

    def test_online_trigger_alone_has_no_topic(self):
        self.assertEqual(self.t("check if the event is still online"), "")

    def test_topic_is_capped_at_200_chars(self):
        self.assertEqual(len(self.t("search for " + "x" * 300)), 200)


class PriorSearchQueryTests(unittest.TestCase):
    def msgs(self, *user_texts):
        out = [{"role": "system", "content": "x"}]
        for i, text in enumerate(user_texts):
            out.append({"role": "user", "content": text})
            out.append({"role": "assistant", "content": f"reply {i}"})
        return out

    def test_returns_clean_topic_not_full_message(self):
        messages = self.msgs(
            "Try a search for the weather and air quality in Lenggong please.")
        self.assertEqual(
            chat._find_prior_search_query(messages),
            "the weather and air quality in Lenggong")

    def test_bare_retry_skipped_and_prior_topic_used(self):
        messages = self.msgs(
            "Try a search for the weather and air quality in Lenggong please.",
            "Could you try to search again?",
        )
        self.assertEqual(
            chat._find_prior_search_query(messages),
            "the weather and air quality in Lenggong")

    def test_no_explicit_message_gives_empty(self):
        self.assertEqual(
            chat._find_prior_search_query(self.msgs("how\u2019s the weather?")), "")


class FastPathQueryTests(unittest.TestCase):
    """End to end (faked LLM + faked search): what query actually reaches
    _run_web_search - never the raw message."""

    def _run(self, messages):
        log = []
        llm = FakeLLM(lambda convo: FakeReply(tool_calls=pack_call()), log)
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search",
                          return_value="1. result.") as ms:
            pack = chat._getResponsePackedWithWebSearch(llm, messages)
        return pack, log, ms

    def test_small_talk_message_searches_only_the_topic(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "user",
             "content": "Good afternoon. Is your web access module working "
                        "well? Try a search for the latest MRT one-way "
                        "ticket prices in Kuala Lumpur please."},
        ]
        pack, log, ms = self._run(messages)
        ms.assert_called_once_with(
            "the latest MRT one-way ticket prices in Kuala Lumpur")
        self.assertEqual(len(log), 1)  # judgement call skipped (fast path)

    def test_bare_retry_with_no_prior_topic_does_not_search(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "how\u2019s the weather in Lenggong?"},
            {"role": "assistant", "content": "It looks cloudy."},
            {"role": "user", "content": "Could you try to search again?"},
        ]
        pack, log, ms = self._run(messages)
        ms.assert_not_called()
        self.assertEqual(len(log), 1)  # phase 1 answered directly (faked)

    def test_bare_retry_reuses_prior_topic(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "user",
             "content": "Try a search for the weather and air quality in "
                        "Lenggong please."},
            {"role": "assistant", "content": "Cloudy, rain in the evening."},
            {"role": "user", "content": "Could you try to search again?"},
        ]
        pack, log, ms = self._run(messages)
        ms.assert_called_once_with("the weather and air quality in Lenggong")
        self.assertEqual(len(log), 1)


if __name__ == "__main__":
    unittest.main()

"""Web-ON honesty and the web-access check (2026-10-25, Amadeus-Fresh test).

Asked to "confirm" web access, she searched for the current time and
believed a stale page ("14:05 UTC, 20 February 2026") over her clock. Now:
  1. a pure clock query is never searched (CLOCK_SEARCH_SKIPPED);
  2. "check / test / confirm your web access" runs a REAL test search on a
     neutral topic and she reports what came back (or that it failed);
  3. a reply that claims a lookup when nothing was searched gets one
     corrective rewrite, then an honest line;
  4. every search / weather fetch of a reply is collected and saved with
     its row (the base for showing her links later).
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_web_search_resilience import (  # noqa: E402
    FakeLLM, FakeReply, chat, pack_call, reply_calls, triage_call)
from test_ceremony import _FreshAppTests  # noqa: E402

RESULTS = ("1. Big news today\nSomething happened.\nURL: https://news.example/a\n\n"
           "2. Other story\nMore text.\nURL: https://news.example/b")


class ClockQueryTests(unittest.TestCase):
    def test_pure_clock_queries(self):
        for q in ["current time", "current time UTC", "What time is it now?",
                  "today's date", "what day is it", "time now", "今何時", "現在の時刻",
                  "今日の日付"]:
            self.assertTrue(chat._is_clock_query(q), q)

    def test_real_topics_still_search(self):
        for q in ["current time in Kuala Lumpur", "top news today",
                  "today's top news", "current date of the Genshin 6.2 banner",
                  "what time does the Riverton metro close", "", None]:
            self.assertFalse(chat._is_clock_query(q), q)

    def test_clock_query_is_not_searched_or_recorded(self):
        chat._begin_turn_sources()
        with patch.object(chat, "_run_web_search") as run:
            out = chat._web_search_with_content("current time UTC")
        run.assert_not_called()
        self.assertEqual(out, chat.CLOCK_SEARCH_SKIPPED)
        self.assertEqual(chat.take_turn_sources(), [])


class SourcesTests(unittest.TestCase):
    def test_parse_results(self):
        self.assertEqual(chat._parse_search_results(RESULTS), [
            {"title": "Big news today", "url": "https://news.example/a"},
            {"title": "Other story", "url": "https://news.example/b"}])
        self.assertEqual(chat._parse_search_results("No web results were found."), [])

    def test_a_search_is_recorded_with_the_page_read(self):
        chat._begin_turn_sources()
        with patch.object(chat, "_run_web_search", return_value=RESULTS), \
             patch.object(chat.webfetch, "fetch_page", return_value={"status": "ok", "text": "page"}), \
             patch.object(chat, "web_reading_plan", return_value=("quick", 0, None, True)), \
             patch.object(chat.store, "effective_context_budget", return_value=8000):
            chat._web_search_with_content("Genshin banner")
        self.assertEqual(chat.take_turn_sources(), [{
            "kind": "search", "query": "Genshin banner",
            "results": [{"title": "Big news today", "url": "https://news.example/a"},
                        {"title": "Other story", "url": "https://news.example/b"}],
            "read": "https://news.example/a", "engine": None, "mode": "quick",
            "used": ["https://news.example/a"]}])
        self.assertEqual(chat.take_turn_sources(), [])   # handed over once

    def test_nothing_is_recorded_outside_a_reply(self):
        chat.take_turn_sources()          # no reply in progress
        with patch.object(chat, "_run_web_search", return_value=RESULTS), \
             patch.object(chat.webfetch, "fetch_page", return_value={"status": "blocked"}), \
             patch.object(chat, "web_reading_plan", return_value=("quick", 0, None, True)), \
             patch.object(chat.store, "effective_context_budget", return_value=8000):
            chat._web_search_with_content("x")
        self.assertEqual(chat.take_turn_sources(), [])


class SavedSourcesTests(_FreshAppTests):
    def test_round_trip(self):
        mid = self.memory.append_message("assistant", "Hi.", japanese="やあ。")
        self.assertEqual(self.memory.load_message_sources(mid), [])
        sources = [{"kind": "search", "query": "q", "results": [], "read": None}]
        self.memory.set_message_sources(mid, sources)
        self.assertEqual(self.memory.load_message_sources(mid), sources)
        self.memory.set_message_sources(mid, [])     # empty -> left as it was
        self.assertEqual(self.memory.load_message_sources(mid), sources)


class _Loop(unittest.TestCase):
    def run_loop(self, messages, handler, triage=None, search_text=RESULTS):
        log = []
        chat._begin_turn_sources()
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.webfetch, "fetch_page", return_value={"status": "blocked"}), \
             patch.object(chat, "web_reading_plan", return_value=("quick", 0, None, True)), \
             patch.object(chat.store, "effective_context_budget", return_value=8000), \
             patch.object(chat, "_run_web_search", return_value=search_text) as run:
            pack = chat._getResponsePackedWithWebSearch(
                FakeLLM(handler, log, triage=triage), messages)
        return pack, reply_calls(log), run, chat.take_turn_sources()


CONFIRM = [
    {"role": "system", "content": "persona"},
    {"role": "user", "content": "Do you have web search capabilities?"},
    {"role": "assistant",
     "content": "No, web search is currently turned off, so I can't search the web."},
    {"role": "user", "content": "Well, I just turned it on. Could you confirm?"},
]


class WebCheckRouteTests(_Loop):
    def check_triage(self):
        return [{"name": "WebTriagePack", "args": {
            "wants_weather": "no", "weather_place": "none", "wants_search": "no",
            "search_topic": "none", "wants_web_check": "yes"}}]

    def test_confirm_runs_a_real_test_search(self):
        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="It works - I just found Big news today."))
        pack, log, run, sources = self.run_loop(CONFIRM, handler, triage=self.check_triage())
        run.assert_called_once_with(chat.WEB_CHECK_QUERY)
        note = log[0][-1]["content"]
        self.assertIn("this test search WORKED", note)
        self.assertIn("Big news today", note)
        self.assertEqual(sources[0]["query"], chat.WEB_CHECK_QUERY)
        self.assertIn("It works", pack.assistant_reply_ENG)

    def test_failed_test_search_is_reported_honestly(self):
        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="Web access is on, but my test failed."))
        _pack, log, _run, _src = self.run_loop(
            CONFIRM, handler, triage=self.check_triage(),
            search_text="The web search did not return anything this time.")
        self.assertIn("did NOT return any results", log[0][-1]["content"])

    def test_a_named_topic_wins_over_the_check(self):
        triage = [{"name": "WebTriagePack", "args": {
            "wants_weather": "no", "weather_place": "none", "wants_search": "yes",
            "search_topic": "Riverton metro ticket prices", "wants_web_check": "yes"}}]
        msgs = [{"role": "system", "content": "persona"},
                {"role": "user", "content": "Is your web access working? Search "
                                            "Riverton metro ticket prices."}]

        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="Tickets cost 2."))
        _pack, _log, run, _src = self.run_loop(msgs, handler, triage=triage)
        run.assert_called_once_with("Riverton metro ticket prices")

    def test_candidates(self):
        self.assertTrue(chat._web_triage_candidate(CONFIRM[-1]["content"], CONFIRM))
        self.assertTrue(chat._web_triage_candidate(
            "Can you check your web access?", [{"role": "user", "content": "x"}]))
        older = [CONFIRM[2], {"role": "user", "content": "ok"},
                 {"role": "assistant", "content": "Sure, that sounds fun."},
                 {"role": "user", "content": "what's your favourite colour?"}]
        self.assertFalse(chat._web_triage_candidate(older[-1]["content"], older))

    def test_her_switched_off_wordings_count_as_a_refusal(self):
        for line in ["いいえ、今はWeb検索機能はオフになっているわ。",
                     "No, my web search function is currently turned off."]:
            msgs = [{"role": "assistant", "content": line},
                    {"role": "user", "content": "Well, I just turned it on. Could you confirm?"}]
            self.assertTrue(chat._last_reply_refused_search(msgs), line)
            self.assertTrue(chat._web_triage_candidate(msgs[-1]["content"], msgs), line)

    def test_keyword_fallback_only_when_triage_failed(self):
        line = "Can you check your web access?"
        self.assertEqual(chat._deterministic_route(line, [], allow_check=True), {"check": True})
        self.assertNotEqual(chat._deterministic_route(line, [], allow_check=False), {"check": True})


class PretendLookupNetTests(_Loop):
    MSGS = [{"role": "system", "content": "persona"},
            {"role": "user", "content": "How are you today?"}]

    def test_claim_without_search_is_rewritten(self):
        replies = iter([
            FakeReply(tool_calls=pack_call(eng="Based on the latest data, I'm great.")),
            FakeReply(tool_calls=pack_call(eng="I'm great, thanks.")),
        ])
        pack, _log, run, _src = self.run_loop(self.MSGS, lambda convo: next(replies))
        run.assert_not_called()
        self.assertEqual(pack.assistant_reply_ENG, "I'm great, thanks.")

    def test_a_second_claim_gets_the_honest_line(self):
        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="According to the search results, fine."))
        pack, _log, _run, _src = self.run_loop(self.MSGS, handler)
        self.assertEqual(pack.assistant_reply_ENG,
                         chat._honest_no_lookup_pack().assistant_reply_ENG)

    def test_a_real_search_may_say_so(self):
        msgs = [{"role": "system", "content": "persona"},
                {"role": "user", "content": "Search for the Genshin 6.2 banner."}]
        triage = triage_call(search="yes", topic="Genshin 6.2 banner")

        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="According to the search results, Ineffa."))
        pack, _log, run, sources = self.run_loop(msgs, handler, triage=triage)
        run.assert_called_once()
        self.assertIn("Ineffa", pack.assistant_reply_ENG)
        self.assertEqual(len(sources), 1)

    def test_the_web_on_instructions_say_the_clock_wins(self):
        text = chat.PROACTIVE_SEARCH_BLOCK["content"]
        self.assertIn("RIGHT NOW note is right", text)
        self.assertIn("unless you actually ran web_search", text)


if __name__ == "__main__":
    unittest.main()

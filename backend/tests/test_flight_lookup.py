"""A user's flight at the airport (2026-11-06, made-up example details).

They didn't know the flight number. She searched her own guesses ("... XY365
XY349"), said the gate "matches XY365", and when they found XY1234 her search
for it was answered with older general notes (that swap is gone - see
test_lookups). Here: flight numbers nobody gave her leave the search words,
and the router keeps the known details in the topic.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_web_search_resilience import chat  # noqa: E402

import websearch  # noqa: E402


class GuessedFlightNumberTests(unittest.TestCase):
    def test_her_guesses_leave_the_search(self):
        out, removed = websearch.guard_query_flight_numbers(
            "Lisbon airport departure gate B12 XY365 XY349 November 2026",
            ["Yeah i can see 9.40am on the gate. Not sure what's the flight number",
             "Gate B12"])
        self.assertEqual(out, "Lisbon airport departure gate B12 November 2026")
        self.assertEqual(removed, ["XY365", "XY349"])

    def test_the_users_flight_number_stays(self):
        for query, said in (("XY1234 flight status Lisbon to Oslo", "Looks like it's XY1234"),
                            ("BA 117 flight search", "is ba117 on time?"),
                            ("3U8765 departure time", "it's 3U 8765")):
            self.assertEqual(websearch.guard_query_flight_numbers(query, [said]), (query, []))

    def test_only_flight_searches(self):
        for query in ("PS5 Pro price", "RTX 5090 review", "GT7 update"):
            self.assertEqual(websearch.guard_query_flight_numbers(query, []), (query, []))

    def test_a_guess_only_search_keeps_the_plain_words(self):
        self.assertEqual(websearch.guard_query_flight_numbers("XY365 flight status", []),
                         ("flight status", ["XY365"]))


class GuardInARepliesTests(unittest.TestCase):
    def tearDown(self):
        chat.take_turn_sources()
        chat._turn.question = ""
        chat._turn.reply_context = None

    def test_user_words_and_seen_results_count(self):
        chat._begin_turn_sources()
        chat._turn.question = "Yeah i can see 9.40am on the gate"
        chat._turn.reply_context = [{"role": "user", "content": "It's the flight back to Oslo"},
                                    {"role": "assistant", "content": "Is it XY365?"},
                                    {"role": "user", "content": "Yeah i can see 9.40am on the gate"}]
        # Her own earlier guess (in her reply) is not a known flight number.
        self.assertEqual(chat._guard_query("XY365 flight status Lisbon"), "flight status Lisbon")
        # A number found in this reply's results may be searched next.
        chat._record_source({"kind": "search", "query": "Lisbon to Oslo flights 9:40",
                             "results": [{"title": "XY1234 Flight Status - Example Air",
                                          "url": "https://flights.example.com/status-xy1234/"}]})
        self.assertEqual(chat._guard_query("XY1234 flight status"), "XY1234 flight status")


class RouterTopicTests(unittest.TestCase):
    def test_the_topic_keeps_known_details(self):
        text = chat.WebTriagePack.model_fields["search_topic"].description
        self.assertIn("from where, to where, airline, flight number", text)
        self.assertIn("never a detail nobody said", text)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Offline checks for the weather fast path.

No network anywhere: weather._get_json is faked, and the chat loop runs on a
faked LLM. Covers the Open-Meteo parsing + formatting, the honest
degradation (unknown place / dead service / air-quality optional), the
deterministic weather-intent detection, place extraction, and the loop
wiring (live data fed as plain text; no place -> she asks; weather beats
the explicit-search fast path; web OFF -> no weather call at all).
"""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import weather  # noqa: E402
import chat  # noqa: E402


# --- faked Open-Meteo payloads (shapes verified live, 2026-09-15) ------------
GEO = {
    "results": [
        {"id": 1767492, "name": "Lenggong", "latitude": 5.10633,
         "longitude": 100.96792, "country_code": "MY", "admin1": "Perak",
         "admin2": "Ulu Perak", "country": "Malaysia",
         "timezone": "Asia/Kuala_Lumpur", "population": 12722},
        {"id": 999, "name": "Lenggongi", "latitude": 45.0, "longitude": 18.0,
         "country": "Poland", "admin1": None},
    ]
}
FC = {
    "timezone": "Asia/Kuala_Lumpur",
    "current": {
        "time": "2026-09-15T23:45", "temperature_2m": 25.5,
        "relative_humidity_2m": 100, "apparent_temperature": 32.2,
        "is_day": 0, "precipitation": 0.0, "weather_code": 3,
        "wind_speed_10m": 2.3,
    },
    "daily": {
        "time": ["2026-09-15", "2026-09-16"],
        "temperature_2m_max": [34.1, 33.0],
        "temperature_2m_min": [25.7, 24.9],
        "precipitation_probability_max": [84, 70],
        "sunrise": ["2026-09-15T07:06", "2026-09-16T07:06"],
        "sunset": ["2026-09-15T19:15", "2026-09-16T19:14"],
    },
}
AIR = {"current": {"time": "2026-09-15T23:00", "us_aqi": 159,
                   "pm2_5": 68.2, "pm10": 71.7}}


def fake_get_json(url, params, timeout):
    if "geocoding" in url:
        return GEO
    if "air-quality" in url:
        return AIR
    if "api.open-meteo" in url:
        return FC
    raise AssertionError("unexpected url: " + url)


# --- the faked LLM (same pattern as test_web_search_resilience.py) -----------
class FakeReply:
    def __init__(self, content="", tool_calls=None, tool_call_chunks=None):
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_chunks = tool_call_chunks


class FakeBound:
    def __init__(self, handler, log, triage=None):
        self.handler = handler
        self.log = log
        self.triage = triage  # canned answer for the web-triage router call

    def invoke(self, convo):
        self.log.append(list(convo))
        # The web-triage router call is answered with a canned tool call
        # (default: "no/no" - defer to the keyword routing), never with the
        # test's own handler.
        last = convo[-1] if convo else {}
        if (last.get("role") == "user"
                and isinstance(last.get("content"), str)
                and last["content"].startswith("Classify the user's latest")):
            return FakeReply(tool_calls=self.triage
                                        if self.triage is not None
                                        else triage_call())
        return self.handler(convo)

    def bind(self, **kwargs):
        return self


class FakeLLM:
    def __init__(self, handler, log, triage=None):
        self.handler = handler
        self.log = log
        self.last_tools = None
        self.triage = triage if triage is not None else triage_call()

    def bind_tools(self, tools, tool_choice=None):
        self.last_tools = [t.get("function", {}).get("name") for t in tools]
        return FakeBound(self.handler, self.log, self.triage)

    def invoke(self, messages):
        return self.handler(messages)


def pack_call(eng="ok", jps="\u3057\u3083\u3088"):
    return [{"name": "AmadeusPack",
             "args": {"assistant_reply_JPS": jps, "assistant_reply_ENG": eng}}]


def triage_call(weather="no", place="none", search="no", topic="none"):
    """A canned web_triage tool call (the router speaks yes/no/none)."""
    return [{"name": "WebTriagePack",
             "args": {"wants_weather": weather, "weather_place": place,
                      "wants_search": search, "search_topic": topic}}]


# --- weather.py: parsing + formatting ----------------------------------------
class GeocodeTests(unittest.TestCase):
    def test_picks_first_result(self):
        with patch.object(weather, "_get_json", return_value=GEO):
            loc = weather.geocode("Lenggong")
        self.assertEqual(loc["name"], "Lenggong")
        self.assertEqual(loc["admin1"], "Perak")
        self.assertEqual(loc["country"], "Malaysia")

    def test_empty_results_gives_none(self):
        with patch.object(weather, "_get_json", return_value={"results": []}):
            self.assertIsNone(weather.geocode("Xyzzyland"))

    def test_missing_results_key_gives_none(self):
        with patch.object(weather, "_get_json", return_value={}):
            self.assertIsNone(weather.geocode("Xyzzyland"))


class FormatTests(unittest.TestCase):
    def test_wmo_descriptions(self):
        self.assertEqual(weather.wmo_description(0), "clear sky")
        self.assertEqual(weather.wmo_description(3), "overcast")
        self.assertEqual(weather.wmo_description(95), "thunderstorm")
        self.assertEqual(weather.wmo_description(65535), "unknown conditions")
        self.assertEqual(weather.wmo_description(None), "unknown conditions")

    def test_aqi_categories(self):
        self.assertEqual(weather.aqi_category(25), "Good")
        self.assertEqual(weather.aqi_category(50), "Good")
        self.assertEqual(weather.aqi_category(51), "Moderate")
        self.assertEqual(weather.aqi_category(100), "Moderate")
        self.assertEqual(weather.aqi_category(151), "Unhealthy")
        self.assertEqual(weather.aqi_category(301), "Hazardous")
        self.assertIsNone(weather.aqi_category(None))

    def test_num_formatting(self):
        self.assertEqual(weather._num(25.5), "25.5")
        self.assertEqual(weather._num(34.0), "34")
        self.assertEqual(weather._num(2.3, 0), "2")
        self.assertIsNone(weather._num(None))
        self.assertIsNone(weather._num("abc"))


class ReportTests(unittest.TestCase):
    def test_full_report(self):
        with patch.object(weather, "_get_json", side_effect=fake_get_json):
            r = weather.fetch_weather_report("Lenggong")
        self.assertIn("Lenggong, Perak, Malaysia", r)
        self.assertIn("Now (23:45 local): 25.5\u00b0C", r)
        self.assertIn("feels like 32.2\u00b0C", r)
        self.assertIn("overcast", r)
        self.assertIn("humidity 100%", r)
        self.assertIn("wind 2 km/h", r)
        self.assertIn("no rain right now", r)
        self.assertIn("Today: high 34\u00b0C / low 26\u00b0C", r)
        self.assertIn("up to 84% chance of rain", r)
        self.assertIn("Sunrise 07:06, sunset 19:15", r)
        self.assertIn("Tomorrow: high 33\u00b0C / low 25\u00b0C", r)
        self.assertIn("up to 70% chance of rain", r)
        self.assertIn("Air quality (US AQI): 159 (Unhealthy)", r)
        self.assertIn("PM2.5 68.2", r)
        self.assertIn("Open-Meteo", r)

    def test_rain_right_now_phrased(self):
        fc = json.loads(json.dumps(FC))
        fc["current"]["precipitation"] = 1.2

        def gj(url, params, timeout):
            if "geocoding" in url:
                return GEO
            if "air-quality" in url:
                return AIR
            return fc

        with patch.object(weather, "_get_json", side_effect=gj):
            r = weather.fetch_weather_report("Lenggong")
        self.assertIn("rain right now", r)
        self.assertNotIn("no rain right now", r)

    def test_unknown_place_is_honest(self):
        with patch.object(weather, "_get_json", return_value={"results": []}):
            self.assertEqual(weather.fetch_weather_report("Xyzzyland"),
                             weather.NOT_FOUND_LINE)

    def test_service_down_is_honest(self):
        with patch.object(weather, "_get_json",
                          side_effect=OSError("connection reset")):
            self.assertEqual(weather.fetch_weather_report("Lenggong"),
                             weather.SERVICE_DOWN_LINE)

    def test_forecast_down_after_geocode_is_honest(self):
        def gj(url, params, timeout):
            if "geocoding" in url:
                return GEO
            raise OSError("down")

        with patch.object(weather, "_get_json", side_effect=gj):
            self.assertEqual(weather.fetch_weather_report("Lenggong"),
                             weather.SERVICE_DOWN_LINE)

    def test_air_failure_only_drops_the_air_line(self):
        def gj(url, params, timeout):
            if "geocoding" in url:
                return GEO
            if "air-quality" in url:
                raise OSError("down")
            return FC

        with patch.object(weather, "_get_json", side_effect=gj):
            r = weather.fetch_weather_report("Lenggong")
        self.assertNotIn("Air quality", r)
        self.assertIn("Today:", r)
        self.assertIn("Open-Meteo", r)

    def test_empty_place_is_honest(self):
        self.assertEqual(weather.fetch_weather_report("   "),
                         weather.NOT_FOUND_LINE)


# --- chat.py: deterministic intent + place extraction ------------------------
class WeatherIntentTests(unittest.TestCase):
    def test_positive(self):
        for text in [
            "how's the weather today?",
            "What's the forecast for Tokyo?",
            "will it rain in Penang tonight?",
            "how hot is it in Bangkok?",
            "air quality in Lenggong",
            "what's the humidity in KL?",
            "what's the AQI here?",
            "is it raining?",
            "will it snow this weekend?",
            "check the weather for my trip to Tokyo",
            "can you search the weather in Lenggong?",
            "\u4eca\u65e5\u306e\u5929\u6c17\u306f\u3069\u3046?",
            "\u660e\u65e5\u306e\u6c17\u6e29\u306f\uff1f",
        ]:
            self.assertTrue(chat._weather_intent(text), text)

    def test_negative(self):
        for text in [
            "tell me about Mitya",
            "this game is so hot",
            "I'm feeling a bit cold",
            "the weathered stone in the room",
            "I turned your web access on",
            "good morning!",
            "what did you have for breakfast?",
        ]:
            self.assertFalse(chat._weather_intent(text), text)


class PlaceExtractionTests(unittest.TestCase):
    def test_places_extracted_with_casing(self):
        cases = {
            "weather in Lenggong": "Lenggong",
            "What's the forecast for Tokyo?": "Tokyo",
            "will it rain in Penang tonight?": "Penang",
            "how hot is it in Bangkok?": "Bangkok",
            "what's the weather like in Kuala Lumpur tomorrow?": "Kuala Lumpur",
            "air quality in Lenggong": "Lenggong",
            "check the weather for my trip to Tokyo": "Tokyo",
            "will it be hot in Singapore tomorrow?": "Singapore",
        }
        for text, expected in cases.items():
            self.assertEqual(chat._extract_place(text), expected, text)

    def test_no_place_returns_empty(self):
        for text in [
            "how's the weather today?",
            "weather",
            "what's the weather like?",
            "is it raining outside?",
            "what's the weather in my area?",
            "how cold is it in the city?",
            "\u4eca\u65e5\u306e\u5929\u6c17\u306f\uff1f",
        ]:
            self.assertEqual(chat._extract_place(text), "", text)


# --- chat.py: the wiring inside the web loop ---------------------------------
MESSAGES_WEATHER = [
    {"role": "system", "content": "persona"},
    {"role": "user", "content": "how's the weather in Lenggong?"},
]


class WeatherWiringTests(unittest.TestCase):
    def test_live_data_fed_as_plain_text_and_search_skipped(self):
        log = []

        def handler(convo):
            return FakeReply(tool_calls=pack_call(
                eng="25.5 degrees, overcast, rain likely."))

        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          return_value="FAKE LIVE BLOCK"), \
             patch.object(chat, "_run_web_search",
                          side_effect=AssertionError("search must not run")):
            pack = chat._getResponsePackedWithWebSearch(
                FakeLLM(handler, log), MESSAGES_WEATHER)

        self.assertEqual(
            pack.assistant_reply_ENG, "25.5 degrees, overcast, rain likely.")
        self.assertEqual(len(log), 2)  # triage + phase 2 - no judgement call
        fed = log[1][-1]["content"]
        self.assertIn("Live weather data for 'Lenggong'", fed)
        self.assertIn("FAKE LIVE BLOCK", fed)
        self.assertIn("AmadeusPack", fed)

    def test_weather_beats_explicit_search(self):
        log = []

        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="It is raining."))

        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          return_value="FAKE") as fw, \
             patch.object(chat, "_run_web_search",
                          side_effect=AssertionError("search must not run")):
            chat._getResponsePackedWithWebSearch(FakeLLM(handler, log), [
                {"role": "system", "content": "persona"},
                {"role": "user",
                 "content": "can you search the weather in Tokyo?"},
            ])

        fw.assert_called_once_with("Tokyo")
        self.assertEqual(len(log), 2)

    def test_no_place_makes_her_ask(self):
        log = []

        def handler(convo):
            return FakeReply(tool_calls=pack_call(
                eng="Which town are you asking about?"))

        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          side_effect=AssertionError("must not fetch")), \
             patch.object(chat, "_run_web_search", return_value="1. result."):
            pack = chat._getResponsePackedWithWebSearch(
                FakeLLM(handler, log), [
                    {"role": "system", "content": "persona"},
                    {"role": "user", "content": "how's the weather?"},
                ])

        self.assertEqual(pack.assistant_reply_ENG,
                         "Which town are you asking about?")
        self.assertIn("not clear from their message", log[1][-1]["content"])

    def test_service_down_line_is_fed_honestly(self):
        log = []

        def handler(convo):
            return FakeReply(tool_calls=pack_call(
                eng="Sorry, I could not reach the weather service."))

        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          return_value=weather.SERVICE_DOWN_LINE):
            chat._getResponsePackedWithWebSearch(
                FakeLLM(handler, log), MESSAGES_WEATHER)

        self.assertIn(weather.SERVICE_DOWN_LINE, log[1][-1]["content"])


class WebOffWeatherTests(unittest.TestCase):
    """Web OFF: the weather fast path must not run at all (user's decision)."""

    def test_web_off_never_calls_weather(self):
        class Structured:
            def invoke(self, messages):
                return chat.AmadeusPack(assistant_reply_JPS="\u3046\u3093\u3002",
                                         assistant_reply_ENG="ok")

        class OffLLM(FakeLLM):
            def with_structured_output(self, schema, method=None):
                return Structured()

        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="ok"))

        with patch.object(chat, "get_llm", return_value=OffLLM(handler, [])), \
             patch.object(chat.store, "load_web_access", return_value=False), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          side_effect=AssertionError("web off: no weather")):
            pack = chat.getResponsePacked(
                [{"role": "user",
                  "content": "how's the weather in Lenggong?"}])

        self.assertEqual(pack.assistant_reply_ENG, "ok")


class TriageRoutingTests(unittest.TestCase):
    """The model-judged router: an affirmative triage beats the keywords, a
    "no/no" answer defers to them, and a dead call never blocks the reply."""

    def test_novel_phrasing_and_context_place_route_via_triage(self):
        log = []
        llm = FakeLLM(
            lambda convo: FakeReply(tool_calls=pack_call(eng="Rain likely.")),
            log,
            triage=triage_call(weather="yes", place="Ipoh"))
        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          return_value="FAKE") as fw, \
             patch.object(chat, "_run_web_search",
                          side_effect=AssertionError("search must not run")):
            chat._getResponsePackedWithWebSearch(llm, [
                {"role": "system", "content": "persona"},
                {"role": "user", "content": "I'm based in Ipoh, fyi."},
                {"role": "assistant", "content": "Noted!"},
                {"role": "user", "content": "do I need a raincoat tomorrow?"},
            ])
        fw.assert_called_once_with("Ipoh")  # the place came from CONTEXT
        self.assertEqual(len(log), 2)  # triage + phase 2

    def test_no_no_triage_defers_to_keywords(self):
        log = []
        llm = FakeLLM(
            lambda convo: FakeReply(tool_calls=pack_call(eng="Cloudy.")),
            log)  # default triage: "no/no"
        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          return_value="FAKE") as fw:
            chat._getResponsePackedWithWebSearch(llm, MESSAGES_WEATHER)
        fw.assert_called_once_with("Lenggong")  # the keyword route did it
        self.assertEqual(len(log), 2)

    def test_debris_topic_reuses_prior_query(self):
        log = []
        llm = FakeLLM(
            lambda convo: FakeReply(tool_calls=pack_call(eng="Here you go.")),
            log,
            triage=triage_call(search="yes", topic="me again"))
        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          side_effect=AssertionError("must not fetch")), \
             patch.object(chat, "_run_web_search",
                          return_value="1. result.") as ms:
            chat._getResponsePackedWithWebSearch(llm, [
                {"role": "system", "content": "persona"},
                {"role": "user",
                 "content": "Try a search for the MRT ticket prices please."},
                {"role": "assistant", "content": "They cost 2 ringgit."},
                {"role": "user", "content": "Could you try to search again?"},
            ])
        ms.assert_called_once_with("the MRT ticket prices")

    def test_dead_triage_call_never_blocks_reply(self):
        llm = FakeLLM(lambda convo: FakeReply(tool_calls=pack_call(eng="ok")),
                      [])
        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_web_triage",
                          side_effect=TimeoutError("wedged")), \
             patch.object(chat.weather, "fetch_weather_report",
                          return_value="FAKE") as fw:
            chat._getResponsePackedWithWebSearch(llm, MESSAGES_WEATHER)
        fw.assert_called_once_with("Lenggong")  # the keyword fallback did it

    def test_timeout_guard(self):
        def slow():
            time.sleep(0.2)
            return "late"
        with self.assertRaises(TimeoutError):
            chat._invoke_with_timeout(slow, timeout=0.05)
        self.assertEqual(chat._invoke_with_timeout(lambda: 7, timeout=1), 7)


class FinalCallRobustnessTests(unittest.TestCase):
    """The final structured call can come back empty (transient) or clipped
    mid-JSON (a low max-output-tokens cap) - one bounded retry, then an
    honest line that says what ACTUALLY failed (no invented 'search')."""

    def _weather_run(self, llm):
        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat.weather, "fetch_weather_report",
                          return_value="FAKE LIVE BLOCK"):
            return chat._getResponsePackedWithWebSearch(llm, MESSAGES_WEATHER)

    def test_empty_final_call_gets_one_bounded_retry(self):
        n = 0

        def handler(convo):
            nonlocal n
            n += 1
            if n == 1:
                return FakeReply(content="")
            return FakeReply(tool_calls=pack_call(eng="AQI 160, unhealthy."))

        pack = self._weather_run(FakeLLM(handler, []))
        self.assertEqual(pack.assistant_reply_ENG, "AQI 160, unhealthy.")
        self.assertEqual(n, 2)  # the empty call + the retry

    def test_clipped_tool_call_retries_and_recovers(self):
        n = 0

        def handler(convo):
            nonlocal n
            n += 1
            if n == 1:
                # a max-tokens cap cut the AmadeusPack JSON mid-stream:
                # unparseable tool call, no content
                return FakeReply(content="",
                                 tool_call_chunks=[{"name": "AmadeusPack",
                                                    "args": '{"assistant_reply'}])
            return FakeReply(tool_calls=pack_call(eng="Recovered."))

        pack = self._weather_run(FakeLLM(handler, []))
        self.assertEqual(pack.assistant_reply_ENG, "Recovered.")
        self.assertEqual(n, 2)

    def test_empty_final_call_twice_says_the_truth_not_a_search(self):
        def handler(convo):
            return FakeReply(content="")

        pack = self._weather_run(FakeLLM(handler, []))
        # honest AND weather-accurate: the data WAS fetched, no search involved
        self.assertIn("live weather data", pack.assistant_reply_ENG)
        self.assertNotIn("search", pack.assistant_reply_ENG.lower())


if __name__ == "__main__":
    unittest.main()

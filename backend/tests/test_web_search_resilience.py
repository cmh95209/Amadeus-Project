# -*- coding: utf-8 -*-
"""Offline checks for the web-search resilience guards.

Covers, with a faked LLM and a faked DuckDuckGo search (no server, no network):
  1. the explicit-request fast path (skips the phase-1 judgement call);
  2. the rate-limit (429) detection, the bounded wait, the single retry, and
     the no-stacked-waits rule;
  3. the honesty net: a search turn that cannot be completed returns the fixed
     honest line instead of her pre-search announcement;
  4. the normal search flow still works end to end (regression).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chat  # noqa: E402


class _Fake429(Exception):
    status_code = 429

    def __str__(self):
        return ("Error code: 429 - {'error': {'status': 'RESOURCE_EXHAUSTED', "
                "'message': 'Quota exceeded for users/generateContent'}}")


class FakeReply:
    def __init__(self, content="", tool_calls=None, tool_call_chunks=None):
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_chunks = tool_call_chunks


class FakeBound:
    """Stands in for a langchain bind_tools() result."""

    def __init__(self, handler, log, triage=None):
        self.handler = handler  # fn(convo) -> FakeReply (or raises)
        self.log = log          # one entry per invoke: the convo that was sent
        self.triage = triage    # canned answer for the web-triage router call

    def invoke(self, convo):
        self.log.append(list(convo))
        # The web-triage router call is answered with a canned tool call
        # (default: "no/no" - defer to the keyword routing), never with the
        # test's own handler. Tests opt in to an affirmative triage by
        # passing triage=... to FakeLLM.
        last = convo[-1] if convo else {}
        if (last.get("role") == "user"
                and isinstance(last.get("content"), str)
                and last["content"].startswith("Classify the user's latest")):
            return FakeReply(tool_calls=self.triage
                                        if self.triage is not None
                                        else triage_call())
        return self.handler(convo)

    def bind(self, **kwargs):
        return self  # the tool_choice downgrade is a no-op in the fake


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


# The captured incident: web toggled on, follow-up "try again" (NOT an explicit
# search phrase - it must go through the normal judgement-call path).
MESSAGES = [
    {"role": "system", "content": "persona"},
    {"role": "user", "content": "I\u2019ve turned your web access on. Could you try again?"},
]


class ExplicitSearchDetectionTests(unittest.TestCase):
    def msgs(self, text):
        return [{"role": "system", "content": "x"},
                {"role": "user", "content": text}]

    def test_explicit_requests_match(self):
        for text in [
            "Can you search the web about the character Alyosha?",
            "Ok, how about the character Mitya. Can you search",
            "Can you look up the new patch notes?",
            "can you look it up for me?",
            "check if the event is still online",
            "can you google that for me?",
        ]:
            self.assertNotEqual(
                chat._user_explicitly_asks_for_search(self.msgs(text)), "", text)

    def test_non_explicit_messages_do_not_match(self):
        for text in [
            "I\u2019ve turned your web access on. Could you try again?",
            "how\u2019s the weather today?",
            "tell me about Mitya",
            "what do you think of the new character?",
        ]:
            self.assertEqual(
                chat._user_explicitly_asks_for_search(self.msgs(text)), "", text)


class RateLimitDetectionTests(unittest.TestCase):
    def _e(self, msg, status=None):
        exc = Exception(msg)
        exc.status_code = status
        return exc

    def test_positive_cases(self):
        self.assertTrue(chat._is_rate_limited(self._e("Error code: 429", 429)))
        self.assertTrue(chat._is_rate_limited(
            self._e("Error code: 429 - {'error': {'status': 'RESOURCE_EXHAUSTED'}}")))
        self.assertTrue(chat._is_rate_limited(
            self._e("Quota exceeded for users/generateContent")))
        self.assertTrue(chat._is_rate_limited(self._e("429 Too Many Requests")))

    def test_negative_cases(self):
        self.assertFalse(chat._is_rate_limited(self._e("connection dropped", 500)))
        self.assertFalse(chat._is_rate_limited(
            self._e("tool_choice='required' cannot guarantee", 400)))
        self.assertFalse(chat._is_rate_limited(ValueError("no usable reply")))


class RateLimitWaitTests(unittest.TestCase):
    def setUp(self):
        chat._RATE_LIMIT_STATE["last_wait"] = 0.0

    def test_waits_once_then_retries(self):
        calls = []
        sleeps = []

        def handler(convo):
            calls.append(1)
            if len(calls) == 1:
                raise _Fake429()
            return FakeReply(content="done", tool_calls=pack_call())

        with patch.object(chat.time, "sleep", side_effect=sleeps.append):
            out = chat._invoke_forced(
                FakeBound(handler, []), [{"role": "user", "content": "hi"}])

        self.assertEqual(out.content, "done")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], chat._RATE_LIMIT_MIN_WAIT)
        self.assertLessEqual(sleeps[0], chat._RATE_LIMIT_MAX_WAIT)

    def test_second_429_within_window_does_not_wait_again(self):
        sleeps = []

        def handler(convo):
            raise _Fake429()

        with patch.object(chat.time, "sleep", side_effect=sleeps.append):
            with self.assertRaises(_Fake429):
                chat._invoke_forced(
                    FakeBound(handler, []), [{"role": "user", "content": "hi"}])
        first_wait = len(sleeps)
        self.assertEqual(first_wait, 1)
        # immediately again: the window has not reset -> no second wait
        with patch.object(chat.time, "sleep", side_effect=sleeps.append):
            with self.assertRaises(_Fake429):
                chat._invoke_forced(
                    FakeBound(handler, []), [{"role": "user", "content": "hi"}])
        self.assertEqual(len(sleeps), first_wait)

    def test_non_rate_limit_errors_raise_without_waiting(self):
        sleeps = []

        def handler(convo):
            raise ConnectionError("connection dropped")

        with patch.object(chat.time, "sleep", side_effect=sleeps.append):
            with self.assertRaises(ConnectionError):
                chat._invoke_forced(
                    FakeBound(handler, []), [{"role": "user", "content": "hi"}])
        self.assertEqual(sleeps, [])


class WebLoopHonestyNetTests(unittest.TestCase):
    """The captured incident: she announces the search in plain text, the final
    call then fails or comes back empty, and the ONLY surviving text is the
    announcement. That must never become the reply."""

    def _run(self, handler, messages=None):
        log = []
        llm = FakeLLM(handler, log)
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False):
            pack = chat._getResponsePackedWithWebSearch(
                llm, messages or MESSAGES)
        return pack, log, llm

    def _assert_honest(self, pack):
        # the honest line, not her pre-search announcement
        self.assertIn("temporary glitch", pack.assistant_reply_ENG)
        self.assertNotIn("search for information regarding", pack.assistant_reply_ENG)
        self.assertTrue(chat._has_japanese(pack.assistant_reply_JPS))

    def test_failed_final_call_returns_honest_pack(self):
        def handler(convo):
            if len(convo) <= len(MESSAGES):
                # phase 1: plain-text announcement instead of a tool call
                return FakeReply(content=(
                    "I\u2019ll search for information regarding Alyosha. "
                    "Just a moment."))
            raise _Fake429()  # phase 2: rate limit, even after the single wait

        pack, log, _ = self._run(handler)
        self._assert_honest(pack)

    def test_empty_final_call_returns_honest_pack(self):
        def handler(convo):
            if len(convo) <= len(MESSAGES):
                return FakeReply(content=(
                    "I\u2019ll search for information regarding Alyosha. "
                    "Just a moment."))
            return FakeReply(content="")  # phase 2: empty, no tool call

        pack, log, _ = self._run(handler)
        self._assert_honest(pack)


class WebLoopFlowTests(unittest.TestCase):
    """Regression: the paths that USED to work must keep working."""

    def test_normal_search_flow_still_works(self):
        log = []

        def handler(convo):
            if len(convo) <= len(MESSAGES):
                return FakeReply(tool_calls=[
                    {"name": "web_search",
                     "args": {"query": "Alyosha character"}, "id": "1"}])
            return FakeReply(tool_calls=pack_call(
                eng="Alyosha is a character from Genshin Impact."))

        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search",
                          return_value="1. Alyosha - some result text.") as ms:
            pack = chat._getResponsePackedWithWebSearch(
                FakeLLM(handler, log), MESSAGES)

        self.assertEqual(
            pack.assistant_reply_ENG, "Alyosha is a character from Genshin Impact.")
        self.assertEqual(len(log), 2)  # phase 1 + phase 2
        ms.assert_called_once_with("Alyosha character")
        self.assertIn("Web search results for 'Alyosha character'",
                      log[1][-1]["content"])

    def test_explicit_request_skips_judgement_call(self):
        log = []

        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="Here is what I found."))

        llm = FakeLLM(handler, log)
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "user",
             "content": "Can you search the web about the character Alyosha?"},
        ]
        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search",
                          return_value="1. result.") as ms:
            pack = chat._getResponsePackedWithWebSearch(llm, messages)

        self.assertEqual(pack.assistant_reply_ENG, "Here is what I found.")
        self.assertEqual(len(log), 2)  # triage + phase 2 - judgement call skipped
        # the query is the extracted TOPIC, not the raw message
        ms.assert_called_once_with("the character Alyosha")
        self.assertEqual(llm.last_tools, ["AmadeusPack"])  # phase 2 last

    def test_explicit_request_with_failed_final_call_returns_honest_pack(self):
        def handler(convo):
            raise _Fake429()

        messages = [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "Can you search the web about Mitya?"},
        ]
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search", return_value="1. result."):
            pack = chat._getResponsePackedWithWebSearch(
                FakeLLM(handler, []), messages)

        self.assertIn("temporary glitch", pack.assistant_reply_ENG)
        self.assertNotIn("Just a moment", pack.assistant_reply_ENG)



# --- Repetition-loop guard, anti-anchoring nudge, daily-quota skip ------------

LOOP_TAIL = " What's the next question? ... Hmph." * 10
ANSWER_PREFIX = ("Pavlina is a playable character in Genshin Impact, "
                 "revealed in the latest update.")


class RepetitionDetectorTests(unittest.TestCase):
    def test_english_cycle_is_detected(self):
        cut = chat._repetition_cut(ANSWER_PREFIX + LOOP_TAIL)
        self.assertIsNotNone(cut)
        self.assertGreater(cut, 0)

    def test_japanese_cycle_is_detected(self):
        self.assertIsNotNone(chat._repetition_cut("次の質問は？ふん。" * 10))

    def test_clean_text_is_not_flagged(self):
        text = (ANSWER_PREFIX + " She carries a bow and her elemental "
                "affinity is hydro, which pairs well with electro characters "
                "in a team, so the community has been speculating about her "
                "role in the upcoming story arc.")
        self.assertIsNone(chat._repetition_cut(text))

    def test_de_loop_keeps_the_prefix(self):
        cleaned = chat._de_loop(ANSWER_PREFIX + LOOP_TAIL)
        self.assertIn("Genshin Impact", cleaned)
        self.assertLess(cleaned.count("next question"), 2)
        self.assertGreaterEqual(len(cleaned), 40)
        self.assertIsNone(chat._repetition_cut(cleaned))

    def test_pure_cycle_has_no_usable_prefix(self):
        cleaned = chat._de_loop(LOOP_TAIL.strip())
        self.assertLess(len(cleaned), 40)


class WebLoopLoopGuardTests(unittest.TestCase):
    """A final answer that degenerated into a repetition loop must never reach
    the UI: cut the loop out, or (no clean part) one bounded retry, or the
    honest line."""

    def _run(self, handler, messages=None):
        log = []
        llm = FakeLLM(handler, log)
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search", return_value="1. result."):
            pack = chat._getResponsePackedWithWebSearch(llm, messages or MESSAGES)
        return pack, log, llm

    def test_looped_final_answer_is_cut_to_the_clean_part(self):
        def handler(convo):
            last = str(convo[-1].get("content", ""))
            if "Translate" in last:  # the salvage translation pass
                return FakeReply(content="こんにちは、これが翻訳です。")
            return FakeReply(content=ANSWER_PREFIX + LOOP_TAIL)

        pack, _, _ = self._run(handler)
        self.assertIn("Genshin Impact", pack.assistant_reply_ENG)
        self.assertLess(pack.assistant_reply_ENG.count("next question"), 2)

    def test_pure_looped_answer_returns_honest_pack(self):
        def handler(convo):
            return FakeReply(content=LOOP_TAIL.strip())

        pack, _, _ = self._run(handler)
        self.assertIn("temporary glitch", pack.assistant_reply_ENG)


class RefusalOverrideTests(unittest.TestCase):
    """The captured incident: history of 'I can't search' refusals, web turned
    back ON, user says 'try again'. Even if the model refuses AGAIN in prose
    (ignoring every prompt instruction), the app must run the search itself.
    """

    MESSAGES = [
        {"role": "system", "content": "persona"},
        {"role": "user",
         "content": "Can you search info about the character Pavlina?"},
        {"role": "assistant", "content": "I can't search the web right now."},
        {"role": "user",
         "content": ("Ok, try again, now I turned it on. Sorry, I'm testing "
                     "your web functionalities." + " " * 300)},
    ]

    def _run(self, handler, messages=None):
        log = []
        llm = FakeLLM(handler, log)
        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search",
                          return_value="1. Pavlina - Genshin Impact character.") as ms:
            pack = chat._getResponsePackedWithWebSearch(
                llm, messages or self.MESSAGES)
        return pack, log, ms

    def test_retry_after_refusal_triggers_app_level_search(self):
        n = 0

        def handler(convo):
            nonlocal n
            n += 1
            if n == 1:  # phase 1: she refuses in prose again, no tool call
                return FakeReply(content="I'm sorry, I still can't search.")
            return FakeReply(tool_calls=pack_call(
                eng="According to my search, Pavlina is a Genshin "
                    "Impact character."))

        pack, log, ms = self._run(handler)
        self.assertIn("Genshin Impact", pack.assistant_reply_ENG)
        ms.assert_called_once()  # the app ran the search itself
        self.assertIn("Pavlina", ms.call_args[0][0])
        # the results were fed back into the context before the final call
        self.assertIn("Web search results for", log[1][-1]["content"])

    def test_no_override_without_retry_phrasing(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "assistant", "content": "I can't search the web right now."},
            {"role": "user", "content": "what's your favorite color?"},
        ]

        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="White, I guess."))

        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search") as ms:
            pack = chat._getResponsePackedWithWebSearch(FakeLLM(handler, []), messages)

        self.assertEqual(pack.assistant_reply_ENG, "White, I guess.")
        ms.assert_not_called()  # ordinary turn: no app-level search


class NudgeNoteTests(unittest.TestCase):
    def _phase1_convo(self, messages):
        log = []

        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="ok"))

        with patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search"):
            chat._getResponsePackedWithWebSearch(FakeLLM(handler, log), messages)
        self.assertTrue(log)
        return log[0]

    def test_note_present_with_recent_refusal(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "assistant",
             "content": "I don't actually have the capability to search."},
            {"role": "user", "content": "which version did that character debut in?"},
        ]
        convo = self._phase1_convo(messages)
        self.assertTrue(any(
            "web access has just been turned ON" in str(m.get("content", ""))
            for m in convo))

    def test_note_absent_without_refusal(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "assistant", "content": "Sure, that sounds fun."},
            {"role": "user", "content": "which version did that character debut in?"},
        ]
        convo = self._phase1_convo(messages)
        self.assertFalse(any(
            "web access has just been turned ON" in str(m.get("content", ""))
            for m in convo))


class DailyQuotaTests(unittest.TestCase):
    def setUp(self):
        chat._RATE_LIMIT_STATE["last_wait"] = 0.0

    def _daily_429(self):
        exc = Exception(
            "Error code: 429 - {'error': {'status': 'RESOURCE_EXHAUSTED', "
            "'message': 'Quota exceeded for metric: projects/x/"
            "generate_content_free_tier_requests, limit: 20, model: "
            "gemini-3.6-flash. quotaId: "
            "GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20'}}")
        exc.status_code = 429
        return exc

    def test_detection(self):
        self.assertTrue(chat._is_daily_quota_exhausted(self._daily_429()))
        self.assertFalse(chat._is_daily_quota_exhausted(_Fake429()))
        self.assertFalse(chat._is_daily_quota_exhausted(
            Exception("429 Too Many Requests, retry after 60s")))

    def test_daily_quota_raises_without_waiting(self):
        sleeps = []

        def handler(convo):
            raise self._daily_429()

        with patch.object(chat.time, "sleep", side_effect=sleeps.append):
            with self.assertRaises(Exception):
                chat._invoke_forced(FakeBound(handler, []),
                                    [{"role": "user", "content": "hi"}])
        self.assertEqual(sleeps, [])  # no wait: waiting cannot fix a daily quota



# --- Good-night regression: looped phase-1 answer + web-ON false-refusal net ---


class Phase2ContextSanitizeTests(unittest.TestCase):
    """A looped phase-1 prose answer must not be fed back into the final
    call's context (it primes the same degeneration); clean answers, however
    short, pass through untouched."""

    def _run(self, handler, messages=None):
        log = []
        llm = FakeLLM(handler, log)
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search", return_value="1. result."):
            pack = chat._getResponsePackedWithWebSearch(llm, messages or MESSAGES)
        return pack, log, llm

    def test_looped_phase1_answer_is_not_fed_back(self):
        def handler(convo):
            if len(convo) <= len(MESSAGES):
                return FakeReply(content="おやすみ。" * 40)  # phase 1 loop
            return FakeReply(tool_calls=pack_call(
                eng="Good night! Sweet dreams."))

        pack, log, _ = self._run(handler)
        self.assertEqual(pack.assistant_reply_ENG, "Good night! Sweet dreams.")
        # the loop must not have reached the final call's context
        for m in log[1]:
            self.assertLess(str(m.get("content", "")).count("おやすみ。"), 3,
                            m.get("content", "")[:80])

    def test_clean_short_phase1_answer_still_fed_back(self):
        def handler(convo):
            if len(convo) <= len(MESSAGES):
                return FakeReply(content="おやすみ。")  # a normal short answer
            return FakeReply(tool_calls=pack_call(
                eng="Good night! Sweet dreams."))

        pack, log, _ = self._run(handler)
        self.assertIn("おやすみ。", [str(m.get("content")) for m in log[1]
                                     if m.get("role") == "assistant"])


class WebOnFalseRefusalNetTests(unittest.TestCase):
    """Web ON + no search requested + a 'can't search' claim in the reply is a
    false statement about the settings and gets ONE corrective rewrite; a
    refusal after a real search request is never touched."""

    MESSAGES = [
        {"role": "system", "content": "persona"},
        {"role": "assistant",
         "content": "I couldn't actually finish the search this time."},
        {"role": "user", "content": "good night"},
    ]

    def _run(self, handler, messages=None):
        log = []
        llm = FakeLLM(handler, log)
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_run_web_search", return_value="1. result."):
            pack = chat._getResponsePackedWithWebSearch(llm, messages or self.MESSAGES)
        return pack, log, llm

    def test_false_refusal_is_rewritten(self):
        n = 0

        def handler(convo):
            nonlocal n
            n += 1
            if n == 1:  # phase 1: direct answer carrying the false refusal
                return FakeReply(tool_calls=pack_call(
                    eng=("Sorry, I can't use web search in this session right "
                        "now. But good night! See you tomorrow.")))
            return FakeReply(tool_calls=pack_call(
                eng=("Good night! You tested me all day - that made me "
                    "happy. See you tomorrow.")))

        pack, log, _ = self._run(handler)
        self.assertEqual(n, 2)  # exactly one corrective rewrite
        self.assertNotIn("can't use web", pack.assistant_reply_ENG.lower())
        self.assertIn("Good night", pack.assistant_reply_ENG)

    def test_refusal_kept_when_user_asked_for_search(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "Can you search the weather in Paris?"},
        ]

        def handler(convo):
            return FakeReply(tool_calls=pack_call(
                eng="I'm sorry, I can't search the web right now."))

        n = 0

        def counting(convo):
            nonlocal n
            n += 1
            return handler(convo)

        pack, _, _ = self._run(counting, messages)
        self.assertEqual(n, 1)  # no rewrite: after a real request a refusal is honest
        self.assertIn("can't search", pack.assistant_reply_ENG)

    def test_rewrite_still_refusing_serves_original(self):
        n = 0

        def handler(convo):
            nonlocal n
            n += 1
            return FakeReply(tool_calls=pack_call(
                eng="I can't use web search, but good night!"))

        pack, _, _ = self._run(handler)
        self.assertEqual(n, 2)  # rewrite attempted once, then give up
        self.assertIn("can't use web", pack.assistant_reply_ENG)  # original served


if __name__ == "__main__":
    unittest.main()

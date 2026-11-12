# -*- coding: utf-8 -*-
"""Cloud servers stall: the re-send ("stalled? send it again").

llm._ThoughtFreeChatOpenAI._generate re-sends a stalled request once for
every client whose server is NOT local (not localhost / private LAN /
link-local / .local - every cloud provider and a custom internet address;
her built-in model at 127.0.0.1 is local by the same check). Local and
LAN servers never get it: one graphics card, a second copy of the request
only makes both slower, and slow answers are normal there.

The patience is llm._cloud_patience (override env var
AMADEUS_CLOUD_PATIENCE); the reasoning level is read from whatever shape
the host uses (llm._effort_level). The race rules are covered here with
fakes only (the parent ChatOpenAI._generate is patched - no network), so
every test is a fraction of a second. The patience is set through the env
var (the table itself is pure and tested directly).
"""
import io
import os
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import llm
import timing
import vertex_auth  # noqa: F401  (the app code under test)

from langchain_core.messages import HumanMessage

# Cloud addresses (the rule is ON for all of them): Vertex (the address the
# stall was measured on), the other providers, and a public custom address.
VERTEX = "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi"
OPENAI = "https://api.openai.com/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/openai"
CUSTOM_PUBLIC = "https://some-public-host.example/v1"
# Local addresses (the rule is OFF for all of them): loopback (her built-in
# model is 127.0.0.1), localhost, the LAN, IPv6 loopback, .local names.
LOOPBACK = "http://127.0.0.1:8000/v1"
LOCALHOST = "http://localhost:8000/v1"
LAN = "http://192.168.0.10:8000/v1"
LAN10 = "http://10.0.0.5:8000/v1"
LOOPBACK_V6 = "http://[::1]:8000/v1"
DOT_LOCAL = "http://home-lab.local:8000/v1"

# What the per-host fields _build_client writes look like in a client's
# extra_body (Settings -> Reasoning, from "off" to "high").
EFFORT_SHAPES = {
    "openai": {"reasoning_effort": "high"},
    "gemini-api": {"reasoning_effort": "minimal"},
    "vertex": {"reasoning_effort": "low"},
    "openrouter-high": {"reasoning": {"effort": "high"}},
    "openrouter-off": {"reasoning": {"enabled": False}},
}


class _Refused(Exception):
    """A 400 that names the reasoning setting (path A: a softer setting)."""
    status_code = 400

    def __str__(self):
        return ("Error code: 400 - Reasoning is mandatory for this endpoint "
                "and cannot be disabled.")


class _RefusedPlain(Exception):
    """A 400 that does not say why (path B: one try without the field)."""
    status_code = 400

    def __str__(self):
        return "Error code: 400 - Bad Request"


class _Scripted:
    """The scripted fate of one attempt: `steps` is a list of
    (delay, outcome), one entry per call of the parent _generate (the
    attempt's first call, then its fallback retry; the last step repeats).
    outcome: a value -> returned, an Exception instance -> raised, None ->
    a fresh "result-<thread name>" is returned. `calls` records each call:
    {"name", "extra_body"} - "<client>" means no per-request override was
    sent (the body came from the client's own attribute), so the fallback
    retries' per-request bodies are visible here."""

    def __init__(self, steps):
        self.steps = steps
        self.calls = []

    def __call__(self, self_, messages, stop=None, run_manager=None, **kw):
        step = self.steps[min(len(self.calls), len(self.steps) - 1)]
        self.calls.append({"name": threading.current_thread().name,
                           "extra_body": kw.get("extra_body", "<client>")})
        delay, outcome = step
        if delay:
            time.sleep(delay)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return "result-" + threading.current_thread().name
        return outcome


class PatienceTableTests(unittest.TestCase):
    """_cloud_patience: small (<8,000 chars) vs large prompt, the
    reasoning level multiplier (off/minimal/low x1, medium x2, high x3),
    and the AMADEUS_CLOUD_PATIENCE override."""

    def setUp(self):
        # The table must not be masked by an override left in the env.
        self._had = os.environ.get("AMADEUS_CLOUD_PATIENCE")
        os.environ.pop("AMADEUS_CLOUD_PATIENCE", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._had is None:
            os.environ.pop("AMADEUS_CLOUD_PATIENCE", None)
        else:
            os.environ["AMADEUS_CLOUD_PATIENCE"] = self._had

    SMALL = [HumanMessage("hi")]
    LARGE = [HumanMessage("x" * 9000)]

    def test_small_prompt_by_effort(self):
        self.assertEqual(llm._cloud_patience(self.SMALL, "off"), 12.0)
        self.assertEqual(llm._cloud_patience(self.SMALL, "minimal"), 12.0)
        self.assertEqual(llm._cloud_patience(self.SMALL, "low"), 12.0)
        self.assertEqual(llm._cloud_patience(self.SMALL, "medium"), 24.0)
        self.assertEqual(llm._cloud_patience(self.SMALL, "high"), 36.0)

    def test_large_prompt_by_effort(self):
        self.assertEqual(llm._cloud_patience(self.LARGE, "off"), 30.0)
        self.assertEqual(llm._cloud_patience(self.LARGE, "minimal"), 30.0)
        self.assertEqual(llm._cloud_patience(self.LARGE, "low"), 30.0)
        self.assertEqual(llm._cloud_patience(self.LARGE, "medium"), 60.0)
        self.assertEqual(llm._cloud_patience(self.LARGE, "high"), 90.0)

    def test_the_split_is_exactly_eight_thousand_characters(self):
        self.assertEqual(llm._cloud_patience([HumanMessage("x" * 7999)], "low"), 12.0)
        self.assertEqual(llm._cloud_patience([HumanMessage("x" * 8000)], "low"), 30.0)

    def test_unknown_or_missing_effort_waits_like_off(self):
        self.assertEqual(llm._cloud_patience(self.SMALL, None), 12.0)
        self.assertEqual(llm._cloud_patience(self.SMALL, ""), 12.0)
        self.assertEqual(llm._cloud_patience(self.SMALL, "crazy"), 12.0)
        self.assertEqual(llm._cloud_patience(self.LARGE, "CRAZY"), 30.0)

    def test_multimodal_content_lists_count_their_text_parts(self):
        message = HumanMessage(content=[{"type": "text", "text": "hello"},
                                        {"type": "image_url",
                                         "image_url": {"url": "x" * 9000}}])
        # 5 chars of text (the image part has no text) -> small prompt.
        self.assertEqual(llm._cloud_patience([message], "low"), 12.0)

    def test_plain_string_and_dict_messages_count_too(self):
        self.assertEqual(llm._cloud_patience(["x" * 9000], "low"), 30.0)
        self.assertEqual(llm._cloud_patience([{"content": "x" * 9000}], "low"), 30.0)

    def test_env_override_wins_over_the_table(self):
        os.environ["AMADEUS_CLOUD_PATIENCE"] = "0.25"
        self.assertEqual(llm._cloud_patience(self.SMALL, "high"), 0.25)
        self.assertEqual(llm._cloud_patience(self.LARGE, "off"), 0.25)

    def test_env_override_zero_or_junk_means_off(self):
        for value in ("0", "0.0", "-1", "abc"):
            with self.subTest(value=value):
                os.environ["AMADEUS_CLOUD_PATIENCE"] = value
                self.assertEqual(llm._cloud_patience(self.SMALL, "high"), 0.0)
                self.assertEqual(llm._cloud_patience(self.LARGE, "low"), 0.0)


class EffortShapeTests(unittest.TestCase):
    """_effort_level: the reasoning level read from whichever shape the
    client was built with (the per-host fields _build_client writes)."""

    def test_the_shapes_each_host_writes(self):
        self.assertEqual(llm._effort_level(EFFORT_SHAPES["openai"]), "high")
        self.assertEqual(llm._effort_level(EFFORT_SHAPES["gemini-api"]), "minimal")
        self.assertEqual(llm._effort_level(EFFORT_SHAPES["vertex"]), "low")
        self.assertEqual(llm._effort_level(EFFORT_SHAPES["openrouter-high"]), "high")
        # OpenRouter with thinking off: the shape says "enabled": False.
        self.assertEqual(llm._effort_level(EFFORT_SHAPES["openrouter-off"]), "off")

    def test_the_level_is_case_insensitive_and_ignores_other_fields(self):
        self.assertEqual(llm._effort_level({"reasoning_effort": "MEDIUM"}), "medium")
        self.assertEqual(llm._effort_level({"reasoning_effort": "low", "top_k": 5}), "low")
        self.assertEqual(llm._effort_level({"reasoning": {"effort": "High"}}), "high")

    def test_missing_or_unknown_shapes_wait_like_off(self):
        self.assertEqual(llm._effort_level({}), "")
        self.assertEqual(llm._effort_level(None), "")
        self.assertEqual(llm._effort_level({"top_k": 5}), "")
        self.assertEqual(llm._effort_level({"reasoning_effort": 3}), "")
        self.assertEqual(llm._effort_level({"reasoning": "high"}), "")
        self.assertEqual(llm._effort_level({"reasoning": {"effort": 3}}), "")
        self.assertEqual(llm._effort_level({"reasoning": {}}), "")

    def test_each_shape_feeds_the_table(self):
        small = [HumanMessage("hi")]
        # The levels the shapes yield, through the table: high x3,
        # minimal/off/low x1 - the OpenRouter "enabled": False client waits
        # like "off".
        self.assertEqual(
            llm._cloud_patience(small, llm._effort_level(EFFORT_SHAPES["openrouter-high"])), 36.0)
        self.assertEqual(
            llm._cloud_patience(small, llm._effort_level(EFFORT_SHAPES["gemini-api"])), 12.0)
        self.assertEqual(
            llm._cloud_patience(small, llm._effort_level(EFFORT_SHAPES["openrouter-off"])), 12.0)
        self.assertEqual(
            llm._cloud_patience(small, llm._effort_level(EFFORT_SHAPES["vertex"])), 12.0)
        self.assertEqual(
            llm._cloud_patience(small, llm._effort_level(EFFORT_SHAPES["openai"])), 36.0)


class RaceBase(unittest.TestCase):
    """A real _ThoughtFreeChatOpenAI pointed at the Vertex address (the
    stall was measured there; the rule is host-independent, so the same
    races hold on any cloud address), whose parent _generate is scripted;
    AMADEUS_CLOUD_PATIENCE is small, so the whole race plays out in a
    fraction of a second."""
    PATIENCE = "0.2"

    def setUp(self):
        self._had = os.environ.get("AMADEUS_CLOUD_PATIENCE")
        os.environ["AMADEUS_CLOUD_PATIENCE"] = self.PATIENCE
        self.addCleanup(self._restore)
        self.client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url=VERTEX)

    def _restore(self):
        if self._had is None:
            os.environ.pop("AMADEUS_CLOUD_PATIENCE", None)
        else:
            os.environ["AMADEUS_CLOUD_PATIENCE"] = self._had

    def _fake(self, first, second):
        def fake(self_, messages, stop=None, run_manager=None, **kw):
            name = threading.current_thread().name
            script = second if name.endswith("-try-2") else first
            return script(self_, messages, stop=stop, run_manager=run_manager, **kw)
        return fake

    def _run(self, fake, client=None, messages=None):
        """client._generate(...) with `fake` standing in for the parent
        _generate (the cloud re-send runs each attempt in a thread named
        amadeus-cloud-try-1 / -2; the single-attempt paths call it in the
        calling thread). Returns (outcome, calls, log): the fake's outcome
        (its return value, or the exception that decided the call), the
        recorded calls, and all of stdout.

        The attempt threads are daemon threads: after the call settles, a
        late one would wake up and call the REAL client (a network call to
        the provider, which these tests must not make) - so the fake keeps
        answering ("late") until every attempt thread has exited, and
        _run holds the threads to that point while the patch is still in."""
        state = {"closed": False}

        def guarded(self_, messages, stop=None, run_manager=None, **kw):
            name = threading.current_thread().name
            if state["closed"]:
                return "late"
            return fake(self_, messages, stop=stop, run_manager=run_manager, **kw)

        log = io.StringIO()
        with patch.object(llm.ChatOpenAI, "_generate", guarded), redirect_stdout(log):
            try:
                try:
                    target = client if client is not None else self.client
                    result = target._generate(messages if messages is not None else [])
                except Exception as exc:
                    result = exc
            finally:
                state["closed"] = True
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                pending = [t for t in threading.enumerate()
                           if t is not threading.current_thread()
                           and t.name.startswith("amadeus-cloud-try-") and t.is_alive()]
                if not pending:
                    break
                for t in pending:
                    t.join(0.05)
        return result, log.getvalue()


class CloudRaceTests(RaceBase):
    """The rule is ON for every cloud server: the same races as on the
    Vertex address hold on the other providers and a public custom address."""

    def test_the_race_holds_on_a_public_custom_address(self):
        client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url=CUSTOM_PUBLIC)
        first = _Scripted([(0.4, "late-first")])   # still silent after the patience
        second = _Scripted([(0.05, "second")])
        result, log = self._run(self._fake(first, second), client)
        self.assertEqual(result, "second")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)      # exactly one re-send
        self.assertEqual(log.count("has not answered"), 1)
        self.assertIn("The model server has not answered after 0.2 s", log)
        self.assertIn("sending the request again", log)

    def test_the_race_holds_on_openai_and_openrouter(self):
        for base in (OPENAI, OPENROUTER):
            with self.subTest(base=base):
                client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url=base)
                first = _Scripted([(0.4, "late-first")])
                second = _Scripted([(0.05, "second")])
                result, log = self._run(self._fake(first, second), client)
                self.assertEqual(result, "second")
                self.assertEqual(log.count("has not answered"), 1)

    def test_the_openrouter_shape_of_the_effort_feeds_the_same_race(self):
        # A thinking OpenRouter client (extra_body {"reasoning": {"effort":
        # "medium"}}): the rule is host- and shape-independent - a stalled
        # request on it is re-sent like on any other cloud server.
        client = llm._ThoughtFreeChatOpenAI(
            model="m", api_key="k", base_url=OPENROUTER,
            extra_body={"reasoning": {"effort": "medium"}})
        first = _Scripted([(0.4, "late-first")])
        second = _Scripted([(0.05, "second")])
        result, log = self._run(self._fake(first, second), client)
        self.assertEqual(result, "second")
        self.assertIn("The model server has not answered after 0.2 s", log)

    def test_fast_answer_is_one_attempt_and_no_log_line(self):
        first = _Scripted([(0.05, "fast")])
        second = _Scripted([(0.05, "second")])
        result, log = self._run(self._fake(first, second))
        self.assertEqual(result, "fast")
        self.assertEqual(first.calls[0]["name"], "amadeus-cloud-try-1")
        self.assertEqual(second.calls, [])
        self.assertNotIn("has not answered", log)

    def test_stall_then_re_send_wins_with_the_second_attempt(self):
        first = _Scripted([(1.5, "late-first")])   # still silent after the patience
        second = _Scripted([(0.05, "second")])
        result, log = self._run(self._fake(first, second))
        self.assertEqual(result, "second")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)      # exactly one re-send
        self.assertEqual(log.count("has not answered"), 1)
        self.assertIn("The model server has not answered after 0.2 s", log)
        self.assertIn("sending the request again", log)

    def test_stall_but_the_first_answers_before_the_second(self):
        first = _Scripted([(0.5, "first")])         # answers after the re-send went out
        second = _Scripted([(2.0, "second")])       # ...but later than the first
        result, log = self._run(self._fake(first, second))
        self.assertEqual(result, "first")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)
        self.assertEqual(log.count("has not answered"), 1)

    def test_first_fails_quickly_is_raised_without_a_re_send(self):
        first = _Scripted([(0.02, _RefusedPlain())])
        second = _Scripted([(0.05, "second")])
        result, log = self._run(self._fake(first, second))
        self.assertIsInstance(result, _RefusedPlain)
        self.assertEqual(second.calls, [])
        self.assertNotIn("has not answered", log)

    def test_second_fails_but_the_still_hanging_first_answers(self):
        first = _Scripted([(0.5, "first")])
        second = _Scripted([(0.05, _RefusedPlain())])
        result, log = self._run(self._fake(first, second))
        self.assertEqual(result, "first")
        self.assertEqual(log.count("has not answered"), 1)

    def test_both_attempts_fail_raises_the_first_attempts_error(self):
        first_error = _RefusedPlain()                 # fails after the re-send went out
        second_error = _RefusedPlain()
        first = _Scripted([(0.4, first_error)])
        second = _Scripted([(0.05, second_error)])
        result, log = self._run(self._fake(first, second))
        # The rule: when both attempts fail, the FIRST attempt's error is
        # what the caller sees (exactly what one failed call raises today).
        self.assertIs(result, first_error)
        self.assertIsNot(result, second_error)
        self.assertEqual(log.count("has not answered"), 1)

    def test_env_zero_turns_the_re_send_off_on_a_cloud_client(self):
        os.environ["AMADEUS_CLOUD_PATIENCE"] = "0"
        first = _Scripted([(0.05, "one")])
        second = _Scripted([(0.05, "second")])
        result, log = self._run(self._fake(first, second))
        self.assertEqual(result, "one")
        # One attempt, in the calling thread - no worker, no waiting.
        self.assertEqual(first.calls[0]["name"], threading.current_thread().name)
        self.assertEqual(second.calls, [])
        self.assertNotIn("has not answered", log)


class LocalRuleOffTests(RaceBase):
    """The rule is OFF for a local / LAN server: one attempt in the calling
    thread even when the server takes longer than the patience, no worker
    thread, no log line (one graphics card - a second copy would only make
    both copies slower, and slow answers are normal there)."""
    STALL = 0.3     # longer than the 0.2 s patience

    def _stall_once(self, base_url, extra_body=None):
        client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url=base_url,
                                            extra_body=extra_body)
        first = _Scripted([(self.STALL, "slow-but-one")])
        second = _Scripted([(0.05, "second")])
        result, log = self._run(self._fake(first, second), client)
        self.assertEqual(result, "slow-but-one")
        # The ONE attempt ran in the calling thread (no worker, no wait).
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(first.calls[0]["name"], threading.current_thread().name)
        self.assertEqual(second.calls, [])
        self.assertNotIn("has not answered", log)
        self.assertFalse(any(t.name.startswith("amadeus-cloud-try-")
                             for t in threading.enumerate()))

    def test_loopback_never_resent(self):
        self._stall_once(LOOPBACK)

    def test_localhost_never_resent(self):
        self._stall_once(LOCALHOST)

    def test_lan_addresses_never_resent(self):
        for base in (LAN, LAN10):
            with self.subTest(base=base):
                self._stall_once(base)

    def test_ipv6_loopback_never_resent(self):
        self._stall_once(LOOPBACK_V6)

    def test_dot_local_names_never_resent(self):
        self._stall_once(DOT_LOCAL)

    def test_her_builtin_model_never_resent(self):
        # Her built-in model's address is 127.0.0.1 (the launcher's own
        # llama-server): the same _is_local_host check that excludes every
        # other local address also excludes it - a slow built-in answer is
        # never re-sent.
        self._stall_once("http://127.0.0.1:5501/v1")
        self.assertEqual(llm._is_local_host("http://127.0.0.1:5501/v1"), True)


class FallbackRaceTests(RaceBase):
    """The two reasoning fallbacks must keep working through the new path,
    exactly like SafetyNetTests (test_providers) but on a cloud client
    whose attempt may run in a worker thread: a single, fast attempt, no
    re-send, and the session-level result lands on the client."""

    def test_refused_reasoning_setting_is_retried_with_a_softer_one(self):
        client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url=VERTEX,
                                            extra_body={"reasoning_effort": "low"})
        first = _Scripted([(0.0, _Refused()), (0.0, "result")])
        second = _Scripted([(0.05, "second")])

        def fake(self_, messages, stop=None, run_manager=None, **kw):
            if threading.current_thread().name.endswith("-try-2"):
                return second(self_, messages, stop=stop, run_manager=run_manager, **kw)
            return first(self_, messages, stop=stop, run_manager=run_manager, **kw)

        result, log = self._run(fake, client)
        self.assertEqual(result, "result")
        self.assertEqual(second.calls, [])             # no re-send: it answered in time
        self.assertNotIn("has not answered", log)
        # The first attempt's two calls: its own body, then the per-request
        # override (softer or None = no body at all for this request).
        self.assertEqual(len(first.calls), 2)
        self.assertEqual(first.calls[0]["extra_body"], "<client>")
        self.assertIsNone(first.calls[1]["extra_body"])
        # The client keeps what the server accepted (the field is popped for
        # a non-minimal setting), applied from the calling thread.
        self.assertIsNone(client.extra_body)

    def test_unexplained_400_gets_one_plain_try_and_the_client_keeps_it(self):
        client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url=VERTEX,
                                            extra_body={"reasoning_effort": "medium",
                                                        "top_k": 5})
        first = _Scripted([(0.0, _RefusedPlain()), (0.0, "result")])
        second = _Scripted([(0.05, "second")])

        def fake(self_, messages, stop=None, run_manager=None, **kw):
            if threading.current_thread().name.endswith("-try-2"):
                return second(self_, messages, stop=stop, run_manager=run_manager, **kw)
            return first(self_, messages, stop=stop, run_manager=run_manager, **kw)

        result, log = self._run(fake, client)
        self.assertEqual(result, "result")
        self.assertEqual(second.calls, [])
        self.assertEqual(len(first.calls), 2)
        self.assertEqual(first.calls[0]["extra_body"], "<client>")
        self.assertEqual(first.calls[1]["extra_body"], {"top_k": 5})   # the plain body, per-request
        self.assertEqual(client.extra_body, {"top_k": 5})


class TimingCarryOverTests(RaceBase):
    """timing keeps its reports per thread (a stage's worker thread); the
    race must hand the WINNING attempt's reports to the calling thread, and
    drop the loser's."""

    def test_the_winners_reports_reach_the_calling_thread(self):
        timing.start_reports()

        def fake(self_, messages, stop=None, run_manager=None, **kw):
            name = threading.current_thread().name
            if name.endswith("-try-1"):
                time.sleep(0.6)                       # loses the race
                llm.timing.note_server_report({"usage": {"prompt_tokens": 111,
                                                         "completion_tokens": 1}})
                return "late-first"
            time.sleep(0.05)
            llm.timing.note_server_report({"usage": {"prompt_tokens": 5000,
                                                     "completion_tokens": 120}})
            return "second"

        result, log = self._run(fake)
        self.assertEqual(result, "second")
        reports = timing.take_reports()
        self.assertEqual(len(reports), 1)              # only the winner's
        self.assertEqual(reports[0]["prompt"], 5000)
        self.assertEqual(reports[0]["wrote"], 120)
        self.assertEqual(timing.take_reports(), [])    # nothing stranded here


class AddReportsTests(unittest.TestCase):
    """timing.add_reports: re-attach another thread's reports to this
    thread's list (the worker hand-over the race uses)."""

    def test_add_reports_extends_this_threads_list(self):
        timing.take_reports()                           # drain any leftovers
        timing.start_reports()
        timing.add_reports([{"prompt": 10, "wrote": 2}])
        timing.add_reports([{"prompt": 30, "wrote": 4}])
        self.assertEqual(timing.take_reports(),
                         [{"prompt": 10, "wrote": 2}, {"prompt": 30, "wrote": 4}])

    def test_add_reports_accepts_nothing_and_never_raises(self):
        timing.take_reports()
        timing.add_reports(None)
        timing.add_reports([])
        self.assertEqual(timing.take_reports(), [])


if __name__ == "__main__":
    unittest.main()

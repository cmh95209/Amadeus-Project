# -*- coding: utf-8 -*-
"""Offline checks for the greeting time-lie net (2026-10-05 rework).

The greeting prompt carries ONE locked situation the code measured
(compute_greeting_situation) and the generated pack is checked against it:
hard facts, soft voice - the net catches PROVABLY false time claims only
(absence constructions in situations where the user was never away, and
overstated gap buckets on a real return), never tone. On a violation the
caller makes ONE bounded correction retry, then serves a static
situation-safe line. Also covered: the sidecar recall block spliced into
the greeting prompt (read-only; the sidecar is faked so the tests stay
hermetic).
"""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

import chat
import memory as store
from chat import AmadeusPack


def _sit(situation, fresh=False, phrase=None):
    """A minimal situation dict for the pure net checks."""
    last_contact = None
    if phrase is not None:
        last_contact = {"phrase": phrase, "time": None,
                        "conversation_id": 1, "title": "T"}
    return {"situation": situation, "unanswered_fresh": fresh,
            "last_contact": last_contact}


def pack_call(eng, jps):
    return [{"name": "AmadeusPack",
             "args": {"assistant_reply_JPS": jps, "assistant_reply_ENG": eng}}]


class _FakeReply:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_chunks = None


class _FakeBound:
    def __init__(self, handler):
        self._handler = handler

    def invoke(self, messages):
        return self._handler(messages)


class _FakeLLM:
    """Same surface test_greeting.py uses; script entries are FakeReplies or
    Exceptions, default answer is the clean pack."""

    def __init__(self, script=None,
                 eng="So you're here. Good.", jps="ふーん、来たんだ。"):
        self.script = list(script) if script else []
        self.eng = eng
        self.jps = jps
        self.calls = 0
        self.last_messages = None

    def bind_tools(self, tools, tool_choice=None):
        return _FakeBound(self._invoke)

    def _invoke(self, messages):
        self.calls += 1
        self.last_messages = messages
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        return _FakeReply(tool_calls=pack_call(self.eng, self.jps))


def _lie_reply(eng, jps):
    return _FakeReply(tool_calls=pack_call(eng, jps))


class TimeLieNetTests(unittest.TestCase):
    """_greeting_time_lie_check: the pure EN+JA pattern matrix."""

    def test_non_return_absence_constructions_are_lies(self):
        sit = _sit("continuous")
        for eng in (
            "It's been a while since we talked.",
            "You've been gone for ages.",
            "Haven't seen you in a while.",
            "Have you been away?",
            "Since we last talked, I learned so much.",
            "Long time no see!",
            "How long has it been?",
        ):
            with self.subTest(eng=eng):
                self.assertIsNotNone(
                    chat._greeting_time_lie_check(sit, eng, ""))
        for ja in (
            "久しぶり。",
            "ずっと会えなかったね。",
            "連絡がなかったから心配した。",
            "何も言わずに出て行ったの?",
        ):
            with self.subTest(ja=ja):
                self.assertIsNotNone(
                    chat._greeting_time_lie_check(sit, "", ja))

    def test_non_return_days_scale_or_above_claims_are_lies(self):
        sit = _sit("continuous")
        for eng in (
            "It's been two days, I guess.",
            "About a week, huh.",
            "Two months since our last chat.",
            "A year feels like it passed.",
        ):
            with self.subTest(eng=eng):
                self.assertIsNotNone(
                    chat._greeting_time_lie_check(sit, eng, ""))
        for ja in (
            "二日ぶりだ。",
            "一週間ほど会ってないね。",
            "数日前以来だね。",
            "二ヶ月経ったんだ。",
            "一年前に話したよね?",
        ):
            with self.subTest(ja=ja):
                self.assertIsNotNone(
                    chat._greeting_time_lie_check(sit, "", ja))

    def test_non_return_truthful_short_lines_pass(self):
        sit = _sit("continuous")
        for eng, ja in (
            ("So you're back.", "戻って来たんだね。"),
            ("Two minutes, give or take. Your question?", "数分しか経ってないのに。質問は?"),
            ("There you are. I have a question.", "そこにいる。聞きたいことがあって。"),
        ):
            self.assertIsNone(
                chat._greeting_time_lie_check(sit, eng, ja),
                "should pass: %r / %r" % (eng, ja))

    def test_welcome_back_only_lies_on_fresh_unanswered(self):
        fresh = _sit("unanswered", fresh=True)
        stale = _sit("unanswered", fresh=False)
        self.assertIsNotNone(chat._greeting_time_lie_check(
            fresh, "Welcome back.", "おかえり。"))
        # Stale: they WERE away from the app; a welcome-back is honest.
        self.assertIsNone(chat._greeting_time_lie_check(
            stale, "Welcome back.", "おかえり。"))

    def test_return_only_overstatement_is_a_lie(self):
        # Bucket: days ("a day or two ago"). A week claim overstates; the
        # matching phrase does not.
        sit = _sit("return", phrase="a day or two ago")
        self.assertIsNotNone(chat._greeting_time_lie_check(
            sit, "It's been about a week since we talked.", ""))
        self.assertIsNotNone(chat._greeting_time_lie_check(
            sit, "", "一週間ぶりだな。"))
        self.assertIsNone(chat._greeting_time_lie_check(
            sit, "A day or two. You're back.", "一日二日ぶりか。おかえり。"))
        # Understatement passes (the truth line already names the phrase).
        self.assertIsNone(chat._greeting_time_lie_check(
            sit, "You're back.", "おかえり。"))
        # A welcome-back line is FINE on a return.
        self.assertIsNone(chat._greeting_time_lie_check(
            sit, "Welcome back.", "おかえり。"))
        # Bucket: weeks. A month claim overstates; weeks does not.
        sitw = _sit("return", phrase="a few weeks ago")
        self.assertIsNotNone(chat._greeting_time_lie_check(
            sitw, "It's been months.", ""))
        self.assertIsNone(chat._greeting_time_lie_check(
            sitw, "A few weeks, huh.", "数週間ぶりで。"))

    def test_first_meeting_treats_any_gap_claim_as_lie(self):
        sit = _sit("first")
        self.assertIsNotNone(chat._greeting_time_lie_check(
            sit, "Welcome back, it's been a week.", "一週間ぶりだ。"))
        self.assertIsNone(chat._greeting_time_lie_check(
            sit, "Hi. You're new here, I take it?", "初めまして、かな?"))

    def test_unknown_situation_flags_time_claims(self):
        sit = _sit("unknown")
        self.assertIsNotNone(chat._greeting_time_lie_check(
            sit, "It's been a few days, right?", "数日経ってるよね?"))
        self.assertIsNone(chat._greeting_time_lie_check(
            sit, "You're here, I see.", "来てるんだな。"))

    def test_claimed_scale_ignores_non_gap_time_words(self):
        # "last week" without a gap claim: the EN marker list is scale-based
        # and "week" does count as a weeks claim - so on a non-return
        # situation any week mention is flagged (conservative v1).
        sit = _sit("continuous")
        self.assertIsNotNone(chat._greeting_time_lie_check(
            sit, "Last week felt like a lifetime.", ""))


class GreetingNetEndToEndTests(unittest.TestCase):
    """generate_greeting with the net live: correction retry, fallback,
    and the sidecar recall splice (sidecar faked, temp store)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem_db = str(Path(self.tmp.name) / "memory.db")
        self.active_file = str(Path(self.tmp.name) / "active_conversation.txt")
        self.memory_block = None
        self._p_db = patch.object(store, "PATH_TO_MEMORY", self.mem_db)
        self._p_act = patch.object(store, "PATH_TO_ACTIVE_CONV",
                                   self.active_file)
        self._p_db.start()
        self._p_act.start()
        self._p_bridge = patch.object(
            chat, "cm_bridge", SimpleNamespace(
                fetch_memory_block=lambda cid: self.memory_block))
        self._p_bridge.start()

    def tearDown(self):
        self._p_db.stop()
        self._p_act.stop()
        self._p_bridge.stop()
        self.tmp.cleanup()

    def conn(self):
        c = sqlite3.connect(self.mem_db)
        store._ensure_messages_table(c)
        store._ensure_conversations(c)
        return c

    def _seed_continuous(self, minutes_ago=2):
        """One user message, minutes_ago back: CONTINUOUS same-tab."""
        now = datetime.now().astimezone()
        c = self.conn()
        cid = c.execute(
            "INSERT INTO conversations (title) VALUES ('Continuous')").lastrowid
        c.commit()
        uid = store.append_message("user", "the live question", cid)
        aid = store.append_message("assistant", "the answer", cid)
        c.execute("UPDATE messages SET created_at = ? WHERE id IN (?, ?)",
                  ((now - timedelta(minutes=minutes_ago)
                    ).strftime("%Y-%m-%d %H:%M"), uid, aid))
        c.commit()
        c.close()
        Path(self.active_file).write_text(str(cid), encoding="utf-8")
        return cid

    def _seed_return(self, hours_ago=5):
        """A user message hours_ago back (> 2h): RETURN same-tab."""
        now = datetime.now().astimezone()
        c = self.conn()
        cid = c.execute(
            "INSERT INTO conversations (title) VALUES ('Return')").lastrowid
        c.commit()
        uid = store.append_message("user", "the old question", cid)
        aid = store.append_message("assistant", "the old answer", cid)
        c.execute("UPDATE messages SET created_at = ? WHERE id IN (?, ?)",
                  ((now - timedelta(hours=hours_ago)
                    ).strftime("%Y-%m-%d %H:%M"), uid, aid))
        c.commit()
        c.close()
        Path(self.active_file).write_text(str(cid), encoding="utf-8")
        return cid

    def _run(self, fake, memory_block=None):
        self.memory_block = memory_block
        fake_ja = SimpleNamespace(build_voice_context=lambda trust: {
            "role": "system", "content": "VOICE"})
        fake_stats = SimpleNamespace(load_stat=lambda key: 62.0)
        with patch.object(chat, "get_llm", lambda *a, **k: fake), \
             patch.object(chat, "reset_llm"), \
             patch.object(chat, "_finalize", lambda pack, llm: pack), \
             patch.object(chat, "ja_voice", fake_ja), \
             patch.object(chat, "stats", fake_stats), \
             patch.object(store, "load_default_personality_messages",
                          return_value=[{"role": "system",
                                         "content": "PERSONALITY"}]):
            result = chat.generate_greeting()
        return result

    def test_clean_first_line_never_retries(self):
        fake = _FakeLLM()
        self._seed_continuous()
        pack, _aid, _cid = self._run(fake)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(pack.assistant_reply_ENG,
                         "So you're here. Good.")

    def test_violation_bounces_to_correction_once(self):
        # First call lies (week claim on a continuous), the correction call
        # is clean: exactly 2 calls, the clean line ships and is stored.
        fake = _FakeLLM(script=[
            _lie_reply("It's been a week since we talked.",
                       "一週間ぶりだね。"),
            _lie_reply("So you're here. Good.", "ふーん、来たんだ。"),
        ])
        self._seed_continuous()
        pack, _aid, _cid = self._run(fake)
        self.assertEqual(fake.calls, 2)
        self.assertEqual(pack.assistant_reply_ENG, "So you're here. Good.")
        c = self.conn()
        stored = [r[0] for r in c.execute(
            "SELECT content FROM messages WHERE greeting = 1").fetchall()]
        c.close()
        self.assertEqual(stored, ["So you're here. Good."])

    def test_double_violation_serves_static_fallback(self):
        # Both calls lie: exactly 2 LLM calls (initial + one correction),
        # then the pre-written situation-safe line ships - no third call.
        fake = _FakeLLM(
            script=[
                _lie_reply("It's been a week since we talked.",
                           "一週間ぶりだね。"),
                _lie_reply("You've been gone for ages.",
                           "ずっと会えなかった。"),
            ])
        self._seed_continuous()
        pack, _aid, _cid = self._run(fake)
        self.assertEqual(fake.calls, 2)
        en, ja = chat._GREETING_FALLBACK_LINES["continuous"]
        self.assertEqual(pack.assistant_reply_ENG, en)
        self.assertEqual(pack.assistant_reply_JPS, ja)

    def test_return_overstatement_falls_back_to_safe_line(self):
        # A return whose generated line overstates the measured gap twice:
        # the static return line ships (it contains no gap claim at all).
        fake = _FakeLLM(
            script=[
                _lie_reply("It's been about a week since we talked.",
                           "一週間ぶりだな。"),
                _lie_reply("Months, maybe a year.", "何ヶ月も経ってる。"),
            ])
        self._seed_return(hours_ago=5)
        pack, _aid, _cid = self._run(fake)
        self.assertEqual(fake.calls, 2)
        en, _ja = chat._GREETING_FALLBACK_LINES["return"]
        self.assertEqual(pack.assistant_reply_ENG, en)

    def test_correction_call_failure_serves_fallback(self):
        # The correction call itself fails (connection drop): one bounded
        # attempt, then the safe line - the greeting still ships.
        fake = _FakeLLM(
            script=[
                _lie_reply("It's been a week since we talked.",
                           "一週間ぶりだね。"),
                RuntimeError("simulated drop on the correction call"),
            ])
        self._seed_continuous()
        pack, _aid, _cid = self._run(fake)
        self.assertEqual(fake.calls, 2)
        en, _ja = chat._GREETING_FALLBACK_LINES["continuous"]
        self.assertEqual(pack.assistant_reply_ENG, en)

    def test_memory_block_spliced_when_sidecar_has_content(self):
        fake = _FakeLLM()
        self._seed_continuous()
        block = {"role": "system",
                 "content": "LONG-TERM MEMORY (recall): USER_FACTS\n- x"}
        pack, _aid, _cid = self._run(fake, memory_block=block)
        system = [m for m in fake.last_messages if m["role"] == "system"]
        self.assertEqual(len(system), 1)  # merged into the leading block
        self.assertIn("LONG-TERM MEMORY", system[0]["content"])
        self.assertIn("- x", system[0]["content"])
        # The stored line is unchanged by the recall block.
        self.assertEqual(pack.assistant_reply_ENG, "So you're here. Good.")

    def test_memory_block_none_keeps_prompt_baseline(self):
        fake = _FakeLLM()
        self._seed_continuous()
        self._run(fake, memory_block=None)
        system = [m for m in fake.last_messages if m["role"] == "system"]
        self.assertNotIn("LONG-TERM MEMORY",
                         system[0]["content"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Offline checks for cross-conversation greeting awareness (2026-09-21).

A greeting is aimed at the PERSON, not at a thread: before this change the
timing anchor was the active tab's last user message, so with a 2-day-old
conversation open in another tab, opening a 7-day-old tab made her greet
the user as if he had been gone a week. The fix (greeting path only):

  1. the absence anchor widens to the user's newest message ANYWHERE when
     it is more recent than the greeted conversation's own and within
     GREETING_ANCHOR_ELAPSED_CAP (30 days) - "how long have I been away"
     is about the user, not the tab;
  2. a bounded cross-conversation note (other_conversation_facts: at most
     3 other tabs, 120-char content each, 30-day cutoff, prompt-only)
     hands her what was said where, so she can ask how the other thing
     turned out - a digest, never a merge;
  3. a switch is a TOPIC-SWITCH acknowledgment, not a second welcome-back:
     the main anchor stays the per-tab gap, the framing is a topic change,
     the cross note is suppressed, and the previous tab (the one the user
     LEFT) is named via load_previous_tab_summary. There are NO
     staleness/cooldown gates - a line is voiced on every switch.

Normal replies are untouched: load_internal_context() without is_greeting
is scope-guarded below. All seeds are deterministic (fixed "now").
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


GREET_MARK = "The user just opened the app"
GREET_SWITCH_MARK = "The user has switched to this conversation"
CROSS_MARK = "Cross-conversation notes"
PERSONALITY_MARK = "GREET_CROSS_PERSONALITY"
VOICELINE_MARK = "GREET_CROSS_VOICELINE"

# One fixed "now" so the phrase ladder and timestamps are deterministic.
NOW = datetime(2026, 9, 21, 18, 30).astimezone()


class FakeReply:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_chunks = None


def pack_call(eng="Welcome back.", jps="おかえり。"):
    return [{"name": "AmadeusPack",
             "args": {"assistant_reply_JPS": jps, "assistant_reply_ENG": eng}}]


class _FakeBound:
    def __init__(self, handler):
        self._handler = handler

    def invoke(self, messages):
        return self._handler(messages)


class _FakeLLM:
    """Stands in for get_llm(): bind_tools(...).invoke(messages) - the exact
    surface generate_greeting() uses via _invoke_forced - records the prompt
    and answers with a canned AmadeusPack tool call."""

    def __init__(self, script=None, eng="Welcome back.", jps="おかえり。"):
        self.script = list(script) if script else []
        self.eng = eng
        self.jps = jps
        self.calls = 0
        self.last_messages = None
        self.last_tool_choice = None
        self.last_tool_names = None

    def bind_tools(self, tools, tool_choice=None):
        self.last_tool_choice = tool_choice
        self.last_tool_names = [t.get("function", {}).get("name")
                                if isinstance(t, dict) else None
                                for t in tools]
        return _FakeBound(self._invoke)

    def _invoke(self, messages):
        self.calls += 1
        self.last_messages = messages
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        return FakeReply(tool_calls=pack_call(self.eng, self.jps))


class _StoreHarness:
    """A temporary memory DB + active-conversation pointer (the harness never
    touches the real data/ files). Call close() when done - it stops the
    patches and removes the temp dir."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem_db = str(Path(self.tmp.name) / "memory.db")
        self.active_file = str(Path(self.tmp.name) / "active_conversation.txt")
        self._p_db = patch.object(store, "PATH_TO_MEMORY", self.mem_db)
        self._p_act = patch.object(store, "PATH_TO_ACTIVE_CONV",
                                   self.active_file)
        self._p_db.start()
        self._p_act.start()

    def close(self):
        self._p_db.stop()
        self._p_act.stop()
        self.tmp.cleanup()

    def conn(self):
        c = sqlite3.connect(self.mem_db)
        store._ensure_messages_table(c)
        store._ensure_conversations(c)
        return c

    def new_conv(self, title):
        c = self.conn()
        cid = c.execute("INSERT INTO conversations (title) VALUES (?)",
                        (title,)).lastrowid
        c.commit()
        c.close()
        return cid

    def set_active(self, conv_id):
        Path(self.active_file).write_text(str(conv_id), encoding="utf-8")

    def set_created(self, message_id, ts):
        c = self.conn()
        c.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                  (ts.strftime("%Y-%m-%d %H:%M"), message_id))
        c.commit()
        c.close()


def _seed_live_shape(h):
    """The live shape, deterministic: conv 1 'General' user 8d1h back;
    conv 3 'Goodnight' user 2d19h back (the global anchor); conv 4
    'Genshin' user 7d back; conv 5 'Bot' assistant-only. Returns
    (c1, c3, c4)."""
    c1 = h.new_conv("General")
    c3 = h.new_conv("Goodnight")
    c4 = h.new_conv("Genshin")
    c5 = h.new_conv("Bot")
    u1 = store.append_message(
        "user", "Haha, there are quite a few plot twists in Conan.", c1)
    a1 = store.append_message(
        "assistant", "Oh? So the culprit was still up for debate.", c1)
    u3 = store.append_message(
        "user", "oops... good night, i'll look forward to talking to you "
                "tomorrow.", c3)
    a3 = store.append_message("assistant", "Rest well.", c3)
    u4 = store.append_message(
        "user", "What are the anniversary rewards for version 7.1", c4)
    store.append_message("assistant", "hello from the bot", c5)
    h.set_created(u1, NOW - timedelta(days=8, hours=1))
    h.set_created(a1, NOW - timedelta(days=8, hours=1))
    h.set_created(u3, NOW - timedelta(days=2, hours=19))
    h.set_created(a3, NOW - timedelta(days=2, hours=18))
    h.set_created(u4, NOW - timedelta(days=7))
    return c1, c3, c4


class OtherConversationFactsTests(unittest.TestCase):
    """other_conversation_facts: the bounded cross-conversation digest."""

    def setUp(self):
        self.h = _StoreHarness()
        self.addCleanup(self.h.close)

    def test_anchor_and_others_newest_first(self):
        c1, c3, c4 = _seed_live_shape(self.h)
        facts = store.other_conversation_facts(now=NOW)
        self.assertEqual(facts["anchor"]["conversation_id"], c3)
        self.assertEqual(facts["anchor"]["phrase"], "two days ago")
        self.assertEqual(facts["anchor"]["title"], "Goodnight")
        self.assertIn("good night, i'll look forward to talking",
                      facts["anchor"]["content"])
        # Newest by time first, anchor excluded, assistant-only conv 5
        # excluded: Genshin (7d) before General (8d).
        self.assertEqual(
            [(e["conversation_id"], e["title"], e["phrase"])
             for e in facts["others"]],
            [(c4, "Genshin", "about a week ago"),
             (c1, "General", "about a week ago")])

    def test_caps_three_tabs_120_chars_30_day_cutoff(self):
        h = self.h
        c0 = h.new_conv("Anchor")
        ua = store.append_message("user", "anchor line", c0)
        h.set_created(ua, NOW - timedelta(days=1, hours=1))
        long_titles = ["A-long", "B-long", "C-long", "D-long", "E-long"]
        for i, title in enumerate(long_titles):
            c = h.new_conv(title)
            uid = store.append_message("user", "word " * 60, c)
            # 3d..7d: all within the 30-day cap.
            h.set_created(uid, NOW - timedelta(days=3 + i))
        facts = store.other_conversation_facts(now=NOW)
        self.assertEqual(len(facts["others"]), 3)  # capped at 3
        # Newest first.
        self.assertEqual([e["title"] for e in facts["others"]],
                         ["A-long", "B-long", "C-long"])
        for entry in facts["others"]:
            self.assertEqual(len(entry["content"]), 123)  # 120 + "..."
            self.assertTrue(entry["content"].endswith("..."))
        # 30-day cutoff: the 40d tab drops out entirely.
        c40 = h.new_conv("Ancient")
        u40 = store.append_message("user", "ancient line", c40)
        h.set_created(u40, NOW - timedelta(days=40))
        facts = store.other_conversation_facts(now=NOW)
        self.assertNotIn(
            c40, [e["conversation_id"] for e in facts["others"]])

    def test_empty_store(self):
        facts = store.other_conversation_facts(now=NOW)
        self.assertIsNone(facts["anchor"])
        self.assertEqual(facts["others"], [])


class ConversationAnchorTests(unittest.TestCase):
    """load_conversation_anchor: the per-tab timing anchor."""

    def setUp(self):
        self.h = _StoreHarness()
        self.addCleanup(self.h.close)

    def test_empty_conversation_is_none(self):
        cid = self.h.new_conv("Fresh")
        self.assertIsNone(store.load_conversation_anchor(cid, now=NOW))

    def test_phrase_and_content(self):
        cid = self.h.new_conv("T")
        uid = store.append_message("user", "the plot twist line", cid)
        self.h.set_created(uid, NOW - timedelta(days=7, hours=16))
        anchor = store.load_conversation_anchor(cid, now=NOW)
        self.assertEqual(anchor["phrase"], "about a week ago")
        self.assertIn("the plot twist line", anchor["content"])
        self.assertGreater(anchor["elapsed_seconds"], 7 * 86400)


class PreviousTabSummaryTests(unittest.TestCase):
    """load_previous_tab_summary: the "you were just in ..." line's data.

    (The switch gates - staleness + cooldown + greeting_activity - are gone:
    a topic-switch acknowledgment is voiced on every switch.)"""

    def setUp(self):
        self.h = _StoreHarness()
        self.addCleanup(self.h.close)

    def _seed(self):
        cA = self.h.new_conv("Old Thread")
        cB = self.h.new_conv("Fresh Thread")
        uA = store.append_message("user", "the old plot twist question", cA)
        aA = store.append_message("assistant", "the old answer line", cA)
        uB = store.append_message("user", "what are the anniversary rewards", cB)
        self.h.set_created(uA, NOW - timedelta(days=4))
        self.h.set_created(aA, NOW - timedelta(days=4))
        self.h.set_created(uB, NOW - timedelta(days=2, hours=1))
        return cA, cB

    def test_summarizes_newest_message_of_any_role(self):
        cA, cB = self._seed()
        out = store.load_previous_tab_summary(cB)
        self.assertEqual(out["conversation_id"], cB)
        self.assertEqual(out["title"], "Fresh Thread")
        self.assertEqual(out["last_message"], "what are the anniversary rewards")
        self.assertIsNotNone(out["last_message_phrase"])
        # An assistant line is usable too (the newest message of ANY role).
        conn = self.h.conn()
        a2 = conn.execute(
            "INSERT INTO messages (role, content, conversation_id, "
            "created_at) VALUES ('assistant', 'the newest assistant line', "
            "?, ?)",
            (cB, NOW.strftime("%Y-%m-%d %H:%M"))).lastrowid
        conn.commit()
        conn.close()
        out = store.load_previous_tab_summary(cB)
        self.assertEqual(out["last_message"], "the newest assistant line")

    def test_none_when_missing_empty_or_same_tab(self):
        cA, cB = self._seed()
        # Missing id.
        self.assertIsNone(store.load_previous_tab_summary(None))
        # Empty tab.
        empty = self.h.new_conv("Empty")
        self.assertIsNone(store.load_previous_tab_summary(empty))
        # The tab being entered is excluded (you can't be "just in" the tab
        # you just switched to).
        self.assertIsNone(
            store.load_previous_tab_summary(cA, exclude_conversation_id=cA))

    def test_long_line_is_collapsed_and_truncated(self):
        cA, cB = self._seed()
        longc = self.h.new_conv("Long")
        uid = store.append_message("user", "word " * 60, longc)
        self.h.set_created(uid, NOW - timedelta(days=1))
        out = store.load_previous_tab_summary(longc)
        self.assertEqual(len(out["last_message"]), 123)  # 120 + "..."
        self.assertTrue(out["last_message"].endswith("..."))


class CrossTabTimingTests(unittest.TestCase):
    """load_internal_context: the widened anchor + the cross note."""

    def setUp(self):
        self.h = _StoreHarness()
        self.addCleanup(self.h.close)

    def _seed(self):
        return _seed_live_shape(self.h)

    def test_widened_anchor_and_cross_note(self):
        c1, c3, c4 = self._seed()
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=c1)["content"]
        # The MAIN timing line is widened to the global anchor (2d19h ->
        # "two days ago"), not the tab's own 8d -> "about a week".
        self.assertIn(
            "Put casually, the last message was two days ago.",
            content)
        self.assertIn(
            "Time since previous user message: approximately 2 days, 19 hours.",
            content)
        self.assertNotIn("No previous user message", content)
        # The cross note carries both numbers, labeled, plus the other tab.
        self.assertIn(CROSS_MARK, content)
        self.assertIn(
            "Overall you last talked to me two days ago "
            "(2026-09-18 23:30), in a different conversation", content)
        self.assertIn("good night, i'll look forward to talking to you",
                      content)
        self.assertIn(
            "In THIS conversation your last message was about a week ago",
            content)
        self.assertIn("plot twists in Conan", content)
        self.assertIn("Also in \"Genshin\" (about a week ago)", content)
        self.assertIn("you just know", content)
        # Order: the cross block sits between the timing line and the
        # accuracy guard.
        self.assertLess(content.index("- " + CROSS_MARK),
                        content.index("- When you mention how long"))

    def test_greeted_tab_holds_global_anchor_no_cross_note(self):
        c1, c3, c4 = self._seed()
        self.h.set_active(c3)
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=c3)["content"]
        self.assertNotIn(CROSS_MARK, content)
        self.assertIn(
            "Put casually, the last message was two days ago.", content)
        # Zero-cost case: the (empty) cross_block must not leave a blank line.
        self.assertNotIn("- This is a return moment\n\n-", content)
        self.assertIn("This is a return moment after a real absence",
                      content)

    def test_empty_greeted_tab_widens_without_cross_note(self):
        self._seed()
        fresh = self.h.new_conv("Fresh")
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=fresh)["content"]
        self.assertIn(
            "Put casually, the last message was two days ago.", content)
        self.assertNotIn("No previous user message", content)
        self.assertNotIn(CROSS_MARK, content)

    def test_anchor_beyond_cap_keeps_tab_wording(self):
        # The ONLY user message anywhere is 40 days old (beyond the cap):
        # the empty greeted tab keeps its own wording; nothing is implied.
        stale_c = self.h.new_conv("Ancient")
        uid = store.append_message("user", "long ago", stale_c)
        self.h.set_created(uid, NOW - timedelta(days=40))
        fresh = self.h.new_conv("Fresh")
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=fresh)["content"]
        self.assertIn("No previous user message is recorded.", content)
        self.assertNotIn(CROSS_MARK, content)

    def test_normal_reply_is_scoped_out(self):
        c1, c3, c4 = self._seed()
        self.h.set_active(c1)
        content = store.load_internal_context(now=NOW)["content"]
        self.assertNotIn(CROSS_MARK, content)
        # The normal reply keeps the active tab's own anchor.
        self.assertIn(
            "Put casually, the last message was about a week ago.", content)

    def test_is_greeting_without_conversation_id_unchanged(self):
        # Regression guard: the pre-change call shape (is_greeting=True, no
        # conversation_id) still works - it cannot compare tabs, so it keeps
        # the active tab's anchor and no cross note.
        c1, c3, c4 = self._seed()
        self.h.set_active(c1)
        content = store.load_internal_context(now=NOW, is_greeting=True)["content"]
        self.assertNotIn(CROSS_MARK, content)
        self.assertIn("about a week ago", content)

    def test_switch_frame_is_a_topic_switch_not_a_welcome_back(self):
        # A switch is NOT a return: the main anchor stays the per-tab gap
        # (8d -> "about a week ago", NOT the widened 2d "two days ago"), the
        # framing is a topic change, the cross note is suppressed (the
        # prev-tab line replaces it), and the previous tab is named.
        c1, c3, c4 = self._seed()
        self.h.set_active(c1)
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=c1,
            is_return=False, previous_conversation_id=c3)["content"]
        # Main line: the per-tab anchor, NOT the widened global one (2d).
        self.assertIn(
            "Put casually, the last message was about a week ago.", content)
        self.assertNotIn(
            "Put casually, the last message was two days ago.", content)
        self.assertNotIn("This is a return moment", content)
        # The topic-switch framing is explicit.
        self.assertIn(
            "The user is now reading this conversation",
            content)
        # The cross note is suppressed on a switch...
        self.assertNotIn(CROSS_MARK, content)
        # ...and replaced by the previous-tab line.
        self.assertIn(
            '- You were just in the conversation "Goodnight"', content)
        # The prev-tab line is pure data now: no instruction tail.
        self.assertNotIn("acknowledge the topic change", content)

    def test_switch_frame_without_previous_id_has_no_prev_tab_line(self):
        c1, c3, c4 = self._seed()
        self.h.set_active(c1)
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=c1,
            is_return=False)["content"]
        self.assertIn("The user is now reading this conversation", content)
        self.assertNotIn("You were just in the conversation", content)
        self.assertNotIn(CROSS_MARK, content)

    def test_switch_frame_reports_recent_repeat_greetings(self):
        # The deterministic backstop: two of her own greeting lines in this
        # tab within the 30-minute window (a quick bounce) are told to her
        # flatly, on the switch frame only.
        c1, c3, c4 = self._seed()
        self.h.set_active(c1)
        g1 = store.append_message("assistant", "welcome back, line one",
                                  c1, is_greeting=True)
        g2 = store.append_message("assistant", "welcome back, line two",
                                  c1, is_greeting=True)
        self.h.set_created(g1, NOW - timedelta(minutes=20))
        self.h.set_created(g2, NOW - timedelta(minutes=5))
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=c1,
            is_return=False)["content"]
        self.assertIn(
            "- You have already greeted this tab 2 times in the last 30 "
            "minutes.", content)

    def test_repeat_fact_absent_below_threshold_and_in_startup_frame(self):
        c1, c3, c4 = self._seed()
        self.h.set_active(c1)
        # One recent greeting: below the threshold - no fact on a switch.
        g1 = store.append_message("assistant", "welcome back, line one",
                                  c1, is_greeting=True)
        self.h.set_created(g1, NOW - timedelta(minutes=5))
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=c1,
            is_return=False)["content"]
        self.assertNotIn("already greeted this tab", content)
        # Two recent greetings: the fact is switch-only - the startup
        # (return) frame stays clean of it.
        g2 = store.append_message("assistant", "welcome back, line two",
                                  c1, is_greeting=True)
        self.h.set_created(g2, NOW - timedelta(minutes=5))
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=c1,
            is_return=True)["content"]
        self.assertNotIn("already greeted this tab", content)


class SwitchGreetingGenerationTests(unittest.TestCase):
    """generate_greeting(mode=...) end to end against the fake LLM + real
    temp store (same pattern as test_greeting.py)."""

    def _seed(self):
        h = _StoreHarness()
        self.addCleanup(h.close)
        c1, c3, c4 = _seed_live_shape(h)
        return h, c1, c3, c4

    def _run(self, h, c1, c3, mode, conversation_id, active_for_run,
             previous_conversation_id=None):
        fake = _FakeLLM()
        fake_ja = SimpleNamespace(build_voice_context=lambda trust: {
            "role": "system", "content": VOICELINE_MARK + " (trust 62)"})
        fake_stats = SimpleNamespace(load_stat=lambda key: 62.0)
        # Faithful to the real flow: the switch run activates the target tab
        # (so build_prompt_messages returns its messages) and passes the tab
        # the user LEFT (previous_conversation_id); the default startup run
        # greets the active tab (c3, the newest one here).
        Path(h.active_file).write_text(str(active_for_run), encoding="utf-8")
        with patch.object(chat, "get_llm", lambda *a, **k: fake), \
             patch.object(chat, "reset_llm"), \
             patch.object(chat, "_finalize", lambda pack, llm: pack), \
             patch.object(chat, "ja_voice", fake_ja), \
             patch.object(chat, "stats", fake_stats), \
             patch.object(store, "load_default_personality_messages",
                          return_value=[{"role": "system",
                                         "content": PERSONALITY_MARK}]):
            try:
                result = chat.generate_greeting(
                    mode=mode, conversation_id=conversation_id,
                    previous_conversation_id=previous_conversation_id)
                exc = None
            except Exception as e:
                result, exc = None, e
        conn = sqlite3.connect(h.mem_db)
        try:
            rows = conn.execute(
                "SELECT id, role, content, conversation_id, greeting "
                "FROM messages WHERE role = 'assistant' AND greeting = 1 "
                "ORDER BY id").fetchall()
        finally:
            conn.close()
        return fake, result, exc, rows

    def test_switch_mode_re_greeting_hides_history_and_directs(self):
        # The bounce case: the target tab's last line is one of her own
        # greetings. The arrival line (prompt tail) carries the vary-topic
        # directive (live 2026-09-23: the system-notes position lost to the
        # template of her own switch lines), and the stored greeting rows
        # are hidden from the history - she cannot repeat a line she cannot
        # see - while the timing block keeps the FACTS about them.
        h, c1, c3, c4 = self._seed()
        # Two prior greetings in the target tab (the bounce case): the
        # repeat-count fact (>=2) + the last-line quote both apply, and both
        # rows must be hidden from the history. They carry distinctive
        # JAPANESE text, because the history rows render the Japanese - the
        # English text of the newest one legitimately appears in the
        # timing block's last-line quote.
        store.append_message(
            "assistant", "the old welcome-back line", c1, is_greeting=True,
            japanese="old greeting ja one")
        store.append_message(
            "assistant", "the second old switch line", c1, is_greeting=True,
            japanese="old greeting ja two")
        fake, result, exc, rows = self._run(
            h, c1, c3, mode="switch", conversation_id=c1, active_for_run=c1,
            previous_conversation_id=c3)
        self.assertIsNone(exc)
        pack, assistant_id, conv_id = result
        self.assertEqual(conv_id, c1)
        self.assertEqual(len(rows), 3)  # two old greetings + the new one
        self.assertEqual(rows[-1][3], c1)
        self.assertEqual(rows[-1][0], assistant_id)
        messages = fake.last_messages
        system = [m for m in messages if m["role"] == "system"]
        self.assertEqual(len(system), 1)
        content = system[0]["content"]
        self.assertIn(GREET_SWITCH_MARK, content)
        self.assertIn('You were just in the conversation "Goodnight"',
                      content)
        self.assertIn(
            "The user is now reading this conversation",
            content)
        # The per-tab anchor uses the REAL clock while the seed is pinned to
        # a fixed date, so the phrase is run-dependent - assert the invariant
        # instead: the main anchor is NOT the widened global one ("two days
        # ago") and NOT a return moment. (The unit test pins the exact phrase
        # with a fixed now.)
        self.assertNotIn(
            "Put casually, the last message was two days ago.", content)
        self.assertNotIn("This is a return moment", content)
        # The cross note is suppressed on a switch (the prev-tab line is the
        # re-orientation).
        self.assertNotIn(CROSS_MARK, content)
        # The directive lives on the ARRIVAL line now, not in the system
        # notes (the position it was ignored from).
        self.assertNotIn("completely different topic", content)
        self.assertEqual(
            messages[-1], {"role": "user",
                           "content": "[The user has switched to this "
                                      "conversation again - you have "
                                      "already greeted them. This time "
                                      "speak a completely different topic "
                                      "(science, trivia, ask about their "
                                      "day, etc.). Randomize it. Be sassy "
                                      "and teasing.]"})
        # History (the non-system part): BOTH old greeting rows are hidden -
        # no template to copy - checked via their Japanese text (what the
        # history rows actually render)...
        history = [m["content"] for m in messages
                   if m["role"] != "system"]
        self.assertNotIn("old greeting ja one",
                         " | ".join(history))
        self.assertNotIn("old greeting ja two",
                         " | ".join(history))
        # ...but the non-greeting history is intact.
        self.assertIn("Oh? So the culprit was still up for debate.",
                      " | ".join(history))
        # Facts about the hidden lines stay in the timing block: the
        # last-line quote (greeting tag) + the repeat count.
        self.assertIn("one of your greetings", content)
        self.assertIn("already greeted this tab 2 times", content)

    def test_switch_mode_first_switch_is_plain(self):
        # No previous greeting line in the target tab: plain arrival, full
        # history (nothing to hide), no directive.
        h, c1, c3, c4 = self._seed()
        fake, result, exc, rows = self._run(
            h, c1, c3, mode="switch", conversation_id=c1, active_for_run=c1,
            previous_conversation_id=c3)
        self.assertIsNone(exc)
        pack, assistant_id, conv_id = result
        self.assertEqual(conv_id, c1)
        messages = fake.last_messages
        self.assertEqual(
            messages[-1], {"role": "user",
                           "content": "[The user has switched to this "
                                      "conversation.]"})
        # Full history visible (the seed's assistant line is not a greeting).
        self.assertIn("Oh? So the culprit was still up for debate.",
                      " | ".join(m["content"] for m in messages))
        # No repeat fact (no greetings in the tab yet).
        system = [m for m in messages if m["role"] == "system"][0]["content"]
        self.assertNotIn("already greeted this tab", system)

    def test_startup_default_unchanged(self):
        # Default mode: the startup instruction + arrival line (regression
        # anchor for the (f)/(l) behavior); the greeting is stored in the
        # ACTIVE tab (c3 here). No switch gating at all anymore, so nothing
        # else to assert about the bookkeeping.
        h, c1, c3, c4 = self._seed()
        fake, result, exc, rows = self._run(
            h, c1, c3, mode="startup", conversation_id=None, active_for_run=c3)
        self.assertIsNone(exc)
        pack, assistant_id, conv_id = result
        self.assertEqual(conv_id, c3)
        self.assertEqual(rows[0][3], c3)
        messages = fake.last_messages
        system = [m for m in messages if m["role"] == "system"][0]["content"]
        self.assertIn(GREET_MARK, system)
        self.assertNotIn(GREET_SWITCH_MARK, system)
        self.assertNotIn("You were just in the conversation", system)
        self.assertEqual(
            messages[-1],
            {"role": "user", "content": "[The user just opened the app.]"})
        # c3 holds the global anchor -> no cross note in the startup prompt.
        self.assertNotIn(CROSS_MARK, system)


class LastAssistantLineTests(unittest.TestCase):
    """load_last_assistant_line + the 'your last line' timing fact.

    Her prompt carries no per-message timestamps, so without this fact she
    cannot tell the user JUST reopened the app or bounced back into a tab
    minutes after her last line - the signal behind 'why did you close me
    again?' and 'are you testing me?'."""

    D = 86400

    def setUp(self):
        self.h = _StoreHarness()
        self.addCleanup(self.h.close)

    def test_none_without_assistant_lines(self):
        cid = self.h.new_conv("OnlyUser")
        store.append_message("user", "hi", cid)
        self.assertIsNone(store.load_last_assistant_line(cid, now=NOW))

    def test_phrase_and_greeting_flag(self):
        cid = self.h.new_conv("T")
        store.append_message("user", "hi", cid)
        aid = store.append_message("assistant", "the answer line", cid,
                                   is_greeting=True)
        self.h.set_created(aid, NOW - timedelta(hours=3))
        out = store.load_last_assistant_line(cid, now=NOW)
        self.assertEqual(out["phrase"], "a few hours ago")
        self.assertTrue(out["is_greeting"])
        self.assertIn("the answer line", out["content"])

    def test_timing_fact_in_greeting_frames(self):
        cid = self.h.new_conv("T")
        store.append_message("user", "hi there", cid)
        aid = store.append_message("assistant", "the last thing I said",
                                   cid)
        self.h.set_created(aid, NOW - timedelta(hours=3))
        # Startup (return) frame carries the fact...
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=cid,
            is_return=True)["content"]
        self.assertIn("Your last line in this conversation was a few hours "
                      "ago: \"the last thing I said\".", content)
        # ...as does the switch frame, with the greeting tag when the last
        # line was a greeting.
        aid2 = store.append_message("assistant", "a greeting line", cid,
                                    is_greeting=True)
        self.h.set_created(aid2, NOW - timedelta(minutes=30))
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=cid,
            is_return=False)["content"]
        self.assertIn("Your last line in this conversation was less than an "
                      "hour ago and it was one of your greetings: "
                      "\"a greeting line\".", content)
        # The vary-topic directive does NOT sit in this block (live
        # 2026-09-23: the system-notes position was ignored - it lost to
        # the template of her own switch lines at the prompt tail). It now
        # rides the re-greeting arrival line (generate_greeting), so the
        # fact stays pure data in every frame.
        self.assertNotIn("completely different topic", content)
        self.assertNotIn("sassy", content)
        content = store.load_internal_context(
            now=NOW, is_greeting=True, conversation_id=cid,
            is_return=True)["content"]
        self.assertNotIn("completely different topic", content)
        # Normal replies never carry the fact.
        self.h.set_active(cid)
        content = store.load_internal_context(now=NOW)["content"]
        self.assertNotIn("Your last line in this conversation", content)


class RecentGreetingCountTests(unittest.TestCase):
    """recent_greeting_count: the deterministic 'she already said it' fact.

    The model can be told its previous switch lines are in the prompt and
    still not notice them (live 2026-09-22: five welcome-back lines on
    five quick switches), so the count is read from the stored greeting
    flag instead of being trusted to the model."""

    def setUp(self):
        self.h = _StoreHarness()
        self.addCleanup(self.h.close)

    def test_counts_only_greeting_rows_in_window_for_that_tab(self):
        cid = self.h.new_conv("T")
        store.append_message("user", "hello", cid)
        g_recent = store.append_message(
            "assistant", "welcome back, recent", cid, is_greeting=True)
        g_old = store.append_message(
            "assistant", "welcome back, old", cid, is_greeting=True)
        a_plain = store.append_message("assistant", "a normal reply", cid)
        g_other = store.append_message(
            "assistant", "a greeting in another tab",
            self.h.new_conv("Other"), is_greeting=True)
        self.h.set_created(g_recent, NOW - timedelta(minutes=5))
        self.h.set_created(g_old, NOW - timedelta(days=1))
        self.h.set_created(a_plain, NOW - timedelta(minutes=5))
        self.h.set_created(g_other, NOW - timedelta(minutes=5))
        # Only the in-tab, in-window greeting row counts: the day-old
        # greeting, the normal reply, and the other tab's greeting all
        # drop out.
        self.assertEqual(store.recent_greeting_count(cid, now=NOW), 1)
        # Widening the window brings the day-old row back.
        self.assertEqual(
            store.recent_greeting_count(
                cid, now=NOW, window_seconds=2 * 86400), 2)
        # A tab with no greetings is zero.
        fresh = self.h.new_conv("Fresh")
        self.assertEqual(store.recent_greeting_count(fresh, now=NOW), 0)


if __name__ == "__main__":
    unittest.main()

"""Calendar-aware time wording + "last seen" presence (2026-10-15).

The code does ALL the time maths and hands her one finished phrase; these
tests pin the phrase table at the tricky moments (around midnight, exactly
24 h, month / year boundaries) and the real case that started it: the user
left at 23:29 and came back at 18:09 the next day - "last night", never
"a day or two ago".
"""

from datetime import datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402 - the shared harness


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).astimezone()


class ContactPhraseTests(_FreshAppTests):
    def phrase(self, then, now):
        return self.memory._contact_phrase(then, now)

    def test_the_reported_case_is_last_night(self):
        self.assertEqual(self.phrase(at(2026, 10, 14, 23, 29), at(2026, 10, 15, 18, 9)),
                         "last night")

    def test_short_gaps_use_hours_even_across_midnight(self):
        self.assertEqual(self.phrase(at(2026, 10, 14, 23, 29), at(2026, 10, 15, 0, 40)),
                         "a couple of hours ago")
        self.assertEqual(self.phrase(at(2026, 10, 14, 22, 0), at(2026, 10, 15, 2, 30)),
                         "a few hours ago")
        self.assertEqual(self.phrase(at(2026, 10, 15, 9, 0), at(2026, 10, 15, 9, 40)),
                         "less than an hour ago")

    def test_same_day_parts(self):
        now = at(2026, 10, 15, 21, 0)
        self.assertEqual(self.phrase(at(2026, 10, 15, 2, 0), now), "last night")
        self.assertEqual(self.phrase(at(2026, 10, 15, 8, 0), now), "this morning")
        self.assertEqual(self.phrase(at(2026, 10, 15, 13, 0), now), "earlier this afternoon")
        self.assertEqual(self.phrase(at(2026, 10, 15, 6, 30), at(2026, 10, 15, 23, 59)),
                         "this morning")
        self.assertEqual(self.phrase(at(2026, 10, 15, 17, 0), at(2026, 10, 15, 23, 30)),
                         "earlier this evening")

    def test_previous_day(self):
        now = at(2026, 10, 15, 18, 0)
        self.assertEqual(self.phrase(at(2026, 10, 14, 18, 0), now), "last night")  # exactly 24 h
        self.assertEqual(self.phrase(at(2026, 10, 14, 9, 0), now), "yesterday morning")
        self.assertEqual(self.phrase(at(2026, 10, 14, 14, 0), now), "yesterday afternoon")

    def test_longer_gaps(self):
        now = at(2026, 10, 15, 18, 0)
        self.assertEqual(self.phrase(at(2026, 10, 13, 19, 0), now), "the day before yesterday")  # 47 h
        self.assertEqual(self.phrase(at(2026, 10, 12, 12, 0), now), "a few days ago")
        self.assertEqual(self.phrase(at(2026, 10, 10, 12, 0), now), "several days ago")
        self.assertEqual(self.phrase(at(2026, 10, 8, 12, 0), now), "about a week ago")
        self.assertEqual(self.phrase(at(2026, 9, 20, 12, 0), now), "a few weeks ago")

    def test_month_and_year_boundaries(self):
        self.assertEqual(self.phrase(at(2026, 9, 30, 22, 0), at(2026, 10, 1, 19, 0)),
                         "last night")
        self.assertEqual(self.phrase(at(2025, 12, 31, 23, 0), at(2026, 1, 1, 12, 0)),
                         "last night")
        self.assertEqual(self.phrase(at(2025, 12, 30, 12, 0), at(2026, 1, 1, 12, 0)),
                         "the day before yesterday")

    def test_every_phrase_has_a_scale(self):
        now = at(2026, 10, 15, 18, 0)
        thens = [at(2026, 10, 15, 17, 30), at(2026, 10, 15, 15, 0), at(2026, 10, 15, 13, 0),
                 at(2026, 10, 15, 8, 0), at(2026, 10, 15, 2, 0), at(2026, 10, 14, 20, 0),
                 at(2026, 10, 14, 9, 0), at(2026, 10, 14, 14, 0), at(2026, 10, 13, 9, 0),
                 at(2026, 10, 12, 9, 0), at(2026, 10, 10, 9, 0), at(2026, 10, 1, 9, 0),
                 at(2026, 8, 1, 9, 0), at(2025, 1, 1, 9, 0)]
        for then in thens:
            with self.subTest(then=then):
                self.assertIn(self.phrase(then, now), self.memory.CONTACT_PHRASE_SCALE)


class HistoryTimeNoteTests(_FreshAppTests):
    """A2: history notes say WHEN a message was said, in the same wording."""

    def note(self, created, prev, now):
        return self.memory._time_note(created, prev, now)

    def test_note_uses_calendar_wording_not_arithmetic(self):
        now = at(2026, 10, 15, 18, 9)
        note = self.note("2026-10-14 23:29", "2026-10-14 18:00", now)
        self.assertEqual(note, "[Time note: the following was said last night.]")
        self.assertNotRegex(note, r"\d")

    def test_no_note_without_a_real_break(self):
        self.assertIsNone(self.note("2026-10-14 23:29", "2026-10-14 22:00",
                                    at(2026, 10, 15, 18, 0)))
        self.assertIsNone(self.note("2026-10-14 23:29", None, at(2026, 10, 15, 18, 0)))
        self.assertIsNone(self.note("garbage", "2026-10-14 10:00", at(2026, 10, 15, 18, 0)))

    def test_older_notes(self):
        now = at(2026, 10, 15, 18, 0)
        self.assertEqual(self.note("2026-10-13 10:00", "2026-10-12 10:00", now),
                         "[Time note: the following was said the day before yesterday.]")
        self.assertEqual(self.note("2026-10-01 10:00", "2026-09-20 10:00", now),
                         "[Time note: the following was said about two weeks ago.]")

    def test_notes_reach_the_prompt_history(self):
        conv = self.memory.load_active_conversation()
        u1 = self.memory.append_message("user", "old question", conversation_id=conv)
        a1 = self.memory.append_message("assistant", "old answer", conversation_id=conv)
        u2 = self.memory.append_message("user", "new question", conversation_id=conv)
        import sqlite3
        db = sqlite3.connect("data/memory.db")
        db.execute("UPDATE messages SET created_at = '2020-01-01 10:00' WHERE id IN (?, ?)", (u1, a1))
        db.commit()
        db.close()
        b = self.memory.append_message("assistant", "a reply after the break",
                                       conversation_id=conv)
        texts = [m["content"] for m in self.memory.load_memory_for_prompt(conv)]
        noted = [t for t in texts if t.startswith("[Time note:")]
        self.assertEqual(len(noted), 0)  # the newest user line stays clean
        u3 = self.memory.append_message("user", "and another", conversation_id=conv)
        texts = [m["content"] for m in self.memory.load_memory_for_prompt(conv)]
        self.assertTrue(any(t.startswith("[Time note: the following was said less than an hour ago.]")
                            for t in texts), texts)
        self.assertTrue(all(isinstance(x, int) for x in (u2, b, u3)))


class CalendarTimeCheckTests(_FreshAppTests):
    """C: the time check knows "last night" is hours-scale."""

    def sit(self, phrase, situation="return"):
        return {"situation": situation, "unanswered_fresh": False,
                "last_contact": {"phrase": phrase, "time": at(2026, 10, 14, 23, 29),
                                 "title": "T", "conversation_id": 1},
                "truth_line": "SITUATION: ..."}

    def test_reported_line_is_caught(self):
        sit = self.sit("last night")
        for eng in ("It's been a day or two, hasn't it?", "It's been a day, huh.",
                    "Days without you, honestly."):
            with self.subTest(eng=eng):
                self.assertIsNotNone(self.chat._greeting_time_lie_check(sit, eng, ""))

    def test_truthful_lines_pass(self):
        sit = self.sit("last night")
        for eng in ("Since last night, huh?", "Welcome back. You left late last night.",
                    "Hm, you're back. How was your day?"):
            with self.subTest(eng=eng):
                self.assertIsNone(self.chat._greeting_time_lie_check(sit, eng, ""))

    def test_yesterday_phrases_allow_a_day(self):
        self.assertIsNone(self.chat._greeting_time_lie_check(
            self.sit("yesterday afternoon"), "It's been a day, huh.", ""))
        self.assertIsNotNone(self.chat._greeting_time_lie_check(
            self.sit("yesterday afternoon"), "It's been a week!", ""))

    def test_every_new_phrase_has_a_known_scale(self):
        for phrase in ("this morning", "earlier this afternoon", "earlier this evening",
                       "last night", "yesterday morning", "the day before yesterday",
                       "a few days ago", "a couple of hours ago", "less than an hour ago"):
            self.assertIn(phrase, self.chat._GREETING_BUCKET_SCALE)

    def test_correction_and_truth_line_carry_no_raw_time(self):
        line = self.chat._greeting_correction_line(self.sit("last night"))
        self.assertIn("last night", line)
        self.assertNotRegex(line, r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}")


class ReturnTruthLineTests(_FreshAppTests):
    def test_return_situation_line_has_the_phrase_but_no_timestamp(self):
        import sqlite3
        conv = self.memory.load_active_conversation()
        uid = self.memory.append_message("user", "good night", conversation_id=conv)
        db = sqlite3.connect("data/memory.db")
        db.execute("UPDATE messages SET created_at = '2026-10-14 23:29' WHERE id = ?", (uid,))
        db.commit()
        db.close()
        sit = self.memory.compute_greeting_situation(conv, now=at(2026, 10, 15, 18, 9))
        self.assertEqual(sit["situation"], "return")
        self.assertIn("last night", sit["truth_line"])
        self.assertNotRegex(sit["truth_line"], r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}")


class TimingLogLineTests(_FreshAppTests):
    """D: one diagnostic line per greeting - times and decisions, no text."""

    def test_log_line_shape(self):
        sit = {"situation": "return",
               "last_contact": {"phrase": "last night", "time": at(2026, 10, 14, 23, 29)},
               "last_seen": {"time": at(2026, 10, 15, 16, 53)}}
        line = self.chat._greeting_timing_log_line("startup", 16, sit, "ok")
        self.assertEqual(
            line,
            '[Amadeus] Greeting timing: kind=startup tab=16 situation=return '
            'phrase="last night" last_seen=2026-10-15 16:53 '
            'last_talked=2026-10-14 23:29 net=ok')

    def test_log_line_without_situation_or_times(self):
        self.assertIn("situation=unavailable",
                      self.chat._greeting_timing_log_line("switch", 3, None, "no-situation"))
        line = self.chat._greeting_timing_log_line(
            "startup", 1, {"situation": "first", "last_contact": None}, "ok")
        self.assertIn("last_seen=- last_talked=-", line)

    def test_generate_greeting_prints_it(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from unittest.mock import patch

        class Fake:
            def bind_tools(self, *a, **k):
                return self

            def invoke(self, messages):
                return SimpleNamespace(content="", tool_call_chunks=None, tool_calls=[
                    {"name": "AmadeusPack", "args": {"assistant_reply_JPS": "こんにちは。",
                                                     "assistant_reply_ENG": "Hello."}}])
        self.memory.save_ceremony_state("done")
        buf = io.StringIO()
        with patch.object(self.chat, "get_llm", lambda *a, **k: Fake()), \
             patch.object(self.chat, "_finalize", lambda p, l: p), \
             patch.object(self.chat, "ja_voice", SimpleNamespace(
                 build_voice_context=lambda t: {"role": "system", "content": "v"})), \
             patch.object(self.chat, "stats", SimpleNamespace(load_stat=lambda k: 50.0)), \
             redirect_stdout(buf):
            self.chat.generate_greeting()
        log = buf.getvalue()
        self.assertIn("[Amadeus] Greeting timing: kind=startup", log)
        self.assertNotIn("Hello.", log.split("Greeting timing:")[1].splitlines()[0])


class PresenceTests(_FreshAppTests):
    """B: "last seen" visits, merged into the ONE situation line."""

    def setUp(self):
        super().setUp()
        self.memory.save_ceremony_state("done")
        self.conv = self.memory.load_active_conversation()

    def set_time(self, mid, stamp):
        import sqlite3
        db = sqlite3.connect("data/memory.db")
        db.execute("UPDATE messages SET created_at = ? WHERE id = ?", (stamp, mid))
        db.commit()
        db.close()

    def visits(self):
        import sqlite3
        db = sqlite3.connect("data/memory.db")
        try:
            return db.execute("SELECT started_at, last_seen_at FROM visits ORDER BY id").fetchall()
        finally:
            db.close()

    def talked_last_night(self):
        uid = self.memory.append_message("user", "good night!", conversation_id=self.conv)
        self.set_time(uid, "2026-10-14 23:29")
        aid = self.memory.append_message("assistant", "Night.", conversation_id=self.conv)
        self.set_time(aid, "2026-10-14 23:30")

    # --- the visits table -------------------------------------------------
    def test_pings_merge_into_one_visit_and_split_after_a_break(self):
        self.memory.record_presence(now=at(2026, 10, 15, 16, 53))
        self.memory.record_presence(now=at(2026, 10, 15, 16, 58))
        self.memory.record_presence(now=at(2026, 10, 15, 17, 6))
        self.assertEqual(self.visits(), [("2026-10-15 16:53:00", "2026-10-15 17:06:00")])
        self.memory.record_presence(now=at(2026, 10, 15, 18, 9))
        self.assertEqual(len(self.visits()), 2)

    def test_old_visits_are_trimmed(self):
        for i in range(self.memory.VISITS_KEEP + 5):
            self.memory.record_presence(now=at(2026, 1, 1, 0, 0).replace(day=1) +
                                        __import__("datetime").timedelta(hours=i))
        self.assertEqual(len(self.visits()), self.memory.VISITS_KEEP)

    def test_last_seen_and_the_regenerate_cap(self):
        self.assertIsNone(self.memory.load_last_seen())
        self.memory.record_presence(now=at(2026, 10, 15, 16, 53))
        self.memory.record_presence(now=at(2026, 10, 15, 17, 0))
        self.memory.record_presence(now=at(2026, 10, 15, 18, 9))   # a new visit
        self.memory.record_presence(now=at(2026, 10, 15, 18, 14))
        self.assertEqual(self.memory.load_last_seen(), at(2026, 10, 15, 18, 14))
        # Regenerating the 18:09 greeting: only what was known at 18:09.
        self.assertEqual(self.memory.load_last_seen(before=at(2026, 10, 15, 18, 9)),
                         at(2026, 10, 15, 17, 0))

    # --- the situation ----------------------------------------------------
    def test_opened_the_app_without_writing_is_not_an_absence(self):
        # The reported day: talked last night, opened the app at 16:53 and
        # stayed a while without writing, back at 18:09.
        self.talked_last_night()
        self.memory.record_presence(now=at(2026, 10, 15, 16, 53))
        self.memory.record_presence(now=at(2026, 10, 15, 17, 0))
        sit = self.memory.compute_greeting_situation(self.conv, now=at(2026, 10, 15, 18, 9))
        self.assertEqual(sit["situation"], "continuous")
        self.assertIn("just not writing", sit["truth_line"])
        self.assertIn("This is not a welcome-back moment", sit["truth_line"])
        self.assertEqual(sit["truth_line"].count(" ago"), 1)   # ONE phrase only
        self.assertIsNotNone(self.chat._greeting_time_lie_check(
            sit, "It's been a day or two, hasn't it?", ""))

    def test_visit_after_the_last_message_sets_the_return_phrase(self):
        uid = self.memory.append_message("user", "see you", conversation_id=self.conv)
        self.set_time(uid, "2026-10-11 20:00")
        self.memory.record_presence(now=at(2026, 10, 14, 22, 0))
        sit = self.memory.compute_greeting_situation(self.conv, now=at(2026, 10, 15, 18, 0))
        self.assertEqual(sit["situation"], "return")
        self.assertIn("was last here in the app last night", sit["truth_line"])
        self.assertIn("did not write anything", " ".join(sit["details"]))
        self.assertEqual(sit["last_contact"]["phrase"], "last night")
        self.assertIsNotNone(self.chat._greeting_time_lie_check(sit, "It's been days!", ""))

    def test_without_visits_it_measures_from_messages_as_before(self):
        self.talked_last_night()
        sit = self.memory.compute_greeting_situation(self.conv, now=at(2026, 10, 15, 18, 9))
        self.assertEqual(sit["situation"], "return")
        self.assertIn("last night", sit["truth_line"])
        self.assertIsNone(sit["last_seen"])

    def test_a_ping_right_after_the_last_message_changes_nothing(self):
        self.talked_last_night()
        self.memory.record_presence(now=at(2026, 10, 14, 23, 35))
        sit = self.memory.compute_greeting_situation(self.conv, now=at(2026, 10, 15, 18, 9))
        self.assertEqual(sit["situation"], "return")
        self.assertNotIn("did not write", " ".join(sit["details"]))

    def test_window_back_after_a_night_is_still_a_return(self):
        # Minimised overnight; the window comes back into view at 09:00
        # (its first ping), and a tab switch follows a moment later: the
        # arrival's own ping must not hide the night.
        self.talked_last_night()
        self.memory.record_presence(now=at(2026, 10, 14, 23, 40))
        self.memory.record_presence(now=at(2026, 10, 15, 9, 0))
        sit = self.memory.compute_greeting_situation(
            self.conv, now=at(2026, 10, 15, 9, 1), switch=True)
        self.assertEqual(sit["situation"], "return")
        self.assertIn("last night", sit["truth_line"])
        # ...but after a few minutes back, they are simply here.
        self.memory.record_presence(now=at(2026, 10, 15, 9, 5))
        sit = self.memory.compute_greeting_situation(
            self.conv, now=at(2026, 10, 15, 9, 6), switch=True)
        self.assertEqual(sit["situation"], "continuous")

    # --- the routes -------------------------------------------------------
    def test_greet_route_records_the_visit_after_measuring(self):
        from unittest.mock import patch
        seen_during = []
        pack = self.chat.AmadeusPack(assistant_reply_ENG="Hi.", assistant_reply_JPS="やあ。")

        def fake_generate(**kw):
            seen_during.append(self.memory.load_last_seen())
            return pack, 7, self.conv
        self.chat.setKey("sk-test")
        with patch.object(self.api, "model_ready", return_value={"ready": True, "reason": ""}), \
             patch.object(self.api, "generate_greeting", side_effect=fake_generate):
            res = self.client.post("/greet", json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(seen_during, [None])        # it did not see its own visit
        self.assertEqual(len(self.visits()), 1)      # ...which is recorded after

    def test_presence_route(self):
        res = self.client.post("/presence", json={"conversation_id": self.conv})
        self.assertEqual(res.json, {"ok": True})
        self.assertEqual(len(self.visits()), 1)

    def test_presence_route_is_quiet_during_first_launch(self):
        self.memory.save_ceremony_state("intro")
        res = self.client.post("/presence", json={})
        self.assertEqual(res.json["recorded"], False)


if __name__ == "__main__":
    unittest.main()

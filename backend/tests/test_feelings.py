"""Her short-term feelings (feelings.py, 2026-10-30)."""
import os
import importlib
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import feelings

NOW = datetime(2026, 10, 30, 15, 0)


class FeelingsBase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        # Other suites re-import memory.py; feelings reads whichever one is
        # current, so patch that one.
        self.store = importlib.import_module("memory")
        self._p = patch.object(self.store, "PATH_TO_MEMORY",
                               os.path.join(self._dir.name, "memory.db"))
        self._p.start()
        self.path = os.path.join(self._dir.name, "feelings.txt")

    def tearDown(self):
        self._p.stop()
        self._dir.cleanup()

    def write(self, text, at=NOW):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(self.path, (at.timestamp(), at.timestamp()))


class NoFileTests(FeelingsBase):
    def test_no_file_means_all_normal_and_no_line(self):
        f = feelings.current(NOW)
        self.assertEqual(f["irritation"], 0)
        self.assertEqual(f["missing"], 0)
        self.assertEqual(feelings.feelings_line(NOW), "")
        self.assertFalse(os.path.exists(self.path))

    def test_ensure_file_starts_clock_from_newest_user_message(self):
        with patch.object(self.store, "newest_user_message_time",
                          return_value=NOW - timedelta(days=3)):
            feelings.ensure_file(NOW)
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("last_contact = 2026-10-27 15:00", f.read())


class FadingTests(FeelingsBase):
    def test_half_life_fade(self):
        self.write("irritation = 60\nworry = 40\n", at=NOW)
        f = feelings.current(NOW + timedelta(hours=2))
        self.assertAlmostEqual(f["irritation"], 30, places=3)
        f = feelings.current(NOW + timedelta(hours=12))
        self.assertAlmostEqual(f["worry"], 20, places=3)

    def test_mood_fades_toward_zero_from_below(self):
        self.write("mood = -40\n", at=NOW)
        self.assertAlmostEqual(feelings.current(NOW + timedelta(hours=4))["mood"], -10)

    def test_broken_line_is_skipped(self):
        self.write("irritation = lots\nworry = 30\n", at=NOW)
        f = feelings.current(NOW)
        self.assertEqual(f["irritation"], 0)
        self.assertEqual(f["worry"], 30)


class AbsenceTests(FeelingsBase):
    def contact(self, ago):
        self.write(f"last_contact = {(NOW - ago):%Y-%m-%d %H:%M}\n", at=NOW - ago)

    def test_time_alone_never_irritates_below_devoted(self):
        self.contact(timedelta(days=20))
        f = feelings.current(NOW, trust=90)
        self.assertEqual(f["irritation"], 0)
        self.assertEqual(f["missing"], 100)

    def test_missing_starts_after_six_hours_overnight_too(self):
        self.contact(timedelta(hours=5))
        self.assertEqual(feelings.current(NOW, trust=90)["missing"], 0)
        self.contact(timedelta(hours=39))  # across a night: no pause
        self.assertAlmostEqual(feelings.current(NOW, trust=90)["missing"], 50)

    def test_absence_worry_after_two_days(self):
        self.contact(timedelta(hours=47))
        self.assertEqual(feelings.current(NOW, trust=90)["worry"], 0)
        self.contact(timedelta(days=4))
        self.assertGreater(feelings.current(NOW, trust=90)["worry"], 0)

    def test_absence_stirs_nothing_below_close(self):
        self.contact(timedelta(days=20))
        f = feelings.current(NOW, trust=79)
        self.assertEqual((f["missing"], f["worry"], f["irritation"]), (0, 0, 0))
        self.assertEqual(feelings.feelings_line(NOW, trust=79), "")

    def test_close_is_curious_not_missing(self):
        self.contact(timedelta(hours=20))
        self.assertEqual(feelings.feelings_line(NOW, trust=82), "")
        self.contact(timedelta(days=3))
        line = feelings.feelings_line(NOW, trust=82)
        self.assertIn("quite curious what they have been up to", line)
        self.assertNotIn("missed", line)
        self.assertNotIn("worried", line)
        self.assertEqual(feelings.describe_for_user(NOW, trust=82),
                         ["Quite curious what you have been up to"])

    def test_devoted_stages(self):
        self.contact(timedelta(days=3))
        f = feelings.current(NOW, trust=96)
        self.assertLess(f["missing"], 50)  # builds over a whole week
        self.assertEqual(f["irritation"], 0)
        self.contact(timedelta(days=7, hours=2))
        f = feelings.current(NOW, trust=96)
        self.assertEqual(f["stage"], "week")
        self.assertGreaterEqual(f["irritation"], 15)
        self.contact(timedelta(days=10))
        line = feelings.feelings_line(NOW, trust=96)
        self.assertIn("grumpy", line)
        self.contact(timedelta(days=15))
        line = feelings.feelings_line(NOW, trust=100)
        self.assertIn("close to panic", line)
        self.assertIn("want to know what happened", line)
        self.assertIn("missed them very much", line)


class EventTests(FeelingsBase):
    def test_ignored_greetings_escalate_and_count_once(self):
        self.write(f"last_contact = {NOW:%Y-%m-%d %H:%M}\n", at=NOW)
        feelings.note_ignored_greeting(7, 1, trust=82, now=NOW)
        first = feelings.current(NOW)["irritation"]
        feelings.note_ignored_greeting(7, 1, trust=82, now=NOW)  # same again
        self.assertAlmostEqual(feelings.current(NOW)["irritation"], first, places=0)
        feelings.note_ignored_greeting(7, 2, trust=82, now=NOW)
        second = feelings.current(NOW)
        self.assertGreater(second["irritation"] - first, first - 0)
        self.assertGreater(second["irritation"], second["worry"])
        self.assertGreater(second["worry"], 0)

    def test_ignored_greeting_annoys_at_every_level_no_worry_below_close(self):
        self.write(f"last_contact = {NOW:%Y-%m-%d %H:%M}\n", at=NOW)
        feelings.note_ignored_greeting(7, 1, trust=10, now=NOW)
        f = feelings.current(NOW)
        self.assertGreaterEqual(f["irritation"], feelings.SHOW_FROM)
        self.assertEqual(f["worry"], 0)
        self.assertIn("your last greeting got no reply", feelings.feelings_line(NOW))

    def test_familiar_softens_the_annoyance(self):
        self.write(f"last_contact = {NOW:%Y-%m-%d %H:%M}\n", at=NOW)
        feelings.note_ignored_greeting(7, 1, trust=50, now=NOW)
        early = feelings.current(NOW)["irritation"]
        os.remove(self.path)
        self.write(f"last_contact = {NOW:%Y-%m-%d %H:%M}\n", at=NOW)
        feelings.note_ignored_greeting(7, 1, trust=65, now=NOW)
        f = feelings.current(NOW)
        self.assertLess(f["irritation"], early)
        self.assertEqual(f["worry"], 0)

    def test_devoted_ignored_greeting_leans_to_worry(self):
        self.write(f"last_contact = {NOW:%Y-%m-%d %H:%M}\n", at=NOW)
        feelings.note_ignored_greeting(7, 1, trust=96, now=NOW)
        f = feelings.current(NOW)
        self.assertGreater(f["worry"], f["irritation"])

    def test_reply_halves_irritation_resets_missing(self):
        self.write("irritation = 40\nlast_contact = 2026-10-29 01:00\n", at=NOW)
        feelings.note_user_replied(NOW, trust=82)
        f = feelings.current(NOW)
        self.assertAlmostEqual(f["irritation"], 20, places=0)
        self.assertEqual(f["missing"], 0)

    def test_late_night_worry_once_per_night(self):
        late = datetime(2026, 10, 30, 23, 40)
        self.write(f"last_contact = {late:%Y-%m-%d %H:%M}\n", at=late)
        feelings.note_user_replied(late, trust=82)
        w1 = feelings.current(late)["worry"]
        self.assertGreaterEqual(w1, feelings.LATE_NIGHT_WORRY - 1)
        after = datetime(2026, 10, 31, 0, 20)  # same night
        feelings.note_user_replied(after, trust=82)
        self.assertLess(feelings.current(after)["worry"], w1 + 1)
        self.assertIn("up this late", feelings.feelings_line(after))

    def test_not_late_at_23_29(self):
        t = datetime(2026, 10, 30, 23, 29)
        self.write(f"last_contact = {t:%Y-%m-%d %H:%M}\n", at=t)
        feelings.note_user_replied(t, trust=82)
        self.assertEqual(round(feelings.current(t)["worry"]), 0)

    def test_late_night_does_not_worry_her_below_close(self):
        late = datetime(2026, 10, 30, 23, 40)
        self.write(f"last_contact = {late:%Y-%m-%d %H:%M}\n", at=late)
        feelings.note_user_replied(late, trust=79)
        self.assertEqual(round(feelings.current(late)["worry"]), 0)

    def test_devoted_week_absence_kept_after_reply(self):
        t = NOW - timedelta(days=8)
        self.write(f"last_contact = {t:%Y-%m-%d %H:%M}\n", at=t)
        feelings.note_user_replied(NOW, trust=97)
        f = feelings.current(NOW, trust=97)
        self.assertEqual(f["missing"], 0)
        self.assertGreater(f["worry"], 15)
        self.assertIn("more than a week", f["worry_reason"])

    def test_check_changes_are_capped(self):
        feelings.apply_changes({"mood": 50, "playfulness": 3, "nonsense": 9}, NOW)
        f = feelings.current(NOW)
        self.assertEqual(f["mood"], feelings.CHECK_STEP_MAX)
        self.assertEqual(f["playfulness"], 3)

    def test_no_playfulness_below_familiar(self):
        feelings.apply_changes({"playfulness": 8, "irritation": 6}, NOW, trust=40)
        f = feelings.current(NOW)
        self.assertEqual((round(f["playfulness"]), round(f["irritation"])), (0, 6))
        feelings.apply_changes({"playfulness": 8}, NOW, trust=60)
        self.assertEqual(round(feelings.current(NOW)["playfulness"]), 8)


class LineTests(FeelingsBase):
    def test_line_is_permission_without_numbers(self):
        self.write("irritation = 50\nirritation_reason = your last greeting got no reply\n")
        line = feelings.feelings_line(NOW)
        self.assertTrue(line.startswith("HOW YOU FEEL RIGHT NOW: quite irritated"))
        self.assertIn("keep them to yourself", line)
        self.assertNotRegex(line, r"\d")

    def test_small_feelings_are_not_mentioned(self):
        self.write("irritation = 10\nmood = -12\n")
        self.assertEqual(feelings.feelings_line(NOW), "")

    def test_explicit_path_wins(self):
        other = os.path.join(self._dir.name, "other.txt")
        with open(other, "w", encoding="utf-8") as f:
            f.write("mood = -50\n")
        self.assertIn("quite down", feelings.feelings_line(datetime.now(), path=other))
        self.assertEqual(feelings.feelings_line(datetime.now()), "")


class PromptTests(unittest.TestCase):
    """The line sits right after the closeness block in her prompt's
    changing part, and only when a feeling is away from normal."""

    def prompt(self, directory, right_now=False):
        import chat
        patches = [
            patch.object(chat.store, "PATH_TO_MEMORY",
                         os.path.join(directory, "memory.db")),
            patch.object(chat.store, "load_default_personality_messages", lambda: []),
            patch.object(chat.stats, "load_stat", lambda key: 82),
            patch.object(chat.ja_voice, "build_voice_rules",
                         lambda: {"role": "system", "content": "VOICE"}),
            patch.object(chat.ja_voice, "build_closeness_context",
                         lambda trust: {"role": "system", "content": "CLOSENESS"}),
        ]
        for p in patches:
            p.start()
        try:
            msgs = chat._ordered_prompt(
                {"role": "system", "content": "RULES"},
                {"role": "system", "content": "WEB"},
                [{"role": "system", "content": "TIMING"}],
                [{"role": "user", "content": "hi"}], right_now=right_now)
        finally:
            for p in patches:
                p.stop()
        return " | ".join(m["content"] for m in msgs)

    def test_line_after_closeness_only_when_needed(self):
        with tempfile.TemporaryDirectory() as d:
            calm = self.prompt(d)
            self.assertNotIn("HOW YOU FEEL", calm)
            with open(os.path.join(d, "feelings.txt"), "w", encoding="utf-8") as f:
                f.write("worry = 50\nworry_reason = they are still up this late\n")
            text = self.prompt(d)
            self.assertLess(text.index("CLOSENESS"), text.index("HOW YOU FEEL"))
            self.assertLess(text.index("HOW YOU FEEL"), text.index("TIMING"))
            self.assertIn("quite worried about them (they are still up this late)", text)
            tail = self.prompt(d, right_now=True)
            self.assertGreater(tail.index("HOW YOU FEEL"), tail.index("hi"))


if __name__ == "__main__":
    unittest.main()

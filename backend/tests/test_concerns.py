"""Things on her mind about the user (concerns.py, 2026-10-30)."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import concerns

TODAY = date(2026, 10, 30)  # a Friday


class Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "concerns.txt")

    def tearDown(self):
        self._dir.cleanup()

    def add(self, text, when=None, today=TODAY):
        return concerns.add(text, when, today=today, path=self.path)


class ListTests(Base):
    def test_add_load_roundtrip_and_file_is_plain_text(self):
        self.assertTrue(self.add("exam on Monday"))
        items = concerns.load(self.path)
        self.assertEqual(items[0]["text"], "exam on Monday")
        self.assertEqual(items[0]["due"], date(2026, 11, 2))
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("[open]\n- exam on Monday | added 2026-10-30 | due 2026-11-02", text)

    def test_near_duplicates_are_skipped(self):
        self.add("skipped lunch again")
        self.assertFalse(self.add("Skipped lunch again."))
        self.assertFalse(self.add("none"))
        self.assertEqual(len(concerns.load(self.path)), 1)

    def test_kept_until_settled_no_expiry(self):
        self.add("job interview", today=date(2026, 1, 1))
        line = concerns.prompt_line(80, today=TODAY, path=self.path)
        self.assertIn("job interview", line)

    def test_two_asks_make_it_quiet_and_a_mention_wakes_it(self):
        self.add("sore throat")
        concerns.mark([1], "asked", path=self.path)
        self.assertEqual(concerns.load(self.path)[0]["status"], "open")
        concerns.mark([1], "asked", path=self.path)
        self.assertEqual(concerns.load(self.path)[0]["status"], "quiet")
        self.assertEqual(concerns.prompt_line(80, today=TODAY, path=self.path), "")
        self.assertTrue(self.add("sore throat"))
        item = concerns.load(self.path)[0]
        self.assertEqual((item["status"], item["asked"]), ("open", 0))

    def test_done_is_kept_with_its_date(self):
        self.add("exam")
        self.add("cold")
        concerns.mark([1], "done", today=TODAY, path=self.path)
        items = concerns.load(self.path)
        done = [i for i in items if i["status"] == "done"]
        self.assertEqual(done[0]["text"], "exam")
        self.assertEqual(done[0]["done"], TODAY)
        self.assertEqual(concerns.check_list(self.path), "1. cold")

    def test_store_cap_retires_the_oldest(self):
        words = ["exam", "cold", "rent", "knee", "dentist", "visa", "move",
                 "laptop", "sister", "deadline", "insomnia", "car"]
        for w in words:
            self.add(w)
        self.assertEqual(len(concerns.active(concerns.load(self.path))), concerns.STORE_MAX)

    def test_remove_by_index(self):
        self.add("a cold")
        self.add("an exam")
        self.assertTrue(concerns.remove(0, path=self.path))
        self.assertEqual([i["text"] for i in concerns.load(self.path)], ["an exam"])

    def test_hand_edited_file_is_read(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("dentist tomorrow\n[quiet]\n- old thing | asked 2\nbad | due nope\n")
        items = concerns.load(self.path)
        self.assertEqual([(i["text"], i["status"]) for i in items],
                         [("dentist tomorrow", "open"), ("old thing", "quiet"),
                          ("bad", "quiet")])


class DayTests(unittest.TestCase):
    def test_check_date_used_when_sane(self):
        self.assertEqual(concerns.resolve_day("exam", "2026-11-05", TODAY), date(2026, 11, 5))
        self.assertIsNone(concerns.resolve_day("exam", "1999-01-01", TODAY))

    def test_words_in_the_text(self):
        self.assertEqual(concerns.resolve_day("dentist tomorrow", None, TODAY), date(2026, 10, 31))
        self.assertEqual(concerns.resolve_day("exam on friday", None, TODAY), TODAY)
        self.assertEqual(concerns.resolve_day("exam next friday", None, TODAY), date(2026, 11, 6))
        self.assertIsNone(concerns.resolve_day("feels ill", "none", TODAY))


class PromptTests(Base):
    def test_background_only_below_close(self):
        self.add("exam")
        line = concerns.prompt_line(10, today=TODAY, path=self.path)
        self.assertIn("exam", line)
        self.assertIn("without raising them yourself", line)
        self.assertNotIn("Ask about one", line)
        self.assertIn("Ask about one", concerns.prompt_line(80, today=TODAY, path=self.path))

    def test_passed_date_invites_how_it_went(self):
        self.add("exam", "2026-10-28", today=date(2026, 10, 27))
        line = concerns.prompt_line(80, today=TODAY, path=self.path)
        self.assertIn("that has passed, so you could ask how it went", line)
        self.assertIn("one at a time", line)

    def test_devoted_makes_a_point_of_asking(self):
        self.add("skipped meals")
        line = concerns.prompt_line(96, today=TODAY, path=self.path)
        self.assertIn("make a point of asking", line)

    def test_at_most_three_shown(self):
        for t in ("exam aaa", "cold bbb", "interview ccc", "move ddd"):
            self.add(t)
        line = concerns.prompt_line(80, today=TODAY, path=self.path)
        self.assertEqual(sum(t in line for t in ("aaa", "bbb", "ccc", "ddd")), 3)

    def test_greeting_options_never_lock_a_topic(self):
        self.add("exam aaa")
        self.add("cold bbb")
        with open(self.path, encoding="utf-8") as f:
            before = f.read()
        first = concerns.greeting_options(today=TODAY, path=self.path)
        self.assertIn("aaa", first)
        self.assertIn("bbb", first)
        # Again (a regenerated greeting): the same topics, file unchanged.
        self.assertEqual(concerns.greeting_options(today=TODAY, path=self.path), first)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_greeting_options_skip_quiet_and_done(self):
        self.add("exam")
        concerns.mark([1], "done", today=TODAY, path=self.path)
        self.assertEqual(concerns.greeting_options(today=TODAY, path=self.path), "")


class CheckTests(Base):
    """The trust check's CONCERN / WHEN / DONE / ASKED lines."""

    def apply(self, reply):
        import chat
        with patch.object(chat, "_concerns_path", lambda: self.path):
            chat._apply_check_concerns(reply)

    def test_new_concern_with_date(self):
        # The check's date must survive the day it was written: ten days
        # out, computed at run time (a hard-coded "future" date rots).
        future = date.today() + timedelta(days=10)
        self.apply("TRUST: 1\nCONCERN: chemistry exam\nWHEN: " + future.isoformat() +
                   "\nDONE: none\nASKED: none")
        item = concerns.load(self.path)[0]
        self.assertEqual((item["text"], item["due"]), ("chemistry exam", future))

    def test_done_and_asked_use_the_numbered_list(self):
        self.add("exam")
        self.add("cold")
        self.apply("CONCERN: none\nDONE: 2\nASKED: 1")
        items = {i["text"]: i for i in concerns.load(self.path)}
        self.assertEqual(items["cold"]["status"], "done")
        self.assertEqual(items["exam"]["asked"], 1)

    def test_garbled_reply_changes_nothing(self):
        self.add("exam")
        self.apply("+2 warmer")
        self.assertEqual(concerns.check_list(self.path), "1. exam")


class SeedTests(Base):
    def run_seed(self, reply, material="Conversation \"x\": USER: exam on Monday"):
        import chat
        llm = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=reply))
        with patch.object(chat, "_concerns_path", lambda: self.path), \
                patch.object(chat.store, "recent_user_material", lambda: material), \
                patch.object(chat, "get_llm", lambda *a, **k: llm):
            chat.seed_concerns_once()

    def test_seeds_at_most_three_once(self):
        self.run_seed("CONCERN: exam | WHEN: none\nCONCERN: cold\nCONCERN: rent due\n"
                      "CONCERN: knee pain")
        self.assertEqual(len(concerns.load(self.path)), 3)
        self.run_seed("CONCERN: something else")   # file exists: never again
        self.assertEqual(len(concerns.load(self.path)), 3)

    def test_nothing_found_still_marks_it_done(self):
        self.run_seed("CONCERN: none")
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(concerns.load(self.path), [])

    def test_model_failure_retries_next_start(self):
        import chat
        def boom(*a, **k):
            raise RuntimeError("server down")
        with patch.object(chat, "_concerns_path", lambda: self.path), \
                patch.object(chat.store, "recent_user_material", lambda: "stuff"), \
                patch.object(chat, "get_llm", boom):
            chat.seed_concerns_once()
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()

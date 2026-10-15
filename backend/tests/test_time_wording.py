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


if __name__ == "__main__":
    unittest.main()

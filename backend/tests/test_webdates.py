"""Dates in web text relative to today (2026-10-25): ranges, "covers today",
labels ("upcoming, in 4 days"), and future dates said in the past tense."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webdates as wd

TODAY = date(2026, 10, 25)


class FindDatesTests(unittest.TestCase):
    def dates(self, text):
        return [d for _, _, d in wd.find_dates(text, TODAY)]

    def test_formats(self):
        self.assertEqual(self.dates("October 29, 2026"), [date(2026, 10, 29)])
        self.assertEqual(self.dates("Oct 29th"), [date(2026, 10, 29)])
        self.assertEqual(self.dates("29 October 2026"), [date(2026, 10, 29)])
        self.assertEqual(self.dates("2026-10-29 and 2026/11/03"), [date(2026, 10, 29), date(2026, 11, 3)])
        self.assertEqual(self.dates("2026年10月29日、11月3日"), [date(2026, 10, 29), date(2026, 11, 3)])

    def test_a_missing_year_is_the_closest_one(self):
        self.assertEqual(self.dates("Jan 5"), [date(2027, 1, 5)])      # 72 days ahead, not 293 back
        self.assertEqual(self.dates("Aug 20"), [date(2026, 8, 20)])

    def test_no_false_dates(self):
        for text in ("version 7.2", "Feb 31", "13月40日", "May I help you", "10 people"):
            self.assertEqual(self.dates(text), [], text)


class RangeTests(unittest.TestCase):
    def test_ranges_and_today(self):
        for text, covers in [
                ("Phase 2 runs Oct 13 - Nov 3 with Skirk", True),
                ("10月13日～11月3日 スカーク", True),
                ("October 13 to November 3, 2026", True),
                ("Skirk until November 3", True),
                ("11月3日まで開催", True),
                ("Phase 1: November 4, 2026 – November 24, 2026", False),
                ("The banner ran October 1st to October 12th", False),
                ("7.2 starts November 4", False),
                ("until March 3", False)]:                       # too far ahead to be "now"
            self.assertEqual(wd.covers_today(text, TODAY), covers, text)

    def test_a_range_over_new_year(self):
        self.assertEqual(wd.date_ranges("Dec 20 - Jan 5", TODAY), [(date(2026, 12, 20), date(2027, 1, 5))])


class LabelTests(unittest.TestCase):
    def test_labels(self):
        out = wd.label_dates("Consoles launch October 29, 2026; Steam Aug 20, 2026; 10月25日.", TODAY)
        self.assertIn("October 29, 2026 (upcoming, in 4 days)", out)
        self.assertIn("Aug 20, 2026 (about 2 months ago)", out)
        self.assertIn("10月25日 (today)", out)

    def test_no_double_labels(self):
        once = wd.label_dates("October 29", TODAY)
        self.assertEqual(wd.label_dates(once, TODAY), once)

    def test_relative_words(self):
        self.assertEqual(wd.relative_label(date(2026, 10, 26), TODAY), "upcoming, tomorrow")
        self.assertEqual(wd.relative_label(date(2026, 10, 24), TODAY), "yesterday")
        self.assertEqual(wd.relative_label(date(2026, 10, 12), TODAY), "13 days ago")
        self.assertEqual(wd.relative_label(date(2024, 10, 25), TODAY), "about 2 years ago")


class PastTenseTests(unittest.TestCase):
    def test_future_dates_in_past_tense_are_found(self):
        found = wd.past_tense_future_dates(
            "The Steam version came out on August 20, 2026. The console versions were "
            "released on October 29, 2026. Also, it has officially launched on Oct 30.",
            "コンソール版は10月29日に発売されました。", TODAY)
        self.assertEqual([d for d, _ in found], [date(2026, 10, 29), date(2026, 10, 30), date(2026, 10, 29)])

    def test_correct_tenses_pass(self):
        for en, ja in [
                ("It will be released on October 29.", ""),
                ("The consoles are scheduled for October 29, 2026.", ""),
                ("The Steam version was released on August 20, 2026.", ""),
                ("Phase 1 starts on November 4.", ""),
                ("", "10月29日に発売予定です。"),
                ("", "8月20日に発売されました。")]:
            self.assertEqual(wd.past_tense_future_dates(en, ja, TODAY), [], en or ja)


class LastYearTests(unittest.TestCase):
    """"Last year" (去年 / 昨年 / "last August") on a month her English line
    dates to THIS year - 2026-10-26: 「PC版は去年の8月に発売」 for August 2026."""

    def test_found(self):
        found = wd.last_year_on_this_year(
            "The PC version was released back in August 2026.",
            "PC版は去年の8月に発売されていて、10月29日に発売される予定なの。", TODAY)
        self.assertEqual([m for m, _ in found], [8])
        found = wd.last_year_on_this_year("It came out last August. Steam: August 20, 2026.", "", TODAY)
        self.assertEqual([m for m, _ in found], [8])

    def test_right_ones_pass(self):
        for en, ja in [
                ("It was released in August 2025.", "去年の8月に発売されたの。"),
                ("The PC version was released in August 2026.", "PC版は今年の8月に発売されたの。"),
                ("The PC version was released in August 2026.", "PC版は8月に発売されたの。"),
                ("", "去年の8月に発売されたの。")]:                  # no year to compare: left alone
            self.assertEqual(wd.last_year_on_this_year(en, ja, TODAY), [], (en, ja))


if __name__ == "__main__":
    unittest.main()

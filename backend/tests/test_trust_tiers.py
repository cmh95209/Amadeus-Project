# -*- coding: utf-8 -*-
"""Trust tier revamp (2026-10-26).

  1. Close is split into Close 80-84 / Trusted 85-94 / Devoted 95-98 /
     Special 99+, and trust may grow past 100 up to stats.TRUST_MAX;
  2. trust lives in data/trust.txt: created once from the old stats.db value,
     a hand edit is picked up on the next read, a non-number keeps the last
     good value and leaves the file alone for the user to fix;
  3. the new tiers address the user like Close (bare name, teasing form ok),
     and her closeness line says when trust is past 100.
All file work happens in a temporary folder; no live data is touched.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stats
import ja_voice


class _TempStats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self._patches = [
            mock.patch.object(stats, "DATA_DIR", self.dir),
            mock.patch.object(stats, "PATH_TO_STATS", os.path.join(self.dir, "stats.db")),
            mock.patch.object(stats, "_last_good", {}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    @property
    def trust_file(self):
        return os.path.join(self.dir, "trust.txt")

    def write_trust_file(self, text):
        with open(self.trust_file, "w", encoding="utf-8") as f:
            f.write(text)

    def read_trust_file(self):
        with open(self.trust_file, "r", encoding="utf-8") as f:
            return f.read().strip()


class TierLabelTests(unittest.TestCase):
    def test_boundaries(self):
        cases = [(0, "Distant"), (19, "Distant"), (20, "Cautious"), (59, "Warming up"),
                 (79, "Familiar"), (80, "Close"), (84, "Close"), (85, "Trusted"),
                 (94, "Trusted"), (95, "Devoted"), (98, "Devoted"), (99, "Special"),
                 (100, "Special"), (stats.TRUST_MAX, "Special")]
        for value, name in cases:
            with self.subTest(value=value):
                self.assertEqual(stats.tier_label("trust", value), name)

    def test_fractions_between_ranges_still_land(self):
        self.assertEqual(stats.tier_label("trust", 84.5), "Close")
        self.assertEqual(stats.tier_label("trust", 98.9), "Devoted")


class TrustFileTests(_TempStats):
    def test_missing_file_created_from_old_db_value(self):
        conn = sqlite3.connect(stats.PATH_TO_STATS)
        conn.execute("CREATE TABLE stats (key TEXT PRIMARY KEY, value REAL NOT NULL, updated_at TEXT)")
        conn.execute("INSERT INTO stats VALUES ('trust', 63, 'x')")
        conn.commit()
        conn.close()
        self.assertEqual(stats.load_stat("trust"), 63.0)
        self.assertEqual(self.read_trust_file(), "63")
        # stats.db keeps its old row untouched
        conn = sqlite3.connect(stats.PATH_TO_STATS)
        self.assertEqual(conn.execute("SELECT value FROM stats WHERE key='trust'").fetchone()[0], 63)
        conn.close()

    def test_fresh_install_starts_at_default(self):
        self.assertEqual(stats.load_stat("trust"), 10.0)
        self.assertTrue(os.path.exists(self.trust_file))

    def test_hand_edit_is_picked_up(self):
        stats.load_stat("trust")
        self.write_trust_file("96\n")
        self.assertEqual(stats.load_stat("trust"), 96.0)
        self.assertEqual(ja_voice.stats.tier_label("trust", stats.load_stat("trust")), "Devoted")

    def test_not_a_number_keeps_last_good_and_leaves_file(self):
        self.write_trust_file("88")
        self.assertEqual(stats.load_stat("trust"), 88.0)
        self.write_trust_file("ninety")
        self.assertEqual(stats.load_stat("trust"), 88.0)
        self.assertEqual(self.read_trust_file(), "ninety")

    def test_out_of_range_edit_is_clamped(self):
        self.write_trust_file("500")
        self.assertEqual(stats.load_stat("trust"), float(stats.TRUST_MAX))
        self.write_trust_file("-4")
        self.assertEqual(stats.load_stat("trust"), 0.0)

    def test_can_grow_past_100_up_to_max(self):
        self.write_trust_file("99")
        self.assertEqual(stats.adjust_stat("trust", 3), 102.0)
        self.assertEqual(self.read_trust_file(), "102")
        stats.set_stat("trust", 999)
        self.assertEqual(stats.load_stat("trust"), float(stats.TRUST_MAX))

    def test_settings_show_it_out_of_100(self):
        self.write_trust_file("104")
        item = [s for s in stats.all_stats() if s["key"] == "trust"][0]
        self.assertEqual(item["scale"], 100)
        self.assertEqual(item["max"], stats.TRUST_MAX)
        self.assertEqual(item["tier"], "Special")

    def test_other_stats_stay_in_the_database(self):
        stats.set_stat("_user_msg_counter", 3)
        self.assertEqual(stats.load_stat("_user_msg_counter"), 3.0)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "_user_msg_counter.txt")))


class VoiceForNewTiersTests(unittest.TestCase):
    def _rule(self, tier, pronouns="he/him"):
        import memory
        with mock.patch.object(memory, "load_username", lambda: "Alex"), \
             mock.patch.object(memory, "load_username_reading", lambda: "アレックス"), \
             mock.patch.object(memory, "load_address", lambda: "auto"), \
             mock.patch.object(memory, "load_pronouns", lambda: pronouns):
            return ja_voice.address_rule(tier)

    def test_new_tiers_use_bare_name_and_teasing(self):
        for tier in ("Trusted", "Devoted", "Special"):
            with self.subTest(tier=tier):
                rule = self._rule(tier)
                self.assertIn("call them: アレックス.", rule)
                self.assertIn("Only when teasing them, アレックスくん", rule)
                self.assertNotIn("さん", rule)

    def test_closeness_line_past_100(self):
        with mock.patch.object(ja_voice, "address_rule", lambda tier: ""):
            high = ja_voice.build_closeness_context(104)["content"]
            normal = ja_voice.build_closeness_context(90)["content"]
        self.assertIn("Special trust 104 out of 100 (past the top of the scale).", high)
        self.assertIn("Trusted trust 90 out of 100.", normal)


def _old_style_voice():
    """The default as a pre-revamp file looked: no Trusted/Devoted/Special,
    Close still 80-100, plus a line the user wrote themselves."""
    common, sections = ja_voice.split_tier_sections(ja_voice.DEFAULT_VOICE_BLOCK.strip())
    lines = ja_voice.DEFAULT_VOICE_BLOCK.strip().split("\n")
    out, skip = [], False
    for ln in lines:
        m = ja_voice._TIER_HEAD.match(ln)
        if m:
            skip = m.group(1) in ("Trusted", "Devoted", "Special")
        elif ln.startswith("---"):
            skip = False
        if not skip:
            out.append(ln)
    text = "\n".join(out).replace(ja_voice._NEW_CLOSE_HEAD, ja_voice._OLD_CLOSE_HEAD)
    return text.replace("--- 正直さと締め方 ---", "・ユーザーが書いた行。\n\n--- 正直さと締め方 ---")


class TierSectionTests(unittest.TestCase):
    ALL = ["Distant", "Cautious", "Warming up", "Familiar", "Close",
           "Trusted", "Devoted", "Special"]

    def test_default_has_every_tier_and_no_example_lines(self):
        _, sections = ja_voice.split_tier_sections(ja_voice.DEFAULT_VOICE_BLOCK.strip())
        self.assertEqual(list(sections), self.ALL)
        for name, sec in sections.items():
            with self.subTest(tier=name):
                self.assertNotIn("例", sec)
        self.assertEqual(set(sections), {name for _, _, name in stats.STAT_DEFS[0]["tiers"]})

    def test_rules_carry_no_tier_text_and_closeness_carries_one(self):
        default = ja_voice.DEFAULT_VOICE_BLOCK.strip()
        with mock.patch.object(ja_voice, "load_voice_block", lambda: default), \
             mock.patch.object(ja_voice, "address_rule", lambda tier: ""), \
             mock.patch.object(ja_voice, "names_rule", lambda: ""):
            rules = ja_voice.build_voice_rules()["content"]
            devoted = ja_voice.build_closeness_context(96)["content"]
        self.assertNotIn("距離感 0-19", rules)
        self.assertNotIn("【Special】", rules)
        self.assertIn("--- 挨拶と、無視されたとき ---", rules)
        self.assertIn("【Devoted】", devoted)
        self.assertNotIn("【Special】", devoted)
        self.assertNotIn("【Trusted】", devoted)

    def test_file_without_tiers_is_sent_whole(self):
        common, sections = ja_voice.split_tier_sections("自分で書いた声のルール。")
        self.assertEqual(common, "自分で書いた声のルール。")
        self.assertEqual(sections, {})


class VoiceFileUpdateTests(unittest.TestCase):
    def test_old_file_gets_new_tiers_only(self):
        old = _old_style_voice()
        new = ja_voice._add_missing_tiers(old)
        _, sections = ja_voice.split_tier_sections(new)
        self.assertEqual(list(sections), TierSectionTests.ALL)
        self.assertIn(ja_voice._NEW_CLOSE_HEAD, new)
        self.assertNotIn(ja_voice._OLD_CLOSE_HEAD, new)
        self.assertIn("・ユーザーが書いた行。", new)
        # nothing else moved: removing the added sections gives the old text back
        self.assertEqual(ja_voice._add_missing_tiers(new), new)

    def test_custom_file_without_tiers_untouched(self):
        self.assertEqual(ja_voice._add_missing_tiers("自由な文章だけ。"), "自由な文章だけ。")

    def test_load_saves_the_update_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "voice_ja.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(_old_style_voice())
            with mock.patch.object(ja_voice, "VOICE_FILE", path):
                text = ja_voice.load_voice_block()
            with open(path, "r", encoding="utf-8") as f:
                saved = f.read()
        self.assertEqual(saved, text)
        self.assertIn("【Special】", saved)


if __name__ == "__main__":
    unittest.main()

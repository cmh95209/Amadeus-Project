# -*- coding: utf-8 -*-
"""Cross-tab memory (cross_tab.py, 2026-11-12).

- the two switches (defaults on, saved, bad input refused);
- the catch-up: other tabs only, newest first, summary + last lines for the
  newest, one line for older ones, filled to a token budget, dates (not
  "2 hours ago") so the text stays the same all day;
- the search: language-agnostic terms (words / 2-letter pieces), stop words
  found from the history itself, whole-word matching, other tabs only, the
  exchange around a hit;
- the built-in model's history gives room back; learn-on-leave asks the
  memory service with the minimum.
A temp SQLite store backs the REAL memory functions; no network.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import memory as store
import cross_tab


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            patch.object(store, "PATH_TO_MEMORY", str(Path(self._tmp.name) / "memory.db")),
            patch.object(store, "DATA_DIR", self._tmp.name),
            patch.object(cross_tab, "_brain", lambda: "cloud"),
        ]
        for p in self._patches:
            p.start()
        self.current = self._tab("Current chat", [("user", "hello"), ("assistant", "hi", "やあ")],
                                 day="2026-11-12 09:00")
        self._active = patch.object(store, "load_active_conversation", return_value=self.current)
        self._active.start()

    def tearDown(self):
        self._active.stop()
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _tab(self, title, lines, day="2026-11-10 20:00", summary=""):
        conn = sqlite3.connect(store.PATH_TO_MEMORY)
        conn.close()
        conv = store.create_conversation(title)
        for line in lines:
            role, text = line[0], line[1]
            store.append_message(role, text, conversation_id=conv,
                                 japanese=line[2] if len(line) > 2 else None)
        conn = sqlite3.connect(store.PATH_TO_MEMORY)
        conn.execute("UPDATE messages SET created_at = ? WHERE conversation_id = ?", (day, conv))
        if summary:
            conn.execute("UPDATE conversations SET rolling_summary = ? WHERE id = ?", (summary, conv))
        conn.commit()
        conn.close()
        return conv


class SettingsTests(_Base):
    def test_defaults_are_on(self):
        self.assertEqual(cross_tab.load_settings(), {"catch_up": True, "search": True})

    def test_save_one_switch(self):
        cross_tab.save_settings({"search": False})
        self.assertEqual(cross_tab.load_settings(), {"catch_up": True, "search": False})
        self.assertFalse(cross_tab.search_on())

    def test_bad_input_refused(self):
        with self.assertRaises(ValueError):
            cross_tab.save_settings({"search": "no"})
        with self.assertRaises(ValueError):
            cross_tab.save_settings({"other": True})

    def test_broken_file_means_defaults(self):
        Path(self._tmp.name, cross_tab.SETTINGS_FILE).write_text("{oops", encoding="utf-8")
        self.assertEqual(cross_tab.load_settings(), cross_tab.DEFAULTS)


class CatchUpTests(_Base):
    def _three(self):
        self._tab("Cooking", [("user", "I made curry"), ("assistant", "Measure the spices.", "スパイスを量って。")],
                  day="2026-11-11 19:00", summary="The user is learning to cook curry.")
        self._tab("Work", [("user", "Big meeting tomorrow"), ("assistant", "Prepare notes.", "メモを用意して。")],
                  day="2026-11-09 08:00", summary="A long meeting is coming up.")
        self._tab("Old trip", [("user", "Kyoto was lovely")], day="2026-10-01 12:00",
                  summary="A trip to Kyoto in autumn.")

    def test_other_tabs_newest_first_without_the_current_one(self):
        self._three()
        text = cross_tab.catch_up_block()["content"]
        self.assertTrue(text.startswith(cross_tab.CATCH_UP_LABEL))
        self.assertNotIn("Current chat", text)
        self.assertLess(text.index('"Cooking"'), text.index('"Work"'))
        self.assertLess(text.index('"Work"'), text.index('"Old trip"'))
        self.assertIn("learning to cook curry", text)
        self.assertIn('User: "I made curry"', text)
        self.assertIn('You: "スパイスを量って。"', text)       # her spoken line
        self.assertIn("Wed 11 Nov 2026", text)                  # a date, not "x hours ago"

    def test_older_tabs_get_one_line(self):
        with patch.object(cross_tab, "FULL_TABS", 1):
            self._three()
            text = cross_tab.catch_up_block()["content"]
        work = [l for l in text.splitlines() if '"Work"' in l][0]
        self.assertNotIn("Last:", work)
        self.assertIn("meeting is coming up", work)

    def test_budget_limits_the_tabs(self):
        self._three()
        budget = store.estimate_tokens(cross_tab.CATCH_UP_LABEL) + 45
        small = cross_tab.catch_up_block(budget=budget)
        self.assertIsNotNone(small)
        self.assertNotIn("Old trip", small["content"])
        self.assertLessEqual(store.estimate_tokens(small["content"]), budget)

    def test_off_or_nothing_else(self):
        self.assertIsNone(cross_tab.catch_up_block())          # only the current tab
        self._three()
        cross_tab.save_settings({"catch_up": False})
        self.assertIsNone(cross_tab.catch_up_block())

    def test_same_text_on_every_call(self):
        self._three()
        self.assertEqual(cross_tab.catch_up_block(), cross_tab.catch_up_block())


class TermTests(unittest.TestCase):
    def test_words_for_spaced_scripts(self):
        self.assertEqual(cross_tab.query_terms("The Curry recipe, again!"),
                         ["the", "curry", "recipe", "again"])
        self.assertEqual(cross_tab.query_terms("Привет мир"), ["привет", "мир"])
        self.assertEqual(cross_tab.query_terms("café a"), ["café"])  # single letters dropped

    def test_pieces_for_unspaced_scripts(self):
        self.assertEqual(cross_tab.query_terms("咖喱食谱"), ["咖喱", "喱食", "食谱"])
        self.assertEqual(cross_tab.query_terms("カレーを作った"), ["カレ", "レー", "ーを", "を作", "作っ", "った"])
        self.assertEqual(cross_tab.query_terms("ทดสอบ"), ["ทด", "ดส", "สอ", "อบ"])

    def test_mixed_run_split_at_the_script_change(self):
        self.assertEqual(cross_tab.query_terms("東京tower"), ["東京", "tower"])

    def test_full_width_and_case_folded(self):
        self.assertEqual(cross_tab.query_terms("ＣＵＲＲＹ"), ["curry"])


class SearchTests(_Base):
    def _history(self):
        lines = []
        for i in range(30):
            lines += [("user", f"small talk number {i} about the weather"),
                      ("assistant", f"reply {i} about the weather", "天気の話ね")]
        self._tab("Chit-chat", lines, day="2026-11-01 10:00")
        self.cooking = self._tab("Cooking", [
            ("user", "My green curry recipe needs more lemongrass"),
            ("assistant", "Then add lemongrass early.", "レモングラスは早めに入れて。"),
            ("user", "我学会了做咖喱"), ("assistant", "很好", "いいわね"),
        ], day="2026-11-05 19:00")
        # The current tab says the same words: never returned.
        store.append_message("user", "green curry recipe lemongrass", conversation_id=self.current)

    def test_finds_the_topic_in_another_tab_with_its_reply(self):
        self._history()
        found = cross_tab.search("what about that green curry recipe?")
        self.assertTrue(found)
        top = found[0]
        self.assertEqual(top["conversation_id"], self.cooking)
        self.assertIn("lemongrass", top["user"])
        self.assertEqual(top["her"], "レモングラスは早めに入れて。")

    def test_chinese_query_finds_chinese(self):
        self._history()
        found = cross_tab.search("你还记得我学会做咖喱的事吗")
        self.assertTrue(found)
        self.assertEqual(found[0]["user"], "我学会了做咖喱")
        # One plain word alone is not a topic - in Chinese as in English.
        self.assertEqual(cross_tab.search("咖喱"), [])

    def test_common_words_alone_find_nothing(self):
        self._history()
        self.assertEqual(cross_tab.search("about the weather"), [])

    def test_word_forms_and_whole_short_words(self):
        self._history()
        # Longer words find their other forms (recipes -> recipe) ...
        self.assertTrue(cross_tab.search("recipes with lemongrasses"))
        # ... short ones only themselves ("gre" is not "green").
        m = cross_tab._matcher("gre")
        self.assertFalse(m("green curry"))
        self.assertTrue(cross_tab._matcher("cat")("a cat sat"))
        self.assertFalse(cross_tab._matcher("cat")("category"))

    def test_one_ordinary_word_is_not_a_topic_but_a_name_is(self):
        self._history()
        store.append_message("user", "I bought Zettacare today", conversation_id=self.cooking)
        self.assertEqual(cross_tab.search("tell me about lemongrass"), [])   # one plain word
        self.assertTrue(cross_tab.search("did I mention Zettacare?"))         # one rare name

    def test_name_terms(self):
        self.assertEqual(cross_tab.name_terms("Hello. I took Gaviscon and an RTX5090 today"),
                         {"gaviscon", "rtx5090"})

    def test_block_and_switch(self):
        self._history()
        block = cross_tab.search_block("that green curry recipe")
        self.assertTrue(block["content"].startswith(cross_tab.SEARCH_LABEL))
        self.assertIn('In "Cooking" (Thu 5 Nov 2026)', block["content"])
        # A small budget still gets the best match, cut shorter.
        long_her = "レモングラス" * 60
        store.append_message("user", "green curry recipe with lemongrass again", conversation_id=self.cooking)
        store.append_message("assistant", "Long answer.", conversation_id=self.cooking, japanese=long_her)
        small = cross_tab.search_block("that green curry recipe", budget=150)
        self.assertIsNotNone(small)
        self.assertLessEqual(store.estimate_tokens(small["content"]), 150)
        cross_tab.save_settings({"search": False})
        self.assertIsNone(cross_tab.search_block("that green curry recipe"))
        self.assertIsNone(cross_tab.search_block(""))


class BudgetAndLearnTests(_Base):
    def test_reserve_only_on_the_built_in_model(self):
        self.assertEqual(cross_tab.reserve_tokens(), 0)
        with patch.object(cross_tab, "_brain", lambda: "builtin"):
            self.assertEqual(cross_tab.reserve_tokens(), sum(cross_tab.BUDGETS["builtin"]))
            cross_tab.save_settings({"catch_up": False})
            self.assertEqual(cross_tab.reserve_tokens(), cross_tab.BUDGETS["builtin"][1])

    def test_learn_on_leave(self):
        import cm_bridge
        with patch.object(cm_bridge, "learn_now") as learn:
            cross_tab.on_tab_left(3, 3)
            learn.assert_not_called()
            cross_tab.on_tab_left(3, 5)
            learn.assert_called_once_with(3, min_new=cross_tab.LEARN_ON_LEAVE_MIN)
            cross_tab.on_tab_left(None, 5)
            self.assertEqual(learn.call_count, 1)


if __name__ == "__main__":
    unittest.main()

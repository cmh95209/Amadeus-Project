"""Her lookup memory (2026-10-25).

Every web search inside a reply is saved per chat (query, results, pages read,
notes). A later search for the same thing in that chat uses the saved notes
while they are fresh; she sees an index of the chat's lookups and can reopen
one with reread_lookup (old notes on a time-sensitive topic -> the saved pages
are read again; nothing usable -> a fresh search). Lookups go with their chat,
message, undo or memory reset. Throwaway databases, faked network + model.
"""

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_web_search_resilience import FakeLLM, FakeReply, chat, pack_call  # noqa: E402

import memory as store  # noqa: E402

PAGES = [{"url": "https://prydwen.gg/banners", "title": "Genshin Banners", "date": "2026-10-24",
          "rank": 2, "notes": "- Skirk and Escoffier until Nov 3 \"Phase 2\""},
         {"url": "https://ign.com/x", "title": "IGN", "date": "2026-10-19", "rank": 3, "notes": None}]
RESULTS = [{"title": "Game8", "url": "https://game8.co/a"},
           {"title": "Genshin Banners", "url": "https://prydwen.gg/banners", "date": "2026-10-24"},
           {"title": "IGN", "url": "https://ign.com/x"}]
SEARCH_TEXT = chat._format_search_results([
    {"title": r["title"], "body": "snippet", "href": r["url"], "date": r.get("date", "")}
    for r in RESULTS])


class _Db(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self._dir.name) / "memory.db")
        self._p = patch.object(store, "PATH_TO_MEMORY", self.db)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._dir.cleanup()
        chat.take_turn_sources()

    def age(self, lookup_id, hours):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE web_lookups SET created_at = ? WHERE id = ?",
                     ((datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S"),
                      lookup_id))
        conn.commit()
        conn.close()

    def count(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT COUNT(*) FROM web_lookups").fetchone()[0]
        finally:
            conn.close()


class StoreTests(_Db):
    def test_save_load_recent(self):
        a = store.save_lookup(1, 3, "Genshin banner", "firecrawl", "reader", RESULTS, PAGES)
        b = store.save_lookup(1, 4, "Python version", "ddgs", "quick", [], [], "excerpt")
        store.save_lookup(2, 1, "other chat", None, None, [], [])
        lk = store.load_lookup(a)
        self.assertEqual((lk["conversation_id"], lk["turn"], lk["query"], lk["mode"]),
                         (1, 3, "Genshin banner", "reader"))
        self.assertEqual(lk["pages"][0]["notes"], PAGES[0]["notes"])
        self.assertEqual([x["id"] for x in store.recent_lookups(1)], [b, a])
        self.assertEqual(store.load_lookup(b)["excerpt"], "excerpt")
        self.assertIsNone(store.load_lookup(999))

    def test_sources_link_lookups_to_the_reply(self):
        uid = store.append_message("user", "banner?", conversation_id=1)
        her = store.append_message("assistant", "Skirk.", conversation_id=1)
        lid = store.save_lookup(1, 1, "Genshin banner", None, "reader", RESULTS, PAGES)
        store.set_message_sources(her, [{"kind": "search", "query": "q", "lookup_id": lid},
                                        {"kind": "weather", "place": "Tokyo"}])
        self.assertEqual(store.load_lookup(lid)["message_id"], her)
        store.delete_message(uid)
        self.assertIsNotNone(store.load_lookup(lid))          # the user's row had none
        store.delete_message(her)
        self.assertIsNone(store.load_lookup(lid))

    def test_chat_delete_reset_and_undo_remove_lookups(self):
        store.append_message("user", "a", conversation_id=1)
        store.save_lookup(1, 1, "q1", None, None, [], [])
        store.reset_memory(1)
        self.assertEqual(self.count(), 0)

        conv = store.create_conversation() if hasattr(store, "create_conversation") else None
        if conv is not None:
            store.append_message("user", "b", conversation_id=conv)
            store.save_lookup(conv, 1, "q2", None, None, [], [])
            store.delete_conversation(conv)
            self.assertEqual(self.count(), 0)

        with patch.object(store, "load_active_conversation", return_value=1):
            store.append_message("user", "c", conversation_id=1)
            her = store.append_message("assistant", "d", conversation_id=1)
            lid = store.save_lookup(1, 1, "q3", None, None, [], [])
            store.link_lookups([lid], her)
            store.undo_last_assistant(1)
        self.assertIsNone(store.load_lookup(lid))


class ReuseTests(_Db):
    def search(self, query, question="Who is on the banner?", run_text=SEARCH_TEXT,
               read=("", [])):
        chat._begin_turn_sources(1)
        chat._turn.question = question
        try:
            with patch.object(chat, "_run_web_search", return_value=run_text) as run, \
                 patch.object(chat, "_read_results", return_value=read):
                out = chat._web_search_with_content(query)
        finally:
            chat._turn.question = ""
        return out, run, chat.take_turn_sources()

    def test_a_search_is_saved_and_reused_while_fresh(self):
        def read(query, results, source):
            source["mode"], source["used"], source["read"] = "reader", [PAGES[0]["url"]], PAGES[0]["url"]
            return "notes block", [dict(p) for p in PAGES]
        chat._begin_turn_sources(1)
        with patch.object(chat, "_run_web_search", return_value=SEARCH_TEXT), \
             patch.object(chat, "_read_results", side_effect=read), \
             patch.object(store, "current_turn", return_value=7):
            chat._web_search_with_content("Genshin Impact current banner")
        sources = chat.take_turn_sources()
        lid = sources[0]["lookup_id"]
        saved = store.load_lookup(lid)
        self.assertEqual(saved["turn"], 7)
        self.assertEqual(saved["pages"][0]["notes"], PAGES[0]["notes"])

        out, run, sources = self.search("current Genshin Impact banner")
        run.assert_not_called()
        self.assertIn(f"Your saved notes from lookup #{lid} (turn 7", out)
        self.assertIn("Skirk and Escoffier until Nov 3", out)
        self.assertNotIn("IGN", out.split("<<<")[1] if "<<<" in out else "")  # no-notes page dropped
        self.assertTrue(sources[0]["reused"])
        self.assertEqual(sources[0]["used"], [PAGES[0]["url"]])

    def test_check_again_or_a_different_topic_searches(self):
        lid = store.save_lookup(1, 1, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        _, run, _ = self.search("Genshin Impact current banner", question="Can you check again?")
        run.assert_called_once()
        _, run, _ = self.search("Honkai Star Rail current banner")
        run.assert_called_once()
        self.assertTrue(store.load_lookup(lid))

    def test_old_notes_on_a_time_sensitive_topic_are_not_reused(self):
        lid = store.save_lookup(1, 1, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        self.age(lid, 30)                                   # 'w' topic: fresh for 24 h
        _, run, _ = self.search("Genshin Impact current banner")
        run.assert_called_once()
        lid2 = store.save_lookup(1, 1, "Steins;Gate anime release year", None, "reader", RESULTS, PAGES)
        self.age(lid2, 24 * 10)                             # timeless: fresh for 30 days
        _, run, _ = self.search("Steins;Gate anime release year")
        run.assert_not_called()

    def test_nothing_is_saved_or_reused_outside_a_reply(self):
        store.save_lookup(1, 1, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        chat._begin_turn_sources()                          # no chat attached
        with patch.object(chat, "_run_web_search", return_value=SEARCH_TEXT) as run, \
             patch.object(chat, "_read_results", return_value=("", [])):
            chat._web_search_with_content("Genshin Impact current banner")
        run.assert_called_once()
        self.assertEqual(self.count(), 1)

    def test_lookups_of_another_chat_are_never_used(self):
        store.save_lookup(2, 1, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        _, run, _ = self.search("Genshin Impact current banner")
        run.assert_called_once()


class IndexAndRereadTests(_Db):
    def test_index_lists_the_chats_lookups(self):
        chat._begin_turn_sources(1)
        self.assertIsNone(chat._lookup_index_message())
        a = store.save_lookup(1, 3, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        self.age(a, 2)
        msg = chat._lookup_index_message()["content"]
        self.assertIn("EARLIER LOOKUPS", msg)
        self.assertIn(f'#{a} (turn 3, 2 hours ago): "Genshin Impact current banner" - prydwen.gg', msg)
        self.assertIn("reread_lookup", msg)
        chat.take_turn_sources()
        self.assertIsNone(chat._lookup_index_message())    # outside a reply

    def test_reread_fresh_notes(self):
        lid = store.save_lookup(1, 3, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        chat._begin_turn_sources(1)
        steps = []
        chat._turn.progress = lambda s, t: steps.append(t)
        try:
            out = chat._reread_lookup(lid)
        finally:
            chat._turn.progress = None
        self.assertIn("Skirk and Escoffier", out)
        self.assertIn("still fresh", out)
        self.assertIn("Checking my notes from earlier...", steps)
        self.assertTrue(chat.take_turn_sources()[0]["reused"])

    def test_reread_old_notes_reads_the_pages_again(self):
        lid = store.save_lookup(1, 3, "Genshin Impact current banner", "firecrawl", "reader",
                                RESULTS, PAGES)
        self.age(lid, 48)
        chat._begin_turn_sources(1)
        seen = []

        def read(query, results, source):
            seen.append([r["url"] for r in results])
            source["mode"], source["used"] = "reader", ["https://prydwen.gg/banners"]
            return "fresh notes block", [dict(PAGES[0], notes="- Mitya next, Nov 4")]
        with patch.object(chat, "_read_results", side_effect=read), \
             patch.object(chat, "_run_web_search") as run:
            out = chat._reread_lookup(lid)
        run.assert_not_called()
        self.assertEqual(seen[0][0], "https://prydwen.gg/banners")   # the pages read before first
        self.assertIn("read again just now", out)
        self.assertIn("fresh notes block", out)
        self.assertEqual(self.count(), 2)                              # a fresh lookup is saved

    def test_reread_falls_back_to_a_new_search(self):
        lid = store.save_lookup(1, 3, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        self.age(lid, 48)
        chat._begin_turn_sources(1)
        with patch.object(chat, "_read_results", return_value=("", [])), \
             patch.object(chat, "_run_web_search", return_value=SEARCH_TEXT) as run:
            chat._reread_lookup(lid)
        run.assert_called_once()

    def test_unknown_or_foreign_lookup(self):
        other = store.save_lookup(2, 1, "x", None, None, [], [])
        chat._begin_turn_sources(1)
        self.assertIn("There is no lookup #999", chat._reread_lookup(999))
        self.assertIn(f"There is no lookup #{other}", chat._reread_lookup(other))
        self.assertIn("There is no lookup #abc", chat._reread_lookup("abc"))

    def test_the_tool_is_offered_only_with_lookups_and_works(self):
        msgs = [{"role": "system", "content": "persona"},
                {"role": "user", "content": "And what about the next banner after that?"}]
        log = []
        state = {"n": 0}

        def handler(convo):
            state["n"] += 1
            if state["n"] == 1 and llm.last_tools and "reread_lookup" in llm.last_tools:
                return FakeReply(tool_calls=[{"name": "reread_lookup", "args": {"lookup": lid}}])
            return FakeReply(tool_calls=pack_call(eng="Mitya is next."))
        chat._begin_turn_sources(1)
        llm = FakeLLM(handler, log)
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False):
            chat._getResponsePackedWithWebSearch(llm, msgs)
        self.assertNotIn("reread_lookup", llm.last_tools)
        chat.take_turn_sources()

        lid = store.save_lookup(1, 3, "Genshin Impact current banner", None, "reader", RESULTS, PAGES)
        chat._begin_turn_sources(1)
        log.clear()
        state["n"] = 0
        llm = FakeLLM(handler, log)
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False):
            pack = chat._getResponsePackedWithWebSearch(llm, msgs)
        self.assertIn("reread_lookup", llm.last_tools)
        self.assertEqual(pack.assistant_reply_ENG, "Mitya is next.")
        fed = [m["content"] for c in log for m in c if isinstance(m.get("content"), str)]
        self.assertTrue(any("Skirk and Escoffier until Nov 3" in t for t in fed))
        self.assertTrue(chat.take_turn_sources()[0]["reused"])


if __name__ == "__main__":
    unittest.main()

"""Links the user sends (2026-10-26).

Web ON: the app opens the links in the user's latest message (at most 3) -
risky links refused, every redirect hop checked first, X posts through
FxTwitter, Keenable's reader as the backup for pages that shut robots out -
the reader writes notes, her reply is written from the notes, each page is
saved as a lookup with its text. Web OFF: she says she can't open it. No
network anywhere: downloads, FxTwitter, Keenable and the model are faked.
"""

import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lookups import _Db  # noqa: E402
from test_web_search_resilience import FakeLLM, FakeReply, chat, pack_call, reply_calls  # noqa: E402
import test_prompt_order  # noqa: E402

import memory as store  # noqa: E402
import webfetch  # noqa: E402
import weblinks  # noqa: E402
import webreader  # noqa: E402
import websearch  # noqa: E402

LONG_TEXT = "\n\n".join(f"Paragraph {i} about the Divergence Meter and worldlines." * 3
                        for i in range(60))


def ok_page(text=LONG_TEXT, title="Page", final=None, via="page"):
    return {"status": "ok", "text": text, "title": title, "published": "2026-10-20",
            "updated": "", "title_month": "", "reason": "", "final_url": final, "via": via}


# --- finding links ----------------------------------------------------------------
class FindLinksTests(unittest.TestCase):
    def test_punctuation_and_brackets(self):
        text = ("look 「https://x.com/jack/status/20」 and (see "
                "https://en.wikipedia.org/wiki/Steins;Gate_(TV_series)). Also www.example.com/a, "
                "and https://example.com/a#part. これ→https://example.jp/記事。")
        links, more = weblinks.find_links(text)
        self.assertEqual(links, ["https://x.com/jack/status/20",
                                 "https://en.wikipedia.org/wiki/Steins;Gate_(TV_series)",
                                 "https://www.example.com/a"])
        self.assertEqual(more, 1)   # example.com/a#part is the same page; example.jp is the 4th

    def test_at_most_three(self):
        text = " ".join(f"https://site{i}.com/p" for i in range(5))
        links, more = weblinks.find_links(text)
        self.assertEqual(len(links), 3)
        self.assertEqual(more, 2)

    def test_no_link(self):
        self.assertEqual(weblinks.find_links("no links, just example dot com"), ([], 0))
        self.assertFalse(weblinks.has_links("http://localhost:5050 is mine"))
        self.assertTrue(weblinks.has_links("www.example.com"))

    def test_words_around_the_links(self):
        self.assertEqual(chat._question_without_links("https://a.com/x"), "")
        self.assertEqual(chat._question_without_links("what does https://a.com/x say?"),
                         "what does say?")


# --- risky links + redirects -------------------------------------------------------
class RiskyLinkTests(unittest.TestCase):
    def test_refused(self):
        self.assertEqual(webfetch.risky_link("https://user:pw@example.com/"), "carries a password or key")
        self.assertEqual(webfetch.risky_link("https://example.com/?token=abc"), "carries a password or key")
        self.assertEqual(webfetch.risky_link("https://example.com/?API_KEY=1&x=2"), "carries a password or key")
        self.assertEqual(webfetch.risky_link("https://example.com/" + "a" * 2100), "is too long")
        self.assertEqual(webfetch.risky_link("ftp://example.com/"), "is not a web address")

    def test_allowed(self):
        for url in ["https://example.com/news?id=5&page=2", "https://www.youtube.com/watch?v=abc",
                    "https://en.wikipedia.org/wiki/Key_(music)"]:
            self.assertEqual(webfetch.risky_link(url), "", url)

    def test_redirect_to_private_address_refused_before_it_is_followed(self):
        handler = webfetch._SafeRedirects()
        req = webfetch.urllib.request.Request("http://93.184.216.34/start")
        with self.assertRaises(webfetch._UnsafeRedirect):
            handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/admin")
        nxt = handler.redirect_request(req, None, 302, "Found", {}, "http://93.184.216.35/next")
        self.assertEqual(nxt.full_url, "http://93.184.216.35/next")
        self.assertEqual(webfetch._SafeRedirects.max_redirections, 5)

    def test_download_reports_unsafe_redirect(self):
        with patch.object(webfetch._OPENER, "open", side_effect=webfetch._UnsafeRedirect("x")):
            self.assertEqual(webfetch._download("http://93.184.216.34/", 3)[0], "unsafe")
        with patch.object(webfetch, "_download", return_value=("unsafe", "", "")):
            page = webfetch.fetch_page("http://93.184.216.34/")
        self.assertEqual(page["status"], "unsafe")
        self.assertIn("private or local", page["reason"])


class FetchPageTests(unittest.TestCase):
    def test_not_a_web_page(self):
        with patch.object(webfetch, "_download", return_value=(None, "%PDF-1.7 ...", "application/pdf")):
            page = webfetch.fetch_page("http://93.184.216.34/a.pdf")
        self.assertEqual(page["status"], "unsupported")
        self.assertIn("application/pdf", page["reason"])

    def test_short_page_read_when_the_user_gave_it(self):
        html = "<html><body><main><p>" + "Short but real text. " * 20 + "</p></main></body></html>"
        with patch.object(webfetch, "_download", return_value=(None, html, "text/html")):
            self.assertEqual(webfetch.fetch_page("http://93.184.216.34/")["status"], "empty")
            self.assertEqual(webfetch.fetch_page("http://93.184.216.34/", min_chars=200)["status"], "ok")

    def test_reasons(self):
        for err, reason in [("http_401", "needs a login"), ("http_402", "is behind a paywall"),
                            ("http_404", "was not found"), ("http_503", "had a server error"),
                            ("TimeoutError('timed out')", "took too long")]:
            with patch.object(webfetch, "_download", return_value=(err, "", "")):
                self.assertEqual(webfetch.fetch_page("http://93.184.216.34/")["reason"], reason, err)

    def test_final_url_after_redirects(self):
        def fake(url, timeout, meta=None):
            meta["final_url"] = "http://93.184.216.34/final"
            return None, "<html><body><main><p>" + "x " * 1500 + "</p></main></body></html>", "text/html"
        with patch.object(webfetch, "_download", side_effect=fake):
            self.assertEqual(webfetch.fetch_page("http://93.184.216.34/s")["final_url"],
                             "http://93.184.216.34/final")


# --- X posts + Keenable's reader ------------------------------------------------------
FX = {"code": 200, "tweet": {
    "text": "just setting up my twttr", "created_timestamp": 1142974214,
    "author": {"name": "jack", "screen_name": "jack"}, "likes": 300000, "retweets": 120000,
    "replies": 18000, "media": {"photos": [{}]},
    "quote": {"text": "quoted words", "author": {"name": "Q", "screen_name": "q"}}}}


class XPostTests(unittest.TestCase):
    def test_post_links(self):
        self.assertEqual(weblinks.x_post("https://x.com/jack/status/20"), ("jack", "20"))
        self.assertEqual(weblinks.x_post("https://mobile.twitter.com/a_b/status/123?s=20"), ("a_b", "123"))
        self.assertIsNone(weblinks.x_post("https://x.com/jack"))
        self.assertTrue(weblinks.is_x_link("https://x.com/search?q=amadeus"))
        self.assertFalse(weblinks.is_x_link("https://example.com/x.com"))

    def test_read_through_fxtwitter(self):
        with patch.object(weblinks, "_get_json", return_value=FX) as get:
            page = weblinks.fetch_link("https://x.com/jack/status/20")
        self.assertIn("api.fxtwitter.com/jack/status/20", get.call_args[0][0])
        self.assertEqual((page["status"], page["via"], page["published"]), ("ok", "x", "2006-03-21"))
        self.assertIn("just setting up my twttr", page["text"])
        self.assertIn("Quoting a post by Q (@q)", page["text"])
        self.assertIn("1 photo(s)", page["text"])

    def test_missing_post(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with patch.object(weblinks, "_get_json", side_effect=err):
            page = weblinks.fetch_link("https://x.com/jack/status/20")
        self.assertEqual(page["status"], "error")
        self.assertIn("private, deleted or not found", page["reason"])

    def test_profile_needs_a_login(self):
        with patch.object(weblinks, "_get_json") as get:
            page = weblinks.fetch_link("https://x.com/jack")
        get.assert_not_called()
        self.assertEqual(page["status"], "unsupported")
        self.assertIn("login", page["reason"])


class KeenableBackupTests(unittest.TestCase):
    def setUp(self):
        websearch.reset_keenable_cooldown()

    def tearDown(self):
        websearch.reset_keenable_cooldown()

    def test_blocked_page_read_through_keenable(self):
        answer = {"url": "https://example.com/a", "title": "A", "content": "[Link](https://z.com) " + "words " * 100,
                  "published_at": 1792800000}
        with patch.object(webfetch, "fetch_page", return_value={"status": "blocked", "reason": "blocked automated access",
                                                                 "final_url": "https://example.com/a"}), \
             patch.object(weblinks, "_get_json", return_value=answer) as get:
            page = weblinks.fetch_link("https://example.com/a")
        self.assertEqual((page["status"], page["via"]), ("ok", "keenable"))
        self.assertIn("live=true", get.call_args[0][0])
        self.assertEqual(get.call_args[0][2], {"X-Keenable-Title": websearch.KEENABLE_APP})
        self.assertNotIn("https://z.com", page["text"])
        self.assertRegex(page["published"], r"^\d{4}-\d{2}-\d{2}$")

    def test_no_backup_for_login_or_paywall(self):
        for reason in ("needs a login", "is behind a paywall", "was not found"):
            with patch.object(webfetch, "fetch_page", return_value={"status": "error", "reason": reason}), \
                 patch.object(weblinks, "_get_json") as get:
                page = weblinks.fetch_link("https://example.com/a")
            get.assert_not_called()
            self.assertEqual(page["reason"], reason)

    def test_rate_limit_cools_down(self):
        err = urllib.error.HTTPError("u", 429, "busy", {}, None)
        with patch.object(weblinks, "_get_json", side_effect=err):
            self.assertEqual(weblinks.keenable_page("https://example.com/a")["status"], "error")
        self.assertTrue(websearch.keenable_cooling_down())
        with patch.object(weblinks, "_get_json") as get:
            weblinks.keenable_page("https://example.com/a")
        get.assert_not_called()

    def test_refused_link_never_leaves_the_pc(self):
        with patch.object(webfetch, "fetch_page") as fp, patch.object(weblinks, "_get_json") as get:
            page = weblinks.fetch_link("https://example.com/?password=x")
        fp.assert_not_called()
        get.assert_not_called()
        self.assertEqual(page["status"], "refused")


# --- the reader ------------------------------------------------------------------------
class LinkReaderTests(unittest.TestCase):
    def test_spread_without_a_question(self):
        text = "\n\n".join(f"Part {i}. " + "x" * 300 for i in range(40))
        out = webreader.spread_chunks(text, 3000)
        self.assertIn("Part 0.", out)
        self.assertLessEqual(len(out), 3200)
        self.assertTrue(any(f"Part {i}." in out for i in range(20, 40)))   # not only the top

    def test_rules_and_longer_notes(self):
        seen = {}

        def reader(msgs, timeout):
            seen["system"] = msgs[0]["content"]
            return "\n".join(f"- note {i}" for i in range(14))
        pages = [{"url": "https://a.com/x", "title": "A", "date": "", "text": LONG_TEXT, "rank": 1}]
        webreader.read_pages("", "", pages, reader, webreader.time.monotonic() + 5, "2026-10-26",
                             4000, links=True)
        self.assertIn("without a question", seen["system"])
        self.assertIn("never copy links", seen["system"])
        self.assertEqual(pages[0]["notes"].count("\n") + 1, webreader.LINK_NOTES_MAX_LINES)
        block = webreader.link_notes_block(pages)
        self.assertIn("[L1] A (a.com", block)
        self.assertIn(webreader.WEB_DATA_OPEN, block)

    def test_question_task(self):
        rules = webreader.link_reader_rules("2026-10-26", "is this true?")
        self.assertIn("\"is this true?\"", rules)


# --- the reply ---------------------------------------------------------------------------
def fake_reader(notes="- The page says the meter reads 1.048596."):
    return lambda pin, max_tokens=0: (lambda msgs, timeout: notes)


class ReadUserLinksTests(_Db):
    def run_links(self, message, pages, plan=("reader", 1, None, True), reader=None):
        conv = 1
        store.append_message("user", message, conversation_id=conv)
        chat._begin_turn_sources(conv)
        chat._turn.question = message
        with patch.object(weblinks, "fetch_link", side_effect=lambda u, t: dict(pages[u])), \
             patch.object(chat, "web_reading_plan", return_value=plan), \
             patch.object(chat.store, "effective_context_budget", return_value=8000), \
             patch.object(chat, "_reader_call", reader or fake_reader()):
            text = chat._read_user_links(message)
        return text, chat.take_turn_sources()

    def test_notes_failures_and_sources(self):
        pages = {"https://a.com/x": ok_page(title="Meter", final="https://a.com/final"),
                 "https://b.com/y": {"status": "error", "reason": "needs a login"}}
        text, sources = self.run_links("what do https://a.com/x and https://b.com/y say?", pages)
        self.assertIn("[L1] Meter", text)
        self.assertIn("1.048596", text)
        self.assertIn("[L2] Could not open b.com: it needs a login.", text)
        self.assertIn("never guess what it says", text)
        self.assertNotIn("Paragraph 3 about", text)      # raw page text never reaches her
        kinds = [(s["kind"], s["status"]) for s in sources]
        self.assertEqual(kinds, [("link", "failed"), ("link", "ok")])
        ok = sources[1]
        self.assertEqual((ok["url"], ok["final"]), ("https://a.com/x", "https://a.com/final"))
        saved = store.load_lookup(ok["lookup_id"])
        self.assertEqual(saved["query"], "link: https://a.com/x")
        self.assertTrue(saved["pages"][0]["text"].startswith("Paragraph 0"))

    def test_same_link_soon_after_is_not_downloaded_again(self):
        pages = {"https://a.com/x": ok_page()}
        self.run_links("https://a.com/x", pages)
        with patch.object(weblinks, "fetch_link") as fetch:
            store.append_message("user", "and what about the meter?", conversation_id=1)
            chat._begin_turn_sources(1)
            chat._turn.question = "what does https://a.com/x say about the meter?"
            with patch.object(chat, "web_reading_plan", return_value=("reader", 1, None, True)), \
                 patch.object(chat.store, "effective_context_budget", return_value=8000), \
                 patch.object(chat, "_reader_call", fake_reader()):
                text = chat._read_user_links(chat._turn.question)
            sources = chat.take_turn_sources()
        fetch.assert_not_called()
        self.assertIn("1.048596", text)
        self.assertTrue(sources[0].get("reused"))

    def test_more_than_three(self):
        msg = " ".join(f"https://s{i}.com/p" for i in range(4))
        pages = {f"https://s{i}.com/p": ok_page() for i in range(4)}
        text, _ = self.run_links(msg, pages)
        self.assertIn("1 more link; only the first 3 were opened", text)

    def test_quick_mode_uses_best_paragraphs(self):
        pages = {"https://a.com/x": ok_page()}
        text, _ = self.run_links("https://a.com/x", pages, plan=("quick", 0, None, True))
        self.assertIn("[L1]", text)
        self.assertIn("Paragraph 0", text)

    def test_reread_tool_reads_the_saved_text(self):
        _, sources = self.run_links("https://a.com/x", {"https://a.com/x": ok_page()})
        lid = sources[0]["lookup_id"]
        chat._begin_turn_sources(1)
        chat._turn.question = "what else did it say?"
        with patch.object(weblinks, "fetch_link") as fetch, \
             patch.object(chat, "web_reading_plan", return_value=("reader", 1, None, True)), \
             patch.object(chat.store, "effective_context_budget", return_value=8000), \
             patch.object(chat, "_reader_call", fake_reader("- It also covers worldlines.")):
            text = chat._reread_lookup(lid)
        chat.take_turn_sources()
        fetch.assert_not_called()
        self.assertIn("worldlines", text)
        self.assertIn(f"lookup #{lid} is a page the user sent", text)

    def test_link_lookups_never_answer_a_search(self):
        self.run_links("https://a.com/x", {"https://a.com/x": ok_page()})
        chat._begin_turn_sources(1)
        chat._turn.question = "a com x"
        self.assertIsNone(chat._matching_lookup("a com x"))
        chat.take_turn_sources()


class LoopTests(unittest.TestCase):
    def run_loop(self, text, handler, triage=None):
        log = []
        msgs = [{"role": "system", "content": "persona"}, {"role": "user", "content": text}]
        chat._begin_turn_sources()
        with patch.object(chat.time, "sleep"), \
             patch.object(chat.store, "load_deep_thinking", return_value=False), \
             patch.object(chat, "_read_user_links", return_value="NOTES [L1] the meter") as read, \
             patch.object(chat, "_run_web_search", return_value="1. X\nURL: https://x.com/\n") as run:
            pack = chat._getResponsePackedWithWebSearch(FakeLLM(handler, log, triage=triage), msgs)
        return pack, reply_calls(log), read, run, chat.take_turn_sources()

    def test_link_notes_reach_her_and_no_search_runs(self):
        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="The meter reads 1.048596."))
        triage = [{"name": "WebTriagePack", "args": {
            "wants_weather": "no", "weather_place": "none", "wants_search": "yes",
            "search_topic": "https://a.com/x", "wants_web_check": "no"}}]
        pack, log, read, run, _ = self.run_loop("can you check https://a.com/x for me?", handler, triage)
        read.assert_called_once()
        run.assert_not_called()          # "check this link" is not a search
        prompt = " ".join(str(m.get("content")) for m in log[0])
        self.assertIn("NOTES [L1] the meter", prompt)
        self.assertNotIn("no web search was actually run", prompt)
        self.assertIn("1.048596", pack.assistant_reply_ENG)

    def test_message_without_links_skips_the_link_reader(self):
        def handler(convo):
            return FakeReply(tool_calls=pack_call(eng="Hi."))
        _pack, _log, read, _run, _ = self.run_loop("how are you?", handler)
        read.assert_not_called()


class WebOffTests(unittest.TestCase):
    right_now = False
    _run = test_prompt_order.PromptOrderTests._run   # her whole prompt, web OFF, faked model

    def test_web_off_she_says_she_cannot_open_it(self):
        with patch.object(weblinks, "fetch_link") as fetch:
            msgs = self._run(chat.getResponsePacked, 50, "MEMORY", "TIMING",
                             [{"role": "user", "content": "read https://a.com/x please"}])
            plain = self._run(chat.getResponsePacked, 50, "MEMORY", "TIMING",
                              [{"role": "user", "content": "how are you?"}])
        fetch.assert_not_called()
        text = " ".join(str(m.get("content")) for m in msgs)
        self.assertIn("web access is OFF", text)
        self.assertNotIn("web access is OFF", " ".join(str(m.get("content")) for m in plain))


class SourcesForUiTests(unittest.TestCase):
    def test_link_entries(self):
        out = store.sources_for_ui([
            {"kind": "link", "url": "https://a.com/x", "final": "https://a.com/f", "title": "A",
             "date": "2026-10-20", "status": "ok", "via": "page", "lookup_id": 3},
            {"kind": "link", "url": "https://b.com/y", "status": "failed", "reason": "needs a login"},
            {"kind": "link", "url": "javascript:alert(1)", "status": "ok"}])
        self.assertEqual(out, [
            {"kind": "link", "url": "https://a.com/x", "final": "https://a.com/f", "title": "A",
             "opened": True, "date": "2026-10-20"},
            {"kind": "link", "url": "https://b.com/y", "final": "https://b.com/y", "title": "",
             "opened": False, "reason": "needs a login"}])


if __name__ == "__main__":
    unittest.main()

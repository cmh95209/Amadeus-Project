# -*- coding: utf-8 -*-
"""Offline checks for the local page reader (webfetch) and its wiring.

No network anywhere: webfetch._download is faked, and chat's search is faked.
Covers char_budget scaling, the SSRF guard, html_to_text, bot-wall detection,
fetch_page_text statuses, and chat._web_search_with_content (content appended
when a page reads cleanly; plain snippets returned when it can't).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import webfetch  # noqa: E402
import chat  # noqa: E402


class CharBudgetTests(unittest.TestCase):
    def test_large_window_uses_ceiling(self):
        self.assertEqual(webfetch.char_budget(40000), webfetch.MAX_PAGE_CHARS)

    def test_small_window_shrinks(self):
        # 4000 tokens * 4 chars * 0.35 = 5600
        self.assertEqual(webfetch.char_budget(4000), 5600)

    def test_floor_never_below_min(self):
        self.assertGreaterEqual(webfetch.char_budget(100), webfetch.MIN_PAGE_CHARS)

    def test_none_uses_ceiling(self):
        self.assertEqual(webfetch.char_budget(None), webfetch.MAX_PAGE_CHARS)


class SsrfTests(unittest.TestCase):
    BLOCKED = [
        "http://127.0.0.1/secret", "http://localhost/admin",
        "https://192.168.1.1/router", "http://10.0.0.5/",
        "http://172.16.0.1/", "http://169.254.1.1/", "http://0.0.0.0/",
        "http://[::1]/", "http://100.64.0.1/", "http://host.lan/",
    ]

    def test_blocks_private_and_local(self):
        for u in self.BLOCKED:
            self.assertFalse(webfetch.is_safe_public_url(u)[0], u)

    def test_blocks_non_http_scheme(self):
        self.assertFalse(webfetch.is_safe_public_url("ftp://example.com")[0])
        self.assertFalse(webfetch.is_safe_public_url("file:///etc/passwd")[0])
        self.assertFalse(webfetch.is_safe_public_url("javascript:alert(1)")[0])

    def test_allows_public_ip_literal(self):
        self.assertTrue(webfetch.is_safe_public_url("https://8.8.8.8/")[0])

    def test_hostname_checked_via_resolver(self):
        # No real DNS: patch the resolver.
        pub = [(2, 1, 1, "", ("93.184.216.34", 80))]
        with patch.object(webfetch.socket, "getaddrinfo", return_value=pub):
            self.assertTrue(webfetch.is_safe_public_url("https://example.com")[0])
        priv = [(2, 1, 1, "", ("192.168.0.9", 80))]
        with patch.object(webfetch.socket, "getaddrinfo", return_value=priv):
            self.assertFalse(webfetch.is_safe_public_url("https://example.com")[0])


class HtmlToTextTests(unittest.TestCase):
    def test_strips_script_and_style(self):
        html = ("<html><head><style>.a{color:red}</style></head>"
                "<body><script>var x=1;</script><p>Hello world.</p></body></html>")
        text = webfetch.html_to_text(html)
        self.assertIn("Hello world.", text)
        self.assertNotIn("var x", text)
        self.assertNotIn("color:red", text)

    def test_headings_and_lists(self):
        html = "<h1>Title</h1><ul><li>one</li><li>two</li></ul>"
        text = webfetch.html_to_text(html)
        self.assertIn("# Title", text)
        self.assertIn("- one", text)
        self.assertIn("- two", text)

    def test_main_content_preferred_over_sidebar(self):
        junk = "<div>" + ("Sidebar junk. " * 400) + "</div>"
        main = "<main><p>" + ("Real article sentence. " * 60) + "</p></main>"
        text = webfetch.html_to_text(junk + main)
        self.assertIn("Real article sentence.", text)
        self.assertNotIn("Sidebar junk", text)

    def test_falls_back_to_all_when_main_thin(self):
        html = ("<article><p>tiny</p></article><div>"
                + ("Body text here. " * 60) + "</div>")
        text = webfetch.html_to_text(html)
        self.assertIn("Body text here.", text)


class BotWallTests(unittest.TestCase):
    def test_cloudflare(self):
        self.assertTrue(webfetch.looks_bot_walled("Just a moment... checking your browser"))

    def test_access_denied(self):
        self.assertTrue(webfetch.looks_bot_walled("Access to this page has been denied."))

    def test_normal_page_not_walled(self):
        self.assertFalse(webfetch.looks_bot_walled(
            "Lenggong is a town in Perak. The weather today is warm and humid."))


class FetchStatusTests(unittest.TestCase):
    LONG = "Word " * 700  # ~3500 chars, over MIN_PAGE_CHARS

    def _html(self):
        return "<html><body><p>" + self.LONG + "</p></body></html>"

    def test_ok_html(self):
        with patch.object(webfetch, "_download",
                          return_value=(None, self._html(), "text/html")):
            status, text = webfetch.fetch_page_text("https://example.com/a")
        self.assertEqual(status, "ok")
        self.assertIn("Word", text)

    def test_truncates_to_budget(self):
        with patch.object(webfetch, "_download",
                          return_value=(None, self._html(), "text/html")):
            status, text = webfetch.fetch_page_text("https://example.com/a", max_chars=3000)
        self.assertEqual(status, "ok")
        self.assertLessEqual(len(text), 3100)
        self.assertIn("truncated", text)

    def test_blocked(self):
        with patch.object(webfetch, "_download",
                          return_value=("http_403",
                                        "<html><body>Just a moment...</body></html>",
                                        "text/html")):
            status, _ = webfetch.fetch_page_text("https://example.com/a")
        self.assertEqual(status, "blocked")

    def test_empty_when_thin(self):
        with patch.object(webfetch, "_download",
                          return_value=(None,
                                        "<html><body><p>Nothing here.</p></body></html>",
                                        "text/html")):
            status, _ = webfetch.fetch_page_text("https://example.com/a")
        self.assertEqual(status, "empty")

    def test_unsafe_refused_before_download(self):
        status, _ = webfetch.fetch_page_text("http://127.0.0.1/x")
        self.assertEqual(status, "unsafe")

    def test_error_on_download_failure(self):
        with patch.object(webfetch, "_download", return_value=("OSError('boom')", "", "")):
            status, _ = webfetch.fetch_page_text("https://example.com/a")
        self.assertEqual(status, "error")


class WiringTests(unittest.TestCase):
    RESULTS = ("1. Top page\nsome snippet\nURL: https://example.com/a\n\n"
               "2. Second page\nanother snippet\nURL: https://example.com/b")

    def test_appends_fetched_content(self):
        with patch.object(chat, "_run_web_search", return_value=self.RESULTS), \
             patch.object(webfetch, "fetch_page_text",
                          return_value=("ok", "The real numbers: 31.7 C, AQI 142.")):
            out = chat._web_search_with_content("Lenggong weather")
        self.assertIn("Top page", out)                 # snippets preserved
        self.assertIn("The real numbers: 31.7 C", out)  # fetched content appended

    def test_falls_back_to_snippets_when_walled(self):
        with patch.object(chat, "_run_web_search", return_value=self.RESULTS), \
             patch.object(webfetch, "fetch_page_text", return_value=("blocked", "")):
            out = chat._web_search_with_content("Lenggong weather")
        self.assertEqual(out, self.RESULTS)  # plain snippets, unchanged

    def test_no_urls_returns_search_unchanged(self):
        with patch.object(chat, "_run_web_search",
                          return_value="No web results were found."):
            out = chat._web_search_with_content("anything")
        self.assertEqual(out, "No web results were found.")


if __name__ == "__main__":
    unittest.main()

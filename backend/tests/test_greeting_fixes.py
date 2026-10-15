"""Greeting bug fixes (2026-10-15 review):

1. The switch greeting knows the tab the user LEFT. The client activates
   the target tab BEFORE it asks for the greeting, so the route reads the
   previous tab from the request body ({"previous_id": n}) instead of the
   (already switched) active tab.
2. The time-lie net lets a genuine welcome-back through: on a real return,
   absence wording ("久しぶり", "it's been a while") is true, not a lie -
   only an OVERSTATED gap is still caught.
3. A greeting is pinned to the tab it was written for: the history it reads
   and the row it stores follow that tab, even if the active tab changes
   while the line is being generated.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402 - the shared harness


class SwitchGreetingPreviousTabTests(_FreshAppTests):
    """Fix 1: the route forwards the tab the user left."""

    def setUp(self):
        super().setUp()
        self.memory.save_ceremony_state("done")
        self.chat.setKey("sk-test")
        self.tab_a = self.memory.load_active_conversation()
        self.tab_b = self.memory.create_conversation("Tab B")
        self.pack = self.chat.AmadeusPack(
            assistant_reply_ENG="So, this one.", assistant_reply_JPS="で、こっち。")
        self.seen = {}

        def fake_generate(**kw):
            self.seen.update(kw)
            return self.pack, 7, kw.get("conversation_id")
        self.fake_generate = fake_generate

    def switch_like_the_client(self, body):
        # The real client order: activate the target, THEN ask for the line.
        self.client.post(f"/conversations/{self.tab_b}/activate")
        with self.model(True), patch.object(
                self.api, "generate_greeting", side_effect=self.fake_generate):
            return self.client.post(f"/conversations/{self.tab_b}/greet",
                                    json=body)

    def test_previous_id_from_body_reaches_the_generator(self):
        res = self.switch_like_the_client({"previous_id": self.tab_a})
        self.assertIs(res.json["ready"], True)
        self.assertEqual(self.seen["conversation_id"], self.tab_b)
        self.assertEqual(self.seen["previous_conversation_id"], self.tab_a)

    def test_without_body_falls_back_to_no_usable_previous_tab(self):
        # Old client: the only thing left to read is the active tab, which is
        # the target itself - the summary lookup then drops it (None).
        self.switch_like_the_client({})
        self.assertEqual(self.seen["previous_conversation_id"], self.tab_b)
        self.assertIsNone(self.memory.load_previous_tab_summary(
            self.tab_b, exclude_conversation_id=self.tab_b))

    def test_invalid_previous_id_is_ignored(self):
        for bad in ("2", True, None, 1.5):
            with self.subTest(bad=bad):
                self.seen.clear()
                res = self.switch_like_the_client({"previous_id": bad})
                self.assertEqual(res.status_code, 200)
                self.assertNotEqual(self.seen["previous_conversation_id"], bad)

    def test_previous_tab_summary_names_the_left_tab(self):
        self.memory.append_message("user", "good night!",
                                   conversation_id=self.tab_a)
        summary = self.memory.load_previous_tab_summary(
            self.tab_a, exclude_conversation_id=self.tab_b)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["conversation_id"], self.tab_a)
        self.assertIn("good night!", summary["last_message"])


if __name__ == "__main__":
    unittest.main()

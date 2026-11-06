"""Settings -> Personality & mood: /mood and /concerns (2026-10-30)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ceremony import _FreshAppTests  # noqa: E402 - shared harness


class MoodApiTests(_FreshAppTests):
    def test_calm_and_empty(self):
        data = self.client.get("/mood").get_json()
        self.assertEqual((data["feelings"], data["concerns"]), ([], []))

    def test_feelings_worded_for_the_user(self):
        Path("data").mkdir(exist_ok=True)
        Path("data/feelings.txt").write_text(
            "irritation = 40\nirritation_reason = your last 2 greetings got no reply\n",
            encoding="utf-8")
        data = self.client.get("/mood").get_json()
        self.assertEqual(data["feelings"],
                         ["Quite irritated (her last 2 greetings got no reply)"])

    def test_concerns_listed_and_deleted(self):
        Path("data").mkdir(exist_ok=True)
        Path("data/concerns.txt").write_text(
            "[open]\n- exam | added 2026-10-29 | due 2026-11-02\n- cold\n", encoding="utf-8")
        data = self.client.get("/mood").get_json()
        self.assertEqual([c["text"] for c in data["concerns"]], ["exam", "cold"])
        self.assertEqual(data["concerns"][0]["due"], "2026-11-02")
        self.assertEqual(self.client.delete("/concerns/0").status_code, 200)
        data = self.client.get("/mood").get_json()
        self.assertEqual([c["text"] for c in data["concerns"]], ["cold"])
        self.assertEqual(self.client.delete("/concerns/5").status_code, 404)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Untouched old defaults are updated, edited files are kept (2026-10-29).

  1. a personality / voice file that is still an older shipped default (only
     spacing differs) is replaced at startup, with a dated backup;
  2. a file the user edited is left alone and gets a one-time Settings note;
     "Keep mine" hides it until the next default, "Use the new default"
     backs the edit up and writes the default;
  3. the current defaults are registered in OLD_DEFAULTS, so the next
     version can recognise them.
All files live in a temporary folder; no live data is touched.
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import defaults_refresh as dr
import ja_voice
import memory

OLD_P = "古いデフォルトの人格。\n- old rule\n"
OLD_V = "【声のルール】\n古い声。\n"
NEW_P = "新しいデフォルトの人格。\n- new rule"


def _current_modules():
    """Other test files drop and re-import modules mid-run; use the copies
    the code will actually import."""
    global dr, ja_voice, memory
    dr = importlib.import_module("defaults_refresh")
    ja_voice = importlib.import_module("ja_voice")
    memory = importlib.import_module("memory")


# The real data files of this checkout: no test here may change them (a
# leaked path once overwrote the worktree's personality.txt - 2026-10-29).
_REAL_FILES = [ROOT / "data" / n for n in ("personality.txt", "voice_ja.txt", "defaults_seen.json")]
_real_before = {}


def setUpModule():
    for p in _REAL_FILES:
        _real_before[p] = p.read_bytes() if p.exists() else None


def tearDownModule():
    for p, before in _real_before.items():
        after = p.read_bytes() if p.exists() else None
        assert after == before, f"a test changed the real file {p}"


class _Temp(unittest.TestCase):
    def setUp(self):
        _current_modules()
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.p_path = os.path.join(d, "personality.txt")
        self.v_path = os.path.join(d, "voice_ja.txt")
        default_path = os.path.join(d, "personality.default.txt")
        with open(default_path, "w", encoding="utf-8") as f:
            f.write(NEW_P)
        values = {
            "PERSONALITY_FILE": self.p_path,
            "VOICE_FILE": self.v_path,
            "PERSONALITY_DEFAULT_FILE": default_path,
            "SEEN_FILE": os.path.join(d, "defaults_seen.json"),
            "OLD_DEFAULTS": {"personality": {dr.fingerprint(OLD_P)},
                             "voice": {dr.fingerprint(OLD_V)}},
        }
        # every loaded copy of the module (api may hold an older one)
        copies = {id(dr): dr}
        api = sys.modules.get("api")
        if api is not None and hasattr(api, "defaults_refresh"):
            copies[id(api.defaults_refresh)] = api.defaults_refresh
        self._patches = [mock.patch.object(mod, name, value)
                         for mod in copies.values() for name, value in values.items()]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def backups(self, path):
        folder, name = os.path.split(path)
        return [n for n in os.listdir(folder) if n.startswith(name + ".bak_")]


class RefreshTests(_Temp):
    def test_untouched_old_defaults_are_replaced_with_backup(self):
        with open(self.p_path, "wb") as f:  # Windows line ends + trailing spaces only
            f.write((OLD_P.replace("\n", "  \r\n") + "   \r\n").encode("utf-8"))
        self.write(self.v_path, OLD_V)
        self.assertEqual(sorted(dr.refresh_untouched()), ["personality", "voice"])
        self.assertEqual(self.read(self.p_path), NEW_P)
        self.assertEqual(self.read(self.v_path), ja_voice.DEFAULT_VOICE_BLOCK.strip())
        self.assertEqual(len(self.backups(self.p_path)), 1)
        self.assertEqual(len(self.backups(self.v_path)), 1)
        self.assertEqual(dr.notices(), [])

    def test_edited_files_are_kept_and_noted(self):
        self.write(self.p_path, OLD_P + "- my own rule\n")
        self.write(self.v_path, OLD_V + "自分で足した行。\n")
        self.assertEqual(dr.refresh_untouched(), [])
        self.assertIn("my own rule", self.read(self.p_path))
        self.assertEqual([n["name"] for n in dr.notices()], ["personality", "voice"])
        self.assertEqual(self.backups(self.p_path), [])

    def test_current_and_missing_files_need_nothing(self):
        self.write(self.p_path, NEW_P)
        self.assertEqual(dr.status("personality"), "current")
        self.assertEqual(dr.status("voice"), "missing")
        self.assertEqual(dr.refresh_untouched(), [])
        self.assertEqual(dr.notices(), [])

    def test_keep_mine_hides_the_note(self):
        self.write(self.p_path, "全部自分で書いた。")
        dr.keep_mine("personality")
        self.assertEqual(dr.notices(), [])
        self.assertEqual(self.read(self.p_path), "全部自分で書いた。")

    def test_note_comes_back_for_a_newer_default(self):
        self.write(self.p_path, "全部自分で書いた。")
        dr.keep_mine("personality")
        self.write(dr.PERSONALITY_DEFAULT_FILE, NEW_P + "\n- even newer")
        self.assertEqual([n["name"] for n in dr.notices()], ["personality"])

    def test_use_new_default_backs_up_the_edit(self):
        self.write(self.p_path, "全部自分で書いた。")
        backup = dr.use_new_default("personality")
        self.assertEqual(self.read(self.p_path), NEW_P)
        self.assertTrue(backup.startswith("personality.txt.bak_"))
        folder = os.path.dirname(self.p_path)
        self.assertEqual(self.read(os.path.join(folder, backup)), "全部自分で書いた。")
        self.assertEqual(dr.notices(), [])

    def test_missing_default_file_is_harmless(self):
        os.remove(dr.PERSONALITY_DEFAULT_FILE)
        self.write(self.p_path, OLD_P)
        self.assertEqual(dr.status("personality"), "no_default")
        self.assertEqual(dr.refresh_untouched(), [])
        self.assertEqual(self.read(self.p_path), OLD_P)
        with self.assertRaises(ValueError):
            dr.use_new_default("personality")


class RegisteredDefaultsTests(unittest.TestCase):
    def setUp(self):
        _current_modules()

    def test_current_defaults_are_registered(self):
        with open(ROOT / "data" / "personality.default.txt", "r", encoding="utf-8") as f:
            personality = f.read()
        self.assertIn(dr.fingerprint(personality), dr.OLD_DEFAULTS["personality"],
                      "add the new personality default's fingerprint to OLD_DEFAULTS")
        self.assertIn(dr.fingerprint(ja_voice.DEFAULT_VOICE_BLOCK), dr.OLD_DEFAULTS["voice"],
                      "add the new voice default's fingerprint to OLD_DEFAULTS")

    def test_default_copy_is_the_shipped_personality(self):
        with open(ROOT / "data" / "personality.default.txt", "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("SITUATION line in your notes", text)
        self.assertIn("CHARACTER_BOOK", text)


class RouteTests(_Temp):
    def setUp(self):
        importlib.import_module("api")
        super().setUp()  # now api is loaded, so its copy is patched too
        self.client = sys.modules["api"].application.test_client()

    def test_notice_and_answers(self):
        self.write(self.p_path, "全部自分で書いた。")
        notices = self.client.get("/defaults/notices").get_json()["notices"]
        self.assertEqual(notices, [{"name": "personality", "label": "personality"}])
        bad = self.client.post("/defaults/answer", json={"name": "api_key", "choice": "use_new"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/defaults/answer", json={"name": "personality", "choice": "use_new"})
        self.assertTrue(ok.get_json()["backup"].startswith("personality.txt.bak_"))
        self.assertEqual(self.client.get("/defaults/notices").get_json()["notices"], [])


if __name__ == "__main__":
    unittest.main()

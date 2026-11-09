# -*- coding: utf-8 -*-
"""The built web page (2026-11-09).

The launcher serves a built page (2-4x quicker than Vite's developer server)
and builds it when the page's code changed. A failed build never leaves a
half page: the old one stays, or the developer server takes over. A file
(or env var) keeps the developer server for working on the page.
"""
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_LAUNCHER = Path(__file__).resolve().parents[2] / "scripts" / "launcher.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_page", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PageTests(unittest.TestCase):
    def setUp(self):
        self.l = _load_launcher()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.front = root / "frontend"
        (self.front / "src").mkdir(parents=True)
        (self.front / "src" / "App.tsx").write_text("one", encoding="utf-8")
        (self.front / "index.html").write_text("<html>", encoding="utf-8")
        self.runtime = root / ".runtime"
        for name, value in {
            "FRONTEND_DIR": self.front, "RUNTIME_DIR": self.runtime,
            "PAGE_DIST": self.front / "dist", "PAGE_STAMP": self.runtime / "page_build.txt",
            "PAGE_MODE_FILE": self.runtime / "page_mode.txt",
            "say": lambda *a, **k: None, "note": lambda *a, **k: None,
        }.items():
            p = patch.object(self.l, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"AMADEUS_PAGE_DEV": ""})
        env.start()
        self.addCleanup(env.stop)
        self.builds = []

    def fake_build(self, ok=True, html=True):
        def run_tool(label, command, cwd=None, passthrough=False, extra_env=None):
            self.builds.append((command, extra_env))
            out = Path(command[command.index("--outDir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            if html:
                (out / "index.html").write_text("built", encoding="utf-8")
            return 0 if ok else 1
        return patch.object(self.l, "run_tool", run_tool)

    # -- developer server switch -------------------------------------------
    def test_developer_server_by_file_or_env(self):
        self.assertFalse(self.l.page_dev_mode())
        self.runtime.mkdir()
        (self.runtime / "page_mode.txt").write_text("dev\n", encoding="utf-8")
        self.assertTrue(self.l.page_dev_mode())
        (self.runtime / "page_mode.txt").write_text("built", encoding="utf-8")
        self.assertFalse(self.l.page_dev_mode())
        with patch.dict(os.environ, {"AMADEUS_PAGE_DEV": "1"}):
            self.assertTrue(self.l.page_dev_mode())

    def test_developer_server_means_no_build(self):
        with patch.dict(os.environ, {"AMADEUS_PAGE_DEV": "1"}), self.fake_build():
            self.assertFalse(self.l.prepare_page("npm"))
        self.assertEqual(self.builds, [])

    # -- what counts as "the page changed" ---------------------------------
    def test_fingerprint_follows_the_page_code_only(self):
        first = self.l.page_fingerprint()
        self.assertEqual(first, self.l.page_fingerprint())
        (self.front / "node_modules").mkdir()
        (self.front / "node_modules" / "x.js").write_text("x", encoding="utf-8")
        (self.front / "dist").mkdir()
        (self.front / "dist" / "index.html").write_text("x", encoding="utf-8")
        self.assertEqual(first, self.l.page_fingerprint(), "tools and the build itself do not count")
        time.sleep(0.01)
        (self.front / "src" / "App.tsx").write_text("two", encoding="utf-8")
        self.assertNotEqual(first, self.l.page_fingerprint())

    # -- building ------------------------------------------------------------
    def test_first_start_builds_then_reuses(self):
        with self.fake_build():
            self.assertTrue(self.l.prepare_page("npm"))
            self.assertTrue(self.l.prepare_page("npm"))
        self.assertEqual(len(self.builds), 1, "built once, then reused")
        command, env = self.builds[0]
        self.assertEqual(command[:3], ["npm", "run", "build:page"])
        self.assertEqual(env["TMP"], str(self.runtime / "page-tmp"), "its own temp folder")
        self.assertEqual((self.front / "dist" / "index.html").read_text(encoding="utf-8"), "built")
        self.assertFalse((self.front / "dist.new").exists())

    def test_changed_code_builds_again(self):
        with self.fake_build():
            self.l.prepare_page("npm")
            time.sleep(0.01)
            (self.front / "src" / "App.tsx").write_text("changed", encoding="utf-8")
            self.assertTrue(self.l.prepare_page("npm"))
        self.assertEqual(len(self.builds), 2)

    def test_failed_build_keeps_the_old_page_and_falls_back(self):
        (self.front / "dist").mkdir()
        (self.front / "dist" / "index.html").write_text("old", encoding="utf-8")
        with self.fake_build(ok=False):
            self.assertFalse(self.l.prepare_page("npm"), "developer server this time")
        self.assertEqual((self.front / "dist" / "index.html").read_text(encoding="utf-8"), "old")
        self.assertFalse((self.front / "dist.new").exists(), "no half-built page left over")
        self.assertFalse((self.runtime / "page_build.txt").exists(), "tried again next start")

    def test_build_without_a_page_counts_as_failed(self):
        with self.fake_build(ok=True, html=False):
            self.assertFalse(self.l.prepare_page("npm"))
        self.assertFalse((self.front / "dist").exists())

    # -- serving -------------------------------------------------------------
    def test_commands(self):
        built = self.l.page_command("npm", True)
        self.assertEqual(built[:3], ["npm", "run", "preview"])
        self.assertIn("--strictPort", built)
        self.assertEqual(built[built.index("--port") + 1], str(self.l.FRONTEND_PORT))
        self.assertEqual(built[built.index("--host") + 1], "127.0.0.1")
        self.assertEqual(self.l.page_command("npm", False),
                         ["npm", "run", "dev", "--", "--host", "127.0.0.1"], "unchanged developer server")


class PageConfigTests(unittest.TestCase):
    def test_phone_address_allowed_for_the_built_page(self):
        config = (_LAUNCHER.parents[1] / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
        preview = config[config.index("preview:"):]
        self.assertIn('allowedHosts: [".ts.net"]', preview[:200])

    def test_build_script_skips_the_type_check(self):
        import json
        scripts = json.loads((_LAUNCHER.parents[1] / "frontend" / "package.json").read_text(encoding="utf-8"))["scripts"]
        self.assertEqual(scripts["build:page"], "vite build")


if __name__ == "__main__":
    unittest.main()

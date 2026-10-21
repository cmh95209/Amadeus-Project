# -*- coding: utf-8 -*-
"""Offline checks for the processor-voice speed-ups (Stage 3 A, MH 2026-10-21):
  1. the launcher gives a processor voice one thread per real core and the
     backend streaming mode 2; a card voice gets nothing new;
  2. the core count falls back sensibly without psutil;
  3. the warm-up speaks one throw-away line once the voice engine is up,
     and never fails loudly.
"""
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND.parent / "scripts"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import tts  # noqa: E402


def _launcher():
    spec = importlib.util.spec_from_file_location("amadeus_launcher_voice_test", SCRIPTS / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.l = _launcher()

    def test_processor_voice_gets_threads_and_mode_2(self):
        with patch.object(self.l, "physical_cores", return_value=8):
            voice, backend = self.l.voice_speed_env({"voice_device": "cpu"})
        self.assertEqual((voice["OMP_NUM_THREADS"], voice["MKL_NUM_THREADS"], voice["AMADEUS_VOICE_THREADS"]),
                         ("8", "8", "8"))
        self.assertEqual(backend, {"GPTSOVITS_STREAMING_MODE": "2"})

    def test_card_voice_unchanged(self):
        with patch.object(self.l.shutil, "which", return_value=r"C:\nvidia-smi.exe"):
            self.assertEqual(self.l.voice_speed_env({"voice_device": "cuda"}), ({}, {}))
            self.assertEqual(self.l.voice_speed_env(None), ({}, {}))

    def test_no_nvidia_means_processor_voice(self):
        with patch.object(self.l.shutil, "which", return_value=None):
            self.assertTrue(self.l.voice_on_processor(None))

    def test_core_count_fallback(self):
        with patch.dict(sys.modules, {"psutil": None}), patch.object(self.l.os, "cpu_count", return_value=16):
            self.assertEqual(self.l.physical_cores(), 8)
        with patch.dict(sys.modules, {"psutil": None}), patch.object(self.l.os, "cpu_count", return_value=2):
            self.assertEqual(self.l.physical_cores(), 2)


class WarmUpTests(unittest.TestCase):
    def test_speaks_one_line_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            seen = []

            def fake_render(text, path):
                seen.append(text)
                Path(path).write_bytes(b"RIFF")
                return True

            with patch.object(tts, "OUT_WAV", Path(d) / "generated.wav"), \
                 patch.object(tts.requests, "get", return_value=None), \
                 patch.object(tts, "renderVoiceToPath", side_effect=fake_render), \
                 redirect_stdout(io.StringIO()) as out:
                tts.warm_up(wait_s=5)
            self.assertEqual(seen, [tts.WARM_UP_LINE])
            self.assertFalse((Path(d) / "warm_up.wav").exists())
            self.assertIn("voice warm-up took", out.getvalue())

    def test_engine_never_up_is_quiet(self):
        with patch.object(tts.requests, "get", side_effect=tts.requests.RequestException("down")), \
             patch.object(tts, "renderVoiceToPath") as render, \
             patch("time.sleep", lambda s: None):
            tts.warm_up(wait_s=0.01)
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()

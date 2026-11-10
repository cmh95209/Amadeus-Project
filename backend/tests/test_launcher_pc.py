# -*- coding: utf-8 -*-
"""The launcher's PC block (2026-11-10): a few lines in logs/launcher.log
say exactly what the PC this install runs on - OS, CPU, memory, graphics
card (with driver and the CUDA version the driver can run) and the
built-in llama.cpp. The probes are best effort with short timeouts and
must never stop the start; these tests mock every probe (plus one real
smoke run) and check the install-log copy the launcher keeps in logs/.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND.parent / "scripts"
for _p in (BACKEND, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load_launcher():
    spec = importlib.util.spec_from_file_location("launcher_under_test",
                                                  SCRIPTS / "launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PcBlockTests(unittest.TestCase):
    """The PC block with every probe mocked: the lines are complete and in
    the right shape, no matter what the probes return (even 'unknown')."""

    def setUp(self):
        self.L = _load_launcher()

    def _lines(self, os_, cpu, ram, gpu, llama):
        with patch.object(self.L, "_pc_os", return_value=os_), \
                patch.object(self.L, "_pc_cpu", return_value=cpu), \
                patch.object(self.L, "_pc_ram_gb", return_value=ram), \
                patch.object(self.L, "_pc_graphics", return_value=gpu), \
                patch.object(self.L, "_pc_llama_cpp", return_value=llama):
            return self.L.pc_block_lines()

    def test_full_values(self):
        lines = self._lines("Windows 11 10.0.26200", "AMD Ryzen 7 9800X3D", 32,
                            "NVIDIA GeForce RTX 2060, 6 GB, driver 581.15, CUDA 13.1",
                            "b11400 (cuda-12.4)")
        self.assertEqual(lines[0], "  PC")
        self.assertEqual(lines[1], "    OS        : Windows 11 10.0.26200")
        self.assertEqual(lines[2], "    CPU       : AMD Ryzen 7 9800X3D")
        self.assertEqual(lines[3], "    Memory    : 32 GB")
        self.assertEqual(lines[4], "    Graphics  : NVIDIA GeForce RTX 2060, 6 GB, driver 581.15, CUDA 13.1")
        self.assertEqual(lines[5], "    llama.cpp : b11400 (cuda-12.4)")
        self.assertEqual(len(lines), 6)

    def test_unknown_values_still_fill_every_line(self):
        lines = self._lines("unknown", "unknown", None, "unknown", "not installed")
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[1], "    OS        : unknown")
        self.assertEqual(lines[2], "    CPU       : unknown")
        self.assertEqual(lines[3], "    Memory    : unknown")
        self.assertEqual(lines[4], "    Graphics  : unknown")
        self.assertEqual(lines[5], "    llama.cpp : not installed")


class PcProbeSmokeTests(unittest.TestCase):
    """The REAL probes, run once on this PC: they must never raise, no
    matter what the PC has (nvidia-smi present or not, ...)."""

    def setUp(self):
        self.L = _load_launcher()

    def test_probes_never_raise(self):
        started = time.monotonic()
        self.assertIsInstance(self.L._pc_os(), str)
        self.assertIsInstance(self.L._pc_cpu(), str)
        self.assertTrue(self.L._pc_ram_gb() is None
                        or isinstance(self.L._pc_ram_gb(), int))
        self.assertIsInstance(self.L._pc_graphics(), str)
        self.assertIsInstance(self.L._pc_llama_cpp(), str)
        self.assertLess(time.monotonic() - started, 30)

    def test_pc_block_lines(self):
        lines = self.L.pc_block_lines()
        self.assertEqual(lines[0], "  PC")
        self.assertEqual(len(lines), 6)


class RefreshInstallLogTests(unittest.TestCase):
    """logs/install_log.txt stays a copy of the installer's own log (the
    one in the install folder, the project folder's parent)."""

    def setUp(self):
        self.L = _load_launcher()
        self.root = Path(tempfile.mkdtemp(prefix="amadeus_pc_logs_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.install_root = self.root.parent / (self.root.name + "-install")
        self.install_root.mkdir(parents=True)
        self.project = self.install_root / "Amadeus-Project"
        self.project.mkdir()
        (self.project / "logs").mkdir()
        self.src = self.install_root / "install_log.txt"
        self.src.write_text("install log", encoding="utf-8")
        # Point the module at this fake install (PROJECT_ROOT = project folder).
        p = patch.object(self.L, "PROJECT_ROOT", self.project)
        p.start()
        self.addCleanup(p.stop)
        self.notes = []
        pn = patch.object(self.L, "note", side_effect=self.notes.append)
        pn.start()
        self.addCleanup(pn.stop)

    def test_missing_copy_is_copied_in(self):
        self.assertFalse((self.project / "logs" / "install_log.txt").exists())
        self.L.refresh_install_log()
        copy = self.project / "logs" / "install_log.txt"
        self.assertTrue(copy.is_file())
        self.assertEqual(copy.read_text(encoding="utf-8"), "install log")
        self.assertTrue(any("install_log.txt" in n for n in self.notes))

    def test_newer_installer_log_replaces_the_copy(self):
        copy = self.project / "logs" / "install_log.txt"
        copy.write_text("old copy", encoding="utf-8")
        time.sleep(0.05)
        self.src.write_text("new install log", encoding="utf-8")
        self.L.refresh_install_log()
        self.assertEqual(copy.read_text(encoding="utf-8"), "new install log")

    def test_older_installer_log_keeps_the_copy(self):
        copy = self.project / "logs" / "install_log.txt"
        copy.write_text("kept copy", encoding="utf-8")
        time.sleep(0.05)
        self.src.write_text("newer-looking text", encoding="utf-8")
        os.utime(self.src, (time.time() - 3600, time.time() - 3600))   # pretend the installer ran a day ago
        self.L.refresh_install_log()
        self.assertEqual(copy.read_text(encoding="utf-8"), "kept copy")

    def test_no_installer_log_does_nothing(self):
        self.src.unlink()
        self.L.refresh_install_log()
        self.assertFalse((self.project / "logs" / "install_log.txt").exists())
        self.assertEqual(self.notes, [])


if __name__ == "__main__":
    unittest.main()

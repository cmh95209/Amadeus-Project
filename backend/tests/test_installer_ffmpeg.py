# -*- coding: utf-8 -*-
"""The Windows installer's FFmpeg step: videos need ffmpeg AND ffprobe.

2026-11-06: an ffmpeg that another program had put on the PATH (without its
ffprobe) counted as "FFmpeg already present", and videos then failed. The
real block is taken out of the installer and run with stand-ins for winget
and the download (fake, empty .exe files - only their names are looked up).
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_WIN = Path(__file__).resolve().parents[2] / "scripts" / "install_windows.ps1"
_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def _ffmpeg_block() -> str:
    text = _WIN.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("    # FFmpeg (her voice engine reads audio with it")
    end = text.index("    # Speech model for videos", start)
    return text[start:end]


@unittest.skipUnless(_POWERSHELL, "Windows PowerShell needed")
class FfmpegStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_ffmpeg_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.other = self.tmp / "other-program"     # some program's own ffmpeg
        self.other.mkdir()
        self.tools = self.tmp / "tools" / "ffmpeg"

    def run_step(self, on_path):
        for name in on_path:
            (self.other / name).write_bytes(b"")
        script = self.tmp / "run.ps1"
        script.write_text(
            "function Log($m, $c) { Write-Output (\"LOG \" + $m) }\n"
            "function Install-Pkg($n, $id) { Write-Output \"WINGET $id\" }\n"
            # The portable zip holds both programs.
            "function Install-PortableZip($label, $url, $b, $sha, $inner, $dest) {\n"
            "  Write-Output \"PORTABLE $label\"\n"
            "  New-Item -ItemType Directory -Force -Path (Join-Path $dest 'bin') | Out-Null\n"
            "  foreach ($n in 'ffmpeg.exe', 'ffprobe.exe') { Set-Content -Path (Join-Path $dest ('bin\\' + $n)) -Value '' }\n"
            "}\n"
            "function Refresh-Path { $env:PATH = (Join-Path $FfmpegDir 'bin') + ';' + $env:PATH }\n"
            f"$env:PATH = '{self.other}' + ';' + $env:SystemRoot + '\\System32'\n"
            f"$FfmpegDir = '{self.tools}'\n"
            + _ffmpeg_block() + "Write-Output 'RESULT=OK'\n",
            encoding="utf-8-sig")
        out = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                             capture_output=True, text=True)
        return out.stdout + out.stderr

    def test_both_present_installs_nothing(self):
        out = self.run_step(["ffmpeg.exe", "ffprobe.exe"])
        self.assertIn("FFmpeg already present", out)
        self.assertNotIn("WINGET", out)
        self.assertNotIn("PORTABLE", out)

    def test_ffmpeg_without_ffprobe_gets_her_own_copy(self):
        out = self.run_step(["ffmpeg.exe"])
        self.assertIn("RESULT=OK", out, out)
        self.assertNotIn("already present", out)
        self.assertNotIn("WINGET", out)          # winget would add a second ffmpeg to the PATH
        self.assertIn("PORTABLE", out)
        self.assertIn("FFmpeg ready", out)
        self.assertTrue((self.tools / "bin" / "ffprobe.exe").is_file())

    def test_none_tries_winget_then_the_portable_copy(self):
        out = self.run_step([])
        self.assertIn("RESULT=OK", out, out)
        self.assertIn("WINGET Gyan.FFmpeg", out)
        self.assertIn("PORTABLE", out)
        self.assertIn("FFmpeg ready", out)
        self.assertNotIn("WARNING", out)


if __name__ == "__main__":
    unittest.main()

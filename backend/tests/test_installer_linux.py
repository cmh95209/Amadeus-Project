# -*- coding: utf-8 -*-
"""The Linux installer (scripts/install_linux.sh, 2026-11-08), run for real.

Run by bash (Git Bash on Windows, the system bash elsewhere). Nothing is
installed and nothing leaves this PC:
  1. the whole script is valid bash, never runs sudo, and stops on anything
     that is not Linux;
  2. the A/B screens (--llm-screens-only, pretend cards, a temp folder):
     NVIDIA -> CUDA build + voice on the card, AMD -> Vulkan + voice on the
     processor, an NVIDIA card without its driver / too little memory /
     no card -> option B, re-runs, what the user changed survives;
  3. llama.cpp's Linux download: the two NVIDIA archives unpacked into ONE
     folder, the start test, the "does it see the card" test, the fallback
     to the Vulkan build;
  4. the app-menu entries;
  5. the parts that must stay the same as the Mac installer (update step,
     new defaults, voice engine pin, speech model) and what the launcher
     needs on Linux (start scripts, the voice toolbox's spelling).
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parents[1]
_SCRIPTS = _ROOT / "scripts"
_LINUX = _SCRIPTS / "install_linux.sh"
_MAC = _SCRIPTS / "install_macos.sh"

sys.path.insert(0, str(_TESTS))
from test_installer_macos_llm import _BASH, _Files, _posix, _sha  # noqa: E402


def _text(path=_LINUX) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _block(name, path=_LINUX) -> str:
    text = _text(path)
    start = text.index("# >>> " + name)
    return text[start:text.index("# <<< " + name, start)]


def _code_only(text: str) -> str:
    """Code lines without comments or blank lines (to compare logic)."""
    return "\n".join(line.rstrip() for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))


def _load_tiers():
    spec = importlib.util.spec_from_file_location("amadeus_llm_tiers_linux", _SCRIPTS / "llm_tiers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptTextTests(unittest.TestCase):
    def test_no_sudo_anywhere(self):
        # MH's pick 2a (2026-11-08): everything in the home folder, no admin
        # password, no system package manager (messages may still NAME them).
        for line in _code_only(_text()).splitlines():
            code = re.sub(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'", '""', line)   # quoted text out
            with self.subTest(line=line):
                self.assertNotRegex(code, r"\b(sudo|pkexec)\b")
                self.assertNotRegex(code, r"\b(apt|apt-get|dnf|yum|pacman|zypper)\b")

    def test_blocks_are_marked(self):
        block = _block("BUILT-IN MODEL FUNCTIONS")
        for name in ("pc_choose", "detect_card", "get_verified_file", "install_llama_build",
                     "install_llama_with_fallback", "write_local_llm_config", "install_local_llm"):
            self.assertIn(name + "()", block)
        self.assertIn("write_menu_entries()", _block("APP MENU FUNCTIONS"))

    def test_voice_toolbox_has_the_launchers_spelling(self):
        # Linux tells upper and lower case apart: the launcher starts
        # "conda run -n GPTSoVITS".
        self.assertIn('VOICE_ENV="GPTSoVITS"', _text())
        self.assertNotIn('"GPTSoVits"', _code_only(_text()))

    def test_named_pytorch_builds(self):
        # A plain "pip install torch" on Linux fetches the 3 GB CUDA build.
        text = _text()
        self.assertIn('--index-url "https://download.pytorch.org/whl/$1"', text)
        self.assertIn('torch_index="cu128"', text)
        self.assertIn('torch_index="cpu"', text)
        self.assertNotRegex(_code_only(text), r"install torch torchvision torchaudio\s*(2>|\|)")

    def test_same_pins_as_the_mac_installer(self):
        mac, lin = _text(_MAC), _text()
        for var in ("GPT_SOVITS_COMMIT", "SPEECH_REV", "NODE_VER", "LID_URL", "LID_BYTES",
                    "HF_REPO_URL", "GPT_REPO_URL", "REPO_URL", "NLTK_RESOURCES", "HF_MARKER"):
            with self.subTest(var=var):
                pat = r"^\s*" + var + r"=(\S+)"
                self.assertEqual(re.search(pat, lin, re.M).group(1), re.search(pat, mac, re.M).group(1))
        for spec in re.findall(r'"((?:config\.json|model\.bin|tokenizer\.json|vocabulary\.txt) \d+ [0-9a-f]{64})"', mac):
            self.assertIn(spec, lin)

    def test_same_app_packages_as_the_mac_installer(self):
        pat = r'pip_in_env "amadeus" install (Flask[^\\\n]*)'
        self.assertEqual(re.search(pat, _text()).group(1), re.search(pat, _text(_MAC)).group(1))

    def test_update_step_is_the_macs(self):
        def update(text):
            start = text.index('if [ -d "$PROJ/.git" ]; then\n    log "  Found an existing Amadeus')
            end = text.index('    log "  Updated to the latest version on the $BRANCH branch."', start)
            return _code_only(text[start:end])
        self.assertEqual(update(_text()), update(_text(_MAC)))

    def test_new_defaults_are_the_macs(self):
        self.assertEqual(_code_only(_block("NEW DEFAULTS FUNCTIONS")),
                         _code_only(_block("NEW DEFAULTS FUNCTIONS", _MAC)))

    def test_git_lfs_is_switched_on_from_the_home_folder(self):
        # 2026-11-08 WSL test: started inside another git folder, "git lfs
        # install" failed silently and the voice models came as placeholders.
        text = _text()
        self.assertIn('( cd "$HOME" && git lfs install --skip-repo )', text)
        self.assertIn('cd "$HOME" && git config --global --get filter.lfs.process', text)
        # ... and the whole installer works from the home folder.
        top = text[:text.index("# >>> BUILT-IN MODEL FUNCTIONS")]
        self.assertIn('cd "$HOME" 2>/dev/null || cd /', top)

    def test_relative_install_folder_is_made_absolute(self):
        if not _BASH:
            self.skipTest("bash not available")
        tmp = Path(tempfile.mkdtemp(prefix="amadeus_linux_rel_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        script = tmp / "install_linux.sh"
        script.write_bytes(_text().encode("utf-8"))
        out = subprocess.run([_BASH, str(script), "rel-folder", "--llm-screens-only", "--simulate-gpu", "none"],
                             cwd=tmp, input="B\n", capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue((tmp / "rel-folder" / "models" / "local_llm_choice.json").is_file())

    def test_ffmpeg_lands_where_the_app_looks(self):
        text = _text()
        block = text[text.index("# --- 2d. FFmpeg"):text.index("# --- 2e.")]
        self.assertIn('FFMPEG_BIN="$TOOLS_DIR/ffmpeg/bin"', block)
        self.assertIn("-c conda-forge --override-channels", block)
        self.assertNotIn("die ", block)

    def test_start_scripts(self):
        for name in ("start_linux.sh", "start_linux_no_model.sh"):
            with self.subTest(script=name):
                raw = (_ROOT / name).read_bytes()
                self.assertTrue(raw.startswith(b"#!/usr/bin/env bash\n"))
        main = (_ROOT / "start_linux.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/launcher.py", main)
        self.assertIn(".amadeus-tools/node/bin", main)
        self.assertIn("logs", main)
        self.assertNotIn(".runtime/logs", main)
        self.assertIn("--no-model", (_ROOT / "start_linux_no_model.sh").read_text(encoding="utf-8"))

    def test_start_scripts_are_executable_in_git(self):
        git = shutil.which("git")
        if not git or not (_ROOT / ".git").exists():
            self.skipTest("not a git checkout")
        out = subprocess.run([git, "ls-files", "-s", "start_linux.sh", "start_linux_no_model.sh",
                              "scripts/install_linux.sh"], cwd=_ROOT, capture_output=True, text=True).stdout
        modes = {line.split()[-1]: line.split()[0] for line in out.splitlines()}
        if not modes:
            self.skipTest("not committed yet")
        for name in ("start_linux.sh", "start_linux_no_model.sh"):
            self.assertEqual(modes.get(name), "100755", name)


@unittest.skipUnless(_BASH, "bash not available")
class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_linux_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = self.tmp / "install_linux.sh"
        self.script.write_bytes(_text().encode("utf-8"))
        self.inst = self.tmp / "inst"


class SyntaxTests(_Base):
    def test_whole_script_is_valid_bash(self):
        for name in ("install_linux.sh",):
            out = subprocess.run([_BASH, "-n", str(self.script)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, name + out.stderr)
        for name in ("start_linux.sh", "start_linux_no_model.sh"):
            copy = self.tmp / name
            copy.write_bytes((_ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
            out = subprocess.run([_BASH, "-n", str(copy)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, name + out.stderr)


class ScreenTests(_Base):
    def screens(self, answers, gpu="nvidia", vram=16, ram=32, driver="610.88", extra=()):
        args = [_BASH, str(self.script), _posix(self.inst), "--llm-screens-only",
                "--simulate-gpu", gpu, "--simulate-vram-gb", str(vram), "--simulate-ram-gb", str(ram)]
        if driver:
            args += ["--simulate-driver", driver]
        return subprocess.run(args + list(extra), input="\n".join(answers) + "\n",
                              capture_output=True, text=True, timeout=120)

    def choice(self):
        return json.loads((self.inst / "models" / "local_llm_choice.json").read_text(encoding="utf-8"))

    def settings_path(self):
        return self.inst / "Amadeus-Project" / "backend" / "data" / "local_llm.json"

    def settings(self):
        return json.loads(self.settings_path().read_text(encoding="utf-8"))

    def test_nvidia_card_gets_cuda_and_voice_on_the_card(self):
        out = self.screens(["A", "Y"], vram=16)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("STEP 0: How should Amadeus think?", out.stdout)
        c = self.choice()
        self.assertEqual((c["mode"], c["model"], c["tier"], c["build"], c["voice_device"]),
                         ("builtin", "12B", "16-23", "cuda-13.4", "cuda"))
        s = self.settings()
        self.assertEqual((s["mode"], s["model"], s["tier"], s["context"], s["kv_cache"], s["voice_device"]),
                         ("builtin", "12B", "16-23", 32768, "f16", "cuda"))
        self.assertEqual(s["model_label"], "Gemma 4 12B (QAT)")
        self.assertEqual((s["port"], s["gpu_layers"], s["build"]), (8091, 999, "cuda-13.4"))
        self.assertFalse(s["model_ok"])
        self.assertFalse(s["server_ok"])
        self.assertTrue(s["simulated"])
        self.assertEqual(s["installer"], "linux-v1.0")
        self.assertIn("(answer: Y)", (self.inst / "install_linux.log").read_text(encoding="utf-8"))

    def test_older_nvidia_driver_gets_the_older_cuda_build(self):
        self.screens(["A", "Y"], vram=12, driver="575.64")
        self.assertEqual(self.settings()["build"], "cuda-12.8")
        self.screens(["N", "A", "Y"], vram=12, driver="550.120")
        self.assertEqual(self.settings()["build"], "vulkan")
        self.assertEqual(self.settings()["voice_device"], "cuda")   # the voice still uses the card

    def test_amd_card_gets_vulkan_and_voice_on_the_processor(self):
        out = self.screens(["A", "Y"], gpu="amd", vram=16, driver="")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        s = self.settings()
        self.assertEqual((s["build"], s["voice_device"], s["model"]), ("vulkan", "cpu", "12B"))

    def test_8gb_card_gets_the_3bit_model(self):
        self.screens(["A", "Y"], vram=8)
        s = self.settings()
        self.assertEqual((s["model"], s["tier"], s["voice_device"]), ("12B-Q3", "8-11", "cpu"))
        self.assertEqual(s["model_label"], "Gemma 4 12B (3-bit, for 8 GB cards)")

    def test_6gb_card_shows_the_notice(self):
        out = self.screens(["A", "Y"], vram=6)
        self.assertIn("NOTICE: this graphics card has only 6 GB", out.stdout)
        self.assertEqual(self.settings()["tier"], "6-7")

    def test_nvidia_without_its_driver_gets_option_b_and_a_plain_reason(self):
        out = self.screens(["A", ""], gpu="nvidia-nodriver", vram=0, driver="")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("NVIDIA's own driver is not installed", out.stdout)
        self.assertIn("run this installer again", out.stdout)
        self.assertEqual(self.choice()["mode"], "custom")
        self.assertFalse(self.settings_path().exists())

    def test_no_card_gets_option_b(self):
        out = self.screens(["A", ""], gpu="none", vram=0, driver="")
        self.assertIn("This PC has none", out.stdout)
        self.assertEqual(self.choice()["mode"], "custom")

    def test_amd_with_little_memory_gets_option_b(self):
        out = self.screens(["A", ""], gpu="amd", vram=16, ram=8, driver="")
        self.assertIn("at least 16 GB of normal memory (this PC has 8 GB)", out.stdout)
        self.assertEqual(self.choice()["mode"], "custom")

    def test_rerun_keeps_choice_or_switches_to_b(self):
        self.screens(["A", "Y"], vram=24)
        out = self.screens(["Y"], vram=24)
        self.assertIn("Last time you chose: A)", out.stdout)
        self.assertEqual(self.choice()["mode"], "builtin")
        self.screens(["N", "B"], vram=24)
        self.assertEqual(self.choice()["mode"], "custom")
        s = self.settings()   # llama.cpp stays ready for a GGUF of the user's own
        self.assertEqual((s["mode"], s["model"], s["tier"], s["build"]), ("custom", "12B", "24-plus", "cuda-13.4"))

    def test_kept_builtin_choice_keeps_its_voice_device(self):
        self.screens(["A", "Y"], vram=12)
        self.screens(["Y"], gpu="none", vram=0, driver="")   # the card is gone: the old choice stays
        c = self.choice()
        self.assertEqual((c["model"], c["voice_device"]), ("12B", "cuda"))
        self.assertEqual(self.settings()["voice_device"], "cuda")

    def test_what_the_user_changed_in_the_app_survives(self):
        self.screens(["A", "Y"], vram=24)
        own = self.inst / "models" / "my model's v2 (copy).gguf"
        own.write_bytes(b"x")
        s = self.settings()
        s.update({"selected_file": _posix(own), "history_budget": 9000})
        self.settings_path().write_text(json.dumps(s, indent=4, ensure_ascii=False), encoding="utf-8")
        self.screens(["Y"], vram=24)
        s = self.settings()
        self.assertEqual((s["selected_file"], s["history_budget"]), (_posix(own), 9000))

    def test_only_runs_on_linux(self):
        if sys.platform.startswith("linux"):
            self.skipTest("this is Linux")
        out = subprocess.run([_BASH, str(self.script), _posix(self.inst)],
                             input="", capture_output=True, text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("This installer is for Linux", out.stdout)
        self.assertFalse((self.inst / "models").exists())

    def test_bad_test_options_are_refused(self):
        for args in (["--simulate-gpu", "radeon"], ["--simulate-vram-gb", "lots"],
                     ["--simulate-ram-gb", "-5"], ["--simulate-driver", "x1"],
                     ["--simulate-driver", "5;rm"], ["--branch", "x;y"], ["--nonsense"]):
            with self.subTest(args=args):
                out = subprocess.run([_BASH, str(self.script), *args, "--hardware-check-only"],
                                     capture_output=True, text=True, timeout=60)
                self.assertEqual(out.returncode, 2, out.stdout)

    def test_hardware_check_installs_nothing(self):
        out = subprocess.run([_BASH, str(self.script), _posix(self.inst), "--hardware-check-only"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Graphics card :", out.stdout)
        self.assertFalse(self.inst.exists())


def _tar(entries) -> bytes:
    """A .tar.gz from {name: (text, mode)}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, (text, mode) in entries.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _server_program(devices="CUDA0: NVIDIA GeForce RTX (stand-in)", version_exit=0, need_lib=""):
    """A stand-in llama-server: --version, --list-devices; with need_lib it
    fails unless that file sits next to it (NVIDIA's runtime files)."""
    check = (f'[ -f "$(dirname "$0")/{need_lib}" ] || {{ echo "error while loading shared libraries: {need_lib}"; exit 127; }}\n'
             if need_lib else "")
    return ("#!/bin/sh\n" + check +
            'case "$1" in\n'
            f"  --version) echo 'version: 11400 (stand-in)'; exit {version_exit} ;;\n"
            f"  --list-devices) printf 'Available devices:\\n  {devices}\\n' ;;\n"
            "esac\n")


class _FunctionBase(_Base):
    """Runs the installer's built-in-model functions with test values."""

    def setUp(self):
        super().setUp()
        (self.tmp / "defs.sh").write_text(_block("BUILT-IN MODEL FUNCTIONS") + _block("APP MENU FUNCTIONS"),
                                          encoding="utf-8")
        self.log = self.tmp / "install.log"

    def run_bash(self, body, extra_setup=""):
        script = "\n".join([
            "set -uo pipefail",
            f"INSTALL_DIR='{_posix(self.inst)}'",
            f"INSTALL_LOG='{_posix(self.log)}'",
            "PROJ=\"$INSTALL_DIR/Amadeus-Project\"",
            "SKIP_LLM_DOWNLOAD=0; INSTALLER_VERSION=v1.0; SIM_GPU=''; SIM_VRAM_GB=-1; SIM_RAM_GB=-1; SIM_DRIVER=''",
            "log() { printf '%s\\n' \"$1\"; printf '%s\\n' \"$1\" >> \"$INSTALL_LOG\"; }",
            "step() { printf 'STEP %s: %s\\n' \"$1\" \"$2\"; }",
            "die() { printf 'DIE %s\\n' \"$1\"; exit 1; }",
            f". '{_posix(self.tmp / 'defs.sh')}'",
            "DOWNLOAD_RETRY_DELAY=0",
            extra_setup,
            body,
        ])
        (self.tmp / "run.sh").write_text(script, encoding="utf-8")
        return subprocess.run([_BASH, str(self.tmp / "run.sh")], capture_output=True, text=True,
                              timeout=180)

    @staticmethod
    def result(out):
        lines = [l for l in out.stdout.splitlines() if l.startswith("RESULT=")]
        return lines[-1] if lines else "NO RESULT: " + out.stdout + out.stderr


class LlamaBuildTests(_FunctionBase):
    """llama.cpp's Linux archives, served by a tiny local web server."""

    def serve(self, builds):
        """builds: {build: (min_driver, {file name: bytes})} -> a LINUX_BUILDS table."""
        files = {name: data for _, named in builds.values() for name, data in named.items()}
        server = _Files(files)
        self.addCleanup(server.close)
        rows = []
        for build, (need, named) in builds.items():
            rows.append(" ".join([build, str(need)] +
                                 [f"{n} {len(d)} {_sha(d)}" for n, d in named.items()]))
        setup = ("LINUX_BUILDS='" + "\n".join(rows) + "'\n"
                 f"llama_url() {{ printf '%s/%s' '{server.base}' \"$1\"; }}")
        return setup

    def cuda_archives(self, **program):
        return {
            "llama-b11400-bin-ubuntu-cuda-13.4-x64.tar.gz": _tar({
                "llama-b11400/llama-server": (_server_program(need_lib="libcudart.so.13", **program), 0o755),
                "llama-b11400/libggml-cuda.so": ("x", 0o755)}),
            "cudart-llama-b11400-bin-ubuntu-cuda-13.4-x64.tar.gz": _tar({
                "cudart-llama-b11400-bin-ubuntu-cuda-13.4-x64/libcudart.so.13": ("x", 0o644),
                "cudart-llama-b11400-bin-ubuntu-cuda-13.4-x64/libcublas.so.13": ("x", 0o644)}),
        }

    def vulkan_archive(self, devices="Vulkan0: AMD Radeon (stand-in)"):
        return {"llama-b11400-bin-ubuntu-vulkan-x64.tar.gz": _tar({
            "llama-b11400/llama-server": (_server_program(devices=devices), 0o755)})}

    def test_nvidia_archives_end_up_in_one_folder(self):
        setup = self.serve({"cuda-13.4": (580, self.cuda_archives())})
        out = self.run_bash("if install_llama_build cuda-13.4; then echo \"RESULT=OK $LLAMA_EXE\"; "
                            "else echo \"RESULT=ERR $VF_ERROR\"; fi", setup)
        result = self.result(out)
        self.assertTrue(result.startswith("RESULT=OK "), result)
        exe = Path(result.split(" ", 1)[1].replace("/c/", "C:/", 1) if os.name == "nt" else result.split(" ", 1)[1])
        folder = self.inst / "models" / "llama.cpp" / "b11400-linux-cuda-13.4"
        self.assertTrue((folder / "llama-b11400" / "libcudart.so.13").is_file())
        self.assertTrue((folder / "llama-b11400" / "libcublas.so.13").is_file())
        self.assertFalse((folder / "cudart-llama-b11400-bin-ubuntu-cuda-13.4-x64").exists())
        self.assertEqual(exe.name, "llama-server")
        self.assertTrue((folder / "install_complete.txt").is_file())
        # The downloads are removed once unpacked; a re-run skips everything.
        self.assertEqual(list((self.inst / "models" / "downloads").glob("*.tar.gz")), [])
        out = self.run_bash("install_llama_build cuda-13.4 && echo RESULT=OK", setup)
        self.assertIn("Already have llama.cpp", out.stdout)

    def test_missing_libgomp_comes_from_conda_forge(self):
        # 2026-11-08 WSL test: a minimal Ubuntu lacks libgomp.so.1, which
        # every llama.cpp Linux build needs.
        archives = {"llama-b11400-bin-ubuntu-vulkan-x64.tar.gz": _tar({
            "llama-b11400/llama-server": (_server_program(devices="Vulkan0: stand-in",
                                                          need_lib="libgomp.so.1"), 0o755)})}
        fake = self.tmp / "fakebin"
        fake.mkdir()
        (fake / "ldd").write_text(
            '#!/bin/sh\n[ -f "$(dirname "$1")/libgomp.so.1" ] && echo "libgomp.so.1 => found" '
            '|| echo "\tlibgomp.so.1 => not found"\n', encoding="utf-8")
        (fake / "conda").write_text(
            '#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = "-p" ] && d="$2"; shift; done\n'
            'mkdir -p "$d/lib" && echo lib > "$d/lib/libgomp.so.1.0.0"\n', encoding="utf-8")
        for f in ("ldd", "conda"):
            (fake / f).chmod(0o755)
        setup = (self.serve({"vulkan": (0, archives)}) +
                 f"\nPATH='{_posix(fake)}':\"$PATH\"\nTOOLS_DIR='{_posix(self.tmp / 'tools')}'\n"
                 f"CONDA='{_posix(fake / 'conda')}'")
        out = self.run_bash("if install_llama_build vulkan; then echo RESULT=OK; "
                            "else echo \"RESULT=ERR $VF_ERROR\"; fi", setup)
        self.assertEqual(self.result(out), "RESULT=OK", out.stdout + out.stderr)
        self.assertIn("adding the helper library llama.cpp needs (libgomp)", out.stdout)
        self.assertTrue((self.inst / "models" / "llama.cpp" / "b11400-linux-vulkan" / "llama-b11400"
                         / "libgomp.so.1").is_file())

    def test_a_build_that_cannot_see_the_card_is_refused(self):
        setup = self.serve({"vulkan": (0, self.vulkan_archive(devices="(none)"))})
        out = self.run_bash("if install_llama_build vulkan; then echo RESULT=OK; "
                            "else echo \"RESULT=ERR $VF_ERROR\"; fi", setup)
        self.assertIn("could not see your graphics card", self.result(out))
        self.assertIn("mesa-vulkan-drivers", self.result(out))

    def test_cuda_failure_falls_back_to_vulkan(self):
        archives = self.cuda_archives(devices="(none)")      # e.g. a driver too old for CUDA 13
        setup = self.serve({"cuda-13.4": (580, archives), "vulkan": (0, self.vulkan_archive())})
        out = self.run_bash("CHOICE_BUILD=cuda-13.4\n"
                            "if install_llama_with_fallback; then echo \"RESULT=OK $CHOICE_BUILD\"; "
                            "else echo \"RESULT=ERR $VF_ERROR\"; fi", setup)
        self.assertEqual(self.result(out), "RESULT=OK vulkan", out.stdout)
        self.assertIn("Trying llama.cpp's Vulkan build instead", out.stdout)

    def test_program_that_will_not_start_is_reported(self):
        archives = {"llama-b11400-bin-ubuntu-vulkan-x64.tar.gz": _tar({
            "llama-b11400/llama-server": (_server_program(version_exit=1), 0o755)})}
        setup = self.serve({"vulkan": (0, archives)})
        out = self.run_bash("CHOICE_BUILD=vulkan\nif install_llama_with_fallback; then echo RESULT=OK; "
                            "else echo \"RESULT=ERR $VF_ERROR\"; fi", setup)
        self.assertIn("would not start on this PC", self.result(out))

    def test_unsafe_archive_is_refused(self):
        archives = {"llama-b11400-bin-ubuntu-vulkan-x64.tar.gz": _tar({
            "llama-b11400/llama-server": (_server_program(), 0o755),
            "../outside.txt": ("x", 0o644)})}
        setup = self.serve({"vulkan": (0, archives)})
        out = self.run_bash("if install_llama_build vulkan; then echo RESULT=OK; "
                            "else echo \"RESULT=ERR $VF_ERROR\"; fi", setup)
        self.assertIn("Unsafe path", self.result(out))
        self.assertFalse((self.inst / "models" / "outside.txt").exists())

    def test_background_list_has_both_nvidia_archives(self):
        setup = self.serve({"cuda-13.4": (580, self.cuda_archives())})
        out = self.run_bash("CHOICE_MODE=custom; CHOICE_MODEL=''; CH_OFFERED=1; CH_BUILD=cuda-13.4\n"
                            "local_llm_files | sed 's/^/FILE=/'", setup)
        files = [l for l in out.stdout.splitlines() if l.startswith("FILE=")]
        self.assertEqual(len(files), 2, out.stdout + out.stderr)
        self.assertTrue(any("cudart-llama" in f for f in files))


class PickBuildTests(_FunctionBase):
    def test_driver_versions(self):
        out = self.run_bash("for d in 610.88 580.10 575.64 570.0 569.9 525.60 ''; do "
                            "printf 'B=%s=%s\\n' \"$d\" \"$(pick_build nvidia \"$d\")\"; done; "
                            "printf 'B=amd=%s\\n' \"$(pick_build amd '')\"")
        got = dict(l[2:].rsplit("=", 1) for l in out.stdout.splitlines() if l.startswith("B="))
        tiers = _load_tiers()
        table = tiers.load_table()
        for driver in ("610.88", "580.10", "575.64", "570.0", "569.9", "525.60", ""):
            self.assertEqual(got[driver], tiers.pick_build(table, "nvidia", driver, table["linux"]["builds"]),
                             driver)
        self.assertEqual(got["amd"], "vulkan")


class MenuTests(_FunctionBase):
    def test_entries_are_written_and_the_extra_one_removed(self):
        apps = self.tmp / "share" / "applications"
        proj = self.tmp / "My Amadeus" / "Amadeus-Project"
        setup = f"XDG_DATA_HOME='{_posix(self.tmp / 'share')}'"
        out = self.run_bash(f"write_menu_entries '{_posix(proj)}' 1 && echo RESULT=OK", setup)
        self.assertEqual(self.result(out), "RESULT=OK", out.stdout + out.stderr)
        main = (apps / "amadeus.desktop").read_text(encoding="utf-8")
        self.assertIn("[Desktop Entry]", main)
        self.assertIn(f'Exec="{_posix(proj)}/start_linux.sh"', main)   # quoted: the folder has a space
        self.assertIn("Terminal=true", main)
        self.assertIn("icon-512.png", main)
        self.assertIn("start_linux_no_model.sh", (apps / "amadeus-no-model.desktop").read_text(encoding="utf-8"))
        self.run_bash(f"write_menu_entries '{_posix(proj)}' 0", setup)
        self.assertTrue((apps / "amadeus.desktop").exists())
        self.assertFalse((apps / "amadeus-no-model.desktop").exists())

    def test_special_characters_are_escaped(self):
        out = self.run_bash("printf 'Q=%s\\n' \"$(desktop_quote '/home/a \"b\" $c')\"")
        self.assertIn('Q="/home/a \\"b\\" \\$c"', out.stdout)


class LauncherVoiceEnvTests(unittest.TestCase):
    """Linux tells upper and lower case apart: the launcher starts the voice
    toolbox under the spelling that really exists."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("amadeus_launcher_linux", _SCRIPTS / "launcher.py")
        self.launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.launcher)
        self.tmp = Path(tempfile.mkdtemp(prefix="amadeus_envs_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launcher.ENV_SUFFIX_FILE = self.tmp / "env_suffix.txt"
        (self.tmp / "conda" / "bin").mkdir(parents=True)
        self.conda = str(self.tmp / "conda" / "bin" / "conda")

    def test_existing_spelling_is_used(self):
        envs = self.tmp / "conda" / "envs"
        (envs / "GPTSoVITS").mkdir(parents=True)
        self.assertEqual(self.launcher.voice_env_name(self.conda), "GPTSoVITS")
        if not (envs / "GPTSoVits").exists():          # a case-sensitive disk (Linux)
            (envs / "GPTSoVITS").rmdir()
            (envs / "GPTSoVits").mkdir()
            self.assertEqual(self.launcher.voice_env_name(self.conda), "GPTSoVits")
            self.assertEqual(self.launcher.gptsovits_command(self.conda)[3], "GPTSoVits")

    def test_nothing_found_keeps_the_usual_name(self):
        self.assertEqual(self.launcher.voice_env_name(self.conda), "GPTSoVITS")
        self.assertEqual(self.launcher.voice_env_name(""), "GPTSoVITS")


if __name__ == "__main__":
    unittest.main()

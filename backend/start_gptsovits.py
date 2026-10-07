from __future__ import annotations
from pathlib import Path
import runpy
import sys
import os


def _install_audio_fallback():
    """Make audio loading robust on Windows.

    GPT-SoVITS reads the reference clip with torchaudio.load(). Newer torchaudio
    (2.11) routes WAV files through 'torchcodec', whose native DLLs often fail to
    load here (missing FFmpeg libraries). We keep the normal path, but if it fails
    we transparently fall back to soundfile - which is already installed and works.
    """
    try:
        import torchaudio
        import soundfile as sf
        import torch
        import numpy as np
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[Amadeus] audio fallback not installed: {exc!r}")
        return

    _orig_load = torchaudio.load

    def _load(path, *args, **kwargs):
        try:
            return _orig_load(path, *args, **kwargs)
        except Exception as exc:
            print(f"[Amadeus] torchaudio.load failed ({exc!r}); using soundfile fallback")
            data, sr = sf.read(str(path), dtype="float32")
            if data.ndim == 1:
                data = data[None, :]        # mono -> (1, samples)
            else:
                data = data.T               # (samples, ch) -> (ch, samples)
            return torch.from_numpy(data).contiguous(), sr

    torchaudio.load = _load


# The voice engine's english text module (g2p_en) reads Western-script
# words - a name the user typed, "AI", ... - using data files that a fresh
# machine does not have (a pronunciation dictionary + grammar patterns).
# Its own automatic downloader only knows the OLD resource names, while
# the newer NLTK a fresh install receives looks up the RENAMED tagger
# files - so the first line containing a Western-script word crashed the
# whole voice request (2026-10-07, fresh-VM launch: her wake-up line came
# out without voice). Providing every known name here is the self-heal for
# EVERY install on next start; the installer additionally pre-downloads
# them, so on most machines this check is a fast no-op.
_NLTK_VOICE_RESOURCES = (
    "averaged_perceptron_tagger_eng",  # NLTK >= 3.9.2 (what fresh installs get)
    "averaged_perceptron_tagger",      # older NLTK, and the name g2p_en itself checks
    "cmudict",                          # the english pronunciation dictionary
)


def _ensure_nltk_resources(gpt_root: Path):
    """Make sure the voice engine's text data for Western-script words exists.

    The newer upstream engine tells NLTK to look in the clone's runtime
    folder first (via the NLTK_DATA env var it sets at startup); point at
    the same folder so anything fetched here lands exactly where the engine
    will look. Best effort - never blocks startup: if the machine is offline
    the engine's own first-use fetch tries again later.
    """
    nltk_dir = gpt_root / "runtime" / "nltk_data"
    try:
        os.environ["NLTK_DATA"] = str(nltk_dir)
        nltk_dir.mkdir(parents=True, exist_ok=True)
        import nltk
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[Amadeus] could not check the voice text data (NLTK missing?): {exc!r}")
        return

    missing = []
    for res in _NLTK_VOICE_RESOURCES:
        try:
            nltk.data.find(res)
        except LookupError:
            missing.append(res)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[Amadeus] could not check voice text data '{res}': {exc!r}")

    if not missing:
        print("[Amadeus] voice text data present (Western-script words can be spoken).")
        return

    # Bound the worst case on flaky/offline networks (nltk's downloader has
    # no timeout of its own).
    import socket
    socket.setdefaulttimeout(30)

    fetched = []
    for res in missing:
        try:
            # Explicit target: the engine's own first search path (see above),
            # so the fetch never lands in a user-profile fallback folder.
            if nltk.download(res, quiet=True, download_dir=str(nltk_dir)):
                fetched.append(res)
            else:
                print(f"[Amadeus] could not fetch voice text data '{res}' right now - the engine will try again the first time she speaks such a word.")
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[Amadeus] could not fetch voice text data '{res}': {exc!r}")

    if fetched:
        print(f"[Amadeus] fetched voice text data: {', '.join(fetched)}")
    if len(fetched) < len(missing):
        print("[Amadeus] some voice text data is still missing (no internet right now?). Pure Japanese lines are unaffected; Western-script words will be retried at first use.")


def main():
    # run_gptsovits.py is inside Amadeus/, so project root is one level up
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    GPT_ROOT = PROJECT_ROOT / "GPT-SoVITS"
    GPT_PKG = GPT_ROOT / "GPT_SoVITS"

    if not GPT_ROOT.exists():
        raise RuntimeError(f"GPT-SoVITS not found at: {GPT_ROOT}")
    if not (GPT_ROOT / "config.py").exists():
        raise RuntimeError(f"config.py not found at: {GPT_ROOT / 'config.py'}")
    if not (GPT_ROOT / "api_v2.py").exists():
        raise RuntimeError(f"api_v2.py not found at: {GPT_ROOT / 'api_v2.py'}")

    # Ensure BOTH import roots are visible:
    # - GPT_ROOT: allows `import config`
    # - GPT_PKG : allows `import text.*` and other package-relative imports
    sys.path.insert(0, str(GPT_ROOT))
    sys.path.insert(0, str(GPT_PKG))

    # The newer upstream's language detector (fast_langdetect, used by
    # split_lang) downloads its language-identification model on first use -
    # but its downloader REFUSES to run if the cache directory is missing,
    # and a fresh GPT-SoVITS clone has no such directory: the first /tts
    # died with 'FileNotFoundError: fast-langdetect: Cache directory not
    # found' (2026-10-07, fresh-VM launch). Creating the directory here is
    # the self-heal for EVERY install (old and new); the installer
    # additionally pre-downloads the model file so the first voice line
    # never waits on a network fetch.
    _fld_cache = GPT_PKG / "pretrained_models" / "fast_langdetect"
    try:
        _fld_cache.mkdir(parents=True, exist_ok=True)
        print(f"[Amadeus] ensured language-model cache dir: {_fld_cache}")
    except OSError as exc:  # pragma: no cover - defensive
        print(f"[Amadeus] could not create language-model cache dir: {exc!r}")

    # Self-heal the english text data (see _ensure_nltk_resources) BEFORE the
    # engine starts, so the first Western-script word in any fresh install
    # can be spoken.
    _ensure_nltk_resources(GPT_ROOT)

    # GPT-SoVITS expects to run from repo root
    os.chdir(GPT_ROOT)

    print("[Amadeus] Starting GPT-SoVITS native API...")
    _install_audio_fallback()

    # Run script
    runpy.run_path(str(GPT_ROOT / "api_v2.py"), run_name="__main__")


if __name__ == "__main__":
    main()

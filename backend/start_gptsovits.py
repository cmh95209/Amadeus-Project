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

    # GPT-SoVITS expects to run from repo root
    os.chdir(GPT_ROOT)

    print("[Amadeus] Starting GPT-SoVITS native API...")
    _install_audio_fallback()

    # Run script
    runpy.run_path(str(GPT_ROOT / "api_v2.py"), run_name="__main__")


if __name__ == "__main__":
    main()

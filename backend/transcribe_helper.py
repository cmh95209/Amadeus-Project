"""Writes down the speech in one audio file (run by transcribe.py).

Runs with her voice engine's Python (it has faster-whisper), NOT the
backend's: argv = model folder, device (cuda / cpu), audio file. Prints one
JSON line: {"language", "segments": [{"start", "end", "text"}]}.
"""
import json
import os
import sys


def main() -> int:
    model_dir, device, audio = sys.argv[1], sys.argv[2], sys.argv[3]
    from faster_whisper import WhisperModel
    model = WhisperModel(model_dir, device=device,
                         compute_type="float16" if device == "cuda" else "int8",
                         cpu_threads=max(1, min(8, os.cpu_count() or 4)))
    segments, info = model.transcribe(audio, vad_filter=True, beam_size=5)
    out = {"language": info.language,
           "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in segments]}
    # ASCII-only JSON: the Windows console code page can't garble it.
    print(json.dumps(out, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

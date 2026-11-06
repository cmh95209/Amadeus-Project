"""Offline streaming checks: python -m unittest discover -s backend/tests."""
import ast
import importlib.util
from itertools import chain
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _engine_header():
    """The header GPT-SoVITS starts a streamed line with: sizes say 0 bytes."""
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(32000); w.writeframes(b'')
    return buf.getvalue()


def _load_tts(requests):
    with patch.dict(sys.modules, {'requests': requests}):
        spec = importlib.util.spec_from_file_location('speech_tts_test', ROOT / 'tts.py')
        tts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tts)
    return tts


FFFF = b'\xff\xff\xff\xff'


class SpeechTests(unittest.TestCase):
    def test_stream_cleanup(self):
        header = _engine_header()
        class Upstream:
            closed = False
            def __enter__(self): return self
            def __exit__(self, *args): self.closed = True
            def raise_for_status(self): pass
            # The header can arrive split, and glued to the first sound.
            def iter_content(self, **kwargs): yield from (header[:20], header[20:] + b'pcm', b'more')
        upstream = Upstream()
        requests = SimpleNamespace(post=lambda *a, **kw: upstream)
        with tempfile.TemporaryDirectory() as directory:
            tts = _load_tts(requests)
            tts.OUT_WAV = Path(directory) / 'generated.wav'
            tts._request_payload = lambda text: {'text': text}
            chunks = tts.streamVoiceChunks('test')
            first = next(chunks)
            # Firefox: the browser gets "length unknown", not "0 bytes".
            self.assertEqual(first[4:8], FFFF)
            self.assertEqual(first[40:44], FFFF)
            self.assertEqual(first[:4] + first[8:40], header[:4] + header[8:40])
            self.assertTrue(first.endswith(b'pcm'))
            chunks.close()
            self.assertTrue(upstream.closed)
            self.assertFalse(tts._speech_lock.locked())
            self.assertFalse(tts.OUT_WAV.with_suffix('.browser.tmp').exists())
            streamed = b''.join(tts.streamVoiceChunks('test'))
            self.assertEqual(streamed[44:], b'pcmmore')
            # The saved copy carries the real sizes (Firefox replays it).
            saved = tts.OUT_WAV.read_bytes()
            self.assertEqual(int.from_bytes(saved[4:8], 'little'), len(saved) - 8)
            self.assertEqual(int.from_bytes(saved[40:44], 'little'), 7)
            self.assertEqual(saved[44:], b'pcmmore')

    def test_non_wav_stream_passes_through(self):
        tts = _load_tts(SimpleNamespace(post=None))
        self.assertEqual(list(tts._browser_stream(iter([b'OggS', b'data']))), [b'OggS', b'data'])
        self.assertEqual(list(tts._browser_stream(iter([b'{"message": "error"}']))), [b'{"message": "error"}'])
        self.assertEqual(list(tts._browser_stream(iter([]))), [])

    def test_fix_wav_sizes(self):
        tts = _load_tts(SimpleNamespace(post=None))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'voice_1.wav'
            path.write_bytes(_engine_header() + b'\x01\x00' * 10)
            self.assertTrue(tts.fix_wav_sizes(path))
            data = path.read_bytes()
            self.assertEqual(int.from_bytes(data[4:8], 'little'), len(data) - 8)
            self.assertEqual(int.from_bytes(data[40:44], 'little'), 20)
            self.assertFalse(tts.fix_wav_sizes(path))          # already right: untouched
            import wave
            with wave.open(str(path)) as w:
                self.assertEqual(w.getnframes(), 10)
            other = Path(directory) / 'note.txt'
            other.write_bytes(b'not a wav file')
            self.assertFalse(tts.fix_wav_sizes(other))
            self.assertEqual(other.read_bytes(), b'not a wav file')
            self.assertFalse(tts.fix_wav_sizes(Path(directory) / 'missing.wav'))

    def test_speech_ticket(self):
        # Exercise the route body without importing LLM/TTS/Flask dependencies.
        tree = ast.parse((ROOT / 'api.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'speech')
        function.decorator_list = []
        class Response:
            def __init__(self, generator, **kwargs):
                self.generator = generator
                self.headers = {}
            def call_on_close(self, callback): self.close = callback
        tickets = {'one': (time.monotonic(), 'test', None)}
        # The speed-log stopwatch (backend/timing.py) has no heavy imports.
        spec = importlib.util.spec_from_file_location('speech_timing_test', ROOT / 'timing.py')
        timing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(timing)
        scope = dict(request=SimpleNamespace(method='GET'), _speech_requests=tickets, timing=timing,
                     _speech_requests_lock=threading.Lock(), time=time, chain=chain,
                     jsonify=lambda data: data, Response=Response,
                     streamVoiceChunks=lambda text, save_path=None: (chunk for chunk in (b'header', b'pcm')))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'api.py', 'exec'), scope)
        response = scope['speech']('one')
        self.assertEqual(scope['speech']('one')[1], 404)
        self.assertEqual(b''.join(response.generator), b'headerpcm')
        tickets['old'] = (time.monotonic() - 301, 'test', None)
        self.assertEqual(scope['speech']('old')[1], 404)
        def fail(text, save_path=None):
            raise RuntimeError('upstream failure')
            yield
        scope['streamVoiceChunks'] = fail
        tickets['error'] = (time.monotonic(), 'test', None)
        self.assertEqual(scope['speech']('error')[1], 502)


if __name__ == '__main__':
    unittest.main()

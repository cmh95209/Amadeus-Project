"""Personality API and prompt regression tests; no LLM or TTS calls."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class PersonalityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, previous_directory)
        backend = str(Path(__file__).resolve().parents[1])
        sys.path.insert(0, backend)
        self.addCleanup(sys.path.remove, backend)
        modules = patch.dict(sys.modules, {
            'llm': SimpleNamespace(get_llm=lambda *args: None, reset_llm=lambda: None),
            'tts': SimpleNamespace(
                streamVoiceChunks=lambda text, save_path=None: iter(()),
                renderVoiceToPath=lambda text, path: False,
            ),
        })
        modules.start()
        self.addCleanup(modules.stop)
        for name in ('api', 'chat', 'memory', 'chat_interactions'):
            sys.modules.pop(name, None)
        self.api = importlib.import_module('api')
        self.chat = importlib.import_module('chat')
        self.memory = importlib.import_module('memory')
        self.client = self.api.application.test_client()

    def test_save_read_and_next_prompt_without_restart(self):
        self.memory.save_personality('Original personality')
        self.memory.append_message('user', 'Keep this conversation')
        initial = self.client.get('/getPersonality')
        self.assertEqual(initial.json['personality'], 'Original personality')
        self.assertEqual(initial.headers['Cache-Control'], 'no-store')
        updated = '  You are Kurisu.\n自然に話してね。  '
        response = self.client.post('/setPersonality', json={'personality': updated})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['personality'], updated.strip())
        self.assertEqual(self.client.get('/getPersonality').json['personality'], updated.strip())
        self.assertEqual(Path('data/personality.txt').read_text(encoding='utf-8'), updated.strip())
        captured = []
        reply = self.chat.AmadeusPack(assistant_reply_ENG='Hello', assistant_reply_JPS='こんにちは')
        # Cover BOTH reply paths: web-access OFF goes through
        # with_structured_output; web-access ON goes through bind_tools and a
        # forced AmadeusPack tool call. (Fresh installs default to OFF now.)
        tool_reply = SimpleNamespace(tool_calls=[{
            'name': 'AmadeusPack',
            'id': 'c1',
            'args': {'assistant_reply_JPS': reply.assistant_reply_JPS,
                     'assistant_reply_ENG': reply.assistant_reply_ENG},
        }])
        fake = SimpleNamespace(
            with_structured_output=lambda *a, **k: SimpleNamespace(
                invoke=lambda messages: captured.extend(messages) or reply),
            bind_tools=lambda tools, **k: SimpleNamespace(
                invoke=lambda messages: captured.extend(messages) or tool_reply),
        )
        with patch.object(self.chat, 'get_llm', return_value=fake):
            self.chat.getResponsePacked([])
        # Amadeus merges ALL of its instruction blocks (personality, timing,
        # output rules, voice) into ONE leading system message, because most
        # chat templates reject or silently drop extra system messages.
        systems = [m for m in captured if m["role"] == "system"]
        self.assertEqual(len(systems), 1)
        self.assertTrue(captured[0]["content"].startswith(updated.strip()))
        self.assertIn("assistant_reply_JPS", captured[0]["content"])
        self.assertIn("Private timing context", captured[0]["content"])
        self.assertEqual(self.memory.load_memory_raw()[0]['content'], 'Keep this conversation')

    def test_invalid_input_does_not_overwrite_personality(self):
        self.memory.save_personality('Keep me')
        for payload in ({}, [], {'personality': None}, {'personality': 123},
                        {'personality': []}, {'personality': ''}, {'personality': '  '}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/setPersonality', json=payload).status_code, 400)
                self.assertEqual(self.memory.load_personality(), 'Keep me')

    def test_storage_errors_have_readable_responses(self):
        with patch.object(self.api, 'getPersonality', side_effect=OSError):
            response = self.client.get('/getPersonality')
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json['message'], 'Could not load personality')
        with patch.object(self.api, 'setPersonality', side_effect=OSError):
            response = self.client.post('/setPersonality', json={'personality': 'New text'})
            self.assertEqual(response.status_code, 500)
            self.assertIn('Could not save personality', response.json['message'])


if __name__ == '__main__':
    unittest.main()

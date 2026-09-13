"""Test selection/memory pairing without loading the LLM or TTS."""
import ast
from pathlib import Path
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class InteractionAudioTests(unittest.TestCase):
    def test_variants_remain_paired(self):
        root = Path(__file__).resolve().parents[1]
        chat_tree = ast.parse((root / 'chat.py').read_text())
        function = next(n for n in chat_tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'SpecialInteraction')
        # The event/response tables moved to chat_interactions.py (chat.py
        # imports them), so load the real data from that file instead.
        tables = ast.parse((root / 'chat_interactions.py').read_text())
        nodes = [n for n in tables.body if isinstance(n, ast.Assign) and any(
                     isinstance(t, ast.Name) and t.id in
                     {'INTERACTION_EVENTS', 'INTERACTION_RESPONSES'}
                     for t in n.targets)] + [function]
        messages = []
        # append_message now also carries the Japanese line; set_message_audio
        # records which recording belongs to the reply (both no-ops here).
        scope = {'store': SimpleNamespace(
                     append_message=lambda role, text, japanese=None: messages.append((role, text)),
                     set_message_audio=lambda message_id, audio_url: None),
                 'random': random}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'chat.py', 'exec'), scope)
        for interaction_id, variants in scope['INTERACTION_RESPONSES'].items():
            for variant in variants:
                messages.clear()
                with patch.object(random, 'choice', return_value=variant):
                    reply = scope['SpecialInteraction'](interaction_id)
                self.assertEqual(reply['response'], variant['text'])
                self.assertEqual(reply['audio_url'], variant['audio_url'])
                self.assertEqual(messages[-1], ('assistant', variant['text']))
        variant = {'text': 'Example text', 'audio_url': '/audio/example.wav'}
        with patch.object(random, 'choice', return_value=variant):
            reply = scope['SpecialInteraction'](2)
            # The reply now also carries event/response ids (None here, since
            # the store mock returns None), so check the visible fields.
            self.assertEqual(reply['response'], 'Example text')
            self.assertEqual(reply['audio_url'], '/audio/example.wav')
            self.assertEqual(reply['event_id'], None)
            self.assertEqual(reply['response_id'], None)
        messages.clear()
        with self.assertRaises(ValueError): scope['SpecialInteraction'](999)
        self.assertEqual(messages, [])


if __name__ == '__main__': unittest.main()

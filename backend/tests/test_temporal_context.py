"""Offline regression checks for conversation gaps and incoming-message order."""
import ast
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('temporal_memory', ROOT / 'memory.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class TemporalContextTests(unittest.TestCase):
    def context(self, timestamp, now):
        rows = [] if timestamp is None else [{'role': 'user', 'created_at': timestamp}]
        with patch.object(store, 'load_memory_raw', return_value=rows), patch.object(store, 'previous_contact_timing', return_value=None):
            return store.load_internal_context(now)['content']

    def test_legacy_local_timestamp_and_followup(self):
        now = datetime(2026, 9, 6, 18, 42).astimezone()
        content = self.context('2026-09-06 14:10', now)
        self.assertIn('4 hours, 32 minutes', content)
        self.assertIn('first message after a substantial', content)
        followup = self.context('2026-09-06 18:42', now + timedelta(seconds=20))
        self.assertIn('less than one minute', followup)
        self.assertIn('a welcome-back is not expected', followup)

    def test_multi_day_and_timezone_aware_dates(self):
        now = datetime(2026, 9, 6, 18, 42, tzinfo=timezone.utc)
        content = self.context((now - timedelta(days=2, hours=3)).isoformat(), now)
        self.assertIn('2 days, 3 hours', content)
        equivalent = now.astimezone(timezone(timedelta(hours=-7))).isoformat()
        self.assertIn('less than one minute', self.context(equivalent, now))

    def test_unknown_bad_or_future_dates_do_not_invent_absence(self):
        now = datetime.now().astimezone()
        self.assertIn('No previous user message', self.context(None, now))
        for timestamp in ('bad date', '', 42, (now + timedelta(days=1)).isoformat()):
            with self.subTest(timestamp=timestamp):
                self.assertIn('unavailable or unreliable', self.context(timestamp, now))

    def test_latest_user_turn_ignores_assistant_timestamp(self):
        rows = [{'role': 'user', 'created_at': '2026-09-06 14:10'},
                {'role': 'assistant', 'created_at': '2026-09-06 18:41'}]
        with patch.object(store, 'load_memory_raw', return_value=rows):
            self.assertIn('4 hours, 32 minutes', store.load_internal_context(
                datetime(2026, 9, 6, 18, 42).astimezone())['content'])

    def test_capture_precedes_insert_and_does_not_enter_memory(self):
        # Run the real orchestration function against a temporary SQLite store.
        tree = ast.parse((ROOT / 'chat.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'getOutputPacked')
        now = datetime(2026, 9, 6, 18, 42).astimezone()
        captures = []
        reply = SimpleNamespace(assistant_reply_ENG='Welcome back.',
                                assistant_reply_JPS='おかえり。')
        def respond(context, internal_context):
            captures.append(internal_context['content'])
            self.assertEqual(context[-1], {'role': 'user', 'content': 'Hello again'})
            return reply
        with tempfile.TemporaryDirectory() as directory, patch.object(
                store, 'PATH_TO_MEMORY', str(Path(directory) / 'memory.db')):
            store.append_message('user', 'Earlier message')
            # A sqlite3 `with` only commits; it does NOT close, and the
            # leftover handle would keep the file locked when the temp
            # directory is removed (fails on Windows). Close explicitly.
            conn = store.sqlite3.connect(store.PATH_TO_MEMORY)
            try:
                conn.execute("UPDATE messages SET created_at = '2026-09-06 14:10'")
                # close() rolls back uncommitted changes; commit first or the
                # timestamp never actually changes.
                conn.commit()
            finally:
                conn.close()
            original_context = store.load_internal_context
            # maybe_schedule_trust_rescore is a real chat.py helper the
            # orchestration calls after storing the reply; no-op it here.
            # The orchestration mirrors each persisted turn to the
            # CharacterMemory sidecar (fire-and-forget). Stub it out: the test
            # must not fire a real HTTP save, and a live sidecar on a dev
            # machine must not ingest test turns.
            scope = {'store': store, 'AmadeusPack': object, 'getResponsePacked': respond,
                     'maybe_schedule_trust_rescore': lambda: None,
                     'cm_bridge': SimpleNamespace(save_turn=lambda *a, **k: None)}
            exec(compile(ast.Module(body=[function], type_ignores=[]), 'chat.py', 'exec'), scope)
            with patch.object(store, 'load_internal_context', side_effect=lambda: original_context(now)):
                scope['getOutputPacked']('Hello again')
            self.assertIn('4 hours, 32 minutes', captures[0])
            self.assertEqual([row['role'] for row in store.load_memory_raw()],
                             ['user', 'user', 'assistant'])
            self.assertNotIn('Private timing context', str(store.load_memory_raw()))


class PreviousContactLineTests(unittest.TestCase):
    # Cross-conversation last-contact line (2026-10-01): the reply-path timing
    # block must ground a 'when did we last talk / how long was I away' answer
    # in the MEASURED newest user message outside the active conversation, never
    # a guess. These checks pin that line and the previous_contact_timing helper.

    def _content(self, rows, contact, now):
        with patch.object(store, 'load_memory_raw', return_value=rows), patch.object(store, 'previous_contact_timing', return_value=contact):
            return store.load_internal_context(now)['content']

    def test_last_contact_line_is_measured(self):
        now = datetime(2026, 9, 6, 18, 42).astimezone()
        contact = {
            'time': now - timedelta(days=8, hours=4),
            'phrase': 'a week and a bit ago',
            'content': 'Talk to you tomorrow',
        }
        content = self._content([], contact, now)
        self.assertIn('You last spoke with the user a week and a bit ago (2026-08-29 14:42', content)
        self.assertIn('"Talk to you tomorrow"', content)
        self.assertIn('measured gap - not a guess', content)

    def test_no_other_conversation_keeps_the_baseline(self):
        now = datetime(2026, 9, 6, 18, 42).astimezone()
        content = self._content([], None, now)
        self.assertNotIn('You last spoke with the user', content)

    def test_previous_contact_timing_reads_only_other_conversations(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(store, 'PATH_TO_MEMORY', str(Path(directory) / 'memory.db')), patch.object(store, 'PATH_TO_ACTIVE_CONV', str(Path(directory) / 'active.txt')):
                first = store.create_conversation('Old thread')
                store.append_message('user', 'Old thread message', conversation_id=first)
                second = store.create_conversation('Fresh thread')
                store.append_message('user', 'Fresh thread message', conversation_id=second)
                info = store.previous_contact_timing(datetime.now().astimezone())
                self.assertIsNotNone(info)
                self.assertEqual(info['title'], 'Old thread')
                self.assertIn('Old thread message', info['content'])
                store.append_message('user', 'Fresh thread new message', conversation_id=second)
                info2 = store.previous_contact_timing(datetime.now().astimezone())
                self.assertEqual(info2['title'], 'Old thread')
                store.delete_conversation(first)
                self.assertIsNone(store.previous_contact_timing(datetime.now().astimezone()))


if __name__ == '__main__':
    unittest.main()

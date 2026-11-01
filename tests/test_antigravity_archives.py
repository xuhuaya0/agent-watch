"""Native Antigravity archive changes must not depend on transcript activity."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from aw.adapters.antigravity import scan
from aw.adapters.native_sidebar import antigravity_archives
from aw.config import DEFAULTS
from aw.control.antigravity import Antigravity
from aw.control.broker import ControlError
from aw.control.sessions import SessionScope
from aw.scanner import scan_all


class AntigravityArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sid = str(uuid.uuid4())
        self.dbpath = self.root / 'conversation_summaries.db'
        self.db = sqlite3.connect(self.dbpath)
        self.addCleanup(self.db.close)
        # Real FTS headers and body chunks mirror the installed native schema.
        self.db.execute('CREATE VIRTUAL TABLE conversations_fts USING fts5(conversation_id UNINDEXED, chunk_index UNINDEXED, archived UNINDEXED, title)')
        self.db.executemany('INSERT INTO conversations_fts VALUES (?, ?, ?, ?)', [
            (self.sid, -1, 0, 'Task'), (self.sid, 0, 0, 'Body'),
        ])
        self.db.commit()
        self.log = self.root / 'brain' / self.sid / '.system_generated/logs/transcript.jsonl'
        self.log.parent.mkdir(parents=True)
        self.log.write_text(json.dumps({'type': 'USER_INPUT', 'content': 'Question', 'created_at': '2026-01-01T00:00:00Z'})+'\n')
        self.sidebar = self.root / 'app_storage.json'
        self.sidebar.write_text(json.dumps({'pinned_conversations_order': json.dumps([self.sid])}))
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg['machine'] = 'test'
        self.cfg['adapters']['antigravity'].update(root=str(self.root), sidebar_path=str(self.sidebar), enabled=True)
        self.cfg['adapters']['codex']['enabled'] = False
        self.cfg['adapters']['dsh']['enabled'] = False
        self.config = self.root / 'control.json'
        self.config.write_text(json.dumps({'antigravity': {'scope': 'all', 'data_root': str(self.root)}}))
        self.scope = SessionScope(self.config)

    def archive(self, value):
        self.db.execute('UPDATE conversations_fts SET archived=? WHERE chunk_index=-1', (value,))
        self.db.commit()

    def test_archive_and_restore_refresh_without_transcript_changes_even_when_pinned(self):
        before = (self.log.read_bytes(), self.log.stat().st_mtime_ns, self.sidebar.read_bytes())
        self.assertEqual([t.session_id for t in scan(self.cfg)], [self.sid])
        self.assertTrue(scan(self.cfg)[0].extra['native_pin']['pinned'])
        self.archive(1)
        self.assertEqual(antigravity_archives(self.root), {self.sid: True})
        self.assertEqual(scan(self.cfg), [])
        self.archive(0)
        self.assertEqual([t.session_id for t in scan(self.cfg)], [self.sid])
        self.assertIs(scan(self.cfg)[0].extra['native_archived'], False)
        self.assertEqual(before, (self.log.read_bytes(), self.log.stat().st_mtime_ns, self.sidebar.read_bytes()))

    def test_only_header_is_authoritative_not_body_chunks(self):
        self.db.execute('UPDATE conversations_fts SET archived=1 WHERE chunk_index=0')
        self.db.commit()
        self.assertEqual(antigravity_archives(self.root), {self.sid: False})
        before = self.dbpath.read_bytes()
        antigravity_archives(self.root)
        self.assertEqual(before, self.dbpath.read_bytes())

    def test_all_and_selected_control_scope_revoke_and_restore(self):
        for mode in ('all', 'selected'):
            self.config.write_text(json.dumps({'antigravity': {'scope': mode, 'session_ids': [self.sid], 'data_root': str(self.root)}}))
            self.archive(0)
            self.assertEqual(self.scope(), [self.sid])
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout='{}')
            provider = Antigravity('/tmp/native', self.scope, run=run, environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:1'})
            self.assertTrue(provider.session_snapshot(self.sid))
            self.archive(1)
            calls.clear()
            self.assertEqual(self.scope(), [])
            self.assertEqual(provider.session_snapshot(self.sid), [])
            with self.assertRaisesRegex(ControlError, 'native_session_unavailable'):
                provider.execute({'session_id': self.sid, 'action': {'kind': 'reply', 'text': 'stale reply'}})
            self.assertEqual(calls, [])
            self.archive(0)
            self.assertEqual(self.scope(), [self.sid])

    def test_missing_index_is_not_created(self):
        missing = self.root / 'older-installation'
        self.assertEqual(antigravity_archives(missing), {})
        self.assertFalse(missing.exists())

    def test_unreadable_index_reports_scan_error_and_disables_control(self):
        self.db.execute('DROP TABLE conversations_fts')
        self.db.commit()
        tasks, errors = scan_all(self.cfg)
        self.assertEqual(tasks, [])
        self.assertIn('antigravity', errors)
        self.assertEqual(self.scope(), [])

    def test_invalid_archive_values_are_not_treated_as_unarchived(self):
        for value in (None, 'false', 2):
            self.archive(value)
            with self.assertRaises(ValueError):
                antigravity_archives(self.root)
            self.assertEqual(self.scope(), [])

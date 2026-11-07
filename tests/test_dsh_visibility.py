"""Native blank-session and committed-log selection rules."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aw.adapters import dsh
from aw.config import DEFAULTS


def event(kind, **data):
    return {'type': kind, 'time': 1000, 'data': data}


class DeepSeekVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg['adapters']['dsh']['root'] = str(self.root)
        dsh._LOGS.clear()
        self.addCleanup(dsh._LOGS.clear)

    def write(self, sid, rows=(), *, name='session.v4.jsonl', project='project', origin=None):
        path = self.root/'sessions'/project/sid/name
        path.parent.mkdir(parents=True, exist_ok=True)
        header = {'type': 'session', 'id': sid, 'cwd': '/synthetic/project', 'createdAt': 1000}
        if origin is not None:
            header['origin'] = origin
        raw = ''.join(json.dumps(row)+'\n' for row in [header, *rows]).encode()
        if name.endswith('.zstd'):
            raw = dsh.zstandard.ZstdCompressor().compress(raw)
        path.write_bytes(raw)
        return path

    def workspace(self, ids, archived=(), pinned=()):
        folder = self.root/'storages'; folder.mkdir(exist_ok=True)
        (folder/'workspace.json').write_text(json.dumps({'tables': {'workspaces': {
            'project': {'path': '/synthetic/project', 'sessionIds': list(ids)}}},
            'global': {'archivedSessionIds': list(archived), 'pinnedSessionIds': list(pinned)}}))

    def test_registered_pinned_or_titled_initialization_is_blank_until_turn_start(self):
        self.write('session-empty', [event('session/title', title='Draft'),
            event('permission/preset', preset='workspace-write'),
            event('model/selection', model='example-model'), event('session/end-seed')])
        self.workspace(['session-empty'], pinned=['session-empty'])
        self.assertEqual(dsh.scan(self.cfg, 2), [])
        with (self.root/'sessions/project/session-empty/session.v4.jsonl').open('a') as file:
            file.write(json.dumps(event('turn/start', turn=1))+'\n')
        task, = dsh.scan(self.cfg, 3)
        self.assertEqual((task.session_id, task.title, task.state), ('session-empty', 'Draft', 'running'))
        self.assertTrue(task.extra['native_pin']['pinned'])

    def test_native_blank_uses_turn_event_not_message_text_or_turn_number(self):
        for sid, content, turn in [('session-image', [{'type': 'image', 'source': 'synthetic'}], 1),
                                   ('session-empty-text', [], 1), ('session-zero', '', 0)]:
            self.write(sid, [event('user/message', content=content, source={'kind': 'user'}),
                            event('turn/start', turn=turn)])
        self.assertEqual({t.session_id for t in dsh.scan(self.cfg, 2)},
                         {'session-image', 'session-empty-text', 'session-zero'})

    def test_normal_unlisted_cli_history_and_session_prefix_remain_visible(self):
        path = self.write('session-real', [event('turn/start', turn=1),
            event('session/title', title='session-not-a-debug-session'),
            event('turn/end', turn=1, reason={'kind': 'completed'})])
        os.utime(path, (1, 1))
        self.workspace([])
        task, = dsh.scan(self.cfg, 10**9)
        self.assertEqual((task.title, task.session_id), ('session-not-a-debug-session', 'session-real'))
        self.cfg['include_all_sessions'] = False
        self.assertEqual(dsh.scan(self.cfg, 10**9), [])

    def test_native_subagent_marker_and_archive_override_sidebar_membership(self):
        rows = [event('turn/start', turn=1)]
        self.write('session-child', rows, origin='subagent')
        self.write('session-archived', rows)
        self.workspace(['session-child', 'session-archived'], archived=['session-archived'])
        self.assertEqual(dsh.scan(self.cfg, 2), [])

    def test_backup_corrupt_and_temp_names_cannot_replace_real_compressed_log(self):
        active = self.write('session-real', [event('turn/start', turn=1),
            event('session/title', title='Real task'), event('turn/end', turn=1, reason={'kind': 'completed'})],
            name='session.v4.jsonl.zstd')
        ignored = ['session.v4.jsonl.zstd.bak-test', 'session.v4.jsonl.zstd.bak-corrupt-test',
                   'session.v4.jsonl.tmp', 'session.v4.jsonl.zstd.partial',
                   'session.v04.jsonl', 'session.v0.jsonl', 'session.v99.jsonl.backup']
        for name in ignored:
            p = active.with_name(name); p.write_bytes(b'not an active log'); os.utime(p, (10**9, 10**9))
        before = {p: hashlib.sha256(p.read_bytes()).digest() for p in active.parent.iterdir()}
        native_glob = dsh.glob.glob
        candidates = native_glob(str(self.root/'sessions/*/*/session.v*.jsonl*'))
        for order in (candidates, list(reversed(candidates))):
            with patch.object(dsh.glob, 'glob', return_value=order):
                task, = dsh.scan(self.cfg, 2)
                self.assertEqual((task.title, task.state), ('Real task', 'done'))
        self.assertEqual({p: hashlib.sha256(p.read_bytes()).digest() for p in before}, before)
        self.assertEqual(set(dsh._LOGS), {str(active)})

    def test_backups_alone_do_not_create_a_placeholder_task(self):
        self.write('session-backup', [event('turn/start', turn=1)], name='session.v4.jsonl.bak')
        self.assertEqual(dsh.scan(self.cfg, 2), [])

    def test_highest_canonical_generation_wins_independently_of_mtime(self):
        old = self.write('session-real', [event('turn/start', turn=1),
            event('session/title', title='Old title')], name='session.v3.jsonl')
        self.write('session-real', [event('turn/start', turn=2),
            event('session/title', title='Current title')])
        os.utime(old, (10**9, 10**9))
        task, = dsh.scan(self.cfg, 2)
        self.assertEqual(task.title, 'Current title')

    def test_ambiguous_encoding_duplicate_id_or_future_version_does_not_guess(self):
        rows = [event('turn/start', turn=1)]
        self.write('session-mixed', rows)
        self.write('session-mixed', rows, name='session.v3.jsonl.zstd')
        self.write('session-duplicate', rows, project='one')
        self.write('session-duplicate', rows, project='two')
        self.write('session-future', rows)
        self.write('session-future', rows, name='session.v10.jsonl')
        self.write('session-normal', rows)
        tasks = {t.session_id: t for t in dsh.scan(self.cfg, 2)}
        for sid in ('session-mixed', 'session-duplicate', 'session-future'):
            self.assertEqual(tasks[sid].state, 'error')
        self.assertEqual(tasks['session-normal'].state, 'running')
        self.workspace([], archived=['session-mixed', 'session-duplicate', 'session-future'])
        self.assertEqual([t.session_id for t in dsh.scan(self.cfg, 2)], ['session-normal'])


if __name__ == '__main__':
    unittest.main()

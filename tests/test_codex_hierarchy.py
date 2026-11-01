import copy
import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest

from aw.adapters import codex
from aw.config import DEFAULTS
from aw.model import Task

NOW = 1800000000


def row(kind, age=60, **payload):
    return {"type": kind, "timestamp": datetime.datetime.fromtimestamp(
        NOW-age, datetime.timezone.utc).isoformat(), "payload": payload}


def event(kind, age=60, **payload):
    return row('event_msg', age, type=kind, **payload)


class CodexHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'sessions').mkdir()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg['adapters']['codex']['root'] = str(self.root)

    def write(self, sid, events, *, parent=None, source='vscode', thread_source='user', mtime=NOW, meta=True):
        payload = {'id': sid, 'cwd': '/work/project', 'source': source, 'thread_source': thread_source}
        if parent:
            payload['parent_thread_id'] = parent
        rows = ([row('session_meta', 100, **payload)] if meta else []) + events
        path = self.root/'sessions'/f'rollout-{sid}.jsonl'
        path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n', encoding='utf-8')
        os.utime(path, (mtime, mtime))
        return path

    def child(self, sid, parent, events):
        return self.write(sid, events, source={'subagent': {'thread_spawn': {'parent_thread_id': parent}}})

    def scan(self, now=NOW):
        return codex.scan(self.cfg, now)

    def test_guardian_with_and_without_real_events_is_filtered(self):
        for sid, events in [('empty', [row('response_item', type='compaction'), event('thread_settings_applied')]),
                            ('full', [event('user_message', message='Review execution'), event('task_started')])]:
            self.write(sid, events, source={'subagent': {'other': 'guardian'}})
        self.write('guardian-tag', [event('task_started')], thread_source='guardian_review')
        self.write('main', [event('user_message', message='guardian implementation'), event('task_complete')])
        self.assertEqual([t.session_id for t in self.scan()], ['rollout-main'])

    def test_initialization_only_is_not_a_task(self):
        self.write('empty', [row('response_item', type='compaction'), event('thread_settings_applied'),
                             row('response_item', type='message', role='developer', content='Setup instructions')])
        self.assertEqual(self.scan(), [])

    def test_child_and_grandchild_fold_under_main_and_roundtrip(self):
        self.write('main', [event('task_started')])
        self.child('child', 'main', [event('user_message', message='Review'), event('task_started')])
        self.child('grandchild', 'child', [event('user_message', message='Check tests'), event('task_complete')])
        tasks = self.scan()
        self.assertEqual(len(tasks), 1)
        task = Task.from_dict(tasks[0].to_dict(NOW))
        child = task.extra['subagents'][0]
        self.assertEqual((child['label'], child['state']), ('Review', 'running'))
        self.assertEqual(child['subagents'][0]['label'], 'Check tests')
        self.assertEqual(task.session_id, 'rollout-main')

    def test_active_parent_inherits_highest_priority_child_attention(self):
        self.write('main', [event('task_started')])
        self.child('a', 'main', [event('task_started'), event('error', message='Failure')])
        self.child('b', 'main', [event('task_started'), event('exec_approval_request', 10)])
        task = self.scan()[0]
        self.assertEqual(task.state, 'waiting_approval')
        self.assertEqual(task.state_since, NOW-10)

    def test_completed_parent_stays_completed(self):
        self.write('main', [event('task_complete')])
        self.child('child', 'main', [event('task_started'), event('exec_approval_request')])
        task = self.scan()[0]
        self.assertEqual(task.state, 'done')
        self.assertEqual(task.extra['subagents'][0]['state'], 'waiting_approval')

    def test_user_fork_parent_id_is_not_subagent(self):
        self.write('main', [event('task_complete')])
        self.write('fork', [event('task_started')], parent='main')
        self.assertEqual(len(self.scan()), 2)

    def test_recent_child_loads_parent_outside_lookback(self):
        self.write('main', [event('task_complete', 8*86400)], mtime=NOW-8*86400)
        self.child('child', 'main', [event('task_started')])
        tasks = self.scan()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].extra['subagents'][0]['state'], 'running')

    def test_missing_parent_and_cycles_do_not_become_main_tasks(self):
        self.child('missing', 'absent', [event('task_started')])
        self.child('a', 'b', [event('task_started')])
        self.child('b', 'a', [event('task_started')])
        self.assertEqual(self.scan(), [])

    def test_missing_state_time_uses_last_activity_not_scan_time(self):
        self.write('main', [event('user_message', 60000, message='Request'), event('thread_settings_applied', 1)])
        first, second = self.scan()[0], self.scan(NOW+100)[0]
        self.assertEqual(first.state_since, NOW-60000)
        self.assertEqual(first.state_since, second.state_since)
        self.assertEqual(first.to_dict(NOW)['activity_age_sec'], 60000)

    def test_missing_all_timestamps_falls_back_to_stable_file_time(self):
        self.write('main', [{'type':'event_msg','payload':{'type':'task_started'}}], meta=False, mtime=NOW-60000)
        self.assertEqual(self.scan()[0].state_since, NOW-60000)
        self.assertEqual(self.scan(NOW+100)[0].state_since, NOW-60000)

    def test_settings_event_does_not_refresh_running_activity(self):
        self.write('main', [event('task_started', 2000), event('thread_settings_applied', 1)])
        self.assertEqual(self.scan()[0].state, 'stalled')

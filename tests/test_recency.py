import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aw.config import DEFAULTS
from aw.model import Task
from aw.store import Store
from hub_fixture import isolated_hub

NOW = 1800000000.0

class RecencyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(copy.deepcopy(DEFAULTS),tmp.name)
        self.enterContext(patch('aw.store.time.time', return_value=NOW))

    def task(self, age, state='done', sid=None):
        return Task(tool='codex',session_id=sid or str(age),title='Example',
                    state=state,state_since=NOW-age,last_event_at=NOW-age)

    def test_exact_history_boundaries_and_unknown_timestamp(self):
        ages=[7199,7200,43199,43200]
        self.store.set_local([self.task(x) for x in ages]+[self.task(NOW,sid='unknown')],{})
        views=self.store.view()
        self.assertEqual([x['group'] for x in views],['done','recent','recent','other','other'])
        self.assertEqual(self.store.counts(views)['done'],1)
        self.assertEqual(self.store.counts(views)['recent'],2)
        self.assertEqual([x['key'] for x in self.store.task_summary()['items']],[views[0]['key']])

    def test_ack_does_not_move_recent_completion_into_another_age_group(self):
        self.store.set_local([self.task(60)],{})
        key=self.store.view()[0]['key']
        self.store.ack(key)
        self.assertTrue(self.store.view()[0]['acked'])
        self.assertEqual(self.store.view()[0]['group'],'done')
        self.assertFalse(self.store.task_summary()['items'])

    def test_active_tasks_remain_active_after_twelve_hours(self):
        self.store.set_local([self.task(50000,'running'),self.task(60000,'waiting_approval')],{})
        self.assertEqual([x['group'] for x in self.store.view()],['attention','running'])

    def test_newest_activity_first_even_for_running_tasks(self):
        self.store.set_local([self.task(100,'running'),self.task(10,'running'),self.task(5),self.task(60)],{})
        self.assertEqual([x['session_id'] for x in self.store.view()],['10','100','5','60'])

    def test_bookkeeping_after_completion_does_not_make_old_task_recent(self):
        t=self.task(50000); t.last_event_at=NOW
        self.store.set_local([t],{})
        self.assertEqual(self.store.view()[0]['group'],'other')

class RuntimeIsolationTests(unittest.TestCase):
    def test_hub_fixture_isolates_all_writable_runtime_paths(self):
        with isolated_hub() as hub:
            from aw.config import HERE
            runtime=Path(hub.DATA_DIR)
            self.assertNotEqual(runtime,Path(HERE)/'data')
            self.assertEqual(Path(hub.STORE.remote_file).parent,runtime)
            self.assertEqual(Path(hub.STORE.ack_file).parent,runtime)
            self.assertEqual(Path(hub.AUTH.path).parent,runtime)
            response=hub.app.test_client().post('/api/push',headers={'Authorization':'Bearer test-receiver'},json={'machine':'laptop','tasks':[]})
            self.assertEqual(response.status_code,200)
            self.assertTrue(Path(hub.STORE.remote_file).exists())
        self.assertFalse(runtime.exists())

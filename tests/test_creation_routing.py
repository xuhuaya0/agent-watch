import tempfile
import json
import unittest
import uuid
from pathlib import Path

from aw.control.broker import Broker, ControlError
from aw.control.worker import Worker
from aw.control.sessions import SessionScope


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.source = str(uuid.uuid4())
        self.native = {'tool': 'codex', 'session_id': self.source, 'reply': True,
                       'create': {'cwd': '/workspace/demo', 'revision': 'v1'}}
        self.exchange([])
        self.command_id = str(uuid.uuid4())
        self.broker.submit('device', 'codex', self.source, self.command_id,
                           {'kind': 'create', 'text': 'hello', 'revision': 'v1'})
        self.assertEqual(self.exchange([])['command']['id'], self.command_id)

    def exchange(self, receipts):
        return self.broker.exchange('device', 'worker', [self.native], receipts)

    def test_only_a_new_thread_in_the_command_harness_can_be_linked(self):
        target = str(uuid.uuid4())
        receipt = {'id': self.command_id, 'state': 'delivered'}
        for tool, sid in [('antigravity', target), ('codex', self.source), ('codex', 'session-' + target)]:
            with self.subTest(tool=tool, sid=sid), self.assertRaises(ControlError):
                self.exchange([{**receipt, 'result': {'tool': tool, 'session_id': sid}}])
        destination = {'tool': 'codex', 'session_id': target}
        self.exchange([{**receipt, 'result': destination}])
        self.assertEqual(self.broker.status('device', 'codex', self.source)['commands'][0]['result'], destination)


class CreationReceiptTests(unittest.TestCase):
    def test_desktop_draft_is_never_reported_as_a_sent_conversation(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = str(uuid.uuid4())
        row = {'tool':'codex','session_id':source,'reply':True,
               'create':{'cwd':'/workspace/example','revision':'r1','mode':'draft'}}
        calls = []
        class DraftProvider:
            def snapshot(self):return [row]
            def execute(self, command):
                calls.append(command['id'])
                return 'draft_opened'
        worker = Worker({'hub_url':'http://example.invalid','device_id':'device','token':'fixture'},
                        {'codex':DraftProvider()}, root/'worker.json')
        command = {'id':str(uuid.uuid4()),'tool':'codex','session_id':source,
                   'action':{'kind':'create','text':'Only a draft','revision':'r1'}}
        worker.exchange = lambda body: {'ok':True,'receipts':[],'command':command}
        worker.step();worker.step()
        receipt = worker.state['receipts'][0]
        self.assertEqual(receipt['state'],'resolved')
        self.assertNotIn('result',receipt)
        self.assertIn('确认并发送',receipt['detail'])
        self.assertEqual(calls,[command['id']])
        self.assertNotIn('Only a draft',(root/'worker.json').read_text())

    def test_project_permissions_require_live_explicit_device_opt_in(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root/'control.json'
        scope = SessionScope(path)
        for settings, allowed in [({'scope':'all'},False),
                                  ({'scope':'all','native_project_permissions':True},True),
                                  ({'scope':'selected','native_project_permissions':True},False),
                                  ({'scope':'all','native_project_permissions':False},False)]:
            path.write_text(json.dumps({'antigravity':settings}))
            self.assertEqual(scope.allows_project_permissions(),allowed)
        path.write_text('{')
        self.assertFalse(scope.allows_project_permissions())

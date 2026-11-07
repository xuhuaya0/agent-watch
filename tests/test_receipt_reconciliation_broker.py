"""Late receipts settle delivery uncertainty without another native dispatch."""
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from aw.control.broker import Broker, ControlError


def capability():
    return {'tool': 'codex', 'session_id': 'conversation', 'reply': True, 'requests': []}


class ReceiptReconciliationBrokerTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.now = 1000
        self.broker = Broker(self.directory, lambda: self.now)
        self.broker.provision('device')
        self.register()

    def register(self):
        return self.broker.exchange('device', 'worker', [capability()], [])

    def exchange(self, receipts, *, device='device', worker='worker'):
        return self.broker.exchange(device, worker, [], receipts)

    def submit_and_dispatch(self):
        self.register()
        command_id = str(uuid.uuid4())
        self.broker.submit('device', 'codex', 'conversation', command_id,
                           {'kind': 'reply', 'text': 'Synthetic message'})
        response = self.register()
        self.assertEqual(response['command']['id'], command_id)
        return command_id

    def row(self, command_id):
        with self.broker.db() as db:
            row = db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
        return dict(row) if row else None

    def command_status(self, command_id):
        commands = self.broker.status('device', 'codex', 'conversation')['commands']
        return next(row for row in commands if row['id'] == command_id)

    def test_late_confirmation_settles_unknown_and_survives_restart(self):
        for source in ('native_record', 'late_ack'):
            with self.subTest(source=source):
                command_id = self.submit_and_dispatch()
                self.exchange([{'id': command_id, 'state': 'unknown'}])
                receipt = {'id': command_id, 'state': 'delivered', 'confirmation': source}
                response = self.exchange([receipt])
                self.assertEqual(response['receipts'], [command_id])
                self.assertIsNone(response['command'])
                self.assertEqual(self.command_status(command_id)['confirmation'], source)
                self.assertIsNone(self.row(command_id)['action'])
                self.broker = Broker(self.directory, lambda: self.now)
                self.assertEqual(self.command_status(command_id)['state'], 'delivered')
                self.assertEqual(self.command_status(command_id)['confirmation'], source)
                self.assertIsNone(self.register()['command'])

    def test_late_receipt_can_follow_timeout_restart_or_token_rotation(self):
        for cause in ('timeout', 'restart', 'token_rotation'):
            with self.subTest(cause=cause):
                command_id = self.submit_and_dispatch()
                if cause == 'timeout':
                    self.now += 121
                elif cause == 'restart':
                    self.broker = Broker(self.directory, lambda: self.now)
                else:
                    self.broker.provision('device')
                self.assertEqual(self.command_status(command_id)['state'], 'unknown')
                self.assertIsNone(self.register()['command'])
                self.exchange([{'id': command_id, 'state': 'delivered', 'confirmation': 'late_ack'}])
                self.assertEqual(self.command_status(command_id)['state'], 'delivered')
                self.assertIsNone(self.register()['command'])

    def test_terminal_receipts_cannot_be_downgraded_or_relabelled(self):
        for first_state in ('delivered', 'resolved', 'rejected'):
            with self.subTest(first_state=first_state):
                command_id = self.submit_and_dispatch()
                first = {'id': command_id, 'state': first_state, 'detail': 'Original outcome'}
                if first_state == 'delivered':
                    first['confirmation'] = 'native_record'
                self.exchange([first])
                before = self.command_status(command_id)
                late = [{'id': command_id, 'state': state, 'detail': 'Late outcome'}
                        for state in ('unknown', 'rejected', 'resolved')]
                late.append({'id': command_id, 'state': 'delivered', 'confirmation': 'late_ack'})
                for receipt in late:
                    response = self.exchange([receipt])
                    self.assertEqual(response['receipts'], [command_id])
                    self.assertIsNone(response['command'])
                    self.assertEqual(self.command_status(command_id), before)

    def test_same_batch_unknown_after_confirmation_cannot_undo_it(self):
        command_id = self.submit_and_dispatch()
        response = self.exchange([
            {'id': command_id, 'state': 'unknown'},
            {'id': command_id, 'state': 'delivered', 'confirmation': 'native_record'},
            {'id': command_id, 'state': 'unknown'},
        ])
        self.assertIsNone(response['command'])
        status = self.command_status(command_id)
        self.assertEqual((status['state'], status['confirmation']), ('delivered', 'native_record'))

    def test_receipt_is_bound_to_device_worker_and_command(self):
        command_id = self.submit_and_dispatch()
        self.exchange([{'id': command_id, 'state': 'unknown'}])
        before = self.row(command_id)
        receipt = {'id': command_id, 'state': 'delivered', 'confirmation': 'native_record'}
        self.broker.provision('another-device')
        self.exchange([receipt], device='another-device')
        self.exchange([receipt], worker='another-worker')
        self.exchange([{**receipt, 'id': str(uuid.uuid4())}])
        self.assertEqual(self.row(command_id), before)
        self.exchange([receipt])
        self.assertEqual(self.command_status(command_id)['state'], 'delivered')

    def test_replacement_worker_cannot_claim_predecessor_receipt(self):
        command_id = self.submit_and_dispatch()
        self.exchange([{'id': command_id, 'state': 'unknown'}])
        self.now += 16
        self.broker.exchange('device', 'replacement', [capability()], [])
        receipt = {'id': command_id, 'state': 'delivered', 'confirmation': 'native_record'}
        self.exchange([receipt], worker='replacement')
        self.assertEqual(self.command_status(command_id)['state'], 'unknown')
        # Native discovery may no longer include the conversation; the original
        # device/worker/command binding still permits its durable receipt.
        self.exchange([receipt])
        self.assertEqual(self.command_status(command_id)['state'], 'delivered')

    def test_unknown_never_requeues_when_client_repeats_same_command_id(self):
        command_id = self.submit_and_dispatch()
        self.exchange([{'id': command_id, 'state': 'unknown'}])
        same = self.broker.submit('device', 'codex', 'conversation', command_id,
                                  {'kind': 'reply', 'text': 'Synthetic message'})
        self.assertEqual(same['state'], 'unknown')
        self.assertIsNone(self.register()['command'])
        self.assertIsNone(self.row(command_id)['action'])
        self.exchange([{'id': command_id, 'state': 'delivered', 'confirmation': 'late_ack'}])
        self.assertIsNone(self.register()['command'])

    def test_receipt_cannot_confirm_a_command_before_dispatch(self):
        command_id = str(uuid.uuid4())
        self.broker.submit('device', 'codex', 'conversation', command_id, {'kind': 'reply', 'text': 'Synthetic message'})
        response = self.broker.exchange('device', 'worker', [capability()], [
            {'id': command_id, 'state': 'delivered', 'confirmation': 'native_record'}])
        self.assertEqual(response['command']['id'], command_id)
        self.assertEqual(response['receipts'], [])
        self.assertEqual(self.row(command_id)['state'], 'dispatched')
        self.assertIsNone(self.row(command_id)['confirmation'])

    def test_receipt_outliving_retention_is_acknowledged_without_recreation(self):
        command_id = self.submit_and_dispatch()
        self.exchange([{'id': command_id, 'state': 'unknown'}])
        self.now += 7 * 86400 + 1
        response = self.exchange([{'id': command_id, 'state': 'delivered', 'confirmation': 'native_record'}])
        self.assertEqual(response['receipts'], [command_id])
        self.assertIsNone(response['command'])
        self.assertIsNone(self.row(command_id))

    def test_legacy_receipt_remains_valid_and_has_no_invented_confirmation(self):
        command_id = self.submit_and_dispatch()
        self.exchange([{'id': command_id, 'state': 'unknown'}])
        self.exchange([{'id': command_id, 'state': 'delivered', 'detail': 'Accepted'}])
        self.assertEqual(self.command_status(command_id)['state'], 'delivered')
        self.assertNotIn('confirmation', self.command_status(command_id))
        # A newer retry cannot retroactively relabel an older terminal result.
        self.exchange([{'id': command_id, 'state': 'delivered', 'confirmation': 'native_record'}])
        self.assertNotIn('confirmation', self.command_status(command_id))

    def test_confirmation_and_receipt_metadata_are_strictly_bounded(self):
        command_id = self.submit_and_dispatch()
        for value in (None, True, 1, [], {}, '', 'inferred', 'message contents'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ControlError, 'invalid_receipt'):
                    self.exchange([{'id': command_id, 'state': 'delivered', 'confirmation': value}])
        for state in ('unknown', 'waiting', 'rejected', 'resolved'):
            with self.subTest(state=state):
                with self.assertRaisesRegex(ControlError, 'invalid_receipt'):
                    self.exchange([{'id': command_id, 'state': state, 'confirmation': 'native_record'}])
        for field in ('diagnostic', 'raw', 'text', 'token', 'native_payload'):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ControlError, 'invalid_receipt'):
                    self.exchange([{'id': command_id, 'state': 'delivered', field: 'Synthetic private value'}])
        self.assertEqual(self.row(command_id)['state'], 'dispatched')
        self.assertIsNone(self.row(command_id)['confirmation'])

    def test_confirmation_is_rejected_for_non_reply_actions(self):
        request = {'id': 'approval', 'revision': 'version', 'kind': 'approval',
                   'prompt': 'Approve synthetic operation?', 'choices': ['approve', 'reject']}
        cap = {**capability(), 'requests': [request]}
        self.broker.exchange('device', 'worker', [cap], [])
        command_id = str(uuid.uuid4())
        self.broker.submit('device', 'codex', 'conversation', command_id,
                           {'kind': 'approval', 'request_id': 'approval', 'revision': 'version', 'decision': 'approve'})
        self.broker.exchange('device', 'worker', [cap], [])
        with self.assertRaisesRegex(ControlError, 'invalid_receipt'):
            self.exchange([{'id': command_id, 'state': 'delivered', 'confirmation': 'native_record'}])
        self.assertEqual(self.row(command_id)['state'], 'dispatched')
        self.assertIsNone(self.row(command_id)['confirmation'])

    def test_confirmation_column_migrates_existing_database_without_changing_receipts(self):
        legacy_dir = self.enterContext(tempfile.TemporaryDirectory())
        command_id = str(uuid.uuid4())
        with closing(sqlite3.connect(str(Path(legacy_dir) / 'control.sqlite3'))) as db:
            with db:
                db.execute('''CREATE TABLE commands (
                    id TEXT PRIMARY KEY, device TEXT NOT NULL, tool TEXT NOT NULL, session TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, action TEXT, state TEXT NOT NULL, worker TEXT,
                    created REAL NOT NULL, deadline REAL NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    result TEXT, kind TEXT NOT NULL DEFAULT '')''')
                db.execute('INSERT INTO commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (command_id, 'device', 'codex', 'conversation', 'fingerprint', None,
                            'delivered', 'worker', self.now, self.now + 120, 'Accepted', None, 'reply'))
        migrated = Broker(legacy_dir, lambda: self.now)
        status = migrated.status('device', 'codex', 'conversation')['commands'][0]
        self.assertEqual(status['state'], 'delivered')
        self.assertNotIn('confirmation', status)
        with migrated.db() as db:
            self.assertIn('confirmation', {row[1] for row in db.execute('PRAGMA table_info(commands)')})


if __name__ == '__main__':
    unittest.main()

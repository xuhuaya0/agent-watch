import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from aw.control.broker import ControlError
from aw.control.diagnostics import Diagnostics, LIMIT, error_category
from aw.control.receipts import MAX_PENDING, TTL
from aw.control.worker import Worker


class Provider:
    def __init__(self):
        self.calls = []
        self.checks = []
        self.retired = []
        self.evidence = None
        self.outcome = TimeoutError('private input must never enter diagnostics')

    def snapshot(self):
        return [{'tool': 'codex', 'session_id': 'test-session', 'reply': True, 'requests': []}]

    def prepare_reply_receipt(self, command):
        return {'command_id': command['id'],
                'sha256': hashlib.sha256(command['action']['text'].encode()).hexdigest()}

    def check_reply_receipt(self, intent):
        self.checks.append(intent)
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence

    def retire_reply_receipt(self, intent):
        self.retired.append(intent)

    def execute(self, command):
        self.calls.append(command['id'])
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class WorkerReceiptTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = root / 'state.json'
        self.config = {'device_id': 'test-device', 'hub_url': 'http://127.0.0.1:1', 'token': 'synthetic'}
        self.provider = Provider()
        self.now = 1000.
        self.command = {'id': str(uuid.uuid4()), 'tool': 'codex', 'session_id': 'test-session',
                        'action': {'kind': 'reply', 'text': 'synthetic private message'}}
        self.worker = self.make_worker()

    def make_worker(self):
        worker = Worker(self.config, {'codex': self.provider}, self.path)
        worker.reply_receipts.clock = lambda: self.now
        return worker

    def dispatch(self):
        self.worker.exchange = lambda body: {'ok': True, 'receipts': [], 'command': self.command}
        self.worker.step()

    def acknowledge(self):
        self.worker.exchange = lambda body: {'ok': True, 'receipts': [r['id'] for r in body['receipts']]}
        self.worker.step()

    def test_intent_is_durable_before_native_write_and_contains_no_body(self):
        original = self.provider.execute
        def execute(command):
            saved = json.loads(self.path.read_text())
            self.assertIn(command['id'], saved['reply_receipts'])
            self.assertEqual(saved['receipts'][0]['state'], 'executing')
            self.assertNotIn(command['action']['text'], self.path.read_text())
            return original(command)
        self.provider.execute = execute
        self.dispatch()
        self.assertEqual(self.worker.state['receipts'][0]['state'], 'unknown')
        log = self.path.with_name('control-diagnostics.jsonl').read_text()
        self.assertNotIn('private input', log)
        self.assertIn('"category":"timeout"', log)
        self.assertIn('"phase":"native_execute"', log)

    def test_unknown_acknowledged_by_hub_still_reconciles_after_worker_restart(self):
        self.dispatch()
        self.acknowledge()
        self.assertEqual(self.worker.state['receipts'], [])
        self.assertIn(self.command['id'], self.worker.reply_receipts.pending)
        self.worker = self.make_worker()
        self.now += 6
        self.provider.evidence = {'state': 'delivered', 'evidence': 'native_record'}
        sent = []
        def exchange(body):
            sent.extend(body['receipts'])
            return {'ok': True, 'receipts': [r['id'] for r in body['receipts']]}
        self.worker.exchange = exchange
        self.worker.step()
        self.assertEqual(sent[0]['confirmation'], 'native_record')
        self.assertEqual(sent[0]['state'], 'delivered')
        self.assertIn('原生会话记录', sent[0]['detail'])
        self.assertEqual(self.provider.calls, [self.command['id']])
        self.assertFalse(self.worker.reply_receipts.pending)

    def test_late_ack_is_durable_if_hub_exchange_fails(self):
        self.dispatch()
        self.provider.evidence = {'state': 'delivered', 'evidence': 'late_ack'}
        def fail(body):
            raise ConnectionError('secret token and request body')
        self.worker.exchange = fail
        with self.assertRaises(ConnectionError):
            self.worker.step()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['receipts'][0]['confirmation'], 'late_ack')
        self.assertEqual(saved['receipts'][0]['state'], 'delivered')
        self.assertFalse(saved['reply_receipts'])
        self.assertEqual(self.provider.calls, [self.command['id']])
        log = self.path.with_name('control-diagnostics.jsonl').read_text()
        self.assertNotIn('secret token', log)
        self.assertIn('"phase":"exchange"', log)

    def test_absent_or_invalid_evidence_never_confirms_or_replays(self):
        self.dispatch()
        self.acknowledge()
        for evidence in (None, {}, {'state': 'delivered', 'evidence': 'guessed'},
                         {'state': 'rejected', 'evidence': 'native_record'}, TimeoutError('secret')):
            self.now += 6
            self.provider.evidence = evidence
            self.worker.exchange = lambda body: {'ok': True, 'receipts': [], 'command': self.command}
            self.worker.step()
            self.assertFalse(self.worker.state['receipts'])
            self.assertIn(self.command['id'], self.worker.reply_receipts.pending)
        self.assertEqual(self.provider.calls, [self.command['id']])

    def test_normal_success_retires_intent_and_never_uses_readback(self):
        self.provider.outcome = 'delivered'
        self.dispatch()
        self.assertFalse(self.worker.reply_receipts.pending)
        self.assertEqual(len(self.provider.retired), 1)
        self.acknowledge()
        self.assertEqual(self.provider.checks, [])

    def test_positive_no_write_rejection_retires_intent(self):
        self.provider.outcome = ControlError('reply_unavailable')
        self.dispatch()
        self.assertEqual(self.worker.state['receipts'][0]['state'], 'rejected')
        self.assertFalse(self.worker.reply_receipts.pending)

    def test_probe_failure_does_not_block_original_delivery(self):
        self.provider.outcome = 'delivered'
        def prepare(command):
            raise ValueError('secret request')
        self.provider.prepare_reply_receipt = prepare
        self.dispatch()
        self.assertEqual(self.worker.state['receipts'][0]['state'], 'delivered')
        self.assertFalse(self.worker.reply_receipts.pending)
        self.assertNotIn('secret request', self.path.with_name('control-diagnostics.jsonl').read_text())

    def test_expiry_stops_reading_without_claiming_failure_or_retrying(self):
        self.dispatch()
        self.now += TTL + 1
        self.acknowledge()
        self.assertFalse(self.worker.reply_receipts.pending)
        self.assertEqual(self.provider.checks, [])
        self.assertEqual(self.provider.calls, [self.command['id']])

    def test_reconciliation_is_bounded_and_preserves_existing_terminal_receipt(self):
        self.dispatch()
        first = self.worker.reply_receipts.pending[self.command['id']]
        for index in range(3):
            self.worker.reply_receipts.pending[str(uuid.uuid4())] = dict(first)
        self.provider.evidence = {'state': 'delivered', 'evidence': 'native_record'}
        self.worker.state['receipts'][0]['state'] = 'resolved'
        self.worker.reply_receipts.reconcile()
        self.assertLessEqual(len(self.provider.checks), 1)
        self.assertEqual(self.worker.state['receipts'][0]['state'], 'resolved')

    def test_crash_recovery_uses_readback_and_never_native_execute(self):
        self.worker.reply_receipts.prepare(self.command)
        self.worker.state['receipts'] = [{'id': self.command['id'], 'state': 'executing', 'detail': ''}]
        self.worker.save()
        self.worker = self.make_worker()
        self.provider.evidence = {'state': 'delivered', 'evidence': 'native_record'}
        self.worker.exchange = lambda body: {'ok': True, 'receipts': []}
        self.worker.step()
        self.assertEqual(self.worker.state['receipts'][0]['state'], 'delivered')
        self.assertEqual(self.provider.calls, [])

    def test_global_read_budget_survives_rapid_steps_and_restart(self):
        self.dispatch()
        first = self.worker.reply_receipts.pending[self.command['id']]
        for index in range(12):
            self.worker.reply_receipts.pending[str(uuid.uuid4())] = dict(first)
        self.worker.exchange = lambda body: {'ok': True, 'receipts': []}
        for _ in range(5):
            self.worker.step()
        self.assertEqual(len(self.provider.checks), 1)
        self.worker = self.make_worker()
        self.worker.exchange = lambda body: {'ok': True, 'receipts': []}
        self.worker.step()
        self.assertEqual(len(self.provider.checks), 1)
        self.now += 5
        self.worker.step()
        self.assertEqual(len(self.provider.checks), 2)

    def test_tracking_capacity_is_bounded_without_evicting_existing_uncertainty(self):
        self.dispatch()
        first = self.worker.reply_receipts.pending[self.command['id']]
        while len(self.worker.reply_receipts.pending) < MAX_PENDING:
            self.worker.reply_receipts.pending[str(uuid.uuid4())] = dict(first)
        self.command['id'] = str(uuid.uuid4())
        self.dispatch()
        self.assertEqual(len(self.worker.reply_receipts.pending), MAX_PENDING)
        self.assertNotIn(self.command['id'], self.worker.reply_receipts.pending)
        self.assertEqual(self.worker.state['receipts'][-1]['state'], 'unknown')

    def test_offline_confirmations_flush_in_bounded_batches_after_restart(self):
        # Twenty-one independently confirmed replies must not permanently exceed
        # Broker.exchange's maximum of twenty receipts after an outage.
        for index in range(21):
            command = {**self.command, 'id': str(uuid.uuid4())}
            self.worker.reply_receipts.prepare(command)
        self.worker.save()
        self.provider.evidence = {'state': 'delivered', 'evidence': 'native_record'}
        def offline(body):
            self.assertLessEqual(len(body['receipts']), 20)
            raise ConnectionError('offline')
        self.worker.exchange = offline
        for _ in range(21):
            self.now += 5
            with self.assertRaises(ConnectionError):
                self.worker.step()
        self.assertEqual(len(self.worker.state['receipts']), 21)
        self.worker = self.make_worker()
        batches = []
        def online(body):
            self.assertLessEqual(len(body['receipts']), 20)
            batches.append(len(body['receipts']))
            return {'ok': True, 'receipts': [r['id'] for r in body['receipts']]}
        self.worker.exchange = online
        self.worker.step(); self.worker.step()
        self.assertEqual(batches, [20, 1])
        self.assertEqual(self.worker.state['receipts'], [])
        self.assertEqual(self.provider.calls, [])

    def test_ack_cannot_remove_a_receipt_not_sent_in_current_batch(self):
        self.worker.state['receipts'] = [{'id': str(uuid.uuid4()), 'state': 'delivered', 'detail': ''}
                                         for _ in range(21)]
        last = self.worker.state['receipts'][-1]['id']
        self.worker.exchange = lambda body: {'ok': True, 'receipts': [last]}
        self.worker.step()
        self.assertEqual(len(self.worker.state['receipts']), 21)
        self.assertEqual(self.worker.state['receipts'][-1]['id'], last)

    def test_restore_registers_ids_without_executing_or_reading_native(self):
        self.dispatch()
        restored = []
        self.provider.restore_reply_receipts = restored.extend
        calls, checks = len(self.provider.calls), len(self.provider.checks)
        self.worker = self.make_worker()
        self.assertEqual(restored[0]['command_id'], self.command['id'])
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(len(self.provider.checks), checks)


class DiagnosticsTests(unittest.TestCase):
    def test_rotation_is_bounded_and_never_serializes_exception_or_command_body(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        logger = Diagnostics(root / 'state.json')
        logger.path.write_text('x' * LIMIT)
        command = {'id': str(uuid.uuid4()), 'tool': 'codex', 'session_id': 'private-session',
                   'action': {'kind': 'reply', 'text': 'private-input'}, 'token': 'private-token'}
        logger.record('exception', 'native_execute', command, RuntimeError('private-error'))
        text = logger.path.read_text()
        self.assertTrue(logger.path.with_suffix('.jsonl.1').exists())
        self.assertLess(len(text), 1000)
        self.assertNotIn('private', text)
        if os.name != 'nt':
            self.assertEqual(logger.path.stat().st_mode & 0o777, 0o600)

    def test_unrecognized_control_error_code_is_not_logged_verbatim(self):
        self.assertEqual(error_category(ControlError('private_secret_value')), 'control_error')
        self.assertEqual(error_category(ControlError('codex_owner_changed')), 'codex_owner_changed')

    def test_logging_failure_does_not_interrupt_delivery(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        logger = Diagnostics(root / 'state.json')
        logger.path.mkdir()
        logger.record('exception', 'native_execute', error=TimeoutError('secret'))

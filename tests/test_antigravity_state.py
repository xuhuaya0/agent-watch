import copy
import io
import json
import unittest

from aw.control.antigravity_interactions import NativeInteractions
from aw.control.antigravity_rpc import NativeRpc
from aw.control.antigravity_state import TAIL_STEPS
from aw.control.antigravity_settings import selected_model
from aw.control.broker import ControlError, encoded
import hashlib

SID = '11111111-1111-4111-8111-111111111111'
TID = '22222222-2222-4222-8222-222222222222'


def done():
    return {'type': 'CORTEX_STEP_TYPE_PLANNER_RESPONSE', 'status': 'CORTEX_STEP_STATUS_DONE',
            'plannerResponse': {'response': 'Synthetic result'}}


def question():
    return {'type': 'CORTEX_STEP_TYPE_GENERIC', 'status': 'CORTEX_STEP_STATUS_WAITING',
            'requestedInteraction': {'askQuestion': {'questions': [{'question': 'Choose',
                'options': [{'id': 'native-a', 'text': 'A'}, {'id': 'native-b', 'text': 'B'}]}]}}}


class Rpc:
    def __init__(self, total=1807):
        self.total = total
        self.summary = {'trajectoryId': TID, 'stepCount': total, 'status': 'CASCADE_RUN_STATUS_IDLE',
                        'waitingSteps': [], 'lastModifiedTime': 'synthetic-revision'}
        self.tail = [done() for _ in range(min(TAIL_STEPS, total))]
        self.calls = []; self.reads = 0; self.mutate = None
        self.model_config = {'plannerConfig': {'planModel': 'MODEL_SYNTHETIC'}}
        self.model_error = False

    def call(self, method, body):
        self.calls.append((method, copy.deepcopy(body)))
        if method == 'GetConversationMetadata': return {'trajectoryId': TID, 'numSteps': self.total}
        if method == 'GetAllCascadeTrajectories':
            self.reads += 1
            row = copy.deepcopy(self.summary)
            if self.reads % 2 == 0 and self.mutate: self.mutate(row)
            return {'trajectorySummaries': {SID: row}}
        if method == 'GetCascadeTrajectorySteps': return {'steps': copy.deepcopy(self.tail)}
        if method == 'GetTrajectoryTurns':
            if self.model_error: raise ControlError('native_session_unavailable')
            return {'turns': [{'startStepIndex': body['stepIndices'][0], 'firstStep': {
                'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'userInput': {'userConfig': copy.deepcopy(self.model_config)}}}]}
        if method == 'HandleCascadeUserInteraction': return {}
        raise AssertionError('Full history and unrecognised methods are forbidden')


class BoundedStateTests(unittest.TestCase):
    def test_long_idle_conversation_uses_only_summary_and_tail_with_native_epoch(self):
        rpc = Rpc(); row, _ = NativeInteractions(rpc).inspect(SID)
        self.assertTrue(row['reply']); self.assertEqual(row['delivery']['state'], 'idle')
        self.assertIn(('GetCascadeTrajectorySteps', {'cascadeId': SID, 'stepOffset': 1791,
            'trajectoryVerbosity': 2, 'disableRehydration': True}), rpc.calls)
        expected = hashlib.sha256(encoded([TID, [1806, 'CORTEX_STEP_TYPE_PLANNER_RESPONSE']]).encode()).hexdigest()
        self.assertEqual(row['delivery']['epoch'], expected)
        self.assertEqual(len(rpc.calls), 5)

    def test_waiting_question_before_tail_keeps_original_index_and_native_option_ids(self):
        rpc = Rpc(); rpc.summary['waitingSteps'] = [{'stepIndex': 7, 'step': question()}]
        provider = NativeInteractions(rpc)
        row, _ = provider.inspect(SID); request = row['requests'][0]
        self.assertEqual(request['id'], TID + ':7')
        self.assertFalse(row['reply']); self.assertEqual(row['delivery']['state'], 'waiting')
        provider.execute(SID, {'kind': 'answer', 'request_id': request['id'], 'revision': request['revision'],
            'answers': {'0': {'selected': ['B'], 'custom': ''}}})
        method, body = rpc.calls[-1]
        self.assertEqual(method, 'HandleCascadeUserInteraction')
        self.assertEqual(body['interaction']['stepIndex'], 7)
        self.assertEqual(body['interaction']['askQuestion']['responses'][0]['selectedOptionIds'], ['native-b'])

    def test_unrecognised_waiting_work_disables_reply_even_when_tail_is_all_done(self):
        rpc = Rpc(); rpc.summary['waitingSteps'] = [{'stepIndex': 11, 'step': {
            'status': 'CORTEX_STEP_STATUS_WAITING', 'type': 'FUTURE_NATIVE_INTERACTION'}}]
        row, _ = NativeInteractions(rpc).inspect(SID)
        self.assertFalse(row['reply']); self.assertEqual(row['requests'], [])
        self.assertEqual(row['delivery']['state'], 'waiting')

    def test_changed_summary_identity_count_pending_or_busy_state_fails_closed(self):
        changes = {'trajectoryId': SID, 'stepCount': 1808, 'status': 'CASCADE_RUN_STATUS_RUNNING',
                   'waitingSteps': [{'stepIndex': 2, 'step': question()}],
                   'notFullyIdle': True, 'lastModifiedTime': 'new-revision'}
        for key, value in changes.items():
            with self.subTest(key=key):
                rpc = Rpc(); rpc.mutate = lambda row, k=key, v=value: row.update({k: v})
                with self.assertRaises(ControlError): NativeInteractions(rpc).inspect(SID)
                self.assertFalse(any(m.startswith('Handle') for m, _ in rpc.calls))

    def test_partial_empty_extra_or_inconsistent_tail_fails_closed(self):
        for tail in ([], [done()], [done()] * 17, [None] * 16,
                     [question()] + [done()] * 15):
            with self.subTest(tail_length=len(tail)):
                rpc = Rpc(); rpc.tail = tail
                with self.assertRaises(ControlError): NativeInteractions(rpc).inspect(SID)

    def test_bad_pending_indices_and_shapes_fail_closed(self):
        for pending in (None, {}, [{'stepIndex': -1, 'step': question()}],
                        [{'stepIndex': True, 'step': question()}],
                        [{'stepIndex': 1807, 'step': question()}],
                        [{'stepIndex': 2, 'step': done()}],
                        [{'stepIndex': 2, 'step': question()}] * 2):
            with self.subTest(pending=pending):
                rpc = Rpc(); rpc.summary['waitingSteps'] = pending
                with self.assertRaises(ControlError): NativeInteractions(rpc).inspect(SID)

    def test_new_empty_trajectory_requires_explicit_native_idle(self):
        rpc = Rpc(0); row, _ = NativeInteractions(rpc).inspect(SID)
        self.assertTrue(row['reply']); self.assertEqual(row['delivery'], {'state': 'idle', 'steer': False, 'epoch': 'none'})
        self.assertFalse(any(m == 'GetCascadeTrajectorySteps' for m, _ in rpc.calls))
        for status in (None, True, 0, 'FUTURE_STATUS'):
            rpc = Rpc(0); rpc.summary['status'] = status
            with self.assertRaises(ControlError): NativeInteractions(rpc).inspect(SID)

    def test_active_children_cannot_be_claimed_idle(self):
        for field in ('notFullyIdle', 'hasActiveChildren', 'hasRunningSubagents'):
            rpc = Rpc(); rpc.summary[field] = True
            row, _ = NativeInteractions(rpc).inspect(SID)
            self.assertEqual(row['delivery']['state'], 'running')

    def test_idle_tail_without_completion_cannot_release_waiting_reply(self):
        rpc = Rpc()
        rpc.tail = [{'type': 'CORTEX_STEP_TYPE_GENERIC', 'status': 'CORTEX_STEP_STATUS_DONE'}] * 16
        row, _ = NativeInteractions(rpc).inspect(SID)
        self.assertFalse(row['reply'])
        self.assertEqual(row['delivery'], {'state': 'unknown', 'steer': False, 'epoch': 'none'})

    def test_old_completion_before_latest_user_input_cannot_claim_idle(self):
        rpc = Rpc(10); rpc.summary['lastUserInputStepIndex'] = 9
        rpc.tail[-1] = {'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'status': 'CORTEX_STEP_STATUS_DONE'}
        row, _ = NativeInteractions(rpc).inspect(SID)
        self.assertFalse(row['reply']); self.assertEqual(row['delivery']['state'], 'unknown')
        self.assertNotEqual(row['delivery']['epoch'], 'none')
        rpc.summary['lastUserInputStepIndex'] = 8; rpc.tail[-1] = done()
        self.assertEqual(NativeInteractions(rpc).inspect(SID)[0]['delivery']['state'], 'idle')
        rpc.summary['lastUserInputStepIndex'] = 9
        self.assertEqual(NativeInteractions(rpc).inspect(SID)[0]['delivery']['state'], 'unknown')

    def test_invalid_latest_user_index_fails_closed(self):
        for index in (-1, True, 10, '1'):
            rpc = Rpc(10); rpc.summary['lastUserInputStepIndex'] = index
            with self.assertRaises(ControlError): NativeInteractions(rpc).inspect(SID)

    def test_model_selection_uses_only_one_full_first_step_and_never_stale_metadata(self):
        rpc = Rpc(); context = {}
        NativeInteractions(rpc).inspect(SID, context=context)
        self.assertEqual(selected_model(context), 'MODEL_SYNTHETIC')
        self.assertIn(('GetTrajectoryTurns', {'cascadeId': SID, 'stepIndices': [0], 'trajectoryVerbosity': 3}), rpc.calls)
        rpc.model_config = {'plannerConfig': {'planModel': 'MODEL_UNSPECIFIED'}}
        NativeInteractions(rpc).inspect(SID, context=context)
        context['metadata'] = {'staticConfig': {'cascadeConfig': {'plannerConfig': {'planModel': 'MODEL_OLD'}}}}
        self.assertIsNone(selected_model(context))
        rpc.model_error = True
        row, _ = NativeInteractions(rpc).inspect(SID, context=context)
        self.assertTrue(row['reply']); self.assertIsNone(selected_model(context))

    def test_oversized_single_step_retains_existing_transport_bound(self):
        class Opener:
            def __init__(self): self.calls = []
            def open(self, request, timeout):
                self.calls.append(request)
                method = request.full_url.rsplit('/', 1)[-1]
                if method == 'GetAllCascadeTrajectories':
                    body = {'trajectorySummaries': {SID: {'trajectoryId': TID, 'stepCount': 1, 'status': 'CASCADE_RUN_STATUS_IDLE'}}}
                elif method == 'GetConversationMetadata': body = {'trajectoryId': TID, 'numSteps': 1}
                else: return io.BytesIO(b' ' * (4 * 1024 * 1024 + 1))
                return io.BytesIO(json.dumps(body).encode())
        opener = Opener()
        rpc = NativeRpc({'ANTIGRAVITY_LS_ADDRESS': 'localhost:1234', 'ANTIGRAVITY_CSRF_TOKEN': 'fixture'}, opener)
        with self.assertRaises(ControlError): NativeInteractions(rpc).inspect(SID)
        self.assertEqual(len(opener.calls), 3)


if __name__ == '__main__': unittest.main()

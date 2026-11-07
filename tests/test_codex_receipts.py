import json
import io
import struct
import threading
import unittest
from unittest.mock import patch

from aw.control.broker import ControlError
from aw.control.codex_ipc import CodexDesktop, DesktopRpc
from aw.control import codex_receipts as receipts
from test_codex_ipc import FakeDesktop, SID

NOW = 1700000000.25
COMMAND = {'id': 'panel-message-id', 'session_id': SID,
           'action': {'kind': 'reply', 'text': 'Synthetic reply\n第二行', 'mode': 'after-turn'}}


class LateAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.rpc = DesktopRpc.__new__(DesktopRpc)
        self.rpc.pending = {}; self.rpc.reply_requests = {}; self.rpc.reply_confirmed = {}
        self.rpc.reply_watched = set()
        self.rpc.lock = threading.RLock(); self.rpc.client_id = 'fixture-client'
        self.sent = []; self.rpc.send = self.sent.append

    def dispatch_timeout(self):
        request = {'conversationId': SID, 'turnStart': {'request': {
            'clientUserMessageId': COMMAND['id'], 'input': [{'type': 'text', 'text': COMMAND['action']['text']}]}}}
        class ImmediateTimeout:
            def wait(self, timeout): return False
        with patch('aw.control.codex_ipc.threading.Event', ImmediateTimeout):
            with self.assertRaises(TimeoutError):
                self.rpc.call('thread-follower-start-turn', request, owner='fixture-owner')
        self.assertEqual(self.rpc.pending, {})
        return self.sent[-1]['requestId']

    def response(self, request_id, **changes):
        return {'requestId': request_id, 'resultType': 'success', 'handledByClientId': 'fixture-owner',
                'result': {'result': {'turn': {'id': 'fixture-turn'}}}, **changes}

    def test_late_success_retains_exact_request_owner_and_command_identity_without_text(self):
        identity = self.dispatch_timeout()
        self.assertNotIn(COMMAND['action']['text'], json.dumps(self.rpc.reply_requests))
        self.rpc._remember_reply(self.response(identity))
        self.assertEqual(self.rpc.reply_receipt(SID, COMMAND['id']),
                         {'state': 'delivered', 'evidence': 'late_ack'})
        self.assertIsNone(self.rpc.reply_receipt('other-session', COMMAND['id']))
        self.assertIsNone(self.rpc.reply_receipt(SID, 'other-command'))
        self.assertEqual(len(self.sent), 1)
        self.rpc.retire_reply_receipt(SID, COMMAND['id'])
        self.assertIsNone(self.rpc.reply_receipt(SID, COMMAND['id']))
        self.assertEqual(self.rpc.reply_requests, {})

    def test_wrong_request_owner_error_or_empty_result_do_not_confirm(self):
        identity = self.dispatch_timeout()
        for message in (self.response('other-request'), self.response(identity, handledByClientId='other-owner'),
                        self.response(identity, resultType='error'), self.response(identity, result={}),
                        self.response(identity, result={'result': None})):
            self.rpc._remember_reply(message)
            self.assertIsNone(self.rpc.reply_receipt(SID, COMMAND['id']))

    def test_disconnect_waking_pending_call_has_distinct_safe_error_category(self):
        rpc = self.rpc
        rpc.connected = True
        class DisconnectedWait:
            def wait(self, timeout):
                rpc.connected = False
                return True  # read() wakes pending calls, without a response.
        with patch('aw.control.codex_ipc.threading.Event', DisconnectedWait):
            with self.assertRaises(ConnectionError):
                rpc.call('thread-follower-steer-turn',
                    {'conversationId': SID, 'clientUserMessageId': COMMAND['id']}, owner='fixture-owner')
        self.assertEqual(rpc.pending, {})
        self.assertIsNone(rpc.reply_receipt(SID, COMMAND['id']))
        self.assertEqual(len(self.sent), 1)

    def test_read_loop_accepts_late_framed_response_after_waiter_was_removed(self):
        identity = self.dispatch_timeout()
        raw = json.dumps({'type': 'response', **self.response(identity)}).encode()
        stream = io.BytesIO(struct.pack('<I', len(raw)) + raw)
        def read_exact(count):
            value = stream.read(count)
            if len(value) != count:
                raise EOFError()
            return value
        self.rpc.read_exact = read_exact; self.rpc.connected = True
        events = []; self.rpc.callback = events.append
        self.rpc.read()
        self.assertEqual(self.rpc.reply_receipt(SID, COMMAND['id']),
                         {'state': 'delivered', 'evidence': 'late_ack'})
        self.assertEqual(events[-1]['method'], 'agent-watch/disconnected')
        self.assertEqual(len(self.sent), 1)

    def test_watch_is_bounded_and_expires_without_resending(self):
        for number in range(200):
            self.rpc._track_reply(str(number), 'thread-follower-steer-turn',
                {'conversationId': SID, 'clientUserMessageId': str(number)}, 'fixture-owner')
        self.assertLessEqual(len(self.rpc.reply_requests), 128)
        for value in self.rpc.reply_requests.values(): value['until'] = 0
        self.rpc._prune_replies()
        self.assertEqual(self.rpc.reply_requests, {})
        self.assertEqual(self.sent, [])

    def test_registered_pending_ack_survives_unrelated_rpc_history_and_polling_backlog(self):
        self.rpc.watch_reply_receipt(SID, COMMAND['id'])
        identity = self.dispatch_timeout()
        until = self.rpc.reply_requests[identity]['until']
        for number in range(200):
            self.rpc._track_reply(str(number), 'thread-follower-steer-turn',
                {'conversationId': SID, 'clientUserMessageId': str(number)}, 'fixture-owner')
        self.assertIn(identity, self.rpc.reply_requests)
        self.assertLessEqual(len(self.rpc.reply_requests), 128)
        with patch('aw.control.codex_ipc.time.monotonic', return_value=until - 86400 + 650):
            self.rpc._remember_reply(self.response(identity))
            self.assertIsNotNone(self.rpc.reply_receipt(SID, COMMAND['id']))


class NativeIdentityTests(unittest.TestCase):
    def provider(self):
        provider = CodexDesktop('/unused', lambda: [SID], FakeDesktop, background=False)
        self.addCleanup(provider.close); provider.refresh()
        return provider

    def test_only_explicit_accepted_client_identity_is_evidence(self):
        item = {'type': 'steeringUserMessage', 'clientUserMessageId': 'optimistic-id', 'status': 'accepted',
                'serverUserMessageId': 'engine-item-id', 'serverClientUserMessageId': COMMAND['id']}
        self.assertEqual(receipts.accepted_ids(item), {COMMAND['id']})
        self.assertEqual(receipts.accepted_ids({'type': 'userMessage', 'id': 'engine-item-id',
                                               'clientId': COMMAND['id']}), {COMMAND['id']})
        for value in ({**item, 'status': 'pending'}, {**item, 'status': 'rejected'},
                      {**item, 'serverUserMessageId': None}, {**item, 'serverClientUserMessageId': None},
                      {'type': 'userMessage', 'id': COMMAND['id']},
                      {'type': 'userMessage', 'clientUserMessageId': COMMAND['id']},
                      {'type': 'steeringUserMessage', 'clientUserMessageId': COMMAND['id'], 'status': 'accepted'}):
            self.assertEqual(receipts.accepted_ids(value), set())
        patch_value = {'op': 'add', 'path': ['turnHistory', 'history', 'entitiesByKey', 'turn', 'items', 0], 'value': item}
        self.assertEqual(receipts.patch_ids([patch_value]), {COMMAND['id']})
        self.assertEqual(receipts.patch_ids([{**patch_value, 'path': patch_value['path'] + ['output']}]), set())

    def test_provider_checks_only_original_scope_and_does_not_issue_native_calls(self):
        ids = [SID]
        provider = CodexDesktop('/unused', lambda: ids, FakeDesktop, background=False)
        self.addCleanup(provider.close); provider.refresh()
        intent = provider.prepare_reply_receipt(COMMAND)
        provider.rpc.history = {'turns': [{'items': [{'type': 'userMessage',
            'clientId': COMMAND['id'], 'id': 'engine-item-id'}]}]}
        provider.rpc.follow(SID, provider.rpc.owner)
        calls = len(provider.rpc.calls)
        self.assertEqual(provider.check_reply_receipt(intent), {'state': 'delivered', 'evidence': 'native_record'})
        self.assertEqual(len(provider.rpc.calls), calls)
        ids.clear(); self.assertIsNone(provider.check_reply_receipt(intent))
        ids.append(SID); provider.retire_reply_receipt(intent)
        self.assertIsNone(provider.check_reply_receipt(intent))

    def test_optimistic_turn_params_without_native_user_item_do_not_confirm(self):
        state = {'turns': [{'params': {'clientUserMessageId': COMMAND['id'],
                                      'input': [{'type': 'text', 'text': COMMAND['action']['text']}]},
                            'items': [{'type': 'modelChanged'}]}]}
        self.assertEqual(receipts.snapshot_ids(state), set())

    def test_intent_has_only_identity_and_does_not_include_body_or_path(self):
        intent = self.provider().prepare_reply_receipt(COMMAND)
        self.assertEqual(set(intent), {'version', 'command_id', 'session_id', 'prepared_at'})
        self.assertNotIn(COMMAND['action']['text'], json.dumps(intent, ensure_ascii=False))

    def test_unrelated_large_history_cannot_evict_registered_pending_identity(self):
        provider = self.provider(); intent = provider.prepare_reply_receipt(COMMAND)
        items = [{'type': 'userMessage', 'clientId': str(number)} for number in range(1000)]
        items.insert(500, {'type': 'userMessage', 'clientId': COMMAND['id']})
        provider.rpc.history = {'turns': [{'items': items}]}
        provider.rpc.follow(SID, provider.rpc.owner)
        self.assertEqual(len(provider.reply_confirmed), 1)
        self.assertEqual(provider.check_reply_receipt(intent)['evidence'], 'native_record')

    def test_restore_registers_without_calls_and_requests_one_snapshot_for_same_session(self):
        provider = self.provider()
        intents = [receipts.prepare({**COMMAND, 'id': str(number)}) for number in range(3)]
        original = provider.rpc.follow; follows = []
        def follow(sid, owner, following=True):
            follows.append((sid, owner, following)); return original(sid, owner, following)
        provider.rpc.follow = follow
        provider.restore_reply_receipts(intents)
        self.assertEqual(follows, [])
        for intent in intents:
            self.assertIsNone(provider.check_reply_receipt(intent))
        provider.restore_reply_receipts(intents)
        for intent in intents:
            self.assertIsNone(provider.check_reply_receipt(intent))
            provider.retire_reply_receipt(intent)
        self.assertEqual(len(follows), 1)
        self.assertEqual(provider.reply_watched, {})
        self.assertFalse(any(method.endswith(('start-turn', 'steer-turn')) for method, _, _ in provider.rpc.calls))

    def test_same_text_different_command_and_manual_message_cannot_share_confirmation(self):
        provider = self.provider()
        first = provider.prepare_reply_receipt(COMMAND)
        second = provider.prepare_reply_receipt({**COMMAND, 'id': 'another-command'})
        provider.rpc.history = {'turns': [{'items': [
            {'type': 'userMessage', 'clientId': COMMAND['id'], 'content': [{'type': 'text', 'text': COMMAND['action']['text']}]},
            {'type': 'userMessage', 'clientId': 'manual-message', 'content': [{'type': 'text', 'text': COMMAND['action']['text']}]},
        ]}]}
        provider.rpc.follow(SID, provider.rpc.owner)
        self.assertIsNotNone(provider.check_reply_receipt(first))
        self.assertIsNone(provider.check_reply_receipt(second))

    def test_old_owner_or_foreign_session_snapshot_cannot_confirm(self):
        provider = self.provider(); intent = provider.prepare_reply_receipt(COMMAND)
        provider.owners[SID] = 'new-owner'
        message = {'method': 'thread-stream-state-changed', 'sourceClientId': 'desktop-owner', 'version': 11,
                   'params': {'hostId': 'local', 'conversationId': SID,
                     'change': {'type': 'snapshot', 'revision': 10,
                       'conversationState': {'id': SID, 'requests': [], 'turns': [{'items': [
                         {'type': 'userMessage', 'clientId': COMMAND['id']}]}]}}}}
        provider.event(message)
        self.assertIsNone(provider.check_reply_receipt(intent))
        message['sourceClientId'] = 'new-owner'; message['params']['conversationId'] = 'other-session'
        provider.event(message)
        self.assertNotIn((SID, COMMAND['id']), provider.reply_confirmed)

    def test_capacity_does_not_evict_existing_watches_and_io_errors_remain_classified(self):
        provider = self.provider()
        first = provider.prepare_reply_receipt(COMMAND)
        for number in range(127):
            self.assertIsNotNone(provider.prepare_reply_receipt({**COMMAND, 'id': str(number)}))
        self.assertIsNone(provider.prepare_reply_receipt({**COMMAND, 'id': 'overflow'}))
        self.assertIn((SID, COMMAND['id']), provider.reply_watched)
        from types import SimpleNamespace
        provider.history_reader = SimpleNamespace(path=lambda sid: (_ for _ in ()).throw(OSError('private-path')))
        with self.assertRaises(OSError): provider.check_reply_receipt(first)
        provider.history_reader.path = lambda sid: (_ for _ in ()).throw(ControlError('native_session_archived'))
        self.assertIsNone(provider.check_reply_receipt(first))

    def test_known_items_and_turn_container_patches_keep_accepted_server_identity(self):
        item = {'type': 'steeringUserMessage', 'clientUserMessageId': 'optimistic', 'status': 'accepted',
                'serverUserMessageId': 'engine-id', 'serverClientUserMessageId': COMMAND['id']}
        base = ['turnHistory', 'history', 'entitiesByKey', 'turn']
        for path, value in ((base + ['items'], [item]), (base, {'items': [item]}),
                            (['turns', 0, 'items'], [item]), (['turns', 0], {'items': [item]})):
            self.assertEqual(receipts.patch_ids([{'op': 'replace', 'path': path, 'value': value}]), {COMMAND['id']})
        self.assertEqual(receipts.patch_ids([{'op': 'replace', 'path': base + ['params'],
                                             'value': {'items': [item]}}]), set())

    def test_canonical_history_and_live_overlay_both_supply_native_item_identity(self):
        state = {'turnHistory': {'kind': 'canonical', 'history': {'entitiesByKey': {
            'old': {'items': [{'type': 'userMessage', 'clientId': 'earlier'}]}}}},
                 'turns': [{'items': [{'type': 'userMessage', 'clientId': COMMAND['id']}]}]}
        self.assertEqual(receipts.snapshot_ids(state, {COMMAND['id']}), {COMMAND['id']})


if __name__ == '__main__':
    unittest.main()

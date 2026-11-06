import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from aw.control.codex_ipc import CodexDesktop, patch_requests
from aw.control.sessions import CodexScope
from aw.control.broker import ControlError

SID = 'c0c039d9-2cf2-4c38-ad8a-ffb06685aaab'


class FakeDesktop:
    def __init__(self, path, event):
        self.event = event
        self.connected = True
        self.calls = []
        self.requests = []
        self.history = {}
        self.revision = 1
        self.owner = 'desktop-owner'

    def call(self, method, params, owner=None, timeout=8):
        self.calls.append((method, copy.deepcopy(params), owner))
        if method == 'thread-owner-discovery':
            self.follow(SID, self.owner)
        if method == 'thread-follower-start-turn':
            # The native UI scans text spans directly, before any engine
            # round-trip can supply defaults. A missing collection must fail.
            self.rendered_text_elements = [span for item in params['turnStart']['request']['input']
                                           if item['type'] == 'text' for span in item['text_elements']]
        result = {'result': {'turn': {'id': 'native-turn'}}}
        if method.endswith('approval-decision') or method.endswith('submit-user-input'):
            self.requests = []
            self.follow(SID, self.owner)
            result = {'ok': True}
        if method == 'thread-follower-steer-turn':
            turn = self.history['turnHistory']['history']['entitiesByKey']['tail']
            turn['items'].append({'type': 'steeringUserMessage', 'status': 'accepted', 'input': params['input']})
            self.follow(SID, self.owner)
        return {'handledByClientId': self.owner, 'result': result}

    def follow(self, sid, owner, following=True):
        if not following:
            return
        self.revision += 1
        self.event({'method': 'thread-stream-state-changed', 'sourceClientId': owner, 'version': 11,
                    'params': {'hostId': 'local', 'conversationId': sid,
                               'change': {'type': 'snapshot', 'revision': self.revision,
                                          'conversationState': {'id': sid, 'requests': self.requests, **self.history}}}})

    def close(self):
        self.connected = False


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.ids = [SID]
        self.p = CodexDesktop('/unused', lambda: list(self.ids), FakeDesktop, background=False)
        self.p.refresh()
        self.addCleanup(self.p.close)

    def approval(self):
        self.p.rpc.requests = [{'id': 42, 'method': 'item/commandExecution/requestApproval',
                                'params': {'threadId': SID, 'turnId': 't', 'command': 'git status', 'cwd': '/project'}}]
        self.p.rpc.follow(SID, self.p.rpc.owner)

    def action(self):
        req = self.p.snapshot()[0]['requests'][0]
        return {'kind': 'approval', 'request_id': req['id'], 'revision': req['revision'], 'decision': 'approve'}

    def test_reply_routes_to_confirmed_owner_inherits_settings(self):
        self.assertTrue(self.p.snapshot()[0]['reply'])
        command = {'id': 'dedup', 'session_id': SID, 'action': {'kind': 'reply', 'text': 'Hello & $(literal)'}}
        self.assertEqual(self.p.execute(command), 'delivered')
        method, params, owner = self.p.rpc.calls[-1]
        self.assertEqual(method, 'thread-follower-start-turn')
        self.assertEqual(owner, 'desktop-owner')
        self.assertEqual(params['turnStart']['request'], {'threadId': SID, 'input': [{'type': 'text', 'text': 'Hello & $(literal)', 'text_elements': []}], 'clientUserMessageId': 'dedup'})
        self.assertEqual(params['turnStart']['context'], {'inheritThreadSettings': True})
        self.assertEqual(self.p.rpc.rendered_text_elements, [])

    def test_multiline_reply_renders_without_fabricated_spans_or_changed_text(self):
        text = '请继续\n[说明](https://example.test) ![示例](/example/image.png)\n'
        self.assertEqual(self.p.execute({'id': 'multiline', 'session_id': SID,
            'action': {'kind': 'reply', 'text': text}}), 'delivered')
        request = self.p.rpc.calls[-1][1]['turnStart']['request']
        self.assertEqual(request['input'][0]['text'], text)
        self.assertEqual(self.p.rpc.rendered_text_elements, [])
        self.assertEqual(request['clientUserMessageId'], 'multiline')

    def test_approval_uses_original_numeric_request_id_and_one_shot(self):
        self.approval()
        self.assertFalse(self.p.snapshot()[0]['reply'])
        self.assertEqual(self.p.execute({'session_id': SID, 'action': self.action()}), 'resolved')
        self.assertEqual(self.p.rpc.calls[-1], ('thread-follower-command-approval-decision',
                         {'conversationId': SID, 'requestId': 42, 'decision': 'accept'}, 'desktop-owner'))

    def test_expired_and_changed_approvals_never_sent(self):
        self.approval()
        action = self.action()
        self.p.rpc.requests[0]['params']['command'] = 'different command'
        with self.assertRaisesRegex(ControlError, 'request_expired'):
            self.p.execute({'session_id': SID, 'action': action})
        self.assertFalse(any(m.endswith('approval-decision') for m, _, _ in self.p.rpc.calls))

    def test_unknown_requests_block_reply(self):
        self.p.rpc.requests = [{'id': 'unknown', 'method': 'item/permissions/requestApproval', 'params': {'threadId': SID}}]
        row = self.p.session_snapshot(SID)[0]
        self.assertFalse(row['reply'])
        self.assertEqual(row['requests'], [])
        self.assertTrue(row['reason'])

    def test_settings_follow_native_snapshot_without_extra_rpc_or_writes(self):
        self.p.rpc.history = {'latestThreadSettings': {
            'model': 'model-a', 'effort': 'medium',
            'sandboxPolicy': {'type': 'readOnly'}, 'approvalPolicy': 'never'}}
        self.p.rpc.follow(SID, self.p.rpc.owner)
        count = len(self.p.rpc.calls)
        settings = self.p.snapshot()[0]['settings']
        self.assertEqual(settings['model'], 'model-a')
        self.assertEqual(settings['permission'], '只读 · 需要批准时拒绝')
        self.assertEqual(len(self.p.rpc.calls), count)
        self.p.rpc.history = {}
        self.p.rpc.follow(SID, self.p.rpc.owner)
        self.assertNotIn('settings', self.p.snapshot()[0])

    def test_questions_use_native_options_and_ids(self):
        self.p.rpc.requests = [{'id': 'q1', 'method': 'item/tool/requestUserInput', 'params': {
            'threadId': SID, 'questions': [{'id': 'color', 'question': 'Color?', 'options': [{'label': 'Blue'}], 'isOther': True}]}}]
        req = self.p.session_snapshot(SID)[0]['requests'][0]
        action = {'kind': 'answer', 'request_id': req['id'], 'revision': req['revision'],
                  'answers': {'color': {'selected': ['Blue'], 'custom': ''}}}
        self.assertEqual(self.p.execute({'session_id': SID, 'action': action}), 'resolved')
        self.assertEqual(self.p.rpc.calls[-1][1]['response'], {'answers': {'color': {'answers': ['Blue']}}})

    def test_async_question_steers_original_turn_and_is_removed_after_acceptance(self):
        from test_codex_questions import native
        self.p.rpc.history = native()
        req = self.p.session_snapshot(SID)[0]['requests'][0]
        self.assertFalse(self.p.snapshot()[0]['reply'])
        action = {'kind': 'answer', 'request_id': req['id'], 'revision': req['revision'],
                  'answers': {q['id']: {'selected': [], 'custom': 'Blue'} for q in req['questions']}}
        self.assertEqual(self.p.execute({'id': 'dedup-async', 'session_id': SID, 'action': action}), 'resolved')
        method, params, owner = self.p.rpc.calls[-1]
        self.assertEqual((method, owner), ('thread-follower-steer-turn', 'desktop-owner'))
        self.assertEqual(params['input'][0]['text_elements'], [])
        self.assertEqual(params['clientUserMessageId'], 'dedup-async')
        self.assertEqual(params['restoreMessage']['context'], {})
        self.assertTrue(params['input'][0]['text'].startswith('<send_user_message_question_reply>'))
        self.assertEqual(self.p.snapshot()[0]['requests'], [])
        with self.assertRaisesRegex(ControlError, 'request_expired'):
            self.p.execute({'id': 'duplicate', 'session_id': SID, 'action': action})

    def test_revocation_and_disconnect_remove_capabilities_immediately(self):
        self.ids.clear()
        self.assertEqual(self.p.snapshot(), [])
        self.assertEqual(self.p.session_snapshot(SID), [])
        self.ids.append(SID)
        self.p.event({'method': 'agent-watch/disconnected'})
        self.assertEqual(self.p.snapshot(), [])

    def test_wrong_host_owner_version_or_patch_gap_cannot_publish_actions(self):
        event = {'method': 'thread-stream-state-changed', 'sourceClientId': 'other', 'version': 11,
                 'params': {'hostId': 'local', 'conversationId': SID,
                            'change': {'type': 'snapshot', 'revision': 2, 'conversationState': {'id': SID, 'requests': []}}}}
        self.p.states.clear()
        self.p.event(event)
        self.assertEqual(self.p.snapshot(), [])
        event['sourceClientId'] = self.p.rpc.owner
        event['params']['hostId'] = 'remote'
        self.p.event(event)
        self.assertEqual(self.p.snapshot(), [])
        event['params']['hostId'] = 'local'
        self.p.event(event)
        self.assertEqual(len(self.p.snapshot()), 1)
        event['params']['change'] = {'type': 'patches', 'baseRevision': 1, 'revision': 3, 'patches': []}
        self.p.event(event)
        self.assertEqual(self.p.snapshot(), [])
        self.p.rpc.follow(SID, self.p.rpc.owner)
        event['version'] = 12
        self.p.event(event)
        self.assertEqual(self.p.snapshot(), [])


class PatchTests(unittest.TestCase):
    def test_patches_do_not_mutate_original_or_store_conversation_content(self):
        old = [{'id': 1, 'params': {'command': 'old'}}]
        new = patch_requests(old, [{'op': 'replace', 'path': ['requests', 0, 'params', 'command'], 'value': 'new'},
                                   {'op': 'replace', 'path': ['turns'], 'value': ['private content']}])
        self.assertEqual(old[0]['params']['command'], 'old')
        self.assertEqual(new, [{'id': 1, 'params': {'command': 'new'}}])
        self.assertEqual(patch_requests(new, [{'op': 'remove', 'path': ['requests', 0]}]), [])
        with self.assertRaises(ValueError):
            patch_requests(new, [{'op': 'replace', 'path': ['requests', -1], 'value': {}}])


class ScopeTests(unittest.TestCase):
    def test_all_scope_uses_native_database_excludes_archived_and_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root/'control.json'
            cfg.write_text(json.dumps({'codex': {'scope': 'all', 'data_root': tmp}}))
            db = sqlite3.connect(root/'state_5.sqlite')
            db.execute('create table threads(id,source,archived,updated_at)')
            db.executemany('insert into threads values(?,?,?,?)', [
                (SID, 'vscode', 0, 1), ('00000000-0000-0000-0000-000000000001', 'vscode', 1, 2),
                ('00000000-0000-0000-0000-000000000002', '{"subagent":{}}', 0, 3)])
            db.commit()
            db.close()
            self.assertEqual(CodexScope(cfg)(), [SID])
            cfg.write_text(json.dumps({'codex': {'session_ids': []}}))
            self.assertEqual(CodexScope(cfg)(), [])


if __name__ == '__main__':
    unittest.main()

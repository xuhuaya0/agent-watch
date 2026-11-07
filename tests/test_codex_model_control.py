import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from aw.control.broker import ControlError, normalize_sessions, validate_action
from aw.control.codex_ipc import CodexDesktop
from aw.control.codex_policy import FIELDS
from aw.control.worker import Worker
from test_codex_ipc import FakeDesktop, SID


class Scope:
    def __call__(self):
        return [SID]

    def creation_directories(self):
        return {SID: '/synthetic/project'}


class Catalog:
    def __init__(self):
        self.calls = []
        self.before_fresh = lambda: None
        self.data = {'revision': 'catalog-v1', 'config': {}, 'requirements': {}, 'profiles': [],
            'models': [
                {'value': 'model-a', 'label': 'Model A', 'efforts': ['low', 'high'], 'default_effort': 'low'},
                {'value': 'model-b', 'label': 'Model B', 'efforts': ['medium', 'high'], 'default_effort': 'medium'}]}

    def get(self, cwd, provider, fresh=False):
        self.calls.append((cwd, provider, fresh))
        if fresh:
            self.before_fresh()
        return copy.deepcopy(self.data)


class SettingsDesktop(FakeDesktop):
    def __init__(self, path, event):
        super().__init__(path, event)
        self.history = {'latestModel': 'model-a', 'latestReasoningEffort': 'low',
            'threadRuntimeStatus': {'type': 'idle'},
            'latestThreadSettings': {'model': 'model-a', 'effort': 'low',
                'approvalPolicy': 'on-request', 'approvalsReviewer': 'user',
                'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': ['/synthetic/project'],
                    'networkAccess': False, 'excludeSlashTmp': False, 'excludeTmpdirEnvVar': False},
                'permissions': None, 'activePermissionProfile': None}}
        self.result = {'applied': True}
        self.updated = False
        self.readback = None
        self.after_update = lambda: None
        self.settings_available = True

    def call(self, method, params, owner=None, timeout=8):
        if method == 'thread-follower-read-model-settings':
            self.calls.append((method, copy.deepcopy(params), owner))
            settings = self.history['latestThreadSettings']
            result = {'model': settings['model'], 'reasoningEffort': settings.get('effort'),
                'modelProvider': 'openai', 'resumeState': 'resumed', 'mode': 'default',
                'daybreakEnabled': False, 'threadSource': 'user'}
            if self.updated and self.readback:
                result.update(self.readback)
            return {'handledByClientId': self.owner,
                    'result': {'settings': result if self.settings_available else None}}
        if method == 'thread-follower-update-thread-settings':
            self.calls.append((method, copy.deepcopy(params), owner))
            self.updated = True
            if isinstance(self.result, dict) and self.result.get('applied') is True:
                self.history['latestThreadSettings'].update(copy.deepcopy(params['threadSettings']))
                self.after_update()
                self.follow(SID, self.owner)
            return {'handledByClientId': self.owner, 'result': copy.deepcopy(self.result)}
        return super().call(method, params, owner, timeout)


class ModelControlTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog()
        self.provider = CodexDesktop('/unused', Scope(), SettingsDesktop,
                                     background=False, catalog=self.catalog)
        self.provider.refresh()
        self.addCleanup(self.provider.close)

    def command(self):
        row = self.provider.session_snapshot(SID)[0]
        row = normalize_sessions([row])[('codex', SID)]
        action = {'kind': 'model', 'revision': row['model']['revision'], 'model': 'model-b', 'effort': 'high'}
        validate_action(row, action)
        return {'id': 'model-command', 'tool': 'codex', 'session_id': SID, 'action': action}

    def writes(self):
        return [item for item in self.provider.rpc.calls if item[0] == 'thread-follower-update-thread-settings']

    def picker(self, model='model-a', effort='low'):
        self.provider.rpc.history.update(resumeState='resumed', modelProvider='openai',
            latestCollaborationMode={'mode': 'default', 'settings': {'model': model, 'reasoning_effort': effort}})

    def test_real_picker_effort_survives_null_follower_and_stale_owner_is_rejected(self):
        self.picker(effort='high')
        self.provider.rpc.updated = True
        self.provider.rpc.readback = {'reasoningEffort': None}
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual((row['model']['effort'], row['settings']['reasoning']), ('high', 'High'))
        self.provider.native_settings[SID]['owner'] = 'previous-owner'
        self.assertNotIn('model', self.provider.snapshot()[0])
        self.assertEqual(self.writes(), [])

    def test_collaboration_snapshot_and_patch_changes_invalidate_cached_model(self):
        self.picker()
        self.provider.session_snapshot(SID)
        original = self.provider.snapshot()[0]['model']['revision']
        self.picker(effort='high')
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        self.assertNotIn('model', self.provider.snapshot()[0])
        updated = self.provider.session_snapshot(SID)[0]['model']
        self.assertEqual(updated['effort'], 'high')
        self.assertNotEqual(updated['revision'], original)
        for change in ({'op': 'replace', 'path': ['latestCollaborationMode'], 'value': {
                           'mode': 'default', 'settings': {'model': 'model-a', 'reasoning_effort': None}}},
                       {'op': 'remove', 'path': ['latestCollaborationMode']}):
            revision = self.provider.states[SID]['revision']
            self.provider.event({'method': 'thread-stream-state-changed', 'sourceClientId': self.provider.rpc.owner,
                'version': 11, 'params': {'hostId': 'local', 'conversationId': SID, 'change': {
                    'type': 'patches', 'baseRevision': revision, 'revision': revision + 1, 'patches': [change]}}})
            self.assertNotIn('model', self.provider.snapshot()[0])
            self.provider.refresh_settings(SID, self.provider.rpc.owner)
            self.assertEqual(self.provider.snapshot()[0]['model']['effort_status'], 'unknown')
        self.assertEqual(self.writes(), [])

    def test_late_null_read_cannot_restore_model_after_picker_patch(self):
        self.picker()
        self.provider.session_snapshot(SID)
        arrived, release = threading.Event(), threading.Event()
        original = self.provider.rpc.call
        def delayed(method, *args, **kwargs):
            response = original(method, *args, **kwargs)
            if method == 'thread-follower-read-model-settings':
                arrived.set()
                self.assertTrue(release.wait(2))
            return response
        self.provider.rpc.call = delayed
        reader = threading.Thread(target=self.provider.refresh_settings, args=(SID, self.provider.rpc.owner))
        reader.start()
        try:
            self.assertTrue(arrived.wait(2))
            self.picker(effort='high')
            self.provider.rpc.follow(SID, self.provider.rpc.owner)
        finally:
            release.set(); reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertNotIn('model', self.provider.snapshot()[0])
        self.provider.rpc.call = original
        self.provider.refresh_settings(SID, self.provider.rpc.owner)
        self.assertEqual(self.provider.snapshot()[0]['model']['effort'], 'high')

    def test_removing_all_picker_fields_never_revives_old_follower_effort(self):
        self.picker()
        self.provider.session_snapshot(SID)
        for key in ('latestCollaborationMode', 'resumeState', 'modelProvider'):
            self.provider.rpc.history[key] = None
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        self.provider.refresh_settings(SID, self.provider.rpc.owner)
        self.assertEqual(self.provider.snapshot()[0]['model']['effort_status'], 'unknown')
        for key in ('latestCollaborationMode', 'resumeState', 'modelProvider'):
            self.provider.rpc.history.pop(key)
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        self.assertNotIn('model', self.provider.snapshot()[0])
        self.provider.refresh_settings(SID, self.provider.rpc.owner)
        row = self.provider.snapshot()[0]
        self.assertEqual(row['model']['effort_status'], 'unknown')
        self.assertEqual(row['model']['options'], [])
        self.assertNotIn('effort', row['model'])

    def test_model_readback_uses_confirmed_picker_and_rejects_conflicting_model(self):
        self.picker()
        command = self.command()
        self.provider.rpc.readback = {'reasoningEffort': None}
        self.provider.rpc.after_update = lambda: self.picker('model-b', 'high')
        self.assertEqual(self.provider.execute(command), 'model_updated')
        self.assertEqual(self.provider.snapshot()[0]['model']['effort'], 'high')
        command = self.command()
        self.provider.rpc.after_update = lambda: self.picker('model-a', 'high')
        with patch.object(self.provider.condition, 'wait_for', side_effect=lambda predicate, timeout: predicate()):
            self.assertEqual(self.provider.execute(command), 'unknown')
        self.assertEqual(len(self.writes()), 2)

    def test_lost_patch_does_not_forget_same_owner_picker_contract(self):
        self.picker(effort='high')
        self.provider.session_snapshot(SID)
        revision = self.provider.states[SID]['revision']
        self.provider.event({'method': 'thread-stream-state-changed', 'sourceClientId': self.provider.rpc.owner,
            'version': 11, 'params': {'hostId': 'local', 'conversationId': SID, 'change': {
                'type': 'patches', 'baseRevision': revision - 1, 'revision': revision + 1, 'patches': []}}})
        self.assertNotIn(SID, self.provider.states)
        for key in ('latestCollaborationMode', 'resumeState', 'modelProvider'):
            self.provider.rpc.history.pop(key)
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        row = self.provider.snapshot()[0]
        self.assertEqual(row['model']['effort_status'], 'unknown')
        self.assertEqual(row['model']['options'], [])
        self.assertNotIn('effort', row['model'])
        self.provider.refresh_settings(SID, self.provider.rpc.owner)
        self.assertEqual(self.provider.snapshot()[0]['model']['effort_status'], 'unknown')

    def test_malformed_picker_effort_keeps_unknown_model_and_clears_old_summary(self):
        for effort in (True, {}, [], '', ' ', 'high effort', 'x' * 33):
            with self.subTest(effort=effort):
                self.picker(effort=effort)
                row = self.provider.session_snapshot(SID)[0]
                self.assertEqual(row['model']['effort_status'], 'unknown')
                self.assertEqual(row['model']['options'], [])
                self.assertEqual(row['policy']['options'], [])
                self.assertNotIn('effort', row['model'])
                self.assertNotIn('reasoning', row['settings'])
                self.assertEqual(row['settings']['model'], 'Model A')
        self.assertEqual(self.writes(), [])

    def test_model_and_effort_go_to_same_owner_and_permissions_are_preserved(self):
        command = self.command()
        before = copy.deepcopy(self.provider.rpc.history['latestThreadSettings'])
        self.assertEqual(self.provider.execute(command), 'model_updated')
        self.assertEqual(self.writes(), [('thread-follower-update-thread-settings',
            {'conversationId': SID, 'threadSettings': {'model': 'model-b', 'effort': 'high'},
             'activeTurnId': None}, 'desktop-owner')])
        after = self.provider.rpc.history['latestThreadSettings']
        self.assertEqual({key: after.get(key) for key in FIELDS}, {key: before.get(key) for key in FIELDS})
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual(row['model']['current'], 'model-b')
        self.assertEqual(row['model']['effort'], 'high')
        self.assertIn(('/synthetic/project', 'openai', True), self.catalog.calls)
        self.assertFalse(any(name in ('thread/start', 'thread/resume', 'turn/start')
                             for name, _, _ in self.provider.rpc.calls))

    def test_next_turn_native_model_and_effort_override_older_history_even_while_busy(self):
        self.provider.rpc.updated = True
        self.provider.rpc.readback = {'model': 'model-b', 'reasoningEffort': 'high'}
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual(row['model']['current'], 'model-b')
        self.assertEqual(row['model']['current_label'], 'Model B')
        self.assertEqual(row['settings']['model'], 'Model B')
        self.assertEqual(row['settings']['reasoning'], 'High')
        self.assertEqual(row['model']['effort_status'], 'explicit')
        self.assertEqual(row['model']['effort_label'], 'High')
        self.assertEqual(self.provider.states[SID]['history']['latestThreadSettings']['model'], 'model-a')
        self.provider.rpc.history['threadRuntimeStatus']['type'] = 'active'
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        row = self.provider.snapshot()[0]
        self.assertEqual(row['model']['current'], 'model-b')
        self.assertEqual(row['model']['options'], [])
        self.assertEqual(row['policy']['options'], [])
        self.assertEqual(self.writes(), [])

    def test_directory_missing_current_model_is_preserved_without_inventing_a_writable_choice(self):
        self.provider.rpc.updated = True
        self.provider.rpc.readback = {'model': 'retired-model', 'reasoningEffort': 'ultra'}
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual(row['model']['current'], 'retired-model')
        self.assertEqual(row['model']['current_label'], 'retired-model')
        self.assertEqual(row['model']['effort'], 'ultra')
        self.assertNotIn('retired-model', [item['value'] for item in row['model']['options']])
        with self.assertRaisesRegex(ControlError, 'invalid_model'):
            validate_action(row, {'kind': 'model', 'revision': row['model']['revision'],
                                 'model': 'retired-model', 'effort': 'ultra'})

    def test_unique_provider_alias_is_selected_and_accepted_in_native_readback(self):
        command = self.command()
        self.provider.rpc.readback = {'model': 'openai/model-b'}
        self.assertEqual(self.provider.execute(command), 'model_updated')
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual(row['model']['current'], 'model-b')
        self.assertEqual(row['model']['current_label'], 'Model B')

    def test_late_native_read_cannot_replace_a_newer_confirmed_selection(self):
        arrived, release = threading.Event(), threading.Event()
        original = self.provider.rpc.call
        first = True
        def delayed(method, *args, **kwargs):
            nonlocal first
            response = original(method, *args, **kwargs)
            if method == 'thread-follower-read-model-settings' and first:
                first = False
                arrived.set()
                self.assertTrue(release.wait(2))
            return response
        self.provider.rpc.call = delayed
        reader = threading.Thread(target=self.provider.refresh_settings, args=(SID, self.provider.rpc.owner))
        reader.start()
        try:
            self.assertTrue(arrived.wait(2))
            self.provider.rpc.history['latestThreadSettings'].update(model='model-b', effort='high')
            self.provider.refresh_settings(SID, self.provider.rpc.owner)
        finally:
            release.set(); reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertEqual(self.provider.snapshot()[0]['model']['current'], 'model-b')

    def test_only_native_settings_changes_revoke_cached_reads_and_schedule_prompt_refresh(self):
        self.provider.next_probe[SID] = float('inf')
        cached = self.provider.native_settings[SID]
        self.provider.rpc.history['turns'] = []
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        self.assertIs(self.provider.native_settings[SID], cached)
        self.assertEqual(self.provider.next_probe[SID], float('inf'))
        self.provider.rpc.history['latestThreadSettings'].update(model='model-b', effort='high')
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        self.assertEqual(self.provider.next_probe[SID], 0)
        row = self.provider.snapshot()[0]
        self.assertNotIn('model', row)
        self.assertNotIn('model', row['settings'])
        self.assertEqual(row['policy']['options'], [])
        self.provider.refresh()
        row = self.provider.snapshot()[0]
        self.assertEqual(row['model']['current'], 'model-b')
        self.assertEqual(row['model']['effort'], 'high')
        self.assertEqual(self.writes(), [])

    def test_fresh_catalog_revision_rejects_previously_valid_selection(self):
        command = self.command()
        self.catalog.before_fresh = lambda: self.catalog.data.update(revision='catalog-v2')
        with self.assertRaisesRegex(ControlError, 'model_changed'):
            self.provider.execute(command)
        self.assertEqual(self.writes(), [])

    def test_model_without_reasoning_preserves_catalog_and_clears_old_effort(self):
        self.catalog.data['models'].append({'value': 'without-reasoning',
            'label': 'No reasoning setting', 'efforts': []})
        row = self.provider.session_snapshot(SID)[0]
        action = {'kind': 'model', 'revision': row['model']['revision'], 'model': 'without-reasoning'}
        validate_action(normalize_sessions([row])[('codex', SID)], action)
        self.assertEqual(self.provider.execute({'session_id': SID, 'action': action}), 'model_updated')
        self.assertEqual(self.writes()[0][1]['threadSettings'], {'model': 'without-reasoning', 'effort': None})
        updated = self.provider.session_snapshot(SID)[0]['model']
        self.assertEqual(updated['current'], 'without-reasoning')
        self.assertNotIn('effort', updated)

    def test_model_without_reasoning_is_unconfirmed_until_old_effort_is_cleared(self):
        self.catalog.data['models'].append({'value': 'without-reasoning',
            'label': 'No reasoning setting', 'efforts': []})
        row = self.provider.session_snapshot(SID)[0]
        action = {'kind': 'model', 'revision': row['model']['revision'], 'model': 'without-reasoning'}
        self.provider.rpc.readback = {'reasoningEffort': 'low'}
        with patch.object(self.provider.condition, 'wait_for', side_effect=lambda predicate, timeout: predicate()):
            self.assertEqual(self.provider.execute({'session_id': SID, 'action': action}), 'unknown')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.writes()[0][1]['threadSettings'], {'model': 'without-reasoning', 'effort': None})

    def test_busy_or_new_native_request_during_catalog_refresh_blocks_write(self):
        for busy in ('running', 'request'):
            with self.subTest(busy=busy):
                self.provider.rpc.history['threadRuntimeStatus'] = {'type': 'idle'}
                self.provider.rpc.requests = []
                self.catalog.before_fresh = lambda: None
                command = self.command()
                def become_busy():
                    if busy == 'running':
                        self.provider.rpc.history['threadRuntimeStatus'] = {'type': 'active'}
                    else:
                        self.provider.rpc.requests = [{'id': 'permission', 'method': 'item/permissions/requestApproval',
                                                      'params': {'threadId': SID}}]
                    self.provider.rpc.follow(SID, self.provider.rpc.owner)
                self.catalog.before_fresh = become_busy
                with self.assertRaisesRegex(ControlError, 'model_changed'):
                    self.provider.execute(command)
                self.assertEqual(self.writes(), [])

    def test_invalid_effort_or_added_permission_override_is_rejected_before_write(self):
        command = self.command()
        for changes in ({'effort': 'unsupported'}, {'model': 'missing'},
                        {'sandboxPolicy': {'type': 'dangerFullAccess'}}):
            with self.subTest(changes=changes):
                changed = {**command, 'action': {**command['action'], **changes}}
                with self.assertRaisesRegex(ControlError, 'invalid_model'):
                    self.provider.execute(changed)
        self.assertEqual(self.writes(), [])

    def test_native_rejection_or_malformed_ack_never_reports_model_success(self):
        command = self.command()
        for result in ({'applied': False}, {'result': {'applied': True}}, None):
            with self.subTest(result=result):
                self.provider.rpc.result = result
                self.assertEqual(self.provider.execute(command), 'unknown')

    def test_wrong_readback_and_changed_permissions_are_unknown(self):
        for readback, alter_permission in (({'model': 'model-a'}, False),
                                           ({'reasoningEffort': 'medium'}, False), (None, True)):
            with self.subTest(readback=readback, alter_permission=alter_permission):
                self.provider.rpc.updated = False
                self.provider.rpc.readback = readback
                command = self.command()
                self.provider.rpc.after_update = (lambda: self.provider.rpc.history['latestThreadSettings'].update(
                    approvalsReviewer='guardian_subagent')) if alter_permission else (lambda: None)
                with patch.object(self.provider.condition, 'wait_for', side_effect=lambda predicate, timeout: predicate()):
                    self.assertEqual(self.provider.execute(command), 'unknown')

    def test_native_null_settings_revokes_model_capability_without_breaking_the_session(self):
        self.provider.rpc.settings_available = False
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual(row['model']['current'], 'model-a')
        self.assertEqual(row['model']['options'], [])
        self.assertIn('上次确认值', row['settings']['note'])
        self.assertTrue(row['reply'])
        self.assertEqual(self.writes(), [])

    def test_failed_catalog_cannot_restore_legacy_approval_edits_after_managed_restrictions(self):
        self.catalog.data['profiles'] = [{'id': ':workspace', 'allowed': True, 'description': None}]
        self.catalog.data['requirements'] = {'allowedApprovalPolicies': ['on-request']}
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual(row['policy']['scope'], 'session-permissions')
        action = {'kind': 'policy', 'revision': row['policy']['revision'], 'value': 'auto'}
        self.catalog.data = None
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual(row['policy']['options'], [])
        self.assertEqual(row['model']['options'], [])
        self.assertTrue(row['reply'])
        with self.assertRaisesRegex(ControlError, 'policy_changed'):
            self.provider.execute({'session_id': SID, 'action': action})
        self.assertEqual(self.writes(), [])

    def test_missing_expired_or_mismatched_metadata_never_exposes_legacy_policy(self):
        for broken in ('missing', 'expired', 'owner', 'native-unavailable'):
            with self.subTest(broken=broken):
                self.provider.rpc.settings_available = True
                self.provider.session_snapshot(SID)
                value = self.provider.native_settings[SID]
                if broken == 'missing':
                    self.provider.native_settings[SID] = None
                elif broken == 'expired':
                    value['checked'] -= 31
                elif broken == 'owner':
                    value['owner'] = 'another-owner'
                else:
                    self.provider.rpc.settings_available = False
                    self.provider.session_snapshot(SID)
                row = self.provider.snapshot()[0]
                self.assertEqual(row['policy']['options'], [])
                self.assertEqual(row['policy']['scope'], 'session-permissions')
                if broken in ('missing', 'owner'):
                    self.assertNotIn('model', row)
                else:
                    self.assertEqual(row['model']['options'], [])
        self.assertEqual(self.writes(), [])

    def test_required_model_needs_the_complete_allowed_guardian_mode_before_dispatch(self):
        self.catalog.data['profiles'] = [{'id': ':workspace', 'allowed': True, 'description': None}]
        self.catalog.data['requirements'] = {'autoReview': {'requiredOnModels': ['model-b']}}
        settings = self.provider.rpc.history['latestThreadSettings']
        settings.update(approvalsReviewer='auto_review', approvalPolicy='never',
                        activePermissionProfile={'id': ':danger-full-access'},
                        sandboxPolicy={'type': 'dangerFullAccess'})
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        row = self.provider.session_snapshot(SID)[0]
        self.assertEqual([item['value'] for item in row['model']['options']], ['model-a'])
        action = {'kind': 'model', 'revision': row['model']['revision'], 'model': 'model-b', 'effort': 'high'}
        with self.assertRaisesRegex(ControlError, 'invalid_model'):
            self.provider.execute({'session_id': SID, 'action': action})
        self.assertEqual(self.writes(), [])
        settings.update(approvalPolicy='on-request', activePermissionProfile={'id': ':workspace'},
            sandboxPolicy={'type': 'workspaceWrite', 'networkAccess': False,
                'excludeSlashTmp': False, 'excludeTmpdirEnvVar': False, 'writableRoots': ['/synthetic/project']})
        self.provider.rpc.follow(SID, self.provider.rpc.owner)
        action['revision'] = self.provider.session_snapshot(SID)[0]['model']['revision']
        self.assertEqual(self.provider.execute({'session_id': SID, 'action': action}), 'model_updated')
        self.assertEqual(len(self.writes()), 1)

    def test_required_model_is_withdrawn_for_partial_guardian_state_or_disallowed_native_mode(self):
        self.catalog.data['profiles'] = [{'id': ':workspace', 'allowed': True, 'description': None}]
        self.catalog.data['requirements'] = {'autoReview': {'requiredOnModels': ['model-b']}}
        settings = self.provider.rpc.history['latestThreadSettings']
        settings.update(approvalsReviewer='guardian_subagent', activePermissionProfile={'id': ':workspace'})
        baseline = copy.deepcopy(settings)
        for broken in ('missing-profile', 'network', 'tmp', 'reviewer', 'policy', 'managed-reviewer', 'feature'):
            with self.subTest(broken=broken):
                settings.clear(); settings.update(copy.deepcopy(baseline))
                self.catalog.data['requirements'] = {'autoReview': {'requiredOnModels': ['model-b']}}
                self.catalog.data['config'] = {}
                if broken == 'missing-profile': settings['activePermissionProfile'] = None
                elif broken == 'network': settings['sandboxPolicy']['networkAccess'] = True
                elif broken == 'tmp': settings['sandboxPolicy'].pop('excludeSlashTmp')
                elif broken == 'reviewer': settings['approvalsReviewer'] = 'user'
                elif broken == 'policy': settings['approvalPolicy'] = 'never'
                elif broken == 'managed-reviewer': self.catalog.data['requirements']['allowedApprovalsReviewers'] = ['user']
                else: self.catalog.data['config'] = {'features': {'guardian_approval': False}}
                self.provider.rpc.follow(SID, self.provider.rpc.owner)
                row = self.provider.session_snapshot(SID)[0]
                self.assertEqual([item['value'] for item in row['model']['options']], ['model-a'])
        self.assertEqual(self.writes(), [])

    def test_worker_persists_confirmed_model_receipt_and_never_replays_it(self):
        command = self.command()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            worker = Worker({'device_id': 'synthetic-device', 'hub_url': 'http://127.0.0.1:1',
                             'token': 'synthetic'}, {'codex': self.provider}, path)
            worker.exchange = lambda _: {'ok': True, 'receipts': [], 'command': command}
            self.assertTrue(worker.step())
            receipt = worker.state['receipts'][0]
            self.assertEqual(receipt['state'], 'resolved')
            self.assertIn('模型设置', receipt['detail'])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['receipts'][0], receipt)
            worker.step()
            self.assertEqual(len(self.writes()), 1)

    def test_worker_keeps_unconfirmed_readback_unknown(self):
        command = self.command()
        self.provider.rpc.readback = {'model': 'different-model'}
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker({'device_id': 'synthetic-device', 'hub_url': 'http://127.0.0.1:1',
                             'token': 'synthetic'}, {'codex': self.provider}, Path(tmp)/'state.json')
            worker.exchange = lambda _: {'ok': True, 'receipts': [], 'command': command}
            with patch.object(self.provider.condition, 'wait_for', side_effect=lambda predicate, timeout: predicate()):
                worker.step()
            self.assertEqual(worker.state['receipts'][0]['state'], 'unknown')
            self.assertNotIn('确认此会话', worker.state['receipts'][0]['detail'])


if __name__ == '__main__':
    unittest.main()

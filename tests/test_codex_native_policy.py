import copy
import unittest

from aw.control.codex_policy import (capability, native_capability, settings_for,
                                     valid_capability)


GRANULAR_POLICY = {'granular': {'sandbox_approval': False, 'rules': False,
    'skill_approval': False, 'request_permissions': True, 'mcp_elicitations': True}}


def catalog():
    return {'revision': 'native-catalog-1', 'selected_model': 'example-model',
        'config': {'sandbox_mode': 'workspace-write', 'approval_policy': 'on-request'},
        'requirements': {}, 'profiles': [
            {'id': ':read-only', 'allowed': True, 'description': None},
            {'id': ':workspace', 'allowed': True, 'description': None},
            {'id': ':danger-full-access', 'allowed': True, 'description': None},
            {'id': 'team-profile', 'allowed': True, 'description': 'Native team profile'},
        ]}


def state():
    return {'requests': [], 'history': {'threadRuntimeStatus': {'type': 'idle'},
        'workspaceKind': 'project', 'latestThreadSettings': {
            'activePermissionProfile': {'id': ':workspace', 'extends': None},
            'approvalPolicy': 'on-request', 'approvalsReviewer': 'user',
            'sandboxPolicy': {'type': 'workspaceWrite', 'networkAccess': False,
                'excludeSlashTmp': False, 'excludeTmpdirEnvVar': False,
                'writableRoots': ['/workspace/example']}}}}


def options(result):
    return {item['value']: item for item in result['options']}


class NativePolicyTests(unittest.TestCase):
    def test_primary_ask_and_advanced_modes_keep_distinct_native_effects(self):
        data = catalog()
        result = native_capability(state(), data)
        self.assertTrue(valid_capability(result))
        self.assertEqual((result['scope'], result['current']), ('session-permissions', 'auto'))
        choices = options(result)
        self.assertEqual(choices['auto']['label'], 'Ask for approval')
        self.assertNotIn('group', choices['auto'])
        self.assertEqual(choices['read-only']['group'], 'advanced')
        self.assertEqual(choices['granular']['group'], 'advanced')
        self.assertEqual(choices['full-access']['label'], 'Full access')
        self.assertNotIn('guardian-approvals', choices)
        self.assertEqual(settings_for('read-only', data), {'permissions': ':read-only',
            'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'})
        self.assertEqual(settings_for('auto', data), {'permissions': ':workspace',
            'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'})
        self.assertEqual(settings_for('granular', data), {'permissions': ':workspace',
            'approvalPolicy': GRANULAR_POLICY, 'approvalsReviewer': 'user'})
        self.assertEqual(settings_for('full-access', data), {'permissions': ':danger-full-access',
            'approvalPolicy': 'never', 'approvalsReviewer': 'user'})

    def test_primary_ask_resolves_config_and_projectless_defaults_without_sandbox_guessing(self):
        for config, kind, primary in [({}, 'project', 'auto'), ({}, 'projectless', 'granular'),
                ({'sandbox_mode': 'read-only'}, 'project', 'read-only'),
                ({'sandbox_mode': 'workspace-write', 'approval_policy': GRANULAR_POLICY}, 'project', 'granular')]:
            with self.subTest(config=config, kind=kind):
                data, native = catalog(), state()
                data['config'] = copy.deepcopy(config)
                native['history']['workspaceKind'] = kind
                result = options(native_capability(native, data))
                self.assertEqual(result[primary]['label'], 'Ask for approval')
                self.assertEqual(sum(row['label'] == 'Ask for approval' for row in result.values()), 1)
        data = catalog()
        data['config']['sandbox_workspace_write'] = {'writable_roots': ['/workspace/extra']}
        result = options(native_capability(state(), data))
        self.assertFalse(any(row['label'] == 'Ask for approval' for row in result.values()))
        self.assertEqual(result['auto']['group'], 'advanced')
        self.assertNotIn('sandboxPolicy', settings_for('auto', data))
        self.assertNotIn('/workspace/extra', str(settings_for('auto', data)))

    def test_guardian_requires_native_default_or_managed_evidence_not_feature_permission_alone(self):
        data = catalog()
        data['config']['features'] = {'guardian_approval': True}
        self.assertIsNone(settings_for('guardian-approvals', data))
        data['config']['approvals_reviewer'] = 'auto_review'
        self.assertEqual(settings_for('guardian-approvals', data), {'permissions': ':workspace',
            'approvalPolicy': 'on-request', 'approvalsReviewer': 'guardian_subagent'})
        self.assertEqual(options(native_capability(state(), data))['guardian-approvals']['label'], 'Approve for me')
        data['config']['features']['guardian_approval'] = False
        self.assertIsNone(settings_for('guardian-approvals', data))
        data = catalog()
        data['requirements']['allowedApprovalsReviewers'] = ['auto_review']
        self.assertEqual(set(options(native_capability(state(), data))), {'guardian-approvals'})

    def test_all_managed_requirements_filter_the_exact_policy_reviewer_and_profile(self):
        data = catalog()
        data['requirements'] = {'allowedPermissionProfiles': {':workspace': True, ':read-only': False},
            'allowedSandboxModes': ['workspace-write'], 'allowedApprovalsReviewers': ['user'],
            'allowedApprovalPolicies': [GRANULAR_POLICY]}
        self.assertEqual(set(options(native_capability(state(), data))), {'granular'})
        for value in ('auto', 'read-only', 'full-access', 'guardian-approvals', 'profile:team-profile'):
            self.assertIsNone(settings_for(value, data))
        data['requirements']['allowedApprovalPolicies'][0] = {
            'granular': {**GRANULAR_POLICY['granular'], 'rules': 0}}
        self.assertIsNone(native_capability(state(), data))

    def test_missing_disallowed_duplicate_or_malformed_catalog_never_creates_profiles(self):
        for change in (lambda d: d.update(profiles=[]),
                       lambda d: d.update(profiles=[{**row, 'allowed': False} for row in d['profiles']]),
                       lambda d: d['profiles'].append(copy.deepcopy(d['profiles'][0])),
                       lambda d: d['requirements'].update(allowedApprovalPolicies='on-request'),
                       lambda d: d['requirements'].update(allowedApprovalPolicies=[None]),
                       lambda d: d['requirements'].update(allowedApprovalsReviewers=[False]),
                       lambda d: d['requirements'].update(allowedPermissionProfiles={':workspace': 1}),
                       lambda d: d['requirements'].update(autoReview={'requiredOnModels': 'example-model'})):
            data = catalog()
            change(data)
            self.assertIsNone(native_capability(state(), data))
            self.assertIsNone(settings_for('auto', data))
        for data in (None, {}, {'revision': 'r', 'config': {}, 'requirements': {}, 'profiles': []}):
            self.assertIsNone(native_capability(state(), data))

    def test_model_required_review_cannot_be_bypassed_by_full_access_or_a_named_profile(self):
        data = catalog()
        data['requirements']['autoReview'] = {'requiredOnModels': ['example-model']}
        for model in ('example-model', 'provider/example-model'):
            data['selected_model'] = model
            self.assertEqual(set(options(native_capability(state(), data))), {'guardian-approvals'})
            for forbidden in ('auto', 'read-only', 'granular', 'full-access', 'profile:team-profile'):
                self.assertIsNone(settings_for(forbidden, data))
        for model in (None, '', 'provider/nested/example-model', 'bad provider/example-model'):
            data['selected_model'] = model
            self.assertIsNone(native_capability(state(), data))
            self.assertIsNone(settings_for('full-access', data))
        data['selected_model'] = 'other-model'
        self.assertIsNotNone(settings_for('full-access', data))

    def test_current_preset_needs_profile_approval_reviewer_and_complete_native_sandbox_match(self):
        data = catalog()
        for change in (lambda s: s.update(activePermissionProfile=None),
                       lambda s: s.update(activePermissionProfile={'id': ':read-only'}),
                       lambda s: s.update(approvalPolicy='never'),
                       lambda s: s.update(approvalsReviewer='unknown'),
                       lambda s: s['sandboxPolicy'].pop('excludeSlashTmp'),
                       lambda s: s['sandboxPolicy'].update(networkAccess=True),
                       lambda s: s['sandboxPolicy'].update(excludeTmpdirEnvVar=True)):
            native = state()
            change(native['history']['latestThreadSettings'])
            self.assertEqual(native_capability(native, data)['current'], 'custom')
        native = state()
        data['config']['approvals_reviewer'] = 'guardian_subagent'
        native['history']['latestThreadSettings']['approvalsReviewer'] = 'auto_review'
        self.assertEqual(native_capability(native, data)['current'], 'guardian-approvals')
        native['history']['latestThreadSettings']['approvalPolicy'] = GRANULAR_POLICY
        self.assertEqual(native_capability(native, data)['current'], 'custom')

    def test_named_profile_writes_only_native_id_and_remains_distinct_from_builtin_names(self):
        data, native = catalog(), state()
        data['profiles'].append({'id': 'auto', 'allowed': True, 'description': 'Named profile'})
        native['history']['latestThreadSettings']['activePermissionProfile'] = {'id': 'auto'}
        result = native_capability(native, data)
        self.assertEqual(result['current'], 'profile:auto')
        self.assertEqual(settings_for('profile:auto', data), {'permissions': 'auto'})
        self.assertEqual(settings_for('profile:team-profile', data), {'permissions': 'team-profile'})
        self.assertIsNone(settings_for('profile:missing', data))
        self.assertIsNone(settings_for('custom', data))
        self.assertEqual(options(result)['profile:team-profile']['description'], 'Native team profile')

    def test_named_profile_cannot_bypass_constraints_on_inherited_approval_and_reviewer(self):
        data = catalog()
        data['requirements'] = {'allowedApprovalPolicies': ['on-request'],
                                'allowedApprovalsReviewers': ['auto_review']}
        self.assertIsNone(settings_for('profile:team-profile', data))
        data.update(selected_approval_policy='on-request', selected_reviewer='user')
        self.assertIsNone(settings_for('profile:team-profile', data))
        data['selected_reviewer'] = 'guardian_subagent'
        self.assertEqual(settings_for('profile:team-profile', data), {'permissions': 'team-profile'})
        data['selected_approval_policy'] = 'never'
        self.assertIsNone(settings_for('profile:team-profile', data))

    def test_settings_payloads_are_copies_and_every_native_change_invalidates_revision(self):
        data, native = catalog(), state()
        payload = settings_for('granular', data)
        payload['approvalPolicy']['granular']['rules'] = True
        self.assertEqual(settings_for('granular', data)['approvalPolicy'], GRANULAR_POLICY)
        first = native_capability(native, data)['revision']
        data['requirements']['allowedApprovalPolicies'] = ['on-request']
        self.assertNotEqual(first, native_capability(native, data)['revision'])
        first = native_capability(native, data)['revision']
        native['history']['latestThreadSettings']['approvalsReviewer'] = 'auto_review'
        self.assertNotEqual(first, native_capability(native, data)['revision'])
        native['requests'] = [{'id': 'pending'}]
        self.assertIsNone(native_capability(native, data))
        native['requests'] = []
        native['history']['threadRuntimeStatus']['type'] = 'active'
        self.assertIsNone(native_capability(native, data))

    def test_legacy_capability_keeps_its_narrow_scope_and_group_validation_is_bounded(self):
        legacy = capability(state())
        self.assertTrue(valid_capability(legacy))
        self.assertEqual(legacy['scope'], 'session-next-turn')
        result = native_capability(state(), catalog())
        self.assertTrue(valid_capability(result))
        result['options'][0]['group'] = 'invented'
        self.assertFalse(valid_capability(result))


if __name__ == '__main__':
    unittest.main()

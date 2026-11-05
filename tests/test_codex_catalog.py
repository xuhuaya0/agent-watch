import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aw.control.codex_catalog import MetadataRpc, NativeCatalog, query_catalog


def model(value='example-model'):
    return {'model': value, 'displayName': 'Example model', 'hidden': False,
            'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}],
            'defaultReasoningEffort': 'low'}


class Rpc:
    def __init__(self, root):
        self.root, self.calls, self.closed = root, [], False
        self.responses = {
            'config/read': {'config': {'model_provider': 'openai', 'approval_policy': 'on-request',
                'sandbox_mode': 'workspace-write', 'features': {'guardian_approval': True},
                'model_providers': {'private': {'api_key': 'must not escape'}},
                'sandbox_workspace_write': {'network_access': False, 'private': 'must not escape'}}},
            'configRequirements/read': {'requirements': {'allowedPermissionProfiles': {':workspace': True},
                'autoReview': {'requiredOnModels': ['example-model'], 'private': 'must not escape'},
                'allowedApprovalsReviewers': ['user'], 'private': 'must not escape'}},
            'model/list': {'data': [model()], 'nextCursor': None},
            'permissionProfile/list': {'data': [
                {'id': ':workspace', 'description': None, 'allowed': True},
                {'id': ':read-only', 'description': None, 'allowed': True},
                {'id': 'blocked-profile', 'description': None, 'allowed': False}], 'nextCursor': None}}

    def initialize(self):
        self.calls.append(('initialize', {}))
        return {'codexHome': str(self.root)}

    def call(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        value = self.responses[method]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value(params) if callable(value) else value)

    def close(self):
        self.closed = True


class NativeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.rpc = Rpc(self.root)

    def query(self, provider='openai'):
        return query_catalog('native-cli', self.root, str(self.root), provider,
                             rpc_factory=lambda *_: self.rpc)

    def test_native_only_directory_filters_permissions_and_removes_private_config(self):
        result = self.query()
        self.assertEqual(result['models'], [{'value': 'example-model', 'label': 'Example model',
            'efforts': ['low', 'high'], 'default_effort': 'low'}])
        self.assertEqual([item['id'] for item in result['profiles']], [':workspace'])
        self.assertNotIn('must not escape', json.dumps(result))
        self.assertEqual(result['config']['features'], {'guardian_approval': True})
        self.assertEqual(result['requirements']['autoReview'], {'requiredOnModels': ['example-model']})
        self.assertTrue(self.rpc.closed)
        self.assertEqual([name for name, _ in self.rpc.calls], ['initialize', 'config/read',
            'configRequirements/read', 'model/list', 'permissionProfile/list'])
        self.assertEqual(self.rpc.calls[-1][1]['cwd'], str(self.root))

    def test_provider_and_native_profile_must_match_before_catalog_reads(self):
        with self.assertRaises(ValueError):
            self.query('custom-provider')
        self.assertTrue(self.rpc.closed)
        self.assertNotIn('model/list', [name for name, _ in self.rpc.calls])
        self.rpc = Rpc(self.root/'other-account')
        with self.assertRaises(ValueError):
            self.query()
        self.assertEqual(self.rpc.calls, [('initialize', {})])
        self.assertTrue(self.rpc.closed)

    def test_hidden_malformed_and_ambiguous_models_are_not_selectable(self):
        hidden = {**model('hidden'), 'hidden': True}
        malformed = {**model('bad'), 'defaultReasoningEffort': 'unsupported'}
        duplicate = model('duplicate')
        self.rpc.responses['model/list']['data'] += [hidden, malformed, duplicate, duplicate, None]
        self.assertEqual([row['value'] for row in self.query()['models']], ['example-model'])

    def test_model_without_reasoning_omits_unavailable_default_effort(self):
        self.rpc.responses['model/list']['data'].append({**model('without-reasoning'),
            'supportedReasoningEfforts': [], 'defaultReasoningEffort': None})
        option = self.query()['models'][-1]
        self.assertEqual(option['value'], 'without-reasoning')
        self.assertEqual(option['efforts'], [])
        self.assertNotIn('default_effort', option)

    def test_native_catalog_paginates_and_rejects_repeated_cursors(self):
        self.rpc.responses['model/list'] = lambda args: {
            'data': [model('second' if args['cursor'] else 'first')],
            'nextCursor': None if args['cursor'] else 'next'}
        result = self.query()
        self.assertEqual([row['value'] for row in result['models']], ['first', 'second'])
        self.rpc.responses['model/list'] = {'data': [model()], 'nextCursor': 'repeat'}
        with self.assertRaises(ValueError):
            self.query()
        self.assertTrue(self.rpc.closed)

    def test_profile_read_failure_does_not_fabricate_permission_presets(self):
        self.rpc.responses['permissionProfile/list'] = ValueError('unsupported method')
        with self.assertRaises(ValueError):
            self.query()
        self.assertTrue(self.rpc.closed)

    def test_cached_reads_are_scoped_and_account_config_changes_invalidate_them(self):
        calls = []
        def query(executable, root, cwd, provider):
            calls.append((cwd, provider))
            return {'models': [{'value': 'example'}]}
        catalog = NativeCatalog(self.root/'cli', self.root, query=query)
        first = catalog.get(str(self.root), 'openai')
        first['models'].clear()
        self.assertTrue(catalog.get(str(self.root), 'openai')['models'])
        self.assertEqual(len(calls), 1)
        (self.root/'auth.json').write_text('synthetic account rotation')
        catalog.get(str(self.root), 'openai')
        (self.root/'.codex').mkdir()
        (self.root/'.codex/config.toml').write_text('sandbox_mode = "read-only"')
        catalog.get(str(self.root), 'openai')
        catalog.get(str(self.root), 'custom-provider')
        catalog.get(str(self.root), 'custom-provider', fresh=True)
        self.assertEqual(len(calls), 5)
        self.assertIsNone(catalog.get(str(self.root), None))
        self.assertIsNone(catalog.get('relative', 'openai'))

    def test_changed_account_during_query_revokes_result_and_failure_backs_off(self):
        calls = []
        def query(*_):
            calls.append(True)
            (self.root/'auth.json').write_text(str(len(calls)))
            return {'models': [model()]}
        catalog = NativeCatalog(self.root/'cli', self.root, query=query)
        self.assertIsNone(catalog.get(str(self.root), 'openai'))
        catalog.query = lambda *_: (_ for _ in ()).throw(ValueError('unavailable'))
        self.assertIsNone(catalog.get(str(self.root), 'openai'))
        catalog.query = lambda *_: self.fail('failure cache should suppress another probe')
        self.assertIsNone(catalog.get(str(self.root), 'openai'))

    def test_metadata_transport_rejects_session_methods_and_bounds_output(self):
        class Process:
            stdin = io.BytesIO()
            stdout = io.BytesIO(b'x' * 1025)
            def terminate(self): pass
            def wait(self, timeout): pass
        with patch('aw.control.codex_catalog.subprocess.Popen', return_value=Process()) as popen:
            with patch('aw.control.codex_catalog.MAX_FRAME', 1024):
                rpc = MetadataRpc('native-cli', self.root, str(self.root))
                with self.assertRaises(ValueError):
                    rpc.call('thread/resume', {'threadId': 'existing'})
                with self.assertRaises(OSError):
                    rpc.initialize()
                rpc.close()
        self.assertEqual(popen.call_args.args[0], ['native-cli', 'app-server', '--stdio'])
        self.assertEqual(popen.call_args.kwargs['cwd'], str(self.root))


if __name__ == '__main__':
    unittest.main()

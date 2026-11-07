import copy
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from aw.control.codex_catalog import MetadataRpc, NativeCatalog, query_catalog, _metadata_executable


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
            'efforts': ['low', 'high'], 'effort_labels': {'low': 'Light', 'high': 'High'},
            'default_effort': 'low'}])
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

    def test_cached_catalog_rechecks_desktop_context_and_revokes_failed_gate_reads(self):
        class Features:
            value = {'guardian_approval': True, 'revision': 'a'*64}
            calls = []
            def get(self, fresh=False):
                self.calls.append(fresh)
                return copy.deepcopy(self.value)
        calls, reader = [], Features()
        def query(*_):
            calls.append(True)
            return {'provider': 'openai', 'revision': 'native', 'models': [model()]}
        catalog = NativeCatalog(self.root/'cli', self.root, query=query, feature_reader=reader)
        first = catalog.get(str(self.root), 'openai')
        reader.value = {'guardian_approval': False, 'revision': 'b'*64}
        second = catalog.get(str(self.root), 'openai')
        self.assertFalse(second['desktop_features']['guardian_approval'])
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(len(calls), 1)
        for invalid in (None, {'guardian_approval': 'true', 'revision': 'a'*64},
                        {'guardian_approval': True, 'revision': 'bad'},
                        {'guardian_approval': True, 'revision': 'a'*64, 'account': 'unwanted'}):
            reader.value = invalid
            result = catalog.get(str(self.root), 'openai')
            self.assertNotIn('desktop_features', result)
            self.assertNotEqual(result['revision'], first['revision'])
        reader.value = {'guardian_approval': True, 'revision': 'c'*64}
        catalog.get(str(self.root), 'openai', fresh=True)
        self.assertTrue(reader.calls[-1])
        self.assertEqual(len(calls), 2)

    def test_fresh_model_catalog_can_keep_the_shared_feature_cache(self):
        calls = []
        class Features:
            def get(self, fresh=False):
                calls.append(fresh)
                return {'guardian_approval': True, 'revision': 'a'*64}
        catalog = NativeCatalog(self.root/'cli', self.root,
            query=lambda *_: {'provider': 'openai', 'revision': 'native', 'models': [model()]},
            feature_reader=Features())
        catalog.get(str(self.root), 'openai', fresh=True, fresh_features=False)
        self.assertEqual(calls, [False])

    def test_account_change_during_desktop_feature_read_revokes_entire_catalog(self):
        root = self.root
        class Features:
            def get(self, fresh=False):
                (root/'auth.json').write_text('synthetic account changed during feature request')
                return {'guardian_approval': True, 'revision': 'a'*64}
        catalog = NativeCatalog(self.root/'cli', self.root,
            query=lambda *_: {'provider': 'openai', 'revision': 'native', 'models': [model()]},
            feature_reader=Features())
        self.assertIsNone(catalog.get(str(self.root), 'openai'))

    def test_feature_transport_failure_keeps_readable_catalog_without_old_gate(self):
        class Features:
            def get(self, fresh=False):
                raise TimeoutError('synthetic feature transport timeout')
        catalog = NativeCatalog(self.root/'cli', self.root,
            query=lambda *_: {'provider': 'openai', 'revision': 'native', 'models': [model()]},
            feature_reader=Features())
        result = catalog.get(str(self.root), 'openai')
        self.assertTrue(result['models'])
        self.assertNotIn('desktop_features', result)

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

    def test_trusted_updated_app_can_read_catalog_without_enabling_unverified_draft_creation(self):
        app = self.root/'Example.app'
        executable = app/'Contents/Resources/codex-cli/bin/codex'
        executable.parent.mkdir(parents=True)
        executable.write_text('synthetic executable')
        executable.chmod(0o700)
        plist = app/'Contents/Info.plist'
        plist.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.openai.codex',
                                         'CFBundleVersion': 'future-version'}))
        with patch('aw.control.codex_catalog.subprocess.run') as verify:
            self.assertEqual(_metadata_executable(app), executable)
            self.assertIn('identifier "com.openai.codex"', verify.call_args.args[0][2])
            self.assertTrue(verify.call_args.kwargs['check'])
        with patch('aw.control.codex_catalog.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, 'codesign')):
            self.assertIsNone(_metadata_executable(app))
        plist.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'example.untrusted'}))
        with patch('aw.control.codex_catalog.subprocess.run') as verify:
            self.assertIsNone(_metadata_executable(app))
            verify.assert_not_called()


if __name__ == '__main__':
    unittest.main()

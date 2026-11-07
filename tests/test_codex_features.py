import copy
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch
from aw.control.codex_features import (DesktopFeatures, GUARDIAN_GATE, MAX_BODY,
    _app_session, _check_language_preferences, _curl_bootstrap, _processes, _project)


def context():
    return {'token': 'synthetic.access.token', 'user': 'example-user', 'account': 'example-account', 'plan': 'prolite',
        'semantic': 'example-context', 'stamp': 'example-auth-and-context', 'body': {
            'app_session_id': '11111111-1111-4111-8111-111111111111',
            'app_version': '26.1103.31037', 'brand_name': 'chatgpt', 'build_flavor': 'prod',
            'desktop_app_beta_enabled': False, 'locale': 'en-US', 'stable_id': 'example-stable',
            'system_name': 'macOS', 'system_version': '26.0', 'window_type': 'electron'}}


def response(ctx=None, value=True, now=1000):
    ctx = ctx or context(); b = ctx['body']
    return {'time': now * 1000, 'user': {'userID': ctx['user'],
        'customIDs': {'account_id': ctx['account'], 'stableID': b['stable_id']},
        'locale': b['locale'], 'appVersion': b['app_version'], 'custom': {
            'auth_method': 'chatgpt', 'plan_type': ctx['plan'], 'brand_name': b['brand_name'],
            'codex_app_session_id': b['app_session_id'], 'codex_build_flavor': b['build_flavor'],
            'codex_window_type': b['window_type'], 'systemName': b['system_name'],
            'systemVersion': b['system_version'], 'desktop_app_beta_enabled': b['desktop_app_beta_enabled']}},
        'feature_gates': {GUARDIAN_GATE: {'value': value, 'rule_id': 'synthetic-rule'}}}


def encode(payload):
    return json.dumps({'statsigPayload': json.dumps(payload)}).encode()


class FeatureProjectionTests(unittest.TestCase):
    def test_positive_and_negative_are_explicit_minimal_facts(self):
        for value in (False, True):
            result = _project(encode(response(value=value)), context(), 1000)
            self.assertEqual(set(result), {'guardian_approval', 'revision'})
            self.assertIs(result['guardian_approval'], value)
            self.assertRegex(result['revision'], r'^[a-f0-9]{64}$')
            self.assertNotIn('example-account', json.dumps(result))

    def test_semantic_revision_survives_fresh_timestamp_and_unrelated_gates(self):
        first = _project(encode(response()), context(), 1000)
        payload = response(now=1001); payload['feature_gates']['other'] = {'value': False}
        self.assertEqual(first, _project(encode(payload), context(), 1001))
        self.assertNotEqual(first['revision'], _project(encode(response(value=False)), context(), 1000)['revision'])

    def test_sdk_hashed_lookup_supported_but_unknown_formats_are_not(self):
        payload = response(); hashed = 0
        for char in GUARDIAN_GATE: hashed = (31 * hashed + ord(char)) & 0xffffffff
        payload['feature_gates'][str(hashed)] = payload['feature_gates'].pop(GUARDIAN_GATE)
        self.assertTrue(_project(encode(payload), context(), 1000)['guardian_approval'])
        for row in ({'v': True}, {'value': 1}, {'value': 'true'}, {}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                _project(encode({**response(), 'feature_gates': {GUARDIAN_GATE: row}}), context(), 1000)

    def test_identity_locale_version_and_runtime_targeting_must_all_match(self):
        cases = [('userID',), ('locale',), ('appVersion',), ('customIDs', 'account_id'),
                 ('customIDs', 'stableID'), *[('custom', key) for key in response()['user']['custom']]]
        for path in cases:
            payload = response(); obj = payload['user']
            for key in path[:-1]: obj = obj[key]
            obj[path[-1]] = 'different'
            with self.subTest(path=path), self.assertRaises(ValueError):
                _project(encode(payload), context(), 1000)

    def test_missing_gate_old_future_or_invalid_timestamp_is_unknown(self):
        for timestamp in (0, 699000, 1061000, True, '1000000', None):
            payload = response(); payload['time'] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                _project(encode(payload), context(), 1000)
        with self.assertRaises(ValueError): _project(encode({**response(), 'feature_gates': {}}), context(), 1000)
        with self.assertRaises(ValueError): _project(b'x' * (MAX_BODY + 1), context(), 1000)


class FeatureCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.ctx = context(); self.now = 0
        self.calls = 0; self.failure = False; self.during = None
        def transport(ctx):
            self.calls += 1; data = encode(response(ctx, now=1000 + self.now))
            if self.during: self.during()
            if self.failure: raise TimeoutError('synthetic private error')
            return data
        self.reader = DesktopFeatures(self.root/'Native.app', self.root,
            context_reader=lambda: copy.deepcopy(self.ctx), transport=transport,
            clock=lambda: self.now, wall_clock=lambda: 1000 + self.now)

    def test_one_bootstrap_shared_and_cache_cannot_be_mutated_by_caller(self):
        first = self.reader.get(); first['guardian_approval'] = False
        for _ in range(10): self.assertTrue(self.reader.get()['guardian_approval'])
        self.assertEqual(self.calls, 1); self.now = 601
        self.assertTrue(self.reader.get()['guardian_approval']); self.assertEqual(self.calls, 2)

    def test_fresh_network_check_without_semantic_revision_churn(self):
        first = self.reader.get(); self.now += 1
        self.assertEqual(first, self.reader.get(fresh=True)); self.assertEqual(self.calls, 2)

    def test_failed_fresh_read_revokes_previous_positive_and_throttles(self):
        self.assertTrue(self.reader.get()['guardian_approval']); self.failure = True
        self.assertIsNone(self.reader.get(fresh=True))
        for _ in range(10): self.assertIsNone(self.reader.get())
        self.assertEqual(self.calls, 2); self.now += 31; self.failure = False
        self.assertTrue(self.reader.get()['guardian_approval']); self.assertEqual(self.calls, 3)

    def test_initial_failure_throttled_too(self):
        self.failure = True
        for _ in range(10): self.assertIsNone(self.reader.get())
        self.assertEqual(self.calls, 1)

    def test_context_change_and_midflight_identity_race_invalidate(self):
        first = self.reader.get(); self.ctx.update(stamp='new-auth', semantic='new-user'); self.now += 6
        self.assertNotEqual(first['revision'], self.reader.get()['revision'])
        self.during = lambda: self.ctx.update(stamp='changed-during-request')
        self.assertIsNone(self.reader.get(fresh=True)); self.assertIsNone(self.reader._result)

    def test_credentials_change_forces_context_check_inside_short_ttl(self):
        first = self.reader.get(); self.ctx.update(stamp='new-auth', semantic='new-user')
        (self.root/'auth.json').write_text('synthetic change')
        self.assertNotEqual(first['revision'], self.reader.get()['revision'])


class NativeContextTests(unittest.TestCase):
    def test_browser_language_preferences_must_agree_with_renderer(self):
        for preferences in ({}, {'intl': {}}, {'intl': {'accept_languages': 'en-US,en'}}):
            _check_language_preferences(preferences, 'en-US')
        for intl in (None, {'accept_languages': 'fr-FR'}, {'selected_languages': ['en-US']},
                     {'app_locale': 'fr'}, {'accept_languages': ''}):
            with self.subTest(intl=intl), self.assertRaises(ValueError):
                _check_language_preferences({'intl': intl}, 'en-US')

    def test_exact_app_process_and_unambiguous_renderer_locale(self):
        app = PurePosixPath('/Applications/Example.app')
        main = f'12 1 999 Sat Nov 7 08:00:00 2026 {app}/Contents/MacOS/Codex\n'
        child = f'13 12 999 Sat Nov 7 08:00:01 2026 {app}/Contents/Frameworks/Helper --type=renderer --lang=en-US\n'
        with patch('aw.control.codex_features.os.getuid', return_value=999, create=True), patch('aw.control.codex_features.subprocess.run') as run:
            run.return_value.stdout = main + child
            self.assertEqual(_processes(app, 'Codex'), (12, 'Sat Nov 7 08:00:00 2026', 'en-US'))
            for value in (main, main + child + child.replace('en-US', 'fr-FR'), main + main + child):
                run.return_value.stdout = value
                with self.assertRaises(ValueError): _processes(app, 'Codex')

    def test_only_current_pid_open_log_descriptor_identifies_session(self):
        home = PurePosixPath('/example/home')
        name = 'codex-desktop-11111111-1111-4111-8111-111111111111-12-t0-i1-080000-0.log'
        log = str(home/'Library/Logs/com.openai.codex/2026/11/07'/name)
        with patch('aw.control.codex_features.subprocess.run') as run:
            run.return_value.stdout = 'p12\nn' + log + '\n'
            self.assertEqual(_app_session(12, home), '11111111-1111-4111-8111-111111111111')
            run.return_value.stdout = 'n' + log.replace('-12-t0-', '-99-t0-') + '\n'
            with self.assertRaises(ValueError): _app_session(12, home)

    def test_secret_headers_use_stdin_and_redirect_is_rejected(self):
        class Input(io.BytesIO):
            def close(self): self.saved = self.getvalue(); super().close()
        class Process:
            def __init__(self, output): self.stdin, self.stdout = Input(), io.BytesIO(output)
            def wait(self, **kwargs): return 0
            def poll(self): return 0
            def kill(self): pass
        for status in (b'200', b'302'):
            proc = Process(b'{}\n' + status)
            with patch('aw.control.codex_features.subprocess.Popen', return_value=proc) as popen:
                if status == b'200': self.assertEqual(_curl_bootstrap(context()), b'{}')
                else:
                    with self.assertRaises(ValueError): _curl_bootstrap(context())
            args, kwargs = popen.call_args
            self.assertNotIn(context()['token'], repr(args)); self.assertNotIn(context()['token'], repr(kwargs))
            self.assertIn(context()['token'].encode(), proc.stdin.saved)
            self.assertEqual(args[0][1], '--disable'); self.assertNotIn('--location', args[0])
            self.assertEqual(args[0][args[0].index('--max-time') + 1], '20')
            self.assertNotIn('desktop_app_beta_enabled', proc.stdin.saved.decode())


if __name__ == '__main__': unittest.main()

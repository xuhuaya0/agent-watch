"""Token direction, migration, and task ordering regressions."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

from aw.config import DEFAULTS, load_config, save_config
from aw.model import Task
from aw.pusher import MultiIpPusher
from aw.store import Store


class TokenMigrationTests(unittest.TestCase):
    def config(self, values):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / 'config.json')
        Path(path).write_text(json.dumps(values))
        return path

    def test_legacy_connected_client_keeps_outbound_credential(self):
        path = self.config({'hub_urls': ['http://hub'], 'ingest_token': 'old-shared'})
        cfg = load_config(path)
        self.assertEqual(cfg['hub_token'], 'old-shared')
        save_config(cfg, path)
        cfg['ingest_token'] = 'new-local-receiver'
        save_config(cfg, path)
        self.assertEqual(load_config(path)['hub_token'], 'old-shared')

    def test_hub_without_upstream_does_not_fill_outbound_token(self):
        self.assertEqual(load_config(self.config({'ingest_token': 'receiver'}))['hub_token'], '')

    def test_explicit_empty_or_distinct_outbound_token_never_falls_back(self):
        for token in ['', 'destination']:
            with self.subTest(token=token):
                cfg = load_config(self.config({'hub_urls': ['http://hub'],
                    'ingest_token': 'receiver', 'hub_token': token}))
                self.assertEqual(cfg['hub_token'], token)


class PushFeedbackTests(unittest.TestCase):
    def test_missing_outbound_token_does_not_send_unauthenticated_request(self):
        with patch('aw.pusher.urllib.request.urlopen') as send:
            ok, message = MultiIpPusher().push(['http://hub'], '', 'pc', [])
        self.assertFalse(ok)
        self.assertIn('上报令牌', message)
        send.assert_not_called()

    def test_401_explains_which_token_to_copy_and_closes_response(self):
        response = MagicMock()
        error = HTTPError('http://hub', 401, 'Unauthorized', {}, response)
        with patch('aw.pusher.urllib.request.urlopen', side_effect=error):
            ok, message = MultiIpPusher().push(['http://hub'], 'wrong', 'pc', [])
        self.assertFalse(ok)
        self.assertIn('本机接收令牌', message)
        self.assertIn('上报令牌', message)
        self.assertNotIn('wrong', message)
        response.close.assert_called_once()

    def test_generic_push_path_and_old_hub_fallback(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        for code in [None, 404, 405]:
            with self.subTest(code=code):
                replies = [response] if code is None else [HTTPError('http://hub/api/push', code, 'legacy', {}, None), response]
                with patch('aw.pusher.urllib.request.urlopen', side_effect=replies) as send:
                    ok, _ = MultiIpPusher().push(['http://hub'], 'destination', 'pc', [])
                self.assertTrue(ok)
                urls = [call.args[0].full_url for call in send.call_args_list]
                self.assertEqual(urls, ['http://hub/api/push'] + ([] if code is None else ['http://hub/api/ingest']))

    def test_authentication_failure_does_not_retry_legacy_path(self):
        with patch('aw.pusher.urllib.request.urlopen', side_effect=HTTPError('http://hub',401,'unauthorized',{},None)) as send:
            ok, _ = MultiIpPusher().push(['http://hub'], 'wrong', 'pc', [])
        self.assertFalse(ok)
        self.assertEqual(send.call_count, 1)


class DashboardSettingsTests(unittest.TestCase):
    def setUp(self):
        from hub_fixture import isolated_hub
        hub = self.enterContext(isolated_hub())
        from aw.auth import AuthManager
        self.hub = hub
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg.update(ingest_token='receiver', hub_token='destination',
                        summary_token='readonly', hub_urls=['http://hub'])
        self.store = Store(self.cfg, tmp.name)
        auth = AuthManager(tmp.name, self.cfg)
        auth.set_password('test-password')
        for name, value in [('CFG', self.cfg), ('AUTH', auth), ('STORE', self.store)]:
            p = patch.object(hub, name, value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(hub, 'save_config')
        p.start(); self.addCleanup(p.stop)
        self.client = hub.app.test_client()
        login = self.client.post('/api/auth/login', json={'password': 'test-password'})
        self.headers = {'X-CSRF-Token': login.get_json()['csrf_token']}

    def test_saving_outbound_token_does_not_change_receiver(self):
        with patch.object(self.hub, 'trigger_scan'):
            r = self.client.post('/api/settings', json={'hub_token':'new-destination'}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['hub_token'], 'new-destination')
        self.assertEqual(self.cfg['ingest_token'], 'receiver')
        self.assertTrue(self.hub.AUTH.verify_ingest_token('Bearer receiver'))
        self.assertFalse(self.hub.AUTH.verify_ingest_token('Bearer new-destination'))

    def test_rotating_receiver_does_not_change_outbound(self):
        r = self.client.post('/api/auth/rotate-token', json={'kind':'ingest_token'}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(self.cfg['ingest_token'], 'receiver')
        self.assertEqual(self.cfg['hub_token'], 'destination')

    def test_hub_push_uses_outbound_token(self):
        with patch.object(self.hub, 'scan_all', return_value=([], {})), \
             patch.object(self.hub.PUSHER, 'push') as send:
            self.hub.scan_once_and_push()
        self.assertEqual(send.call_args.args[1], 'destination')

    def test_collector_push_uses_outbound_token(self):
        import collector
        with patch.object(collector, 'load_config', return_value=self.cfg), \
             patch.object(collector, 'scan_all', return_value=([], {})), \
             patch.object(collector.MultiIpPusher, 'push', return_value=(True, 'http://hub')) as send, \
             patch('sys.argv', ['collector.py', '--once']):
            collector.main()
        self.assertEqual(send.call_args.args[1], 'destination')

    def test_attention_then_running_then_done_in_api_and_summary(self):
        now = time.time()
        self.store.set_local([Task(tool='codex', session_id=state, title=state,
            state=state, state_since=now-10, last_event_at=now)
            for state in ['done', 'running', 'waiting_approval']], {})
        for path, field in [('/api/tasks', 'tasks'), ('/api/summary', 'items')]:
            data = self.client.get(path).get_json()
            self.assertEqual([t['state'] for t in data[field]], ['waiting_approval','running','done'])

    def test_generic_push_get_authentication_and_legacy_compatibility(self):
        anon = self.hub.app.test_client()
        incoming = {'Authorization':'Bearer receiver'}
        reading = {'Authorization':'Bearer readonly'}
        payload = {'machine':'sample-device', 'tasks':[]}
        self.assertEqual(anon.post('/api/push', json=payload).status_code,401)
        self.assertEqual(anon.get('/api/get').status_code,401)
        self.assertEqual(anon.post('/api/push', json=payload, headers=reading).status_code,401)
        self.assertEqual(anon.get('/api/get', headers=incoming).status_code,401)
        self.assertEqual(anon.post('/api/push', json=payload, headers=incoming).status_code,200)
        result = anon.get('/api/get', headers=reading)
        self.assertEqual(result.status_code,200)
        self.assertIn('sample-device',result.get_json()['machines'])
        tag = result.headers['ETag']
        self.assertEqual(anon.get('/api/get', headers={**reading,'If-None-Match':tag}).status_code,304)
        self.assertEqual(anon.get('/api/summary', headers={**reading,'If-None-Match':tag}).status_code,304)
        self.assertEqual(anon.get('/api/get?token=readonly').status_code,200)
        self.assertEqual(anon.get('/api/push', headers=incoming).status_code,405)
        self.assertEqual(anon.post('/api/get', headers=reading).status_code,405)
        self.assertEqual(anon.post('/api/ingest', json=payload, headers=incoming).status_code,200)

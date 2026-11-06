"""Isolation and response contracts for the optional standard-library UI fixture."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ui_preview', Path(__file__).resolve().parents[1]/'tools'/'preview_ui.py')
preview_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview_module)
Preview = preview_module.Preview


class PreviewTests(unittest.TestCase):
    def test_media_fixture_uses_only_synthetic_opaque_references_and_a_bounded_raster(self):
        fixture = Preview('media')
        page = fixture.route('GET', '/api/history/tasks/cx-ready')[2]
        message = page['messages'][-1]
        self.assertIn('[代码已提交](https://example.test/', message['text'])
        attachment = message['attachments'][0]
        self.assertTrue(attachment['source'].startswith('/example/'))
        status, kind, data = fixture.route('GET', '/api/attachments/tasks/cx-ready?id=' + attachment['id'])
        self.assertEqual((status, kind), (200, 'image/png'))
        self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertLess(len(data), 10000)
        self.assertEqual(fixture.route('GET', '/api/attachments/tasks/cx-ready?path=/example/image.png')[0], 404)

    def test_scenarios_use_real_assets_and_only_synthetic_data(self):
        fixture = Preview(clock=lambda: 1800000000)
        for scenario in preview_module.SCENARIOS:
            status,kind,page = fixture.route('GET','/?scenario='+scenario)
            self.assertEqual((status,kind),(200,'text/html'))
            self.assertIn('/static/workspace.js',page)
            self.assertIn('管理员登录',page)
            self.assertIn('合成预览',page)
        fixture.select('normal')
        data = fixture.route('GET','/api/tasks?all=1')[2]
        self.assertEqual({t['tool'] for t in data['tasks']},{'codex','antigravity','dsh'})
        self.assertEqual({t['state'] for t in data['tasks']},{'done','running','waiting_input','waiting_approval'})
        for task in data['tasks']:
            self.assertIn(task['machine_ips'][0],('192.0.2.10','192.0.2.20'))
            self.assertNotIn('latest_turn', task['extra'])
            detail = fixture.route('GET','/api/tasks/'+task['key'])[2]
            self.assertEqual(detail['task']['key'],task['key'])
            self.assertIn('latest_turn', detail['task']['extra'])
            control = fixture.route('GET','/api/control/tasks/'+task['key'])[2]
            self.assertEqual(control['capability']['tool'],task['tool'])
            history = fixture.route('GET','/api/history/tasks/'+task['key'])[2]
            self.assertTrue(history['ready']);self.assertIsInstance(history['messages'],list)

    def test_writes_cannot_change_native_control_config_or_credentials(self):
        fixture = Preview(clock=lambda: 1800000000)
        before = fixture.route('GET','/api/settings')[2]
        for method,path in [('POST','/api/control/tasks/cx-ready'),('PATCH','/api/control/devices/demo-mac'),
                            ('POST','/api/settings'),('POST','/api/auth/password'),('POST','/api/auth/rotate-token'),
                            ('DELETE','/api/machines/demo-pc')]:
            status,_,body = fixture.route(method,path,{'password':'do-not-store','action':{'kind':'reply','text':'do-not-send'}})
            self.assertEqual(status,409)
            self.assertEqual(body['error'],'preview_read_only')
        self.assertEqual(fixture.route('GET','/api/settings')[2],before)
        self.assertNotIn('do-not-store',str(fixture.__dict__))
        self.assertNotIn('do-not-send',str(fixture.__dict__))

    def test_asset_path_cannot_read_parent_files_or_unknown_extensions(self):
        fixture = Preview()
        for path in ('/static/../AGENTS.md','/static/%2e%2e/AGENTS.md',
                     '/static//etc/passwd','/static/index.html','/api/unknown'):
            self.assertEqual(fixture.route('GET',path)[0],404)
        self.assertEqual(fixture.route('GET','/static/workspace.css')[0],200)

    def test_demo_login_and_offline_failure_modes_are_explicit(self):
        fixture = Preview('login')
        self.assertFalse(fixture.route('GET','/api/auth/status')[2]['authenticated'])
        self.assertEqual(fixture.route('GET','/api/tasks')[0],401)
        fixture.route('POST','/api/auth/login',{'password':'not-saved'})
        self.assertEqual(fixture.route('GET','/api/tasks')[0],200)
        self.assertNotIn('not-saved',str(fixture.__dict__))
        fixture.select('offline')
        self.assertFalse(fixture.route('GET','/api/control/tasks/cx-ready')[2]['connected'])
        self.assertEqual(fixture.route('GET','/api/history/tasks/cx-ready')[2]['error'],'history_unavailable')
        cached_reply = fixture.route('GET', '/api/tasks/ag-ready')[2]['task']['extra']['latest_turn']['assistant']
        self.assertIn('第 30 段缓存回复', cached_reply)
        fixture.select('auth_failure');self.assertEqual(fixture.route('GET','/api/auth/status')[0],503)
        fixture.select('failure');self.assertEqual(fixture.route('GET','/api/tasks')[0],503)
        fixture.select('empty');self.assertEqual(fixture.route('GET','/api/tasks')[2]['tasks'],[])

    def test_refresh_changes_detail_activity_and_native_revision_independently(self):
        now = [1800000000]
        fixture = Preview('refresh',clock=lambda:now[0])
        first = fixture.route('GET','/api/tasks/cx-ready')[2]['task']
        now[0] += 5
        second = fixture.route('GET','/api/tasks/cx-ready')[2]['task']
        self.assertGreater(second['activity_at'],first['activity_at'])
        self.assertEqual(second['title'],first['title'])
        fixture.select('changed')
        initial = fixture.route('GET','/api/control/tasks/ag-ready')[2]['capability']['policy']
        now[0] += 15
        changed = fixture.route('GET','/api/control/tasks/ag-ready')[2]['capability']['policy']
        self.assertNotEqual(initial['revision'],changed['revision'])
        self.assertNotEqual(initial['current'],changed['current'])

    def test_reader_can_compare_a_stable_midpage_landmark_across_updates_and_prepend(self):
        now = [1800000000]
        fixture = Preview('refresh', clock=lambda: now[0])
        path = '/api/history/tasks/cx-ready'
        first = fixture.route('GET', path)[2]
        now[0] += 5
        refreshed = fixture.route('GET', path)[2]
        self.assertGreater(len(first['messages']), 20)
        self.assertEqual(first['messages'][:-1], refreshed['messages'][:-1])
        self.assertNotEqual(first['messages'][-1]['text'], refreshed['messages'][-1]['text'])
        process = first['messages'][-4:-1]
        self.assertEqual([m['role'] for m in process], ['tool', 'assistant', 'tool'])
        self.assertEqual(process[1]['phase'], 'commentary')
        self.assertTrue(all(m['text'] for m in process))
        older = fixture.route('GET', path + '?cursor=' + refreshed['next_cursor'])[2]
        self.assertIsNone(older['next_cursor'])
        self.assertTrue(older['messages'])
        self.assertTrue({m['id'] for m in older['messages']}.isdisjoint(m['id'] for m in refreshed['messages']))

    def test_codex_model_and_full_permissions_follow_capability_contract_without_native_writes(self):
        from aw.control.codex_settings import valid_model_capability
        from aw.control.codex_policy import valid_capability
        fixture = Preview()
        cap = fixture.route('GET', '/api/control/tasks/cx-ready')[2]['capability']
        self.assertTrue(valid_model_capability(cap['model']))
        self.assertTrue(valid_capability(cap['policy']))
        self.assertEqual(cap['policy']['scope'], 'session-permissions')
        self.assertEqual(sum(option.get('group') != 'advanced' for option in cap['policy']['options']), 3)
        self.assertEqual(sum(option.get('group') == 'advanced' for option in cap['policy']['options']), 1)
        self.assertTrue(all(option['value'].startswith('example-') for option in cap['model']['options']))
        self.assertTrue(any(not option['efforts'] for option in cap['model']['options']))
        for action in ({'kind':'model', 'revision':cap['model']['revision'], 'model':'example-model-b', 'effort':'medium'},
                       {'kind':'policy', 'revision':cap['policy']['revision'], 'value':'demo-read-only'}):
            status, _, response = fixture.route('POST', '/api/control/tasks/cx-ready', {'action':action})
            self.assertEqual(status, 409)
            self.assertEqual(response['error'], 'preview_read_only')
        self.assertEqual(cap, fixture.route('GET', '/api/control/tasks/cx-ready')[2]['capability'])
        running = fixture.route('GET', '/api/control/tasks/cx-running')[2]['capability']
        self.assertEqual(running['model']['options'], [])
        self.assertEqual(running['delivery']['state'], 'running')
        self.assertTrue(running['reply'])
        self.assertIn('model', running['settings'])

    def test_readonly_and_unknown_settings_are_distinct_for_all_harnesses(self):
        fixture = Preview()
        for scenario in ('readonly', 'unknown_settings'):
            fixture.select(scenario)
            for sid in ('cx-ready', 'ag-ready', 'ds-ready'):
                cap = fixture.route('GET', '/api/control/tasks/' + sid)[2]['capability']
                self.assertTrue(cap['reply'])
                self.assertTrue({'model', 'policy', 'create'}.isdisjoint(cap))
                if scenario == 'readonly':
                    self.assertIn('model', cap['settings'])
                    self.assertIn('permission', cap['settings'])
                else:
                    self.assertNotIn('settings', cap)

    def test_changing_models_updates_revision_and_compatible_reasoning_effort(self):
        now = [1800000000]
        fixture = Preview('changed', clock=lambda: now[0])
        first = fixture.route('GET', '/api/control/tasks/cx-ready')[2]['capability']
        now[0] += 15
        changed = fixture.route('GET', '/api/control/tasks/cx-ready')[2]['capability']
        self.assertNotEqual(first['model']['revision'], changed['model']['revision'])
        self.assertNotEqual(first['model']['current'], changed['model']['current'])
        selected = next(option for option in changed['model']['options'] if option['value'] == changed['model']['current'])
        self.assertIn(changed['model']['effort'], selected['efforts'])
        self.assertEqual(changed['settings']['model'], selected['label'])


if __name__ == '__main__':unittest.main()

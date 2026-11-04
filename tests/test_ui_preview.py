"""Isolation and response contracts for the optional standard-library UI fixture."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ui_preview', Path(__file__).resolve().parents[1]/'tools'/'preview_ui.py')
preview_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview_module)
Preview = preview_module.Preview


class PreviewTests(unittest.TestCase):
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
            detail = fixture.route('GET','/api/tasks/'+task['key'])[2]
            self.assertEqual(detail['task']['key'],task['key'])
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


if __name__ == '__main__':unittest.main()

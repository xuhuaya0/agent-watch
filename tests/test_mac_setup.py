"""Installer boundaries; synthetic credentials and no real native control writes."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import tarfile
import urllib.error
import urllib.request

from tools import mac_setup as setup
from aw.config import DEFAULTS


class MacSetupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg.update(machine_id='11111111-1111-4111-8111-111111111111',
                        hub_urls=['https://hub.example'], hub_token='synthetic-push-secret')

    def test_hub_rejects_credentials_paths_queries_and_ambiguous_urls(self):
        for value in ['file:///tmp/config', 'https://user:password@hub.example',
                      'https://hub.example/api/push', 'https://hub.example?token=private',
                      'https://hub.example/#x', 'https://hub.example:invalid', 'http://host\n.example']:
            with self.subTest(value=value), self.assertRaises(setup.SetupError):
                setup.hub_url(value)
        self.assertEqual(setup.hub_url(' https://hub.example:8090/ '), 'https://hub.example:8090')

    def test_redirect_is_not_followed_with_push_or_login_secrets(self):
        client = setup.api_client()
        self.assertTrue(any(isinstance(h, setup.NoRedirect) for h in client.handlers))
        request = urllib.request.Request('https://hub.example', headers={'Authorization':'Bearer synthetic'})
        with self.assertRaises(setup.SetupError):
            setup.NoRedirect().redirect_request(request, None, 307, '', {}, 'https://elsewhere.example')

    def test_error_details_never_echo_remote_body_or_credentials(self):
        client = Mock()
        client.open.side_effect = urllib.error.HTTPError('https://hub.example', 401,
            'synthetic-private-value', {}, io.BytesIO(b'synthetic-private-value'))
        with self.assertRaises(setup.SetupError) as caught:
            setup.api(client, 'https://hub.example', '/api/push', {}, {'Authorization':'Bearer synthetic-private-value'})
        self.assertIn('401', str(caught.exception))
        self.assertNotIn('synthetic-private-value', str(caught.exception))

    def test_provision_is_device_bound_private_and_repeat_does_not_rotate(self):
        answers = [{'ok':True,'csrf_token':'synthetic-csrf'},
                   {'ok':True,'device_id':self.cfg['machine_id'],'token':'synthetic-device-token'}]
        with patch.object(setup, 'api', side_effect=answers) as api:
            path = setup.pair_codex(self.root, self.root, self.cfg, 'synthetic-admin-password')
            self.assertEqual(api.call_count, 2)
            self.assertEqual(api.call_args.args[2], '/api/control/devices/'+self.cfg['machine_id'])
            self.assertEqual(api.call_args.args[4], {'X-CSRF-Token':'synthetic-csrf'})
            self.assertEqual(setup.pair_codex(self.root,self.root,self.cfg,''),path)
            self.assertEqual(api.call_count,2)
        data = json.loads(path.read_text())
        self.assertEqual(data['codex']['scope'], 'all')
        self.assertEqual(data['codex']['transport'], 'desktop-ipc')
        self.assertNotIn('synthetic-admin-password', path.read_text())
        self.assertNotEqual(data['token'],self.cfg['hub_token'])
        if os.name != 'nt': self.assertEqual(path.stat().st_mode & 0o777,0o600)
        other = dict(self.cfg, hub_urls=['https://other.example'])
        with self.assertRaises(setup.SetupError):setup.pair_codex(self.root,self.root,other,'')

    def test_lost_provision_ack_is_not_automatically_retried_or_rotated(self):
        with patch.object(setup, 'api', side_effect=[{'ok':True,'csrf_token':'synthetic'},setup.SetupError('timeout')]) as api:
            with self.assertRaises(setup.SetupError):setup.pair_codex(self.root,self.root,self.cfg,'synthetic')
            self.assertEqual(api.call_count,2)
            with self.assertRaises(setup.SetupError):setup.pair_codex(self.root,self.root,self.cfg,'synthetic')
            self.assertEqual(api.call_count,2)
        self.assertFalse((self.root/'private/codex/control.json').exists())

    def test_archive_traversal_links_and_devices_are_rejected(self):
        for name, kind in [('root/../../outside',tarfile.REGTYPE),
                           ('root/link',tarfile.SYMTYPE), ('root/dev',tarfile.CHRTYPE)]:
            archive = self.root/'source.tar'
            with tarfile.open(archive,'w') as tar:
                item=tarfile.TarInfo(name);item.type=kind;item.linkname='/tmp/outside';tar.addfile(item)
            with self.assertRaises(setup.SetupError):setup.extract_source(archive,self.root/'out')
        self.assertFalse((self.root/'outside').exists())

    def test_prepare_refuses_unowned_existing_directory_before_installing(self):
        (self.root/'program').mkdir()
        with patch.object(setup.subprocess,'run') as run, self.assertRaises(setup.SetupError):
            setup.prepare(self.root,Path('/synthetic/uv'))
        run.assert_not_called()

    def test_control_job_refuses_overwriting_another_install(self):
        home = self.root/'home';path=home/'Library/LaunchAgents/com.agentwatch.codex.plist'
        path.parent.mkdir(parents=True)
        path.write_bytes(setup.plistlib.dumps({'Label':'com.agentwatch.codex','ProgramArguments':['other']}))
        before=path.read_bytes()
        with patch.object(setup.Path,'home',return_value=home), patch.object(setup.subprocess,'run') as run:
            with self.assertRaises(setup.SetupError):
                setup.install_control_job(self.root, self.root/'python',self.root/'control.json')
        self.assertEqual(path.read_bytes(),before)
        run.assert_not_called()

    def test_first_run_connects_and_pairs_without_saving_admin_password(self):
        from types import SimpleNamespace
        program=self.root/'program';program.mkdir()
        fake_home=self.root/'home';fake_home.mkdir()
        calls=[]
        def request(client, base, endpoint, payload=None, headers=None):
            calls.append((endpoint,payload,headers))
            if endpoint=='/api/auth/login':return {'ok':True,'csrf_token':'synthetic-csrf'}
            if endpoint.startswith('/api/control/devices/'):
                return {'ok':True,'device_id':endpoint.rsplit('/',1)[-1],'token':'synthetic-control'}
            cfg=json.loads((program/'config.json').read_text())
            return {'ok':True,'devices':[{'device_id':cfg['machine_id'],'connected':True}]}
        output=io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(setup.Path,'home',return_value=fake_home))
            stack.enter_context(patch('builtins.input',side_effect=['https://hub.example','y','n']))
            stack.enter_context(patch.object(setup.getpass,'getpass',side_effect=['synthetic-push','synthetic-admin-password']))
            stack.enter_context(patch.object(setup,'available_port',return_value=18091))
            stack.enter_context(patch.object(setup,'scan_and_push',return_value=([SimpleNamespace(tool='codex')],{})))
            stack.enter_context(patch.object(setup,'api',side_effect=request))
            stack.enter_context(patch('aw.config.save_config',side_effect=lambda cfg:setup.private_json(program/'config.json',cfg)))
            job=stack.enter_context(patch.object(setup,'install_control_job'))
            run=stack.enter_context(patch.object(setup.subprocess,'run'))
            stack.enter_context(contextlib.redirect_stdout(output))
            setup.configure(self.root,program,program/'.venv/bin/python',self.root/'Agent Watch.app')
        self.assertEqual(len(calls),3)
        self.assertEqual(job.call_count,1)
        self.assertEqual(run.call_args.args[0],['open',str(self.root/'Agent Watch.app')])
        self.assertIn('中心已确认 Codex 交互连接',output.getvalue())
        for path in self.root.rglob('*.json'):
            self.assertNotIn('synthetic-admin-password',path.read_text())
        self.assertNotIn('synthetic-push',output.getvalue())
        cfg=json.loads((program/'config.json').read_text())
        self.assertEqual(cfg['host'],'127.0.0.1')
        self.assertTrue(cfg['adapters']['codex']['enabled'])
        self.assertFalse(cfg['adapters']['dsh']['enabled'])

    @unittest.skipIf(os.name=='nt','POSIX file locks')
    def test_menu_instance_lock_covers_launch_agent_and_app_starts(self):
        from aw.mac_instance import acquire
        first=acquire(self.root)
        try:
            self.assertIsNotNone(first)
            pid=(self.root/'tray.pid').read_text()
            self.assertIsNone(acquire(self.root))
            self.assertEqual((self.root/'tray.pid').read_text(),pid)
        finally:first.close()
        second=acquire(self.root)
        self.assertIsNotNone(second);second.close()


if __name__=='__main__':unittest.main()

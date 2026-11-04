import json
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from aw.control.broker import ControlError
from aw.control.codex import Codex
from aw.control.codex_ipc import CodexDesktop
from aw.control.codex_create import capability, DesktopDraft, _verified_desktop_bundle
from aw.control.sessions import CodexScope


SOURCE = '00000000-0000-4000-8000-000000000001'
TARGET = '00000000-0000-4000-8000-000000000002'


class SharedServer:
    def __init__(self, directory):
        self.cwd = directory
        self.calls = []
        self.loaded = [SOURCE]
        self.first_turn = None
        self.wrong_target = False
        self.lose = None
        self.on_start = None

    def call(self, method, params):
        self.calls.append((method, params))
        if method == self.lose:
            raise TimeoutError()
        if method == 'thread/loaded/list':
            return {'data':list(self.loaded), 'nextCursor':None}
        if method == 'thread/resume':
            return {}
        if method == 'thread/read':
            return {'thread':{'id':params['threadId'], 'cwd':self.cwd}}
        if method == 'thread/start':
            if self.on_start:
                self.on_start()
            return {'cwd':self.cwd, 'thread':{'id':SOURCE if self.wrong_target else TARGET,
                                             'cwd':self.cwd, 'ephemeral':False}}
        if method == 'turn/start':
            if self.first_turn:
                self.first_turn()
            return {'turn':{'id':'first-turn'}}
        raise AssertionError(method)


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(self.tmp)
        self.config = root/'control.json'
        self.config.write_text(json.dumps({'codex':{'scope':'all', 'data_root':self.tmp}}))
        self.db = sqlite3.connect(root/'state_5.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE threads(id,source,archived,updated_at,cwd)')
        self.db.execute('INSERT INTO threads VALUES(?,?,?,?,?)', (SOURCE,'desktop',0,1,self.tmp))
        self.db.commit()
        self.scope = CodexScope(self.config)
        self.rpc = SharedServer(self.tmp)
        self.provider = Codex('unused','unused', self.scope, rpc_factory=lambda *_: self.rpc)
        self.text = '多行 & more\n?path=/untrusted#path'
        self.command = {'id':'cmd','session_id':SOURCE,'action':{'kind':'create',
            'text':self.text,'revision':capability(SOURCE,self.tmp)['revision']}}

    def writes(self):
        return [(op,p) for op,p in self.rpc.calls if op in ('thread/start','turn/start')]

    def test_shared_server_acknowledges_bound_target_and_native_defaults(self):
        result = self.provider.execute(self.command)
        self.assertEqual(result, {'state':'delivered','created_session_id':TARGET})
        self.assertEqual(self.writes(), [
            ('thread/start', {'cwd':self.tmp,'threadSource':'user'}),
            ('turn/start', {'threadId':TARGET,'input':[{'type':'text','text':self.text}], 'clientUserMessageId':'cmd'})])

    def test_created_user_thread_remains_controllable_by_same_provider(self):
        self.db.execute('ALTER TABLE threads ADD COLUMN thread_source TEXT')
        def persist_turn():
            self.db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)',
                            (TARGET,'appServer',0,2,self.tmp,'user'))
            self.db.commit()
            self.rpc.loaded.append(TARGET)
            self.rpc.first_turn = None
        self.rpc.first_turn = persist_turn
        self.assertEqual(self.provider.execute(self.command)['created_session_id'], TARGET)
        self.assertEqual(self.provider.session_snapshot(TARGET)[0]['session_id'],TARGET)
        self.assertEqual(self.provider.execute({'session_id':TARGET, 'id':'followup',
                              'action':{'kind':'reply', 'text':'Follow up'}}), 'delivered')
        self.assertEqual(self.rpc.calls[-1][1]['threadId'],TARGET)

    def test_first_turn_approval_arriving_before_catalog_refresh_is_retained(self):
        def pending_approval():
            self.assertNotIn(TARGET, self.scope())
            self.provider.event({'id':7, 'method':'item/commandExecution/requestApproval',
                'params':{'threadId':TARGET, 'turnId':'first-turn',
                          'command':'printf synthetic-test', 'cwd':self.tmp}})
            self.db.execute('INSERT INTO threads VALUES(?,?,?,?,?)', (TARGET,'desktop',0,2,self.tmp))
            self.db.commit()
            self.rpc.loaded.append(TARGET)
        self.rpc.first_turn = pending_approval
        self.assertEqual(self.provider.execute(self.command)['state'], 'delivered')
        row = self.provider.session_snapshot(TARGET)[0]
        self.assertEqual(row['requests'][0]['id'], '7')
        self.assertEqual(row['requests'][0]['kind'], 'approval')

    def test_changed_native_connection_cannot_receive_new_turn_or_destination(self):
        replacement = SharedServer(self.tmp)
        self.rpc.on_start = lambda: setattr(self.provider, 'rpc', replacement)
        self.assertEqual(self.provider.execute(self.command), {'state':'unknown', 'created_session_id':TARGET})
        self.assertNotIn(TARGET, self.provider.session_ids)
        self.assertEqual([method for method, _ in self.writes()], ['thread/start'])
        self.assertFalse(any(method == 'turn/start' for method, _ in replacement.calls))

    def test_malformed_post_turn_readback_keeps_known_destination(self):
        original = self.rpc.call
        def mismatched(method, params):
            value = original(method,params)
            if method=='thread/read' and params['threadId']==TARGET:
                value['thread']['cwd'] = '/other'
            return value
        self.rpc.call=mismatched
        self.assertEqual(self.provider.execute(self.command), {'state':'unknown','created_session_id':TARGET})
        self.assertEqual([m for m,_ in self.writes()], ['thread/start','turn/start'])

    def test_scope_and_revisions_rechecked_before_creation(self):
        for cause in ('scope','archive','new_directory','wrong_revision'):
            with self.subTest(cause=cause):
                with self.config.open('w') as config:
                    json.dump({'codex':{'scope':'selected' if cause=='scope' else 'all',
                                        'session_ids':[SOURCE],'data_root':self.tmp}},config)
                self.db.execute('UPDATE threads SET archived=?,cwd=?',
                                (cause=='archive', '/changed' if cause=='new_directory' else self.tmp))
                self.db.commit()
                if cause=='wrong_revision':
                    self.command['action']['revision']='stale'
                with self.assertRaises(ControlError):self.provider.execute(self.command)
                self.assertEqual(self.writes(), [])

    def test_scope_revocation_during_native_read_prevents_the_next_write(self):
        original = self.rpc.call
        for revoke_on_read in (1, 2):
            with self.subTest(revoke_on_read=revoke_on_read):
                self.config.write_text(json.dumps({'codex':{'scope':'all', 'data_root':self.tmp}}))
                self.rpc.calls.clear()
                count = 0
                def revoke_during_read(method, params):
                    nonlocal count
                    result = original(method, params)
                    if method == 'thread/read' and params['threadId'] == SOURCE:
                        count += 1
                        if count == revoke_on_read:
                            self.config.write_text(json.dumps({'codex':{'scope':'selected',
                                'session_ids':[SOURCE], 'data_root':self.tmp}}))
                    return result
                self.rpc.call = revoke_during_read
                if revoke_on_read == 1:
                    with self.assertRaisesRegex(ControlError, 'create_unavailable'):
                        self.provider.execute(self.command)
                    self.assertEqual(self.writes(), [])
                else:
                    self.assertEqual(self.provider.execute(self.command),
                                     {'state':'unknown', 'created_session_id':TARGET})
                    self.assertEqual([method for method, _ in self.writes()], ['thread/start'])

    def test_uncertain_create_and_first_turn_are_never_replayed(self):
        self.rpc.lose = 'thread/start'
        self.assertEqual(self.provider.execute(self.command), 'unknown')
        self.assertEqual(len(self.writes()), 1)
        self.rpc.lose = 'turn/start'
        self.assertEqual(self.provider.execute(self.command), {'state':'unknown','created_session_id':TARGET})
        self.assertEqual(len(self.writes()), 3)

    def test_source_id_never_used_as_created_target(self):
        self.rpc.wrong_target = True
        self.assertEqual(self.provider.execute(self.command), 'unknown')
        self.assertEqual(len(self.writes()),1)

    def test_source_archive_after_create_preserves_id_but_sends_no_message(self):
        def archive():
            self.db.execute('UPDATE threads SET archived=1')
            self.db.commit()
        self.rpc.on_start=archive
        self.assertEqual(self.provider.execute(self.command), {'state':'unknown','created_session_id':TARGET})
        self.assertEqual(len(self.writes()),1)

    def test_plain_callable_scope_has_no_creation_capability(self):
        provider = Codex('unused', 'unused', lambda: [SOURCE], rpc_factory=lambda *_: self.rpc)
        self.assertNotIn('create', provider.snapshot()[0])

    def test_native_bundle_requires_verified_build_and_signer(self):
        bundle = Path(self.tmp)/'Codex.app'
        (bundle/'Contents').mkdir(parents=True)
        plist = bundle/'Contents/Info.plist'
        with patch('aw.control.codex_create.subprocess.run') as verify:
            for build in ('24141', '24614'):
                plist.write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.openai.codex',
                                                 'CFBundleVersion':build}))
                self.assertTrue(_verified_desktop_bundle(bundle))
            args, kwargs = verify.call_args
            self.assertEqual(args[0][0:2], ['/usr/bin/codesign', '--verify'])
            self.assertIn('certificate leaf[subject.OU] = "2DC432GLL2"', args[0][2])
            self.assertEqual(args[0][-1], str(bundle))
            self.assertTrue(kwargs['check'])
            verify.side_effect = subprocess.CalledProcessError(1, 'codesign')
            self.assertFalse(_verified_desktop_bundle(bundle))
            verify.reset_mock(side_effect=True)
            for info in ({'CFBundleIdentifier':'different.app', 'CFBundleVersion':'24614'},
                         {'CFBundleIdentifier':'com.openai.codex', 'CFBundleVersion':'future'}, []):
                plist.write_bytes(plistlib.dumps(info))
                self.assertFalse(_verified_desktop_bundle(bundle))
            verify.assert_not_called()
            plist.write_text('invalid plist')
            self.assertFalse(_verified_desktop_bundle(bundle))

    def test_draft_is_bound_to_bundle_directory_and_literal_prompt(self):
        calls=[]
        bundle=Path(self.tmp)/'Desktop.app'
        draft=DesktopDraft(bundle, opener=lambda app,url: calls.append((app,url)))
        cap=capability(SOURCE,self.tmp,'draft')
        self.assertNotEqual(cap['revision'], self.command['action']['revision'])
        self.command['action']['revision']=cap['revision']
        with patch.object(draft, 'available', return_value=True):
            self.assertEqual(draft.execute(self.scope,self.command),'draft_opened')
        app,url=calls[0]
        self.assertEqual(app,bundle)
        parts=urlsplit(url)
        self.assertEqual((parts.scheme,parts.netloc,parts.path,parts.fragment),('codex','threads','/new',''))
        self.assertEqual(parse_qs(parts.query),{'path':[self.tmp],'prompt':[self.text]})
        self.db.execute('UPDATE threads SET archived=1')
        self.db.commit()
        with self.assertRaises(ControlError):draft.execute(self.scope,self.command)
        self.assertEqual(len(calls),1)

    def test_changed_installed_app_prevents_draft_dispatch(self):
        calls = []
        draft = DesktopDraft(Path(self.tmp)/'Codex.app', opener=lambda *_: calls.append(True))
        self.command['action']['revision'] = capability(SOURCE, self.tmp, 'draft')['revision']
        with patch.object(draft, 'available', return_value=False):
            with self.assertRaisesRegex(ControlError, 'create_unavailable'):
                draft.execute(self.scope, self.command)
        self.assertEqual(calls, [])

    def test_scope_revocation_during_signature_verification_prevents_draft(self):
        calls = []
        draft = DesktopDraft(Path(self.tmp)/'Codex.app', opener=lambda *_: calls.append(True))
        self.command['action']['revision'] = capability(SOURCE, self.tmp, 'draft')['revision']
        def verify_and_revoke(force=False):
            self.assertTrue(force)
            self.db.execute('UPDATE threads SET archived=1')
            self.db.commit()
            return True
        with patch.object(draft, 'available', side_effect=verify_and_revoke):
            with self.assertRaisesRegex(ControlError, 'create_unavailable'):
                draft.execute(self.scope, self.command)
        self.assertEqual(calls, [])

    def test_desktop_upgrade_revokes_capability_and_signature_cache(self):
        bundle = Path(self.tmp)/'Codex.app'
        files = ('Contents/Info.plist', 'Contents/MacOS/Codex',
                 'Contents/_CodeSignature/CodeResources', 'Contents/Resources/app.asar')
        for name in files:
            path = bundle/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic')
        info = {'CFBundleIdentifier':'com.openai.codex', 'CFBundleVersion':'24614',
                'CFBundleExecutable':'Codex'}
        plist = bundle/files[0]
        plist.write_bytes(plistlib.dumps(info))
        draft = DesktopDraft(bundle, opener=lambda *_: self.fail('Unverified app was opened'))
        provider = CodexDesktop('/unused', self.scope, background=False, draft=draft)
        def rows():
            return provider._with_creation([{'tool':'codex','session_id':SOURCE,'reply':True}])
        with patch('aw.control.codex_create.subprocess.run') as verify:
            self.assertIn('create', rows()[0])
            self.assertIn('create', rows()[0])
            self.assertEqual(verify.call_count, 1)
            plist.write_bytes(plistlib.dumps({**info, 'CFBundleVersion':'future'}))
            self.assertNotIn('create', rows()[0])
            self.command['action']['revision'] = capability(SOURCE, self.tmp, 'draft')['revision']
            with self.assertRaisesRegex(ControlError, 'create_unavailable'):
                draft.execute(self.scope, self.command)
            plist.write_bytes(plistlib.dumps(info))
            self.assertIn('create', rows()[0])
            for name in files[1:]:
                calls = verify.call_count
                (bundle/name).write_text('changed native file')
                self.assertIn('create', rows()[0])
                self.assertEqual(verify.call_count, calls+1)
                rows()
                self.assertEqual(verify.call_count, calls+1)
            (bundle/files[1]).write_text('invalid signature')
            verify.side_effect = subprocess.CalledProcessError(1, 'codesign')
            self.assertNotIn('create', rows()[0])
            calls = verify.call_count
            self.assertNotIn('create', rows()[0])
            self.assertEqual(verify.call_count, calls)

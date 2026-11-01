from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from aw.adapters import antigravity, codex
from aw.adapters.native_sidebar import (antigravity_pins, antigravity_storage_path,
                                        codex_sidebar, codex_pin, dsh_pins)
from aw.config import DEFAULTS
from aw.model import Task
from hub_fixture import isolated_hub


class NativeSidebarTests(unittest.TestCase):
    def setUp(self):
        self.tmp=self.enterContext(tempfile.TemporaryDirectory());self.root=Path(self.tmp)
        self.cfg=copy.deepcopy(DEFAULTS);self.cfg['adapters']['codex']['root']=str(self.root)
        self.db=self.root/'state_5.sqlite'
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE threads (id TEXT,name TEXT,title TEXT,thread_source TEXT,source TEXT,archived INTEGER,is_pinned INTEGER,updated_at INTEGER)')
            c.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',[
                ('main','Saved title','Question','user','vscode',0,0,100),
                ('old','Old task','Question','user','vscode',0,1,10),
                ('archived','Archived task','Question','user','vscode',1,1,9),
                ('guardian','Internal','Internal','guardian_review','vscode',0,1,99),
                ('placeholder','','','','cli',0,0,99)])

    def test_legacy_sidebar_is_authoritative_including_empty_list(self):
        p=self.root/'.codex-global-state.json';p.write_text(json.dumps({'pinned-thread-ids':['main','archived']}))
        meta,pins=codex_sidebar(self.root)
        self.assertTrue(codex_pin('main',meta,pins)['pinned'])
        self.assertFalse(codex_pin('old',meta,pins)['pinned'])
        self.assertFalse(codex_pin('archived',meta,pins)['pinned'])
        p.write_text(json.dumps({'pinned-thread-ids':[]}))
        meta,pins=codex_sidebar(self.root);self.assertFalse(codex_pin('main',meta,pins)['pinned'])

    def test_database_fallback_read_only_and_internal_rows_excluded(self):
        digest=hashlib.sha256(self.db.read_bytes()).digest()
        tasks=codex.scan(self.cfg);self.assertEqual({t.extra['thread_id'] for t in tasks},{'main','old'})
        self.assertTrue(next(t for t in tasks if t.extra['thread_id']=='old').extra['native_pin']['pinned'])
        self.assertEqual(digest,hashlib.sha256(self.db.read_bytes()).digest())
        self.assertTrue(all(t.extra['metadata_only'] for t in tasks))

    def test_unsupported_or_corrupt_pin_source_is_unknown(self):
        self.db.unlink();(self.root/'.codex-global-state.json').write_text('{')
        meta,pins=codex_sidebar(self.root);self.assertFalse(codex_pin('main',meta,pins)['known'])

    def test_legacy_desktop_history_without_sidebar_index_remains_visible(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',('legacy',None,'Original question',None,'vscode',0,0,1))
        tasks=codex.scan(self.cfg)
        old=next(t for t in tasks if t.extra['thread_id']=='legacy')
        self.assertEqual(old.title,'Original question');self.assertTrue(old.extra['metadata_only'])
        self.assertNotIn('placeholder',{t.extra['thread_id'] for t in tasks})

    def test_old_rollout_retained_and_completed_scan_cached(self):
        self.db.unlink();folder=self.root/'sessions';folder.mkdir();p=folder/'rollout-main.jsonl'
        rows=[{'type':'session_meta','payload':{'id':'main'}},
              {'type':'event_msg','timestamp':'2020-01-01T00:00:00Z','payload':{'type':'user_message','message':'Old question'}},
              {'type':'event_msg','timestamp':'2020-01-01T00:00:01Z','payload':{'type':'task_complete','last_agent_message':'Answer'}}]
        p.write_text('\n'.join(json.dumps(r) for r in rows)+'\n');os.utime(p,(100,100))
        self.assertEqual(len(codex.scan(self.cfg)),1)
        with patch.object(codex,'analyze',side_effect=AssertionError('reparsed unchanged completed log')):
            self.assertEqual(len(codex.scan(self.cfg)),1)
        limited=copy.deepcopy(self.cfg);limited['include_all_sessions']=False
        self.assertEqual(codex.scan(limited),[])

    def test_dsh_native_pin_order_and_unknown_versions(self):
        folder=self.root/'storages';folder.mkdir();p=folder/'workspace.json'
        p.write_text(json.dumps({'global':{'pinnedSessionIds':['second','first']}}))
        self.assertEqual(dsh_pins(self.root),{'second':0,'first':1})
        p.write_text(json.dumps({'global':{'pinnedSessionIds':[]}}));self.assertEqual(dsh_pins(self.root),{})
        p.write_text('{}');self.assertIsNone(dsh_pins(self.root))

    def test_antigravity_native_pin_refresh_and_read_only(self):
        storage=self.root/'app_storage.json'
        storage.write_text(json.dumps({'pinned_conversations_order':json.dumps(['second','first'])}))
        before=storage.read_bytes()
        self.assertEqual(antigravity_pins(storage),{'second':0,'first':1})
        cfg=copy.deepcopy(self.cfg)
        cfg['adapters']['antigravity'].update(root=str(self.root),sidebar_path=str(storage))
        log=self.root/'brain/first/.system_generated/logs/transcript.jsonl'
        log.parent.mkdir(parents=True)
        log.write_text(json.dumps({'type':'USER_INPUT','content':'Question','created_at':'2020-01-01T00:00:00Z'})+'\n')
        self.assertTrue(antigravity.scan(cfg)[0].extra['native_pin']['pinned'])
        self.assertEqual(storage.read_bytes(),before)
        storage.write_text(json.dumps({'pinned_conversations_order':'[]'}))
        pin=antigravity.scan(cfg)[0].extra['native_pin']
        self.assertTrue(pin['known']);self.assertFalse(pin['pinned'])
        storage.write_text('{}')
        self.assertFalse(antigravity.scan(cfg)[0].extra['native_pin']['known'])

    def test_antigravity_corrupt_or_missing_pin_data_is_unknown(self):
        storage=self.root/'app_storage.json'
        self.assertIsNone(antigravity_pins(storage))
        for value in ('{', '{}', '{"pinned_conversations_order":"bad"}',
                      '{"pinned_conversations_order":[1]}'):
            storage.write_text(value)
            self.assertIsNone(antigravity_pins(storage))

    def test_antigravity_platform_storage_locations(self):
        with patch('aw.adapters.native_sidebar.sys.platform','win32'), patch.dict(os.environ,{'APPDATA':str(self.root)}):
            self.assertEqual(antigravity_storage_path(),self.root/'Antigravity/app_storage.json')
        with patch('aw.adapters.native_sidebar.sys.platform','linux'), patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root)}):
            self.assertEqual(antigravity_storage_path(),self.root/'Antigravity/app_storage.json')
        with patch('aw.adapters.native_sidebar.sys.platform','darwin'), patch('pathlib.Path.home',return_value=self.root):
            self.assertEqual(antigravity_storage_path(),self.root/'Library/Application Support/Antigravity/app_storage.json')

    def test_hub_rejects_panel_only_pin_and_ignores_legacy_saved_pin(self):
        with isolated_hub() as hub:
            client=hub.app.test_client();auth=client.post('/api/auth/setup',json={'password':'test-password'}).get_json()
            task=Task(tool='codex',session_id='x',title='Task',state='done',state_since=time.time(),last_event_at=time.time(),extra={'native_pin':{'known':True,'pinned':False}})
            hub.STORE.set_local([task],{});key=hub.STORE.view()[0]['ui_key']
            hub.STORE.ui_state[key]={'pinned':True,'pinned_at':1}
            self.assertFalse(hub.STORE.view()[0]['ui']['pinned'])
            from urllib.parse import quote
            reply=client.patch('/api/workspace/tasks/'+quote(key,safe=''),json={'pinned':True},headers={'X-CSRF-Token':auth['csrf_token']})
            self.assertEqual(reply.status_code,409)
            self.assertEqual(reply.get_json()['error'],'native_pin_read_only')
            task.extra['native_pin']['pinned']=True
            self.assertTrue(hub.STORE.view()[0]['ui']['pinned'])

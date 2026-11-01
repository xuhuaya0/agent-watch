import json
import time
import unittest
from urllib.parse import quote

from aw.model import Task
from aw.store import Store
from aw.adapters import codex, antigravity, dsh
from aw.config import DEFAULTS
from hub_fixture import isolated_hub


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.hub=self.enterContext(isolated_hub())
        self.client=self.hub.app.test_client()
        result=self.client.post('/api/auth/setup',json={'password':'test-password'}).get_json()
        self.headers={'X-CSRF-Token':result['csrf_token']}
        self.now=time.time()
        self.task=Task(tool='codex',session_id='rollout-abc',title='A saved name',state='done',
                       state_since=self.now,last_event_at=self.now,machine='Test PC',
                       extra={'thread_id':'stable-thread','cwd':'/project',
                              'latest_turn':{'user':'Question','assistant':'Long result '*100,'truncated':False}})
        self.hub.STORE.set_local([self.task],{})
        self.key=self.hub.STORE.view()[0]['ui_key']
        self.url='/api/workspace/tasks/'+quote(self.key,safe='')

    def test_preferences_require_admin_and_csrf(self):
        other=self.hub.app.test_client()
        self.assertEqual(other.patch(self.url,json={'read_at':self.now}).status_code,401)
        self.assertEqual(other.patch(self.url,json={'read_at':self.now},headers={'Authorization':'Bearer test-reader'}).status_code,401)
        self.assertEqual(self.client.patch(self.url,json={'read_at':self.now}).status_code,403)
        self.assertEqual(self.client.patch(self.url,json={'read_at':self.now},headers=self.headers).status_code,200)

    def test_partial_changes_persist_and_preserve_other_fields(self):
        for patch in ({'read_at':self.now-1},{'read_at':self.now}):
            self.assertEqual(self.client.patch(self.url,json=patch,headers=self.headers).status_code,200)
        reloaded=Store(self.hub.CFG,self.hub.DATA_DIR)
        reloaded.set_local([self.task],{})
        ui=reloaded.view()[0]['ui']
        self.assertIsNone(ui['pinned']);self.assertEqual(ui['read_at'],self.now)
        self.assertFalse(reloaded.view()[0]['acked'])

    def test_rename_or_rollout_filename_change_keeps_preferences(self):
        self.client.patch(self.url,json={'read_at':self.now},headers=self.headers)
        self.task.session_id='rollout-replaced';self.task.title='Renamed';self.hub.STORE.set_local([self.task],{})
        view=self.hub.STORE.view()[0]
        self.assertEqual(view['ui_key'],self.key);self.assertEqual(view['ui']['read_at'],self.now)

    def test_preferences_do_not_mix_devices(self):
        self.client.patch(self.url,json={'read_at':self.now},headers=self.headers)
        self.hub.STORE.ingest('Other PC',[self.task.to_dict()],machine_id='another-device')
        rows=self.hub.STORE.view()
        self.assertEqual(sum(bool(t['ui'].get('read_at')) for t in rows),1)
        self.assertEqual(len({t['ui_key'] for t in rows}),2)

    def test_invalid_preferences_rejected_and_read_time_bounded(self):
        for data in ({'pinned':'true'},{'x':1},{'read_at':float('inf')},{'read_at':True},{'read_at':-1},[],{}):
            self.assertEqual(self.client.patch(self.url,json=data,headers=self.headers).status_code,400)
        result=self.client.patch(self.url,json={'read_at':self.now+10000},headers=self.headers).get_json()
        self.assertEqual(result['ui']['read_at'],self.now)
        self.assertEqual(self.client.patch('/api/workspace/tasks/missing',json={'read_at':self.now},headers=self.headers).status_code,404)

    def test_archive_is_rejected_and_legacy_flags_removed_on_reload(self):
        self.assertEqual(self.client.patch(self.url,json={'archived':True},headers=self.headers).status_code,400)
        with self.assertRaises(ValueError):
            self.hub.STORE.update_ui(self.key, {'archived':True})
        old={self.key:{'archived':True,'pinned':True,'pinned_at':1,'read_at':self.now},
             'old-session':{'archived':True}}
        Store._save_json(self.hub.STORE.ui_file,old)
        reloaded=Store(self.hub.CFG,self.hub.DATA_DIR)
        reloaded.set_local([self.task],{})
        with open(reloaded.ui_file,encoding='utf-8') as f:
            self.assertEqual(json.load(f),{self.key:{'read_at':self.now}})
        self.assertNotIn('archived',reloaded.view()[0]['ui'])
        self.assertEqual(len(reloaded.view()),1)

    def test_polling_omits_full_messages_and_detail_is_authenticated(self):
        brief=self.client.get('/api/tasks?all=1').get_json()['tasks'][0]
        self.assertNotIn('latest_turn',brief['extra'])
        path='/api/tasks/'+quote(brief['key'],safe='')
        self.assertEqual(self.hub.app.test_client().get(path).status_code,401)
        detail=self.client.get(path).get_json()['task']
        self.assertEqual(detail['extra']['latest_turn']['assistant'],'Long result '*100)
        self.assertEqual(self.client.get('/api/tasks/missing').status_code,404)

    def test_full_messages_survive_push_and_reload(self):
        payload=self.task.to_dict()
        self.hub.STORE.ingest('Remote',[payload],machine_id='remote')
        reloaded=Store(self.hub.CFG,self.hub.DATA_DIR)
        self.assertEqual(reloaded.view()[0]['extra']['latest_turn'],payload['extra']['latest_turn'])


class LatestTurnTests(unittest.TestCase):
    def test_codex_retains_full_reply_and_clears_on_new_turn(self):
        def event(kind,**data):return {'type':'event_msg','timestamp':'2026-10-28T12:00:00Z','payload':{'type':kind,**data}}
        long='A result\n'*100
        rows=[event('task_started'),event('user_message',message='Question'),event('task_complete',last_agent_message=long)]
        self.assertEqual(codex.analyze(rows,1793200000,DEFAULTS)['reply'],long)
        self.assertEqual(codex.analyze(rows+[event('task_started')],1793200000,DEFAULTS)['reply'],'')

    def test_dsh_does_not_pair_previous_reply_with_new_turn(self):
        st=dsh.new_state()
        dsh.reduce_event(st,{'type':'assistant/message','data':{'message':{'content':'Old answer'}}})
        dsh.reduce_event(st,{'type':'turn/start','data':{'turn':2}})
        self.assertEqual(st['last_assistant'],'')

    def test_antigravity_reply_is_limited_to_current_turn(self):
        steps=[{'type':'USER_INPUT','content':'Old question'}, {'type':'PLANNER_RESPONSE','content':'Old answer'},
               {'type':'USER_INPUT','content':'New question'}]
        self.assertEqual(antigravity.analyze(steps,time.time(),time.time(),DEFAULTS)['reply'],'')

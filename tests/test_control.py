import tempfile
import unittest
import uuid
from urllib.parse import quote
from aw.control.broker import Broker, ControlError
from aw.model import Task
from hub_fixture import isolated_hub


def cap(requests=None, reply=True, sid='s'):
    return {'tool':'codex','session_id':sid,'reply':reply,'requests':requests or []}


def approval(revision='v1'):
    return {'id':'a','revision':revision,'kind':'approval','prompt':'Run the displayed command once?', 'choices':['approve','reject']}


def action():
    return {'kind':'approval','request_id':'a','revision':'v1','decision':'approve'}


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.now=1000
        self.b=Broker(self.tmp,lambda:self.now)
        self.token=self.b.provision('pc')
        self.b.exchange('pc','worker',[cap([approval()])],[])

    def submit(self,a=None,id=None):
        return self.b.submit('pc','codex','s',id or str(uuid.uuid4()),a or action())

    def test_device_tokens_and_rotation(self):
        self.assertTrue(self.b.authenticate('pc',self.token))
        self.assertFalse(self.b.authenticate('other',self.token))
        self.assertFalse(self.b.authenticate('pc','test-receiver'))
        self.b.provision('pc')
        self.assertFalse(self.b.authenticate('pc',self.token))
        self.assertFalse(self.b.status('pc','codex','s')['connected'])

    def test_claim_once_receipt_isolated_and_payload_purged(self):
        c=self.submit()
        x=self.b.exchange('pc','worker',[cap([approval()])],[])
        self.assertEqual(x['command']['id'],c['id'])
        self.assertIsNone(self.b.exchange('pc','worker',[cap([approval()])],[])['command'])
        self.b.provision('other')
        self.b.exchange('other','w',[],[{'id':c['id'],'state':'delivered'}])
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'dispatched')
        self.b.exchange('pc','worker',[cap()],[{'id':c['id'],'state':'delivered','detail':'原应用已接收'}])
        with self.b.db() as db:
            self.assertIsNone(db.execute('SELECT action FROM commands WHERE id=?',(c['id'],)).fetchone()[0])
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'delivered')

    def test_same_id_is_idempotent_and_changed_payload_conflicts(self):
        c=self.submit();self.assertEqual(self.submit(id=c['id']),c)
        with self.assertRaisesRegex(ControlError,'command_id_conflict'):
            self.submit({'kind':'reply','text':'different'},c['id'])
        self.assertEqual(len(self.b.status('pc','codex','s')['commands']),1)

    def test_resolved_or_changed_approval_never_dispatched(self):
        self.submit()
        self.assertIsNone(self.b.exchange('pc','worker',[cap([approval('v2')])],[])['command'])
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'rejected')
        with self.assertRaisesRegex(ControlError,'request_expired'):self.submit()

    def test_no_auto_retry_after_uncertain_delivery_or_restart(self):
        self.submit();self.b.exchange('pc','worker',[cap([approval()])],[])
        restarted=Broker(self.tmp,lambda:self.now)
        self.assertIsNone(restarted.exchange('pc','worker',[cap([approval()])],[])['command'])
        self.assertEqual(restarted.status('pc','codex','s')['commands'][0]['state'],'unknown')

    def test_offline_expiration_worker_change_and_revoke(self):
        self.now+=16
        with self.assertRaisesRegex(ControlError,'control_offline'):self.submit()
        self.b.exchange('pc','worker',[cap([approval()])],[])
        self.submit();self.now+=61
        self.assertIsNone(self.b.exchange('pc','worker',[cap([approval()])],[])['command'])
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'expired')
        with self.assertRaisesRegex(ControlError,'device_already_connected'):
            self.b.exchange('pc','second',[cap()],[])
        self.now+=1;self.submit({'kind':'reply','text':'hello'})
        self.now+=16;self.assertIsNone(self.b.exchange('pc','second',[cap()],[])['command'])
        self.now+=1;self.submit({'kind':'reply','text':'new'})
        self.b.revoke('pc')
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'cancelled')

    def test_question_validation_rejects_invented_choices_and_multiple(self):
        req={'id':'q','revision':'v','kind':'question','prompt':'Choose','questions':[
            {'id':'one','question':'Which?','options':['A','B'],'custom':True}]}
        self.b.exchange('pc','worker',[cap([req])],[])
        a={'kind':'answer','request_id':'q','revision':'v','answers':{'one':{'selected':['A'],'custom':''}}}
        for selected,custom in [(['C'],''),(['A','B'],''),(['A'],'other'),([],'' )]:
            a['answers']['one']={'selected':selected,'custom':custom}
            with self.assertRaises(ControlError):self.submit(a)
        a['answers']['one']={'selected':[],'custom':'My answer'}
        self.assertEqual(self.submit(a)['state'],'queued')

    def test_independent_harness_workers_route_without_overwriting(self):
        ds={'tool':'dsh','session_id':'ds-session','reply':True,'requests':[]}
        self.b.exchange('pc','deepseek',[ds],[])
        first=self.submit()
        second=self.b.submit('pc','dsh','ds-session',str(uuid.uuid4()),{'kind':'reply','text':'Hi'})
        self.assertEqual(self.b.device_status('pc')['sessions'],2)
        self.assertEqual(self.b.exchange('pc','deepseek',[ds],[])['command']['id'],second['id'])
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'queued')
        self.assertEqual(self.b.exchange('pc','worker',[cap([approval()])],[])['command']['id'],first['id'])
        self.b.exchange('pc','deepseek',[ds],[{'id':first['id'],'state':'delivered'}])
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'dispatched')

    def test_one_harness_expiring_does_not_hide_the_other(self):
        ds={'tool':'dsh','session_id':'ds-session','reply':True,'requests':[]}
        self.now+=10;self.b.exchange('pc','deepseek',[ds],[])
        self.now+=6
        self.assertFalse(self.b.status('pc','codex','s')['connected'])
        self.assertTrue(self.b.status('pc','dsh','ds-session')['connected'])
        self.assertEqual(self.b.device_status('pc')['sessions'],1)

    def test_invalid_payloads_and_session_identity(self):
        for a in [{'kind':'shell','command':'echo x'},{**action(),'decision':'always'}, {'kind':'reply','text':'  '}]:
            with self.assertRaises(ControlError):self.submit(a)
        with self.assertRaisesRegex(ControlError,'control_offline'):
            self.b.submit('pc','codex','another',str(uuid.uuid4()),action())
        with self.assertRaises(ControlError):self.b.exchange('pc','worker',[cap([{'id':'x'}])],[])

    def test_pause_cancels_queued_but_does_not_claim_to_retract_dispatched(self):
        c=self.submit()
        self.b.set_enabled('pc',False)
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'cancelled')
        self.assertFalse(self.b.status('pc','codex','s')['connected'])
        with self.assertRaisesRegex(ControlError,'control_paused'):self.submit()
        self.assertIsNone(self.b.exchange('pc','worker',[cap([approval()])],[])['command'])
        self.assertFalse(Broker(self.tmp,lambda:self.now).device_status('pc')['enabled'])
        self.b.set_enabled('pc',True)
        self.now+=1;c=self.submit()
        self.b.exchange('pc','worker',[cap([approval()])],[])
        self.b.set_enabled('pc',False)
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'dispatched')
        self.b.exchange('pc','worker',[cap()],[{'id':c['id'],'state':'delivered'}])
        self.assertEqual(self.b.status('pc','codex','s')['commands'][0]['state'],'delivered')


class ControlApiTests(unittest.TestCase):
    def setUp(self):
        self.h=self.enterContext(isolated_hub());self.c=self.h.app.test_client()
        d=self.c.post('/api/auth/setup',json={'password':'test-password'}).get_json()
        self.headers={'X-CSRF-Token':d['csrf_token']}
        self.h.STORE.set_local([Task(tool='codex',session_id='rollout',title='T',state='done',state_since=1,last_event_at=1,extra={'thread_id':'s'})],{})
        t=self.h.STORE.view()[0];self.device=t['machine_id'];self.url='/api/control/tasks/'+quote(t['ui_key'],safe='')

    def test_admin_csrf_and_independent_control_auth(self):
        other=self.h.app.test_client()
        self.assertEqual(other.get(self.url).status_code,401)
        self.assertEqual(self.c.post(self.url,json={}).status_code,403)
        self.assertEqual(other.post('/api/control/devices/'+self.device).status_code,401)
        result=self.c.post('/api/control/devices/'+self.device,headers=self.headers).get_json()
        payload={'device_id':self.device,'worker_id':'w','sessions':[cap()],'receipts':[]}
        for token in ['test-receiver','test-reader','']:
            self.assertEqual(other.post('/api/control/exchange',json=payload,headers={'Authorization':'Bearer '+token}).status_code,401)
        self.assertEqual(other.post('/api/control/exchange',json=payload,headers={'Authorization':'Bearer '+result['token']}).status_code,200)
        self.assertTrue(self.c.get(self.url).get_json()['connected'])
        body={'id':str(uuid.uuid4()),'action':{'kind':'reply','text':'hello'}}
        self.assertEqual(self.c.post(self.url,json=body,headers=self.headers).status_code,200)
        self.assertEqual(self.c.delete('/api/control/devices/'+self.device,headers=self.headers).status_code,200)
        self.assertEqual(other.post('/api/control/exchange',json=payload,headers={'Authorization':'Bearer '+result['token']}).status_code,401)

    def test_connection_settings_require_admin_csrf_and_never_expose_credentials(self):
        self.assertEqual(self.h.app.test_client().get('/api/control/devices').status_code,401)
        token=self.c.post('/api/control/devices/'+self.device,headers=self.headers).get_json()['token']
        url='/api/control/devices/'+self.device
        self.assertEqual(self.c.patch(url,json={'enabled':False}).status_code,403)
        self.assertEqual(self.c.patch(url,json={'enabled':'false'},headers=self.headers).status_code,400)
        self.assertEqual(self.c.patch(url,json={'enabled':False},headers=self.headers).status_code,200)
        response=self.c.get('/api/control/devices')
        self.assertNotIn(token,response.get_data(as_text=True))
        self.assertFalse(response.get_json()['devices'][0]['enabled'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_native_wait_overrides_lists_details_and_get_without_changing_history(self):
        self.h.CONTROL.provision(self.device)
        now=[1000]
        self.h.CONTROL.clock=lambda:now[0]
        self.h.CONTROL.exchange(self.device,'w',[cap([approval()])],[])
        listed=self.c.get('/api/tasks?all=1').get_json()
        task=listed['tasks'][0]
        self.assertEqual((task['state'],task['state_label'],task['group']),('waiting_approval','等待批准','attention'))
        self.assertEqual(listed['counts']['attention'],1)
        self.assertEqual(task['age'],'-')
        self.assertEqual(self.c.get('/api/tasks/'+quote(task['key'],safe='')).get_json()['task']['state'],'waiting_approval')
        self.assertEqual(self.c.get('/api/get').get_json()['items'][0]['state'],'waiting_approval')
        self.assertEqual(self.h.STORE.view()[0]['state'],'done')
        # Pausing writes does not hide an observed native wait.
        self.h.CONTROL.set_enabled(self.device,False)
        self.assertEqual(self.h.CONTROL.pending_states()[(self.device,'codex','s')],'waiting_approval')
        self.assertFalse(self.h.CONTROL.status(self.device,'codex','s')['connected'])
        now[0]+=16
        self.assertEqual(self.h.CONTROL.pending_states(),{})
        self.assertEqual(self.c.get('/api/tasks?all=1').get_json()['tasks'][0]['state'],'done')
        # Same session ID on another device must not affect this task.
        self.h.CONTROL.provision('other')
        self.h.CONTROL.exchange('other','w',[cap([approval()])],[])
        self.assertEqual(self.c.get('/api/tasks?all=1').get_json()['tasks'][0]['state'],'done')
        self.h.CONTROL.exchange(self.device,'w',[cap([{'id':'q','revision':'v1','kind':'question',
            'prompt':'Question','questions':[{'id':'1','question':'Q','options':['A']}]}])],[])
        self.assertEqual(self.c.get('/api/tasks?all=1').get_json()['tasks'][0]['state'],'waiting_input')
        self.h.CONTROL.exchange(self.device,'w',[cap()],[])
        self.assertEqual(self.c.get('/api/tasks?all=1').get_json()['tasks'][0]['state'],'done')


class NativeSettingsContractTests(unittest.TestCase):
    def test_native_metadata_is_bounded_and_has_no_arbitrary_state(self):
        from aw.control.broker import normalize_sessions
        settings = {'model': 'Example Model', 'permission': 'Workspace', 'scope': 'session'}
        row = {**cap(), 'settings': settings}
        self.assertEqual(normalize_sessions([row])[('codex', 's')]['settings'], settings)
        unknown = {'model': 'Example Model', 'note': 'Scope unavailable'}
        self.assertEqual(normalize_sessions([{**row, 'settings': unknown}])[('codex', 's')]['settings'], unknown)
        for bad in ({**settings, 'raw_config': 'secret'}, {**settings, 'model': ['bad']},
                    {**settings, 'scope': 'global'}, {**settings, 'note': 'x' * 501}):
            with self.subTest(bad=list(bad)):
                with self.assertRaisesRegex(ControlError, 'invalid_settings_capability'):
                    normalize_sessions([{**row, 'settings': bad}])

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from aw.control.antigravity import Antigravity
from aw.control.codex import Codex
from aw.control.worker import Worker
from aw.control.broker import ControlError

SID='c0c039d9-2cf2-4c38-ad8a-ffb06685aaab'


class AntigravityTests(unittest.TestCase):
    def test_native_executable_receives_literal_message_without_shell(self):
        calls=[]
        def run(argv,**kwargs):
            calls.append((argv,kwargs));return SimpleNamespace(returncode=0,stdout='{"ok":true}')
        p=Antigravity('/tmp/language_server.exe',lambda:[SID],run=run,environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:123'})
        self.assertTrue(p.snapshot()[0]['reply']);self.assertEqual(p.snapshot()[0]['requests'],[])
        message='中文\n" & echo OWNED | $(touch test) %PATH%'
        self.assertEqual(p.execute({'session_id':SID,'action':{'kind':'reply','text':message}}),'delivered')
        argv,kwargs=calls[-1]
        self.assertEqual(argv[-2:],[SID,message]);self.assertFalse(kwargs['shell'])
        self.assertEqual(argv[1:3],['agentapi','send-message'])
        self.assertEqual(argv[3],'--title=Agent Watch')

    def test_missing_context_shell_wrappers_and_unknown_outcome_fail_closed(self):
        with self.assertRaises(ValueError):Antigravity('agentapi.bat',lambda:[])
        p=Antigravity('/tmp/native',lambda:[SID],environ={})
        self.assertEqual(p.snapshot(),[])
        p=Antigravity('/tmp/native',lambda:[SID],environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:1'},
                      run=lambda *a,**kw:SimpleNamespace(returncode=1,stdout='{"error":"failure"}'))
        self.assertEqual(p.snapshot(),[])
        self.assertEqual(p.execute({'session_id':SID,'action':{'kind':'reply','text':'hi'}}),'unknown')
        with self.assertRaises(ControlError):p.execute({'session_id':SID,'action':{'kind':'approval'}})
        with self.assertRaisesRegex(ControlError,'starts_with_option'):
            p.execute({'session_id':SID,'action':{'kind':'reply','text':'--title=unexpected'}})
        with self.assertRaisesRegex(ControlError,'native_session_unavailable'):
            p.execute({'session_id':str(uuid.uuid4()),'action':{'kind':'reply','text':'hi'}})


class FakeProvider:
    def __init__(self):self.calls=[];self.online=True
    def snapshot(self):return [{'tool':'codex','session_id':'s','reply':True,'requests':[]}] if self.online else []
    def execute(self,c):self.calls.append(c);return 'delivered'


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=self.enterContext(tempfile.TemporaryDirectory());self.path=Path(self.temp)/'state.json'
        self.p=FakeProvider();self.w=Worker({'device_id':'pc','hub_url':'http://127.0.0.1:1','token':'fake'}, {'codex':self.p}, self.path)
        self.c={'id':str(uuid.uuid4()),'tool':'codex','session_id':'s','action':{'kind':'reply','text':'hello'}}

    def test_execute_once_and_only_report_on_next_exchange(self):
        self.w.exchange=lambda b:{'ok':True,'receipts':[],'command':self.c}
        self.assertTrue(self.w.step())  # caller immediately uploads the receipt
        self.assertIsNone(self.w.step());self.assertEqual(len(self.p.calls),1)
        self.assertEqual(self.w.state['receipts'][0]['state'],'delivered')

    def test_live_native_state_rechecked_after_server_dispatch(self):
        def exchange(body):
            self.p.online=False
            return {'ok':True,'receipts':[],'command':self.c}
        self.w.exchange=exchange;self.w.step()
        self.assertEqual(self.p.calls,[]);self.assertEqual(self.w.state['receipts'][0]['state'],'rejected')

    def test_native_queue_receipts_are_durable_and_never_requeued(self):
        for outcome in ('queued_native','queued_unwoken'):
            with self.subTest(outcome=outcome):
                self.c['id']=str(uuid.uuid4())
                calls=[]
                def execute(command):calls.append(command['id']);return outcome
                self.p.execute=execute
                self.w.exchange=lambda b:{'ok':True,'receipts':[],'command':self.c}
                self.assertTrue(self.w.step());self.w.step()
                receipt=self.w.state['receipts'][-1]
                self.assertEqual(calls,[self.c['id']]);self.assertEqual(receipt['state'],'delivered')
                self.assertIn('原生队列',receipt['detail'])
                if outcome=='queued_unwoken':self.assertIn('勿重复发送',receipt['detail'])
                self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['receipts'][-1],receipt)

    def test_crash_marker_is_not_replayed_after_restart(self):
        self.w.state['receipts']=[{'id':self.c['id'],'state':'executing','detail':''}];self.w.save()
        recovered=Worker(self.w.config,{'codex':self.p},self.path)
        recovered.exchange=lambda b:{'ok':True,'receipts':[],'command':self.c}
        recovered.step();self.assertEqual(self.p.calls,[])
        self.assertEqual(recovered.state['receipts'][0]['state'],'unknown')

    def test_uncertain_native_failure_not_reported_as_success(self):
        def fail(c):raise TimeoutError('secret prompt must not be returned')
        self.p.execute=fail;self.w.exchange=lambda b:{'ok':True,'receipts':[],'command':self.c};self.w.step()
        result=self.w.state['receipts'][0]
        self.assertEqual(result['state'],'unknown');self.assertNotIn('secret',json.dumps(result))

    def test_state_cannot_move_to_another_hub_device_or_token(self):
        for field,value in [('hub_url','http://other'),('device_id','other'),('token','rotated')]:
            with self.assertRaisesRegex(ValueError,'configuration changed'):
                Worker({**self.w.config,field:value},{'codex':self.p},self.path)


class FakeRpc:
    def __init__(self,exe,sock,event):self.event=event;self.calls=[];self.writes=[]
    def call(self,method,params):
        self.calls.append((method,params))
        if method=='thread/loaded/list':return {'data':[SID],'nextCursor':None}
        return {'thread':{'id':SID},'turn':{'id':'turn-1'}}
    def send(self,value):
        self.writes.append(value)
        self.event({'method':'serverRequest/resolved','params':{'threadId':SID,'requestId':value['id']}})


class CodexTests(unittest.TestCase):
    def setUp(self):self.p=Codex('/tmp/codex','/tmp/existing-socket',[SID],rpc_factory=FakeRpc)
    def approval(self):
        self.p.event({'id':42,'method':'item/commandExecution/requestApproval','params':{
            'threadId':SID,'turnId':'t','itemId':'i','command':'git status','cwd':'/project','reason':'Inspect changes'}})

    def test_reply_uses_existing_thread_without_policy_override(self):
        self.p.snapshot()
        c={'id':'client-message','session_id':SID,'action':{'kind':'reply','text':'Continue'}}
        self.assertEqual(self.p.execute(c),'delivered')
        method,params=self.p.rpc.calls[-1]
        self.assertEqual(method,'turn/start');self.assertEqual(params['threadId'],SID)
        self.assertEqual(set(params),{'threadId','input','clientUserMessageId'})
        self.assertFalse(any(m=='thread/start' for m,p in self.p.rpc.calls))

    def test_approval_round_trip_ids_once_only_and_resolved_event(self):
        self.approval();req=self.p.snapshot()[0]['requests'][0]
        c={'session_id':SID,'action':{'kind':'approval','request_id':req['id'],'revision':req['revision'],'decision':'approve'}}
        self.assertEqual(self.p.execute(c),'resolved')
        self.assertEqual(self.p.rpc.writes,[{'id':42,'result':{'decision':'accept'}}])
        self.assertEqual(self.p.snapshot()[0]['requests'],[])
        with self.assertRaisesRegex(ControlError,'request_expired'):self.p.execute(c)

    def test_question_ids_and_custom_answers_preserved(self):
        self.p.event({'id':'request-x','method':'item/tool/requestUserInput','params':{
            'threadId':SID,'turnId':'t','questions':[{'id':'q','question':'Choose','options':[{'label':'A'}],'isOther':True}]}})
        req=self.p.snapshot()[0]['requests'][0]
        c={'session_id':SID,'action':{'kind':'answer','request_id':req['id'],'revision':req['revision'],
             'answers':{'q':{'selected':[],'custom':'Different'}}}}
        self.assertEqual(self.p.execute(c),'resolved')
        self.assertEqual(self.p.rpc.writes[-1],{'id':'request-x','result':{'answers':{'q':{'answers':['Different']}}}})

    def test_secrets_and_blind_file_approvals_never_exposed(self):
        for method,extra in [('item/tool/requestUserInput',{'questions':[{'id':'secret','question':'Password','isSecret':True}]}),('item/fileChange/requestApproval',{'reason':'write files'}),('item/commandExecution/requestApproval',{'command':'curl example','networkApprovalContext':{'host':'example'}})]:
            self.p.event({'id':'x','method':method,'params':{'threadId':SID,**extra}})
        self.assertEqual(self.p.snapshot()[0]['requests'],[])
        self.approval();self.p.event({'method':'agent-watch/disconnected'})
        self.assertEqual(self.p.snapshot(),[])

    def test_old_turn_completion_does_not_clear_new_turn_request(self):
        self.approval()
        self.p.event({'method':'turn/completed','params':{'threadId':SID,'turn':{'id':'older'}}})
        self.assertEqual(len(self.p.snapshot()[0]['requests']),1)
        self.p.event({'method':'turn/completed','params':{'threadId':SID,'turn':{'id':'t'}}})
        self.assertEqual(self.p.snapshot()[0]['requests'],[])

    def test_cold_history_is_not_resumed_in_another_server(self):
        self.p.rpc.call=lambda method,params:{'data':[],'nextCursor':None}
        self.assertEqual(self.p.snapshot(),[])

class WorkerLockTests(unittest.TestCase):
    def test_other_process_cannot_own_state_until_owner_releases(self):
        import tempfile, subprocess, sys
        from pathlib import Path
        from aw.control.lock import WorkerLock
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'worker.lock';owner=WorkerLock(path)
            try:
                self.assertTrue(owner.acquire())
                script='from aw.control.lock import WorkerLock; import sys; lock=WorkerLock(sys.argv[1]); print(lock.acquire()); lock.close()'
                def competing():return subprocess.check_output([sys.executable,'-c',script,str(path)],text=True).strip()
                self.assertEqual(competing(),'False')
                owner.release()
                self.assertEqual(competing(),'True')
            finally:owner.close()

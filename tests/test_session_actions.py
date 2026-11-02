import copy
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
from aw.control.broker import Broker, ControlError, validate_action
from aw.control.worker import Worker
from aw.control.dsh import DeepSeek
from aw.control.codex_ipc import CodexDesktop
from test_dsh_control import FakeGateway, SID as DSH_SID
from test_codex_ipc import FakeDesktop, SID


class CreationGateway(FakeGateway):
    def __init__(self, path, event):
        super().__init__(path, event)
        self.catalog[0]['cwd'] = '/workspace/example'
        self.created = []
        self.fail_prompt = False

    def call(self, endpoint, args):
        if endpoint == 'session/create':
            self.calls.append((endpoint,copy.deepcopy(args)))
            self.created.append(args['request']['sessionId'])
            return {'sessionId':self.created[-1]}
        if endpoint == 'session/prompt' and self.fail_prompt:
            self.calls.append((endpoint,copy.deepcopy(args)))
            raise TimeoutError()
        return super().call(endpoint,args)


class CreateTests(unittest.TestCase):
    def setUp(self):
        self.p = DeepSeek({'scope':'all','connection_file':'unused'},CreationGateway,background=False)
        self.p.refresh();self.addCleanup(self.p.close)

    def action(self):
        return {'kind':'create','text':'--literal 中文 & $(no shell)',
                'revision':self.p.snapshot()[0]['create']['revision']}

    def test_creation_uses_new_identity_and_preserves_directory_and_text(self):
        action = self.action()
        result = self.p.execute({'id':str(uuid.uuid4()),'session_id':DSH_SID,'action':action})
        self.assertEqual(result['state'],'delivered')
        sid = result['created_session_id'];self.assertNotEqual(sid,DSH_SID)
        calls = [c for c in self.p.gateway.calls if c[0] in ('session/create','session/prompt')]
        self.assertEqual(calls[0][1],{'request':{'sessionId':sid,'cwd':'/workspace/example'}})
        self.assertEqual(calls[1][1]['request']['sessionId'],sid)
        self.assertEqual(calls[1][1]['request']['content'],[{'type':'text','text':action['text']}])

    def test_selected_scope_or_changed_directory_never_creates(self):
        action = self.action()
        self.p.gateway.catalog[0]['cwd']='/workspace/other'
        with self.assertRaisesRegex(ControlError,'create_unavailable'):
            self.p.execute({'id':'c','session_id':DSH_SID,'action':action})
        self.p.config.update(scope='selected',session_ids=[DSH_SID]);self.p.refresh()
        self.assertNotIn('create',self.p.snapshot()[0]);self.assertFalse(self.p.gateway.created)

    def test_partial_creation_reports_unknown_and_does_not_fallback(self):
        self.p.gateway.fail_prompt=True
        result=self.p.execute({'id':'c','session_id':DSH_SID,'action':self.action()})
        self.assertEqual(result['state'],'unknown')
        prompts=[args['request'] for method,args in self.p.gateway.calls if method=='session/prompt']
        self.assertEqual(len(prompts),1);self.assertEqual(prompts[0]['sessionId'],result['created_session_id'])

    def test_lost_create_response_keeps_destination_without_sending_or_retrying(self):
        call=self.p.gateway.call
        def lost(endpoint,args):
            result=call(endpoint,args)
            if endpoint=='session/create':raise TimeoutError()
            return result
        self.p.gateway.call=lost
        result=self.p.execute({'id':'c','session_id':DSH_SID,'action':self.action()})
        self.assertEqual(result,{'state':'unknown','created_session_id':self.p.gateway.created[0]})
        self.assertEqual(len(self.p.gateway.created),1)
        self.assertFalse(any(method=='session/prompt' for method,_ in self.p.gateway.calls))

    def test_broker_worker_receipt_deduplicates_and_keeps_new_destination(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        broker=Broker(root/'hub')
        worker=Worker({'hub_url':'http://example.invalid','device_id':'d','token':'fixture-key'},
                      {'dsh':self.p},root/'worker.json')
        worker.exchange=lambda body:{'ok':True,**broker.exchange('d',body['worker_id'],body['sessions'],body['receipts'],body['read_results'])}
        worker.step();action=self.action();cid=str(uuid.uuid4())
        broker.submit('d','dsh',DSH_SID,cid,action)
        worker.step()
        # A center restart clears the message body but must not lose the
        # command type needed to accept the destination receipt afterward.
        broker=Broker(root/'hub')
        worker.step()
        receipt=broker.status('d','dsh',DSH_SID)['commands'][0]
        self.assertEqual(receipt['state'],'delivered')
        self.assertEqual(receipt['result'],{'tool':'dsh','session_id':self.p.gateway.created[0]})
        broker.submit('d','dsh',DSH_SID,cid,action);worker.step()
        self.assertEqual(len(self.p.gateway.created),1)
        self.assertNotIn(action['text'],(root/'worker.json').read_text())


class PolicyDesktop(FakeDesktop):
    def __init__(self,path,event):
        super().__init__(path,event)
        self.history={'threadRuntimeStatus':{'type':'idle'},'latestThreadSettings':{
            'approvalPolicy':'on-request','approvalsReviewer':'user',
            'sandboxPolicy':{'type':'workspaceWrite','writableRoots':['/workspace/example']},
            'activePermissionProfile':None,'permissions':None}}

    def call(self,method,params,owner=None,timeout=8):
        if method=='thread-follower-update-thread-settings':
            self.calls.append((method,copy.deepcopy(params),owner))
            self.history['latestThreadSettings'].update(params['threadSettings'])
            self.follow(SID,self.owner)
            return {'handledByClientId':self.owner,'result':{'method':method,'result':{'applied':True}}}
        return super().call(method,params,owner,timeout)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.p=CodexDesktop('/unused',lambda:[SID],PolicyDesktop,background=False)
        self.p.refresh();self.addCleanup(self.p.close)

    def action(self):
        return {'kind':'policy','revision':self.p.snapshot()[0]['policy']['revision'],'value':'never'}

    def test_only_session_policy_written_and_native_readback_required(self):
        before=copy.deepcopy(self.p.rpc.history['latestThreadSettings'])
        result=self.p.execute({'id':'c','session_id':SID,'action':self.action()})
        self.assertEqual(result,'policy_updated')
        method,params,owner=self.p.rpc.calls[-1]
        self.assertEqual(method,'thread-follower-update-thread-settings')
        self.assertEqual(params,{'conversationId':SID,'threadSettings':{'approvalPolicy':'never'},'activeTurnId':None})
        self.assertEqual(owner,'desktop-owner')
        self.assertEqual(self.p.rpc.history['latestThreadSettings'],{**before,'approvalPolicy':'never'})

    def test_running_or_changed_native_permissions_withdraw_stale_option(self):
        action=self.action()
        self.p.rpc.history['latestThreadSettings']['approvalsReviewer']='auto_review'
        self.p.rpc.follow(SID,self.p.rpc.owner)
        with self.assertRaisesRegex(ControlError,'policy_changed'):
            self.p.execute({'id':'c','session_id':SID,'action':action})
        self.p.rpc.history['threadRuntimeStatus']['type']='active'
        self.p.rpc.follow(SID,self.p.rpc.owner)
        self.assertNotIn('policy',self.p.snapshot()[0])
        self.assertFalse(any(m=='thread-follower-update-thread-settings' for m,_,_ in self.p.rpc.calls))

    def test_arbitrary_policy_and_extra_sandbox_fields_are_rejected(self):
        row=self.p.snapshot()[0]
        for action in ({**self.action(),'value':'auto-approve'}, {**self.action(),'sandboxPolicy':'danger-full-access'}):
            with self.assertRaises(ControlError):validate_action(row,action)

    def test_rpc_acceptance_without_native_readback_stays_unknown(self):
        original=self.p.rpc.call
        def call(method,*args,**kwargs):
            if method=='thread-follower-update-thread-settings':return {'result':{'result':{'applied':True}}}
            return original(method,*args,**kwargs)
        self.p.rpc.call=call
        with patch.object(self.p.condition,'wait_for',return_value=False):
            self.assertEqual(self.p.execute({'id':'c','session_id':SID,'action':self.action()}),'unknown')

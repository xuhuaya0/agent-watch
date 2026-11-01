import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from aw.control.dsh import DeepSeek, connection_record
from aw.control.broker import ControlError

SID='session-c0c039d9-2cf2-4c38-ad8a-ffb06685aaab'
class FakeGateway:
    def __init__(self,path,event):
        self.event=event;self.connected=True;self.calls=[]
        self.catalog=[{'sessionId':SID,'blank':False,'agentAvailable':False},
            {'sessionId':'child','origin':'subagent'}, {'sessionId':'session-empty','blank':True}]
        self.tool={'callId':'call-1','name':'shell','arguments':{'command':'printf test'}}
    def live(self):return self.connected
    def close(self):self.connected=False;self.event({'type':'disconnected'})
    def call(self,endpoint,args):
        self.calls.append((endpoint,copy.deepcopy(args)))
        if endpoint=='session/list':return {'items':self.catalog}
        if endpoint=='session/projections':return {'asOfSeq':50}
        if endpoint=='session/page':return {'records':[{'type':'event','event':{'type':'tool/call','data':self.tool}}]}
        if endpoint=='session/prompt':return {'accepted':True}
        if endpoint=='userQuestions/answer':return True
        raise AssertionError(endpoint)
    def answer(self,event_id,outcome):
        self.calls.append(('$events/result',copy.deepcopy(outcome)))
        # The native Gateway does not echo cancel to the answering client.
        return None

class DshControlTests(unittest.TestCase):
    def provider(self):
        p=DeepSeek({'connection_file':'unused','scope':'all'},FakeGateway,background=False);p.refresh();return p
    def request(self,p,kind='question'):
        frame={'type':'waterfall','eventId':'e-1','agentId':SID,
            'event':'user-questions/request','request':{'questions':[{'id':'q1','question':'Color?','options':[{'label':'Blue','description':'A blue option'}]}]}}
        if kind=='approval':frame.update(event='approval/request',request={'callId':'call-1','toolName':'shell','reason':'test'})
        p.event(frame);p.refresh();return p.snapshot()[0]['requests'][0]
    def test_all_includes_cold_root_and_excludes_subagent_and_blank(self):
        self.assertEqual([r['session_id'] for r in self.provider().snapshot()],[SID])
    def test_selected_scope_not_silently_expanded(self):
        p=DeepSeek({'connection_file':'unused'},FakeGateway,background=False);p.refresh();self.assertEqual(p.snapshot(),[])
    def test_reply_queues_original_session_and_preserves_request_id(self):
        p=self.provider();self.assertEqual(p.execute({'id':'cmd-1','session_id':SID,'action':{'kind':'reply','text':'Hello'}}),'delivered')
        self.assertEqual(p.gateway.calls[-1],('session/prompt',{'request':{'sessionId':SID,'requestId':'cmd-1','mode':'queue','content':[{'type':'text','text':'Hello'}]}}))
    def test_question_reply_preserves_native_shape(self):
        p=self.provider();r=self.request(p)
        a={'kind':'answer','request_id':r['id'],'revision':r['revision'],'answers':{'q1':{'selected':['Blue'],'custom':''}}}
        self.assertEqual(p.execute({'id':'cmd-2','session_id':SID,'action':a}),'delivered')
        self.assertEqual(p.snapshot()[0]['requests'], [])
        with self.assertRaises(ControlError):
            p.execute({'id':'duplicate','session_id':SID,'action':a})
        self.assertIn(('$events/result',{'kind':'result','value':{'answers':[{'id':'q1','selected':['Blue']}]}}),p.gateway.calls)
    def test_approval_has_exact_tool_arguments_and_only_one_shot(self):
        for decision,value in [('approve','allowed-once'),('reject','rejected')]:
            p=self.provider();r=self.request(p,'approval');self.assertIn('printf test',r['prompt'])
            a={'kind':'approval','request_id':r['id'],'revision':r['revision'],'decision':decision}
            self.assertEqual(p.execute({'id':'cmd-3','session_id':SID,'action':a}),'delivered')
            self.assertEqual(p.snapshot()[0]['requests'], [])
            self.assertEqual(p.gateway.calls[-1],('$events/result',{'kind':'result','value':value}))
    def test_serialized_tool_arguments_are_readable_without_changing_revision_source(self):
        p=self.provider();p.gateway.tool['arguments']=json.dumps({'command': 'printf test'})
        r=self.request(p,'approval')
        self.assertIn('\"command\": \"printf test\"',r['prompt'])
        self.assertNotIn('\\\"command',r['prompt'])
    def test_missing_or_wrong_call_cannot_offer_blind_approval(self):
        p=self.provider();p.gateway.tool['callId']='different'
        p.event({'type':'waterfall','eventId':'e-1','agentId':SID,'event':'approval/request','request':{'callId':'call-1','toolName':'shell'}})
        p.refresh();self.assertEqual(p.snapshot()[0]['requests'],[])
        self.assertIn(('$events/result',{'kind':'next'}),p.gateway.calls)
    def test_native_cancel_immediately_withdraws_action(self):
        p=self.provider();r=self.request(p);p.event({'type':'cancel','eventId':'e-1'})
        self.assertEqual(p.snapshot()[0]['requests'],[])
        with self.assertRaises(ControlError):p.execute({'id':'c','session_id':SID,'action':{'kind':'answer','request_id':r['id'],'revision':r['revision'],'answers':{'q1':{'selected':['Blue'],'custom':''}}}})
    def test_disconnect_withdraws_sessions(self):
        p=self.provider();p.gateway.close();self.assertEqual(p.snapshot(),[])
    def test_continued_question_uses_native_answer_method_not_event_replay(self):
        p=self.provider()
        p.gateway.catalog[0]['projections']={'values':{'userQuestions':{'questions':{'active':[
            {'callId':'timed-1','state':'continued','questions':[{'id':'q1','question':'Continue?','options':[{'label':'Yes'}]}]}]}}}}
        p.refresh();r=p.snapshot()[0]['requests'][0]
        a={'kind':'answer','request_id':r['id'],'revision':r['revision'],'answers':{'q1':{'selected':['Yes'],'custom':''}}}
        self.assertEqual(p.execute({'id':'cmd','session_id':SID,'action':a}),'delivered')
        self.assertEqual(p.gateway.calls[-1],('userQuestions/answer',{'agent':SID,'callId':'timed-1','answer':{'answers':[{'id':'q1','selected':['Yes']}]}}))
        p.gateway.catalog[0]['projections']={}
        with self.assertRaises(ControlError):p.execute({'id':'other','session_id':SID,'action':a})
    def test_unknown_scope_fails_closed(self):
        p=DeepSeek({'connection_file':'unused','scope':'typo','session_ids':[SID]},FakeGateway,background=False)
        p.refresh();self.assertEqual(p.snapshot(),[])
    def test_connection_only_fresh_loopback_normal_token_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'connection.json'
            for url,age,valid in [('http://127.0.0.1:19387/?token=test',0,True),('http://example.com/?token=test',0,False),('http://127.0.0.1:19387/?token=test',60,False),('http://127.0.0.1:19387/?token=a&token=b',0,False),('http://user@127.0.0.1:19387/?token=x',0,False)]:
                path.write_text(json.dumps({'version':1,'url':url,'pid':123,'updated':(time.time()-age)*1000}))
                if valid:self.assertEqual(connection_record(path)[1],'http://127.0.0.1:19387')
                else:
                    with self.assertRaises(ControlError):connection_record(path)

if __name__=='__main__':unittest.main()

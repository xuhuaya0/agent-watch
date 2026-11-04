import copy
import unittest
from aw.control.antigravity_interactions import NativeInteractions
from aw.control.broker import ControlError
SID='cbffb29d-f2f0-4a1a-9bf9-e8dc4c198554'
TID='257dd3e8-afd5-46e6-9f8b-9857ccef508d'


class Rpc:
    def __init__(self):
        # Shape observed from the real dedicated Windows test conversation.
        self.step={'type':'CORTEX_STEP_TYPE_GENERIC','status':'CORTEX_STEP_STATUS_WAITING',
                   'requestedInteraction':{'askQuestion':{'questions':[{'question':'测试颜色？',
                       'options':[{'id':'1','text':'Blue'},{'id':'2','text':'Green'}]}]}}}
        self.writes=[];self.result={};self.tid=TID;self.total=1
    def call(self,method,body):
        if method=='GetConversationMetadata':return {'trajectoryId':TID}
        if method=='GetCascadeTrajectory':return {'numTotalSteps':self.total,'trajectory':{
            'trajectoryId':self.tid,'cascadeId':SID,'steps':[copy.deepcopy(self.step)]}}
        self.writes.append(copy.deepcopy(body));return self.result


class NativeQuestionTests(unittest.TestCase):
    def setUp(self):self.rpc=Rpc();self.provider=NativeInteractions(self.rpc)
    def action(self,selected=None,custom=''):
        req=self.provider.inspect(SID)[0]['requests'][0]
        return {'kind':'answer','request_id':req['id'],'revision':req['revision'],
                'answers':{'0':{'selected':['Blue'] if selected is None else selected,'custom':custom}}}
    def test_real_generic_question_maps_labels_to_native_ids(self):
        row,_=self.provider.inspect(SID);self.assertFalse(row['reply'])
        self.assertEqual(self.provider.execute(SID,self.action()),'delivered')
        body=self.rpc.writes[0]
        self.assertEqual(body['cascadeId'],SID)
        self.assertEqual(set(body['interaction']),{'trajectoryId','stepIndex','askQuestion'})
        self.assertEqual(body['interaction']['trajectoryId'],TID)
        self.assertEqual(body['interaction']['stepIndex'],0)
        self.assertEqual(body['interaction']['askQuestion']['responses'][0]['selectedOptionIds'],['1'])
    def test_changed_or_resolved_request_never_submits(self):
        old=self.action()
        self.rpc.step['requestedInteraction']['askQuestion']['questions'][0]['question']='Changed'
        with self.assertRaisesRegex(ControlError,'request_expired'):self.provider.execute(SID,old)
        self.rpc.step['status']='CORTEX_STEP_STATUS_DONE'
        with self.assertRaisesRegex(ControlError,'request_expired'):self.provider.execute(SID,old)
        self.assertEqual(self.rpc.writes,[])
    def test_mismatched_or_partial_trajectory_fails_closed(self):
        action=self.action();self.rpc.tid='d0c039d9-2cf2-4c38-ad8a-ffb06685aaab'
        with self.assertRaises(ControlError):self.provider.execute(SID,action)
        self.rpc.tid=TID;self.rpc.total=2
        with self.assertRaises(ControlError):self.provider.execute(SID,action)
        self.assertEqual(self.rpc.writes,[])
    def test_unknown_permission_or_ambiguous_options_do_not_create_controls(self):
        self.rpc.step['requestedInteraction']={'permission':{'allow':False}}
        self.assertEqual(self.provider.inspect(SID)[0]['requests'],[])
        self.assertFalse(self.provider.inspect(SID)[0]['reply'])
        self.rpc=Rpc();self.provider=NativeInteractions(self.rpc)
        self.rpc.step['requestedInteraction']['askQuestion']['questions'][0]['options'][1]['text']='Blue'
        self.assertEqual(self.provider.inspect(SID)[0]['requests'],[])
    def test_uncertain_write_is_attempted_once(self):
        self.rpc.result=None
        self.assertEqual(self.provider.execute(SID,self.action()),'unknown')
        self.assertEqual(len(self.rpc.writes),1)
    def test_malformed_native_snapshots_fail_closed(self):
        for payload in (None, [], {'trajectory':None}, {'trajectory':[]}, {'trajectory':{}}):
            with self.subTest(payload=payload):
                original=self.rpc.call
                self.rpc.call=lambda method,body: {'trajectoryId':TID} if method=='GetConversationMetadata' else payload
                with self.assertRaisesRegex(ControlError,'native_session_unavailable'):
                    self.provider.inspect(SID)
                self.rpc.call=original
        self.assertEqual(self.rpc.writes,[])
    def test_freeform_and_multiple_answer_validation(self):
        self.provider.execute(SID,self.action([],custom='Literal & $(no shell)'))
        self.assertEqual(self.rpc.writes[0]['interaction']['askQuestion']['responses'][0]['writeInResponse'],'Literal & $(no shell)')
        action=self.action(['Blue','Green'])
        with self.assertRaises(ControlError):self.provider.execute(SID,action)
        self.rpc.step['requestedInteraction']['askQuestion']['questions'][0]['isMultiSelect']=True
        self.provider.execute(SID,self.action(['Blue','Green']))
        self.assertEqual(self.rpc.writes[-1]['interaction']['askQuestion']['responses'][0]['selectedOptionIds'],['1','2'])

    def test_malformed_optional_project_metadata_preserves_question_controls(self):
        original=self.rpc.call
        for metadata in (None,[],['unexpected'],42,True,'unexpected'):
            with self.subTest(metadata=metadata):
                self.rpc.call=lambda method,body: {'trajectoryId':TID,'metadata':metadata} if method=='GetConversationMetadata' else original(method,body)
                context={}
                row,_=self.provider.inspect(SID,context=context)
                self.assertIsNone(context['project_id'])
                self.assertEqual(len(row['requests']),1)
        self.rpc.call=original


class NativeApprovalTests(unittest.TestCase):
    def setUp(self):
        self.rpc=Rpc()
        # Real Request Review payload from the isolated Windows test project.
        self.permission={'resource':{'action':'command','target':"Write-Output 'AW-APPROVAL-TEST'"},
            'persistSuggestionType':'PERSIST_SUGGESTION_TYPE_SUGGESTED',
            'suggestedPersistPattern':"Write-Output 'AW-APPROVAL-TEST'",
            'actionDescription':'Approval test output',
            'triggerSource':{'type':'TYPE_DEFAULT_FALLBACK'},'shellName':'powershell'}
        self.rpc.step['requestedInteraction']={'permission':self.permission}
        self.provider=NativeInteractions(self.rpc,approvals=True)
    def action(self,decision='approve'):
        req=self.provider.inspect(SID)[0]['requests'][0]
        return {'kind':'approval','request_id':req['id'],'revision':req['revision'],'decision':decision}
    def test_one_shot_approve_and_reject_never_persist_or_edit(self):
        row,_=self.provider.inspect(SID)
        self.assertFalse(row['reply'])
        self.assertIn(self.permission['resource']['target'],row['requests'][0]['prompt'])
        for decision in ('approve','reject'):
            self.assertEqual(self.provider.execute(SID,self.action(decision)),'delivered')
            self.assertEqual(self.rpc.writes[-1],{'cascadeId':SID,'interaction':{
                'trajectoryId':TID,'stepIndex':0,'permission':{
                    'allow':decision=='approve','scope':'PERMISSION_SCOPE_ONCE'}}})
    def test_explicit_enable_required(self):
        row,_=NativeInteractions(self.rpc).inspect(SID)
        self.assertFalse(row['reply']);self.assertEqual(row['requests'],[])
    def test_every_changed_permission_field_expires_action(self):
        changes={'resource':{'action':'command','target':'different command'},
                 'actionDescription':'different explanation','reason':'changed',
                 'suggestedPersistPattern':'different pattern','shellName':'bash',
                 'approvalScopeFilter':['PERMISSION_SCOPE_ONCE']}
        for key,value in changes.items():
            with self.subTest(key=key):
                saved=copy.deepcopy(self.permission);action=self.action()
                self.permission[key]=value
                with self.assertRaisesRegex(ControlError,'request_expired'):
                    self.provider.execute(SID,action)
                self.permission.clear();self.permission.update(saved)
        self.assertEqual(self.rpc.writes,[])
    def test_unknown_shapes_and_non_once_scopes_are_not_actionable(self):
        mutations=[{'resource':{'action':'file_write','target':'a'}},
            {'triggerSource':{'type':'TYPE_SANDBOX'}}, {'sandboxOverride':True},
            {'approvalScopeFilter':['PERMISSION_SCOPE_GLOBAL']},
            {'approvalScopeFilter':'PERMISSION_SCOPE_ONCE'},
            {'approvalScopeFilter':[{}]}, {'resource':{'action':'command','target':'x'*12001}},
            {'reason':{}}, {'persistSuggestionType':'future_type'}]
        for mutation in mutations:
            self.rpc.step['requestedInteraction']={'permission':{**self.permission,**mutation}}
            row,_=self.provider.inspect(SID)
            self.assertFalse(row['reply']);self.assertEqual(row['requests'],[])
        self.assertEqual(self.rpc.writes,[])
    def test_resolved_request_and_unsupported_decisions_do_not_write(self):
        action=self.action();action['decision']='always'
        with self.assertRaises(ControlError):self.provider.execute(SID,action)
        action=self.action();self.rpc.step['status']='CORTEX_STEP_STATUS_DONE'
        with self.assertRaisesRegex(ControlError,'request_expired'):self.provider.execute(SID,action)
        self.assertEqual(self.rpc.writes,[])
    def test_uncertain_approval_is_not_retried(self):
        self.rpc.result=None
        self.assertEqual(self.provider.execute(SID,self.action()),'unknown')
        self.assertEqual(len(self.rpc.writes),1)

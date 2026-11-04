import copy
import json
from pathlib import Path
import tempfile
import unittest
from aw.control.antigravity_create import ProjectCreation
from aw.control.broker import ControlError, normalize_sessions

SOURCE='00000000-0000-4000-8000-000000000001'
PROJECT='00000000-0000-4000-8000-000000000002'
MODEL='MODEL_SYNTHETIC_A'

class Rpc:
    def __init__(self):
        self.project={'id':PROJECT,'name':'Example project','settings':{'permissionPreset':2}}
        self.owner=PROJECT;self.model=MODEL;self.writes=[];self.targets={};self.lost=None;self.started=lambda:None
        self.steps=[{'userInput':{'userConfig':{'plannerConfig':{'planModel':MODEL,
                    'toolConfig':{'runCommand':{'autoExecutionPolicy':'do-not-copy'}}}}}}]
        self.static={};self.target_mismatch=False
    def call(self,name,body):
        if name=='GetConversationMetadata':
            owner=self.owner if body['conversationId']==SOURCE else self.targets.get(body['conversationId'])
            return {'metadata':{'projectId':owner,'staticConfig':self.static}}
        if name=='ReadProject':return {'project':copy.deepcopy(self.project)}
        if name=='GetCascadeTrajectory':return {'trajectory':{'cascadeId':SOURCE,'steps':copy.deepcopy(self.steps)}}
        self.writes.append((name,copy.deepcopy(body)))
        if name=='StartCascade':
            sid=body['cascadeId'];self.targets[sid]='another-project' if self.target_mismatch else PROJECT
            self.started()
            return None if self.lost==name else {'cascadeId':sid}
        if name=='SendUserCascadeMessage':return None if self.lost==name else {}
        raise AssertionError(name)

class CreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'control.json';self.path.write_text(json.dumps({'antigravity':{'scope':'all'}}))
        self.rpc=Rpc();self.creation=ProjectCreation(self.rpc,self.path);self.ids=[SOURCE]
    def session_ids(self):return self.ids
    def session_snapshot(self,sid):
        cap=self.creation.capability(sid)
        return [{'tool':'antigravity','session_id':sid,'reply':True,'requests':[],**({'create':cap} if cap else {})}]
    def command(self):
        cap=self.creation.capability(SOURCE)
        return {'session_id':SOURCE,'action':{'kind':'create','project_id':PROJECT,'revision':cap['revision'],'text':'fixed test'}}
    def test_new_native_id_original_project_and_model_only(self):
        cap=self.creation.capability(SOURCE);self.assertEqual(cap['project_id'],PROJECT)
        self.assertEqual(normalize_sessions(self.session_snapshot(SOURCE))[('antigravity',SOURCE)]['create'],cap)
        result=self.creation.execute(self,self.command());target=result['created_session_id']
        self.assertEqual(result['state'],'delivered');self.assertNotEqual(target,SOURCE)
        self.assertEqual(self.rpc.writes,[('StartCascade',{'cascadeId':target,'source':19,
            'projectEnvConfig':{'projectId':PROJECT,'defaultProjectEnvironment':{}}}),
            ('SendUserCascadeMessage',{'cascadeId':target,'items':[{'text':'fixed test'}],
             'cascadeConfig':{'plannerConfig':{'planModel':MODEL}}})])
    def test_capability_rejects_selected_archived_remote_and_unknown_model(self):
        for change in ('selected','archived','remote','boolean_backend','model','metadata'):
            with self.subTest(change=change):
                self.setUp()
                if change=='selected':self.path.write_text(json.dumps({'antigravity':{'scope':'selected'}}))
                if change=='archived':self.rpc.project['archived']=True
                if change=='remote':self.rpc.project['backendType']=2
                if change=='boolean_backend':self.rpc.project['backendType']=True
                if change=='model':self.rpc.steps[-1]['userInput']['userConfig']['plannerConfig']['planModel']='unknown'
                if change=='metadata':self.rpc.owner='invalid'
                self.assertIsNone(self.creation.capability(SOURCE));self.assertFalse(self.rpc.writes)
    def test_latest_model_has_priority_and_static_fallback_copies_no_permissions(self):
        self.rpc.static={'commandExecutionPolicy':'eager','cascadeConfig':{'plannerConfig':{'planModel':'MODEL_SYNTHETIC_B'}}}
        self.assertEqual(self.creation.capability(SOURCE)['model_name'],MODEL)
        self.rpc.steps=[];self.assertEqual(self.creation.capability(SOURCE)['model_name'],'MODEL_SYNTHETIC_B')
        self.rpc.steps=[{'userInput':{'userConfig':{'plannerConfig':{'requestedModel':{'alias':'custom'}}}}}]
        self.assertIsNone(self.creation.capability(SOURCE))
    def test_stale_model_or_project_is_rejected_before_creation(self):
        for change in ('model','project'):
            with self.subTest(change=change):
                self.setUp();command=self.command()
                if change=='model':self.rpc.steps[-1]['userInput']['userConfig']['plannerConfig']['planModel']='MODEL_SYNTHETIC_B'
                else:self.rpc.project['settings']['permissionPreset']=3
                with self.assertRaises(ControlError):self.creation.execute(self,command)
                self.assertFalse(self.rpc.writes)
    def test_project_or_scope_change_during_creation_never_sends_prompt(self):
        for change in ('target','scope','source','settings'):
            with self.subTest(change=change):
                self.setUp();command=self.command()
                if change=='target':self.rpc.target_mismatch=True
                if change=='scope':self.rpc.started=lambda:self.path.write_text(json.dumps({'antigravity':{'scope':'selected'}}))
                if change=='source':self.rpc.started=lambda:self.ids.clear()
                if change=='settings':self.rpc.started=lambda:self.rpc.project['settings'].update(permissionPreset=3)
                result=self.creation.execute(self,command)
                self.assertEqual(result['state'],'unknown');self.assertEqual(len(self.rpc.writes),1)
                self.assertTrue(result['created_session_id'])
    def test_membership_timestamp_changes_do_not_invalidate_creation(self):
        command=self.command()
        self.rpc.started=lambda:self.rpc.project.update(updatedAt='later',projectConversations={'conversations':[]})
        self.assertEqual(self.creation.execute(self,command)['state'],'delivered')

    def test_scope_revocation_during_native_reads_prevents_the_next_write(self):
        for revoke_at in (2,3):
            with self.subTest(revoke_at=revoke_at):
                self.setUp();command=self.command();native=self.rpc.call;reads=0
                def call(method,body):
                    nonlocal reads
                    response=native(method,body)
                    if method=='GetCascadeTrajectory':
                        reads+=1
                        if reads==revoke_at:
                            self.path.write_text(json.dumps({'antigravity':{'scope':'selected'}}))
                    return response
                self.rpc.call=call
                if revoke_at==2:
                    with self.assertRaisesRegex(ControlError,'create_unavailable'):
                        self.creation.execute(self,command)
                    self.assertEqual(self.rpc.writes,[])
                else:
                    self.assertEqual(self.creation.execute(self,command)['state'],'unknown')
                    self.assertEqual([m for m,b in self.rpc.writes],['StartCascade'])

    def test_uncertainty_keeps_new_id_and_never_retries(self):
        for method in ('StartCascade','SendUserCascadeMessage'):
            with self.subTest(method=method):
                self.setUp();self.rpc.lost=method
                result=self.creation.execute(self,self.command())
                self.assertEqual(result['state'],'unknown');self.assertTrue(result['created_session_id'])
                self.assertEqual([n for n,b in self.rpc.writes].count(method),1)

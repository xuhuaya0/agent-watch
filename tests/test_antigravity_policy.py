import copy
from pathlib import Path
import tempfile
import unittest
from aw.control.antigravity import Antigravity
from aw.control.antigravity_policy import ProjectPermissions, valid_capability
from aw.control.antigravity_rpc import NativeRpc
from aw.control.broker import ControlError, normalize_sessions
from aw.control.worker import Worker

SID = 'session-a'
PID = 'project-a'

class Rpc:
    def __init__(self):
        self.project = {'id':PID,'name':'Test project','backendType':'BACKEND_TYPE_LOCAL',
            'projectConversations':{'conversations':[{'conversationId':SID}]},
            'projectResources':{'resources':[{'folderUri':'file:///project'}]},
            'settings':{'securityPlugins':{'p':{'enabled':True}},'artifactReviewMode':2},
            'permissionGrants':{'v2Migrated':True},'updatedAt':'initial'}
        self.controls = {};self.sandbox = True;self.auto = True;self.owner = PID
        self.writes=[];self.fail_members=False;self.after=lambda:None;self.lost=False
    def call(self,method,data):
        if method=='GetConversationMetadata':return {'metadata':{'projectId':self.owner}}
        if method=='FetchAdminControls':return dict(self.controls)
        if method=='GetServerConfiguration':return {'config':{'sandboxAvailable':self.sandbox}}
        if method=='GetMendelFlags':return {'experimentConfig':{'experiments':[
            {'keyString':'enable-auto-permission-preset','disabled':not self.auto}]}}
        if method=='ReadProject':
            if self.fail_members:return {'notFoundOnDisk':True}
            return {'project':copy.deepcopy(self.project)} if data['id']==self.project['id'] else {}
        if method=='UpdateProject':
            self.writes.append(copy.deepcopy(data));self.project = copy.deepcopy(data['project'])
            self.project['updatedAt']='after';self.after()
            return None if self.lost else {}
        raise AssertionError(method)

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.rpc = Rpc();self.permissions = ProjectPermissions(self.rpc)
    def row(self,sid):
        return [{'tool':'antigravity','session_id':sid,'reply':True,'requests':[],
                 'policy':self.permissions.capability(sid,PID)}]
    def action(self,value):
        cap = self.row(SID)[0]['policy']
        return {'kind':'policy','value':value,'revision':cap['revision'],'project_id':PID}
    def execute(self,action):return self.permissions.execute(SID,action,self.row,lambda:True)
    def test_native_project_write_and_restoration(self):
        before = copy.deepcopy(self.rpc.project)
        cap = self.row(SID)[0]['policy'];self.assertEqual(cap['current'],'use_global')
        self.assertEqual(normalize_sessions(self.row(SID))[('antigravity',SID)]['policy'],cap)
        self.assertEqual(self.execute(self.action('request_review')),'policy_updated')
        self.assertEqual(self.rpc.writes[0]['project']['settings'],{**before['settings'],
            'permissionPreset':2,'autoExecutionPolicy':1,'fileAccessPolicy':2,'sandboxMode':False})
        self.assertEqual(self.execute(self.action('use_global')),'policy_updated')
        for k in ('projectResources','permissionGrants','projectConversations'):
            self.assertEqual(before[k],self.rpc.project[k])
        self.assertNotIn('sandboxMode',self.rpc.project['settings'])
        self.assertEqual(self.rpc.project['settings']['securityPlugins'],before['settings']['securityPlugins'])
    def test_declines_stale_project_edits_and_scope_changes(self):
        for change in ('name','resource','owner','archived','remote','invalid-backend','membership','pending','replace'):
            with self.subTest(change=change):
                self.setUp();action=self.action('request_review');snapshot=self.row
                if change=='name':self.rpc.project['name']='renamed'
                if change=='resource':self.rpc.project['projectResources']={}
                if change=='owner':self.rpc.owner='other-project'
                if change=='archived':self.rpc.project['archived']=True
                if change=='remote':self.rpc.project['backendType']='BACKEND_TYPE_REMOTE_AICODE'
                if change=='invalid-backend':self.rpc.project['backendType']=True
                if change=='membership':self.rpc.project['projectConversations']['conversations']=[]
                if change=='pending':snapshot=lambda sid:[dict(self.row(sid)[0],reply=False)]
                if change=='replace':action['project_id']='other-project'
                with self.assertRaises(ControlError):self.permissions.execute(SID,action,snapshot,lambda:True)
                self.assertEqual(self.rpc.writes,[])
    def test_gates_rechecked_before_write(self):
        action=self.action('auto');self.rpc.auto=False
        with self.assertRaises(ControlError):self.execute(action)
        self.rpc.sandbox=False
        cap=self.permissions.capability(SID,PID,fresh=True)
        self.assertEqual([o['value'] for o in cap['options']],['use_global','request_review','turbo'])
        self.rpc.controls={'adminControlsApplicable':True};cap=self.permissions.capability(SID,PID,fresh=True)
        self.assertEqual([o['value'] for o in cap['options']],['use_global'])
        self.assertFalse(self.rpc.writes)

    def test_native_metadata_binds_project_when_legacy_members_are_omitted(self):
        self.rpc.project.pop('projectConversations')
        self.assertIsNotNone(self.permissions.capability(SID,PID))
        self.assertEqual(self.execute(self.action('request_review')),'policy_updated')
        self.assertNotIn('projectConversations',self.rpc.writes[0]['project'])
        self.rpc.owner='another-project'
        self.assertIsNone(self.permissions.capability(SID,PID))
    def test_uncertain_ack_or_unrelated_postwrite_difference_is_unknown(self):
        for change in ('lost','resource','unexpected','missing'):
            with self.subTest(change=change):
                self.setUp();action=self.action('request_review')
                if change=='lost':self.rpc.lost=True
                if change=='resource':self.rpc.after=lambda:self.rpc.project.pop('projectResources')
                if change=='unexpected':self.rpc.after=lambda:self.rpc.project['settings'].update(sandboxMode=True)
                if change=='missing':self.rpc.after=lambda:setattr(self.rpc,'fail_members',True)
                self.assertEqual(self.execute(action),'unknown');self.assertEqual(len(self.rpc.writes),1)
    def test_malformed_panel_contracts_excluded(self):
        cap=self.row(SID)[0]['policy']
        for field,bad in [('scope','global'),('project_id',''),('options',[{'value':'extra','label':'x'}]),
                          ('options',[cap['options'][0]]*2),('revision','stale')]:
            self.assertFalse(valid_capability({**cap,field:bad}))
    def test_mixed_modes_are_custom_and_same_value_requires_no_write(self):
        self.assertEqual(self.execute(self.action('use_global')),'policy_updated');self.assertEqual(self.rpc.writes,[])
        self.rpc.project['settings'].update(permissionPreset='AGENT_PERMISSION_PRESET_REQUEST_REVIEW',
            autoExecutionPolicy='CASCADE_COMMANDS_AUTO_EXECUTION_EAGER',sandboxMode=False,fileAccessPolicy=2)
        self.assertEqual(self.row(SID)[0]['policy']['current'],'custom')
        self.rpc.project['settings'].update(autoExecutionPolicy=1,sandboxMode=0)
        self.assertEqual(self.row(SID)[0]['policy']['current'],'custom')

    def test_optional_native_discovery_failures_hide_only_this_capability(self):
        original = self.rpc.call
        for method, malformed in [('FetchAdminControls', []), ('GetServerConfiguration', []),
                                  ('GetMendelFlags', {'experimentConfig':{'experiments':[None]}}),
                                  ('ReadProject', []), ('ReadProject', {'project':{}})]:
            with self.subTest(method=method, malformed=malformed):
                calls = []
                def call(name, data):
                    calls.append(name)
                    return malformed if name == method else original(name,data)
                self.rpc.call = call
                self.permissions = ProjectPermissions(self.rpc)
                self.assertIsNone(self.permissions.capability(SID,PID))
                first = calls.count(method)
                self.assertIsNone(self.permissions.capability(SID,PID))
                if method != 'ReadProject':
                    self.assertEqual(calls.count(method),first)
                self.assertFalse(self.rpc.writes)
        self.rpc.call = original

    def test_project_write_transport_uncertainty_is_not_retried(self):
        class TimeoutOpener:
            def __init__(self):self.calls = []
            def open(self, request, timeout):
                self.calls.append(request)
                raise TimeoutError()
        client = TimeoutOpener()
        rpc = NativeRpc({'ANTIGRAVITY_LS_ADDRESS':'localhost:1234',
                         'ANTIGRAVITY_CSRF_TOKEN':'synthetic-test-context'},client)
        self.assertIsNone(rpc.call('UpdateProject',{'project':self.rpc.project}))
        self.assertEqual(len(client.calls),1)
        with self.assertRaises(ControlError):rpc.call('UpdateGlobalSettings',{})
        self.assertEqual(len(client.calls),1)

    def test_local_revocation_during_native_reads_rejects_before_project_write(self):
        for revoked in ('opt-in','all-scope','session'):
            with self.subTest(revoked=revoked):
                self.setUp()
                class Scope:
                    enabled = True
                    mode = 'all'
                    ids = [SID]
                    def __call__(self):return self.ids
                    def allows_project_permissions(self):return self.enabled and self.mode == 'all'
                class Interactions:
                    def inspect(self,sid,context=None):
                        if context is not None:context['project_id']=PID
                        return {'tool':'antigravity','session_id':sid,'reply':True,'requests':[]},{}
                scope=Scope()
                provider=Antigravity('/synthetic/native',scope,interactions=Interactions(),
                    environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:1234'},project_permissions=self.permissions)
                cap=provider.session_snapshot(SID)[0]['policy']
                action={'kind':'policy','value':'request_review','project_id':PID,'revision':cap['revision']}
                original=self.rpc.call
                fresh_started=False
                def call(method,data):
                    nonlocal fresh_started
                    if method=='GetMendelFlags':fresh_started=True
                    result=original(method,data)
                    if fresh_started and method=='ReadProject':
                        if revoked=='opt-in':scope.enabled=False
                        if revoked=='all-scope':scope.mode='selected'
                        if revoked=='session':scope.ids=[]
                    return result
                self.rpc.call=call
                root=Path(self.enterContext(tempfile.TemporaryDirectory()))
                worker=Worker({'device_id':'test-device','hub_url':'http://127.0.0.1:1','token':'test-key'},
                    {'antigravity':provider},root/'state.json')
                worker.exchange=lambda body:{'ok':True,'receipts':[], 'command':{
                    'id':'test-command','tool':'antigravity','session_id':SID,'action':action}}
                worker.step()
                self.assertEqual(self.rpc.writes,[])
                receipt=worker.state['receipts'][0]
                self.assertEqual((receipt['state'],receipt['detail']),('rejected','policy_changed'))

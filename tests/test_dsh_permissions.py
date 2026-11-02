import copy
import unittest
import tempfile
from pathlib import Path
from aw.control.worker import Worker
from aw.control.broker import ControlError, normalize_sessions
from aw.control.dsh import DeepSeek
from aw.control.dsh_permissions import DEFINITION, options
from test_dsh_control import FakeGateway, SID

CATALOG={'options':[{'value':'workspace-write','name':'Workspace','description':'Workspace sandbox; ask when needed'},
                    {'value':'danger-full-access','name':'Full access','description':'Unrestricted sandbox; never ask'}]}

class Gateway(FakeGateway):
    def __init__(self,path,event):
        super().__init__(path,event)
        self.catalog[0].update(agentAvailable=True,running=False,
            projections={'values':{'permissions':{'currentValue':'workspace-write'},'inbox':{'next-turn':[],'next-step':[]}}})
        self.presets=copy.deepcopy(CATALOG);self.definition=DEFINITION
        self.ack=True;self.apply=True;self.after=None;self.broken=False
    def call(self,endpoint,args):
        if endpoint not in ('permissionPresets/catalog','commands/list','commands/execute'):return super().call(endpoint,args)
        self.calls.append((endpoint,copy.deepcopy(args)))
        if self.broken:raise OSError('disconnected')
        if endpoint=='permissionPresets/catalog':return copy.deepcopy(self.presets)
        if endpoint=='commands/list':return [{'name':'permission','definitionId':self.definition}]
        if self.apply:self.catalog[0]['projections']['values']['permissions']['currentValue']=args['line'].split(' ')[1]
        if self.after:self.after(self)
        return {'commandId':'native-command','result':{'kind':'success' if self.ack else 'error'}}

class PermissionTests(unittest.TestCase):
    def provider(self):
        p=DeepSeek({'connection_file':'unused','scope':'all'},Gateway,background=False);p.refresh();return p
    def command(self,p):
        return {'id':'cmd-policy','session_id':SID,'action':{'kind':'policy',
            'revision':p.snapshot()[0]['policy']['revision'],'value':'danger-full-access'}}
    def writes(self,p):return [c for c in p.gateway.calls if c[0]=='commands/execute']
    def test_native_catalog_roundtrip_and_exact_command(self):
        p=self.provider();cap=p.snapshot()[0]
        self.assertEqual(normalize_sessions([cap])[('dsh',SID)]['policy'],cap['policy'])
        self.assertEqual(p.execute(self.command(p)),'policy_updated')
        self.assertEqual(self.writes(p),[('commands/execute',{'agentId':SID,'line':'/permission danger-full-access','submittedAttachments':[]})])
        self.assertFalse(any(c[0]=='session/prompt' for c in p.gateway.calls))
    def test_worker_receipt_does_not_claim_sandbox_is_unchanged(self):
        p=self.provider();command={**self.command(p),'tool':'dsh'}
        with tempfile.TemporaryDirectory() as tmp:
            w=Worker({'device_id':'test','hub_url':'http://localhost:1','token':'fake'},
                     {'dsh':p},Path(tmp)/'state.json')
            w.exchange=lambda body:{'ok':True,'receipts':[],'command':command}
            self.assertTrue(w.step());w.step()
            receipt=w.state['receipts'][0]
            self.assertEqual(receipt['state'],'resolved')
            self.assertIn('沙盒与审批设置',receipt['detail'])
            self.assertNotIn('保持原值',receipt['detail'])
            self.assertEqual(len(self.writes(p)),1)
    def test_bad_or_changed_native_command_and_catalog_prevent_write(self):
        for change in ('owner','option','state','running','queue','archive','unavailable','question'):
            with self.subTest(change=change):
                p=self.provider();cmd=self.command(p);g=p.gateway
                if change=='owner':g.definition='unrelated-plugin'
                elif change=='option':g.presets['options'][1]['description']='Changed behavior'
                elif change=='state':g.catalog[0]['projections']['values']['permissions']['currentValue']='custom'
                elif change=='running':g.catalog[0]['running']=True
                elif change=='queue':g.catalog[0]['projections']['values']['inbox']['next-turn']=['queued']
                elif change=='archive':g.catalog[0]['archived']=True
                elif change=='unavailable':g.catalog[0]['agentAvailable']=False
                else:p.event({'type':'waterfall','eventId':'q','agentId':SID,'event':'user-questions/request','request':{'questions':[{'id':'q','question':'Choose?'}]}})
                with self.assertRaises(ControlError):p.execute(cmd)
                self.assertEqual(self.writes(p),[])
    def test_unknown_response_or_failed_readback_is_not_success(self):
        for mode in ('negative','no-change','archive','disconnect'):
            p=self.provider();cmd=self.command(p);g=p.gateway
            if mode=='negative':g.ack=False
            elif mode=='no-change':g.apply=False
            elif mode=='archive':g.after=lambda x:x.catalog[0].update(archived=True)
            else:g.after=lambda x:x.close()
            self.assertEqual(p.execute(cmd),'unknown')
            self.assertEqual(len([c for c in g.calls if c[0]=='commands/execute']),1)
    def test_invalid_names_and_schema_fail_closed(self):
        for name in ('custom','x y','x\n/other','-option','x;other'):
            bad=copy.deepcopy(CATALOG);bad['options'][1]['value']=name
            self.assertIsNone(options(bad))
        p=self.provider();cap=p.snapshot()[0]
        for mutate in (lambda c:c['policy'].update(scope='global'),lambda c:c['policy']['options'][0].update(shell='bad'),lambda c:c.update(tool='antigravity')):
            c=copy.deepcopy(cap);mutate(c)
            with self.assertRaises(ControlError):normalize_sessions([c])
    def test_custom_visible_but_cannot_be_targeted(self):
        p=self.provider();p.gateway.catalog[0]['projections']['values']['permissions']['currentValue']='custom';p.refresh()
        cmd=self.command(p);cmd['action']['value']='custom'
        with self.assertRaises(ControlError):p.execute(cmd)
        self.assertEqual(self.writes(p),[])
    def test_optional_api_failure_keeps_other_capabilities(self):
        p=self.provider();p.permissions.checked=-100;p.gateway.broken=True;p.refresh()
        row=p.snapshot()[0];self.assertTrue(row['reply']);self.assertTrue(row['history']);self.assertNotIn('policy',row)
    def test_read_cache_limits_optional_calls_but_write_refreshes(self):
        p=self.provider();p.refresh();p.refresh()
        self.assertEqual(sum(c[0]=='commands/list' for c in p.gateway.calls),1)
        p.execute(self.command(p))
        self.assertEqual(sum(c[0]=='commands/list' for c in p.gateway.calls),2)

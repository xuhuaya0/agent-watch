import copy
import unittest

from aw.control.antigravity import Antigravity
from aw.control.antigravity_settings import NativeSettings, selected_model
from aw.control.dsh import DeepSeek
from aw.control.dsh_permissions import Permissions
from aw.control.dsh_settings import SessionSettings
from test_dsh_permissions import Gateway as PermissionGateway


class AgRpc:
    def __init__(self):
        self.calls = []
        self.response = {'userStatus':{'privateAccountField':'must not leave this adapter',
            'cascadeModelConfigData':{'clientModelConfigs':[
                {'label':'Example Model A', 'modelOrAlias':{'model':'MODEL_SYNTHETIC_A'}}]}}}

    def call(self, method, body):
        self.calls.append((method, body))
        if isinstance(self.response, Exception):raise self.response
        return copy.deepcopy(self.response)


def ag_context(model='MODEL_SYNTHETIC_A'):
    return {'trajectory':{'steps':[{'userInput':{'userConfig':{'plannerConfig':{'planModel':model}}}}]}}


class AntigravitySettingsTests(unittest.TestCase):
    def test_waiting_chat_retains_settings_without_permission_write_controls(self):
        class Scope:
            def __call__(self):return ['native-session']
            def allows_project_permissions(self):return True
        class Interactions:
            rpc = AgRpc()
            def inspect(self, sid, context=None):
                context.update(ag_context());context['project_id'] = 'native-project'
                return {'tool':'antigravity','session_id':sid,'reply':False,'requests':[]}, {}
        class ProjectPermissions:
            def capability(self, sid, project_id):
                return {'current':'request_review','project_name':'Example project'}
        provider = Antigravity('/synthetic/native', Scope(), interactions=Interactions(),
                               project_permissions=ProjectPermissions())
        result = provider.session_snapshot('native-session')[0]
        self.assertNotIn('policy', result)
        self.assertEqual(result['settings']['permission'], 'Request Review')
        self.assertEqual(result['settings']['model'], 'Example Model A')

    def test_only_native_model_label_and_project_summary_leave_adapter(self):
        rpc = AgRpc();settings = NativeSettings(rpc)
        policy = {'current':'request_review', 'project_name':'Example project'}
        result = settings.read(ag_context(), policy)
        self.assertEqual(result, {'scope':'project','project_name':'Example project',
            'permission':'Request Review','model':'Example Model A',
            'note':'模型来自已保存的会话记录，不含原应用中尚未发送的选择。'})
        self.assertNotIn('privateAccountField', str(settings.__dict__))
        settings.read(ag_context(), policy)
        self.assertEqual(rpc.calls, [('GetUserStatus', {})])

    def test_missing_ambiguous_and_alias_models_are_not_guessed(self):
        for rows in ([], [{'label':'Alias label','modelOrAlias':{'alias':'MODEL_SYNTHETIC_A'}}],
                     [{'label':'One','modelOrAlias':{'model':'MODEL_SYNTHETIC_A'}},
                      {'label':'Two','modelOrAlias':{'model':'MODEL_SYNTHETIC_A'}}]):
            rpc = AgRpc();rpc.response['userStatus']['cascadeModelConfigData']['clientModelConfigs'] = rows
            result = NativeSettings(rpc).read(ag_context())
            self.assertNotIn('model', result)
            self.assertNotIn('MODEL_', str(result))
            self.assertIn('模型名称', result['note'])

    def test_latest_unsupported_selection_never_falls_back_to_old_model(self):
        context = ag_context()
        context['metadata'] = {'staticConfig':{'cascadeConfig':{'plannerConfig':{'planModel':'MODEL_SYNTHETIC_B'}}}}
        context['trajectory']['steps'].append({'userInput':{'userConfig':{'plannerConfig':{'planModel':'unknown'}}}})
        self.assertIsNone(selected_model(context))
        self.assertNotIn('model', NativeSettings(AgRpc()).read(context, create={'model_name':'MODEL_SYNTHETIC_A'}))

    def test_optional_failure_is_cached_and_permissions_remain(self):
        rpc = AgRpc();rpc.response = OSError('offline');settings = NativeSettings(rpc)
        for _ in range(2):
            result = settings.read(ag_context(), {'current':'custom'})
            self.assertEqual(result['permission'], '自定义组合')
            self.assertNotIn('model', result)
        self.assertEqual(len(rpc.calls), 1)

    def test_unknown_metadata_types_do_not_break_native_snapshot(self):
        for context in (None, [], {'trajectory':[]}, {'metadata':{'staticConfig':[]}},
                        {'trajectory':{'steps':[None]}}, ag_context(['malformed'])):
            with self.subTest(context=context):
                result = NativeSettings(AgRpc()).read(context, {'current':['unknown']},
                                                     {'model_name':['unknown']})
                self.assertNotIn('model', result);self.assertNotIn('permission', result)
        for payload in (None, [], {'userStatus':[]}, {'userStatus':{'cascadeModelConfigData':[]}},
                        {'userStatus':{'cascadeModelConfigData':{'clientModelConfigs':[None, {'modelOrAlias':[]}]}}}):
            rpc = AgRpc();rpc.response = payload
            self.assertNotIn('model', NativeSettings(rpc).read(ag_context()))
        self.assertNotIn('scope', NativeSettings(None).read({'project_id':'outside-of-project'}))


class ModelGateway(PermissionGateway):
    def __init__(self, path='unused', event=lambda _:None):
        super().__init__(path, event)
        self.catalog[0]['projections']['values']['modelSelection'] = {
            'next':{'provider':'example','model':'next','reasoningEffort':'high'},
            'lastUsed':{'provider':'example','model':'old'}}
        self.models = {'default':{'provider':'example','model':'fallback'}, 'groups':[
            {'id':'example','name':'Example provider','models':[
                {'id':'next','name':'Next model','reasoning':{'efforts':[{'id':'high','name':'High'}]}},
                {'id':'old','name':'Previous model'},
                {'id':'fallback','name':'Default model','reasoning':{
                    'defaultEffort':'medium','efforts':[{'id':'medium','name':'Medium'}]}}]}]}

    def call(self, endpoint, args):
        if endpoint == 'session/modelCatalog':
            self.calls.append((endpoint, copy.deepcopy(args)))
            if isinstance(self.models, Exception):raise self.models
            return copy.deepcopy(self.models)
        return super().call(endpoint, args)


class DeepSeekSettingsTests(unittest.TestCase):
    def test_running_chat_retains_native_model_permission_and_reasoning_names(self):
        provider = DeepSeek({'connection_file':'unused','scope':'all'}, ModelGateway, background=False)
        provider.refresh();provider.gateway.catalog[0]['running'] = True;provider.refresh()
        result = provider.snapshot()[0]
        self.assertNotIn('policy', result)
        self.assertEqual(result['settings'], {'scope':'session', 'permission':'Workspace',
            'model':'Next model','reasoning':'High'})
        self.assertEqual(sum(endpoint == 'session/modelCatalog' for endpoint,_ in provider.gateway.calls), 1)
        self.assertFalse(any(endpoint in ('commands/execute','session/selectModel','session/prompt')
                             for endpoint,_ in provider.gateway.calls))

    def test_next_null_matches_native_default_but_missing_projection_does_not(self):
        gateway = ModelGateway();settings = SessionSettings(Permissions());row = gateway.catalog[0]
        row['projections']['values']['modelSelection']['next'] = None
        result = settings.read(gateway, row)
        self.assertEqual(result['model'], 'Default model');self.assertEqual(result['reasoning'], 'Medium')
        del row['projections']['values']['modelSelection']['next']
        self.assertNotIn('model', settings.read(gateway, row))
        del row['projections']['values']['modelSelection']
        gateway.calls.clear()
        self.assertNotIn('model', SessionSettings(Permissions()).read(gateway, row))
        self.assertFalse(any(endpoint == 'session/modelCatalog' for endpoint,_ in gateway.calls))

    def test_unknown_native_model_never_uses_last_used_or_default(self):
        gateway = ModelGateway();row = gateway.catalog[0]
        row['projections']['values']['modelSelection']['next']['model'] = 'not-in-catalog'
        result = SessionSettings(Permissions()).read(gateway, row)
        self.assertNotIn('model', result);self.assertNotIn('reasoning', result)
        self.assertEqual(result['permission'], 'Workspace')

    def test_catalog_failure_is_optional_and_gateway_reconnect_invalidates_cache(self):
        gateway = ModelGateway();gateway.models = OSError('offline');settings = SessionSettings(Permissions())
        for _ in range(2):
            result = settings.read(gateway, gateway.catalog[0])
            self.assertNotIn('model', result);self.assertEqual(result['permission'], 'Workspace')
        self.assertEqual(sum(endpoint == 'session/modelCatalog' for endpoint,_ in gateway.calls), 1)
        connected = ModelGateway()
        self.assertEqual(settings.read(connected, connected.catalog[0])['model'], 'Next model')

    def test_unrecognized_permissions_are_not_replaced_with_a_default(self):
        gateway = ModelGateway();row = gateway.catalog[0]
        row['projections']['values']['permissions']['currentValue'] = 'unknown-new-preset'
        result = SessionSettings(Permissions()).read(gateway, row)
        self.assertNotIn('permission', result)
        self.assertEqual(result['model'], 'Next model')

    def test_malformed_optional_projection_and_catalog_keep_heartbeat_rows(self):
        provider = DeepSeek({'connection_file':'unused','scope':'all'}, ModelGateway, background=False)
        provider.refresh()
        values = provider.gateway.catalog[0]['projections']['values']
        for malformed in (None, [], {'next':[]}, {'next':{'provider':[],'model':{}}}):
            values['modelSelection'] = malformed
            values['permissions'] = {'currentValue':[]}
            provider.refresh()
            result = provider.snapshot()[0]
            self.assertTrue(result['reply']);self.assertTrue(result['history'])
            self.assertNotIn('model', result['settings'])
            self.assertNotIn('permission', result['settings'])
        gateway = ModelGateway()
        for catalog in (None, [], {'groups':None}, {'groups':[None]},
                        {'groups':[{'id':'example','models':[None, {'id':'next','name':[]}]}]}):
            gateway.models = catalog
            self.assertNotIn('model', SessionSettings(Permissions()).read(gateway, gateway.catalog[0]))


if __name__ == '__main__':unittest.main()

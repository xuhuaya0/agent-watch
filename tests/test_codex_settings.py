import copy
import unittest

from aw.control.codex_questions import patch_history, project
from aw.control.codex_settings import summary, model_selection, same_model, model_capability


class SettingsTests(unittest.TestCase):
    def test_model_aliases_require_the_same_provider_and_an_unambiguous_native_catalog_match(self):
        data = {'provider': 'openai', 'models': [{'value': 'model-a', 'label': 'Model A'}]}
        self.assertEqual(model_selection('openai/model-a', data, 'openai'), ('model-a', 'Model A'))
        self.assertTrue(same_model('openai/model-a', 'model-a', data, 'openai'))
        self.assertEqual(model_selection('other/model-a', data, 'openai'), ('other/model-a', 'other/model-a'))
        self.assertFalse(same_model('other/model-a', 'model-a', data, 'openai'))
        self.assertEqual(model_selection('openai/model-a', data, 'other')[0], 'openai/model-a')
        data['models'].append({'value': 'openai/model-a', 'label': 'Separate native choice'})
        self.assertFalse(same_model('openai/model-a', 'model-a', data, 'openai'))
        self.assertEqual(model_selection('openai/model-a', data, 'openai')[1], 'Separate native choice')

    def test_null_native_effort_is_not_replaced_by_a_catalog_default_or_previous_turn_effort(self):
        data = {'revision': 'r', 'models': [{'value': 'model-a', 'label': 'Model A',
                 'efforts': ['low', 'high'], 'default_effort': 'high'}], 'requirements': {}}
        state = {'history': {'threadRuntimeStatus': {'type': 'idle'},
                            'latestThreadSettings': {'effort': 'low'}}}
        result = model_capability(state, data, {'model': 'model-a', 'reasoningEffort': None, 'modelProvider': 'openai'})
        self.assertNotIn('effort', result)
        self.assertEqual(result['current'], 'model-a')

    def test_reports_native_fields_without_claiming_a_permission_preset(self):
        state = {'history': {'latestThreadSettings': {
            'model': 'model-a', 'effort': 'high', 'approvalPolicy': 'on-request',
            'approvalsReviewer': 'auto_review',
            'sandboxPolicy': {'type': 'workspaceWrite', 'networkAccess': False,
                              'writableRoots': ['/private/workspace']},
            'activePermissionProfile': {'id': 'custom-profile'},
        }}}
        result = summary(state)
        self.assertEqual(result['model'], 'model-a')
        self.assertEqual(result['reasoning'], '高')
        self.assertEqual(result['permission'], '工作区可写 · 联网受限 · 自动审查 · 按需审批')
        self.assertEqual(result['scope'], 'session')
        self.assertNotIn('custom-profile', str(result))
        self.assertNotIn('/private/workspace', str(result))
        self.assertNotIn('options', result)

    def test_never_is_not_full_access_or_auto_approval(self):
        result = summary({'history': {'latestThreadSettings': {
            'sandboxPolicy': {'type': 'readOnly'}, 'approvalPolicy': 'never'}}})
        self.assertEqual(result['permission'], '只读 · 需要批准时拒绝')
        self.assertNotIn('联网', result['permission'])
        self.assertNotIn('model', result)

    def test_unknown_and_partial_settings_do_not_invent_defaults(self):
        for state in ({}, {'history': {}}, {'history': {'latestThreadSettings': {
            'sandboxPolicy': {'type': 'future-policy'},
            'approvalPolicy': {'granular': True}, 'approvalsReviewer': 'future-reviewer',
            'model': {'unexpected': True}, 'effort': 'unknown'}}}):
            self.assertIsNone(summary(state))
        result = summary({'history': {'latestModel': 'model-b', 'latestReasoningEffort': 'low'}})
        self.assertEqual((result['model'], result['reasoning']), ('model-b', '低'))
        self.assertNotIn('permission', result)
        result = summary({'history': {'latestModel': 'model-b', 'latestReasoningEffort': 'low',
                                     'latestThreadSettings': {'model': 'model-c', 'effort': None}}})
        self.assertEqual(result['model'], 'model-c')
        self.assertNotIn('reasoning', result)

    def test_projection_and_native_patches_update_metadata_without_prompt_text(self):
        native = {'latestModel': 'model-old', 'latestReasoningEffort': 'medium',
                  'latestThreadSettings': {'model': 'model-old', 'effort': 'medium',
                                           'cwd': '/private/project', 'developerInstructions': 'private text'},
                  'latestCollaborationMode': {'settings': {'developer_instructions': 'private text'}},
                  'turns': [{'items': [{'type': 'agentMessage', 'content': [{'type': 'text', 'text': 'private text'}]}]}]}
        projected = project(native)
        before = copy.deepcopy(projected)
        patched = patch_history(projected, [
            {'op': 'replace', 'path': ['latestThreadSettings', 'model'], 'value': 'model-new'},
            {'op': 'replace', 'path': ['latestThreadSettings', 'effort'], 'value': 'high'},
            {'op': 'replace', 'path': ['latestModel'], 'value': 'model-new'},
            {'op': 'replace', 'path': ['latestReasoningEffort'], 'value': 'high'},
            {'op': 'replace', 'path': ['latestThreadSettings', 'cwd'], 'value': '/private/other'},
        ])
        self.assertEqual(projected, before)
        self.assertNotIn('private', str(patched))
        self.assertEqual(summary({'history': patched})['model'], 'model-new')
        self.assertEqual(summary({'history': patched})['reasoning'], '高')


if __name__ == '__main__':
    unittest.main()

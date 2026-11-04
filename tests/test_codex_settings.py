import copy
import unittest

from aw.control.codex_questions import patch_history, project
from aw.control.codex_settings import summary


class SettingsTests(unittest.TestCase):
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

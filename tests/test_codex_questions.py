import copy
import json
import unittest
from aw.control.codex_questions import project, patch_history, pending, reply_envelope, question_id
from aw.control.broker import validate_action, ControlError

BASE = ['turnHistory', 'history', 'entitiesByKey', 'tail']
def native(status='inProgress'):
    return {'turnHistory': {'kind': 'canonical', 'history': {'entitiesByKey': {'tail': {
        'turnId': 't1', 'status': status, 'items': [
            {'id': 'u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'private chat'}]},
            {'id': 'call1', 'type': 'agentMessage', 'text': 'private result', 'questions': [
                {'title': 'Color?', 'options': ['Red', 'Blue']}, {'title': 'Why?'}]},
            {'id': 'tool', 'type': 'commandExecution', 'output': 'private output'}]}}}}, 'turns': []}
def patch(state, suffix, value, op='replace'):
    return patch_history(state, [{'op': op, 'path': BASE + suffix, 'value': value}])

class QuestionTests(unittest.TestCase):
    def test_native_ids_and_free_text_envelope_without_private_history(self):
        state = project(native()); self.assertNotIn('private', json.dumps(state))
        req = pending(state)[0]
        self.assertEqual(req['questions'][0]['id'], '["request_user_input_async","call1",0]')
        answers = {q['id']: {'selected': [], 'custom': 'Blue'} for q in req['questions']}
        validate_action({'requests': [req]}, {'kind': 'answer', 'request_id': req['id'], 'revision': req['revision'], 'answers': answers})
        rows = json.loads(reply_envelope(req, answers).split('\n')[1])
        self.assertEqual(rows[0], {'questionItemId': question_id('call1', 0), 'question': 'Color?', 'answer': 'Blue'})

    def test_only_accepted_native_answers_resolve_questions(self):
        state = project(native()); req = pending(state)[0]
        answers = {q['id']: {'selected': [], 'custom': 'Blue'} for q in req['questions']}
        item = {'id': 'a', 'type': 'steeringUserMessage', 'status': 'pending', 'input': [
            {'type': 'text', 'text': reply_envelope(req, answers)}]}
        state = patch(state, ['items', 3], item, 'add')
        self.assertEqual(len(pending(state)), 1)
        state = patch(state, ['items', 3, 'status'], 'accepted')
        self.assertEqual(pending(state), [])

    def test_completed_cancelled_and_ambiguous_turns_not_actionable(self):
        for status in ['completed', 'interrupted', 'failed']:
            self.assertEqual(pending(project(native(status))), [])
        state = native(); entities = state['turnHistory']['history']['entitiesByKey']
        entities['other'] = copy.deepcopy(entities['tail'])
        self.assertEqual(pending(project(state)), [])

    def test_nested_patches_invalidate_stale_action_and_remove_finished_question(self):
        state = project(native()); req = pending(state)[0]
        new = patch(state, ['items', 1, 'questions', 0, 'options', 0], 'Green')
        self.assertNotEqual(req['revision'], pending(new)[0]['revision'])
        with self.assertRaisesRegex(ControlError, 'request_expired'):
            validate_action({'requests': pending(new)}, {'kind': 'answer', 'request_id': req['id'], 'revision': req['revision']})
        self.assertEqual(pending(patch(new, ['status'], 'completed')), [])
        self.assertEqual(pending(patch(new, ['items', 1], None, 'remove')), [])
        self.assertEqual(pending(state)[0]['questions'][0]['options'][0], 'Red')

    def test_irrelevant_output_does_not_copy_state(self):
        state = project(native())
        self.assertIs(patch(state, ['items', 2, 'output'], 'private'), state)

    def test_canonical_wins_over_stale_legacy_and_replacements_work(self):
        state = native('completed')
        state['turns'] = list(native()['turnHistory']['history']['entitiesByKey'].values())
        self.assertEqual(pending(project(state)), [])
        del state['turnHistory']
        self.assertEqual(len(pending(project(state))), 1)
        state = patch(project(native('completed')), [], native()['turnHistory']['history']['entitiesByKey']['tail'])
        self.assertEqual(len(pending(state)), 1)

    def test_partial_answers_preserve_question_indices(self):
        state = native(); turn = state['turnHistory']['history']['entitiesByKey']['tail']
        turn['items'].append({'type': 'userMessage', 'content': [{'type': 'text', 'text':
            '<send_user_message_question_reply>\n'+json.dumps([{'questionItemId': question_id('call1', 0), 'question': 'Color?', 'answer': 'Red'}])+'\n</send_user_message_question_reply>'}]})
        self.assertEqual([q['id'] for q in pending(project(state))[0]['questions']], [question_id('call1', 1)])

    def test_native_null_questions_are_normal_agent_messages(self):
        state = native()
        state['turnHistory']['history']['entitiesByKey']['tail']['items'].append(
            {'type': 'agentMessage', 'id': 'ordinary', 'questions': None})
        self.assertEqual(len(pending(project(state))), 1)

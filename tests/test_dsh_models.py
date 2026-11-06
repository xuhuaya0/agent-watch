import copy
import unittest
from unittest.mock import patch

from aw.control.broker import ControlError, normalize_sessions
from aw.control.dsh import DeepSeek
from aw.control.dsh_settings import SCOPE, valid_model_capability
from test_dsh_control import SID
from test_native_settings import ModelGateway


class Gateway(ModelGateway):
    def __init__(self, path='unused', event=lambda _: None):
        super().__init__(path, event)
        self.apply, self.ack, self.after, self.fail = True, True, None, False
        self.omit_list_model, self.sequence = False, 20
        self.projection_failure = False

    def call(self, endpoint, args):
        if self.omit_list_model and endpoint == 'session/list':
            result = copy.deepcopy(super().call(endpoint, args))
            for row in result['items']:
                if 'projections' not in row:
                    continue
                row['projections']['values'].pop('modelSelection', None)
                row['projections']['asOfSeq'] = self.sequence
            return result
        if self.omit_list_model and endpoint == 'session/projections':
            self.calls.append((endpoint, copy.deepcopy(args)))
            if self.projection_failure:
                raise OSError('native projection unavailable')
            return {'asOfSeq': self.sequence, 'values': copy.deepcopy(self.catalog[0]['projections']['values'])}
        if self.omit_list_model and endpoint == 'session/page':
            self.calls.append((endpoint, copy.deepcopy(args)))
            return {'records': [], 'hasMore': False}
        if endpoint != 'session/selectModel':
            return super().call(endpoint, args)
        self.calls.append((endpoint, copy.deepcopy(args)))
        selected = {key: value for key, value in args['request'].items() if key != 'sessionId'}
        if self.apply:
            self.catalog[0]['projections']['values']['modelSelection']['next'] = selected
            # Match the native endpoint's documented side effect.
            self.models['default'] = copy.deepcopy(selected)
        if self.after:
            self.after(self)
        if self.fail:
            raise OSError('lost acknowledgement')
        return {'selected': selected} if self.ack else {}


class DeepSeekModelTests(unittest.TestCase):
    def provider(self):
        provider = DeepSeek({'connection_file': 'unused', 'scope': 'all'}, Gateway, background=False)
        provider.refresh()
        return provider

    def command(self, provider, label='Default model'):
        cap = provider.snapshot()[0]['model']
        option = next(row for row in cap['options'] if row['label'] == label)
        return {'id': 'model-command', 'session_id': SID, 'tool': 'dsh', 'action': {
            'kind': 'model', 'revision': cap['revision'], 'model': option['value'],
            **({'effort': option['default_effort']} if option['efforts'] else {})}}

    def writes(self, provider):
        return [row for row in provider.gateway.calls if row[0] == 'session/selectModel']

    def test_native_model_effort_catalog_and_explicit_default_scope_roundtrip(self):
        provider = self.provider()
        row = provider.snapshot()[0]
        cap = row['model']
        self.assertEqual(normalize_sessions([row])[('dsh', SID)]['model'], cap)
        self.assertEqual(cap['scope'], SCOPE)
        self.assertEqual((cap['current_label'], cap['effort']), ('Next model', 'high'))
        self.assertEqual(cap['options'][0]['effort_labels'], {'high': 'High'})
        self.assertEqual(cap['options'][0]['provider_label'], 'Example provider')
        self.assertNotIn('lastUsed', cap)
        self.assertEqual(provider.execute(self.command(provider)), 'model_updated')
        self.assertEqual(self.writes(provider), [('session/selectModel', {'request': {
            'sessionId': SID, 'provider': 'example', 'model': 'fallback', 'reasoningEffort': 'medium'}})])
        self.assertEqual(provider.snapshot()[0]['model']['current_label'], 'Default model')
        self.assertFalse(any(name in ('commands/execute', 'session/prompt') for name, _ in provider.gateway.calls))

    def test_native_effort_change_and_model_without_reasoning_do_not_guess_fields(self):
        provider = self.provider()
        option = provider.gateway.models['groups'][0]['models'][0]
        option['reasoning']['efforts'].append({'id': 'adapter-owned-effort', 'name': 'Native custom effort'})
        provider.settings.expires = 0
        provider.refresh()
        cap = provider.snapshot()[0]['model']
        action = {'kind': 'model', 'revision': cap['revision'], 'model': cap['current'], 'effort': 'adapter-owned-effort'}
        self.assertEqual(provider.execute({'id': 'effort-command', 'session_id': SID, 'action': action}), 'model_updated')
        self.assertEqual(self.writes(provider)[0][1]['request']['reasoningEffort'], 'adapter-owned-effort')
        self.assertEqual(provider.execute(self.command(provider, 'Previous model')), 'model_updated')
        self.assertNotIn('reasoningEffort', self.writes(provider)[-1][1]['request'])

    def test_global_default_and_next_are_preserved_as_distinct_native_facts(self):
        provider = self.provider()
        cap = provider.snapshot()[0]['model']
        self.assertEqual(cap['current_label'], 'Next model')
        self.assertEqual(provider.settings.catalog['default']['model'], 'fallback')
        provider.gateway.catalog[0]['projections']['values']['modelSelection']['next'] = None
        provider.refresh()
        self.assertEqual(provider.snapshot()[0]['model']['current_label'], 'Default model')
        self.assertNotEqual(provider.snapshot()[0]['model']['revision'], cap['revision'])

    def test_missing_list_selection_hydrates_only_requested_chat_and_invalidates_by_sequence(self):
        provider = self.provider()
        gateway = provider.gateway
        gateway.omit_list_model = True
        provider.refresh()
        self.assertNotIn('model', provider.snapshot()[0])
        self.assertFalse(any(name == 'session/projections' for name, _ in gateway.calls))
        row = provider.session_snapshot(SID)[0]
        self.assertEqual(row['model']['current_label'], 'Next model')
        self.assertEqual(row['settings']['model'], 'Next model')
        for _ in range(3): provider.refresh()
        provider.session_snapshot(SID)
        self.assertEqual(sum(name == 'session/projections' for name, _ in gateway.calls), 1)
        self.assertEqual(set(provider.settings.projections[SID][2]), {'next', 'lastUsed'})
        gateway.sequence += 1
        gateway.catalog[0]['projections']['values']['modelSelection']['next'] = None
        provider.refresh()
        self.assertEqual(provider.snapshot()[0]['model']['current_label'], 'Default model')
        self.assertEqual(provider.session_snapshot(SID)[0]['model']['current_label'], 'Default model')
        self.assertEqual(sum(name == 'session/projections' for name, _ in gateway.calls), 2)

    def test_lazy_projection_is_fresh_before_write_and_readback_uses_changed_native_selection(self):
        provider = self.provider()
        provider.gateway.omit_list_model = True
        provider.session_snapshot(SID)
        command = self.command(provider)
        self.assertEqual(provider.execute(command), 'model_updated')
        self.assertEqual(provider.snapshot()[0]['model']['current_label'], 'Default model')
        self.assertEqual(len(self.writes(provider)), 1)

    def test_opening_history_alone_publishes_current_model_in_the_next_heartbeat(self):
        provider = self.provider()
        gateway = provider.gateway
        gateway.omit_list_model = True
        provider.refresh()
        self.assertNotIn('model', provider.snapshot()[0])
        self.assertEqual(provider.read_history(SID)['messages'], [])
        # The detail pane's existing history request hydrates the chosen chat;
        # ordinary heartbeats then carry its current menu without a write.
        provider.refresh()
        heartbeat = normalize_sessions(provider.snapshot())[('dsh', SID)]
        self.assertEqual(heartbeat['model']['current_label'], 'Next model')
        self.assertEqual(len(heartbeat['model']['options']), 3)
        self.assertFalse(any(name in ('session/selectModel', 'session/prompt', 'commands/execute')
                             for name, _ in gateway.calls))

    def test_open_idle_detail_keeps_menu_after_expiry_and_tracks_native_selection_changes(self):
        provider = self.provider()
        gateway = provider.gateway
        gateway.omit_list_model = True
        provider.read_history(SID)
        deadline = provider.settings.projections[SID][0]
        gateway.catalog[0]['projections']['values']['modelSelection']['next'] = None
        # No new history read, reply or selection action is made. A normal
        # heartbeat refreshes this already requested chat at cache expiry.
        with patch('aw.control.dsh_settings.time.monotonic', return_value=deadline + 1):
            provider.refresh()
            cap = provider.snapshot()[0]['model']
        self.assertEqual(cap['current_label'], 'Default model')
        self.assertTrue(cap['options'])
        self.assertEqual(len(provider.settings.requested_models), 1)
        self.assertEqual(sum(name == 'session/projections' for name, _ in gateway.calls), 3)

    def test_expired_native_read_failure_revokes_choices_and_recovers_without_reopening(self):
        provider = self.provider()
        gateway = provider.gateway
        gateway.omit_list_model = True
        provider.session_snapshot(SID)
        deadline = provider.settings.projections[SID][0]
        gateway.projection_failure = True
        with patch('aw.control.dsh_settings.time.monotonic', return_value=deadline + 1):
            provider.refresh()
            self.assertNotIn('model', provider.snapshot()[0])
            count = sum(name == 'session/projections' for name, _ in gateway.calls)
            provider.refresh()
            self.assertEqual(sum(name == 'session/projections' for name, _ in gateway.calls), count)
        gateway.projection_failure = False
        with patch('aw.control.dsh_settings.time.monotonic', return_value=deadline + 17):
            provider.refresh()
            self.assertTrue(provider.snapshot()[0]['model']['options'])

    def test_projection_watch_set_is_bounded_and_does_not_discover_unopened_chats(self):
        provider = self.provider()
        gateway = provider.gateway
        gateway.omit_list_model = True
        for index in range(18):
            row = {'sessionId': 'session-example-' + str(index), 'projections': {'values': {}}}
            provider.settings.projected_row(gateway, row, hydrate=True)
        self.assertEqual(len(provider.settings.requested_models), 16)
        self.assertEqual(len(provider.settings.projections), 16)
        count = sum(name == 'session/projections' for name, _ in gateway.calls)
        for sid in ('session-example-0', 'session-unopened'):
            provider.settings.projected_row(gateway, {'sessionId': sid, 'projections': {'values': {}}})
        self.assertEqual(sum(name == 'session/projections' for name, _ in gateway.calls), count)

    def test_stale_catalog_selection_busy_pending_and_archived_reject_before_write(self):
        for change in ('catalog', 'current', 'running', 'pending', 'archived', 'queued'):
            with self.subTest(change=change):
                provider = self.provider()
                command = self.command(provider)
                native = provider.gateway.catalog[0]
                if change == 'catalog':
                    provider.gateway.models['groups'][0]['models'][-1]['name'] = 'Changed native model'
                elif change == 'current':
                    native['projections']['values']['modelSelection']['next'] = None
                elif change == 'running':
                    native['running'] = True
                elif change == 'archived':
                    native['archived'] = True
                elif change == 'queued':
                    native['projections']['values']['inbox']['next-turn'] = ['waiting']
                else:
                    provider.event({'type': 'waterfall', 'eventId': 'question', 'agentId': SID,
                        'event': 'user-questions/request', 'request': {'questions': [{'id': 'q', 'question': 'Choose?'}]}})
                with self.assertRaises(ControlError):
                    provider.execute(command)
                self.assertEqual(self.writes(provider), [])

    def test_native_select_model_supports_cold_sessions_without_sending_a_turn(self):
        provider = self.provider()
        provider.gateway.catalog[0]['agentAvailable'] = False
        provider.refresh()
        self.assertTrue(provider.snapshot()[0]['model']['options'])
        self.assertEqual(provider.execute(self.command(provider)), 'model_updated')
        self.assertFalse(any(name == 'session/prompt' for name, _ in provider.gateway.calls))

    def test_unverified_response_readback_and_disconnect_are_unknown_without_retry(self):
        for case in ('no-apply', 'no-ack', 'disconnect', 'lost-ack', 'archived'):
            with self.subTest(case=case):
                provider = self.provider()
                command = self.command(provider)
                if case == 'no-apply': provider.gateway.apply = False
                elif case == 'no-ack': provider.gateway.ack = False
                elif case == 'disconnect': provider.gateway.after = lambda gateway: gateway.close()
                elif case == 'archived': provider.gateway.after = lambda gateway: gateway.catalog[0].update(archived=True)
                else: provider.gateway.fail = True
                gateway = provider.gateway
                self.assertEqual(provider.execute(command), 'unknown')
                self.assertEqual(sum(name == 'session/selectModel' for name, _ in gateway.calls), 1)

    def test_duplicate_provider_models_and_efforts_fail_closed(self):
        for change in ('provider', 'model', 'effort', 'default'):
            provider = self.provider()
            group = provider.gateway.models['groups'][0]
            if change == 'provider': provider.gateway.models['groups'].append(copy.deepcopy(group))
            elif change == 'model': group['models'].append(copy.deepcopy(group['models'][0]))
            elif change == 'effort': group['models'][0]['reasoning']['efforts'] *= 2
            else: group['models'][0]['reasoning']['defaultEffort'] = 'invented'
            provider.settings.expires = 0
            provider.refresh()
            self.assertNotIn('model', provider.snapshot()[0])

    def test_capability_rejects_misleading_scope_unknown_fields_and_effort_labels(self):
        original = self.provider().snapshot()[0]['model']
        for mutate in (lambda c: c.update(scope='session'), lambda c: c.update(raw={'private': True}),
                lambda c: c['options'][0]['effort_labels'].update(invented='Unknown'),
                lambda c: c['options'][0]['efforts'].append('high')):
            cap = copy.deepcopy(original)
            mutate(cap)
            self.assertFalse(valid_model_capability(cap))


if __name__ == '__main__':
    unittest.main()

"""Synthetic native transcripts and read-only protocol boundary checks."""
import json
from pathlib import Path
import tempfile
import unittest
from aw.control.broker import ControlError
from aw.control.history import AntigravityHistory, PAGE_BYTES
from aw.control.dsh import DeepSeek
from test_dsh_control import FakeGateway, SID as DSH_SID

SID = '11111111-1111-4111-8111-111111111111'


class AntigravityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path = self.root/'brain'/SID/'.system_generated/logs/transcript.jsonl'
        self.path.parent.mkdir(parents=True)
        self.reader = AntigravityHistory(self.root, lambda: [SID])

    def write(self, rows):
        self.path.write_text('\n'.join(map(json.dumps, rows))+'\n', encoding='utf-8')

    def test_pagination_preserves_unicode_and_append_without_duplicates(self):
        self.write([{'type':'USER_INPUT', 'content':f'<USER_REQUEST>消息 {i}</USER_REQUEST>'} for i in range(65)])
        page = self.reader.read(SID)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type':'PLANNER_RESPONSE','content':'new'})+'\n')
        items = page['messages']
        while page['next_cursor']:
            page = self.reader.read(SID, page['next_cursor'])
            items = page['messages'] + items
        self.assertEqual([m['text'] for m in items], [f'消息 {i}' for i in range(65)])
        self.assertEqual(len({m['id'] for m in items}), 65)
        self.assertEqual(page['source'], 'antigravity_transcript')

    def test_internal_records_hidden_and_tool_step_keeps_explanation(self):
        self.write([{'type':'SYSTEM_MESSAGE','content':'internal setup'},
                    {'type':'CHECKPOINT','content':'internal reasoning'},
                    {'type':'PLANNER_RESPONSE','content':'Running a tool',
                     'tool_calls':[{'name':'test','args':{'text':'hello'}}]}])
        items = self.reader.read(SID)['messages']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['role'], 'tool')
        self.assertIn('Running a tool', items[0]['text'])
        self.assertIn('hello', items[0]['text'])

    def test_antigravity_tool_sequence_does_not_hide_ordinary_or_final_replies(self):
        self.write([
            {'type':'USER_INPUT','content':'Inspect the sample'},
            {'type':'PLANNER_RESPONSE','content':'Inspecting now',
             'thinking':'internal reasoning',
             'tool_calls':[{'name':'read_file','args':{'path':'sample.txt'}}]},
            {'type':'GENERIC','content':'Sample contents'},
            {'type':'PLANNER_RESPONSE','content':'The sample is ready','tool_calls':[]},
            {'type':'USER_INPUT','content':'Explain the result'},
            {'type':'PLANNER_RESPONSE','content':'An ordinary answer with no tool step'}])
        items = self.reader.read(SID)['messages']
        self.assertEqual([m['role'] for m in items],
                         ['user','tool','tool','assistant','user','assistant'])
        self.assertFalse(any(m.get('phase') == 'commentary' for m in items))
        self.assertEqual(items[3]['text'], 'The sample is ready')
        self.assertEqual(items[-1]['text'], 'An ordinary answer with no tool step')
        self.assertNotIn('internal reasoning', json.dumps(items))

    def test_scope_revocation_during_read_and_invalid_id_fail_closed(self):
        self.write([{'type':'USER_INPUT','content':'hello'}])
        calls = []
        def scope():
            calls.append(True)
            return [SID] if len(calls) == 1 else []
        self.reader.scope = scope
        with self.assertRaisesRegex(ControlError, 'native_session_unavailable'):
            self.reader.read(SID)
        with self.assertRaisesRegex(ControlError, 'native_session_unavailable'):
            self.reader.read('../outside')

    def test_symlink_cannot_read_another_session_or_external_brain(self):
        self.write([{'type':'USER_INPUT','content':'hello'}])
        external = self.root/'other.jsonl'
        external.write_text(self.path.read_text())
        self.path.unlink()
        try:
            self.path.symlink_to(external)
        except OSError:
            self.skipTest('symlinks not available to this Windows account')
        with self.assertRaisesRegex(ControlError, 'history_path_unavailable'):
            self.reader.read(SID)

    def test_long_messages_are_bounded_and_flagged(self):
        self.write([{'type':'PLANNER_RESPONSE','content':'大'*30000} for _ in range(8)])
        page = self.reader.read(SID)
        self.assertTrue(page['next_cursor'])
        self.assertLess(len(json.dumps(page, ensure_ascii=False).encode()), PAGE_BYTES+1000)
        self.assertTrue(all(m['truncated'] and len(m['text'].encode()) <= 48000 for m in page['messages']))


class HistoryGateway(FakeGateway):
    def __init__(self, path, event):
        super().__init__(path, event)
        self.events = [{'seq':i,'time':1000+i,'type':'user/message',
                        'data':{'content':[{'type':'text','text':f'Message {i}'}]}} for i in range(80)]
        self.on_page = lambda: None

    def call(self, endpoint, args):
        if endpoint == 'session/projections':
            self.calls.append((endpoint,args))
            return {'asOfSeq':len(self.events)-1}
        if endpoint == 'session/page':
            self.calls.append((endpoint,args))
            request = args['request']
            end = min(request['throughSeq']+1, request['beforeSeq'])
            start = max(0, end-request['maxMessages'])
            self.on_page()
            return {'records':[{'type':'event','event':e} for e in self.events[start:end]], 'hasMore':start>0}
        return super().call(endpoint, args)


class DeepSeekHistoryTests(unittest.TestCase):
    def provider(self):
        provider = DeepSeek({'connection_file':'unused','scope':'all'}, HistoryGateway, background=False)
        provider.refresh()
        self.addCleanup(provider.close)
        return provider

    def test_cold_read_paginates_snapshot_without_starting_engine(self):
        provider = self.provider()
        page = provider.read_history(DSH_SID)
        self.assertTrue(provider.snapshot()[0]['history'])
        provider.gateway.events.append({'seq':80,'type':'user/message','data':{'content':'new'}})
        items = page['messages']
        while page['next_cursor']:
            page = provider.read_history(DSH_SID, page['next_cursor'])
            items = page['messages'] + items
        self.assertEqual([m['text'] for m in items], [f'Message {i}' for i in range(80)])
        self.assertEqual(len({m['id'] for m in items}), 80)
        self.assertEqual({method for method, _ in provider.gateway.calls}, {'session/list','session/page','session/projections'})

    def test_invalid_cross_session_and_shrunk_history_cursors_rejected(self):
        provider = self.provider()
        cursor = provider.read_history(DSH_SID)['next_cursor']
        for invalid in ('../private', cursor.replace(cursor.split(':')[0], 'other'), 'x:-2:0'):
            with self.assertRaisesRegex(ControlError, 'history_cursor_expired'):
                provider.read_history(DSH_SID, invalid)
        provider.gateway.events = provider.gateway.events[:3]
        with self.assertRaisesRegex(ControlError, 'history_cursor_expired'):
            provider.read_history(DSH_SID, cursor)

    def test_native_archive_during_page_revokes_result(self):
        provider = self.provider()
        provider.gateway.on_page = lambda: provider.gateway.catalog[0].update(archived=True)
        with self.assertRaisesRegex(ControlError, 'native_session_unavailable'):
            provider.read_history(DSH_SID)
        self.assertEqual(provider.snapshot(), [])

    def test_size_trim_keeps_all_messages_across_pages(self):
        provider = self.provider()
        for event in provider.gateway.events:
            event['data']['content'] = '文'*20000
        items, cursor = [], None
        while True:
            page = provider.read_history(DSH_SID, cursor)
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode()), PAGE_BYTES+1000)
            items = page['messages'] + items
            cursor = page['next_cursor']
            if not cursor:
                break
        self.assertEqual([m['id'] for m in items], list(map(str, range(80))))
        self.assertTrue(all(m['truncated'] for m in items))

    def test_internal_and_reasoning_parts_never_rendered(self):
        provider = self.provider()
        provider.gateway.events = [
            {'seq':0,'type':'user/message','data':{'source':{'kind':'system'},'content':'internal'}},
            {'seq':1,'type':'assistant/message','data':{'message':{'content':[
                {'type':'reasoning','text':'private'}, {'type':'text','text':'answer'}]}}},
            {'seq':2,'type':'request/context','data':{'content':'private'}},
            {'seq':3,'type':'assistant/message','surfaceOp':{'op':'replace','startSeq':0,'endSeq':2},
             'data':{'message':{'content':'internal compaction'}}}]
        items = provider.read_history(DSH_SID)['messages']
        self.assertEqual([m['text'] for m in items], ['answer'])

    def test_deepseek_tool_bearing_text_is_progress_but_final_text_remains_visible(self):
        provider = self.provider()
        provider.gateway.events = [
            {'seq':0,'type':'user/message','data':{'content':'Inspect the sample'}},
            {'seq':1,'type':'assistant/message','surfaceOp':'append',
             'data':{'message':{'content':[
                 {'type':'reasoning','text':'internal reasoning'},
                 {'type':'text','text':'Inspecting the sample now'},
                 {'type':'tool-call','toolName':'read_file','toolCallId':'call-1',
                  'input':{'path':'sample.txt'}}]}}},
            {'seq':2,'type':'tool/call','data':{'callId':'call-1','name':'read_file',
                                              'arguments':{'path':'sample.txt'}}},
            {'seq':3,'type':'tool/result','data':{'message':{'toolCallId':'call-1',
                                                          'content':'Sample contents'}}},
            {'seq':4,'type':'assistant/message','data':{'message':{'content':[
                {'type':'text','text':'The sample is ready'}]}}}]
        items = provider.read_history(DSH_SID)['messages']
        self.assertEqual([m['role'] for m in items], ['user','assistant','tool','tool','assistant'])
        self.assertEqual(items[1]['phase'], 'commentary')
        self.assertEqual(items[1]['text'], 'Inspecting the sample now')
        self.assertNotIn('phase', items[-1])
        self.assertEqual(items[-1]['text'], 'The sample is ready')
        self.assertNotIn('internal reasoning', json.dumps(items))

    def test_deepseek_phase_uses_blocks_not_words_and_omits_tool_only_duplicate(self):
        from aw.control.dsh_history import message
        ordinary = message({'seq':1,'type':'assistant/message','data':{'message':{
            'content':'This answer explains a tool-call and how it works.'}}})
        self.assertNotIn('phase', ordinary)
        self.assertIsNone(message({'seq':2,'type':'assistant/message','data':{'message':{
            'content':[{'type':'reasoning','text':'internal'},
                       {'type':'tool-call','toolName':'read_file','input':{}}]}}}))

    def test_deepseek_phase_is_stable_at_a_native_page_boundary(self):
        provider = self.provider()
        provider.gateway.events[64] = {'seq':64,'type':'assistant/message','data':{'message':{
            'content':[{'type':'text','text':'Inspecting now'}, {'type':'tool-call','toolName':'test'}]}}}
        provider.gateway.events[65] = {'seq':65,'type':'tool/call','data':{'name':'test','arguments':'sample'}}
        page = provider.read_history(DSH_SID)
        self.assertEqual(page['messages'][0]['role'], 'tool')
        older = provider.read_history(DSH_SID, page['next_cursor'])
        self.assertEqual(older['messages'][-1]['id'], '64')
        self.assertEqual(older['messages'][-1]['phase'], 'commentary')

    def test_invalid_native_sequence_rejected(self):
        provider = self.provider()
        provider.gateway.events[-1]['seq'] = 1000
        with self.assertRaisesRegex(ControlError, 'history_unavailable'):
            provider.read_history(DSH_SID)

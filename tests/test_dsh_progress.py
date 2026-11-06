"""Readable progress keeps its native phase through collection and history."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from aw.adapters import dsh
from aw.config import DEFAULTS


def assistant(content, **extra):
    return {'type':'assistant/message', 'data':{'message':{'content':content}}, **extra}


class DeepSeekProgressTests(unittest.TestCase):
    def test_tool_step_text_and_final_answer_have_distinct_phases(self):
        state = dsh.new_state()
        dsh.reduce_event(state, assistant([
            {'type':'text','text':'Inspecting the sample'},
            {'type':'tool-call','toolName':'read_file','input':{'path':'sample.txt'}}]))
        self.assertEqual((state['last_assistant'], state['last_assistant_phase']),
                         ('Inspecting the sample', 'commentary'))
        dsh.reduce_event(state, assistant([{'type':'text','text':'The sample is ready'}]))
        self.assertEqual((state['last_assistant'], state['last_assistant_phase']),
                         ('The sample is ready', ''))
        dsh.reduce_event(state, {'type':'turn/start','data':{'turn':2}})
        self.assertEqual((state['last_assistant'], state['last_assistant_phase']), ('', ''))

    def test_replacement_and_reasoning_cannot_become_the_captured_reply(self):
        state = dsh.new_state()
        dsh.reduce_event(state, assistant('Visible answer'))
        dsh.reduce_event(state, assistant('Internal summary', surfaceOp={'op':'replace','startSeq':0,'endSeq':2}))
        dsh.reduce_event(state, assistant([{'type':'reasoning','text':'Internal reasoning'}]))
        self.assertEqual(state['last_assistant'], 'Visible answer')
        self.assertEqual(state['last_assistant_phase'], '')

    def test_collector_exposes_phase_for_captured_progress_and_clears_it_on_final(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root/'sessions'/'project'/'session-test'/'session.v4.jsonl'
        path.parent.mkdir(parents=True)
        rows = [{'type':'session','id':'session-test','cwd':'/project'},
                {'type':'turn/start','time':1000,'data':{'turn':1}},
                assistant([{'type':'text','text':'Inspecting the sample'},
                           {'type':'tool-call','toolName':'read_file','input':{}}], time=2000)]
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
        cfg = copy.deepcopy(DEFAULTS)
        cfg['adapters']['dsh']['root'] = str(root)
        dsh._LOGS.clear()
        self.addCleanup(dsh._LOGS.clear)
        turn = dsh.scan(cfg, 3)[0].extra['latest_turn']
        self.assertEqual(turn['assistant'], 'Inspecting the sample')
        self.assertEqual(turn['assistant_phase'], 'commentary')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(assistant('The sample is ready', time=3000))+'\n')
        turn = dsh.scan(cfg, 4)[0].extra['latest_turn']
        self.assertEqual(turn['assistant'], 'The sample is ready')
        self.assertEqual(turn['assistant_phase'], '')

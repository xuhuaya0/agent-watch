import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from aw.control.history import CodexHistory, reverse_lines
from aw.control.history_queue import HistoryReads
from aw.control.broker import Broker, ControlError
from aw.control.codex_ipc import CodexDesktop
from test_codex_ipc import SID, FakeDesktop


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path=self.root/'sessions/rollout-test.jsonl';self.path.parent.mkdir()
        self.path.write_text('')
        self.db=sqlite3.connect(self.root/'state_5.sqlite');self.addCleanup(self.db.close)
        self.db.execute('create table threads(id,rollout_path,archived)')
        self.db.execute('insert into threads values(?,?,0)',(SID,str(self.path)));self.db.commit()
        self.reader=CodexHistory(self.root,lambda:[SID])

    def write(self, n=80):
        rows=[{'type':'response_item','payload':{'type':'message','role':'user' if i%2 else 'assistant','content':[{'type':'text','text':f'正文 {i}'}]}} for i in range(n)]
        self.path.write_text('\n'.join(map(json.dumps,rows))+'\n')

    def test_reverse_offsets_match_utf8_lines_and_long_records(self):
        lines=[b'abc', '你好'.encode(), b'x'*150000]
        raw=b'\n'.join(lines)+b'\n'
        self.assertEqual(list(reverse_lines(io.BytesIO(raw),len(raw))),[(11,lines[2]),(4,lines[1]),(0,lines[0])])

    def test_pagination_has_no_gaps_or_duplicates_and_survives_append(self):
        self.write();first=self.reader.read(SID)
        with self.path.open('a') as f:f.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'user','content':'new'}})+'\n')
        messages=first['messages'];cursor=first['next_cursor']
        while cursor:
            page=self.reader.read(SID,cursor);messages=page['messages']+messages;cursor=page['next_cursor']
        self.assertEqual([m['text'] for m in messages],[f'正文 {i}' for i in range(80)])
        self.assertEqual(len({m['id'] for m in messages}),80)
        self.assertEqual(self.reader.read(SID)['messages'][-1]['text'],'new')

    def test_compressed_history_uses_native_index_and_keeps_original_untouched(self):
        import zstandard
        self.write();raw=self.path.read_bytes();packed=self.path.with_suffix('.jsonl.zst')
        packed.write_bytes(zstandard.ZstdCompressor().compress(raw));self.path.unlink()
        before=packed.read_bytes();page=self.reader.read(SID)
        self.assertEqual(page['messages'][-1]['text'],'正文 79')
        older=self.reader.read(SID,page['next_cursor'])
        self.assertEqual(older['messages'][-1]['text'],'正文 49')
        self.assertEqual(packed.read_bytes(),before);self.assertFalse(self.path.exists())

    def test_archive_revocation_and_external_path_are_rejected(self):
        self.write();self.db.execute('update threads set archived=1');self.db.commit()
        with self.assertRaisesRegex(ControlError,'archived'):self.reader.read(SID)
        self.db.execute('update threads set archived=0,rollout_path=?',('/etc/passwd',));self.db.commit()
        with self.assertRaisesRegex(ControlError,'path_unavailable'):self.reader.read(SID)

    def test_no_instructions_reasoning_or_duplicate_events(self):
        rows=[{'type':'response_item','payload':{'type':'message','role':'developer','content':'secret instructions'}},
              {'type':'response_item','payload':{'type':'reasoning','summary':'private'}},
              {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'analysis','content':'hidden'}},
              {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':'hidden channel'}},
              {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'commentary','channel':'analysis','content':'hidden mixed phase'}},
              {'type':'response_item','payload':{'type':'message','role':'user','content':'<environment_context>noise</environment_context>Actual'}},
              {'type':'event_msg','payload':{'type':'user_message','message':'Actual'}}]
        self.path.write_text('\n'.join(map(json.dumps,rows))+'\n')
        self.assertEqual([m['text'] for m in self.reader.read(SID)['messages']],['Actual'])

    def test_commentary_phase_is_preserved_only_for_assistant_messages(self):
        payloads = [
            {'type': 'message', 'role': 'assistant', 'phase': 'commentary', 'content': 'Progress update'},
            {'type': 'message', 'role': 'user', 'phase': 'commentary', 'content': 'User message'},
            {'type': 'function_call_output', 'phase': 'commentary', 'output': 'Tool result'},
            {'type': 'message', 'role': 'assistant', 'phase': 'final', 'content': 'Final result'},
        ]
        rows = [{'type': 'response_item', 'payload': payload} for payload in payloads]
        self.path.write_text('\n'.join(map(json.dumps, rows)) + '\n')
        messages = self.reader.read(SID)['messages']
        self.assertEqual([m['text'] for m in messages],
                         ['Progress update', 'User message', 'Tool result', 'Final result'])
        self.assertEqual([m['role'] for m in messages], ['assistant', 'user', 'tool', 'assistant'])
        self.assertEqual(messages[0]['phase'], 'commentary')
        self.assertTrue(all('phase' not in m for m in messages[1:]))

    def test_bad_or_replaced_cursor_fails(self):
        self.write();page=self.reader.read(SID);self.path.unlink();self.write(1)
        with self.assertRaisesRegex(ControlError,'cursor_expired'):self.reader.read(SID,page['next_cursor'])
        with self.assertRaisesRegex(ControlError,'cursor_expired'):self.reader.read(SID,'../../../private')


class ReadQueueTests(unittest.TestCase):
    def test_reads_are_device_bound_revocable_and_ephemeral(self):
        now=[1];q=HistoryReads(lambda:now[0]);caps={('codex',SID):{'history':True}}
        self.assertFalse(q.request('d','codex',SID,None,'w')['ready'])
        reads,_=q.exchange('d','w',caps,[]);item={'id':reads[0]['id'],'result':{'messages':[],'next_cursor':None}}
        q.exchange('other','w',caps,[item]);self.assertFalse(q.request('d','codex',SID,None,'w')['ready'])
        q.exchange('d','w',{},[item]);self.assertEqual(q.request('d','codex',SID,None,'w')['error'],'native_session_unavailable')
        now[0]=50;q.prune();self.assertFalse(q.jobs)

    def test_history_does_not_block_reply_or_persist_text(self):
        import uuid
        root=self.enterContext(tempfile.TemporaryDirectory());b=Broker(root)
        caps=[{'tool':'codex','session_id':SID,'reply':True,'history':True,'requests':[]}]
        b.exchange('d','w',caps,[],[]);b.history('d','codex',SID)
        cmd=b.submit('d','codex',SID,str(uuid.uuid4()),{'kind':'reply','text':'hello'})
        exchanged=b.exchange('d','w',caps,[],[])
        self.assertEqual(exchanged['command']['id'],cmd['id']);self.assertEqual(len(exchanged['reads']),1)
        data={'messages':[{'id':'0','role':'user','text':'PRIVATE-HISTORY'}],'next_cursor':None}
        b.exchange('d','w',caps,[],[{'id':exchanged['reads'][0]['id'],'result':data}])
        self.assertEqual(b.history('d','codex',SID)['messages'][0]['text'],'PRIVATE-HISTORY')
        self.assertNotIn(b'PRIVATE-HISTORY',Path(b.path).read_bytes())


class ColdDesktop(FakeDesktop):
    cold=True
    cold_error=ControlError
    def call(self,method,params,**kwargs):
        if method=='thread-owner-discovery' and self.cold:raise self.cold_error('codex_owner_unavailable')
        return super().call(method,params,**kwargs)


class ColdResumeTests(unittest.TestCase):
    def test_cold_history_read_never_queues_and_reply_queues_once(self):
        class Reader:
            def path(self,sid):return sid
            def read(self,sid,cursor):return {'messages':[],'next_cursor':None}
        for error in (ControlError, TimeoutError):
            with self.subTest(error=error):
                class SilentDesktop(ColdDesktop):
                    cold_error=error
                queued=[]
                def send(sid,text):queued.append((sid,text));return 'queued_native'
                p=CodexDesktop('/unused',lambda:[SID],SilentDesktop,False,Reader(),send)
                self.addCleanup(p.close);p.refresh()
                self.assertTrue(p.snapshot()[0]['reply']);p.read_history(SID,None);self.assertEqual(queued,[])
                self.assertEqual(p.execute({'id':'message','session_id':SID,'action':{'kind':'reply','text':'Hello'}}),'queued_native')
                self.assertEqual(queued,[(SID,'Hello')]);self.assertFalse(p.rpc.calls)

    def test_loaded_pending_approval_never_queues_or_sends_message(self):
        class Reader:
            def path(self,sid):return sid
        queued=[]
        p=CodexDesktop('/unused',lambda:[SID],ColdDesktop,False,Reader(),lambda sid,text:queued.append(sid))
        self.addCleanup(p.close);p.refresh();p.rpc.cold=False
        p.rpc.requests=[{'id':'r','method':'unsupported','params':{'threadId':SID}}]
        with self.assertRaisesRegex(ControlError,'reply_unavailable'):
            p.execute({'id':'message','session_id':SID,'action':{'kind':'reply','text':'Hello'}})
        self.assertEqual(queued,[])
        self.assertFalse(any(c[0]=='thread-follower-start-turn' for c in p.rpc.calls))

    def test_native_queue_uses_exact_arguments_and_only_wakes_after_acceptance(self):
        from aw.control.codex_queue import NativeQueue
        from types import SimpleNamespace
        import subprocess
        calls=[]
        def run(args,**kwargs):
            calls.append((args,kwargs));return SimpleNamespace(returncode=0,stdout='Queued message '+SID+' for thread '+SID+'.')
        queue=NativeQueue('/native/codex','/native/home',run)
        message='--dangerously-bypass-approvals-and-sandbox $(echo NO)'
        self.assertEqual(queue(SID,message),'queued_native')
        self.assertEqual(calls[0][0],['/native/codex','queue','--thread',SID,'--message='+message])
        self.assertEqual(calls[0][1]['env']['CODEX_HOME'],'/native/home')
        self.assertEqual(calls[1][0],['/usr/bin/open','-g','codex://threads/wake/'+SID])
        calls.clear()
        def timeout(args,**kwargs):calls.append(args);raise subprocess.TimeoutExpired(args,20)
        with self.assertRaises(subprocess.TimeoutExpired):NativeQueue('/native/codex','/native/home',timeout)(SID,'hello')
        self.assertEqual(len(calls),1)

    def test_queue_acceptance_survives_wake_failure_without_requeue(self):
        from aw.control.codex_queue import NativeQueue
        from types import SimpleNamespace
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if len(calls)==2:raise OSError('wake failed')
            return SimpleNamespace(returncode=0,stdout='Queued message '+SID+' for thread '+SID+'.')
        self.assertEqual(NativeQueue('/native/codex','/native/home',run)(SID,'hello'),'queued_unwoken')
        self.assertEqual(len(calls),2)

class ArchiveVisibilityTests(unittest.TestCase):
    def test_old_remote_archived_snapshot_is_hidden_in_tasks_detail_counts_and_controls(self):
        from hub_fixture import isolated_hub
        from aw.model import Task
        from urllib.parse import quote
        with isolated_hub() as hub:
            c=hub.app.test_client();c.post('/api/auth/setup',json={'password':'test-password'})
            task=Task(tool='codex',session_id='log',title='Archived',state='done',state_since=1,last_event_at=1,
                      extra={'thread_id':SID,'native_archived':True})
            hub.STORE.ingest('remote',[task.to_dict()],machine_id='device')
            self.assertEqual(c.get('/api/tasks?all=1').get_json()['tasks'],[])
            self.assertEqual(c.get('/api/tasks?all=1').get_json()['counts']['total'],0)
            self.assertEqual(c.get('/api/control/tasks/'+quote('id:device/codex/'+SID,safe='')).status_code,404)
            self.assertEqual(c.get('/api/history/tasks/'+quote('id:device/codex/'+SID,safe='')).status_code,404)
            task.extra['native_archived']=False
            hub.STORE.ingest('remote',[task.to_dict()],machine_id='device')
            self.assertEqual(len(c.get('/api/tasks?all=1').get_json()['tasks']),1)

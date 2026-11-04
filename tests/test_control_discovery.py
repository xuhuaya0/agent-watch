import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from aw.control.sessions import SessionScope
from aw.control.antigravity import Antigravity
from aw.control.broker import ControlError


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config=self.root/'control.json'
        self.scope=SessionScope(self.config)
        self.sid=str(uuid.uuid4())

    def write(self, value):
        self.config.write_text(json.dumps({'antigravity':value}))

    def test_existing_selected_scope_and_live_revocation(self):
        self.write({'session_ids':[self.sid,self.sid]})
        self.assertEqual(self.scope(),[self.sid])
        self.write({'session_ids':[]})
        self.assertEqual(self.scope(),[])
        for value in [None, [], {'scope':'unknown'}, {'session_ids':['../escape']}, {'session_ids':'all'}]:
            self.write(value);self.assertEqual(self.scope(),[])
        self.config.write_text('{')
        self.assertEqual(self.scope(),[])

    def test_all_mode_discovers_new_sessions_without_granting_unrelated_paths(self):
        self.write({'scope':'all','data_root':str(self.root)})
        log=self.root/'brain'/self.sid/'.system_generated/logs/transcript.jsonl'
        log.parent.mkdir(parents=True);log.write_text('{}')
        (self.root/'brain'/'not-a-session').mkdir()
        (self.root/'brain'/str(uuid.uuid4())).mkdir()  # no actual transcript
        self.assertEqual(self.scope(),[self.sid])
        other=str(uuid.uuid4());second=self.root/'brain'/other/'.system_generated/logs/transcript.jsonl'
        second.parent.mkdir(parents=True);second.write_text('{}')
        self.assertEqual(set(self.scope()),{self.sid,other})
        try:(self.root/'brain'/str(uuid.uuid4())).symlink_to(log.parents[2],target_is_directory=True)
        except OSError:pass  # Windows without symlink privilege
        self.assertEqual(set(self.scope()),{self.sid,other})


class DiscoveryTests(unittest.TestCase):
    def test_slow_native_session_does_not_block_other_sessions_or_snapshots(self):
        slow,fast=str(uuid.uuid4()),str(uuid.uuid4())
        release=threading.Event();seen=threading.Event();ids=[slow,fast]
        def run(argv,**kwargs):
            if argv[-1]==slow:release.wait(3)
            if argv[-1]==fast:seen.set()
            return SimpleNamespace(returncode=0,stdout='{}')
        p=Antigravity('/tmp/native',lambda:list(ids),run=run,
                      environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:1'},background=True)
        try:
            started=time.monotonic();p.snapshot()
            self.assertLess(time.monotonic()-started,.5)
            self.assertTrue(seen.wait(2))
            deadline=time.monotonic()+2
            while time.monotonic()<deadline and not p.snapshot():time.sleep(.01)
            self.assertEqual([r['session_id'] for r in p.snapshot()],[fast])
            ids.remove(fast)
            self.assertEqual(p.snapshot(),[])
            with self.assertRaisesRegex(ControlError,'native_session_unavailable'):
                p.execute({'session_id':fast,'action':{'kind':'reply','text':'hello'}})
        finally:release.set();p.close()

    def test_pre_dispatch_probe_is_fresh_and_only_checks_target(self):
        ids=[str(uuid.uuid4()),str(uuid.uuid4())];calls=[]
        def run(argv,**kwargs):
            calls.append(argv[-1]);return SimpleNamespace(returncode=0,stdout='{}')
        p=Antigravity('/tmp/native',lambda:ids,run=run,environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:1'})
        self.assertEqual(p.session_snapshot(ids[1])[0]['session_id'],ids[1])
        self.assertEqual(calls,[ids[1]])

    def test_unexpected_session_error_does_not_stop_discovering_new_chats(self):
        broken,first,later=(str(uuid.uuid4()) for _ in range(3))
        ids=[broken,first]
        seen=threading.Event()
        class Interactions:
            def inspect(self,sid,context=None):
                if sid==broken:raise AttributeError('new native payload shape')
                if sid==later:seen.set()
                return {'tool':'antigravity','session_id':sid,'reply':True,'requests':[]},{}
        provider=Antigravity('/tmp/native',lambda:list(ids),interactions=Interactions(),
            environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:1'},background=True)
        try:
            provider.snapshot()
            deadline=time.monotonic()+2
            while time.monotonic()<deadline and not provider.snapshot():time.sleep(.01)
            self.assertEqual([row['session_id'] for row in provider.snapshot()],[first])
            ids.append(later)
            self.assertTrue(seen.wait(2))
            self.assertTrue(provider._thread.is_alive())
        finally:provider.close()

    def test_malformed_optional_agentapi_metadata_does_not_remove_text_reply(self):
        sid=str(uuid.uuid4())
        for metadata in (None,[],['unexpected'],42,True,'unexpected'):
            with self.subTest(metadata=metadata):
                provider=Antigravity('/tmp/native',lambda:[sid],
                    run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'metadata':metadata})),
                    environ={'ANTIGRAVITY_LS_ADDRESS':'localhost:1'})
                row=provider.session_snapshot(sid)[0]
                self.assertTrue(row['reply'])
                self.assertNotIn('policy',row)

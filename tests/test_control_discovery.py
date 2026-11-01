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

"""Durable wait, explicit insertion, and no-write deferral across dispatch races."""
import copy
from pathlib import Path
import tempfile
import unittest
import uuid

from aw.control.broker import Broker, ControlError, normalize_sessions, validate_action
from aw.control.delivery import ReplyDeferred, codex_delivery, guard_reply
from aw.control.worker import Worker
from test_codex_ipc import FakeDesktop, SID
from aw.control.codex_ipc import CodexDesktop


def capability(state='running', steer=True, sid='chat'):
    delivery = {'state':state, 'steer':steer and state == 'running'}
    if delivery['steer']:
        delivery['revision'] = 'native-turn-a'
    return {'tool':'codex', 'session_id':sid, 'reply':state != 'waiting', 'requests':[], 'delivery':delivery}


class ReplyQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.now = 1000
        self.b = Broker(self.tmp, lambda:self.now)
        self.b.provision('device')
        self.cap = capability()
        self.exchange()

    def exchange(self, receipts=(), cap=None):
        return self.b.exchange('device', 'worker', [cap or self.cap], list(receipts))

    def send(self, text='A message', command_id=None):
        result = self.b.submit('device', 'codex', 'chat', command_id or str(uuid.uuid4()),
                               {'kind':'reply', 'text':text, 'mode':'after-turn'})
        self.now += .01
        return result

    def state(self, command):
        return next(c for c in self.b.status('device', 'codex', 'chat')['commands'] if c['id'] == command['id'])

    def test_running_default_waits_and_survives_restart(self):
        queued = self.send()
        self.assertEqual(queued['state'], 'waiting')
        self.assertEqual(self.state(queued)['text'], 'A message')
        self.assertIsNone(self.exchange()['command'])
        self.now += 500
        self.b = Broker(self.tmp, lambda:self.now)
        self.assertIsNone(self.exchange()['command'])
        self.assertEqual(self.state(queued)['state'], 'waiting')
        self.cap = capability('idle')
        self.assertEqual(self.exchange()['command']['id'], queued['id'])
        self.assertIsNone(self.exchange()['command'])

    def test_fifo_releases_only_one_until_receipt_and_fresh_idle(self):
        first, second = self.send('first'), self.send('second')
        self.cap = capability('idle')
        self.assertEqual(self.exchange()['command']['id'], first['id'])
        self.assertIsNone(self.exchange()['command'])  # even another idle heartbeat
        self.cap = capability('running')
        self.assertIsNone(self.exchange([{'id':first['id'],'state':'delivered'}])['command'])
        self.assertEqual(self.state(second)['state'], 'waiting')
        self.cap = capability('idle')
        self.assertEqual(self.exchange()['command']['id'], second['id'])

    def test_wait_is_not_a_global_operation_lock(self):
        first = self.send()
        self.assertEqual(self.send('another')['state'], 'waiting')
        self.assertEqual(self.state(first)['state'], 'waiting')
        # Resolving an offered request remains possible with pending replies.
        self.cap = capability('waiting')
        self.cap['requests'] = [{'id':'approval','revision':'one','kind':'approval','prompt':'Run?', 'choices':['approve','reject']}]
        self.exchange()
        approved = self.b.submit('device','codex','chat',str(uuid.uuid4()),
            {'kind':'approval','request_id':'approval','revision':'one','decision':'reject'})
        self.assertEqual(self.exchange()['command']['id'], approved['id'])

    def test_promote_same_id_once_and_reject_stale_turn(self):
        queued = self.send()
        with self.assertRaisesRegex(ControlError,'reply_turn_changed'):
            self.b.manage_waiting('device','codex','chat',queued['id'],'steer','old-turn')
        result = self.b.manage_waiting('device','codex','chat',queued['id'],'steer','native-turn-a')
        self.assertEqual(result['id'],queued['id'])
        self.assertEqual(self.b.manage_waiting('device','codex','chat',queued['id'],'steer','native-turn-a'),result)
        sent = self.exchange()['command']
        self.assertEqual(sent['id'],queued['id'])
        self.assertEqual(sent['action']['mode'],'steer')
        self.assertIsNone(self.exchange()['command'])
        with self.assertRaisesRegex(ControlError,'reply_queue_changed'):
            self.b.manage_waiting('device','codex','chat',queued['id'],'cancel')

    def test_cancel_is_idempotent_and_never_dispatches(self):
        queued = self.send()
        one = self.b.manage_waiting('device','codex','chat',queued['id'],'cancel')
        self.assertEqual(self.b.manage_waiting('device','codex','chat',queued['id'],'cancel'),one)
        self.cap = capability('idle')
        self.assertIsNone(self.exchange()['command'])
        self.assertNotIn('text',self.state(queued))
        with self.assertRaisesRegex(ControlError,'reply_queue_changed'):
            self.b.manage_waiting('another','codex','chat',queued['id'],'steer','native-turn-a')

    def test_claim_and_cancel_are_mutually_exclusive(self):
        queued = self.send()
        self.cap = capability('idle')
        self.assertEqual(self.exchange()['command']['id'],queued['id'])
        with self.assertRaisesRegex(ControlError,'reply_queue_changed'):
            self.b.manage_waiting('device','codex','chat',queued['id'],'cancel')
        self.assertEqual(self.state(queued)['state'],'dispatched')

    def test_queue_mutation_rejects_invalid_body_types_before_database_binding(self):
        for command_id in ([], {}, None, 7):
            with self.assertRaisesRegex(ControlError,'invalid_queue_operation') as error:
                self.b.manage_waiting('device','codex','chat',command_id,'cancel')
            self.assertEqual(error.exception.status,400)
        queued=self.send()
        for operation,revision in (([],None),('steer',[]),('steer',None),('cancel',{})):
            with self.assertRaisesRegex(ControlError,'invalid_queue_operation') as error:
                self.b.manage_waiting('device','codex','chat',queued['id'],operation,revision)
            self.assertEqual(error.exception.status,400)

    def test_positive_no_write_deferral_does_not_redispatch_same_exchange(self):
        queued = self.send()
        self.cap = capability('idle')
        self.exchange()
        deferred = {'id':queued['id'],'state':'waiting'}
        self.assertIsNone(self.exchange([deferred])['command'])
        self.assertEqual(self.state(queued)['state'],'waiting')
        self.cap = capability('running')
        self.assertIsNone(self.exchange([deferred])['command'])
        self.cap = capability('idle')
        self.assertEqual(self.exchange()['command']['id'],queued['id'])

    def test_idle_submission_becoming_busy_receives_bounded_waiting_lifetime(self):
        self.cap=capability('idle');self.exchange()
        queued=self.send();self.assertEqual(queued['state'],'queued')
        self.cap=capability('running')
        self.assertIsNone(self.exchange()['command'])
        self.now += 61
        self.assertIsNone(self.exchange()['command'])
        self.assertEqual(self.state(queued)['state'],'waiting')
        with self.b.db() as db:
            deadline=db.execute('SELECT deadline FROM commands WHERE id=?',(queued['id'],)).fetchone()[0]
        self.assertEqual(deadline,queued['created']+86400)
        self.now += 100
        self.exchange([{'id':queued['id'],'state':'waiting'}])
        with self.b.db() as db:
            self.assertEqual(db.execute('SELECT deadline FROM commands WHERE id=?',(queued['id'],)).fetchone()[0],deadline)
        self.now=deadline+1
        self.assertEqual(self.state(queued)['state'],'expired')

    def test_lost_dispatch_or_unknown_receipt_never_retries(self):
        queued = self.send()
        self.cap = capability('idle')
        self.exchange()
        self.b = Broker(self.tmp,lambda:self.now)
        self.assertIsNone(self.exchange()['command'])
        self.assertEqual(self.state(queued)['state'],'unknown')
        # An old no-write receipt cannot revive a dispatch declared uncertain.
        self.assertIsNone(self.exchange([{'id':queued['id'],'state':'waiting'}])['command'])
        self.assertEqual(self.state(queued)['state'],'unknown')

    def test_lost_deferral_ack_cannot_block_or_undo_later_promotion(self):
        queued = self.send()
        self.cap = capability('idle');self.exchange()
        receipt = {'id':queued['id'],'state':'waiting'}
        self.assertIsNone(self.exchange([receipt])['command'])
        self.cap = capability('running');self.exchange()
        self.b.manage_waiting('device','codex','chat',queued['id'],'steer','native-turn-a')
        retry_ack = self.exchange([receipt])
        self.assertIn(queued['id'],retry_ack['receipts'])
        self.assertEqual(retry_ack['command']['id'],queued['id'])
        self.assertEqual(retry_ack['command']['action']['mode'],'steer')
        self.assertIsNone(self.exchange()['command'])

    def test_pause_rotation_and_expiry_cancel_unsent_messages(self):
        queued = self.send()
        self.b.set_enabled('device',False)
        self.assertEqual(self.state(queued)['state'],'cancelled')
        self.b.set_enabled('device',True)
        queued = self.send()
        self.b.provision('device')
        self.assertEqual(self.state(queued)['state'],'cancelled')
        self.exchange()
        queued = self.send()
        self.now += 86401
        self.assertEqual(self.state(queued)['state'],'expired')

    def test_queue_limit_and_same_id_compatibility(self):
        first = self.send()
        result = self.b.submit('device','codex','chat',first['id'],{'kind':'reply','text':'A message','mode':'after-turn'})
        self.assertEqual(result,first)
        for n in range(19):self.send(str(n))
        with self.assertRaisesRegex(ControlError,'reply_queue_full'):self.send('overflow')

    def test_unknown_and_waiting_states_never_release(self):
        for state in ('unknown','waiting'):
            self.cap = capability(state)
            self.exchange()
            queued = self.send(state)
            self.assertIsNone(self.exchange()['command'])
            self.assertEqual(self.state(queued)['state'],'waiting')


class ProviderDeliveryTests(unittest.TestCase):
    def test_timeout_or_crash_after_native_write_retains_barrier_before_next_reply(self):
        for failure in (TimeoutError('lost acknowledgement'), SystemExit('crash'), ControlError('codex_owner_unavailable')):
            with self.subTest(failure=type(failure).__name__):
                tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
                broker = Broker(tmp/'hub');broker.provision('device')
                class Provider:
                    state='running'
                    calls=[]
                    error=failure
                    def snapshot(self):
                        row=capability(self.state);row['delivery']['epoch']='closed-old'
                        return [row]
                    def session_snapshot(self,sid):return self.snapshot()
                    def execute(self,command):
                        self.calls.append(command['id'])  # Native write happened.
                        if self.error:raise self.error
                        return 'delivered'
                provider=Provider()
                config={'hub_url':'https://example.test','device_id':'device','token':'test-token'}
                def worker():
                    value=Worker(config,{'codex':provider},tmp/'state.json')
                    value.exchange=lambda body:{'ok':True,**broker.exchange('device',body['worker_id'],body['sessions'],body['receipts'])}
                    return value
                w=worker();w.step()
                ids=[str(uuid.uuid4()),str(uuid.uuid4())]
                for command_id in ids:
                    broker.submit('device','codex','chat',command_id,{'kind':'reply','text':'Fixed text','mode':'after-turn'})
                provider.state='idle'
                if isinstance(failure,SystemExit):
                    with self.assertRaises(SystemExit):w.step()
                else:w.step()
                provider.error=None
                restarted=worker()
                for _ in range(3):restarted.step()
                self.assertEqual(provider.calls,[ids[0]])
                rows={row['id']:row for row in broker.status('device','codex','chat')['commands']}
                self.assertEqual(rows[ids[0]]['state'],'unknown')
                self.assertEqual(rows[ids[1]]['state'],'waiting')

    def test_known_prewrite_rejection_removes_only_this_attempts_barrier(self):
        tmp=Path(self.enterContext(tempfile.TemporaryDirectory()))
        class Provider:
            failure=ControlError('native_message_starts_with_option')
            def snapshot(self):
                row=capability('idle');row['delivery']['epoch']='closed-old'
                return [row]
            def session_snapshot(self,sid):return self.snapshot()
            def execute(self,command):
                if self.failure:raise self.failure
                return 'delivered'
        provider=Provider()
        config={'hub_url':'https://example.test','device_id':'device','token':'test-token'}
        w=Worker(config,{'codex':provider},tmp/'state.json')
        command={'id':str(uuid.uuid4()),'tool':'codex','session_id':'chat','action':{'kind':'reply','text':'-literal','mode':'after-turn'}}
        w.exchange=lambda _:{'ok':True,'receipts':[],'command':command}
        w.step()
        self.assertEqual(w.state['receipts'][0]['state'],'rejected')
        self.assertNotIn('codex\nchat',w.state['reply_gates'])
        # A later no-write deferral must not erase an earlier accepted message's
        # barrier merely because this attempt never reached provider.execute.
        original={'epoch':'closed-old','saw_busy':False}
        w.state['reply_gates']['codex\nchat']=copy.deepcopy(original)
        command={**command,'id':str(uuid.uuid4())}
        w.step()
        self.assertEqual(w.state['receipts'][-1]['state'],'waiting')
        self.assertEqual(w.state['reply_gates']['codex\nchat'],original)

    def test_native_ack_before_running_cannot_release_second_reply_even_after_restart(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        broker = Broker(tmp/'hub')
        broker.provision('device')
        class Provider:
            state = 'running'
            completion = 'closed-old-turn'
            calls = []
            def snapshot(self):
                row = capability(self.state)
                row['delivery']['epoch'] = self.completion
                return [row]
            def session_snapshot(self,sid):return self.snapshot()
            def execute(self,command):
                self.calls.append(command['id'])
                # Deliberately acknowledge before any new running flag.
                return 'delivered'
        provider = Provider()
        config = {'hub_url':'https://example.test','device_id':'device','token':'test-token'}
        def worker():
            value = Worker(config,{'codex':provider},tmp/'worker.json')
            value.exchange = lambda body: {'ok':True, **broker.exchange('device',body['worker_id'],body['sessions'],body['receipts'])}
            return value
        w = worker();w.step()
        def submit(text):
            return broker.submit('device','codex','chat',str(uuid.uuid4()),{'kind':'reply','text':text,'mode':'after-turn'})
        first,second = submit('first'),submit('second')
        provider.state='idle';w.step()
        self.assertEqual(provider.calls,[first['id']])
        w=worker()
        for _ in range(3):w.step()
        self.assertEqual(provider.calls,[first['id']])
        provider.state='running';w.step()
        self.assertEqual(provider.calls,[first['id']])
        provider.state='idle';provider.completion='closed-first-reply';w.step()
        self.assertEqual(provider.calls,[first['id'],second['id']])
        for _ in range(3):w.step()
        self.assertEqual(provider.calls,[first['id'],second['id']])

    def test_fast_completed_native_turn_releases_gate_without_observing_busy(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        worker = Worker({'hub_url':'https://example.test','device_id':'device','token':'test-token'}, {}, Path(tmp)/'state.json')
        current = capability('idle');current['delivery']['epoch']='closed-old'
        worker.remember_reply({'tool':'codex','session_id':'chat','action':{'kind':'reply'}},current)
        self.assertEqual(worker.gated_sessions([current])[0]['delivery']['state'],'waiting')
        unchanged = copy.deepcopy(current);unchanged['settings']={'model':'Changed metadata'}
        self.assertEqual(worker.gated_sessions([unchanged])[0]['delivery']['state'],'waiting')
        current['delivery']['epoch']='none'
        self.assertEqual(worker.gated_sessions([current])[0]['delivery']['state'],'waiting')
        current['delivery']['epoch']='closed-new'
        self.assertEqual(worker.gated_sessions([current])[0]['delivery']['state'],'idle')

    def test_codex_open_turn_or_input_is_not_a_completion_marker(self):
        state={'history':{'threadRuntimeStatus':{'type':'idle'},'turns':[{'turnId':'old','status':'completed','turnStartedAtMs':1}]}}
        before=codex_delivery(state)['epoch']
        state['history']['turns'].append({'turnId':'new','status':'inProgress','turnStartedAtMs':2})
        self.assertEqual(codex_delivery(state)['epoch'],before)
        state['history']['turns'][-1]['status']='completed'
        self.assertNotEqual(codex_delivery(state)['epoch'],before)

    def test_codex_preserves_native_spans_and_routes_only_explicit_insertion(self):
        provider = CodexDesktop('/unused', lambda:[SID], FakeDesktop, background=False)
        self.addCleanup(provider.close)
        provider.refresh()
        provider.rpc.history = {'threadRuntimeStatus':{'type':'active'},'turnHistory':{'kind':'canonical','history':{'entitiesByKey':{
            'tail':{'turnId':'native-turn-a','status':'inProgress','items':[]}}}}}
        provider.rpc.follow(SID,provider.rpc.owner)
        waiting = {'id':'original-id','session_id':SID,'action':{'kind':'reply','text':'Queued text','mode':'after-turn'}}
        with self.assertRaises(ReplyDeferred):provider.execute(waiting)
        self.assertFalse(any(method=='thread-follower-start-turn' for method,_,_ in provider.rpc.calls))
        revision = provider.snapshot()[0]['delivery']['revision']
        inserted = {**waiting,'action':{**waiting['action'],'mode':'steer','revision':revision}}
        self.assertEqual(provider.execute(inserted),'delivered')
        method,params,_ = provider.rpc.calls[-1]
        self.assertEqual(method,'thread-follower-steer-turn')
        self.assertEqual(params['clientUserMessageId'],'original-id')
        self.assertEqual(params['input'],[{'type':'text','text':'Queued text','text_elements':[]}])
        provider.rpc.history['turnHistory']['history']['entitiesByKey']['tail']['turnId']='native-turn-b'
        provider.rpc.follow(SID,provider.rpc.owner)
        with self.assertRaisesRegex(ControlError,'reply_turn_changed'):provider.execute(inserted)

    def test_cold_codex_retains_native_queue_path(self):
        cap = capability('unknown',False)
        cap['delivery']['native_queue'] = True
        guard_reply(cap,{'kind':'reply','text':'Hello','mode':'after-turn'})
        self.assertFalse(cap['delivery']['steer'])

    def test_worker_defers_on_fresh_busy_without_calling_native_execute(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        class Provider:
            calls = 0
            live = capability('running')
            def snapshot(self):return [capability('idle')]
            def session_snapshot(self,sid):return [self.live]
            def execute(self,command):self.calls += 1;return 'delivered'
        p = Provider()
        worker = Worker({'hub_url':'https://example.test','device_id':'device','token':'test-token'}, {'codex':p}, Path(tmp)/'state.json')
        command = {'id':str(uuid.uuid4()),'tool':'codex','session_id':'chat','action':{'kind':'reply','text':'Hi','mode':'after-turn'}}
        worker.exchange = lambda _: {'ok':True,'receipts':[],'command':command}
        worker.step()
        self.assertEqual(p.calls,0)
        self.assertEqual(worker.state['receipts'][0]['state'],'waiting')
        worker.step()
        self.assertEqual(p.calls,0)
        p.live = capability('idle')
        worker.exchange = lambda _: {'ok':True,'receipts':[command['id']],'command':command}
        worker.step()
        self.assertEqual(p.calls,1)

    def test_unsupported_steering_and_malformed_capabilities_are_rejected(self):
        for tool in ('codex','antigravity','dsh'):
            cap = capability('running',False);cap['tool']=tool
            with self.assertRaisesRegex(ControlError,'reply_turn_changed'):
                validate_action(cap,{'kind':'reply','text':'Hi','mode':'steer','revision':'made-up'})
        cap=capability();cap['delivery']['steer']='yes'
        with self.assertRaisesRegex(ControlError,'invalid_delivery_capability'):normalize_sessions([cap])


if __name__ == '__main__':unittest.main()

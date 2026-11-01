"""Browser -> authenticated HTTP -> durable queue -> worker -> native double.

The last hop is a test double. This is not a live Harness integration test.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from urllib.parse import quote
from werkzeug.serving import make_server, WSGIRequestHandler
from aw.model import Task
from aw.control.worker import Worker
from hub_fixture import isolated_hub


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):pass


class NativeDouble:
    def __init__(self):self.calls=[]
    def snapshot(self):
        return [{'tool':'codex','session_id':'native-session','reply':True,'history':True,'requests':[
            {'id':'approval','revision':'v1','kind':'approval','prompt':'echo test','choices':['approve','reject']},
            {'id':'question','revision':'v1','kind':'question','prompt':'Choose','questions':[
                {'id':'q','question':'Which?','options':['A','B'],'multiple':False,'custom':True}]}]}]
    def read_history(self,sid,cursor):
        return {'messages':[{'id':'0','role':'user','text':'HISTORY-PRIVATE'}],'next_cursor':None}
    def execute(self,command):self.calls.append(command);return 'delivered'


class ControlRoundtrip(unittest.TestCase):
    def test_reply_answer_and_approval_with_real_http_worker(self):
        with isolated_hub() as hub, tempfile.TemporaryDirectory() as temp:
            c=hub.app.test_client()
            auth=c.post('/api/auth/setup',json={'password':'isolated-test-password'}).get_json()
            headers={'X-CSRF-Token':auth['csrf_token']}
            hub.STORE.set_local([Task(tool='codex',session_id='rollout',title='Test',state='done',
                state_since=1,last_event_at=1,extra={'thread_id':'native-session'})],{})
            task=hub.STORE.view()[0];device=task['machine_id']
            provision=c.post('/api/control/devices/'+device,headers=headers).get_json()
            server=make_server('127.0.0.1',0,hub.app,request_handler=QuietHandler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                native=NativeDouble()
                worker=Worker({'hub_url':f'http://127.0.0.1:{server.server_port}',
                               'device_id':device,'token':provision['token']},
                              {'codex':native},Path(temp)/'state.json')
                worker.step()
                url='/api/control/tasks/'+quote(task['ui_key'],safe='')
                self.assertTrue(c.get(url).get_json()['connected'])
                actions=[{'kind':'reply','text':'Unicode 中文\n& | literal'},
                    {'kind':'approval','request_id':'approval','revision':'v1','decision':'reject'},
                    {'kind':'answer','request_id':'question','revision':'v1','answers':{'q':{'selected':['B'],'custom':''}}}]
                for action in actions:
                    command={'id':str(uuid.uuid4()),'action':action}
                    self.assertEqual(c.post(url,json=command,headers=headers).status_code,200)
                    worker.step();worker.step()
                    result=c.get(url).get_json()['commands'][0]
                    self.assertEqual((result['id'],result['state']),(command['id'],'delivered'))
                    self.assertEqual(native.calls[-1]['session_id'],'native-session')
                    self.assertEqual(native.calls[-1]['action'],action)
                history_url='/api/history/tasks/'+quote(task['ui_key'],safe='')
                self.assertFalse(c.get(history_url).get_json()['ready'])
                worker.step();worker.step()
                history=c.get(history_url)
                self.assertEqual(history.get_json()['messages'][0]['text'],'HISTORY-PRIVATE')
                self.assertEqual(history.headers['Cache-Control'],'no-store')
                self.assertEqual(hub.app.test_client().get(history_url).status_code,401)
                self.assertNotIn('HISTORY-PRIVATE',Path(temp,'state.json').read_text())
                self.assertNotIn(b'HISTORY-PRIVATE',Path(hub.CONTROL.path).read_bytes())
                self.assertEqual(len(native.calls),3)
                self.assertNotIn('Unicode',Path(temp,'state.json').read_text())
            finally:
                server.shutdown();thread.join(3);server.server_close()

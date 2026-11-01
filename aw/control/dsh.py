"""DeepSeek desktop's authenticated Connection/Gateway protocol (0.2.0-rc.2).

The profile plugin publishes a normal launch URL locally. Credentials never go
upstream; the Hub sees only capabilities and explicitly requested interactions.
"""
from __future__ import annotations
import copy
import hashlib
import http.cookiejar
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .broker import ControlError, encoded, normalize_sessions, validate_action
from .worker import NoRedirect


def connection_record(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    u = urllib.parse.urlsplit(data['url'])
    query = urllib.parse.parse_qs(u.query)
    if (data.get('version') != 1 or not 0 <= time.time() - data['updated']/1000 < 15
            or u.scheme != 'http' or u.hostname != '127.0.0.1' or not u.port
            or u.username or u.password or u.fragment or u.path not in ('', '/')
            or set(query) != {'token'} or len(query['token']) != 1):
        raise ControlError('dsh_connection_unavailable')
    return data, f'http://127.0.0.1:{u.port}'


class Gateway:
    def __init__(self, path, event):
        import websocket  # Optional control dependency, not needed by the Hub.
        self.path, self.event = path, event
        self.connected = False
        self.client_id = None
        self.lock = threading.RLock()
        data, self.base = connection_record(path)
        self.pid = data['pid']
        jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(jar), NoRedirect())
        try:
            with self.http.open(data['url'], timeout=8):
                raise ControlError('dsh_login_exchange_failed')
        except urllib.error.HTTPError as exc:
            if exc.code != 303:
                raise ControlError('dsh_login_exchange_failed') from None
            exc.close()
        if not jar:
            raise ControlError('dsh_login_exchange_failed')
        self.stream_id = str(uuid.uuid4())
        cookie = '; '.join(f'{c.name}={c.value}' for c in jar if c.domain == '127.0.0.1')
        self.ws = websocket.create_connection(self.base.replace('http:', 'ws:', 1)+'/api/remote.mux',
            cookie=cookie, origin=self.base, timeout=8, http_no_proxy=['127.0.0.1'], enable_multithread=True)
        try:
            self.ws.send(json.dumps({'type':'open', 'streamId':self.stream_id,
                                     'endpoint':'$events', 'payload':{'args':{}}}))
            opening = json.loads(self.ws.recv())
            ready = opening.get('value', {})
            if opening.get('type') != 'item' or opening.get('streamId') != self.stream_id or ready.get('type') != 'ready' or not isinstance(ready.get('clientId'), str):
                raise ControlError('dsh_event_stream_unavailable')
            self.client_id = ready['clientId']
            self.connected = True
            self.ws.settimeout(None)
            threading.Thread(target=self.read, daemon=True).start()
        except Exception:
            self.ws.close()
            raise

    def live(self):
        try:
            data, base = connection_record(self.path)
            return self.connected and data['pid'] == self.pid and base == self.base
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def call(self, endpoint, args):
        if not self.live():
            raise ControlError('dsh_connection_unavailable')
        rpc_id = str(uuid.uuid4())
        message = {'type':'client-request','rpcId':rpc_id,'method':endpoint,'payload':{'args':args}}
        request = urllib.request.Request(self.base+'/api/'+endpoint, data=json.dumps(message).encode(),
                                         headers={'Content-Type':'application/json'})
        with self.lock, self.http.open(request, timeout=10) as response:
            raw = response.read(8*1024*1024+1)
        if len(raw) > 8*1024*1024:
            raise ControlError('native_response_too_large')
        result = json.loads(raw)
        if result.get('type') != 'server-response' or result.get('rpcId') != rpc_id:
            raise ControlError('dsh_invalid_response')
        if result.get('result', {}).get('ok') is not True:
            raise ControlError('dsh_native_request_rejected')
        return result['result'].get('value')

    def answer(self, event_id, outcome):
        return self.call('$events/result', {'clientId':self.client_id,'eventId':event_id,'outcome':outcome})

    def read(self):
        try:
            while self.connected:
                message = json.loads(self.ws.recv())
                if message.get('streamId') != self.stream_id:
                    continue
                if message.get('type') != 'item' or not isinstance(message.get('value'), dict):
                    break
                self.event(message['value'])
        except Exception:
            pass
        finally:
            self.connected = False
            self.event({'type':'disconnected'})

    def close(self):
        self.connected = False
        self.ws.close()


def question_request(frame):
    native = frame['request']
    questions = []
    for q in native['questions']:
        if q.get('isSecret') or q.get('secret') or q.get('intent', {}).get('kind') not in (None, 'plan-review'):
            raise ControlError('unsupported_native_question')
        detail = q.get('detail', '')
        questions.append({'id':q['id'], 'question':q['question'] + ('\n\n'+detail if detail else ''),
            'options':[o['label'] for o in q.get('options') or []],
            'option_details':{o['label']:o.get('description','') for o in q.get('options') or []},
            'multiple':q.get('multiSelect',False), 'custom':q.get('intent') is None})
    return {'id':frame['eventId'], 'revision':hashlib.sha256(encoded(frame).encode()).hexdigest(),
            'kind':'question','prompt':'请回答 DeepSeek 原会话中的问题','questions':questions}


class DeepSeek:
    def __init__(self, config, gateway_factory=Gateway, background=True):
        self.config, self.gateway_factory = config, gateway_factory
        self.gateway = None
        self.refresh_lock = threading.RLock()
        self.generation = 0
        self.rows, self.pending = {}, {}
        self.continued = {}
        self.refreshed = 0
        self.condition = threading.Condition(threading.RLock())
        self.stop = threading.Event()
        if background:
            threading.Thread(target=self.loop, daemon=True).start()

    def event(self, frame):
        with self.condition:
            kind = frame.get('type')
            if kind == 'disconnected':
                self.rows.clear(); self.pending.clear(); self.continued.clear()
            elif kind == 'cancel':
                self.pending.pop(frame.get('eventId'), None)
            elif kind == 'waterfall':
                if frame.get('event') in ('approval/request', 'user-questions/request'):
                    self.pending[frame['eventId']] = {'frame':copy.deepcopy(frame)}
                else:
                    # Never become the answerer for an unrelated native event.
                    threading.Thread(target=self.delegate, args=(frame.get('eventId'),), daemon=True).start()
            self.condition.notify_all()

    def delegate(self, event_id):
        try:
            self.gateway.answer(event_id, {'kind':'next'})
        except Exception:
            pass

    def approval_request(self, frame):
        native = frame['request']; sid = frame['agentId']
        call_id, tool = native.get('callId'), native.get('toolName')
        if not call_id or not tool:
            raise ControlError('approval_details_unavailable')
        projection = self.gateway.call('session/projections', {'request':{'sessionId':sid}})
        page = self.gateway.call('session/page', {'request':{'address':{'kind':'session','sessionId':sid},
                   'throughSeq':projection['asOfSeq'], 'maxMessages':10}})
        calls = [r['event']['data'] for r in page['records'] if r.get('type') == 'event'
                 and r['event'].get('type') == 'tool/call' and r['event'].get('data',{}).get('callId') == call_id]
        if len(calls) != 1 or calls[0].get('name') != tool or 'arguments' not in calls[0]:
            raise ControlError('approval_details_unavailable')
        arguments = calls[0]['arguments']
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass
        display = arguments if isinstance(arguments, str) else json.dumps(arguments,ensure_ascii=False,indent=2)
        prompt = '工具：'+tool+'\n\n参数：\n'+display+'\n\n原因：'+str(native.get('reason') or '')
        if len(prompt) > 16000:
            raise ControlError('approval_details_too_large')
        return {'id':frame['eventId'], 'revision':hashlib.sha256(encoded({'frame':frame,'call':calls[0]}).encode()).hexdigest(),
                'kind':'approval','prompt':prompt,'choices':['approve','reject']}

    def refresh(self):
        with self.refresh_lock:
            self._refresh()

    def _refresh(self):
        if not self.gateway or not self.gateway.live():
            self.generation += 1
            generation = self.generation
            if self.gateway:
                self.gateway.close()
            with self.condition:
                self.rows.clear(); self.pending.clear()
            self.gateway = self.gateway_factory(self.config['connection_file'],
                lambda frame: self.event(frame) if self.generation == generation else None)
        catalog = self.gateway.call('session/list', {'_request':{}})['items']
        mode = self.config.get('scope','selected')
        if mode not in ('selected','all'):
            with self.condition:self.rows.clear();self.continued.clear()
            return
        allowed = set(self.config.get('session_ids', [])) if mode == 'selected' else None
        rows = {r['sessionId']:{'tool':'dsh','session_id':r['sessionId'],'reply':True,'history':True,'requests':[]}
                for r in catalog if r.get('origin') != 'subagent' and not r.get('blank',False) and not r.get('archived', False)
                and r.get('sessionId','').startswith('session-') and (allowed is None or r['sessionId'] in allowed)}
        continued = {}
        for native_row in catalog:
            sid = native_row['sessionId']
            if sid not in rows:continue
            questions = native_row.get('projections',{}).get('values',{}).get('userQuestions',{}).get('questions',{}).get('active',[])
            for question in questions:
                if question.get('state') != 'continued':continue
                key = 'continued:'+question['callId']
                frame = {'eventId':key,'agentId':sid,'request':{'questions':question['questions']}}
                try:
                    request = normalize_sessions([dict(rows[sid],requests=[question_request(frame)])])[('dsh',sid)]['requests'][0]
                except (ControlError,ValueError,KeyError,TypeError):continue
                continued[(sid,key)] = question['callId']
                rows[sid]['requests'].append(request)
        with self.condition:
            pending = list(self.pending.items())
        for event_id, item in pending:
            frame = item['frame']; sid = frame['agentId']
            if sid not in rows:
                self.delegate(event_id)
                with self.condition:self.pending.pop(event_id,None)
                continue
            try:
                request = item.get('request')
                if request is None:
                    request = question_request(frame) if frame['event']=='user-questions/request' else self.approval_request(frame)
                    request = normalize_sessions([dict(rows[sid],requests=[request])])[('dsh',sid)]['requests'][0]
                    with self.condition:
                        if event_id in self.pending:self.pending[event_id]['request'] = request
                rows[sid]['requests'].append(request)
            except (ControlError,ValueError,KeyError,TypeError):
                rows[sid]['reason'] = '此请求的完整内容不可用，请在 DeepSeek 原应用处理'
                self.delegate(event_id)
                with self.condition:self.pending.pop(event_id,None)
        with self.condition:
            # A native cancellation may have arrived during history resolution.
            for row in rows.values():row['requests'] = [r for r in row['requests'] if r['id'] in self.pending or (row['session_id'],r['id']) in continued]
            self.continued = continued
            self.rows, self.refreshed = rows, time.monotonic()

    def loop(self):
        failure = None
        while not self.stop.is_set():
            try:
                self.refresh(); failure = None
            except Exception as exc:
                with self.condition:self.rows.clear()
                category = type(exc).__name__
                if failure != category:print('DeepSeek connection unavailable: '+category,flush=True);failure=category
            self.stop.wait(2)

    def snapshot(self):
        with self.condition:
            if not self.gateway or not self.gateway.live() or time.monotonic()-self.refreshed > 15:
                return []
            rows = copy.deepcopy(list(self.rows.values()))
            for row in rows:row['requests'] = [r for r in row['requests'] if r['id'] in self.pending or (row['session_id'],r['id']) in self.continued]
            return rows

    def session_snapshot(self, sid):
        self.refresh()
        return [r for r in self.snapshot() if r['session_id']==sid]

    def read_history(self, sid, cursor=None):
        from .dsh_history import read_history
        return read_history(self, sid, cursor)

    def execute(self, command):
        sid, action = command['session_id'], command['action']
        rows = self.session_snapshot(sid)
        if not rows:raise ControlError('native_session_unavailable')
        validate_action(rows[0],action)
        if action['kind']=='reply':
            result = self.gateway.call('session/prompt',{'request':{'sessionId':sid,'requestId':command['id'],
                'mode':'queue','content':[{'type':'text','text':action['text']}]}})
            return 'delivered' if result and result.get('accepted') is True else 'unknown'
        answers = {'answers':[{'id':key,'selected':answer['selected'],**({'custom':answer['custom']} if answer['custom'] else {})}
                               for key,answer in action.get('answers',{}).items()]}
        with self.condition:
            call_id = self.continued.get((sid,action['request_id']))
            item = self.pending.get(action['request_id'])
            if not call_id and (not item or item['frame']['agentId']!=sid):raise ControlError('request_expired')
        if call_id:
            result = self.gateway.call('userQuestions/answer',{'agent':sid,'callId':call_id,'answer':answers})
            if result is not True:raise ControlError('request_expired')
            return 'delivered'  # Native continued-question reply queued, not completed.
        value = ('allowed-once' if action['decision']=='approve' else 'rejected') if action['kind']=='approval' else answers
        self.gateway.answer(action['request_id'],{'kind':'result','value':value})
        with self.condition:
            # Gateway removes the replying client before sending cancellation to
            # the other clients. It never echoes cancel to the answerer. The RPC
            # acknowledgement proves receipt, not which client won a reply race.
            self.pending.pop(action['request_id'], None)
            self.condition.notify_all()
        return 'delivered'

    def close(self):
        self.stop.set()
        if self.gateway:self.gateway.close()

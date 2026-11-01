"""Codex JSON-RPC via a pre-existing shared app-server control socket.

Never starts/resumes a separate engine. Unsupported server requests stay in the
native application; no policy, model or sandbox overrides are sent.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import uuid
from .broker import ControlError, encoded, normalize_sessions, validate_action


class Rpc:
    def __init__(self, executable, socket_path, on_event):
        if not Path(socket_path).exists():
            raise ControlError('codex_shared_connection_unavailable')
        self.on_event, self.pending = on_event, {}
        self.lock, self.write_lock = threading.RLock(), threading.Lock()
        self.process = subprocess.Popen([executable, 'app-server', 'proxy', '--sock', socket_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding='utf-8', text=True, bufsize=1, shell=False)
        threading.Thread(target=self.read,daemon=True).start()
        try:
            self.call('initialize',{'clientInfo':{'name':'agent_watch','version':'1.0'}, 'capabilities':{'experimentalApi':True}})
            self.send({'method':'initialized'})
        except Exception:
            self.close()
            raise

    def send(self, value):
        with self.write_lock:
            self.process.stdin.write(json.dumps(value,ensure_ascii=False)+'\n')
            self.process.stdin.flush()

    def read(self):
        try:
            while True:
                line=self.process.stdout.readline(2*1024*1024+1)
                if not line or len(line)>2*1024*1024:break
                msg=json.loads(line)
                if 'method' in msg:
                    self.on_event(msg)
                else:
                    with self.lock:
                        future=self.pending.get(msg.get('id'))
                        if future:
                            future['message']=msg;future['event'].set()
        except (OSError,ValueError):
            pass
        finally:
            self.on_event({'method':'agent-watch/disconnected'})
            with self.lock:
                for future in self.pending.values():future['event'].set()

    def call(self, method, params):
        key='aw-'+str(uuid.uuid4());future={'event':threading.Event()}
        with self.lock:self.pending[key]=future
        try:
            self.send({'id':key,'method':method,'params':params})
            if not future['event'].wait(15) or 'message' not in future:raise TimeoutError()
            result=future['message']
            if 'error' in result:raise ControlError('native_request_rejected')
            return result['result']
        finally:
            with self.lock:self.pending.pop(key,None)

    def close(self):
        # This subprocess is only our transport proxy, never the shared server.
        self.process.terminate()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()


class Codex:
    def __init__(self, executable, socket_path, session_ids, rpc_factory=Rpc):
        self.session_ids=set(session_ids)
        self.requests={};self.lock=threading.RLock();self.connected=True
        self.rpc=rpc_factory(executable,socket_path,self.event)
        self.subscribed=set()

    def event(self,msg):
        method=msg.get('method');p=msg.get('params',{})
        with self.lock:
            if method=='agent-watch/disconnected':
                self.connected=False;self.requests.clear();return
            if method=='serverRequest/resolved':
                item=self.requests.pop(str(p.get('requestId')),None)
                if item:item['resolved'].set()
                return
            if method in ('turn/completed','turn/interrupted'):
                turn_id=p.get('turnId') or (p.get('turn') or {}).get('id')
                for key,item in list(self.requests.items()):
                    if turn_id and item['thread']==p.get('threadId') and item['turn']==turn_id:
                        self.requests.pop(key,None)
                        item['resolved'].set()
                return
            if 'id' not in msg or p.get('threadId') not in self.session_ids:return
            req={'id':str(msg['id']),'revision':hashlib.sha256(encoded(msg).encode()).hexdigest()}
            if method=='item/tool/requestUserInput':
                qs=p.get('questions',[])
                # Secret entry belongs in the native UI, never the panel queue.
                if not qs or any(q.get('isSecret') for q in qs):return
                req.update(kind='question',prompt='请回答原会话中的问题',questions=[
                    {'id':q['id'],'question':q['question'],'options':[o['label'] for o in q.get('options') or []],
                     'option_details':{o['label']:o.get('description','') for o in q.get('options') or []},
                     'multiple':False,'custom':bool(q.get('isOther')) or not q.get('options')} for q in qs])
            elif method=='item/commandExecution/requestApproval':
                if p.get('kind','command')!='command' or not p.get('command') or p.get('networkApprovalContext'):return
                req.update(kind='approval',choices=['approve','reject'],prompt='命令：\n'+p['command']+'\n\n目录：'+str(p.get('cwd') or '')+'\n\n执行环境：'+str(p.get('environmentId') or '原会话环境')+'\n\n原因：'+str(p.get('reason') or ''))
            elif method=='item/fileChange/requestApproval':
                # The approval message does not contain the proposed diff. Do not
                # offer a blind approve button without the matching item payload.
                return
            else:return
            normalize_sessions([{'tool':'codex','session_id':p['threadId'],'reply':False,'requests':[req]}])
            self.requests[req['id']]={'thread':p['threadId'],'turn':p.get('turnId'),'wire_id':msg['id'],'request':req,'resolved':threading.Event()}

    def close(self):
        self.rpc.close()

    def snapshot(self):
        if not self.connected:return []
        loaded=set();cursor=None
        while True:
            page=self.rpc.call('thread/loaded/list',{'cursor':cursor,'limit':100})
            loaded.update(page['data'])
            cursor=page.get('nextCursor')
            if not cursor:break
        # Historical files are not evidence that this server owns the conversation.
        with self.lock:
            self.requests={k:v for k,v in self.requests.items() if v['thread'] in loaded}
        self.subscribed.intersection_update(loaded)
        out=[]
        for sid in sorted(self.session_ids & loaded):
            try:
                if sid not in self.subscribed:
                    self.rpc.call('thread/resume',{'threadId':sid,'excludeTurns':True})
                    self.subscribed.add(sid)
                result=self.rpc.call('thread/read',{'threadId':sid,'includeTurns':False})
                if result['thread']['id']!=sid:continue
            except (ControlError, KeyError):continue
            with self.lock:
                requests=[x['request'] for x in self.requests.values() if x['thread']==sid]
            out.append({'tool':'codex','session_id':sid,'reply':True,'requests':requests,
                        'reason':'文件修改、敏感信息和扩展权限请求请在原应用处理'})
        return out

    def execute(self,command):
        sid,a=command['session_id'],command['action']
        if sid not in self.session_ids:raise ControlError('native_session_unavailable')
        if a['kind']=='reply':
            result=self.rpc.call('turn/start',{'threadId':sid,'input':[{'type':'text','text':a['text']}],
                                               'clientUserMessageId':command['id']})
            return 'delivered' if isinstance(result.get('turn'),dict) and result['turn'].get('id') else 'unknown'
        with self.lock:
            item=self.requests.get(a['request_id'])
            if not item or item['thread']!=sid:raise ControlError('request_expired')
            validate_action({'reply':False,'requests':[item['request']]},a)
            if a['kind']=='approval':result={'decision':'accept' if a['decision']=='approve' else 'decline'}
            else:result={'answers':{k:{'answers':v['selected']+([v['custom']] if v['custom'] else [])} for k,v in a['answers'].items()}}
            self.rpc.send({'id':item['wire_id'],'result':result})
        # A resolved event confirms that the native request ended, but may also
        # mean another client answered first. Do not claim our choice won a race.
        return 'resolved' if item['resolved'].wait(10) else 'unknown'

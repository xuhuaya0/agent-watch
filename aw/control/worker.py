"""Outbound-only control worker. Never retries native actions automatically."""
from __future__ import annotations
import json
import hashlib
import os
import time
import urllib.request
import uuid
from pathlib import Path
from .broker import normalize_sessions, validate_action, ControlError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Worker:
    def __init__(self, config, providers, state_file):
        self.config, self.providers = config, providers
        self.read_results = []
        self.pending_reads = []
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        # A queue identity must never be reused for another device, Hub, or key.
        binding=hashlib.sha256(json.dumps([config['hub_url'].rstrip('/'), config['device_id'], config['token']], ensure_ascii=False).encode()).hexdigest()
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text(encoding='utf-8'))
            if self.state.get('binding') != binding:
                raise ValueError('Control configuration changed; use a separate state file')
        else:
            self.state = {'worker_id': str(uuid.uuid4()), 'receipts': [], 'binding':binding}
        # A prior process might have sent the native action before crashing.
        for r in self.state['receipts']:
            if r['state'] == 'executing':
                r.update(state='unknown', detail='采集端重启，无法确认操作结果；请检查原应用')
        self.save()
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def save(self):
        temp = self.state_file.with_suffix('.tmp')
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, self.state_file)

    def exchange(self, body):
        # Deliberately one configured Hub. Never redirect credentials or fail over
        # to a different command queue merely because ingestion uses several URLs.
        url = self.config['hub_url'].rstrip('/') + '/api/control/exchange'
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
            headers={'Content-Type':'application/json', 'Authorization':'Bearer '+self.config['token']})
        with self.client.open(req, timeout=8) as response:
            raw = response.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('oversized control response')
            return json.loads(raw)

    def step(self):
        sessions = []
        for provider in self.providers.values():
            try:
                sessions.extend(provider.snapshot())
            except Exception:
                # A disconnected native provider advertises no actionable session.
                continue
        normalize_sessions(sessions)
        self.has_sessions = bool(sessions)
        response = self.exchange({'device_id':self.config['device_id'], 'worker_id':self.state['worker_id'],
                                  'sessions':sessions, 'receipts':self.state['receipts'], 'read_results':self.read_results})
        if not isinstance(response, dict) or response.get('ok') is not True:
            raise ValueError('invalid control response')
        accepted = response.get('receipts', [])
        if not isinstance(accepted,list) or any(not isinstance(x,str) for x in accepted):
            raise ValueError('invalid receipts response')
        self.state['receipts'] = [r for r in self.state['receipts'] if r['id'] not in accepted]
        self.save()
        accepted_reads = response.get('read_receipts', [])
        self.read_results = [r for r in self.read_results if r['id'] not in accepted_reads]
        self.pending_reads.extend(response.get('reads', [])[:1])
        command = response.get('command')
        if not command:
            for read in self.pending_reads[:1]:
                try:
                    provider = self.providers.get(read['tool'])
                    if not provider or not hasattr(provider, 'read_history'):
                        raise ControlError('history_unavailable')
                    result = provider.read_history(read['session_id'], read.get('cursor'))
                except ControlError as exc:
                    result = {'error': exc.code}
                except Exception:
                    result = {'error': 'history_unavailable'}
                self.read_results.append({'id': read['id'], 'result': result})
                self.read_results = self.read_results[-4:]
                self.pending_reads.pop(0)
            return bool(self.read_results)
        if any(r['id'] == command['id'] for r in self.state['receipts']):
            return
        receipt = {'id':command['id'], 'state':'executing', 'detail':''}
        self.state['receipts'].append(receipt)
        self.save()  # durable before any side effect
        try:
            provider = self.providers.get(command['tool'])
            if not provider:
                raise ControlError('native_provider_unavailable')
            # Revalidate against LIVE native state, never the Hub's previous scan.
            rows = provider.session_snapshot(command['session_id']) if hasattr(provider, 'session_snapshot') else provider.snapshot()
            current = normalize_sessions(rows).get((command['tool'],command['session_id']))
            if not current:
                raise ControlError('native_session_unavailable')
            validate_action(current, command['action'])
            result = provider.execute(command)
            if result == 'policy_updated' and command['action']['kind'] == 'policy':
                detail = ('原应用已确认此会话的权限预设（沙盒与审批设置）；新会话默认值未修改'
                          if command['tool'] == 'dsh' else
                          '原应用已确认此会话后续轮次的审批策略；沙盒和审批者保持原值')
                receipt.update(state='resolved', detail=detail)
                self.save()
                return True
            if isinstance(result, dict) and command['tool'] == 'dsh' and command['action']['kind'] == 'create':
                sid = result.get('created_session_id', '')
                if (set(result) != {'state', 'created_session_id'} or not isinstance(sid, str)
                        or not sid.startswith('session-') or str(uuid.UUID(sid[8:])) != sid[8:]
                        or result['state'] not in ('delivered', 'unknown')):
                    raise ValueError('invalid creation receipt')
                receipt.update(state=result['state'], result={'tool':'dsh', 'session_id':sid},
                    detail='新会话已创建，首条消息已接收；不代表 Agent 已完成执行' if result['state']=='delivered'
                    else '创建或首条消息送达情况待确认；请按目标会话 ID 检查原应用，勿重复新建')
                self.save()
                return True
            if result in ('queued_native', 'queued_unwoken'):
                receipt.update(state='delivered', detail='已进入 Codex 原生队列；桌面已收到唤醒请求，尚不代表执行完成' if result=='queued_native' else '已进入 Codex 原生队列，但桌面唤醒失败；请在原应用查看，勿重复发送')
                self.save()
                return True
            if result not in ('delivered', 'resolved', 'unknown'):
                raise ValueError('invalid native receipt')
            receipt.update(state=result, detail='原应用已接收；不代表 Agent 已完成执行' if result=='delivered' else '原应用已结束该请求；请查看原应用的最终结果' if result=='resolved' else '无法确认原应用是否已接收，请检查原应用')
        except ControlError as error:
            receipt.update(state='rejected', detail=error.code)
        except Exception:
            # Error details may contain credentials or prompt text. Keep them local
            # and return no raw process output or exception representation.
            receipt.update(state='unknown', detail='无法确认原应用是否已接收，系统不会自动重发')
        self.save()
        return True  # Flush the durable receipt on the next step without sleeping.

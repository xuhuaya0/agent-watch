"""Official agentapi text replies from an Antigravity-managed sidecar."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import uuid
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from .broker import ControlError


class Antigravity:
    def __init__(self, executable, session_ids, run=subprocess.run, environ=None, background=False, interactions=None, history=None, project_permissions=None, creation=None):
        self.executable = str(Path(executable).expanduser().resolve())
        if Path(self.executable).suffix.lower() in ('.bat','.cmd','.ps1'):
            raise ValueError('Use the native language_server executable, not a shell wrapper')
        self.session_ids, self.run = session_ids, run
        self.environ = dict(os.environ if environ is None else environ)
        self.background = background
        self.interactions = interactions
        self.project_permissions = project_permissions
        self.creation = creation
        self.history = history
        from .antigravity_settings import NativeSettings
        rpc = next((getattr(source, 'rpc', None) for source in
                    (interactions, project_permissions, creation) if getattr(source, 'rpc', None) is not None), None)
        self.settings = NativeSettings(rpc)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._cache = {}
        self._thread = None

    def invoke(self, operation, sid, message=None):
        try:
            if str(uuid.UUID(sid)) != sid:raise ValueError()
        except (ValueError, TypeError):
            raise ControlError('invalid_native_session',400)
        if not self.environ.get('ANTIGRAVITY_LS_ADDRESS'):
            raise ControlError('antigravity_sidecar_required')
        argv = [self.executable, 'agentapi', operation]
        if operation == 'send-message':
            argv.append('--title=Agent Watch')
        argv.append(sid)
        if message is not None:
            argv.append(message)
        result = self.run(argv, shell=False, capture_output=True, text=True, encoding='utf-8',
                          timeout=20, env=self.environ)
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            payload = None
        if result.returncode or not isinstance(payload, dict) or 'error' in payload:
            if operation == 'get-conversation-metadata':
                raise ControlError('native_session_unavailable')
            # No documented exactly-once/negative-ack contract for agentapi.
            return None
        return payload

    def session_snapshot(self, sid):
        rows = self._session_snapshot(sid)
        for row in rows:
            row['history'] = self.history is not None
        return rows

    def read_history(self, sid, cursor=None):
        if self.history is None:
            raise ControlError('history_unavailable')
        return self.history.read(sid, cursor)

    def _session_snapshot(self, sid):
        """Fresh native check for ONE target immediately before dispatch."""
        if sid not in self.session_ids():
            return []
        context = {}
        if self.interactions is not None:
            row, _ = self.interactions.inspect(sid, context=context)
        else:
            metadata = self.invoke('get-conversation-metadata', sid) or {}
            body = metadata.get('response', metadata)
            if isinstance(body, dict):
                native_metadata = body.get('metadata')
                context['project_id'] = native_metadata.get('projectId') if isinstance(native_metadata,dict) else None
                context['metadata'] = native_metadata
            row = {'tool':'antigravity', 'session_id':sid, 'reply':True, 'requests':[],
                   'reason':'文字消息以 Agent Watch 系统通知发送；此原生接口尚不支持回答选项或批准执行'}
        create = self.creation.capability(sid, context.get('project_id')) if self.creation else None
        policy = self.project_permissions.capability(sid, context.get('project_id')) if self.project_permissions else None
        if (policy and self.session_ids.allows_project_permissions()
                and row['reply'] and not row.get('requests')):
            row['policy'] = policy
        if create:
            row['create'] = create
        row['settings'] = self.settings.read(context, policy, create)
        return [row]

    def _refresh(self):
        # Slow/missing native sessions cannot block heartbeats and receipts.
        # Each child process has its own timeout; no unbounded work queue.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='ag-metadata') as pool:
            pending = {}
            checked = {}
            while not self._stop.is_set():
                ids = self.session_ids()
                now = time.monotonic()
                with self._lock:
                    self._cache = {s:v for s,v in self._cache.items() if s in ids and now-v[0]<60}
                for sid in sorted(ids, key=lambda s: checked.get(s, -1000)):
                    if len(pending) >= 4:
                        break
                    if sid in pending.values() or now-checked.get(sid, -1000)<10:
                        continue
                    pending[pool.submit(self.session_snapshot, sid)] = sid
                if not pending:
                    self._stop.wait(.5)
                    continue
                done, _ = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
                for future in done:
                    sid = pending.pop(future)
                    checked[sid] = time.monotonic()
                    try:
                        rows = future.result()
                    except Exception:
                        # Isolate malformed or newly introduced native payloads
                        # to this session; never kill discovery for every chat.
                        rows = []
                    with self._lock:
                        if rows:
                            self._cache[sid] = (checked[sid], rows[0])
                        else:
                            self._cache.pop(sid, None)
                checked = {s:t for s,t in checked.items() if s in ids}

    def snapshot(self):
        if not self.environ.get('ANTIGRAVITY_LS_ADDRESS'):
            return []
        if self.background:
            if self._thread is None:
                self._thread = threading.Thread(target=self._refresh, daemon=True, name='ag-discovery')
                self._thread.start()
            ids = set(self.session_ids())
            now = time.monotonic()
            with self._lock:
                return [dict(row) for sid,(at,row) in self._cache.items() if sid in ids and now-at<60]
        result = []
        for sid in self.session_ids():
            try:
                result.extend(self.session_snapshot(sid))
            except (ControlError, subprocess.TimeoutExpired, OSError):
                continue
        return result

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=21)

    def execute(self, command):
        if command['session_id'] not in self.session_ids():
            raise ControlError('native_session_unavailable')
        if command['action']['kind'] == 'create':
            if self.creation is None:
                raise ControlError('native_action_unsupported')
            return self.creation.execute(self, command)
        if command['action']['kind'] == 'policy':
            if not self.project_permissions or not self.session_ids.allows_project_permissions():
                raise ControlError('policy_changed')
            def authorized():
                return (self.session_ids.allows_project_permissions()
                        and command['session_id'] in self.session_ids())
            return self.project_permissions.execute(command['session_id'], command['action'], self.session_snapshot, authorized)
        if command['action']['kind'] != 'reply':
            if self.interactions is not None:
                return self.interactions.execute(command['session_id'],command['action'])
            raise ControlError('native_action_unsupported')
        # agentapi also accepts flags. Until its end-of-options contract is
        # verified, do not let a message be interpreted as an option.
        if command['action']['text'].startswith('-'):
            raise ControlError('native_message_starts_with_option')
        result = self.invoke('send-message', command['session_id'], command['action']['text'])
        return 'delivered' if result is not None else 'unknown'

"""Connect to an existing desktop IPC router; never create a router or engine.

This versioned desktop protocol is deliberately separate from app-server RPC.
Only local conversations with a positively identified owner are controllable.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import socket
import stat
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .broker import ControlError, normalize_sessions, validate_action
from .codex import Codex
from .codex_questions import project, patch_history, pending, reply_envelope
from .delivery import codex_delivery, guard_reply


VERSIONS = {'initialize': 0, 'thread-owner-discovery': 1,
            'thread-stream-following-changed': 1, 'thread-follower-start-turn': 2,
            'thread-follower-command-approval-decision': 1,
            'thread-follower-submit-user-input': 1}
VERSIONS['thread-follower-steer-turn'] = 1
VERSIONS['thread-follower-update-thread-settings'] = 2
VERSIONS['thread-follower-read-model-settings'] = 1
# The desktop's native framing limit; long histories can exceed 90 MiB. A
# snapshot is transient: only requests and a minimal question projection survive.
MAX_FRAME = 256 * 1024 * 1024


class DesktopRpc:
    def __init__(self, path, on_event):
        self.sock = self.connect(path)
        self.callback = on_event
        self.pending = {}
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.client_id = 'initializing-client'
        self.connected = True
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.client_id = self.call('initialize', {'clientType': 'agent-watch'})['result']['clientId']
        except Exception:
            self.close()
            raise

    @staticmethod
    def connect(path):
        if os.name == 'nt':
            from .windows_pipe import NamedPipe
            return NamedPipe(path)
        path = Path(path).expanduser()
        # Never fall back to a global temporary socket, start a router, or chmod
        # another application's files. Only a socket owned by this user is valid.
        if not hasattr(os, 'getuid'):
            raise ControlError('codex_desktop_ipc_unavailable')
        for p, directory in ((path.parent, True), (path, False)):
            st = p.lstat()
            if st.st_uid != os.getuid() or st.st_mode & 0o022 or not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISSOCK(st.st_mode)):
                raise ControlError('codex_unsafe_socket')
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.settimeout(5)
            sock.connect(str(path))
            sock.settimeout(None)
            return sock
        except Exception:
            sock.close()
            raise

    def send(self, message):
        raw = json.dumps(message, ensure_ascii=False).encode('utf-8')
        if len(raw) > MAX_FRAME:
            raise ControlError('native_message_too_large')
        with self.write_lock:
            self.sock.sendall(struct.pack('<I', len(raw)) + raw)

    def call(self, method, params, owner=None, timeout=8):
        key = str(uuid.uuid4())
        waiter = {'event': threading.Event()}
        with self.lock:
            self.pending[key] = waiter
        try:
            msg = {'type': 'request', 'requestId': key, 'sourceClientId': self.client_id,
                   'method': method, 'version': VERSIONS[method], 'params': params,
                   'timeoutMs': int(timeout * 1000)}
            if owner:
                msg['targetClientId'] = owner
            self.send(msg)
            if not waiter['event'].wait(timeout + 1) or 'response' not in waiter:
                raise TimeoutError('desktop IPC response unavailable')
            result = waiter['response']
            if result.get('resultType') != 'success':
                raise ControlError('codex_owner_unavailable')
            if owner and result.get('handledByClientId') != owner:
                raise ControlError('codex_owner_changed')
            return result
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def follow(self, sid, owner, following=True):
        self.send({'type': 'broadcast', 'method': 'thread-stream-following-changed',
                   'version': 1, 'sourceClientId': self.client_id, 'targetClientIds': [owner],
                   'params': {'hostId': 'local', 'conversationId': sid, 'following': following}})

    def read_exact(self, count):
        out = bytearray()
        while len(out) < count:
            part = self.sock.recv(count - len(out))
            if not part:
                raise EOFError()
            out.extend(part)
        return bytes(out)

    def read(self):
        try:
            while self.connected:
                size = struct.unpack('<I', self.read_exact(4))[0]
                if not 0 < size <= MAX_FRAME:
                    raise ValueError('invalid IPC frame')
                message = json.loads(self.read_exact(size))
                if not isinstance(message, dict):
                    raise ValueError('invalid IPC message')
                if message.get('type') == 'response':
                    with self.lock:
                        waiter = self.pending.get(message.get('requestId'))
                        if waiter:
                            waiter['response'] = message
                            waiter['event'].set()
                elif message.get('type') == 'client-discovery-request':
                    self.send({'type': 'client-discovery-response', 'requestId': message['requestId'],
                               'response': {'canHandle': False}})
                elif message.get('type') == 'broadcast':
                    targets = message.get('targetClientIds')
                    if targets is None or self.client_id in targets:
                        self.callback(message)
        except (OSError, EOFError, ValueError, KeyError):
            pass
        finally:
            self.connected = False
            self.callback({'method': 'agent-watch/disconnected'})
            with self.lock:
                for waiter in self.pending.values():
                    waiter['event'].set()

    def close(self):
        self.connected = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def patch_requests(requests, patches):
    """Apply only native Immer request patches. A malformed patch invalidates state."""
    result = copy.deepcopy(requests)
    for patch in patches:
        path = patch['path']
        if not isinstance(path, list) or not path:
            raise ValueError('invalid patch path')
        if path[0] != 'requests':
            continue
        if len(path) == 1:
            if patch['op'] not in ('add', 'replace') or not isinstance(patch.get('value'), list):
                raise ValueError('invalid requests replacement')
            result = copy.deepcopy(patch['value'])
            continue
        parent = result
        for part in path[1:-1]:
            parent = parent[part]
        key = path[-1]
        op = patch['op']
        if isinstance(parent, list):
            if type(key) is not int or key < 0 or key > len(parent):
                raise ValueError('invalid request index')
            if op == 'add':
                parent.insert(key, copy.deepcopy(patch['value']))
            elif op == 'replace':
                parent[key] = copy.deepcopy(patch['value'])
            elif op == 'remove':
                parent.pop(key)
            else:
                raise ValueError('invalid request operation')
        elif isinstance(parent, dict):
            if op == 'remove':
                del parent[key]
            elif op in ('add', 'replace'):
                parent[key] = copy.deepcopy(patch['value'])
            else:
                raise ValueError('invalid request operation')
        else:
            raise ValueError('invalid request container')
    return result


class CodexDesktop:
    def __init__(self, socket_path, scope, rpc_factory=DesktopRpc, background=True, history=None, queue_sender=None, draft=None, catalog=None):
        self.socket_path, self.scope, self.rpc_factory = socket_path, scope, rpc_factory
        self.rpc = None
        self.history_reader = history
        self.queue_sender = queue_sender
        self.draft = draft
        self.catalog = catalog
        self.native_settings = {}
        self.settings_reads = {}
        self.states, self.owners = {}, {}
        self.picker_context_owners = {}
        self.next_probe = {}
        self.owner_checked = {}
        self.condition = threading.Condition(threading.RLock())
        self.stop = threading.Event()
        if background:
            threading.Thread(target=self.refresh_loop, daemon=True).start()

    def event(self, message):
        with self.condition:
            if message.get('method') == 'agent-watch/disconnected':
                self.states.clear()
                self.owners.clear()
                self.owner_checked.clear()
                self.native_settings.clear()
                self.settings_reads.clear()
                self.picker_context_owners.clear()
                self.condition.notify_all()
                return
            if message.get('method') == 'client-status-changed':
                p = message.get('params', {})
                if p.get('status') == 'disconnected':
                    for sid, owner in list(self.owners.items()):
                        if owner == p.get('clientId'):
                            self.owners.pop(sid, None)
                            self.states.pop(sid, None)
                            self.native_settings.pop(sid, None)
                            self.picker_context_owners.pop(sid, None)
                return
            if message.get('method') != 'thread-stream-state-changed':
                return
            p = message.get('params', {})
            sid = p.get('conversationId')
            if p.get('hostId') != 'local' or message.get('sourceClientId') != self.owners.get(sid):
                return
            try:
                if message.get('version') != 11:
                    raise ValueError('unsupported desktop protocol')
                change = p['change']
                revision = change['revision']
                if type(revision) is not int:
                    raise ValueError('invalid revision')
                previous_history = (self.states.get(sid) or {}).get('history')
                if change['type'] == 'snapshot':
                    state = change['conversationState']
                    if state['id'] != sid or not isinstance(state.get('requests'), list):
                        raise ValueError('invalid conversation')
                    requests = copy.deepcopy(state['requests'])
                    history = project(state)
                elif change['type'] == 'patches':
                    state = self.states[sid]
                    if state['revision'] != change['baseRevision'] or revision <= state['revision']:
                        raise ValueError('missing desktop state patches')
                    requests = patch_requests(state['requests'], change['patches'])
                    history = patch_history(state.get('history', {}), change['patches'])
                else:
                    raise ValueError('unsupported change')
                if previous_history is not None and self.catalog is not None:
                    before = previous_history.get('latestThreadSettings') or {}
                    after = history.get('latestThreadSettings') or {}
                    model_changed = (any(before.get(key) != after.get(key) for key in ('model', 'effort'))
                        or any((key in previous_history, previous_history.get(key)) !=
                               (key in history, history.get(key)) for key in
                               ('latestModel', 'latestReasoningEffort', 'latestCollaborationMode',
                                'resumeState', 'modelProvider'))
                        or any(patch.get('path', [None])[0] == 'latestCollaborationMode'
                               for patch in change.get('patches', []) if patch.get('path')))
                    if model_changed or before != after:
                        self.next_probe[sid] = 0
                        self.settings_reads[sid] = object()
                        known = self.native_settings.get(sid)
                        if known:
                            self.native_settings[sid] = {**known, 'checked': float('-inf'),
                                'model_invalidated': model_changed or known.get('model_invalidated', False)}
                if any(key in history for key in ('latestCollaborationMode', 'resumeState', 'modelProvider')):
                    self.picker_context_owners[sid] = self.owners[sid]
                self.states[sid] = {'requests': requests, 'history': history, 'revision': revision,
                                    'picker_context_required': self.picker_context_owners.get(sid) == self.owners[sid],
                                    'received': time.monotonic()}
            except (ValueError, KeyError, IndexError, TypeError):
                self.states.pop(sid, None)
            self.condition.notify_all()

    def refresh(self):
        if self.rpc is None or not self.rpc.connected:
            if self.rpc:
                self.rpc.close()
            self.rpc = self.rpc_factory(self.socket_path, self.event)
        ordered_ids = self.scope()
        ids = set(ordered_ids)
        with self.condition:
            for sid in set(self.owners) - ids:
                self.rpc.follow(sid, self.owners.pop(sid), False)
                self.states.pop(sid, None)
                self.native_settings.pop(sid, None)
                self.picker_context_owners.pop(sid, None)
        def probe(sid):
            if self.stop.is_set():
                return
            try:
                response = self.rpc.call('thread-owner-discovery', {'hostId': 'local', 'conversationId': sid}, timeout=8)
                owner = response['handledByClientId']
                with self.condition:
                    if self.owners.get(sid) != owner:
                        self.states.pop(sid, None)
                    self.owners[sid] = owner
                    self.owner_checked[sid] = time.monotonic()
                    self.next_probe[sid] = time.monotonic() + 10
                    needs_snapshot = sid not in self.states
                # Once subscribed, native patches keep requests current. Do not
                # re-transfer a long conversation's full history every poll.
                if needs_snapshot:
                    self.rpc.follow(sid, owner)
                self.refresh_settings(sid, owner)
            except Exception:
                with self.condition:
                    self.owners.pop(sid, None)
                    self.states.pop(sid, None)
                    self.native_settings.pop(sid, None)
                    self.next_probe[sid] = time.monotonic() + 60
        due = [sid for sid in ordered_ids if self.next_probe.get(sid, 0) <= time.monotonic()]
        # Cold histories cannot delay already owned conversations indefinitely.
        # Process a bounded batch each cycle; an unavailable owner backs off.
        due.sort(key=lambda sid: sid not in self.owners)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(probe, due[:8]))

    def refresh_loop(self):
        failure = None
        while not self.stop.is_set():
            try:
                self.refresh()
                if failure:
                    print('Codex desktop connection restored', flush=True)
                    failure = None
            except Exception as exc:
                with self.condition:
                    self.states.clear()
                # Report categories only: native payloads can contain private
                # chat text. Avoid logging them or flooding a persistent log.
                category = type(exc).__name__
                if category != failure:
                    print('Codex desktop connection unavailable: ' + category, flush=True)
                    failure = category
            self.stop.wait(2)

    def refresh_settings(self, sid, owner, fresh=False, fresh_features=None):
        if not self.catalog:
            return
        token = object()
        with self.condition:
            self.settings_reads[sid] = token
        directories = getattr(self.scope, 'creation_directories', lambda: {})()
        cwd = directories.get(sid)
        native, catalog = None, None
        try:
            native = self.rpc.call('thread-follower-read-model-settings',
                {'conversationId': sid}, owner=owner)['result']['settings']
        except (ControlError, KeyError, TypeError, TimeoutError, OSError):
            pass
        if isinstance(native, dict) and isinstance(native.get('model'), str):
            try:
                extra = {} if fresh_features is None else {'fresh_features': fresh_features}
                catalog = self.catalog.get(cwd, native.get('modelProvider'), fresh=fresh, **extra)
            except (ControlError, KeyError, TypeError, TimeoutError, OSError):
                pass
            if catalog:
                catalog = {**catalog, 'selected_model': native.get('model')}
            value = {'owner': owner, 'catalog': catalog, 'model': native,
                     'cwd': cwd, 'checked': time.monotonic()}
        else:
            value = None
        with self.condition:
            if self.owners.get(sid) == owner and self.settings_reads.get(sid) is token:
                if value and value['catalog']:
                    current = (self.states.get(sid, {}).get('history') or {}).get('latestThreadSettings') or {}
                    value['catalog'].update(selected_approval_policy=current.get('approvalPolicy'),
                                             selected_reviewer=current.get('approvalsReviewer'))
                previous = self.native_settings.get(sid)
                if value is None and previous and previous['owner'] == owner:
                    # Preserve a confirmed current value for display, but a
                    # failed read can never preserve permission to edit it.
                    value = {**previous, 'checked': float('-inf')}
                self.native_settings[sid] = value

    def row(self, sid, state):
        # Reuse the app-server request schema; the desktop publishes exactly the
        # original server request objects, including their native IDs.
        mapper = Codex.__new__(Codex)
        mapper.session_ids = {sid}
        mapper.requests = {}
        mapper.lock = threading.RLock()
        for request in state['requests']:
            mapper.event(request)
        requests = [entry['request'] for entry in mapper.requests.values()]
        unsupported = len(requests) != len(state['requests'])
        requests.extend(pending(state.get('history', {})))
        from .codex_policy import capability, native_capability
        from .codex_settings import summary, model_capability, effective_model_settings
        # Once native catalogs are configured, unavailable or stale metadata
        # must revoke edits, never broaden them to the older approval-only API.
        policy = capability(state) if not requests and self.catalog is None else None
        settings = summary(state)
        model = None
        options = self.native_settings.get(sid)
        owned = options and options['owner'] == self.owners.get(sid)
        recent = owned and time.monotonic() - options['checked'] <= 30
        catalog = options.get('catalog') if owned else None
        if catalog:
            latest = (state.get('history') or {}).get('latestThreadSettings') or {}
            catalog = {**catalog, 'selected_approval_policy': latest.get('approvalPolicy'),
                       'selected_reviewer': latest.get('approvalsReviewer')}
        editable = bool(recent and catalog and not requests)
        if editable and effective_model_settings(state, options['model'], catalog) is None:
            # A policy change also verifies that model preferences stayed put.
            editable = False
        if self.catalog is not None:
            policy = native_capability(state, catalog, editable=editable)
        if owned and not options.get('model_invalidated'):
            # The owner's next-turn settings are authoritative. A previous
            # turn's latestModel can remain unchanged until the next send.
            model = model_capability(state, catalog, options['model'], editable=editable)
        elif owned and options.get('model_invalidated') and settings:
            settings.pop('model', None)
            settings.pop('reasoning', None)
        if model or policy and 'current_label' in policy:
            settings = settings or {'scope': 'session'}
            facts = settings.get('permission')
            notes = []
            if policy:
                settings['permission'] = policy['current_label']
                if facts and facts != policy['current_label']:
                    notes.append(facts)
            if model:
                settings['model'] = model['current_label']
                settings.pop('reasoning', None)
                if model.get('effort_label'):
                    settings['reasoning'] = model['effort_label']
            if not recent and owned:
                notes.append('当前设置暂无法重新确认；上次确认值仅供查看。')
            elif not catalog:
                notes.append('当前设置仅供查看，暂时无法提供可切换选项。')
            settings.pop('note', None)
            if notes:
                settings['note'] = ' '.join(notes)
        delivery = codex_delivery(state)
        if requests:
            delivery = {**delivery, 'state': 'waiting', 'steer': False}
            delivery.pop('revision', None)
        return normalize_sessions([{'tool': 'codex', 'session_id': sid, 'history': self.history_reader is not None,
                                    'delivery': delivery,
                                    **({'policy':policy} if policy else {}),
                                    **({'model':model} if model else {}),
                                    **({'settings':settings} if settings else {}),
                                    'reply': not state['requests'] and not requests, 'requests': requests,
                                    'reason': '此请求请在 Codex 原应用处理' if unsupported else ''}])[('codex', sid)]

    def snapshot(self):
        ids = set(self.scope())
        with self.condition:
            if not self.rpc or not self.rpc.connected:
                return []
            out = []
            for sid, state in self.states.items():
                if sid in ids and time.monotonic() - self.owner_checked.get(sid, 0) <= 30:
                    try:
                        out.append(self.row(sid, state))
                    except (ControlError, KeyError, TypeError):
                        pass
            present = {r['session_id'] for r in out}
            if self.history_reader:
                out.extend(self.cold_row(sid) for sid in ids - present)
            return self._with_creation(out)

    def _with_creation(self, rows):
        if self.draft and rows and self.draft.available():
            from .codex_create import capability
            directories = self.scope.creation_directories()
            for row in rows:
                sid = row['session_id']
                cap = capability(sid, directories.get(sid), 'draft')
                if cap:
                    row['create'] = cap
        return rows

    def cold_row(self, sid):
        return {'tool': 'codex', 'session_id': sid, 'history': True,
                'delivery': {'state': 'unknown', 'steer': False, 'native_queue': self.queue_sender is not None},
                'reply': self.queue_sender is not None, 'requests': [],
                'reason': '回复将进入 Codex 原生队列，并由桌面唤醒原会话' if self.queue_sender else '可查看历史；此设备尚未配置原生队列回复'}

    def read_history(self, sid, cursor):
        if not self.history_reader:
            raise ControlError('history_unavailable')
        return self.history_reader.read(sid, cursor)

    def read_attachment(self, sid, attachment_id):
        if not self.history_reader:
            raise ControlError('attachment_unavailable')
        return self.history_reader.read_attachment(sid, attachment_id)

    def session_snapshot(self, sid, allow_cold=True):
        if sid not in self.scope() or not self.rpc or not self.rpc.connected:
            return []
        try:
            response = self.rpc.call('thread-owner-discovery', {'hostId': 'local', 'conversationId': sid}, timeout=3)
        except (ControlError, TimeoutError):
            if allow_cold and self.history_reader:
                with self.condition:
                    self.owners.pop(sid, None)
                    self.states.pop(sid, None)
                return self._with_creation([self.cold_row(sid)])
            raise
        owner = response['handledByClientId']
        with self.condition:
            if self.owners.get(sid) != owner:
                self.states.pop(sid, None)
            self.owners[sid] = owner
            self.owner_checked[sid] = time.monotonic()
            if sid not in self.states:
                self.rpc.follow(sid, owner)
                self.condition.wait_for(lambda: sid in self.states or not self.rpc.connected, timeout=15)
        self.refresh_settings(sid, owner)
        with self.condition:
            return self._with_creation([self.row(sid, self.states[sid])]) if sid in self.states else []

    def execute(self, command):
        sid, action = command['session_id'], command['action']
        if self.history_reader:
            self.history_reader.path(sid)
        rows = self.session_snapshot(sid)
        if not rows:
            raise ControlError('native_session_unavailable')
        validate_action(rows[0], action)
        if action['kind'] == 'create':
            if not self.draft:
                raise ControlError('create_unavailable')
            return self.draft.execute(self.scope, command)
        if sid not in self.states:
            if action['kind'] != 'reply':
                raise ControlError('request_expired')
            if not self.queue_sender or sid not in self.scope():
                raise ControlError('reply_unavailable')
            self.history_reader.path(sid)
            return self.queue_sender(sid, action['text'])
        if sid not in self.scope():
            raise ControlError('native_session_unavailable')
        with self.condition:
            owner = self.owners[sid]
            # Native patches may arrive between owner discovery and dispatch.
            validate_action(self.row(sid, self.states[sid]), action)
            guard_reply(self.row(sid, self.states[sid]), action)
            native = next((r for r in self.states[sid]['requests'] if str(r.get('id')) == action.get('request_id')), None)
            asynchronous = next((r for r in pending(self.states[sid].get('history', {}))
                                 if r['id'] == action.get('request_id')), None)
        params = {'conversationId': sid}
        if action['kind'] == 'model' or (action['kind'] == 'policy' and rows[0]['policy']['scope'] == 'session-permissions'):
            return self.update_settings(sid, owner, action)
        if action['kind'] == 'policy':
            from .codex_policy import unchanged_permissions
            with self.condition:
                validate_action(self.row(sid, self.states[sid]), action)
                before = copy.deepcopy(self.states[sid])
            response = self.rpc.call('thread-follower-update-thread-settings',
                {**params, 'threadSettings':{'approvalPolicy':action['value']}, 'activeTurnId':None},
                owner=owner)['result']
            if not isinstance(response, dict) or response.get('applied') is not True:
                return 'unknown'
            with self.condition:
                confirmed = self.condition.wait_for(lambda: sid not in self.states or
                    ((self.states[sid].get('history') or {}).get('latestThreadSettings') or {}).get('approvalPolicy') == action['value'], timeout=8)
                return 'policy_updated' if confirmed and sid in self.states and unchanged_permissions(before,self.states[sid]) else 'unknown'
        if action['kind'] == 'reply':
            method = 'thread-follower-steer-turn' if action.get('mode') == 'steer' else 'thread-follower-start-turn'
            # The desktop renders this input before the engine normalizes it.
            # Plain text still needs the native text-span collection.
            if action.get('mode') == 'steer':
                params.update(input=[{'type': 'text', 'text': action['text'], 'text_elements': []}],
                              clientUserMessageId=command['id'],
                              restoreMessage={'id': command['id'], 'text': action['text'], 'context': {}})
            else:
                params['turnStart'] = {'request': {'threadId': sid, 'input': [{'type': 'text', 'text': action['text'], 'text_elements': []}],
                                                 'clientUserMessageId': command['id']},
                                       'context': {'inheritThreadSettings': True}}
        elif asynchronous is not None:
            method = 'thread-follower-steer-turn'
            text = reply_envelope(asynchronous, action['answers'])
            params.update(input=[{'type': 'text', 'text': text, 'text_elements': []}], clientUserMessageId=command['id'],
                          restoreMessage={'id': command['id'], 'text': text, 'context': {}})
        else:
            if native is None:
                raise ControlError('request_expired')
            params['requestId'] = native['id']
            if action['kind'] == 'approval':
                method = 'thread-follower-command-approval-decision'
                params['decision'] = 'accept' if action['decision'] == 'approve' else 'decline'
            else:
                method = 'thread-follower-submit-user-input'
                params['response'] = {'answers': {key: {'answers': value['selected'] + ([value['custom']] if value['custom'] else [])}
                                                 for key, value in action['answers'].items()}}
        result = self.rpc.call(method, params, owner=owner)['result']
        if action['kind'] == 'reply':
            return 'delivered' if isinstance(result, dict) and result.get('result') is not None else 'unknown'
        if asynchronous is not None:
            # Accepted input is not yet proof the agent has consumed the answer.
            with self.condition:
                ended = self.condition.wait_for(lambda: sid not in self.states or not any(
                    r['id'] == asynchronous['id'] for r in pending(self.states[sid].get('history', {}))), timeout=8)
                return 'resolved' if ended and sid in self.states else 'unknown'
        with self.condition:
            ended = self.condition.wait_for(lambda: sid not in self.states or not any(
                r.get('id') == native['id'] for r in self.states[sid]['requests']), timeout=8)
            return 'resolved' if ended and sid in self.states else 'unknown'

    def update_settings(self, sid, owner, action):
        from .codex_policy import FIELDS, matches_selection, settings_for
        from .codex_settings import same_model, effective_model_settings
        # Only selecting automatic review requires a new account rollout read.
        # Model/effort and other native permission choices still refresh their
        # catalog and context, without an unrelated network bootstrap each time.
        self.refresh_settings(sid, owner, fresh=True,
            fresh_features=action['kind'] == 'policy' and action['value'] == 'guardian-approvals')
        with self.condition:
            if sid not in self.scope() or self.owners.get(sid) != owner or sid not in self.states:
                raise ControlError('native_session_unavailable')
            state = self.states[sid]
            validate_action(self.row(sid, state), action)
            options = self.native_settings.get(sid)
            if not options:
                raise ControlError('model_changed' if action['kind'] == 'model' else 'policy_changed')
            before = copy.deepcopy(state['history'].get('latestThreadSettings') or {})
            previous_model = effective_model_settings(state, options['model'], options['catalog'])
            if previous_model is None:
                raise ControlError('model_changed' if action['kind'] == 'model' else 'policy_changed')
            before_revision = state['revision']
            selected_before = (action['kind'] == 'policy'
                and matches_selection(state, action['value'], options['catalog']))
            if action['kind'] == 'model':
                patch = {'model': action['model'], 'effort': action.get('effort')}
            else:
                patch = settings_for(action['value'], options['catalog'])
            if not patch:
                raise ControlError('policy_changed')
        result = self.rpc.call('thread-follower-update-thread-settings',
            {'conversationId': sid, 'threadSettings': patch, 'activeTurnId': None}, owner=owner)['result']
        if not isinstance(result, dict) or result.get('applied') is not True:
            return 'unknown'
        def readback_state():
            state = self.states.get(sid)
            if (not state or self.owners.get(sid) != owner or sid not in self.scope()
                    or state.get('requests') or pending(state.get('history') or {})
                    or (state['history'].get('threadRuntimeStatus') or {}).get('type') != 'idle'):
                return None
            return state
        if action['kind'] == 'policy':
            # Acknowledgement can precede the permission broadcast. Reading
            # immediately lets that expected broadcast invalidate the in-flight
            # read. First observe the selected native state, then read it back.
            with self.condition:
                def published():
                    state = readback_state()
                    return (state is None or (selected_before or state['revision'] > before_revision)
                        and matches_selection(state, action['value'], options['catalog']))
                if not self.condition.wait_for(published, timeout=8) or readback_state() is None:
                    return 'unknown'
        # This native read waits for the owner's pending settings update, and
        # verifies the model that the next real turn will inherit.
        self.refresh_settings(sid, owner)
        if action['kind'] == 'policy':
            with self.condition:
                current = readback_state()
                known = self.native_settings.get(sid)
                selected = effective_model_settings(current, previous_model, options['catalog']) if current else None
                retry_read = (current is not None and known is not None
                    and known.get('owner') == owner and known.get('checked') == float('-inf')
                    and matches_selection(current, action['value'], options['catalog'])
                    and selected is not None
                    and selected.get('reasoningEffort') == previous_model.get('reasoningEffort')
                    and all((current['history'].get('latestThreadSettings') or {}).get(key) == before.get(key)
                            for key in ('model', 'effort')))
            # A second normalization broadcast or another background metadata
            # reader can supersede the first read. Retry only that read, once,
            # after the selected permission and unchanged model are still known.
            if retry_read:
                self.refresh_settings(sid, owner)
        with self.condition:
            options = self.native_settings.get(sid)
            if (not options or not options.get('catalog') or time.monotonic() - options['checked'] > 30
                    or options.get('owner') != owner or readback_state() is None):
                return 'unknown'
            native = options['model']
            def confirmed():
                state = readback_state()
                if not state:
                    return True
                if action['kind'] == 'model':
                    selected = effective_model_settings(state, native, options['catalog'])
                    values = state['history'].get('latestThreadSettings') or {}
                    return (selected is not None
                            and same_model(selected.get('model'), action['model'], options['catalog'], selected.get('modelProvider'))
                            and selected.get('reasoningEffort') == action.get('effort')
                            and all(values.get(key) == before.get(key) for key in FIELDS))
                return matches_selection(state, action['value'], options['catalog'])
            done = self.condition.wait_for(confirmed, timeout=8)
            if not done or readback_state() is None:
                return 'unknown'
            native = effective_model_settings(self.states[sid], native, options['catalog'])
            if native is None:
                return 'unknown'
            if action['kind'] == 'policy' and (not same_model(native.get('model'), previous_model.get('model'),
                    options['catalog'], native.get('modelProvider'))
                    or native.get('reasoningEffort') != previous_model.get('reasoningEffort')):
                return 'unknown'
            return 'model_updated' if action['kind'] == 'model' else 'policy_updated'

    def close(self):
        self.stop.set()
        if self.rpc:
            self.rpc.close()

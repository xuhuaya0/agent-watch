"""Device-bound command queue with explicit, non-retryable delivery uncertainty."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

TOOLS = {'codex', 'antigravity', 'dsh'}
TTL = 15


class ControlError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def text(value, limit=200):
    return isinstance(value, str) and 0 < len(value) <= limit and '\x00' not in value


def normalize_sessions(rows):
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ControlError('invalid_sessions', 400)
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('tool'),str) or row['tool'] not in TOOLS or not text(row.get('session_id')):
            raise ControlError('invalid_session', 400)
        if type(row.get('reply')) is not bool:
            raise ControlError('invalid_capability', 400)
        requests = row.get('requests', [])
        if not isinstance(requests, list) or len(requests) > 20:
            raise ControlError('invalid_requests', 400)
        clean = {'tool': row['tool'], 'session_id': row['session_id'], 'reply': row['reply'],
                 'reason': str(row.get('reason', ''))[:500], 'requests': []}
        if 'history' in row:
            if type(row['history']) is not bool:
                raise ControlError('invalid_capability', 400)
            clean['history'] = row['history']
        if 'create' in row:
            create = row['create']
            if (row['tool'] != 'dsh' or not isinstance(create, dict)
                    or set(create) != {'cwd', 'revision'}
                    or not text(create.get('cwd'), 2000) or not text(create.get('revision'))):
                raise ControlError('invalid_create_capability', 400)
            clean['create'] = dict(create)
        if 'policy' in row:
            from .codex_policy import OPTIONS
            policy = row['policy']
            if (row['tool'] != 'codex' or not isinstance(policy, dict)
                    or set(policy) != {'current','revision','scope','options'}
                    or policy.get('scope') != 'session-next-turn' or policy.get('options') != OPTIONS
                    or policy.get('current') not in ('on-request','never') or not text(policy.get('revision'))):
                raise ControlError('invalid_policy_capability',400)
            clean['policy'] = dict(policy)
        seen = set()
        for req in requests:
            if not isinstance(req, dict) or not text(req.get('id')) or req['id'] in seen:
                raise ControlError('invalid_request_id', 400)
            seen.add(req['id'])
            if req.get('kind') not in ('question', 'approval') or not text(req.get('revision')):
                raise ControlError('invalid_request', 400)
            if not text(req.get('prompt'), 16000):
                raise ControlError('invalid_prompt', 400)
            item = {k: req[k] for k in ('id', 'revision', 'kind', 'prompt')}
            if req['kind'] == 'approval':
                # Only explicitly offered one-shot decisions. No policy escalation.
                choices = req.get('choices')
                if not isinstance(choices, list) or not choices or any(not isinstance(x,str) for x in choices) or len(choices) != len(set(choices)) or set(choices) - {'approve', 'reject'}:
                    raise ControlError('invalid_decisions', 400)
                item['choices'] = choices
            else:
                questions = req.get('questions')
                if not isinstance(questions, list) or not 1 <= len(questions) <= 10:
                    raise ControlError('invalid_questions', 400)
                ids = set()
                item['questions'] = []
                for q in questions:
                    if not isinstance(q, dict) or not text(q.get('id')) or q['id'] in ids or not text(q.get('question'), 4000):
                        raise ControlError('invalid_question', 400)
                    ids.add(q['id'])
                    options = q.get('options', [])
                    if not isinstance(options, list) or len(options) > 30 or any(not text(o, 1000) for o in options) or len(set(options)) != len(options):
                        raise ControlError('invalid_options', 400)
                    if type(q.get('multiple', False)) is not bool or type(q.get('custom', False)) is not bool:
                        raise ControlError('invalid_question_flags', 400)
                    details=q.get('option_details',{})
                    if not isinstance(details,dict) or set(details)-set(options) or any(not isinstance(v,str) or len(v)>4000 for v in details.values()):
                        raise ControlError('invalid_option_details',400)
                    item['questions'].append({'id': q['id'], 'question': q['question'], 'options': options,
                                              'option_details':details,
                                              'multiple': q.get('multiple', False), 'custom': q.get('custom', False)})
            clean['requests'].append(item)
        key = (row['tool'], row['session_id'])
        if key in result:
            raise ControlError('duplicate_session', 400)
        result[key] = clean
    return result


def validate_action(session, action):
    if not isinstance(action, dict):
        raise ControlError('invalid_action', 400)
    kind = action.get('kind')
    if kind == 'policy':
        policy = session.get('policy')
        if not policy or action.get('revision') != policy['revision']:
            raise ControlError('policy_changed')
        if (set(action) != {'kind','revision','value'}
                or action.get('value') not in [o['value'] for o in policy['options']]):
            raise ControlError('invalid_policy',400)
        return
    if kind == 'create':
        if (set(action) != {'kind', 'text', 'revision'} or not text(action.get('text'), 16000)
                or not action['text'].strip()):
            raise ControlError('invalid_message', 400)
        capability = session.get('create')
        if not capability or action.get('revision') != capability['revision']:
            raise ControlError('create_unavailable')
        return
    if kind == 'reply':
        if set(action) != {'kind', 'text'} or not text(action.get('text'), 16000) or not action['text'].strip():
            raise ControlError('invalid_message', 400)
        if not session['reply']:
            raise ControlError('reply_unavailable')
        return
    if kind not in ('answer', 'approval') or not text(action.get('request_id')) or not text(action.get('revision')):
        raise ControlError('invalid_action', 400)
    req = next((r for r in session['requests'] if r['id'] == action['request_id'] and r['revision'] == action['revision']), None)
    if not req:
        raise ControlError('request_expired')
    if kind == 'approval':
        if req['kind'] != 'approval' or set(action) != {'kind', 'request_id', 'revision', 'decision'} or action.get('decision') not in req['choices']:
            raise ControlError('invalid_decision', 400)
    else:
        answers = action.get('answers')
        if req['kind'] != 'question' or set(action) != {'kind', 'request_id', 'revision', 'answers'} or not isinstance(answers, dict):
            raise ControlError('invalid_answers', 400)
        if set(answers) != {q['id'] for q in req['questions']}:
            raise ControlError('invalid_answers', 400)
        for q in req['questions']:
            a = answers[q['id']]
            if not isinstance(a, dict) or set(a) != {'selected', 'custom'}:
                raise ControlError('invalid_answer', 400)
            selected, custom = a['selected'], a['custom']
            if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected) or len(selected) != len(set(selected)) or set(selected) - set(q['options']):
                raise ControlError('invalid_selection', 400)
            if not isinstance(custom, str) or len(custom) > 4000 or '\x00' in custom or (custom and not q['custom']):
                raise ControlError('invalid_custom_answer', 400)
            if not q['multiple'] and len(selected) + bool(custom.strip()) > 1:
                raise ControlError('invalid_selection', 400)
            if not selected and not custom.strip():
                raise ControlError('empty_answer', 400)


class Broker:
    def __init__(self, directory, clock=time.time):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(directory) / 'control.sqlite3')
        self.lock = threading.RLock()
        self.clock, self.live = clock, {}
        from .history_queue import HistoryReads
        self.history_reads = HistoryReads(clock)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS control_settings (device TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, device TEXT NOT NULL, tool TEXT NOT NULL, session TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, action TEXT, state TEXT NOT NULL, worker TEXT,
                    created REAL NOT NULL, deadline REAL NOT NULL, detail TEXT NOT NULL DEFAULT '');
            ''')
            if 'result' not in {r[1] for r in db.execute('PRAGMA table_info(commands)')}:
                db.execute('ALTER TABLE commands ADD COLUMN result TEXT')
            # Never replay anything that might already have reached the native app.
            db.execute("UPDATE commands SET state='unknown',action=NULL,detail='服务重启，操作结果未知，请检查原应用' WHERE state='dispatched'")
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def provision(self, device):
        if not text(device):
            raise ControlError('invalid_device', 400)
        token = secrets.token_urlsafe(32)
        with self.lock, self.db() as db:
            db.execute('INSERT OR REPLACE INTO devices VALUES (?,?)', (device, hashlib.sha256(token.encode()).hexdigest()))
            self._revoke_pending(db, device)
            self.live.pop(device, None)
        return token

    def _revoke_pending(self, db, device):
        db.execute("UPDATE commands SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'unknown' END,action=NULL,detail='设备控制连接已撤销' WHERE device=? AND state IN ('queued','dispatched')", (device,))

    def revoke(self, device):
        with self.lock, self.db() as db:
            db.execute('DELETE FROM devices WHERE id=?', (device,))
            self._revoke_pending(db, device)
            self.live.pop(device, None)

    def authenticate(self, device, token):
        if not isinstance(device, str) or not isinstance(token, str) or len(token) > 200:
            return False
        with self.db() as db:
            row = db.execute('SELECT token_hash FROM devices WHERE id=?', (device,)).fetchone()
        return bool(row and hmac.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest()))

    def _enabled(self, db, device):
        row = db.execute('SELECT enabled FROM control_settings WHERE device=?', (device,)).fetchone()
        return row is None or bool(row[0])

    def _active_workers(self, device):
        return {worker: live for worker, live in self.live.get(device, {}).items()
                if self.clock() - live['at'] < TTL}

    def _owner(self, device, key):
        for worker, live in self._active_workers(device).items():
            if key in live['sessions']:
                return worker, live['sessions'][key]
        return None, None

    def device_status(self, device):
        with self.lock, self.db() as db:
            workers = self._active_workers(device)
            sessions = [cap for live in workers.values() for cap in live['sessions'].values()]
            return {'device_id':device, 'configured':db.execute('SELECT 1 FROM devices WHERE id=?',(device,)).fetchone() is not None,
                    'enabled':self._enabled(db,device), 'connected':bool(workers),
                    'last_seen':max((w['at'] for w in self.live.get(device,{}).values()),default=None), 'sessions':len(sessions),
                    'harnesses':{tool:sum(s['tool']==tool for s in sessions) for tool in sorted(TOOLS)}}

    def pending_states(self):
        """Fresh native waits for display only; never grant action capabilities."""
        with self.lock:
            result = {}
            for device in self.live:
                for live in self._active_workers(device).values():
                    for (tool,sid),cap in live['sessions'].items():
                        kinds = {r['kind'] for r in cap['requests']}
                        if kinds:
                            result[(device,tool,sid)] = 'waiting_approval' if 'approval' in kinds else 'waiting_input'
            return result

    def set_enabled(self, device, enabled):
        if type(enabled) is not bool:
            raise ControlError('invalid_control_setting',400)
        with self.lock, self.db() as db:
            if not db.execute('SELECT 1 FROM devices WHERE id=?',(device,)).fetchone():
                raise ControlError('control_not_configured',409)
            db.execute('INSERT OR REPLACE INTO control_settings VALUES (?,?)',(device,int(enabled)))
            if not enabled:
                db.execute("UPDATE commands SET state='cancelled',action=NULL,detail='已暂停设备交互，未派发的操作已取消' WHERE device=? AND state='queued'",(device,))
        return self.device_status(device)

    def _expire(self, db):
        now = self.clock()
        db.execute("UPDATE commands SET state=CASE WHEN state='queued' THEN 'expired' ELSE 'unknown' END,action=NULL,detail='操作超时；请检查原应用，系统不会自动重发' WHERE state IN ('queued','dispatched') AND deadline<?", (now,))
        db.execute("DELETE FROM commands WHERE created<? AND state NOT IN ('queued','dispatched')", (now - 7*86400,))

    def status(self, device, tool, session):
        with self.lock, self.db() as db:
            self._expire(db)
            _, cap = self._owner(device, (tool, session))
            enabled = self._enabled(db, device)
            rows = db.execute('SELECT id,state,detail,created,result FROM commands WHERE device=? AND tool=? AND session=? ORDER BY created DESC LIMIT 5', (device, tool, session)).fetchall()
            commands = []
            for row in rows:
                item = dict(row)
                result = item.pop('result')
                if result:
                    item['result'] = json.loads(result)
                commands.append(item)
            return {'connected': bool(cap) and enabled, 'capability': cap if enabled else None,
                    'reason': '此设备的面板交互已暂停，可在设置中恢复' if not enabled else '' if cap else '此会话尚未连接原应用的交互接口', 'commands': commands}

    def submit(self, device, tool, session, command_id, action):
        try:
            if str(uuid.UUID(command_id)) != command_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ControlError('invalid_command_id', 400)
        fingerprint = hashlib.sha256(encoded([device, tool, session, action]).encode()).hexdigest()
        with self.lock, self.db() as db:
            self._expire(db)
            row = db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
            if row:
                if row['fingerprint'] != fingerprint:
                    raise ControlError('command_id_conflict')
                return {'id': row['id'], 'state': row['state'], 'detail': row['detail']}
            if not self._enabled(db, device):
                raise ControlError('control_paused')
            worker, cap = self._owner(device, (tool, session))
            if not cap:
                raise ControlError('control_offline')
            validate_action(cap, action)
            if db.execute("SELECT 1 FROM commands WHERE device=? AND tool=? AND session=? AND state IN ('queued','dispatched')", (device, tool, session)).fetchone():
                raise ControlError('operation_in_progress')
            now = self.clock()
            db.execute('INSERT INTO commands(id,device,tool,session,fingerprint,action,state,created,deadline,worker) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (command_id, device, tool, session, fingerprint, encoded(action), 'queued', now, now+60, worker))
            return {'id': command_id, 'state': 'queued', 'detail': ''}

    def history(self, device, tool, session, cursor=None):
        with self.lock, self.db() as db:
            if not self._enabled(db, device):
                raise ControlError('control_paused')
            worker, cap = self._owner(device, (tool, session))
            if not cap or not cap.get('history'):
                raise ControlError('history_unavailable')
            return self.history_reads.request(device, tool, session, cursor, worker)

    def exchange(self, device, worker, sessions, receipts, read_results=None):
        if not text(worker) or not isinstance(receipts, list) or len(receipts) > 20:
            raise ControlError('invalid_exchange', 400)
        caps = normalize_sessions(sessions)
        for r in receipts:
            if not isinstance(r, dict) or not text(r.get('id')) or r.get('state') not in ('delivered', 'resolved', 'rejected', 'unknown') or not isinstance(r.get('detail', ''), str):
                raise ControlError('invalid_receipt', 400)
            if 'result' in r:
                result = r['result']
                try:
                    if (not isinstance(result, dict) or set(result) != {'tool', 'session_id'}
                            or result['tool'] != 'dsh' or not result['session_id'].startswith('session-')
                            or str(uuid.UUID(result['session_id'][8:])) != result['session_id'][8:]):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise ControlError('invalid_receipt', 400) from None
        with self.lock, self.db() as db:
            self._expire(db)
            workers = self._active_workers(device)
            # Separate Harness processes on one device keep independent leases.
            # An exact native session still has only one command recipient.
            for other, live in workers.items():
                if other != worker and caps.keys() & live['sessions'].keys():
                    raise ControlError('device_already_connected')
            if worker not in workers and len(workers) >= 16:
                raise ControlError('too_many_device_workers')
            workers[worker] = {'at': self.clock(), 'sessions': caps}
            self.live[device] = workers
            accepted = []
            for r in receipts:
                row = db.execute('SELECT state,tool,action,result FROM commands WHERE id=? AND device=? AND worker=?', (r['id'], device, worker)).fetchone()
                if row and row['state'] in ('dispatched', 'delivered', 'resolved', 'rejected', 'unknown'):
                    if 'result' in r and row['tool'] == 'dsh' and row['action'] and json.loads(row['action']).get('kind') == 'create':
                        db.execute('UPDATE commands SET result=? WHERE id=?', (encoded(r['result']),r['id']))
                    db.execute("UPDATE commands SET state=?,action=NULL,detail=? WHERE id=? AND state IN ('dispatched','unknown')", (r['state'], r.get('detail', '')[:1000], r['id']))
                    accepted.append(r['id'])
                elif not row:
                    # The receipt may outlive seven-day queue retention. There is
                    # no command left to mutate; let this device discard it.
                    accepted.append(r['id'])
            read_response = {}
            if read_results is not None:
                enabled_caps = caps if self._enabled(db, device) else {}
                reads, read_accepted = self.history_reads.exchange(device, worker, enabled_caps, read_results)
                read_response.update(reads=reads, read_receipts=read_accepted)
            queued = db.execute("SELECT * FROM commands WHERE device=? AND state='queued' ORDER BY created", (device,)).fetchall()
            command = None
            for row in queued:
                cap = caps.get((row['tool'], row['session']))
                if row['worker'] != worker and cap is None:
                    continue  # Another live Harness owns this operation.
                try:
                    if not self._enabled(db, device):
                        raise ControlError('control_paused')
                    if row['worker'] != worker or cap is None:
                        raise ControlError('native_connection_changed')
                    action = json.loads(row['action'])
                    validate_action(cap, action)
                except ControlError as e:
                    db.execute("UPDATE commands SET state='rejected',action=NULL,detail=? WHERE id=?", (e.code, row['id']))
                    continue
                # Persist dispatch before returning the command. A dropped HTTP
                # response becomes unknown, never a second native invocation.
                db.execute("UPDATE commands SET state='dispatched',deadline=? WHERE id=?", (self.clock()+120, row['id']))
                command = {'id': row['id'], 'tool': row['tool'], 'session_id': row['session'], 'action': action}
                break
            result = {'command': command, 'receipts': accepted}
            result.update(read_response)
            return result

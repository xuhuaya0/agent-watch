"""Short-lived, memory-only device history reads, separate from native writes."""
import json
import time
import uuid
from .broker import ControlError


class HistoryReads:
    def __init__(self, clock=time.time):
        self.clock, self.jobs = clock, {}

    def prune(self):
        now = self.clock()
        self.jobs = {k: v for k, v in self.jobs.items() if now - v['created'] < 45
                     and not (v['key'][-1] and 'completed' in v and now - v['completed'] > 3)}

    def request(self, device, tool, sid, cursor, worker, attachment=None):
        from .attachments import ID
        if attachment is not None and (not isinstance(attachment, str) or not ID.fullmatch(attachment) or cursor is not None):
            raise ControlError('invalid_attachment', 400)
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 160):
            raise ControlError('invalid_history_cursor', 400)
        self.prune()
        key = (device, tool, sid, cursor, worker, attachment)
        row = next((v for v in self.jobs.values() if v['key'] == key), None)
        if row is not None and 'completed' in row and self.clock() - row['completed'] > 3:
            self.jobs.pop(row['id'], None)
            row = None
        if row is None:
            if len(self.jobs) >= 32 or (attachment and sum(bool(v['key'][-1]) for v in self.jobs.values()) >= 4):
                raise ControlError('history_busy', 429)
            row = {'id': str(uuid.uuid4()), 'key': key, 'created': self.clock(), 'claimed': False}
            self.jobs[row['id']] = row
        if 'result' in row:
            return {'ready': True, **row['result']}
        if self.clock() - row['created'] > 25:
            return {'ready': True, 'error': 'history_timeout'}
        return {'ready': False}

    def exchange(self, device, worker, caps, results):
        self.prune()
        if not isinstance(results, list) or len(results) > 4:
            raise ControlError('invalid_history_results', 400)
        accepted = []
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                raise ControlError('invalid_history_result', 400)
            row = self.jobs.get(item['id'])
            if row is None:
                accepted.append(item['id']); continue
            d, tool, sid, _, w, attachment = row['key']
            if (d, w) != (device, worker) or not row['claimed']:
                continue
            cap = caps.get((tool, sid))
            result = item.get('result')
            if not cap or not cap.get('history'):
                result = {'error': 'native_session_unavailable'}
            limit = 2900000 if attachment else 1500000
            if not isinstance(result, dict) or len(json.dumps(result).encode()) > limit:
                raise ControlError('invalid_history_result', 400)
            if 'error' in result:
                result = {'error': str(result['error'])[:160]}
            elif attachment:
                from .attachments import validate_media
                validate_media(result.get('media'))
                result = {'media': result['media']}
            else:
                messages = result.get('messages')
                if not isinstance(messages, list) or len(messages) > 30 or any(
                    not isinstance(m, dict) or m.get('role') not in ('user', 'assistant', 'tool') or
                    not isinstance(m.get('text'), str) or len(m['text'].encode()) > 48000 or
                    not isinstance(m.get('id'), str) for m in messages):
                    raise ControlError('invalid_history_result', 400)
                cursor = result.get('next_cursor')
                if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 160):
                    raise ControlError('invalid_history_result', 400)
                from .attachments import clean_descriptors
                result = {'messages': [{**{k: m[k] for k in ('id','role','text','label','timestamp','truncated') if k in m},
                                       **({'attachments': clean_descriptors(m['attachments'])} if 'attachments' in m else {}),
                                       **({'phase': 'commentary'} if m.get('phase') == 'commentary' and m['role'] == 'assistant' else {})}
                                       for m in messages], 'next_cursor': cursor,
                          'source': {'codex':'codex_rollout', 'antigravity':'antigravity_transcript', 'dsh':'dsh_session'}[tool]}
            row['result'] = result
            row['completed'] = self.clock()
            accepted.append(item['id'])
        reads = []
        for row in self.jobs.values():
            d, tool, sid, cursor, w, attachment = row['key']
            if (d, w) == (device, worker) and not row['claimed'] and self.clock() - row['created'] <= 25:
                cap = caps.get((tool, sid))
                if not cap or not cap.get('history'):
                    row['result'] = {'error': 'native_session_unavailable'}; continue
                row['claimed'] = True
                reads.append({'id': row['id'], 'tool': tool, 'session_id': sid,
                              **({'attachment_id': attachment} if attachment else {'cursor': cursor})})
                break
        return reads, accepted

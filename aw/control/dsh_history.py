"""Cold-safe bounded history via the native session/page read interface."""
import datetime
import hashlib
import json
from .broker import ControlError
from .history import bounded_message, PAGE_ITEMS, PAGE_BYTES
from ..adapters.dsh import assistant_phase


def content_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(c.get('text', '') if c.get('type') == 'text' else '[图片附件]'
                     for c in content if isinstance(c, dict) and
                     c.get('type') in ('text', 'image') and isinstance(c.get('text', ''), str))


def message(event):
    # Replacement events represent context rewrites/compaction, not new chat
    # messages. Keep the chronological append record, without showing those
    # internal summaries as another reply.
    if event.get('surfaceOp', 'append') != 'append':
        return None
    kind, data = event.get('type'), event.get('data')
    if not isinstance(data, dict):
        return None
    if kind == 'user/message':
        if (data.get('source') or {}).get('kind', 'user') != 'user':
            return None
        role, label, body = 'user', '你', content_text(data.get('content'))
    elif kind == 'assistant/message':
        role, label = 'assistant', 'DeepSeek'
        body = content_text((data.get('message') or {}).get('content'))
    elif kind == 'tool/call':
        role, label = 'tool', str(data.get('name') or '工具调用')[:200]
        body = data.get('arguments', '')
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False, indent=2)
    elif kind == 'tool/result':
        role, label = 'tool', '工具结果'
        body = content_text((data.get('message') or {}).get('content'))
    else:
        return None
    timestamp = ''
    if isinstance(event.get('time'), (int, float)):
        try:
            timestamp = datetime.datetime.fromtimestamp(event['time']/1000, datetime.timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    result = bounded_message(event['seq'], role, label, body, timestamp)
    if result and role == 'assistant':
        phase = assistant_phase(data.get('message') or {})
        if phase:
            result['phase'] = phase
    return result


def read_history(provider, sid, cursor=None):
    if not provider.session_snapshot(sid):
        raise ControlError('native_session_unavailable')
    gateway = provider.gateway
    projection = gateway.call('session/projections', {'request': {'sessionId': sid}})
    current = projection.get('asOfSeq')
    if type(current) is not int or not -1 <= current <= 2**53-1:
        raise ControlError('history_unavailable')
    stamp = hashlib.sha256(sid.encode()).hexdigest()[:20]
    through, before = current, current + 1
    if cursor is not None:
        try:
            prefix, through_raw, before_raw = cursor.split(':')
            through, before = int(through_raw), int(before_raw)
            if prefix != stamp or not 0 <= before <= through + 1 or not -1 <= through <= current:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ControlError('history_cursor_expired') from None
    page = gateway.call('session/page', {'request': {'address': {'kind': 'session', 'sessionId': sid},
                         'throughSeq': through, 'beforeSeq': before, 'maxMessages': 15}})
    records = page.get('records')
    if not isinstance(records, list) or type(page.get('hasMore')) is not bool:
        raise ControlError('history_unavailable')
    items, size, cut, last_seq, trimmed = [], 0, before, before, False
    for record in reversed(records):
        if not isinstance(record, dict) or record.get('type') != 'event':
            raise ControlError('history_unavailable')
        event = record.get('event')
        if not isinstance(event, dict) or type(event.get('seq')) is not int or not 0 <= event['seq'] < last_seq:
            raise ControlError('history_unavailable')
        last_seq = event['seq']
        item = message(event)
        cost = len(json.dumps(item, ensure_ascii=False).encode()) if item else 0
        if items and (len(items) >= PAGE_ITEMS or size + cost > PAGE_BYTES):
            trimmed = True
            break
        cut = event['seq']
        if item:
            items.append(item); size += cost
    if page['hasMore'] and (not records or cut >= before):
        raise ControlError('history_unavailable')
    # Native archive/scope changes revoke a read even if they occurred mid-page.
    if not provider.session_snapshot(sid) or gateway is not provider.gateway:
        raise ControlError('native_session_unavailable')
    return {'messages': list(reversed(items)), 'source': 'dsh_session',
            'next_cursor': f'{stamp}:{through}:{cut}' if cut > 0 and (trimmed or page['hasMore']) else None}

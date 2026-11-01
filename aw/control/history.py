"""Bounded, on-demand reads of the original local Codex history. No engine starts."""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time

from .broker import ControlError
from ..adapters.codex_titles import user_text

PAGE_ITEMS = 30
PAGE_BYTES = 180000
SCAN_BYTES = 8 * 1024 * 1024


def database_paths(root):
    paths = [p for d in (root, root/'sqlite') for p in d.glob('state_*.sqlite')
             if re.fullmatch(r'state_\d+\.sqlite', p.name)]
    return sorted(paths, key=lambda p: (-int(p.stem.split('_')[1]), p.parent != root))


def reverse_lines(stream, end):
    """Yield (start byte offset, raw line) backwards, without loading the file."""
    pos, tail = end, b''
    while pos:
        start = max(0, pos - 65536)
        stream.seek(start)
        data = stream.read(pos - start) + tail
        chunks = data.split(b'\n')
        offset = start + len(data)
        for line in reversed(chunks[1:]):
            offset -= len(line)
            if line:
                yield offset, line
            offset -= 1
        tail, pos = chunks[0], start
        if len(tail) > SCAN_BYTES:
            raise ControlError('history_record_too_large')
    if tail:
        yield 0, tail


def message(row, offset):
    p = row.get('payload', {})
    if row.get('type') != 'response_item' or not isinstance(p, dict):
        return None
    kind, role = p.get('type'), p.get('role')
    if kind == 'message' and role in ('user', 'assistant'):
        if p.get('phase') == 'analysis' or p.get('channel') == 'analysis':
            return None
        content = p.get('content', [])
        if isinstance(content, str):
            body = content
        elif isinstance(content, list):
            body = '\n'.join(c.get('text', '') if c.get('type') in ('input_text', 'output_text', 'text')
                             else '[图片附件]' if c.get('type') in ('input_image', 'image') else ''
                             for c in content if isinstance(c, dict))
        else:
            return None
        body = user_text(body) if role == 'user' else body.strip()
        label = '你' if role == 'user' else 'Codex'
    elif kind in ('function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output'):
        role = 'tool'
        label = str(p.get('name') or ('工具结果' if kind.endswith('output') else '工具调用'))[:200]
        body = p.get('output') if kind.endswith('output') else p.get('arguments', p.get('input', ''))
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
    else:
        return None
    if not body:
        return None
    # Bound each item while preserving an explicit truncation indicator.
    raw = body.encode('utf-8')
    body = raw[:48000].decode('utf-8', errors='ignore')
    return {'id': str(offset), 'role': role, 'label': label, 'text': body,
            'timestamp': str(row.get('timestamp') or '')[:80], 'truncated': len(raw) > 48000}


@contextmanager
def open_rollout(path):
    if path.suffix != '.zst':
        with path.open('rb') as stream:
            yield stream
        return
    try:
        import zstandard
    except ImportError:
        raise ControlError('history_compression_unavailable')
    # Private temporary storage is removed on close, including on decoding errors.
    # Bound the decompressed size and time; never expand into Codex's sessions tree.
    with path.open('rb') as raw, zstandard.ZstdDecompressor().stream_reader(raw) as decoder, tempfile.SpooledTemporaryFile(max_size=8*1024*1024) as stream:
        size, deadline = 0, time.monotonic()+8
        while True:
            block = decoder.read(1024*1024)
            if not block:
                break
            size += len(block)
            if size > 512*1024*1024 or time.monotonic() > deadline:
                raise ControlError('history_too_large')
            stream.write(block)
        stream.seek(0)
        yield stream


class CodexHistory:
    source = 'codex_rollout'
    message = staticmethod(message)

    def __init__(self, root, scope):
        self.root = Path(root).expanduser().resolve()
        self.scope = scope

    def path(self, sid):
        if sid not in self.scope():
            raise ControlError('native_session_unavailable')
        for path in database_paths(self.root):
            try:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.2)) as db:
                    row = db.execute('SELECT rollout_path,archived FROM threads WHERE id=?', (sid,)).fetchone()
                if row:
                    if row[1]:
                        raise ControlError('native_session_archived')
                    target = Path(row[0]).expanduser().resolve()
                    if not target.exists() and target.suffix == '.jsonl':
                        target = target.with_suffix('.jsonl.zst').resolve()
                    if not target.is_relative_to((self.root/'sessions').resolve()) or not target.name.endswith(('.jsonl', '.jsonl.zst')):
                        raise ControlError('history_path_unavailable')
                    return target
            except sqlite3.Error:
                continue
        raise ControlError('history_unavailable')

    def read(self, sid, cursor=None):
        path = self.path(sid)
        with open_rollout(path) as stream:
            st = path.stat()
            stamp = hashlib.sha256(f'{sid}:{path}:{st.st_dev}:{st.st_ino}'.encode()).hexdigest()[:20]
            file_size = stream.seek(0, 2)
            end = file_size
            if cursor is not None:
                try:
                    prefix, value = cursor.split(':')
                    end = int(value)
                    if prefix != stamp or not 0 <= end <= file_size:
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise ControlError('history_cursor_expired')
            items, size, next_offset = [], 0, None
            for offset, raw in reverse_lines(stream, end):
                try:
                    row = json.loads(raw)
                    item = self.message(row, offset) if isinstance(row, dict) else None
                except (ValueError, TypeError):
                    item = None
                cost = len(json.dumps(item, ensure_ascii=False).encode()) if item else 0
                if items and (len(items) >= PAGE_ITEMS or size + cost > PAGE_BYTES):
                    next_offset = offset + len(raw) + 1
                    break
                if item:
                    items.append(item); size += cost
                if end - offset >= SCAN_BYTES:
                    next_offset = offset
                    break
            # Recheck scope/archive after reading; do not return a newly archived chat.
            self.path(sid)
            return {'messages': list(reversed(items)),
                    'next_cursor': f'{stamp}:{next_offset}' if next_offset else None,
                    'source': self.source}


def bounded_message(identity, role, label, body, timestamp=''):
    if not isinstance(body, str) or not body.strip():
        return None
    raw = body.encode('utf-8')
    return {'id': str(identity), 'role': role, 'label': label,
            'text': raw[:48000].decode('utf-8', errors='ignore'),
            'timestamp': str(timestamp or '')[:80], 'truncated': len(raw) > 48000}


class AntigravityHistory(CodexHistory):
    """Read existing transcript pages, never request native rehydration."""
    source = 'antigravity_transcript'

    def path(self, sid):
        from .sessions import canonical_id
        if not canonical_id(sid) or sid not in self.scope():
            raise ControlError('native_session_unavailable')
        target = (self.root/'brain'/sid/'.system_generated/logs/transcript.jsonl').resolve()
        if not target.is_relative_to(self.root/'brain'/sid):
            raise ControlError('history_path_unavailable')
        if not target.is_file():
            raise ControlError('history_unavailable')
        return target

    @staticmethod
    def message(row, offset):
        from ..adapters.antigravity import _user_text, _NOTIFICATION
        kind, body = row.get('type'), row.get('content', '')
        if kind == 'USER_INPUT':
            role, label, body = 'user', '你', _user_text(body) if isinstance(body, str) else ''
        elif kind == 'PLANNER_RESPONSE':
            role, label = 'assistant', 'Antigravity'
            calls = row.get('tool_calls')
            if calls:
                # Keep a tool-bearing step together without inventing events.
                role, label = 'tool', '工具调用'
                body = ((body + '\n\n') if isinstance(body, str) and body else '') + json.dumps(calls, ensure_ascii=False, indent=2)
        elif kind in ('GENERIC', 'ERROR_MESSAGE'):
            role, label = 'tool', '工具结果' if kind == 'GENERIC' else '错误'
        elif kind == 'SYSTEM_MESSAGE' and isinstance(body, str):
            notification = _NOTIFICATION.search(body)
            if not notification:
                return None
            role, label, body = 'user', '外部通知', notification.group(1)
        else:
            return None
        return bounded_message(offset, role, label, body, row.get('created_at'))

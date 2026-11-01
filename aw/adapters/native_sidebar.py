"""Read native sidebar metadata. Never write Harness databases or preferences."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


def unknown_pin():
    return {'known': False, 'pinned': None, 'writable': False, 'source': 'unavailable'}


def codex_sidebar(root):
    root = Path(root).expanduser().resolve()
    pinned = None
    try:
        data = json.loads((root / '.codex-global-state.json').read_text(encoding='utf-8'))
        value = data.get('pinned-thread-ids')
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            pinned = set(value)  # A present empty list is an authoritative unpin.
    except (OSError, ValueError, AttributeError):
        pass
    indexed = set()
    try:
        with (root / 'session_index.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and isinstance(row.get('id'), str):
                        indexed.add(row['id'])
                except ValueError:
                    continue
    except OSError:
        pass
    result = {}
    paths = [p for directory in (root, root/'sqlite') for p in directory.glob('state_*.sqlite')
             if re.fullmatch(r'state_\d+\.sqlite', p.name)]
    paths.sort(key=lambda p: (-int(p.stem.split('_')[1]), p.parent != root))
    for path in paths:
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.1)) as con:
                columns = {r[1] for r in con.execute('PRAGMA table_info(threads)')}
                fields = [f for f in ('id','name','title','cwd','source','thread_source','archived',
                                     'is_pinned','section_position','updated_at','updated_at_ms',
                                     'first_user_message','preview') if f in columns]
                if 'id' not in fields:
                    continue
                for values in con.execute('SELECT '+','.join(fields)+' FROM threads'):
                    row = dict(zip(fields, values)); tid = row['id']
                    if tid in result:
                        continue
                    archived = bool(row.get('archived'))
                    pin = unknown_pin()
                    if pinned is not None or 'is_pinned' in row:
                        pin = {'known': True, 'pinned': not archived and (tid in pinned if pinned is not None else bool(row['is_pinned'])),
                               'writable': False, 'source': 'codex_global_state' if pinned is not None else 'codex_database'}
                        if pinned is None and isinstance(row.get('section_position'), int):
                            pin['order'] = row['section_position']
                    source = row.get('source', '')
                    try: source = json.loads(source) if source.startswith('{') else source
                    except (ValueError, AttributeError): pass
                    internal = row.get('thread_source') in ('subagent','guardian_review') or isinstance(source, dict) and 'subagent' in source
                    # Old DBs can hold thousands of initialization-only CLI rows.
                    visible = not internal and (row.get('thread_source') == 'user' or source in ('vscode','desktop') or tid in indexed or
                                                bool(row.get('name')) or (pinned is not None and tid in pinned))
                    row.update(native_pin=pin, native_archived=archived, visible=visible)
                    result[tid] = row
        except (sqlite3.Error, OSError):
            continue
    return result, pinned


def codex_pin(tid, metadata, legacy_pins):
    if tid in metadata:
        return metadata[tid]['native_pin']
    if legacy_pins is not None:
        return {'known': True, 'pinned': tid in legacy_pins, 'writable': False, 'source': 'codex_global_state'}
    return unknown_pin()


def dsh_pins(root):
    try:
        data = json.loads((Path(root)/'storages/workspace.json').read_text(encoding='utf-8'))
        value = data.get('global', {}).get('pinnedSessionIds')
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            return {sid: n for n, sid in enumerate(value)}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def antigravity_storage_path():
    """Electron user-data location; an adapter sidebar_path can override it."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home()/'AppData/Roaming')
    elif sys.platform == 'darwin':
        base = Path.home()/'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    return base/'Antigravity/app_storage.json'


def antigravity_pins(path):
    """The native desktop stores its ordered IDs as a JSON-encoded string."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding='utf-8-sig'))
        value = data.get('pinned_conversations_order')
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            return {sid: n for n, sid in enumerate(value)}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def antigravity_archives(root):
    """Read the native search index's conversation headers, never message chunks.

    Antigravity's own archived search uses chunk_index=-1. Body chunks keep
    archived=0 even for archived chats; aggregating them would resurrect chats.
    Read every time: archiving does not modify transcript.jsonl. Missing indexes
    remain compatible with older installations; an unreadable existing index
    raises so the scanner reports an error and control discovery fails closed.
    """
    path = Path(root).expanduser().resolve() / 'conversation_summaries.db'
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.1)) as con:
        rows = con.execute('SELECT conversation_id, archived FROM conversations_fts WHERE chunk_index=-1')
        result = {}
        for sid, archived in rows:
            if not isinstance(sid, str) or archived not in (0, 1):
                raise ValueError('Invalid Antigravity archive metadata')
            if sid in result and result[sid] != bool(archived):
                raise ValueError('Conflicting Antigravity archive metadata')
            result[sid] = bool(archived)
        return result

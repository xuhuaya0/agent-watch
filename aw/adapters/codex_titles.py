"""Read Codex display names without modifying its state or requiring Codex APIs."""

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from typing import Dict, Iterable


_CONTEXT = re.compile(
    r'^\s*<(environment_context|external_codex_apps_open_page|in-app-browser-context|'
    r'user_instructions)(?:\s[^>]*)?>.*?</\1>\s*', re.S)
_REPLY = re.compile(r'^\s*<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*$', re.S)


def user_text(text: str) -> str:
    """Remove known client envelopes; preserve the user's actual request text."""
    text = text.strip()
    while True:
        cleaned, count = _CONTEXT.subn('', text, count=1)
        if not count:
            break
        text = cleaned
    if text.startswith('# AGENTS.md instructions for '):
        return ''
    if text.startswith('# Files mentioned by the user:'):
        marker = re.search(r'^#{1,2} My request:\s*$', text, re.M)
        text = text[marker.end():].strip() if marker else ''
    reply = _REPLY.fullmatch(text)
    if reply:
        try:
            answers = json.loads(reply.group(1))
            return '\n'.join(a['answer'].strip() for a in answers
                             if isinstance(a, dict) and isinstance(a.get('answer'), str)).strip() if isinstance(answers, list) else ''
        except ValueError:
            return ''
    return text.strip()


def title_text(value) -> str:
    return ' '.join(value.split()) if isinstance(value, str) else ''


def read_titles(root: str, thread_ids: Iterable[str]) -> Dict[str, Dict[str, str]]:
    """Prefer explicit names, then the display-name index, then older DB titles.

    SQLite schema versions differ between Codex clients. Only existing databases
    with supported columns are queried, in read-only mode with a bounded timeout.
    An unavailable/locked database must not stop rollout collection.
    """
    root = Path(root).expanduser().resolve()
    ids = set(thread_ids)
    if not ids:
        return {}
    indexed = {}
    try:
        with (root/'session_index.jsonl').open(encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] not in ids:
                    continue
                name = title_text(row.get('thread_name'))
                if name:
                    # The index is append-only: a later entry is a rename.
                    indexed[row['id']] = name
    except OSError:
        pass

    names, titles = {}, {}
    # Prefer the highest schema version. The root DB is the default; some
    # installations place it in a sqlite subdirectory instead.
    paths = []
    for directory in (root, root/'sqlite'):
        paths.extend(directory.glob('state_*.sqlite'))
    def version(path):
        match = re.fullmatch(r'state_(\d+)\.sqlite', path.name)
        return int(match.group(1)) if match else -1
    for path in sorted(paths, key=lambda p: (-version(p), p.parent != root)):
        if version(path) < 0:
            continue
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=0.1)) as con:
                columns = {r[1] for r in con.execute('PRAGMA table_info(threads)')}
                fields = [c for c in ('id', 'name', 'title') if c in columns]
                if 'id' not in fields or len(fields) == 1:
                    continue
                requested = sorted(ids)
                for start in range(0, len(requested), 500):
                    batch = requested[start:start+500]
                    query = 'SELECT '+','.join(fields)+' FROM threads WHERE id IN ('+','.join('?' for _ in batch)+')'
                    for values in con.execute(query, batch):
                        row = dict(zip(fields, values))
                        name = title_text(row.get('name'))
                        title = title_text(user_text(row['title'])) if isinstance(row.get('title'), str) else ''
                        if name:
                            names.setdefault(row['id'], name)
                        if title:
                            titles.setdefault(row['id'], title)
        except (sqlite3.Error, OSError):
            continue

    out = {}
    for tid in ids:
        for source, mapping in (('codex_name', names), ('session_index', indexed), ('codex_title', titles)):
            if tid in mapping:
                out[tid] = {'title': mapping[tid], 'source': source}
                break
    return out

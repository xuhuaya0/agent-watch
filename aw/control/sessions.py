"""Local, explicit Antigravity control scope; discovery does not send messages."""
from pathlib import Path
import json
import uuid
import sqlite3
from contextlib import closing
from ..adapters.native_sidebar import antigravity_archives


def canonical_id(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


class SessionScope:
    def __init__(self, config_path):
        self.config_path = Path(config_path)

    def allows_project_permissions(self):
        try:
            settings = json.loads(self.config_path.read_text(encoding='utf-8'))['antigravity']
            return settings.get('scope') == 'all' and settings.get('native_project_permissions') is True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False

    def __call__(self):
        # Re-read just the scope: changing a Hub or token still requires a new
        # worker binding. Invalid/partially written config fails closed.
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))['antigravity']
            root = Path(config.get('data_root', '~/.gemini/antigravity')).expanduser().resolve()
            archives = antigravity_archives(root)
            mode = config.get('scope', 'selected')
            if mode == 'selected':
                ids = config.get('session_ids', [])
                if not isinstance(ids, list) or any(not canonical_id(s) for s in ids):
                    return []
                return [sid for sid in dict.fromkeys(ids) if archives.get(sid) is not True]
            if mode != 'all':
                return []
            brain = root / 'brain'
            rows = []
            for entry in brain.iterdir():
                if archives.get(entry.name) is True:
                    continue
                if not canonical_id(entry.name) or entry.is_symlink() or not entry.is_dir():
                    continue
                log = entry / '.system_generated' / 'logs' / 'transcript.jsonl'
                if not log.is_file() or not log.resolve().is_relative_to(root):
                    continue
                rows.append((log.stat().st_mtime, entry.name))
            # Most recent conversations are probed first, but no age-based
            # filtering silently removes older conversations from this scope.
            return [sid for _, sid in sorted(rows, reverse=True)]
        except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
            return []


class CodexScope:
    def __init__(self, config_path):
        self.config_path = Path(config_path)

    def __call__(self):
        return list(self._catalog())

    def creation_directories(self):
        return self._catalog(all_only=True)

    def _catalog(self, all_only=False):
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))['codex']
            mode = config.get('scope', 'selected')
            if mode == 'selected' and not all_only:
                ids = config.get('session_ids', [])
                if isinstance(ids, list) and all(canonical_id(s) for s in ids):
                    return dict.fromkeys(ids)
            if mode != 'all':
                return {}
            root = Path(config.get('data_root', '~/.codex')).expanduser().resolve()
            paths = sorted(root.glob('state_*.sqlite'), key=lambda p: int(p.stem.split('_')[-1]), reverse=True)
            for path in paths:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.1)) as db:
                    columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
                    if not {'id', 'source', 'archived', 'updated_at'} <= columns:
                        continue
                    kind = 'thread_source' if 'thread_source' in columns else 'NULL'
                    cwd = 'cwd' if 'cwd' in columns else 'NULL'
                    rows = db.execute(f'SELECT id,source,{kind},{cwd} FROM threads WHERE archived=0 ORDER BY updated_at DESC')
                    result = {}
                    for sid, source, thread_source, directory in rows:
                        try:
                            source = json.loads(source)
                        except (ValueError, TypeError):
                            pass
                        # The desktop database also indexes thousands of short
                        # CLI/internal invocations. They are not desktop chats.
                        if thread_source in ('guardian_review', 'subagent'):
                            continue
                        if source not in ('vscode', 'desktop') and thread_source != 'user':
                            continue
                        if canonical_id(sid) and not (isinstance(source, dict) and 'subagent' in source):
                            result[sid] = directory
                    return result
        except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
            pass
        return {}

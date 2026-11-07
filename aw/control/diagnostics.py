"""Bounded local diagnostics containing categories, never exception text or input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time


LIMIT = 128 * 1024
PHASES = {'snapshot', 'preflight', 'native_execute', 'receipt_prepare',
          'receipt_check', 'receipt_retire', 'exchange', 'recovery'}
EVENTS = {'exception', 'unknown_result', 'confirmed', 'expired', 'capacity', 'restart_unknown'}
SAFE_CODES = {
    'codex_owner_changed', 'codex_owner_unavailable', 'codex_unsafe_socket',
    'codex_desktop_ipc_unavailable', 'codex_shared_connection_unavailable',
    'native_request_rejected', 'native_provider_unavailable', 'native_session_unavailable',
    'native_session_archived', 'native_connection_changed', 'native_action_unsupported',
    'native_message_too_large', 'native_message_starts_with_option', 'reply_unavailable',
    'reply_turn_changed', 'reply_queue_unavailable', 'invalid_message', 'invalid_action',
    'native_queue_write_unconfirmed', 'native_queue_failed',
}


def error_category(error):
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return 'timeout'
    if isinstance(error, (ConnectionError, EOFError)):
        return 'disconnected'
    if isinstance(error, subprocess.CalledProcessError):
        return 'native_process_error'
    if isinstance(error, OSError):
        return 'io_error'
    code = getattr(error, 'code', None)
    if isinstance(code, str):
        return code if code in SAFE_CODES else 'control_error'
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return 'invalid_response'
    return 'unexpected_error'


class Diagnostics:
    def __init__(self, state_file):
        self.path = Path(state_file).with_name('control-diagnostics.jsonl')

    def record(self, event, phase, command=None, error=None):
        # A diagnostic failure must never affect native delivery or its receipt.
        try:
            if event not in EVENTS or phase not in PHASES:
                return
            row = {'at': time.time(), 'event': event, 'phase': phase}
            if error is not None:
                row['category'] = error_category(error)
            if isinstance(command, dict):
                if command.get('tool') in ('codex', 'dsh', 'antigravity'):
                    row['tool'] = command['tool']
                identifier = command.get('id', '')
                if isinstance(identifier, str) and re.fullmatch(r'[0-9a-f-]{36}', identifier):
                    row['command_id'] = identifier
                kind = (command.get('action') or {}).get('kind')
                if kind in ('reply', 'model', 'policy', 'answer', 'approval', 'create'):
                    row['kind'] = kind
            backup = self.path.with_suffix('.jsonl.1')
            if self.path.is_symlink() or backup.is_symlink():
                return
            if self.path.exists() and self.path.stat().st_size >= LIMIT:
                os.replace(self.path, backup)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(str(self.path), flags, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                if os.name != 'nt':
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(row, separators=(',', ':')) + '\n')
        except Exception:
            pass

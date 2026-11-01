"""Use the installed native queue CLI, then wake that queue in the desktop.

Never resume a second Agent engine or write Codex's database directly. A queued
message is accepted native input, not proof that execution has begun/completed.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

from .broker import ControlError
from .sessions import canonical_id


class NativeQueue:
    def __init__(self, executable, root, runner=subprocess.run):
        self.executable, self.root, self.run = str(executable), str(root), runner

    def __call__(self, sid, text):
        if not canonical_id(sid):
            raise ControlError('native_session_unavailable')
        # No model, permission, profile, cwd or resume overrides. CLI owns the
        # queue transaction and message identity. Worker durably marks execution
        # before this call and never repeats it after timeout/unknown outcomes.
        result = self.run([self.executable, 'queue', '--thread', sid, '--message='+text],
                          env={**os.environ, 'CODEX_HOME': self.root},
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, timeout=20, check=False)
        if result.returncode or not re.fullmatch(r'Queued message [0-9a-f-]{36} for thread '+re.escape(sid)+r'\.', result.stdout.strip()):
            raise RuntimeError('Native queue outcome unknown')
        try:
            # Opening a normal conversation URL only displays history. The wake
            # route explicitly resumes an existing queue in the original app.
            self.run(['/usr/bin/open', '-g', 'codex://threads/wake/'+sid],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            return 'queued_unwoken'  # Already queued: never offer a blind retry.
        return 'queued_native'


def native_queue_sender(root, executable=None):
    if sys.platform != 'darwin':
        return None
    candidates = [Path(executable).expanduser()] if executable else [
        base/app/'Contents/Resources/codex-cli/bin/codex'
        for base in (Path('/Applications'), Path.home()/'Applications')
        for app in ('ChatGPT.app', 'Codex.app')]
    for path in candidates:
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        try:
            help_text = subprocess.run([str(path), 'queue', '--help'], capture_output=True,
                                       text=True, timeout=5, check=True).stdout
            if '--thread' in help_text and '--message' in help_text:
                return NativeQueue(path, root)
        except (OSError, subprocess.SubprocessError):
            continue
    return None

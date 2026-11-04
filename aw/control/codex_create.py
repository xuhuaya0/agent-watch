"""Codex creation via an existing shared server, or a macOS desktop draft."""
import hashlib
from pathlib import Path
import plistlib
import subprocess
import sys
from urllib.parse import urlencode, quote

from .broker import ControlError, encoded, validate_action
from .sessions import canonical_id


DESKTOP_DRAFT_BUILDS = frozenset({'24141', '24614'})


def capability(sid, cwd, mode=None):
    if not isinstance(cwd, str) or not cwd or len(cwd) > 2000 or '\x00' in cwd:
        return None
    if not Path(cwd).is_absolute():
        return None
    result = {'cwd': cwd, 'revision': hashlib.sha256(encoded([sid, cwd, mode]).encode()).hexdigest()}
    if mode:
        result['mode'] = mode
    return result


def start(provider, command):
    """Use native defaults and preserve a known destination on uncertain sends."""
    sid = command['session_id']
    action = command['action']
    rows = provider.session_snapshot(sid)
    if len(rows) != 1:
        raise ControlError('native_session_unavailable')
    validate_action(rows[0], action)
    cwd = rows[0]['create']['cwd']
    rpc = provider.rpc
    if provider.scope.creation_directories().get(sid) != cwd:
        raise ControlError('create_unavailable')
    try:
        result = rpc.call('thread/start', {'cwd': cwd, 'threadSource': 'user'})
    except Exception:
        return 'unknown'
    thread = result.get('thread') if isinstance(result, dict) else None
    if not isinstance(thread, dict) or not canonical_id(thread.get('id')) or thread['id'] == sid:
        return 'unknown'
    destination = thread['id']
    outcome = {'state':'unknown', 'created_session_id':destination}
    try:
        if thread.get('cwd') != cwd or result.get('cwd') != cwd or thread.get('ephemeral') is not False:
            return outcome
        source = provider.session_snapshot(sid)
        if rpc is not provider.rpc or len(source) != 1:
            return outcome
        validate_action(source[0], action)
        if provider.scope.creation_directories().get(sid) != cwd:
            return outcome
        with provider.lock:
            if not provider.connected or rpc is not provider.rpc:
                return outcome
            # The first turn can ask for approval before the native catalog is
            # refreshed. This new ID is authorized by the checked all-session
            # create capability, and only for this same native connection.
            provider.session_ids.add(destination)
        reply = rpc.call('turn/start', {'threadId': destination,
            'input':[{'type':'text', 'text':action['text']}], 'clientUserMessageId':command['id']})
        if isinstance(reply.get('turn'), dict) and reply['turn'].get('id'):
            # An empty native thread may not yet have a persisted rollout.
            # Verify its identity after the server accepted the first turn.
            reread = rpc.call('thread/read', {'threadId':destination, 'includeTurns':False})['thread']
            if reread.get('id') == destination and reread.get('cwd') == cwd:
                outcome['state'] = 'delivered'
    except Exception:
        # No replay after creation or an uncertain first turn.
        pass
    return outcome


def draft_url(cwd, prompt):
    return 'codex://threads/new?' + urlencode({'path':cwd, 'prompt':prompt}, quote_via=quote)


def _verified_desktop_bundle(app):
    try:
        with (app/'Contents/Info.plist').open('rb') as file:
            info = plistlib.load(file)
        # Both the installed route's semantics and the native signer matter.
        # A renamed app or a future build does not inherit this capability.
        if (not isinstance(info, dict) or info.get('CFBundleIdentifier') != 'com.openai.codex'
                or info.get('CFBundleVersion') not in DESKTOP_DRAFT_BUILDS):
            return False
        requirement = ('=anchor apple generic and identifier "com.openai.codex" '
                       'and certificate leaf[subject.OU] = "2DC432GLL2"')
        subprocess.run(['/usr/bin/codesign', '--verify', '--test-requirement='+requirement, str(app)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        return True
    except (OSError, TypeError, plistlib.InvalidFileException, subprocess.SubprocessError):
        return False


def desktop_draft_app():
    if sys.platform == 'darwin':
        for base in (Path('/Applications'), Path.home()/'Applications'):
            for name in ('ChatGPT.app', 'Codex.app'):
                app = base/name
                if _verified_desktop_bundle(app):
                    return app
    return None


def _bundle_fingerprint(app):
    try:
        plist = app/'Contents/Info.plist'
        with plist.open('rb') as file:
            executable = plistlib.load(file).get('CFBundleExecutable')
        if (not isinstance(executable, str) or not executable
                or '/' in executable or '\\' in executable or executable in ('.', '..')):
            return None
        paths = (app, plist, app/'Contents/MacOS'/executable,
                 app/'Contents/_CodeSignature/CodeResources', app/'Contents/Resources/app.asar')
        return tuple((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                     for st in (path.stat() for path in paths))
    except (OSError, AttributeError, TypeError, plistlib.InvalidFileException):
        return None


class DesktopDraft:
    def __init__(self, app, opener=None):
        self.app = app
        self._fingerprint = None
        self._available = False
        if opener is None:
            from .macos_launch import open_url
            opener = open_url
        self.open = opener

    def available(self, force=False):
        before = _bundle_fingerprint(self.app)
        if before is None:
            self._fingerprint, self._available = None, False
        elif force or before != self._fingerprint:
            valid = _verified_desktop_bundle(self.app)
            after = _bundle_fingerprint(self.app)
            self._fingerprint = after
            self._available = valid and before == after
        return self._available

    def execute(self, scope, command):
        sid = command['session_id']
        cwd = scope.creation_directories().get(sid)
        cap = capability(sid, cwd, 'draft')
        validate_action({'create':cap}, command['action'])
        # Cheap file identity checks revoke stale buttons after an app update;
        # the actual dispatch always repeats the full signature verification.
        if not Path(cwd).is_dir() or not self.available(force=True):
            raise ControlError('create_unavailable')
        if scope.creation_directories().get(sid) != cwd:
            raise ControlError('create_unavailable')
        self.open(self.app, draft_url(cwd, command['action']['text']))
        return 'draft_opened'

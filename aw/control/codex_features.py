"""Read the desktop's authenticated feature bootstrap without changing it.

Only reviewed desktop builds and an unambiguous running local profile are
supported. Credentials and native targeting stay on the device; callers receive
one boolean and an opaque semantic revision. No account refresh or fallback to
historical feature evaluations is performed.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess
import sys
import threading
import time


BUILDS = frozenset({'25241'})
URL = 'https://chatgpt.com/backend-api/wham/statsig/bootstrap'
GUARDIAN_GATE = '3902016271'
MAX_BODY = 12 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024
CACHE_SECONDS = 600
CONTEXT_SECONDS = 5
ERROR_SECONDS = 30
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
ENTERPRISE_PLANS = frozenset({'business', 'enterprise', 'enterprise_cbp_usage_based',
    'enterprise_cbp_automation', 'enterprise_cbp_trial', 'enterprise_cbp_view_only',
    'deprecated_enterprise', 'hc', 'hc_consumption', 'finserv', 'law', 'ent26',
    'education', 'edu_plus', 'edu_pro', 'edu', 'deprecated_edu', 'k12', 'quorum'})


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _read(path, limit=MAX_FILE):
    with Path(path).open('rb') as file:
        data = file.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Native context exceeds read limit')
    return data


def _short(value, limit=256):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)


def _stat(path):
    try:
        st = Path(path).stat()
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    except FileNotFoundError:
        return None


def _processes(app, executable):
    """Project only the reviewed app's main process and renderer locale."""
    text = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,uid=,lstart=,command='],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=True).stdout
    if len(text) > 4 * MAX_FILE:
        raise ValueError('Process snapshot exceeds limit')
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(None, 8)
        if len(parts) != 9:
            continue
        try:
            pid, parent, uid = map(int, parts[:3])
            args = shlex.split(parts[8])
        except (ValueError, TypeError):
            continue
        if args and uid == os.getuid():
            rows.append((pid, parent, ' '.join(parts[3:8]), args))
    mains = [row for row in rows if row[3][0] == str(app/'Contents/MacOS'/executable)]
    if len(mains) != 1:
        raise ValueError('Running desktop identity is ambiguous')
    pid, _, started, _ = mains[0]
    languages = []
    for _, parent, _, args in rows:
        if parent != pid or not args[0].startswith(str(app/'Contents/Frameworks') + '/') or '--type=renderer' not in args:
            continue
        values = [arg.split('=', 1)[1] for arg in args if arg.startswith('--lang=')]
        if len(values) != 1 or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', values[0]):
            raise ValueError('Native renderer locale is unavailable')
        languages.append(values[0])
    if not languages or len(set(languages)) != 1:
        raise ValueError('Native renderer locale is ambiguous')
    return pid, started, languages[0]


def _app_session(pid, home):
    # An open main-process log descriptor binds the UUID to this running app;
    # old files, log text and cached feature values are never read.
    result = subprocess.run(['/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=True)
    if len(result.stdout) > MAX_FILE:
        raise ValueError('Native file descriptor snapshot exceeds limit')
    prefix = str(home/'Library/Logs/com.openai.codex') + '/'
    pattern = re.compile(r'codex-desktop-(' + UUID.pattern + r')-' + str(pid) + r'-t0-i\d+-\d{6}-\d+\.log')
    sessions = set()
    for line in result.stdout.splitlines():
        if line.startswith('n' + prefix):
            match = pattern.fullmatch(Path(line[1:]).name)
            if match:
                sessions.add(match.group(1))
    if len(sessions) != 1:
        raise ValueError('Running native app session is unavailable')
    return sessions.pop()


def _check_language_preferences(preferences, locale):
    intl = preferences.get('intl', {})
    if not isinstance(intl, dict) or set(intl) - {'accept_languages', 'selected_languages'}:
        raise ValueError('Unverified native language preference')
    for value in intl.values():
        if not isinstance(value, str):
            raise ValueError('Unverified native language preference')
        languages = [part.strip() for part in value.split(',')]
        if (not languages or languages[0] != locale or any(not re.fullmatch(
                r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', part) for part in languages)):
            raise ValueError('Native language preference differs from renderer')


def _curl_bootstrap(context):
    """Fixed HTTPS origin, no redirects/config/proxy, secret headers via stdin."""
    headers = {'Authorization': 'Bearer ' + context['token'],
        'ChatGPT-Account-Id': context['account'], 'Content-Type': 'application/json',
        'Accept': 'application/json', 'Cache-Control': 'no-cache',
        'originator': 'Codex Desktop', 'x-openai-web-frontend': 'codex_webview',
        'x-openai-codex-window-type': 'electron', 'OAI-Product-Sku': 'CODEX'}
    body = copy.deepcopy(context['body'])
    if body.get('desktop_app_beta_enabled') is False:
        body.pop('desktop_app_beta_enabled')  # Exact native false semantics.
    config = ('url = ' + json.dumps(URL) + '\nrequest = "POST"\n' +
        ''.join('header = ' + json.dumps(key + ': ' + value) + '\n' for key, value in headers.items()) +
        'data = ' + json.dumps(json.dumps(body, separators=(',', ':'))) + '\n')
    process = subprocess.Popen(['/usr/bin/curl', '--disable', '--silent', '--compressed',
        '--proto', '=https', '--max-time', '20', '--connect-timeout', '5',
        '--max-filesize', str(MAX_BODY), '--write-out', '\n%{http_code}', '--config', '-'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(Path.home())}, shell=False)
    timer = threading.Timer(23, process.kill)
    timer.daemon = True
    timer.start()
    try:
        process.stdin.write(config.encode())
        process.stdin.close()
        data = process.stdout.read(MAX_BODY + 5)
        if len(data) > MAX_BODY + 4:
            raise ValueError('Bootstrap response exceeds limit')
        if process.wait(timeout=2) or len(data) < 4 or data[-4:] != b'\n200':
            raise ValueError('Native feature bootstrap failed')
        return data[:-4]
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def _project(data, context, now):
    if not isinstance(data, bytes) or len(data) > MAX_BODY:
        raise ValueError('Invalid bootstrap response')
    envelope = json.loads(data)
    payload_text = envelope.get('statsigPayload')
    if not isinstance(payload_text, str):
        raise ValueError('Missing native feature payload')
    payload = json.loads(payload_text)
    user = payload.get('user') or {}
    ids, custom = user.get('customIDs') or {}, user.get('custom') or {}
    body = context['body']
    expected = {'auth_method': 'chatgpt', 'plan_type': context['plan'], 'brand_name': body['brand_name'],
        'codex_app_session_id': body['app_session_id'], 'codex_build_flavor': body['build_flavor'],
        'codex_window_type': body['window_type'], 'systemName': body['system_name'],
        'systemVersion': body['system_version'], 'desktop_app_beta_enabled': body['desktop_app_beta_enabled']}
    if (user.get('userID') != context['user'] or ids.get('account_id') != context['account']
            or ids.get('stableID') != body['stable_id'] or user.get('locale') != body['locale']
            or user.get('appVersion') != body['app_version']
            or any(custom.get(key) != value or type(custom.get(key)) is not type(value)
                   for key, value in expected.items())):
        raise ValueError('Native feature targeting changed')
    generated = payload.get('time')
    if type(generated) not in (int, float) or not now - 300 <= generated / 1000 <= now + 60:
        raise ValueError('Native feature evaluation is stale')
    gates = payload.get('feature_gates')
    if not isinstance(gates, dict):
        raise ValueError('Missing native feature gates')
    hashed = 0
    for char in GUARDIAN_GATE:
        hashed = (31 * hashed + ord(char)) & 0xffffffff
    # This is the bundled SDK's V1 direct-name / _DJB2 lookup. Unknown formats
    # and absent gates are unknown, never an implicit enabled/default result.
    row = gates.get(GUARDIAN_GATE, gates.get(str(hashed)))
    if not isinstance(row, dict) or type(row.get('value')) is not bool:
        raise ValueError('Native guardian evaluation unavailable')
    value = row['value']
    return {'guardian_approval': value,
            'revision': _sha([context['semantic'], value])}


class DesktopFeatures:
    def __init__(self, app, root, *, context_reader=None, transport=_curl_bootstrap,
                 clock=time.monotonic, wall_clock=time.time):
        self.app, self.root = Path(app).resolve(), Path(root).resolve()
        self.home = Path.home()
        self.context_reader, self.transport = context_reader, transport
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.Lock()
        self._bundle = None
        self._context = self._result = self._result_context = None
        self._context_at = self._result_at = self._failed_at = float('-inf')
        self._files = None

    def _fingerprint(self):
        paths = [self.root/'auth.json', self.root/'.codex-global-state.json',
            self.home/'Library/Application Support/Codex/statsig-state.json',
            self.app/'Contents/Info.plist', self.app/'Contents/Resources/app.asar',
            self.app/'Contents/_CodeSignature/CodeResources',
            self.home/'Library/Application Support/Codex/Preferences']
        return tuple(_stat(path) for path in paths)

    def _read_context(self):
        if self.context_reader is not None:
            return self.context_reader()
        if sys.platform != 'darwin' or self.root != (self.home/'.codex').resolve():
            raise ValueError('Unverified desktop profile')
        before = self._fingerprint()
        info = plistlib.loads(_read(self.app/'Contents/Info.plist', 65536))
        executable, version = info.get('CFBundleExecutable'), info.get('CFBundleShortVersionString')
        if (info.get('CFBundleIdentifier') != 'com.openai.codex' or info.get('CFBundleVersion') not in BUILDS
                or not _short(executable) or '/' in executable or '\\' in executable
                or executable in ('.', '..') or not _short(version)):
            raise ValueError('Unreviewed desktop build')
        bundle = before[3:6] + (_stat(self.app/'Contents/MacOS'/executable),)
        if self._bundle != bundle:
            requirement = ('=anchor apple generic and identifier "com.openai.codex" '
                           'and certificate leaf[subject.OU] = "2DC432GLL2"')
            subprocess.run(['/usr/bin/codesign', '--verify', '--test-requirement=' + requirement, str(self.app)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
            self._bundle = bundle
        raw = _read(self.root/'auth.json', 65536)
        auth = json.loads(raw)
        token = (auth.get('tokens') or {}).get('access_token')
        if auth.get('auth_mode') != 'chatgpt' or not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,16384}', token):
            raise ValueError('Unsupported native authentication')
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError('Unsupported native access token')
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        principal = claims.get('https://api.openai.com/auth') or {}
        account = principal.get('chatgpt_account_id') or principal.get('account_id')
        user = principal.get('user_id') or principal.get('chatgpt_user_id')
        plan = principal.get('chatgpt_plan_type')
        expiry = claims.get('exp')
        if (not _short(account) or not _short(user) or account != auth['tokens'].get('account_id')
                or type(expiry) not in (int, float) or expiry < self.wall_clock() + 60
                or not _short(plan) or plan in ENTERPRISE_PLANS):
            raise ValueError('Native principal or managed beta state unavailable')
        stable = json.loads(_read(self.home/'Library/Application Support/Codex/statsig-state.json', 65536)).get('statsig-stable-id')
        if not _short(stable):
            raise ValueError('Native stable identity unavailable')
        state_path = self.root/'.codex-global-state.json'
        state = json.loads(_read(state_path)) if state_path.exists() else {}
        key = 'desktop-app-beta-preferences-by-account-id'
        # Read the persisted native preference only. An enabled preference also
        # depends on a separate availability gate, so it is currently unknown.
        atom = state.get('electron-persisted-atom-state') or {}
        preferences = [obj[key] for obj in (state, atom) if key in obj]
        if any(not isinstance(value, dict) or value.get(account, False) is not False for value in preferences):
            raise ValueError('Native beta enablement needs additional policy data')
        pid, started, locale = _processes(self.app, executable)
        preference_path = self.home/'Library/Application Support/Codex/Preferences'
        preferences = json.loads(_read(preference_path)) if preference_path.exists() else {}
        _check_language_preferences(preferences, locale)
        session = _app_session(pid, self.home)
        system = subprocess.run(['/usr/bin/sw_vers', '-productVersion'], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=3, check=True).stdout.strip()
        if not re.fullmatch(r'\d+(?:\.\d+){1,3}', system):
            raise ValueError('Native system version unavailable')
        body = {'app_session_id': session, 'app_version': version, 'brand_name': 'chatgpt',
            'build_flavor': 'prod', 'desktop_app_beta_enabled': False, 'locale': locale,
            'stable_id': stable, 'system_name': 'macOS', 'system_version': system, 'window_type': 'electron'}
        if before != self._fingerprint() or bundle[-1] != _stat(self.app/'Contents/MacOS'/executable):
            raise ValueError('Native context changed during read')
        semantic = _sha([account, user, plan, body, pid, started, info['CFBundleVersion']])
        return {'token': token, 'account': account, 'user': user, 'plan': plan, 'body': body,
                'pid': pid, 'semantic': semantic, 'stamp': _sha([semantic, hashlib.sha256(raw).hexdigest()])}

    def get(self, fresh=False):
        with self.lock:
            try:
                now, files = self.clock(), self._fingerprint()
                changed = files != self._files
                if fresh or changed or self._context is None or now - self._context_at >= CONTEXT_SECONDS:
                    context = self._read_context()
                    self._context, self._context_at, self._files = context, now, self._fingerprint()
                else:
                    context = self._context
                    if 'pid' in context:
                        os.kill(context['pid'], 0)
                if context['stamp'] != self._result_context:
                    self._result = None
                    self._result_context = context['stamp']
                    self._failed_at = float('-inf')
                if not fresh and self._result is not None and now - self._result_at < CACHE_SECONDS:
                    return copy.deepcopy(self._result)
                if not fresh and now - self._failed_at < ERROR_SECONDS:
                    return None
                result = _project(self.transport(context), context, self.wall_clock())
                # A full re-read, including current app/process/session/locale,
                # prevents account switches and app restarts racing the network.
                after = self._read_context()
                if context['stamp'] != after['stamp']:
                    raise ValueError('Native identity changed during bootstrap')
                self._context, self._context_at, self._files = after, self.clock(), self._fingerprint()
                self._result, self._result_context, self._result_at = result, after['stamp'], self.clock()
                return copy.deepcopy(result)
            except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError, subprocess.SubprocessError):
                self._result = None
                self._failed_at = self.clock()
                return None

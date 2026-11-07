"""Bounded native metadata reads, separate from desktop-owned conversation writes.

This subprocess can only initialize and read catalogs/configuration. It never
starts, resumes, loads or updates a conversation, and exits after each refresh.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import queue
import re
import subprocess
import sys
import threading
import time


READ_METHODS = frozenset({'initialize', 'config/read', 'configRequirements/read',
                          'model/list', 'permissionProfile/list'})
MAX_FRAME = 2 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
MAX_PAGES = 5
IDENTIFIER = re.compile(r'[\w][\w./:-]{0,159}')
CONFIG_FIELDS = ('approval_policy', 'approvals_reviewer', 'permissions',
                 'default_permissions', 'sandbox_mode', 'sandbox_workspace_write',
                 'model_provider')
REQUIREMENT_FIELDS = ('allowedApprovalPolicies', 'allowedApprovalsReviewers',
                      'allowedSandboxModes', 'allowedPermissionProfiles',
                      'defaultPermissions')


class MetadataRpc:
    def __init__(self, executable, root, cwd, timeout=20):
        self.deadline = time.monotonic() + timeout
        self.responses = queue.Queue(maxsize=8)
        self.sequence = 0
        self.process = subprocess.Popen([str(executable), 'app-server', '--stdio'],
            cwd=cwd, env={**os.environ, 'CODEX_HOME': str(root)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        total = 0
        try:
            while True:
                line = self.process.stdout.readline(MAX_FRAME + 1)
                total += len(line)
                if not line or len(line) > MAX_FRAME or total > MAX_OUTPUT:
                    break
                value = json.loads(line)
                if not isinstance(value, dict):
                    break
                if 'id' in value:
                    self.responses.put_nowait(value)
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            try:
                self.responses.put_nowait(None)
            except queue.Full:
                pass

    def call(self, method, params):
        if method not in READ_METHODS:
            raise ValueError('Unsupported metadata operation')
        self.sequence += 1
        message = {'id': self.sequence, 'method': method, 'params': params}
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        self.process.stdin.flush()
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Native metadata deadline exceeded')
            result = self.responses.get(timeout=remaining)
            if result is None:
                raise OSError('Native metadata connection ended')
            if result.get('id') == self.sequence:
                if 'error' in result or not isinstance(result.get('result'), dict):
                    raise ValueError('Native metadata request rejected')
                return result['result']

    def initialize(self):
        result = self.call('initialize', {'clientInfo': {'name': 'agent_watch_catalog',
            'version': '1.0'}, 'capabilities': {'experimentalApi': True}})
        self.process.stdin.write(b'{"method":"initialized"}\n')
        self.process.stdin.flush()
        return result

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()


def _pages(rpc, method, params):
    rows, cursors = [], set()
    cursor = None
    for _ in range(MAX_PAGES):
        result = rpc.call(method, {**params, 'cursor': cursor, 'limit': 100})
        page = result.get('data')
        if not isinstance(page, list) or len(page) > 100:
            raise ValueError('Invalid native metadata page')
        rows.extend(page)
        cursor = result.get('nextCursor')
        if cursor is None:
            return rows
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError('Invalid native metadata cursor')
        cursors.add(cursor)
    raise ValueError('Native metadata exceeds supported page count')


def _models(rows):
    from .codex_settings import NATIVE_REASONING_LABELS
    options, seen, duplicates = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or row.get('hidden') is not False:
            continue
        value, label = row.get('model'), row.get('displayName')
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            continue
        native_efforts = row.get('supportedReasoningEfforts')
        if not isinstance(native_efforts, list):
            continue
        efforts = [entry.get('reasoningEffort') for entry in native_efforts
                   if isinstance(entry, dict)]
        if (len(efforts) != len(native_efforts) or len(efforts) > 16
                or any(not isinstance(effort, str) or not IDENTIFIER.fullmatch(effort)
                       for effort in efforts) or len(set(efforts)) != len(efforts)):
            continue
        default = row.get('defaultReasoningEffort')
        if default is not None and default not in efforts:
            continue
        option = {'value': value, 'label': label, 'efforts': efforts,
                  'effort_labels': {effort: NATIVE_REASONING_LABELS.get(effort, effort) for effort in efforts}}
        if default is not None:
            option['default_effort'] = default
        options.append(option)
    return [row for row in options if row['value'] not in duplicates]


def _profiles(rows, requirements):
    allowed = requirements.get('allowedPermissionProfiles')
    if allowed is not None and not isinstance(allowed, dict):
        raise ValueError('Invalid native permission requirements')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get('allowed') is not True:
            continue
        value, description = row.get('id'), row.get('description')
        if (not isinstance(value, str) or not value or len(value) > 160
                or value in seen or allowed is not None and allowed.get(value) is not True):
            continue
        if description is not None and (not isinstance(description, str) or len(description) > 2000):
            continue
        result.append({'id': value, 'description': description, 'allowed': True})
        seen.add(value)
    return result


def query_catalog(executable, root, cwd, provider, rpc_factory=MetadataRpc):
    rpc = rpc_factory(executable, root, cwd)
    try:
        initialized = rpc.initialize()
        if Path(initialized.get('codexHome', '')).resolve() != Path(root).resolve():
            raise ValueError('Native metadata profile differs from desktop profile')
        config = rpc.call('config/read', {'includeLayers': False, 'cwd': cwd}).get('config')
        if not isinstance(config, dict) or (config.get('model_provider') or 'openai') != provider:
            raise ValueError('Native metadata provider differs from desktop provider')
        requirements = rpc.call('configRequirements/read', {}).get('requirements')
        if requirements is None:
            requirements = {}
        if not isinstance(requirements, dict):
            raise ValueError('Invalid native permission requirements')
        models = _models(_pages(rpc, 'model/list', {'includeHidden': False}))
        profiles = _profiles(_pages(rpc, 'permissionProfile/list', {'cwd': cwd}), requirements)
        safe_config = {key: config[key] for key in CONFIG_FIELDS if key in config}
        features = config.get('features')
        if isinstance(features, dict):
            safe_config['features'] = {key: value for key, value in features.items()
                                       if isinstance(key, str) and type(value) is bool}
        for key, value in config.items():
            if key.startswith('features.') and type(value) is bool:
                safe_config.setdefault('features', {})[key[len('features.'):]] = value
        workspace = safe_config.get('sandbox_workspace_write')
        if isinstance(workspace, dict):
            safe_config['sandbox_workspace_write'] = {key: workspace[key] for key in
                ('writable_roots', 'network_access', 'exclude_slash_tmp', 'exclude_tmpdir_env_var')
                if key in workspace}
        safe_requirements = {key: requirements[key] for key in REQUIREMENT_FIELDS if key in requirements}
        auto_review = requirements.get('autoReview')
        if auto_review is not None:
            if not isinstance(auto_review, dict):
                raise ValueError('Invalid native auto-review requirements')
            required = auto_review.get('requiredOnModels')
            if required is not None:
                if (not isinstance(required, list) or len(required) > 500
                        or any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value)
                               for value in required)):
                    raise ValueError('Invalid native auto-review models')
                safe_requirements['autoReview'] = {'requiredOnModels': required}
        result = {'models': models, 'profiles': profiles, 'config': safe_config,
                  'requirements': safe_requirements, 'provider': provider, 'cwd': cwd}
        result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return result
    finally:
        rpc.close()


class NativeCatalog:
    def __init__(self, executable, root, query=query_catalog):
        self.executable, self.root, self.query = Path(executable), Path(root).resolve(), query
        self.cache = {}
        self.lock = threading.Lock()

    def _fingerprint(self, cwd):
        paths = [self.executable, self.root/'auth.json', self.root/'config.toml']
        paths.extend(parent/'.codex/config.toml' for parent in (cwd, *cwd.parents))
        values = []
        for path in paths:
            try:
                info = path.stat()
                values.append((str(path), info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size))
            except FileNotFoundError:
                values.append((str(path), None))
        return tuple(values)

    def get(self, cwd, model_provider, fresh=False):
        if (not isinstance(cwd, str) or not Path(cwd).is_absolute()
                or not isinstance(model_provider, str) or not model_provider):
            return None
        with self.lock:
            try:
                directory = Path(cwd).resolve(strict=True)
                if not directory.is_dir():
                    return None
                fingerprint = self._fingerprint(directory)
            except (OSError, ValueError):
                return None
            key = (str(directory), model_provider)
            cached = self.cache.get(key)
            if not fresh and cached and cached[0] == fingerprint and cached[1] > time.monotonic():
                return copy.deepcopy(cached[2])
            try:
                result = self.query(self.executable, self.root, str(directory), model_provider)
                if fingerprint != self._fingerprint(directory):
                    result = None
            except (OSError, ValueError, TypeError, TimeoutError, queue.Empty, subprocess.SubprocessError):
                result = None
            if len(self.cache) >= 32 and key not in self.cache:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (fingerprint, time.monotonic() + (60 if result else 15), copy.deepcopy(result))
            return result


def _metadata_executable(app):
    """Verify a metadata reader independently of versioned desktop write APIs.

    An app update must not erase a readable model/profile catalog merely
    because its draft-creation protocol has not been verified yet. This
    reader still requires the native signer and validates every RPC result.
    """
    try:
        with (app/'Contents/Info.plist').open('rb') as file:
            info = plistlib.load(file)
        if not isinstance(info, dict) or info.get('CFBundleIdentifier') != 'com.openai.codex':
            return None
        executable = app/'Contents/Resources/codex-cli/bin/codex'
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return None
        requirement = ('=anchor apple generic and identifier "com.openai.codex" '
                       'and certificate leaf[subject.OU] = "2DC432GLL2"')
        subprocess.run(['/usr/bin/codesign', '--verify', '--test-requirement='+requirement, str(app)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        return executable
    except (OSError, TypeError, plistlib.InvalidFileException, subprocess.SubprocessError):
        return None


def native_catalog(root, executable=None):
    if executable is None and sys.platform == 'darwin':
        for base in (Path('/Applications'), Path.home()/'Applications'):
            for name in ('ChatGPT.app', 'Codex.app'):
                executable = _metadata_executable(base/name)
                if executable is not None:
                    return NativeCatalog(executable, root)
    if executable is None:
        return None
    path = Path(executable).expanduser()
    return NativeCatalog(path, root) if path.is_file() and os.access(path, os.X_OK) else None

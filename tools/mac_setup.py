"""Interactive Mac first install; --prepare-only never pairs or starts services."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

REPO = 'https://github.com/xuhuaya0/agent-watch'
DEPS = ['flask', 'pystray', 'pillow', 'zstandard', 'websocket-client']


class SetupError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SetupError('中心返回重定向，已停止；请填写最终中心地址。')


def hub_url(value):
    value = value.strip().rstrip('/')
    try:
        u = urllib.parse.urlsplit(value)
        port = u.port
    except ValueError:
        raise SetupError('中心地址格式不正确。') from None
    if (u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password
            or u.query or u.fragment or u.path or any(ord(c) < 33 for c in value)):
        raise SetupError('请输入完整中心地址，例如 http://192.0.2.10:8090，不附带路径或 Token。')
    return value


def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def api_client():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def api(client, base, path, payload=None, headers=None):
    request = urllib.request.Request(hub_url(base) + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with client.open(request, timeout=15) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise SetupError('中心响应过大。')
            result = json.loads(data)
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise SetupError('中心未确认接收，请检查地址和服务版本。')
            return result
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if code == 401:
            raise SetupError('认证失败（401）：上报用中心 Push 接收令牌；交互授权用中心网页登录密码。') from None
        raise SetupError(f'中心返回 HTTP {code}，本次操作未确认。') from None
    except (OSError, ValueError):
        raise SetupError('无法确认中心响应，请检查网络和中心地址。') from None


def extract_source(archive, destination):
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        total = 0
        for member in members:
            p = PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or member.issym() or member.islnk():
                raise SetupError('源码归档包含不安全路径。')
            if len(p.parts) < 2:
                continue
            target = destination.joinpath(*p.parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                total += member.size
                if total > 32 * 1024 * 1024:
                    raise SetupError('源码归档超出安装大小限制。')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
                target.chmod(0o700 if member.mode & 0o111 else 0o600)
            else:
                raise SetupError('源码归档包含不支持的文件类型。')


def prepare(root, uv, source=None, revision=None):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = root/'installation.json'
    program = root/'program'
    if program.exists():
        if not marker.is_file() or json.loads(marker.read_text()).get('product') != 'agent-watch':
            raise SetupError('安装目录已存在但不属于此安装器，已停止以保留原文件。')
    else:
        with tempfile.TemporaryDirectory(dir=root, prefix='source-') as temp:
            stage = Path(temp)/'program'
            stage.mkdir()
            if source:
                # Development acceptance only. Never copy runtime/private files.
                for name in ('aw', 'static', 'tools'):
                    shutil.copytree(source/name, stage/name,
                                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                for name in ('hub.py', 'tray.py', 'control_worker.py', 'start_mac.command'):
                    shutil.copy2(source/name, stage/name)
            else:
                if not re.fullmatch(r'[a-f0-9]{40}', revision or ''):
                    raise SetupError('缺少固定源码版本。')
                archive = Path(temp)/'source.tar.gz'
                with urllib.request.urlopen(f'https://codeload.github.com/xuhuaya0/agent-watch/tar.gz/{revision}', timeout=30) as response:
                    content = response.read(16 * 1024 * 1024 + 1)
                if len(content) > 16 * 1024 * 1024:
                    raise SetupError('源码下载超出大小限制。')
                archive.write_bytes(content)
                extract_source(archive, stage)
                if (stage/'tools/mac_setup.py').read_bytes() != Path(__file__).read_bytes():
                    raise SetupError('安装器与源码版本不一致。')
            for name in ('tray.py', 'hub.py', 'tools/mac_setup.py'):
                if not (stage/name).is_file():
                    raise SetupError('源码不完整。')
            stage.rename(program)
        private_json(marker, {'product': 'agent-watch', 'revision': revision or 'development'})
    python = program/'.venv/bin/python'
    if not python.exists():
        subprocess.run([str(uv), 'venv', '--python', sys.executable, str(program/'.venv')], check=True)
    subprocess.run([str(uv), 'pip', 'install', '--python', str(python), '--only-binary', ':all:', *DEPS], check=True)
    return program, python


def build_app(program, target):
    if target.exists():
        plist = target/'Contents/Info.plist'
        if not plist.is_file() or plistlib.loads(plist.read_bytes()).get('CFBundleIdentifier') != 'com.agentwatch.installed':
            raise SetupError('目标位置已有其他应用，已停止，未覆盖。')
        if plistlib.loads(plist.read_bytes()).get('AWProgramRoot') != str(program):
            raise SetupError('已有应用属于另一安装目录，未覆盖。')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    command = '/bin/bash ' + shlex.quote(str(program/'start_mac.command'))
    script = 'on run\n do shell script ' + json.dumps(command, ensure_ascii=False) + '\nend run\n'
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='app-build-') as temp:
        built = Path(temp)/'Agent Watch.app'
        subprocess.run(['/usr/bin/osacompile', '-o', str(built)], input=script, text=True, check=True,
                       stdout=subprocess.DEVNULL)
        plist = built/'Contents/Info.plist'
        data = plistlib.loads(plist.read_bytes())
        data.update(CFBundleIdentifier='com.agentwatch.installed', CFBundleName='Agent Watch',
                    LSUIElement=True, AWProgramRoot=str(program))
        plist.write_bytes(plistlib.dumps(data))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(built)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        built.rename(target)


def available_port():
    for port in (8090, *range(18091, 18111)):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise SetupError('本机可用端口不足。')


def scan_and_push(program, cfg):
    sys.path.insert(0, str(program))
    from aw.scanner import scan_all
    from aw.pusher import local_ips
    tasks, errors = scan_all(cfg)
    api(api_client(), cfg['hub_urls'][0], '/api/push', {
        'machine': cfg['machine'], 'machine_id': cfg['machine_id'], 'ips': local_ips(),
        'tasks': [t.to_dict() for t in tasks]}, {'Authorization': 'Bearer ' + cfg['hub_token']})
    return tasks, errors


def pair_codex(root, program, cfg, password, client=None):
    destination = root/'private/codex/control.json'
    pending = destination.with_suffix('.pending')
    if destination.exists():
        existing = json.loads(destination.read_text())
        if existing.get('device_id') != cfg['machine_id'] or existing.get('hub_url') != cfg['hub_urls'][0]:
            raise SetupError('已有控制配置属于其他设备或中心，未修改。')
        return destination
    if pending.exists():
        raise SetupError('此前控制凭据签发结果未确认；已停止自动重试，请先核对中心。')
    client = client or api_client()
    auth = api(client, cfg['hub_urls'][0], '/api/auth/login', {'password': password, 'remember': False})
    csrf = auth.get('csrf_token')
    if not isinstance(csrf, str) or not csrf:
        raise SetupError('中心未返回授权会话。')
    # A lost provisioning response must never silently rotate an existing credential.
    private_json(pending, {'device_id': cfg['machine_id'], 'hub_url': cfg['hub_urls'][0]})
    issued = api(client, cfg['hub_urls'][0], '/api/control/devices/' + cfg['machine_id'], {}, {'X-CSRF-Token': csrf})
    if issued.get('device_id') != cfg['machine_id'] or not isinstance(issued.get('token'), str) or not issued['token']:
        raise SetupError('未获得有效设备控制凭据。')
    codex_root = Path(cfg['adapters']['codex']['root']).expanduser().resolve()
    private_json(destination, {'hub_url': cfg['hub_urls'][0], 'device_id': cfg['machine_id'],
        'token': issued['token'], 'codex': {'transport': 'desktop-ipc', 'scope': 'all',
        'socket_path': str(codex_root/'ipc/ipc.sock'), 'data_root': str(codex_root)}})
    pending.unlink()
    return destination


def install_control_job(program, python, config):
    label = 'com.agentwatch.codex'
    path = Path.home()/'Library/LaunchAgents'/f'{label}.plist'
    data = {'Label': label, 'ProgramArguments': [str(python), str(program/'control_worker.py'), '--config', str(config)],
        'WorkingDirectory': str(program), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
        'StandardOutPath': str(config.parent/'worker.stdout.log'), 'StandardErrorPath': str(config.parent/'worker.stderr.log')}
    if path.exists():
        if plistlib.loads(path.read_bytes()) != data:
            raise SetupError('已有其他 Codex 控制桥启动配置，未覆盖。')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(data)); path.chmod(0o600)
    target = f'gui/{os.getuid()}/{label}'
    if subprocess.run(['launchctl', 'print', target], capture_output=True).returncode:
        subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)], check=True)


def confirm(prompt):
    return input(prompt + ' [y/N]：').strip().lower() in ('y', 'yes')


def configure(root, program, python, app):
    sys.path.insert(0, str(program))
    from aw.config import DEFAULTS
    cfg_path = program/'config.json'
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        print('已找到此安装的设备配置，将保留设备身份、中心地址和令牌。')
    else:
        cfg = json.loads(json.dumps(DEFAULTS))
        cfg.update(host='127.0.0.1', port=available_port(), machine=socket.gethostname(), machine_id=str(uuid.uuid4()))
        cfg['adapters']['codex']['enabled'] = True
        cfg['adapters']['antigravity']['enabled'] = False
        cfg['adapters']['dsh']['enabled'] = False
        cfg['hub_urls'] = [hub_url(input('中心地址（例如 http://192.0.2.10:8090）：'))]
        cfg['hub_token'] = getpass.getpass('中心的 Push 接收令牌（输入不显示）：').strip()
        if not cfg['hub_token']:
            raise SetupError('未填写上报令牌。')
        private_json(cfg_path, cfg)  # Retain this new identity even if connection fails.
    try:
        tasks, errors = scan_and_push(program, cfg)
    except SetupError:
        # Explicit retry edits only this install's connection, never its device ID.
        print('连接未通过。设备身份已保留；请重新核对中心地址和令牌。')
        if (root/'private/codex/control.json').exists():
            raise SetupError('此安装已有控制桥，请先在原中心核对连接；未改写其目标或凭据。')
        if confirm('现在重新填写中心连接'):
            cfg['hub_urls'] = [hub_url(input('中心地址：'))]
            cfg['hub_token'] = getpass.getpass('中心 Push 接收令牌：').strip()
            private_json(cfg_path, cfg)
            tasks, errors = scan_and_push(program, cfg)
        else:
            raise
    print(f'中心已确认接收：{len(tasks)} 个会话。')
    if errors:
        print('部分 Harness 未能读取，可稍后在本机设置中检查：' + ', '.join(errors))
    data = program/'data'; data.mkdir(exist_ok=True, mode=0o700)
    from aw.auth import AuthManager
    auth = AuthManager(str(data), cfg)
    if not auth.has_password():
        password = secrets.token_urlsafe(18)
        auth.set_password(password)
        private_json(root/'private/local-access.json', {'url': f"http://127.0.0.1:{cfg['port']}", 'password': password})
        print(f'本机设置密码已保存到：{root / "private/local-access.json"}')
    codex_tasks = [t for t in tasks if t.tool == 'codex']
    print('Codex 交互可让中心查看完整历史、回复全部普通会话、回答问题及处理单次审批；不修改 Codex 原权限策略。')
    control = root/'private/codex/control.json'
    if control.exists():
        # Validate identity/destination without issuing another credential.
        control = pair_codex(root, program, cfg, '')
        install_control_job(program, python, control)
        print('已保留并启动既有 Codex 控制桥。')
    elif confirm('是否启用以上 Codex 交互'):
        if not codex_tasks:
            print('还没有可采集的 Codex 会话。请先在 Codex 打开一个会话，再运行本安装命令接入交互。')
        else:
            password = getpass.getpass('中心网页登录密码（只用于本次设备授权，不保存）：')
            client = api_client()
            control = pair_codex(root, program, cfg, password, client)
            del password
            install_control_job(program, python, control)
            connected = False
            for attempt in range(8):
                devices = api(client, cfg['hub_urls'][0], '/api/control/devices').get('devices', [])
                connected = any(d.get('device_id') == cfg['machine_id'] and d.get('connected') for d in devices)
                if connected:
                    break
                time.sleep(2)
            print('中心已确认 Codex 交互连接。' if connected else
                  '控制桥已配置，但中心尚未确认连接。请打开并登录 Codex；不代表当前版本的全部交互已可用。')
    from aw import autostart
    started_at_login = False
    if confirm('是否在登录此 Mac 后自动启动采集器'):
        path = Path.home()/'Library/LaunchAgents'/f'{autostart.MAC_LABEL}.plist'
        if path.exists() and plistlib.loads(path.read_bytes()).get('WorkingDirectory') != str(program):
            raise SetupError('已有其他安装的自动启动配置，未覆盖；本次可从应用手动启动。')
        if not autostart.set_enabled(True):
            raise SetupError('自动启动未能启用，请从应用手动启动。')
        if subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{autostart.MAC_LABEL}'], capture_output=True).returncode:
            raise SetupError('登录启动项尚未加载；可从应用手动启动，或检查系统后台项目设置。')
        started_at_login = True
    if not started_at_login:
        subprocess.run(['open', str(app)], check=True)
    print(f'安装完成。应用：{app}\n中心面板：{cfg["hub_urls"][0]}\n无需复制设备 ID 或修改原应用插件。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--uv', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--revision')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--configured', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise SetupError('此安装入口仅适用于 macOS。')
    os.umask(0o077)
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    import fcntl
    lock = (root/'install.lock').open('a')
    if not args.configured:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError('另一个安装向导正在运行，请完成后再试。') from None
    if args.configured:
        program = root/'program'; python = program/'.venv/bin/python'
        configure(root, program, python, Path.home()/'Applications/Agent Watch.app')
        return
    program, python = prepare(root, args.uv, args.source, args.revision)
    target = root/'Agent Watch.app' if args.prepare_only else Path.home()/'Applications/Agent Watch.app'
    build_app(program, target)
    if args.prepare_only:
        print('准备验收通过：独立 Python、依赖和应用已生成；没有上报、配置凭据或启动服务。')
        return
    subprocess.run([str(python), str(program/'tools/mac_setup.py'), '--root', str(root), '--uv', str(args.uv), '--configured'], check=True)


if __name__ == '__main__':
    try:
        main()
    except (SetupError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, SetupError) else '安装未完成；已保留现有配置，可重新运行安装入口。', file=sys.stderr)
        sys.exit(1)

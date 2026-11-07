#!/usr/bin/env python3
"""Loopback-only synthetic UI fixture. Never imports Hub/native code or configuration.

Run: python3 tools/preview_ui.py --port 18093
Open /__preview to select a scenario. All product assets come from static/.
Only demo login, read receipts and acknowledgements are simulated in memory;
control, credential and configuration writes are refused, including replies.
"""
from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import struct
import zlib
from urllib.parse import parse_qs, unquote, urlsplit

STATIC = Path(__file__).resolve().parents[1] / 'static'
SCENARIOS = {
    'normal': '完整工作台', 'attention': '问题与单次审批', 'offline': '设备与桥接离线',
    'empty': '无会话', 'long': '长标题与长消息', 'login': '登录',
    'setup': '首次设置', 'failure': '读取失败', 'auth_failure': '登录状态读取失败', 'pending': '操作等待与未知回执',
    'refresh': '刷新时保留输入', 'changed': '模型与权限版本变化', 'slow': '慢响应与切页',
    'readonly': '模型与权限只读', 'unknown_settings': '模型与权限未读取',
    'media': '消息链接与原设备图片', 'process': '三种应用的过程折叠',
    'queued': '运行中回复队列', 'effort_states': '真实强度、未读取与不支持',
    'history_pagination': '折叠后的历史分页',
    'history_pagination_failure': '历史分页中途失败与续读',
    'history_pagination_slow': '历史分页慢响应与切页',
}
SPECS = [
    ('cx-ready', 'codex', 'done', 300, '整理发布说明'),
    ('ag-ready', 'antigravity', 'done', 600, '检查界面交互'),
    ('ds-ready', 'dsh', 'done', 900, '梳理测试覆盖'),
    ('cx-running', 'codex', 'running', 15, '正在检查当前修改'),
    ('ag-question', 'antigravity', 'waiting_input', 30, '等待选择方案'),
    ('ds-approval', 'dsh', 'waiting_approval', 60, '等待单次工具审批'),
    ('cx-pinned', 'codex', 'done', 10800, '固定的参考会话'),
    ('ag-recent', 'antigravity', 'done', 14400, '近期界面讨论'),
    ('ds-older', 'dsh', 'done', 86400, '较早的结果记录'),
]


def choice(value, label, description=''):
    return {'value': value, 'label': label, 'description': description}


def media_fixture():
    def chunk(kind, content):
        return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind + content))
    rows = (b'\0' + b'\x26\x88\x80' * 640) * 360
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 640, 360, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def reader_history(older, update):
    """Two bounded pages with stable landmarks and a changing final message."""
    messages = []
    turns = range(1, 13) if older else range(13, 25)
    for turn in turns:
        messages.append({'id': f'user-{turn}', 'role': 'user', 'text': f'请核对第 {turn} 轮的修改。'})
        messages.append({'id': f'result-{turn}', 'role': 'assistant',
                         'text': f'第 {turn} 轮检查结果\n\n已核对本轮的设备、会话和回复。'})
    if not older:
        messages.append({'id': 't1', 'role': 'tool', 'text': '合成工具输出，可展开查看。'})
        messages.append({'id': 'progress-1', 'role': 'assistant', 'phase':'commentary',
                         'text':'合成过程记录：已读完示例输入，正在核对下一项。'})
        messages.append({'id': 't2', 'role': 'tool', 'text':'第二条合成工具输出，与上一条过程记录连续分组。'})
        messages.append({'id': 'latest', 'role': 'assistant', 'text': f'最新进展，第 {update} 次更新。'})
    return {'ready': True, 'messages': messages, 'next_cursor': None if older else 'earlier'}


def pagination_history(cursor):
    """Raw transport pages deliberately cut through long folded process runs."""
    def process(prefix, start, stop):
        return [dict(id=f'{prefix}-{index}', role='assistant' if index % 3 == 0 else 'tool',
                     **({'phase': 'commentary'} if index % 3 == 0 else {}),
                     text=f'合成过程 {prefix} 第 {index} 项，展开后仍可完整阅读。')
                for index in range(start, stop)]
    def conversation(prefix, label):
        return [dict(id=f'{prefix}-{index}', role='user' if index % 2 == 0 else 'assistant',
                     text=f'{label} {index // 2 + 1}：' + ('请核对示例。' if index % 2 == 0 else '示例已核对。'))
                for index in range(20)]
    pages = {
        None: (process('latest-process', 30, 59) + [dict(id='latest-result', role='assistant', text='最新结果：长过程已经完成。')], 'process-latest-2'),
        'process-latest-2': (process('latest-process', 0, 30), 'conversation-latest'),
        'conversation-latest': (conversation('current-conversation', '当前对话') + [dict(id='latest-request', role='user', text='请执行最新这一组检查。')], 'process-older-1'),
        'process-older-1': (process('older-process', 30, 60), 'process-older-2'),
        'process-older-2': (process('older-process', 0, 30), 'conversation-older'),
        'conversation-older': (conversation('earlier-conversation', '更早对话') + [dict(id='older-request', role='user', text='请执行更早这一组检查。')], 'origin'),
        'origin': ([dict(id='origin-request', role='user', text='最早请求。'), dict(id='origin-result', role='assistant', text='最早结果。')], None),
    }
    if cursor not in pages:
        return None
    messages, next_cursor = pages[cursor]
    return {'ready': True, 'messages': messages, 'next_cursor': next_cursor}


class Preview:
    def __init__(self, scenario='normal', clock=time.time):
        self.clock, self.started, self.lock = clock, clock(), threading.RLock()
        self.select(scenario)

    def select(self, scenario):
        self.scenario = scenario if scenario in SCENARIOS else 'normal'
        self.authenticated = self.scenario not in ('login', 'setup')
        self.read_at, self.acked = {}, set()
        self.history_failures = set()

    def tasks(self):
        if self.scenario == 'empty':return []
        result = []
        for sid, tool, state, age, title in SPECS:
            if self.scenario == 'attention' and state not in ('waiting_input', 'waiting_approval'):continue
            machine = 'Example Mac' if tool == 'codex' else 'Example PC'
            device = 'demo-mac' if tool == 'codex' else 'demo-pc'
            timestamp = self.clock() - age if state == 'running' or self.scenario == 'refresh' else self.started - age
            if self.scenario == 'long':title += '：验证非常长的中文标题、英文标识 ExampleProjectAndModuleName 以及多设备信息的截断与换行'
            reply = '合成结果：修改已检查，可继续阅读或查看设置。'
            if self.scenario == 'offline' and sid == 'ag-ready':
                reply = '\n\n'.join(f'第 {n} 段缓存回复。设备离线时，仍然可以阅读已采集的结果。'
                                    for n in range(1, 61))
            result.append({'key':sid, 'ui_key':sid, 'session_id':sid, 'tool':tool,
                'tool_short':{'codex':'CX','antigravity':'AG','dsh':'DS'}[tool],
                'title':title, 'machine':machine, 'machine_id':device,
                'machine_online':self.scenario != 'offline' or tool == 'codex',
                'machine_ips':['192.0.2.10' if tool == 'codex' else '192.0.2.20'],
                'project':'Example Project', 'state':state, 'state_since':self.started-age,
                'activity_at':timestamp, 'turn_started_at':self.started-90 if state == 'running' else None,
                'acked':sid in self.acked, 'ui':{'read_at':self.read_at.get(sid, 0)},
                'prompt':'请检查这次修改，并给出简短结果。', 'detail':'这是一段合成结果，用于检查阅读和交互布局。',
                'extra':{'cwd':'/example/project' if tool == 'codex' else 'C:/Example/Project',
                         'native_pin':{'known':True,'pinned':sid == 'cx-pinned','order':0},
                         'latest_turn':{'user':'请检查这次修改。','assistant':reply}}})
        return result

    def capability(self, task):
        tool, sid = task['tool'], task['session_id']
        cap = {'tool':tool, 'session_id':sid, 'reply':True, 'history':True, 'requests':[], 'delivery':{'state':'running' if task['state']=='running' else 'idle','steer':False}}
        cap['settings'] = {'model':'Example Model ' + {'codex':'A','antigravity':'B','dsh':'C'}[tool],
                           'scope':'project' if tool == 'antigravity' else 'session'}
        if tool == 'codex':
            cap['settings'].update(reasoning='High', permission='工作区可写 · 联网受限 · 由你审批 · 按需审批')
            cap['model'] = {'current':'example-model-a','effort':'high','effort_status':'explicit','effort_label':'High','revision':'demo-model', 'options':[
                {'value':'example-model-a','label':'Example Model A','efforts':['low','high'],'effort_labels':{'low':'Light','high':'High'},'default_effort':'low'},
                {'value':'example-model-b','label':'Example Model B','efforts':['medium','high'],'effort_labels':{'medium':'Medium','high':'High'},'default_effort':'medium'},
                {'value':'example-model-c','label':'Example Model C','efforts':[]}]}
            cap['policy'] = {'scope':'session-permissions','current':'demo-workspace','current_label':'示例：请求审批','revision':'demo-policy',
                'options':[choice('demo-workspace','示例：请求审批','合成工作区权限及审批预设'),
                           choice('demo-auto-review','示例：代为审批','仅展示合成权限选项，不会执行原生操作'),
                           choice('demo-full','示例：完整访问','合成完整访问权限预设，不是实际配置'),
                           {**choice('demo-read-only','示例：只读','合成的其他原生权限配置'), 'group':'advanced'}]}
            cap['create'] = {'cwd':'/example/project','revision':'demo-create','mode':'draft'}
        elif tool == 'antigravity':
            cap['settings'].update(permission='Request Review', project_name='Example Project',
                note='模型来自已保存的会话记录，不含原应用中尚未发送的选择。')
            cap['policy'] = {'scope':'project-permissions','current':'request_review','revision':'demo-policy',
                'project_id':'demo-project','project_name':'Example Project','options':[
                    choice('use_global','跟随全局','此项目使用全局权限设置'),
                    choice('request_review','Request Review','命令执行需要审批；关闭终端沙盒'),
                    choice('default','Default','在沙盒内执行；拒绝访问工作区外的文件'),
                    choice('auto','Auto (Preview)','使用终端沙盒，由原应用决定是否执行命令'),
                    choice('turbo','Turbo','关闭终端沙盒，允许无审批执行命令及访问工作区外的文件')]}
            cap['create'] = {'location':'native-project','project_id':'demo-project','project_name':'Example Project','revision':'demo-create'}
        else:
            cap['settings'].update(permission='Workspace Write', reasoning='High')
            cap['model'] = {'scope':'session-and-default', 'current':'example-model-c', 'current_label':'Example Model C',
                'effort':'high','effort_status':'explicit','effort_label':'High','revision':'demo-dsh-model', 'options':[
                    {'value':'example-model-c','label':'Example Model C','provider_label':'Example Provider',
                     'efforts':['low','high'],'effort_labels':{'low':'Low','high':'High'},'default_effort':'high'},
                    {'value':'example-model-d','label':'Example Model D','provider_label':'Example Provider',
                     'efforts':[]}] }
            cap['policy'] = {'scope':'session-permissions','current':'workspace-write','revision':'demo-policy',
                'options':[choice('read-only','Read Only','只读访问文件'),
                           choice('workspace-write','Workspace Write','可修改工作区内文件'),
                           choice('full-access','Full Access','完整文件访问') ]}
            cap['create'] = {'cwd':'C:/Example/Project','revision':'demo-create'}
        if self.scenario == 'effort_states' and 'model' in cap:
            model = cap['model']
            if sid == 'cx-ready':
                model.pop('effort');model.pop('effort_label');model['effort_status'] = 'unknown'
                cap['settings'].pop('reasoning')
            elif tool == 'dsh':
                model.update(current='example-model-d',current_label='Example Model D',effort_status='unsupported')
                model.pop('effort');model.pop('effort_label')
                cap['settings'].update(model='Example Model D');cap['settings'].pop('reasoning')
        if task['state'] == 'running':
            if 'model' in cap:cap['model']['options'] = []
            if 'policy' in cap:cap['policy']['options'] = []
            if self.scenario == 'queued' and tool in ('codex','dsh'):
                cap['delivery'].update(steer=True,revision='demo-running-turn')
            cap.pop('create');cap['reason'] = '演示：会话正在运行，等待本轮结束。'
        if task['state'] == 'waiting_input':
            cap['reply'] = False;cap.pop('policy', None)
            cap['requests'] = [{'id':'demo-question','revision':'demo-v1','kind':'question','prompt':'选择合成示例的显示方案',
                'questions':[{'id':'layout','question':'列表采用哪种密度？','options':['紧凑','宽松'],
                              'option_details':{'紧凑':'一次显示更多会话','宽松':'保留更大的点击区域'},'custom':True}]}]
        if task['state'] == 'waiting_approval':
            cap['reply'] = False;cap.pop('policy', None)
            cap['requests'] = [{'id':'demo-approval','revision':'demo-v1','kind':'approval',
                                'prompt':'合成工具请求：打印 DEMO-ONLY（不会执行）','choices':['approve','reject']}]
        if self.scenario == 'changed' and cap.get('policy',{}).get('options'):
            generation = int((self.clock()-self.started)//15)
            cap['policy']['revision'] = f'demo-policy-{generation}'
            selected = cap['policy']['options'][generation % 2]
            cap['policy']['current'] = selected['value']
            cap['settings']['permission'] = selected['label']
            if 'model' in cap and cap['model']['options']:
                cap['model']['revision'] = f'demo-model-{generation}'
                selected_model = cap['model']['options'][generation % 2]
                cap['model']['current'] = selected_model['value']
                cap['model']['current_label'] = selected_model['label']
                cap['settings']['model'] = selected_model['label']
                if selected_model['efforts']:
                    cap['model'].update(effort=selected_model['default_effort'],effort_status='explicit',
                        effort_label=selected_model['effort_labels'][selected_model['default_effort']])
                    cap['settings']['reasoning'] = cap['model']['effort_label']
                else:
                    cap['model'].pop('effort',None);cap['model'].pop('effort_label',None)
                    cap['model']['effort_status'] = 'unsupported';cap['settings'].pop('reasoning',None)
        if self.scenario in ('readonly', 'unknown_settings'):
            for field in ('model', 'policy', 'create'):cap.pop(field, None)
            if self.scenario == 'unknown_settings':cap.pop('settings', None)
        return cap

    def machines(self):
        tasks = self.tasks()
        return {'id:'+device:{'machine_id':device, 'machine':name, 'online':self.scenario != 'offline' or device == 'demo-mac',
                             'ips':[ip], 'tasks':sum(t['machine_id'] == device for t in tasks), 'last_seen':self.clock(),
                             'local':device == 'demo-mac'}
                for device,name,ip in [('demo-mac','Example Mac','192.0.2.10'),('demo-pc','Example PC','192.0.2.20')]}

    def page(self):
        notice = ('<aside style="position:fixed;right:8px;bottom:5px;z-index:20;background:#fff8dc;'
                  'color:#513c00;border:1px solid #d6c487;border-radius:4px;padding:3px 8px;font-size:11px;max-width:calc(100vw - 16px);box-sizing:border-box">'
                  '合成预览 · 不连接设备 · 不输入真实密码 · <a href="/__preview">切换场景</a></aside>')
        return (STATIC/'index.html').read_text(encoding='utf-8').replace('<title>Agent Watch</title>',
            '<title>Agent Watch — 合成预览</title>').replace('</body>',notice+'</body>')

    def route(self, method, target, body=None):
        """Pure fixture dispatch, except reading the explicitly allowlisted assets."""
        parsed = urlsplit(target);path = unquote(parsed.path)
        if method == 'GET' and path == '/':
            scenario = parse_qs(parsed.query).get('scenario', [None])[0]
            if scenario is not None:self.select(scenario)
            return 200, 'text/html', self.page()
        if method == 'GET' and path == '/__preview':
            links = ''.join(f'<li><a href="/?scenario={key}">{html.escape(label)}</a></li>' for key,label in SCENARIOS.items())
            return 200, 'text/html', ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>UI 验收场景</title>'
                '<main style="max-width:640px;margin:48px auto;font:16px/1.7 system-ui;padding:24px"><h1>Agent Watch 合成预览</h1>'
                '<p>只读取仓库中的静态文件，不连接真实设备、Hub 或原生应用。登录可填写任意演示字符串，勿使用真实密码。</p>'
                '<p>所有控制、凭据与配置写操作均拒绝；已读与“知道了”仅保存在本进程内存。</p>'
                '<p>刷新场景中，滚到第 18 轮检查结果或填写回复草稿，观察两次轮询后的阅读位置。模型与权限版本场景每 15 秒更新一次。所有模型与权限选项均为合成示例。</p><ul>'+links+'</ul></main></html>')
        if method == 'GET' and path.startswith('/static/'):
            file = (STATIC/path.removeprefix('/static/')).resolve()
            if file.parent == STATIC and file.suffix in ('.js','.css') and file.is_file():
                return 200, 'text/javascript' if file.suffix == '.js' else 'text/css', file.read_text(encoding='utf-8')
            return 404, 'application/json', {'error':'not_found'}
        if path == '/api/auth/status' and method == 'GET':
            if self.scenario == 'auth_failure':return 503, 'application/json', {'error':'demo_auth_unavailable'}
            return 200, 'application/json', {'authenticated':self.authenticated,'needs_setup':self.scenario == 'setup','csrf_token':'demo-only'}
        if path in ('/api/auth/login','/api/auth/setup') and method == 'POST':
            self.authenticated = True
            return 200, 'application/json', {'ok':True,'csrf_token':'demo-only'}
        if path == '/api/auth/logout' and method == 'POST':
            self.authenticated = False
            return 200, 'application/json', {'ok':True}
        if not self.authenticated:return 401, 'application/json', {'error':'demo_login_required'}
        if method == 'GET' and path == '/api/tasks':
            if self.scenario == 'failure':return 503, 'application/json', {'error':'demo_unavailable'}
            tasks = self.tasks()
            # The center sends the full captured turn only on the detail endpoint.
            for task in tasks:
                task['extra'].pop('latest_turn')
            return 200, 'application/json', {'tasks':tasks,'machines':self.machines(),'errors':{},'scanned_at':self.clock(),
                'counts':{'attention':sum(t['state'] in ('waiting_input','waiting_approval') for t in tasks),
                          'running':sum(t['state']=='running' for t in tasks),'done':sum(t['state']=='done' and self.clock()-t['activity_at']<7200 for t in tasks)}}
        tasks = {t['key']:t for t in self.tasks()};sid = path.rsplit('/',1)[-1];task = tasks.get(sid)
        if method == 'GET' and path.startswith('/api/tasks/') and task:return 200, 'application/json', {'task':task}
        if method == 'GET' and path.startswith('/api/control/tasks/') and task:
            connected = self.scenario != 'offline'
            commands = []
            if self.scenario == 'pending':commands = [{'id':'demo-command','kind':'reply','created':self.started,
                'state':'unknown' if task['tool']=='antigravity' else 'queued','detail':'合成回执，不会派发操作'}]
            if self.scenario == 'queued':
                commands = [{'id':'demo-waiting-1','kind':'reply','created':self.started,
                             'state':'waiting','text':'完成当前步骤后，再检查边界情况。','detail':'等待当前运行结束'}]
            return 200, 'application/json', {'connected':connected,'capability':self.capability(task) if connected else None,
                                            'commands':commands,'reason':'' if connected else '演示：原应用控制桥已离线'}
        if method == 'GET' and path.startswith('/api/history/tasks/') and task:
            if self.scenario == 'offline':return 409, 'application/json', {'error':'history_unavailable'}
            if self.scenario == 'media':
                return 200, 'application/json', {'ready':True, 'messages':[
                    {'id':'u1', 'role':'user', 'text':'请查看合成链接和图片。'},
                    {'id':'a1', 'role':'assistant', 'text':'[代码已提交](https://example.test/commit/example)\n\n![原设备图片](</example/image (1).png>)\n\n![缺失图片](/example/missing.png)\n\n[本地文件](/example/report.txt)\n\n```text\n![代码中的示例](/example/not-a-reference.png)\n```',
                     'attachments':[{'id':'A'*32, 'source':'/example/image (1).png', 'name':'image (1).png', 'mime':'image/png', 'width':640, 'height':360},
                                    {'source':'/example/missing.png', 'name':'missing.png', 'error':'attachment_unavailable'}]}], 'next_cursor':None}
            if self.scenario.startswith('history_pagination'):
                cursor = parse_qs(parsed.query).get('cursor', [None])[0]
                if self.scenario == 'history_pagination_failure' and cursor == 'process-older-2' and sid not in self.history_failures:
                    self.history_failures.add(sid)
                    return 503, 'application/json', {'error': 'history_timeout'}
                page = pagination_history(cursor)
                return (200, 'application/json', page) if page else (409, 'application/json', {'error': 'history_cursor_expired'})
            if self.scenario == 'refresh':
                cursor = parse_qs(parsed.query).get('cursor', [None])[0]
                if cursor not in (None, 'earlier'):
                    return 409, 'application/json', {'error': 'history_cursor_expired'}
                update = int((self.clock() - self.started) // 5)
                return 200, 'application/json', reader_history(cursor == 'earlier', update)
            content = '合成结果：本轮修改已经检查。\n\n- 项目、设备和 Harness 可区分\n- 继续回复保持原会话上下文'
            if self.scenario == 'long':content += '\n\n' + ('长内容用于检查滚动、换行、代码与较早记录。\n\n' * 50) + '\n```text\n' + 'long_identifier_'*25 + '\n```'
            return 200, 'application/json', {'ready':True,'messages':[{'id':'u1','role':'user','text':'请检查本轮修改。'},
                 {'id':'p1','role':'assistant','phase':'commentary','text':'合成过程说明，与工具调用一起折叠。'},
                 {'id':'t1','role':'tool','label':'示例读取','text':'合成工具输出，不包含真实执行记录。'},
                 {'id':'p2','role':'assistant','phase':'commentary','text':'合成的中间核对过程。'},
                 {'id':'t2','role':'tool','label':'示例检查','text':'第二个工具结果，不包含真实记录。'},
                 {'id':'a1','role':'assistant','text':content}], 'next_cursor':None}
        if method == 'GET' and path.startswith('/api/attachments/tasks/') and task:
            if self.scenario == 'media' and parse_qs(parsed.query).get('id') == ['A'*32]:
                return 200, 'image/png', media_fixture()
            return 404, 'application/json', {'error':'attachment_unavailable'}
        if method == 'GET' and path == '/api/settings':
            return 200, 'application/json', {'machine':'Example Hub','port':8090,'autostart':False,
                'local_ips':['192.0.2.30'],'adapters':{tool:{'enabled':True} for tool in ('codex','antigravity','dsh')},
                'hub_urls':[],'ingest_token':'DEMO-NOT-A-CREDENTIAL','hub_token':'','summary_token':'DEMO-READ-ONLY','webhooks':[]}
        if method == 'GET' and path == '/api/control/devices':
            return 200, 'application/json', {'devices':[{'device_id':m['machine_id'],'name':m['machine'],'configured':True,
                'enabled':True,'connected':self.scenario != 'offline','sessions':m['tasks'],
                'harnesses':{tool:sum(t['tool'] == tool and t['machine_id'] == m['machine_id'] for t in tasks.values())
                             for tool in ('codex','antigravity','dsh')}} for m in self.machines().values()]}
        if method == 'PATCH' and path.startswith('/api/workspace/tasks/') and task:
            self.read_at[sid] = self.clock()
            return 200, 'application/json', {'ui':{'read_at':self.read_at[sid]}}
        if method == 'POST' and path.startswith('/api/tasks/') and path.endswith('/ack'):
            self.acked.add(path.split('/')[-2]);return 200, 'application/json', {'ok':True}
        if method != 'GET':return 409, 'application/json', {'ok':False,'error':'preview_read_only'}
        return 404, 'application/json', {'error':'not_found'}


def handler(preview):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):pass
        def do_GET(self):self.dispatch()
        def do_POST(self):self.dispatch()
        def do_PATCH(self):self.dispatch()
        def do_DELETE(self):self.dispatch()
        def dispatch(self):
            # Discard all submitted contents, especially demo passwords. Do not
            # log or persist them and do not interpret arbitrary control actions.
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 65536:
                self.send_error(413);return
            self.rfile.read(length)
            if preview.scenario == 'slow' and self.command == 'GET' and self.path == '/api/settings':
                time.sleep(2)  # Browser acceptance: navigate away before this response.
            if (preview.scenario == 'history_pagination_slow' and self.command == 'GET'
                    and urlsplit(self.path).path.startswith('/api/history/tasks/')
                    and parse_qs(urlsplit(self.path).query).get('cursor') == ['process-older-2']):
                time.sleep(1)
            with preview.lock:status,kind,data = preview.route(self.command,self.path)
            encoded = data if isinstance(data, bytes) else (json.dumps(data,ensure_ascii=False) if kind == 'application/json' else data).encode()
            self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(encoded)))
            self.end_headers();self.wfile.write(encoded)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18093)
    parser.add_argument('--scenario',choices=SCENARIOS,default='normal')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:parser.error('port must be between 1 and 65535')
    server = ThreadingHTTPServer(('127.0.0.1',args.port),handler(Preview(args.scenario)))
    print(f'Synthetic UI only: http://127.0.0.1:{args.port}/__preview',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__ == '__main__':main()

"""Antigravity's project permission picker over its managed native RPC.

UpdateProject takes the entire project. Keep it on the device, bind the panel
choice to that snapshot, and verify unrelated settings after the single write.
"""
import copy
import hashlib
import re
import threading
import time
from .broker import ControlError, encoded, text, validate_action

_SCOPE = 'project-permissions'
_FIELDS = ('permissionPreset', 'autoExecutionPolicy', 'fileAccessPolicy', 'sandboxMode')
_PRESET_NAMES = ('UNSPECIFIED', 'DEFAULT', 'REQUEST_REVIEW', 'TURBO', 'VETTED', 'NONE', 'AUTO')
_EXECUTION_NAMES = ('UNSPECIFIED', 'OFF', 'AUTO', 'EAGER', 'PROCEED_IN_SANDBOX')
_FILE_NAMES = ('UNSPECIFIED', 'ALLOW', 'ASK', 'DENY')
_BACKEND_NAMES = ('UNSPECIFIED', 'LOCAL', 'REMOTE_AICODE', 'REMOTE_ENTERPRISE')
# Values used by the native project picker (the global picker's defaults differ).
_VALUES = {'use_global':(0,0,0,None), 'request_review':(2,1,2,False),
           'default':(1,4,3,True), 'auto':(6,2,2,True), 'turbo':(3,3,1,False)}
_LABELS = {
    'use_global':('跟随全局', '此项目使用全局权限设置'),
    'request_review':('Request Review', '命令执行需要审批；关闭终端沙盒'),
    'default':('Default', '在沙盒内执行；拒绝访问工作区外的文件'),
    'auto':('Auto (Preview)', '使用终端沙盒，由原应用决定是否执行命令'),
    'turbo':('Turbo', '关闭终端沙盒，允许无审批执行命令及访问工作区外的文件'),
}

def _option(value):
    label, description = _LABELS[value]
    return {'value':value, 'label':label, 'description':description}


def valid_capability(policy):
    if not isinstance(policy, dict) or set(policy) != {'current','options','scope','revision','project_id','project_name'}:
        return False
    if (policy['scope'] != _SCOPE or not text(policy['project_id'],200) or not text(policy['project_name'],250)
            or not isinstance(policy['revision'], str) or not re.fullmatch(r'[0-9a-f]{64}', policy['revision'])
            or policy['current'] not in (*_VALUES, 'custom')):
        return False
    options = policy['options']
    if not isinstance(options,list) or not 1 <= len(options) <= len(_VALUES):return False
    offered = [option for value in _VALUES if (option := _option(value)) in options]
    return options == offered


def _enum(value, names, prefix):
    if type(value) is int and 0 <= value < len(names):return value
    if isinstance(value,str) and value.startswith(prefix) and value[len(prefix):] in names:
        return names.index(value[len(prefix):])
    return -1


def _knobs(project):
    settings = project.get('settings', {})
    sandbox = settings.get('sandboxMode')
    if sandbox is not None and type(sandbox) is not bool:
        # bool/int equality must not turn an unrecognized native value into a
        # supported permission preset.
        sandbox = 'unknown'
    return (_enum(settings.get('permissionPreset',0), _PRESET_NAMES, 'AGENT_PERMISSION_PRESET_'),
            _enum(settings.get('autoExecutionPolicy',0), _EXECUTION_NAMES, 'CASCADE_COMMANDS_AUTO_EXECUTION_'),
            _enum(settings.get('fileAccessPolicy',0), _FILE_NAMES, 'AGENT_SETTING_POLICY_'),
            sandbox)


def _protected(project):
    rest = copy.deepcopy(project)
    rest.pop('updatedAt', None)
    settings = rest.pop('settings', {})
    for field in _FIELDS:settings.pop(field,None)
    if settings:rest['settings'] = settings
    return rest


class ProjectPermissions:
    def __init__(self, rpc):
        self.rpc = rpc
        self.lock = threading.RLock()
        self.gates = None
        self.expires = 0

    def _available_values(self, fresh):
        if not fresh and self.gates is not None and time.monotonic() < self.expires:
            return self.gates
        # Cache negative discovery too. Optional RPC failures must leave the
        # native reply and one-time approval bridge available.
        self.gates, self.expires = (), time.monotonic() + 30
        controls = self.rpc.call('FetchAdminControls', {})
        if not isinstance(controls, dict):return ()
        if controls.get('adminControlsApplicable') is True or controls.get('agentControls'):
            # A managed installation may clamp several of the preset fields.
            # Only inheritance requires no reconstruction of its admin policy.
            self.gates = ('use_global',)
            return self.gates
        if controls.get('adminControlsApplicable',False) is not False:return ()
        response = self.rpc.call('GetServerConfiguration', {})
        config = response.get('config') if isinstance(response,dict) else None
        if not isinstance(config,dict):return ()
        sandbox = config.get('sandboxAvailable') is True
        flags = self.rpc.call('GetMendelFlags', {}) if sandbox else {}
        experiment_config = flags.get('experimentConfig', {}) if isinstance(flags,dict) else None
        if not isinstance(experiment_config,dict):return ()
        experiments = experiment_config.get('experiments', [])
        if not isinstance(experiments,list) or any(not isinstance(flag,dict) for flag in experiments):return ()
        auto = any(flag.get('keyString') == 'enable-auto-permission-preset'
                   and flag.get('disabled',False) is False for flag in experiments)
        values = ['use_global','request_review']
        if sandbox:
            values.append('default')
            if auto:values.append('auto')
        values.append('turbo')
        self.gates = tuple(values)
        return self.gates

    def _read(self, sid, project_id, fresh):
        if not text(project_id,200) or project_id == 'outside-of-project':return None,None
        values = self._available_values(fresh)
        if not values:return None,None
        metadata_result = self.rpc.call('GetConversationMetadata', {'conversationId':sid})
        metadata = metadata_result.get('metadata') if isinstance(metadata_result,dict) else None
        if not isinstance(metadata,dict) or metadata.get('projectId') != project_id:return None,None
        result = self.rpc.call('ReadProject', {'id':project_id})
        project = result.get('project') if isinstance(result,dict) else None
        if (not isinstance(result,dict) or result.get('notFoundOnDisk') or result.get('errorMessage') or not isinstance(project,dict)
                or project.get('id') != project_id or project.get('archived')
                or _enum(project.get('backendType',0), _BACKEND_NAMES, 'BACKEND_TYPE_') not in (0,1)
                or not isinstance(project.get('settings',{}),dict)):
            return None,None
        # Current native ReadProject omits the legacy conversation list; native
        # conversation metadata is authoritative for project membership. If a
        # version does supply a list, inconsistent ownership fails closed.
        if 'projectConversations' in project:
            conversations = project['projectConversations']
            if not isinstance(conversations,dict):return None,None
            members = conversations.get('conversations', [])
            if not isinstance(members,list):return None,None
            if not any(isinstance(row,dict) and row.get('conversationId')==sid for row in members):return None,None
        knobs = _knobs(project)
        current = next((key for key, fields in _VALUES.items() if knobs == fields),'custom')
        options = [_option(value) for value in values]
        cap = {'scope':_SCOPE,'project_id':project_id,'project_name':project.get('name') or project_id,
               'current':current,'options':options,
               'revision':hashlib.sha256(encoded([sid,project,values]).encode()).hexdigest()}
        return (cap,project) if valid_capability(cap) else (None,None)

    def capability(self, sid, project_id, fresh=False):
        try:
            with self.lock:return self._read(sid,project_id,fresh)[0]
        except (ControlError, OSError, ValueError, TypeError, AttributeError):
            return None

    def execute(self, sid, action, snapshot_single, authorize):
        rows = snapshot_single(sid)
        if len(rows)!=1 or rows[0].get('requests') or not rows[0].get('reply'):
            raise ControlError('policy_changed')
        validate_action(rows[0], action)
        with self.lock:
            meta = self.rpc.call('GetConversationMetadata', {'conversationId':sid})
            metadata = meta.get('metadata') if isinstance(meta,dict) else None
            project_id = metadata.get('projectId') if isinstance(metadata,dict) else None
            cap, project = self._read(sid,project_id,True)
            validate_action({'policy':cap}, action)
            value = action['value']
            if value == cap['current']:
                if authorize() is not True:raise ControlError('policy_changed')
                return 'policy_updated'
            changed = copy.deepcopy(project)
            settings = changed.setdefault('settings', {})
            for field, new in zip(_FIELDS,_VALUES[value]):
                if new is None:settings.pop(field,None)
                else:settings[field] = new
            # Native discovery can take seconds. A local scope/opt-in revocation
            # during those reads must reject before any project side effect.
            # Keep this outside the uncertain-write handler below.
            if authorize() is not True:raise ControlError('policy_changed')
            # The native protocol has no transaction comparing a revision.
            # Consume the exact snapshot we just validated and submit only once.
            try:
                if self.rpc.call('UpdateProject', {'project':changed}) is None:return 'unknown'
                after_cap, after = self._read(sid,project_id,True)
                if (after_cap and after_cap['current']==value and _protected(after)==_protected(project)):
                    return 'policy_updated'
            except (ControlError,OSError,ValueError,AttributeError,TypeError):
                pass
            return 'unknown'

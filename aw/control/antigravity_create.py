"""Create a native conversation in its source project's default environment.

Only the source model selection is inherited. Never copy tool configuration,
static execution policy, profiles or permission grants from another conversation.
"""
import hashlib
import json
import re
import uuid
from pathlib import Path

from .broker import ControlError, encoded, validate_action
from .sessions import canonical_id


def _model(value):
    return isinstance(value, str) and value not in ('MODEL_UNSPECIFIED', 'MODEL_GOOGLE_GEMINI_INTERNAL_BYOM') and bool(re.fullmatch(r'MODEL_[A-Z0-9_]{1,180}', value))


def _plan_model(config):
    if not isinstance(config, dict):
        return None
    planner = config.get('plannerConfig')
    value = planner.get('planModel') if isinstance(planner, dict) else None
    return value if _model(value) else None


class ProjectCreation:
    def __init__(self, rpc, scope_path):
        self.rpc = rpc
        self.scope_path = Path(scope_path)

    def _all_scope(self):
        try:
            value = json.loads(self.scope_path.read_text(encoding='utf-8'))['antigravity']
            return value.get('scope') == 'all'
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False

    def _read(self, sid, expected_project=None):
        if not self._all_scope() or not canonical_id(sid):
            return None
        meta = self.rpc.call('GetConversationMetadata', {'conversationId':sid})
        metadata = meta.get('metadata') if isinstance(meta, dict) else None
        if not isinstance(metadata, dict):
            return None
        pid = metadata.get('projectId')
        if not canonical_id(pid) or (expected_project is not None and pid != expected_project):
            return None
        result = self.rpc.call('ReadProject', {'id':pid})
        project = result.get('project') if isinstance(result, dict) else None
        if (not isinstance(project, dict) or result.get('notFoundOnDisk') or result.get('errorMessage')
                or project.get('id') != pid or project.get('archived')
                or type(project.get('backendType')) is bool
                or project.get('backendType') not in (None,0,1,'BACKEND_TYPE_UNSPECIFIED','BACKEND_TYPE_LOCAL')):
            return None
        name = project.get('name')
        if not isinstance(name, str) or not 1 <= len(name) <= 200 or '\x00' in name:
            return None
        trajectory = self.rpc.call('GetCascadeTrajectory', {'cascadeId':sid,'disableRehydration':True})
        data = trajectory.get('trajectory') if isinstance(trajectory, dict) else None
        if not isinstance(data, dict) or data.get('cascadeId') != sid:
            return None
        steps = data.get('steps', [])
        if not isinstance(steps, list):
            return None
        model = None
        for step in reversed(steps):
            if not isinstance(step, dict):
                return None
            user = step.get('userInput')
            if isinstance(user, dict) and 'userConfig' in user:
                # The latest explicit model selection is authoritative. Do not
                # silently fall back past an unsupported custom/alias model.
                model = _plan_model(user['userConfig'])
                break
        else:
            static = metadata.get('staticConfig', {})
            model = _plan_model(static.get('cascadeConfig')) if isinstance(static, dict) else None
        if not model:
            return None
        semantic_project = {key:value for key,value in project.items()
                            if key not in ('projectConversations','updatedAt')}
        return {'location':'native-project','project_id':pid,'project_name':name,'model_name':model,
                'revision':hashlib.sha256(encoded([sid,semantic_project,model]).encode()).hexdigest()}

    def capability(self, sid, project_id=None):
        try:
            return self._read(sid, project_id)
        except (ControlError, OSError, ValueError, TypeError, AttributeError):
            return None

    def execute(self, provider, command):
        source = command['session_id']
        rows = provider.session_snapshot(source)
        validate_action(rows[0] if len(rows) == 1 else {}, command['action'])
        cap = self._read(source, command['action']['project_id'])
        validate_action({'create':cap}, command['action'])
        if not self._all_scope() or source not in provider.session_ids():
            raise ControlError('create_unavailable')
        target = str(uuid.uuid4())
        result = {'state':'unknown','created_session_id':target}
        try:
            response = self.rpc.call('StartCascade', {
                'cascadeId':target, 'source':19,
                'projectEnvConfig':{'projectId':cap['project_id'],'defaultProjectEnvironment':{}}})
            if not isinstance(response, dict) or response.get('cascadeId') != target:
                return result
            meta = self.rpc.call('GetConversationMetadata', {'conversationId':target})
            if not isinstance(meta, dict) or (meta.get('metadata') or {}).get('projectId') != cap['project_id']:
                return result
            # Scope, project settings and source model may change while the
            # engine creates a chat. Recheck them before sending its first turn.
            if source not in provider.session_ids() or self._read(source, cap['project_id']) != cap:
                return result
            if not self._all_scope() or source not in provider.session_ids():
                return result
            receipt = self.rpc.call('SendUserCascadeMessage', {
                'cascadeId':target, 'items':[{'text':command['action']['text']}],
                'cascadeConfig':{'plannerConfig':{'planModel':cap['model_name']}}})
            if isinstance(receipt, dict):
                result['state'] = 'delivered'
        except (ControlError, OSError, ValueError, TypeError, AttributeError):
            pass
        return result

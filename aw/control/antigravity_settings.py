"""Read-only native labels, kept separate from permission-changing capability."""
import threading
import time

from .antigravity_create import _plan_model
from .antigravity_policy import _LABELS
from .broker import text


def selected_model(context):
    """Use the latest explicit native selection; never guess enum names."""
    if not isinstance(context, dict):return None
    trajectory = context.get('trajectory') or {}
    steps = trajectory.get('steps') if isinstance(trajectory, dict) else None
    if isinstance(steps, list):
        for step in reversed(steps):
            if not isinstance(step, dict):return None
            user = step.get('userInput')
            if isinstance(user, dict) and 'userConfig' in user:
                return _plan_model(user['userConfig'])
    metadata = context.get('metadata') or {}
    static = metadata.get('staticConfig') if isinstance(metadata, dict) else None
    return _plan_model(static.get('cascadeConfig')) if isinstance(static, dict) else None


class NativeSettings:
    def __init__(self, rpc):
        self.rpc = rpc
        self.labels = {}
        self.expires = 0
        self.lock = threading.Lock()

    def model_label(self, model):
        if not text(model, 200) or self.rpc is None:return None
        with self.lock:
            if time.monotonic() >= self.expires:
                self.labels, self.expires = {}, time.monotonic() + 60
                try:
                    response = self.rpc.call('GetUserStatus', {})
                    status = response.get('userStatus') if isinstance(response, dict) else None
                    config = status.get('cascadeModelConfigData') if isinstance(status, dict) else None
                    rows = config.get('clientModelConfigs') if isinstance(config, dict) else None
                    if isinstance(rows, list) and len(rows) <= 500:
                        labels = {}
                        for row in rows:
                            if not isinstance(row, dict):continue
                            native = row.get('modelOrAlias')
                            key = native.get('model') if isinstance(native, dict) and 'alias' not in native else None
                            label = row.get('label')
                            if text(key, 200) and text(label, 200):
                                labels.setdefault(key, set()).add(label)
                        # Cache only unambiguous model labels, never account data.
                        self.labels = {key:next(iter(names)) for key,names in labels.items() if len(names) == 1}
                except Exception:
                    # Optional display data must not disable native interactions.
                    pass
            return self.labels.get(model)

    def read(self, context, policy=None, create=None):
        if not isinstance(context, dict):context = {}
        if not isinstance(policy, dict):policy = None
        if not isinstance(create, dict):create = None
        project_id = context.get('project_id')
        in_project = bool(policy or create or (text(project_id, 200) and project_id != 'outside-of-project'))
        settings = {'scope':'project'} if in_project else {}
        notes = []
        if policy:
            current = policy.get('current')
            if current == 'custom':settings['permission'] = '自定义组合'
            elif isinstance(current, str) and current in _LABELS:settings['permission'] = _LABELS[current][0]
            name = policy.get('project_name')
            if text(name, 250):settings['project_name'] = name
        elif create and text(create.get('project_name'), 250):
            settings['project_name'] = create['project_name']
        if 'permission' not in settings:notes.append('原应用当前未提供可确认的权限设置。')
        model = selected_model(context)
        # Legacy metadata readers do not expose trajectory; the creation adapter
        # has already validated its native source model when this is present.
        if not model and 'trajectory' not in context and create:
            model = create.get('model_name')
        label = self.model_label(model)
        if label:
            settings['model'] = label
            notes.append('模型来自已保存的会话记录，不含原应用中尚未发送的选择。')
        else:notes.append('未读取到模型名称，请在 Antigravity 中查看。')
        if notes:settings['note'] = ' '.join(notes)
        return settings

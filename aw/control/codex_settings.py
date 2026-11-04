"""Read-only summaries of settings published by the native desktop owner.

Approval policy is only one part of permissions. Never infer a native permission
preset or an available model catalog from these values.
"""
import re


REASONING = {'none': '无', 'minimal': '最低', 'low': '低', 'medium': '中',
             'high': '高', 'xhigh': '很高', 'max': '最高', 'ultra': 'Ultra'}
SANDBOX = {'readOnly': '只读', 'workspaceWrite': '工作区可写',
           'dangerFullAccess': '完整文件访问', 'externalSandbox': '外部沙盒'}
REVIEWER = {'user': '由你审批', 'auto_review': '自动审查'}
APPROVAL = {'on-request': '按需审批', 'never': '需要批准时拒绝',
            'untrusted': '不受信任的操作需审批', 'on-failure': '失败后申请审批'}


def summary(state):
    """Return facts only; missing/unknown settings remain absent, not defaults."""
    history = state.get('history') or {}
    settings = history.get('latestThreadSettings') or {}
    if not isinstance(settings, dict):
        return None
    out = {}
    model = settings.get('model') or history.get('latestModel')
    # Native model IDs are identifiers, never arbitrary descriptions/paths.
    if isinstance(model, str) and len(model) <= 160 and re.fullmatch(r'[\w][\w./:-]*', model):
        out['model'] = model
    effort = settings.get('effort', history.get('latestReasoningEffort'))
    if isinstance(effort, str) and effort in REASONING:
        out['reasoning'] = REASONING[effort]

    facts = []
    sandbox = settings.get('sandboxPolicy')
    if isinstance(sandbox, dict):
        kind = sandbox.get('type')
        if isinstance(kind, str) and kind in SANDBOX:
            facts.append(SANDBOX[kind])
        # Absence is not a disabled network flag. Full access and external
        # sandboxes have different schemas; do not guess their network policy.
        if kind in ('workspaceWrite', 'readOnly') and type(sandbox.get('networkAccess')) is bool:
            facts.append('允许联网' if sandbox['networkAccess'] else '联网受限')
    reviewer = settings.get('approvalsReviewer')
    if isinstance(reviewer, str) and reviewer in REVIEWER:
        facts.append(REVIEWER[reviewer])
    approval = settings.get('approvalPolicy')
    if isinstance(approval, str) and approval in APPROVAL:
        facts.append(APPROVAL[approval])
    if facts:
        out['permission'] = ' · '.join(facts)
    if not out:
        return None
    return {**out, 'scope': 'session',
            'note': '模型及完整权限预设请在 Codex 中调整。'}

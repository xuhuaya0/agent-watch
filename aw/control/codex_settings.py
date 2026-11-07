"""Summaries and model choices published by the native desktop owner.

Approval policy is only one part of permissions. Never infer a native permission
preset or an available model catalog from these values.
"""
import hashlib
import re


REASONING = {'none': '无', 'minimal': '最低', 'low': '低', 'medium': '中',
             'high': '高', 'xhigh': '很高', 'max': '最高', 'ultra': 'Ultra'}
# The desktop composer's native labels differ from CLI enum spellings (low is
# Light). Unknown future enum values remain identifiers, never invented labels.
NATIVE_REASONING_LABELS = {'none': 'None', 'minimal': 'Minimal', 'low': 'Light',
                          'medium': 'Medium', 'high': 'High', 'xhigh': 'Extra High',
                          'max': 'Max', 'ultra': 'Ultra', 'persistent': 'Persistent'}
SANDBOX = {'readOnly': '只读', 'workspaceWrite': '工作区可写',
           'dangerFullAccess': '完整文件访问', 'externalSandbox': '外部沙盒'}
REVIEWER = {'user': '由你审批', 'auto_review': '自动审查', 'guardian_subagent': '自动审查'}
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


def valid_model_capability(value):
    from .broker import text
    if (not isinstance(value, dict)
            or not {'current', 'revision', 'options'} <= value.keys() <= {'current', 'current_label', 'effort', 'effort_status', 'effort_label', 'revision', 'options'}
            or not text(value.get('current'), 160) or not text(value.get('revision'))
            or ('current_label' in value and not text(value['current_label'], 200))
            or ('effort' in value and not text(value['effort'], 32))):
        return False
    status = value.get('effort_status')
    if (('effort_status' in value and status not in ('explicit', 'default', 'unsupported', 'unknown'))
            or status in ('explicit', 'default') and 'effort' not in value
            or status in ('unsupported', 'unknown') and 'effort' in value
            or 'effort_label' in value and ('effort' not in value or not text(value['effort_label'], 200))):
        return False
    options = value['options']
    if not isinstance(options, list) or not 0 <= len(options) <= 100:
        return False
    seen = set()
    for option in options:
        if (not isinstance(option, dict)
                or not {'value', 'label', 'efforts'} <= option.keys() <= {'value', 'label', 'efforts', 'effort_labels', 'default_effort'}
                or not text(option.get('value'), 160) or not text(option.get('label'), 200)
                or option['value'] in seen):
            return False
        efforts = option['efforts']
        if (not isinstance(efforts, list) or len(efforts) > 12
                or any(not text(effort, 32) for effort in efforts)
                or len(efforts) != len(set(efforts))
                or ('default_effort' in option and option['default_effort'] not in efforts)):
            return False
        labels = option.get('effort_labels')
        if 'effort_labels' in option and (not isinstance(labels, dict) or set(labels) != set(efforts)
                                         or any(not text(label, 200) for label in labels.values())):
            return False
        seen.add(option['value'])
    return True


def model_selection(model, catalog, provider):
    """Map only an unambiguous alias within the native provider's catalog."""
    catalog = catalog if isinstance(catalog, dict) else {}
    rows = catalog.get('models', [])
    exact = [row for row in rows if row['value'] == model]
    if len(exact) == 1:
        return exact[0]['value'], exact[0]['label']
    if not isinstance(provider, str) or not provider or catalog.get('provider', provider) != provider:
        return model, model
    prefix = provider + '/'
    def key(value):
        if value.startswith(prefix) and '/' not in value[len(prefix):]:
            return value[len(prefix):]
        return value if '/' not in value else None
    identity = key(model) if isinstance(model, str) else None
    aliases = [row for row in rows if identity is not None and key(row['value']) == identity]
    return (aliases[0]['value'], aliases[0]['label']) if len(aliases) == 1 else (model, model)


def same_model(a, b, catalog, provider):
    return a == b or model_selection(a, catalog, provider)[0] == model_selection(b, catalog, provider)[0]


def effective_model_settings(state, native, catalog):
    """Read the loaded desktop picker's selection from the same owner snapshot.

    The follower read exposes latestReasoningEffort, which can be null even
    while the picker's collaboration mode has a selected effort. Never infer
    that selection from prior turns or a model catalog's default.
    """
    if not isinstance(native, dict):
        return native
    history = state.get('history') or {}
    if ('latestCollaborationMode' not in history
            and not state.get('picker_context_required')
            and not any(key in history for key in ('resumeState', 'modelProvider'))):
        return native
    selected = history.get('latestCollaborationMode')
    settings = selected.get('settings') if isinstance(selected, dict) else None
    provider = native.get('modelProvider')
    matches = (isinstance(settings, dict) and selected.get('mode') in ('default', 'plan')
        and history.get('resumeState') == native.get('resumeState') == 'resumed'
        and isinstance(provider, str) and bool(provider)
        and history.get('modelProvider') == provider
        and (not catalog or catalog.get('provider', provider) == provider)
        and isinstance(settings.get('model'), str) and bool(settings['model'].strip())
        and 'reasoning_effort' in settings
        and (settings['reasoning_effort'] is None or
             isinstance(settings['reasoning_effort'], str) and
             re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', settings['reasoning_effort']))
        and same_model(settings['model'], native.get('model'), catalog, provider))
    # Explicit null, a partial replacement or a conflicting owner model does
    # not authorize falling back to an older effort field.
    return {**native, 'reasoningEffort': settings['reasoning_effort']} if matches else None


def model_capability(state, catalog, native, editable=True):
    from .broker import encoded
    from .codex_policy import matches_selection
    history = state.get('history') or {}
    if not isinstance(native, dict) or not native.get('model'):
        return None
    selected_settings = effective_model_settings(state, native, catalog)
    if selected_settings is None:
        native = {**native, 'reasoningEffort': None}
        editable = False
    else:
        native = selected_settings
    model, effort = native.get('model'), native.get('reasoningEffort')
    catalog = catalog if isinstance(catalog, dict) else {}
    current, label = model_selection(model, catalog, native.get('modelProvider'))
    runtime = history.get('threadRuntimeStatus') or {}
    editable = (editable and not state.get('requests') and runtime.get('type') == 'idle'
                and bool(catalog.get('revision')))
    required = (catalog.get('requirements', {}).get('autoReview') or {}).get('requiredOnModels') or []
    options = []
    for option in catalog.get('models', []) if editable else []:
        if re.sub(r'^[A-Za-z0-9_-]+/', '', option['value']) in required:
            # A reviewer name alone is insufficient: full-access/never can
            # retain that name while bypassing the required sandboxed mode.
            if not matches_selection(state, 'guardian-approvals', {**catalog, 'selected_model': option['value']}):
                continue
        options.append(option)
    result = {'current': current, 'current_label': label, 'revision': hashlib.sha256(encoded(
        [catalog.get('revision'), native, history.get('latestThreadSettings'),
         history.get('latestCollaborationMode'), runtime, bool(editable)]).encode()).hexdigest(),
        'options': options}
    if effort is not None:
        result['effort'] = effort
        result['effort_status'] = 'explicit'
        if isinstance(effort, str):
            result['effort_label'] = NATIVE_REASONING_LABELS.get(effort, effort)
    else:
        selected = next((option for option in catalog.get('models', []) if option['value'] == current), None)
        # A null owner value does not prove which default the desktop would
        # use. In particular, do not substitute the previous turn's effort or
        # an unrelated catalog option's default, even while writes are disabled.
        result['effort_status'] = ('unsupported' if selected_settings is not None and selected is not None
                                  and selected.get('efforts') == [] else 'unknown')
    return result if valid_model_capability(result) else None

"""Session approval rules and validated native permission-profile choices."""
import copy
import hashlib
import re
from .broker import encoded

OPTIONS = [
    {'value':'on-request', 'label':'需要时询问审批'},
    {'value':'never', 'label':'不询问审批（需要批准的操作将拒绝）'},
]
FIELDS = ('approvalPolicy', 'approvalsReviewer', 'sandboxPolicy', 'permissions', 'activePermissionProfile')


def valid_capability(value):
    from .broker import text
    if (not isinstance(value, dict)
            or not {'current', 'revision', 'scope', 'options'} <= value.keys() <= {'current', 'current_label', 'current_option', 'revision', 'scope', 'options', 'unavailable_options'}
            or not text(value.get('revision')) or not text(value.get('current'))):
        return False
    if 'current_label' in value and not text(value['current_label']):
        return False
    if value['scope'] == 'session-next-turn':
        return (not {'current_option', 'unavailable_options'} & value.keys()
                and value['current'] in ('on-request', 'never') and value['options'] == OPTIONS)
    if value['scope'] != 'session-permissions':
        return False
    options = value['options']
    if not isinstance(options, list) or not 0 <= len(options) <= 64:
        return False
    seen = set()
    for option in options:
        if (not isinstance(option, dict)
                or not {'value', 'label'} <= option.keys() <= {'value', 'label', 'description', 'group'}
                or not text(option.get('value')) or option['value'] in seen or option['value'] == 'custom'
                or not text(option.get('label'))
                or ('description' in option and (not isinstance(option['description'], str)
                    or len(option['description']) > 2000 or '\x00' in option['description']))
                or ('group' in option and option['group'] != 'advanced')):
            return False
        seen.add(option['value'])
    unavailable = value.get('unavailable_options', [])
    if not isinstance(unavailable, list) or len(unavailable) > 3:
        return False
    for option in unavailable:
        if (not isinstance(option, dict) or set(option) != {'value', 'label', 'reason'}
                or not text(option['value']) or option['value'] not in PRESETS or option['value'] in seen
                or not text(option['label']) or not text(option['reason'], 500)):
            return False
        seen.add(option['value'])
    if 'current_option' in value and (not text(value['current_option']) or value['current_option'] not in seen):
        return False
    current = value['current']
    return current in seen or current == 'custom' or current in PRESETS or current.startswith('profile:') and text(current[8:], 160)


def capability(state):
    history = state.get('history') or {}
    settings = history.get('latestThreadSettings') or {}
    runtime = history.get('threadRuntimeStatus') or {}
    current = settings.get('approvalPolicy')
    if (state.get('requests') or runtime.get('type') != 'idle'
            or current not in ('on-request', 'never')):
        return None
    revision = hashlib.sha256(encoded([settings, runtime]).encode()).hexdigest()
    return {'current':current, 'revision':revision, 'scope':'session-next-turn', 'options':OPTIONS}


# These are the desktop's native permission selections, not reconstructed
# sandbox paths. Profile resolution remains the responsibility of Codex.
GRANULAR = {'granular': {'sandbox_approval': False, 'rules': False,
    'skill_approval': False, 'request_permissions': True, 'mcp_elicitations': True}}
AUTO_REVIEWERS = ('auto_review', 'guardian_subagent')
PRESETS = {
    'read-only': (':read-only', 'read-only', 'on-request', 'user', '只读'),
    'auto': (':workspace', 'workspace-write', 'on-request', 'user', '工作区访问 · 按需审批'),
    'granular': (':workspace', 'workspace-write', GRANULAR, 'user', '工作区访问 · 细分审批'),
    'guardian-approvals': (':workspace', 'workspace-write', 'on-request', 'guardian_subagent', 'Approve for me'),
    'full-access': (':danger-full-access', 'danger-full-access', 'never', 'user', 'Full access'),
}


def _same(a, b):
    # JSON distinguishes booleans from numbers; Python's True == 1 does not.
    return encoded(a) == encoded(b)


def _reviewer_matches(a, b):
    return a == b or a in AUTO_REVIEWERS and b in AUTO_REVIEWERS


def _catalog_parts(catalog):
    from .broker import text
    if not isinstance(catalog, dict) or not text(catalog.get('revision')):
        return None
    config, requirements, rows = (catalog.get(key) for key in ('config', 'requirements', 'profiles'))
    if not isinstance(config, dict) or not isinstance(requirements, dict) or not isinstance(rows, list):
        return None
    for key in ('allowedSandboxModes', 'allowedApprovalsReviewers', 'allowedApprovalPolicies'):
        value = requirements.get(key)
        if value is not None and (not isinstance(value, list) or len(value) > 100):
            return None
        if key != 'allowedApprovalPolicies' and value is not None and any(not text(item) for item in value):
            return None
        if key == 'allowedApprovalPolicies' and value is not None:
            for policy in value:
                if text(policy):
                    continue
                if (not isinstance(policy, dict) or set(policy) != {'granular'}
                        or not isinstance(policy['granular'], dict) or not policy['granular']
                        or any(not text(key) or type(flag) is not bool
                               for key, flag in policy['granular'].items())):
                    return None
    allowed = requirements.get('allowedPermissionProfiles')
    if allowed is not None and (not isinstance(allowed, dict)
            or any(not text(key, 160) or type(value) is not bool for key, value in allowed.items())):
        return None
    auto_review = requirements.get('autoReview')
    if auto_review is not None and not isinstance(auto_review, dict):
        return None
    required = (auto_review or {}).get('requiredOnModels')
    if required is not None and (not isinstance(required, list)
            or any(not text(model, 160) for model in required)):
        return None
    profiles, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict) or not text(row.get('id'), 160):
            return None
        value = row['id']
        if value in seen:
            return None
        seen.add(value)
        if row.get('allowed') is not True or allowed is not None and allowed.get(value) is not True:
            continue
        description = row.get('description')
        if description is not None and (not isinstance(description, str)
                or len(description) > 2000 or '\x00' in description):
            return None
        profiles[value] = description
    return config, requirements, profiles


def _auto_review_required(requirements, model):
    required = (requirements.get('autoReview') or {}).get('requiredOnModels') or []
    if not required:
        return False
    if not isinstance(model, str) or not model:
        return None  # A missing selected model cannot bypass a model restriction.
    if '/' in model:
        prefix, model = model.split('/', 1)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', prefix) or '/' in model or not model:
            return None
    return model in required


def _allowed_preset(preset, requirements):
    _, sandbox, approval, reviewer, _ = preset
    modes, policies, reviewers = (requirements.get(key) for key in
        ('allowedSandboxModes', 'allowedApprovalPolicies', 'allowedApprovalsReviewers'))
    return ((modes is None or sandbox in modes)
        and (policies is None or any(_same(policy, approval) for policy in policies))
        and (reviewers is None or any(_reviewer_matches(item, reviewer) for item in reviewers)))


def _native_choices(catalog):
    parts = _catalog_parts(catalog)
    if parts is None:
        return []
    config, requirements, profiles = parts
    required = _auto_review_required(requirements, catalog.get('selected_model'))
    if required is None:
        return []
    available = [value for value, preset in PRESETS.items()
        if preset[0] in profiles and _allowed_preset(preset, requirements)]
    features = config.get('features') or {}
    guardian_disabled = isinstance(features, dict) and features.get('guardian_approval') is False
    # Feature permission alone does not establish the desktop's Statsig/UI gate.
    # Keep the option only with an authoritative default or managed requirement.
    guardian_known = (required or config.get('approvals_reviewer') in AUTO_REVIEWERS
                      or available == ['guardian-approvals'])
    if guardian_disabled or not guardian_known:
        available = [value for value in available if value != 'guardian-approvals']
    if required:
        available = [value for value in available if value == 'guardian-approvals']
    choices = []
    for value in available:
        profile, _, approval, reviewer, label = PRESETS[value]
        choices.append(({'value': value, 'label': label}, {'permissions': profile,
            'approvalPolicy': approval, 'approvalsReviewer': reviewer}))
    # The native profile list is authoritative for named profiles. Selecting
    # one sends only its ID; never infer its sandbox or approval configuration.
    # Model-required review cannot be proven for an unresolved named profile.
    policies, reviewers = requirements.get('allowedApprovalPolicies'), requirements.get('allowedApprovalsReviewers')
    named_allowed = ((policies is None or 'selected_approval_policy' in catalog and any(
        _same(policy, catalog['selected_approval_policy']) for policy in policies))
        and (reviewers is None or 'selected_reviewer' in catalog and any(
            _reviewer_matches(reviewer, catalog['selected_reviewer']) for reviewer in reviewers)))
    if not required and named_allowed:
        for value, description in profiles.items():
            if value.startswith(':'):
                continue
            option = {'value': 'profile:' + value, 'label': value}
            if description:
                option['description'] = description
            choices.append((option, {'permissions': value}))
    return choices if len(choices) <= 64 else []


def _default_ask_mode(catalog, workspace_kind):
    """Resolve the desktop's single Ask entry, not its effective custom config.

    The native composer prefers the allowed default permission-profile ID;
    otherwise it uses the project/projectless workspace preset. Its other
    read-only/auto/granular variants are not separate menu entries.
    """
    parts = _catalog_parts(catalog)
    if parts is None:
        return None
    config, requirements, profiles = parts
    workspace = ('granular' if workspace_kind == 'projectless'
                 and _allowed_preset(PRESETS['granular'], requirements) else 'auto')
    configured = config.get('default_permissions')
    default = requirements.get('defaultPermissions')
    if default is None and isinstance(configured, str) and configured in profiles:
        default = configured
    if isinstance(default, str) and default in profiles:
        if default == ':read-only':
            return 'read-only'
        if default == ':workspace':
            return workspace
    return workspace


def _unavailable_reason(value, catalog, editable):
    if not editable:
        return '会话正在运行或等待处理，暂时不能更改'
    config, requirements, profiles = _catalog_parts(catalog)
    if _auto_review_required(requirements, catalog.get('selected_model')):
        return '当前模型要求自动审查'
    preset = PRESETS[value]
    if preset[0] not in profiles or not _allowed_preset(preset, requirements):
        return '原生权限配置或管理策略不允许此选项'
    features = config.get('features') or {}
    if value == 'guardian-approvals':
        if isinstance(features, dict) and features.get('guardian_approval') is False:
            return '已在 Codex 配置中禁用自动审查'
        return '尚未读取到 Codex 原生自动审查选项的可用状态'
    return '当前原生设置不允许此选项'


def _matches_preset(settings, value, require_profile=True):
    profile, sandbox, approval, reviewer, _ = PRESETS[value]
    active, policy = settings.get('activePermissionProfile'), settings.get('sandboxPolicy')
    if ((require_profile and (not isinstance(active, dict) or active.get('id') != profile))
            or not _same(settings.get('approvalPolicy'), approval)
            or not _reviewer_matches(settings.get('approvalsReviewer'), reviewer)
            or not isinstance(policy, dict)):
        return False
    if sandbox == 'workspace-write':
        return policy.get('type') == 'workspaceWrite' and all(policy.get(key) is False
            for key in ('networkAccess', 'excludeSlashTmp', 'excludeTmpdirEnvVar'))
    if sandbox == 'read-only':
        return policy.get('type') == 'readOnly' and policy.get('networkAccess') is False
    return policy.get('type') == 'dangerFullAccess'


def settings_for(value, catalog):
    """Return a fresh native settings payload only for a still-allowed choice."""
    return next((copy.deepcopy(settings) for option, settings in _native_choices(catalog)
                 if option['value'] == value), None)


def matches_selection(state, value, catalog):
    """Confirm an offered write, retaining stricter rules than current display."""
    payload = settings_for(value, catalog)
    if payload is None:
        return False
    settings = (state.get('history') or {}).get('latestThreadSettings') or {}
    if value in PRESETS:
        return _matches_preset(settings, value)
    active = settings.get('activePermissionProfile')
    return isinstance(active, dict) and active.get('id') == payload['permissions']


def current_permission(settings):
    """Recognize the native current mode independently of selectable choices.

    Older native threads can have a complete effective mode without a stored
    permission-profile ID. This is display evidence, not permission to write
    that profile or proof that a later profile update was applied.
    """
    from .broker import text
    active = settings.get('activePermissionProfile')
    if isinstance(active, dict) and text(active.get('id'), 160) and not active['id'].startswith(':'):
        return 'profile:' + active['id'], active['id']
    for value in PRESETS:
        if _matches_preset(settings, value, require_profile=active is not None):
            label = 'Ask for approval' if value in ('read-only', 'auto', 'granular') else PRESETS[value][4]
            return value, label
    return 'custom', 'Custom'


def native_capability(state, catalog, editable=True):
    history = state.get('history') or {}
    settings, runtime = history.get('latestThreadSettings') or {}, history.get('threadRuntimeStatus') or {}
    if (not isinstance(settings, dict) or not isinstance(runtime, dict)
            or not isinstance(settings.get('sandboxPolicy'), dict) or settings.get('approvalPolicy') is None):
        return None
    editable = editable and not state.get('requests') and runtime.get('type') == 'idle'
    choices = _native_choices(catalog) if editable else []
    primary = _default_ask_mode(catalog, history.get('workspaceKind'))
    current, current_label = current_permission(settings)
    options = []
    for native_option, _ in choices:
        option, value = dict(native_option), native_option['value']
        if value in PRESETS:
            if value == primary:
                option['label'] = 'Ask for approval'
            elif value not in ('guardian-approvals', 'full-access'):
                continue
        options.append(option)
    result = {'current': current, 'current_label': current_label, 'scope': 'session-permissions', 'options': options,
        'revision': hashlib.sha256(encoded([catalog, settings, runtime, bool(editable)]).encode()).hexdigest()}
    # Keep unsupported entries separate from writable choices. The central
    # broker accepts actions only from options, never unavailable_options.
    if primary is not None:
        shown = {option['value'] for option in options}
        missing = [{'value': value, 'label': label,
                    'reason': _unavailable_reason(value, catalog, editable)}
                   for value, label in ((primary, 'Ask for approval'),
                       ('guardian-approvals', 'Approve for me'), ('full-access', 'Full access'))
                   if value not in shown]
        if missing:
            result['unavailable_options'] = missing
        selected = primary if current in ('read-only', 'auto', 'granular') else current
        if selected in shown or any(option['value'] == selected for option in missing):
            result['current_option'] = selected
    return result if valid_capability(result) else None


def unchanged_permissions(before, after):
    a = (before.get('history') or {}).get('latestThreadSettings') or {}
    b = (after.get('history') or {}).get('latestThreadSettings') or {}
    return all(a.get(k) == b.get(k) for k in FIELDS if k != 'approvalPolicy')

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
    if (not isinstance(value, dict) or set(value) != {'current', 'revision', 'scope', 'options'}
            or not text(value.get('revision')) or not text(value.get('current'))):
        return False
    if value['scope'] == 'session-next-turn':
        return value['current'] in ('on-request', 'never') and value['options'] == OPTIONS
    if value['scope'] != 'session-permissions':
        return False
    options = value['options']
    if not isinstance(options, list) or not 1 <= len(options) <= 64:
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
    return value['current'] == 'custom' or value['current'] in seen


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


def _default_ask_mode(config, workspace_kind, available):
    sandbox, approval = config.get('sandbox_mode'), config.get('approval_policy')
    default = 'granular' if workspace_kind == 'projectless' and 'granular' in available else 'auto'
    if sandbox is None and approval is None:
        return default
    if sandbox in (None, 'read-only') and approval in (None, 'on-request'):
        return 'read-only'
    workspace = config.get('sandbox_workspace_write')
    clean = workspace is None or isinstance(workspace, dict) and (
        not workspace.get('writable_roots')
        and all(workspace.get(key, False) is False for key in
                ('network_access', 'exclude_slash_tmp', 'exclude_tmpdir_env_var')))
    if sandbox == 'workspace-write' and clean:
        if _same(approval, GRANULAR):
            return 'granular'
        if approval in (None, 'on-request'):
            return 'auto'
    return None


def _matches_preset(settings, value):
    profile, sandbox, approval, reviewer, _ = PRESETS[value]
    active, policy = settings.get('activePermissionProfile'), settings.get('sandboxPolicy')
    if (not isinstance(active, dict) or active.get('id') != profile
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


def native_capability(state, catalog):
    history = state.get('history') or {}
    settings, runtime = history.get('latestThreadSettings') or {}, history.get('threadRuntimeStatus') or {}
    if (state.get('requests') or not isinstance(settings, dict) or not isinstance(runtime, dict)
            or runtime.get('type') != 'idle'):
        return None
    choices = _native_choices(catalog)
    if not choices:
        return None
    available = [option['value'] for option, _ in choices]
    primary = _default_ask_mode(catalog['config'], history.get('workspaceKind'), available)
    current, options = 'custom', []
    active = settings.get('activePermissionProfile')
    for native_option, payload in choices:
        option, value = dict(native_option), native_option['value']
        if value in PRESETS:
            if _matches_preset(settings, value):
                current = value
            if value == primary:
                option['label'] = 'Ask for approval'
                option['description'] = PRESETS[value][4]
            elif value not in ('guardian-approvals', 'full-access'):
                option['group'] = 'advanced'
        elif isinstance(active, dict) and active.get('id') == payload['permissions']:
            current = value
        options.append(option)
    options.sort(key=lambda option: option.get('group') == 'advanced')
    result = {'current': current, 'scope': 'session-permissions', 'options': options,
        'revision': hashlib.sha256(encoded([catalog, settings, runtime]).encode()).hexdigest()}
    return result if valid_capability(result) else None


def unchanged_permissions(before, after):
    a = (before.get('history') or {}).get('latestThreadSettings') or {}
    b = (after.get('history') or {}).get('latestThreadSettings') or {}
    return all(a.get(k) == b.get(k) for k in FIELDS if k != 'approvalPolicy')

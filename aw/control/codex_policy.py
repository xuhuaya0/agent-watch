"""Session-only approval changes; sandbox and reviewer are never rewritten."""
import hashlib
from .broker import encoded

OPTIONS = [
    {'value':'on-request', 'label':'需要时询问审批'},
    {'value':'never', 'label':'不询问审批（需要批准的操作将拒绝）'},
]
FIELDS = ('approvalPolicy', 'approvalsReviewer', 'sandboxPolicy', 'permissions', 'activePermissionProfile')


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


def unchanged_permissions(before, after):
    a = (before.get('history') or {}).get('latestThreadSettings') or {}
    b = (after.get('history') or {}).get('latestThreadSettings') or {}
    return all(a.get(k) == b.get(k) for k in FIELDS if k != 'approvalPolicy')

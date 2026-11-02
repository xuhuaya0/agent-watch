"""Native session permission presets, distinct from Codex approval-only policy.

Use the installed Harness command registry, never shell execution or settings
writes. Presets may change sandbox and approval together; the UI says so.
"""
import hashlib
import re
import time
from .broker import ControlError, encoded, text, validate_action

SCOPE = 'session-permissions'
DEFINITION = '@deepseek-ai/dsh-permission-presets'
VALUE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')


def options(catalog):
    rows = catalog.get('options') if isinstance(catalog, dict) else None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        return None
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):return None
        value, label, description = row.get('value'), row.get('name'), row.get('description', '')
        if (not isinstance(value, str) or not VALUE.fullmatch(value) or value == 'custom'
                or value in seen or not text(label, 200) or not isinstance(description, str)
                or len(description) > 2000 or '\x00' in description):
            return None
        seen.add(value)
        result.append({'value':value, 'label':label, 'description':description})
    return result


def valid_capability(value):
    if (not isinstance(value, dict) or set(value) != {'current','revision','scope','options'}
            or value.get('scope') != SCOPE or not text(value.get('revision'))
            or not text(value.get('current'), 80)):
        return False
    rows = value.get('options')
    if not isinstance(rows, list):return False
    normalized = options({'options':[{'value':r.get('value'), 'name':r.get('label'),
        'description':r.get('description')} for r in rows if isinstance(r,dict)]})
    return (normalized == rows and normalized is not None
            and value['current'] in ['custom', *[r['value'] for r in rows]])


def eligible(row):
    projection = row.get('projections') or {}
    values = projection.get('values') or {}
    questions = values.get('userQuestions') or {}
    active = questions.get('active', (questions.get('questions') or {}).get('active', []))
    inbox = values.get('inbox') or {}
    return (row.get('agentAvailable') is True and row.get('running') is False
            and not row.get('archived') and row.get('origin') != 'subagent'
            and not row.get('blank') and not active
            and not inbox.get('next-turn') and not inbox.get('next-step'))


class Permissions:
    def __init__(self):
        self.gateway = None
        self.catalog = None
        self.checked = -100
        self.commands = {}

    def read(self, gateway, row, fresh=False):
        if not eligible(row):return None
        if self.gateway is not gateway:
            self.gateway, self.catalog, self.checked, self.commands = gateway, None, -100, {}
        now = time.monotonic()
        if fresh or now-self.checked > 15:
            self.checked, self.catalog = now, None
            self.catalog = options(gateway.call('permissionPresets/catalog', {}))
        if not self.catalog:return None
        sid = row['sessionId']
        self.commands = {s:v for s,v in self.commands.items() if now-v[0] < 15}
        if fresh or sid not in self.commands:
            defs = gateway.call('commands/list', {'agentId':sid})
            matched = [d for d in defs if isinstance(d,dict) and d.get('name')=='permission'] if isinstance(defs,list) else []
            self.commands[sid] = (now, len(matched)==1 and matched[0].get('definitionId')==DEFINITION)
        if not self.commands[sid][1]:return None
        current = ((row.get('projections') or {}).get('values') or {}).get('permissions', {}).get('currentValue')
        if current not in ['custom', *[r['value'] for r in self.catalog]]:return None
        return {'current':current, 'revision':hashlib.sha256(encoded([sid,current,self.catalog]).encode()).hexdigest(),
                'scope':SCOPE, 'options':self.catalog}


def execute(provider, command):
    sid, action = command['session_id'], command['action']
    gateway = provider.gateway
    # Read catalog, command ownership and native idle/queue state again just
    # before the single write. No automatic retry after an uncertain response.
    rows = gateway.call('session/list', {'_request':{}})['items']
    native = next((r for r in rows if r.get('sessionId') == sid), None)
    if not native:raise ControlError('policy_changed')
    policy = provider.permissions.read(gateway, native, fresh=True)
    validate_action({'policy':policy}, action)
    if action['value'] == policy['current']:return 'policy_updated'
    if gateway is not provider.gateway:
        raise ControlError('policy_changed')
    with provider.condition:
        if any(v['frame']['agentId']==sid for v in provider.pending.values()):
            raise ControlError('policy_changed')
    try:
        result = gateway.call('commands/execute', {'agentId':sid,
            'line':'/permission '+action['value'], 'submittedAttachments':[]})
    except Exception:
        return 'unknown'
    if (not isinstance(result, dict) or not isinstance(result.get('result'),dict)
            or result['result'].get('kind') != 'success'):
        return 'unknown'
    try:
        rows = provider.session_snapshot(sid)
    except Exception:
        return 'unknown'
    if gateway is not provider.gateway or not rows:return 'unknown'
    current = (rows[0].get('policy') or {}).get('current')
    return 'policy_updated' if current == action['value'] else 'unknown'

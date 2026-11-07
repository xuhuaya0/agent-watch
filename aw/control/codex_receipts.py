"""Read-only, identity-based evidence for uncertain native reply delivery.

Text, timestamps, running state and rollout similarity are never delivery proof.
Intents contain only the original command/session identity and preparation time.
"""
import math
import time


def valid_intent(value):
    return (isinstance(value, dict) and value.get('version') == 1
            and isinstance(value.get('command_id'), str) and 0 < len(value['command_id']) <= 256
            and isinstance(value.get('session_id'), str) and 0 < len(value['session_id']) <= 256
            and type(value.get('prepared_at')) in (int, float)
            and math.isfinite(value['prepared_at']))


def prepare(command, clock=time.time):
    if (command.get('action') or {}).get('kind') != 'reply':
        return None
    intent = {'version': 1, 'command_id': command.get('id'),
              'session_id': command.get('session_id'), 'prepared_at': clock()}
    return intent if valid_intent(intent) else None


def accepted_ids(value):
    """Match the desktop's native wasMessageAccepted identity rules.

    Ordinary userMessage items come from engine item events; their clientId is
    the submitted clientUserMessageId. Optimistic turn params are not items.
    Steering needs the server's accepted identity, not its optimistic input ID.
    """
    if not isinstance(value, dict):
        return set()
    kind = value.get('type')
    identity = None
    if kind == 'userMessage':
        identity = value.get('clientId')
    elif (kind == 'steeringUserMessage' and value.get('status') == 'accepted'
          and isinstance(value.get('serverUserMessageId'), str) and value['serverUserMessageId']):
        identity = value.get('serverClientUserMessageId')
    if isinstance(identity, str) and identity:
        return {identity}
    return set()


def snapshot_ids(state, wanted=None):
    canonical = state.get('turnHistory') or {}
    if not isinstance(canonical, dict):
        return set()
    # Native mergeTurns keeps live turns over a paged canonical history. An
    # accepted identity in either source remains positive delivery evidence.
    turns = list(state.get('turns') or [])
    if canonical.get('kind') == 'canonical':
        turns.extend(((canonical.get('history') or {}).get('entitiesByKey') or {}).values())
    found = set()
    for turn in turns:
        if isinstance(turn, dict):
            for item in turn.get('items') or []:
                for identity in accepted_ids(item):
                    if wanted is None or identity in wanted:
                        found.add(identity)
    return found


def patch_ids(patches, wanted=None):
    identities = set()
    for patch in patches:
        path = patch.get('path') or []
        if patch.get('op') not in ('add', 'replace'):
            continue
        value = patch.get('value')
        items = []
        item_path = ((len(path) == 4 and path[0] == 'turns' and type(path[1]) is int and path[2] == 'items')
                     or (len(path) == 6 and path[:3] == ['turnHistory', 'history', 'entitiesByKey']
                         and path[4] == 'items'))
        array_path = ((len(path) == 3 and path[0] == 'turns' and type(path[1]) is int and path[2] == 'items')
                      or (len(path) == 5 and path[:3] == ['turnHistory', 'history', 'entitiesByKey']
                          and path[4] == 'items'))
        turn_path = ((len(path) == 2 and path[0] == 'turns' and type(path[1]) is int)
                     or (len(path) == 4 and path[:3] == ['turnHistory', 'history', 'entitiesByKey']))
        if item_path and type(path[-1]) is int:
            items = [value]
        elif array_path and isinstance(value, list):
            items = value
        elif turn_path and isinstance(value, dict):
            items = value.get('items') or []
        elif path == ['turns'] and isinstance(value, list):
            identities.update(snapshot_ids({'turns': value}, wanted))
        elif path == ['turnHistory'] and isinstance(value, dict):
            identities.update(snapshot_ids({'turnHistory': value}, wanted))
        elif path == ['turnHistory', 'history', 'entitiesByKey'] and isinstance(value, dict):
            identities.update(snapshot_ids({'turns': list(value.values())}, wanted))
        for item in items:
            for identity in accepted_ids(item):
                if wanted is None or identity in wanted:
                    identities.add(identity)
    return identities

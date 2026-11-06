"""Native run-state gates for the Hub's durable, not-yet-dispatched replies.

Waiting is a Hub state, not a claim that a Harness accepted a native queue item.
Only a fresh, positively idle native snapshot releases a waiting reply.
"""
import hashlib

from .broker import ControlError, encoded, text


class ReplyDeferred(ControlError):
    """Positive no-write outcome; safe to return this command to Hub waiting."""
    def __init__(self):
        super().__init__('reply_waiting')


def valid_delivery(value):
    return (isinstance(value, dict) and set(value) <= {'state', 'steer', 'revision', 'native_queue', 'epoch'}
            and value.get('state') in ('idle', 'running', 'waiting', 'unknown')
            and type(value.get('steer')) is bool
            and ('native_queue' not in value or type(value['native_queue']) is bool)
            and ('epoch' not in value or text(value['epoch']))
            and (not value['steer'] or value['state'] == 'running' and text(value.get('revision')))
            and ('revision' not in value or text(value['revision'])))


def should_wait(session, action):
    delivery = session.get('delivery')
    return (action.get('kind') == 'reply' and action.get('mode') == 'after-turn'
            and (not delivery or not delivery.get('native_queue')
                 and (delivery['state'] != 'idle' or not session.get('reply'))))


def guard_reply(session, action):
    if should_wait(session, action):
        raise ReplyDeferred()


def codex_delivery(state):
    history = state.get('history') or {}
    runtime = (history.get('threadRuntimeStatus') or {}).get('type')
    canonical = history.get('turnHistory') or {}
    turns = ((canonical.get('history') or {}).get('entitiesByKey') or {}).values()
    if canonical.get('kind') != 'canonical':
        turns = list(turns) or history.get('turns') or []
    turns = list(turns)
    # A newly recorded input or in-progress turn is not completion. Only closed
    # native turns advance the idle-release marker.
    closed = [turn for turn in turns
              if turn.get('turnId') and turn.get('status') in ('completed', 'failed', 'interrupted')]
    latest = max(enumerate(closed), key=lambda item: (item[1].get('turnStartedAtMs') or 0, item[0]))[1] if closed else None
    epoch = hashlib.sha256(encoded([latest['turnId'], latest['status']]).encode()).hexdigest() if latest else 'none'
    active = [turn.get('turnId') for turn in turns if turn.get('status') == 'inProgress']
    if state.get('requests'):
        return {'state': 'waiting', 'steer': False, 'epoch': epoch}
    if runtime == 'active' or active:
        # An exact active native turn is needed for an explicit insertion. The
        # revision changes when it ends or a different turn takes ownership.
        turn = next((turn for turn in reversed(active) if turn), None)
        result = {'state': 'running', 'steer': bool(turn), 'epoch': epoch}
        if turn:
            result['revision'] = hashlib.sha256(encoded(turn).encode()).hexdigest()
        return result
    return {'state': 'idle' if runtime == 'idle' else 'unknown', 'steer': False, 'epoch': epoch}

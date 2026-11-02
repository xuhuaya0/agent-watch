"""Create through the existing native engine in a freshly validated directory."""
import hashlib
from pathlib import PurePosixPath, PureWindowsPath
import uuid
from .broker import ControlError, encoded, validate_action


def capability(row):
    cwd = row.get('cwd')
    if (not isinstance(cwd, str) or not 0 < len(cwd) <= 2000 or '\x00' in cwd
            or not (PurePosixPath(cwd).is_absolute() or PureWindowsPath(cwd).is_absolute())):
        return None
    return {'cwd': cwd, 'revision': hashlib.sha256(encoded([row['sessionId'], cwd]).encode()).hexdigest()}


def create_session(provider, command, session):
    validate_action(session, command['action'])
    if provider.config.get('scope') != 'all':
        raise ControlError('create_unavailable')
    gateway = provider.gateway
    # Generated on the device, never accepted from a browser. No accidental
    # adoption of a caller-selected existing session and no native retry.
    sid = 'session-' + str(uuid.uuid4())
    outcome = {'state': 'unknown', 'created_session_id': sid}
    try:
        result = gateway.call('session/create', {'request': {'sessionId': sid, 'cwd': session['create']['cwd']}})
        if not isinstance(result, dict) or result.get('sessionId') != sid:
            return outcome
        # The source context may have been archived or moved while creating.
        rows = provider.session_snapshot(command['session_id'])
        if gateway is not provider.gateway or not rows:
            return outcome
        validate_action(rows[0], command['action'])
        result = gateway.call('session/prompt', {'request': {'sessionId': sid,
            'requestId': command['id'], 'mode': 'queue',
            'content': [{'type': 'text', 'text': command['action']['text']}]}})
        if isinstance(result, dict) and result.get('accepted') is True:
            outcome['state'] = 'delivered'
    except Exception:
        # Creation may have happened even when its response was lost. Preserve
        # the generated destination and never label this safe to retry.
        pass
    return outcome

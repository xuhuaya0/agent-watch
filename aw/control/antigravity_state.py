"""Bounded native interaction state, independent of accumulated chat history.

The native sidebar summary includes the complete waiting-step list with original
indices. Tail steps supply completion markers, never evidence of no pending work.
"""
import copy
import uuid

from .broker import ControlError

TAIL_STEPS = 16
WAITING = 'CORTEX_STEP_STATUS_WAITING'
STATUSES = (1, 2, 'CASCADE_RUN_STATUS_IDLE', 'CASCADE_RUN_STATUS_RUNNING')
REVISION_FIELDS = ('trajectoryId', 'stepCount', 'status', 'waitingSteps', 'lastModifiedTime',
                   'lastUserInputStepIndex', 'notFullyIdle', 'hasActiveChildren', 'hasRunningSubagents')


def unavailable():
    raise ControlError('native_session_unavailable')


def count(value):
    if type(value) is not int or not 0 <= value <= 2 ** 32 - 1:
        unavailable()
    return value


def summary(rpc, sid):
    response = rpc.call('GetAllCascadeTrajectories', {'excludeSubtrajectories': True})
    rows = response.get('trajectorySummaries') if isinstance(response, dict) else None
    row = rows.get(sid) if isinstance(rows, dict) else None
    if not isinstance(row, dict) or type(row.get('status')) not in (int, str) or row['status'] not in STATUSES:
        unavailable()
    tid = row.get('trajectoryId')
    try:
        valid = isinstance(tid, str) and str(uuid.UUID(tid)) == tid
    except ValueError:
        valid = False
    if not valid:
        unavailable()
    count(row.get('stepCount', 0))
    for name in ('notFullyIdle', 'hasActiveChildren', 'hasRunningSubagents'):
        if name in row and type(row[name]) is not bool:
            unavailable()
    return row


def read_state(rpc, sid):
    """Read two matching summaries around one bounded tail and metadata read.

    A growing/replaced trajectory, truncated tail, or unrecognised pending-step
    shape fails closed. No retry here can accidentally apply an old interaction.
    """
    # After a native host restart, its summary index can be empty until this
    # read loads an existing conversation. Looking up the summary first would
    # leave cold sessions permanently undiscoverable. Keep both summary checks
    # and validate the metadata against them before exposing any interaction.
    meta = rpc.call('GetConversationMetadata', {'conversationId': sid})
    before = summary(rpc, sid)
    tid = before['trajectoryId']; total = count(before.get('stepCount', 0))
    user_index = count(before.get('lastUserInputStepIndex', 0))
    if total and user_index >= total or not total and user_index:
        unavailable()
    if (not isinstance(meta, dict) or meta.get('trajectoryId') != tid
            or count(meta.get('numSteps', 0)) != total):
        unavailable()
    waiting = before.get('waitingSteps', [])
    if not isinstance(waiting, list):
        unavailable()
    pending = {}
    for entry in waiting:
        if not isinstance(entry, dict) or set(entry) - {'step', 'stepIndex'}:
            unavailable()
        index = count(entry.get('stepIndex', 0))
        step = entry.get('step')
        if (index >= total or index in pending or not isinstance(step, dict)
                or step.get('status') != WAITING):
            unavailable()
        pending[index] = copy.deepcopy(step)
    offset = max(0, total - TAIL_STEPS)
    response = rpc.call('GetCascadeTrajectorySteps', {
        'cascadeId': sid, 'stepOffset': offset, 'trajectoryVerbosity': 2,
        'disableRehydration': True}) if total else {'steps': []}
    steps = response.get('steps', []) if isinstance(response, dict) else None
    if not isinstance(steps, list) or len(steps) != total - offset or any(not isinstance(s, dict) for s in steps):
        unavailable()
    indexed = {offset + i: step for i, step in enumerate(steps)}
    # Pending work may be far earlier than the tail. Conversely, any waiting
    # tail step missing from the complete summary indicates an inconsistent read.
    for index, step in indexed.items():
        if step.get('status') == WAITING:
            if (index not in pending or pending[index].get('type') != step.get('type')
                    or pending[index].get('requestedInteraction') != step.get('requestedInteraction')):
                unavailable()
        elif index in pending:
            unavailable()
    # PROD_UI deliberately strips userConfig. Fetch only the latest input's
    # first step with FULL verbosity, never the full trajectory or whole turn.
    # This optional display read cannot disable otherwise valid interactions.
    model_selection = {'source': 'unavailable' if total else 'metadata'}
    if total:
        try:
            response = rpc.call('GetTrajectoryTurns', {
                'cascadeId': sid, 'stepIndices': [user_index], 'trajectoryVerbosity': 3})
            turns = response.get('turns') if isinstance(response, dict) else None
            turn = turns[0] if isinstance(turns, list) and len(turns) == 1 else None
            step = turn.get('firstStep') if isinstance(turn, dict) else None
            if (isinstance(turn, dict) and type(turn.get('startStepIndex', 0)) is int
                    and turn.get('startStepIndex', 0) == user_index and isinstance(step, dict)
                    and step.get('type') == 'CORTEX_STEP_TYPE_USER_INPUT'
                    and isinstance(step.get('userInput'), dict)):
                user = step['userInput']
                if 'userConfig' in user:
                    config = user['userConfig']
                    planner = config.get('plannerConfig') if isinstance(config, dict) else None
                    model_selection = {'source': 'user', 'config': {'plannerConfig': {
                        'planModel': planner.get('planModel') if isinstance(planner, dict) else None}}}
                elif user_index == 0:
                    model_selection = {'source': 'metadata'}
        except (ControlError, OSError, ValueError, TypeError, KeyError):
            pass
    after = summary(rpc, sid)
    if any(before.get(key) != after.get(key) for key in REVISION_FIELDS):
        unavailable()
    indexed.update(pending)
    status = before['status']
    if any(before.get(name) for name in ('notFullyIdle', 'hasActiveChildren', 'hasRunningSubagents')):
        status = 'CASCADE_RUN_STATUS_RUNNING'
    trajectory = {'trajectoryId': tid, 'cascadeId': sid, 'steps': steps,
                  'stepOffset': offset, 'partial': bool(offset),
                  'lastUserInputStepIndex': user_index, 'modelSelection': model_selection}
    return meta, trajectory, sorted(indexed.items()), status, bool(pending)

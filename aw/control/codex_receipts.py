"""Read-only, identity-based evidence for uncertain native reply delivery.

Text, timestamps, running state and rollout similarity are never delivery proof.
Intents contain only the original command/session identity and preparation time.
"""
import math
import json
import os
import time

READ_BYTES = 4 * 1024 * 1024
TOTAL_BYTES = 64 * 1024 * 1024
RECORD_BYTES = 512 * 1024
READ_SECONDS = 2


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


def completed_identity(raw, session, identity):
    """Accept only a persisted server user-item completion, never plain text."""
    if len(raw) > RECORD_BYTES:
        return False
    try:
        row = json.loads(raw)
        payload = row.get('payload') or {}
        item = payload.get('item') or {}
        finished = payload.get('completed_at_ms')
        return (row.get('type') == 'event_msg' and payload.get('type') == 'item_completed'
                and payload.get('thread_id') == session
                and isinstance(payload.get('turn_id'), str) and bool(payload['turn_id'])
                and type(finished) in (int, float) and math.isfinite(finished) and finished >= 0
                and item.get('type') == 'UserMessage' and item.get('client_id') == identity
                and isinstance(item.get('id'), str) and bool(item['id']))
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        return False


class ReceiptHistory:
    """Bounded, incremental exact-ID search of one uncompressed native rollout.

    Progress is private process memory, never text or a native-file mutation.
    Search existing history backwards, prioritizing appended records on later
    polls. At EOF only growth is read. Rotation, truncation or a budget limit
    stops the scan; absence remains unknown.
    """
    def __init__(self):
        self.stamp = None
        self.total = 0
        self.stopped = False
        self.back_end = 0
        self.back_skip = False
        self.tail_cursor = 0
        self.tail_skip = False
        self.tail_deferred = None
        self.last_size = 0
        self.read_tail_next = True

    def check(self, history, intent):
        if self.stopped or history is None or not valid_intent(intent):
            return None
        deadline = time.monotonic() + READ_SECONDS
        path = history.path(intent['session_id'])
        if path.suffix != '.jsonl':
            self.stopped = True  # Never decompress a whole history for a receipt.
            return None
        with path.open('rb') as stream:
            stat = os.fstat(stream.fileno())
            stamp = (str(path), stat.st_dev, stat.st_ino)
            fresh = self.stamp is None
            if fresh:
                self.stamp = stamp
                self.back_end = self.tail_cursor = self.last_size = stat.st_size
            if stamp != self.stamp or stat.st_size < self.last_size:
                self.stopped = True
                return None
            self.last_size = stat.st_size
            budget = min(READ_BYTES, TOTAL_BYTES - self.total)
            if budget <= 0:
                self.stopped = True
                return None
            forward = (self.tail_cursor < stat.st_size and self.tail_deferred != stat.st_size
                       and (self.read_tail_next or not self.back_end))
            if forward:
                start = self.tail_cursor
                stream.seek(start)
                block = stream.read(min(budget, stat.st_size - start))
                found = self._forward(block, start, stat.st_size, intent, deadline)
            elif self.back_end:
                end = self.back_end
                start = max(0, end - budget)
                stream.seek(start)
                block = stream.read(end - start)
                found = self._backward(block, start, end, intent, deadline, fresh)
            else:
                return None
            self.total += len(block)
            self.read_tail_next = not forward  # Continuous output cannot starve older evidence.
            after = path.stat()
            if ((str(path), after.st_dev, after.st_ino) != self.stamp
                    or after.st_size < self.last_size
                    or (after.st_size == stat.st_size and after.st_mtime_ns != stat.st_mtime_ns)):
                self.stopped = True
                return None
        if history.path(intent['session_id']) != path:
            self.stopped = True
            return None
        if found:
            return {'state': 'delivered', 'evidence': 'native_record'}
        if self.total >= TOTAL_BYTES:
            self.stopped = True
        return None

    def _forward(self, block, start, size, intent, deadline):
        end = start + len(block)
        if self.tail_skip:
            newline = block.find(b'\n')
            if newline < 0:
                self.tail_cursor = end
                return False
            start += newline + 1
            block = block[newline + 1:]
            self.tail_skip = False
        position = start
        lines = block.split(b'\n')
        for line in lines[:-1]:
            if time.monotonic() > deadline:
                self.tail_cursor = position
                return False
            position += len(line) + 1
            self.tail_cursor = position
            if completed_identity(line, intent['session_id'], intent['command_id']):
                return True
        partial = lines[-1]
        if len(partial) > RECORD_BYTES:
            self.tail_cursor, self.tail_skip = end, True
        else:
            self.tail_cursor = position
            self.tail_deferred = size if end == size else None
        return False

    def _backward(self, block, start, end, intent, deadline, fresh):
        if fresh and block and not block.endswith(b'\n'):
            newline = block.rfind(b'\n')
            partial = len(block) - newline - 1
            if partial <= RECORD_BYTES:
                self.tail_cursor, self.tail_deferred = start + newline + 1, end
            else:
                self.tail_cursor, self.tail_skip = end, True
            self.back_skip = True
        if self.back_skip:
            newline = block.rfind(b'\n')
            if newline < 0:
                self.back_end = start
                return False
            block, end = block[:newline + 1], start + newline + 1
            self.back_skip = False
        lines = block.split(b'\n')
        if lines[-1] != b'':
            self.stopped = True
            return False
        position = end
        for index in range(len(lines) - 2, -1, -1):
            line = lines[index]
            if time.monotonic() > deadline:
                self.back_end = position
                return False
            if index == 0 and start:
                # Preserve a short boundary fragment for the next read, while
                # skipping a tool record larger than the per-record budget.
                if len(line) > RECORD_BYTES:
                    self.back_end, self.back_skip = start, True
                else:
                    self.back_end = position
                return False
            position -= len(line) + 1
            self.back_end = position
            if completed_identity(line, intent['session_id'], intent['command_id']):
                return True
        return False


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

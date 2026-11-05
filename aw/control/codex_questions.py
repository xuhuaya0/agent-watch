"""Minimal, patchable projection of native desktop asynchronous questions.

Keep indices but discard ordinary chat text, tool output and attachments. The
canonical turn history is authoritative; the legacy turns array is a fallback.
"""
import copy
import hashlib
import json

from .broker import encoded

OPEN = '<send_user_message_question_reply>'
CLOSE = '</send_user_message_question_reply>'
TEXT = {'type': True, 'text': 'reply_text'}
ITEM = {'type': True, 'id': True, 'status': True,
        'questions': [{'title': True, 'options': [True]}],
        'content': [TEXT], 'input': [TEXT]}
TURN = {'turnId': True, 'status': True, 'items': [ITEM]}
SCHEMA = {'turns': [TURN], 'turnHistory': {'kind': True, 'history': {'entitiesByKey': {'*': TURN}}}}
SCHEMA.update(latestThreadSettings={'approvalPolicy':True, 'approvalsReviewer':True,
              'sandboxPolicy':True, 'permissions':True, 'activePermissionProfile':True,
              'model':True, 'effort':True},
              latestModel=True, latestReasoningEffort=True,
              workspaceKind=True,
              threadRuntimeStatus={'type':True})


def project(value, schema=SCHEMA):
    if value is None:
        return None
    if schema is True:
        return copy.deepcopy(value)
    if schema == 'reply_text':
        # The native parser accepts the envelope after the last message prefix.
        # Retain only the envelope, never the unrelated prompt preceding it.
        if isinstance(value, str) and OPEN in value and value.rstrip().endswith(CLOSE):
            return value[value.rfind(OPEN):].strip()
        return ''
    if isinstance(schema, list):
        if not isinstance(value, list):
            raise ValueError('invalid native question list')
        return [project(v, schema[0]) for v in value]
    if not isinstance(value, dict):
        raise ValueError('invalid native question object')
    return {k: project(v, schema.get(k, schema.get('*'))) for k, v in value.items()
            if k in schema or '*' in schema}


def patch_history(history, patches):
    # Copy only when a relevant patch exists (most native patches are output).
    result = history
    for patch in patches:
        path = patch['path']
        schema = SCHEMA
        for part in path:
            if isinstance(schema, list):
                if type(part) is not int or part < 0:
                    raise ValueError('invalid native question index')
                schema = schema[0]
            elif isinstance(schema, dict):
                schema = schema.get(part, schema.get('*'))
            else:
                schema = None
            if schema is None:
                break
        if schema is None:
            continue
        if not path:
            raise ValueError('invalid native question patch')
        if result is history:
            result = copy.deepcopy(history)
        parent = result
        for part in path[:-1]:
            parent = parent[part]
        key, op = path[-1], patch['op']
        if op == 'remove':
            if isinstance(parent, list):
                parent.pop(key)
            else:
                del parent[key]
        elif op in ('add', 'replace'):
            value = project(patch['value'], schema)
            if isinstance(parent, list):
                if key > len(parent):
                    raise ValueError('invalid native question index')
                if op == 'add':
                    parent.insert(key, value)
                else:
                    parent[key] = value
            else:
                parent[key] = value
        else:
            raise ValueError('invalid native question operation')
    return result


def question_id(item, index):
    return json.dumps(['request_user_input_async', item, index], separators=(',', ':'), ensure_ascii=False)


def answered_ids(item):
    kind = item.get('type')
    if kind == 'steeringUserMessage' and item.get('status') != 'accepted':
        return set()
    if kind not in ('userMessage', 'steeringUserMessage'):
        return set()
    out = set()
    for block in item.get('content' if kind == 'userMessage' else 'input') or []:
        value = block.get('text', '').strip()
        if block.get('type') != 'text' or not value.startswith(OPEN) or not value.endswith(CLOSE):
            continue
        try:
            replies = json.loads(value[len(OPEN):-len(CLOSE)])
            if isinstance(replies, dict):
                replies = [replies]
            for reply in replies:
                if all(isinstance(reply.get(k), str) for k in ('questionItemId', 'question', 'answer')):
                    out.add(reply['questionItemId'])
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def pending(history):
    canonical = history.get('turnHistory', {})
    turns = (list(canonical.get('history', {}).get('entitiesByKey', {}).values())
             if canonical.get('kind') == 'canonical' else history.get('turns', []))
    active = [t for t in turns if t.get('status') == 'inProgress' and t.get('turnId')]
    # Ambiguous ownership of a live question is not permission to answer it.
    if len(active) != 1:
        return []
    turn = active[0]
    answered = set().union(*(answered_ids(i) for i in turn.get('items', [])))
    out = []
    for item in turn.get('items', []):
        if item.get('type') != 'agentMessage' or not item.get('id'):
            continue
        questions = [{'id': question_id(item['id'], n), 'question': q['title'],
                      'options': q.get('options') or [], 'multiple': False, 'custom': True}
                     for n, q in enumerate(item.get('questions') or [])
                     if question_id(item['id'], n) not in answered]
        if questions:
            native = {'turn_id': turn['turnId'], 'item_id': item['id'], 'questions': questions}
            out.append({'id': 'async-' + hashlib.sha256(encoded([turn['turnId'], item['id']]).encode()).hexdigest(),
                        'revision': hashlib.sha256(encoded(native).encode()).hexdigest(),
                        'kind': 'question', 'prompt': '请回答原会话中的问题', 'questions': questions})
    return out


def reply_envelope(request, answers):
    rows = [{'questionItemId': q['id'], 'question': q['question'],
             'answer': answers[q['id']]['custom'] or answers[q['id']]['selected'][0]}
            for q in request['questions']]
    return OPEN + '\n' + json.dumps(rows, ensure_ascii=False, separators=(',', ':')) + '\n' + CLOSE

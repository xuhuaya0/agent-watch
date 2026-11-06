"""Native questions and one-shot commands, bound to the exact pending payload.

Uses the managed loopback protocol observed on Antigravity for Windows. Unknown
interaction shapes are not exposed as actionable requests.
"""
import copy
import hashlib
import uuid
from .broker import ControlError, encoded, normalize_sessions, validate_action, text


def command_prompt(permission):
    """Accept only the observed command review shape, never sandbox overrides."""
    allowed = {'resource','persistSuggestionType','suggestedPersistPattern','reason',
               'actionDescription','triggerSource','shellName','approvalScopeFilter'}
    if not isinstance(permission,dict) or set(permission)-allowed:
        return None
    resource = permission.get('resource')
    if (not isinstance(resource,dict) or set(resource)!={'action','target'} or
        resource['action']!='command' or not text(resource['target'],12000)):
        return None
    if permission.get('triggerSource') != {'type':'TYPE_DEFAULT_FALLBACK'}:
        return None
    for key in ('reason','actionDescription','shellName','suggestedPersistPattern'):
        value = permission.get(key,'')
        if not isinstance(value,str) or len(value)>12000 or '\x00' in value:
            return None
    if permission.get('persistSuggestionType') not in (None,'PERSIST_SUGGESTION_TYPE_SUGGESTED'):
        return None
    scopes = permission.get('approvalScopeFilter',[])
    known = {'PERMISSION_SCOPE_ONCE','PERMISSION_SCOPE_CONVERSATION','PERMISSION_SCOPE_WORKSPACE',
             'PERMISSION_SCOPE_GLOBAL','PERMISSION_SCOPE_PROJECT'}
    if (not isinstance(scopes,list) or any(not isinstance(s,str) or s not in known for s in scopes) or
        (scopes and 'PERMISSION_SCOPE_ONCE' not in scopes)):
        return None
    parts = ['仅批准本次命令执行',permission.get('actionDescription',''),
             'Shell: '+permission.get('shellName',''),resource['target'],permission.get('reason','')]
    prompt = '\n\n'.join(p for p in parts if p)
    return prompt if text(prompt,16000) else None


class NativeInteractions:
    def __init__(self, rpc, approvals=False):
        self.rpc = rpc
        self.approvals = approvals

    def inspect(self, sid, *, context=None):
        meta = self.rpc.call('GetConversationMetadata', {'conversationId':sid})
        result = self.rpc.call('GetCascadeTrajectory', {'cascadeId':sid, 'disableRehydration':True})
        if not isinstance(meta,dict) or not isinstance(result,dict):
            raise ControlError('native_session_unavailable')
        if context is not None:
            metadata = meta.get('metadata')
            context['project_id'] = metadata.get('projectId') if isinstance(metadata,dict) else None
        trajectory = result.get('trajectory', {})
        if not isinstance(trajectory,dict):
            raise ControlError('native_session_unavailable')
        tid = trajectory.get('trajectoryId')
        try:
            valid_id = isinstance(tid,str) and str(uuid.UUID(tid)) == tid
        except ValueError:
            valid_id = False
        steps = trajectory.get('steps')
        if (not valid_id or trajectory.get('cascadeId') != sid or meta.get('trajectoryId') != tid or
            not isinstance(steps,list) or result.get('numTotalSteps') != len(steps)):
            raise ControlError('native_session_unavailable')
        if context is not None:
            context['metadata'] = meta.get('metadata')
            context['trajectory'] = trajectory
        row = {'tool':'antigravity','session_id':sid,'reply':True,'requests':[],
               'reason':'文字回复通过原生系统通知发送；问题回答直接提交给原生请求'}
        native_status = result.get('status')
        completed = [(index, step.get('type')) for index, step in enumerate(steps) if isinstance(step,dict)
                     and step.get('status') == 'CORTEX_STEP_STATUS_DONE'
                     and (step.get('type') == 'CORTEX_STEP_TYPE_ERROR_MESSAGE'
                          or step.get('type') == 'CORTEX_STEP_TYPE_PLANNER_RESPONSE'
                          and isinstance(step.get('plannerResponse'),dict)
                          and not step['plannerResponse'].get('toolCalls'))]
        row['delivery'] = {'state': 'idle' if native_status in (1, 'CASCADE_RUN_STATUS_IDLE')
                           else 'running' if native_status in (2, 'CASCADE_RUN_STATUS_RUNNING')
                           else 'unknown', 'steer': False,
                           'epoch': hashlib.sha256(encoded([tid, completed[-1]]).encode()).hexdigest() if completed else 'none'}
        bindings = {}
        for index,step in enumerate(steps):
            if not isinstance(step,dict) or step.get('status') != 'CORTEX_STEP_STATUS_WAITING':
                continue
            native = step.get('requestedInteraction')
            if native:
                row['reply'] = False
                row['reason'] = '原应用正在等待交互；不支持的请求请在原应用处理'
            if self.approvals and isinstance(native,dict) and set(native)=={'permission'}:
                prompt = command_prompt(native['permission'])
                if prompt:
                    identity = f'{tid}:{index}'
                    revision = hashlib.sha256(encoded(native).encode()).hexdigest()
                    row['requests'].append({'id':identity,'revision':revision,'kind':'approval',
                                            'prompt':prompt,'choices':['approve','reject']})
                    bindings[identity] = (tid,index,None)
                continue
            if not isinstance(native,dict) or set(native) != {'askQuestion'}:
                continue
            question = native['askQuestion']
            if not isinstance(question,dict) or set(question) != {'questions'}:
                continue
            questions = question['questions']
            if not isinstance(questions,list) or not 1 <= len(questions) <= 10:
                continue
            normalized = []
            for number,q in enumerate(questions):
                if (not isinstance(q,dict) or set(q)-{'question','options','isMultiSelect'} or
                    type(q.get('isMultiSelect',False)) is not bool):
                    break
                opts=q.get('options',[])
                if (not isinstance(opts,list) or any(not isinstance(o,dict) or set(o)!={'id','text'} or
                    not isinstance(o['id'],str) or not o['id'] or not isinstance(o['text'],str) for o in opts) or
                    len({o['id'] for o in opts}) != len(opts)):
                    break
                normalized.append({'id':str(number),'question':q.get('question'),'options':[o['text'] for o in opts],
                                   'multiple':q.get('isMultiSelect',False),'custom':True})
            if len(normalized) != len(questions):
                continue
            identity = f'{tid}:{index}'
            revision = hashlib.sha256(encoded(native).encode()).hexdigest()
            request = {'id':identity,'revision':revision,'kind':'question','prompt':'原应用正在等待你的回答',
                       'questions':normalized}
            try:
                normalize_sessions([{**row,'requests':[request]}])
            except ControlError:
                continue
            row['requests'].append(request)
            bindings[identity] = (tid,index,copy.deepcopy(questions))
        # Replying into a pending question can advance a turn without answering
        # that request. Offer its actual answer controls instead.
        if row['requests']:
            row['reply'] = False
            row['delivery']['state'] = 'waiting'
        normalize_sessions([row])
        return row,bindings

    def execute(self, sid, action):
        row,bindings = self.inspect(sid)
        validate_action(row,action)
        if action['kind'] not in ('answer','approval'):
            raise ControlError('native_action_unsupported')
        tid,index,questions = bindings[action['request_id']]
        interaction = {'trajectoryId':tid,'stepIndex':index}
        if action['kind']=='approval':
            interaction['permission'] = {'allow':action['decision']=='approve','scope':'PERMISSION_SCOPE_ONCE'}
            return self._submit(sid,interaction)
        responses = []
        for number,question in enumerate(questions):
            answer = action['answers'][str(number)]
            response = copy.deepcopy(question)
            ids = {o['text']:o['id'] for o in question.get('options',[])}
            response['selectedOptionIds'] = [ids[label] for label in answer['selected']]
            response['writeInResponse'] = answer['custom']
            responses.append(response)
        # LanguageServerService uses cascadeId; the same-named Google GCA
        # request uses conversationId and is a different protobuf message.
        interaction['askQuestion'] = {'responses':responses}
        return self._submit(sid,interaction)

    def _submit(self,sid,interaction):
        body = {'cascadeId':sid,'interaction':interaction}
        result = self.rpc.call('HandleCascadeUserInteraction',body)
        if result is None:
            return 'unknown'
        # A successful native acknowledgement means delivered, not completed.
        # Confirmation comes from the subsequent native snapshot removing the
        # request; never replay the action to discover its outcome.
        return 'delivered'

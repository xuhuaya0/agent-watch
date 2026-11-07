"""DeepSeek's native model catalog, selection and explicit default side effect."""
import copy
import hashlib
import time

from .broker import ControlError, encoded, text, validate_action


SCOPE = 'session-and-default'


def _selection(value):
    if (not isinstance(value, dict) or not text(value.get('provider'), 200)
            or not text(value.get('model'), 200)
            or ('reasoningEffort' in value and not text(value['reasoningEffort'], 80))):
        return None
    return {key: value[key] for key in ('provider', 'model', 'reasoningEffort') if key in value}


def _identity(selection):
    return 'dsh:' + hashlib.sha256(encoded([selection['provider'], selection['model']]).encode()).hexdigest()


def _editable(row):
    # Unlike the permission command, session/selectModel explicitly resumes a
    # cold native session itself. agentAvailable=False does not revoke this API.
    try:
        values = (row.get('projections') or {}).get('values') or {}
        questions, inbox = values.get('userQuestions') or {}, values.get('inbox') or {}
        active = questions.get('active', (questions.get('questions') or {}).get('active', []))
        return (row.get('running') is False and not row.get('archived')
                and row.get('origin') != 'subagent' and not row.get('blank')
                and not active and not inbox.get('next-turn') and not inbox.get('next-step'))
    except (AttributeError, TypeError):
        return False


def _model_options(catalog):
    groups = catalog.get('groups') if isinstance(catalog, dict) else None
    if not isinstance(groups, list) or len(groups) > 100:
        return None
    options, selections, providers = [], {}, set()
    for group in groups:
        if (not isinstance(group, dict) or not text(group.get('id'), 200)
                or group['id'] in providers or not text(group.get('name'), 200)
                or not isinstance(group.get('models'), list) or len(group['models']) > 100):
            return None
        providers.add(group['id'])
        for model in group['models']:
            if not isinstance(model, dict) or not text(model.get('id'), 200) or not text(model.get('name'), 200):
                return None
            selected = {'provider': group['id'], 'model': model['id']}
            identity = _identity(selected)
            if identity in selections:
                return None
            option = {'value': identity, 'label': model['name'], 'provider_label': group['name'], 'efforts': []}
            reasoning = model.get('reasoning')
            if reasoning is not None:
                levels = reasoning.get('efforts') if isinstance(reasoning, dict) else None
                if not isinstance(levels, list) or len(levels) > 12:
                    return None
                labels = {}
                for level in levels:
                    if (not isinstance(level, dict) or not text(level.get('id'), 80)
                            or not text(level.get('name'), 200) or level['id'] in labels):
                        return None
                    labels[level['id']] = level['name']
                option['efforts'], option['effort_labels'] = list(labels), labels
                default = reasoning.get('defaultEffort')
                if default is not None:
                    if not isinstance(default, str) or default not in labels:
                        return None
                    option['default_effort'] = default
            selections[identity] = selected
            options.append(option)
    return (options, selections) if len(options) <= 100 else None


def valid_model_capability(value):
    if (not isinstance(value, dict)
            or not {'current', 'current_label', 'revision', 'scope', 'options'} <= value.keys()
                <= {'current', 'current_label', 'effort', 'effort_status', 'effort_label', 'revision', 'scope', 'options'}
            or value.get('scope') != SCOPE or not text(value.get('current'), 160)
            or not text(value.get('current_label'), 200) or not text(value.get('revision'))
            or ('effort' in value and not text(value['effort'], 80))):
        return False
    status = value.get('effort_status')
    if (('effort_status' in value and status not in ('explicit', 'default', 'unsupported', 'unknown'))
            or status in ('explicit', 'default') and 'effort' not in value
            or status in ('unsupported', 'unknown') and 'effort' in value
            or 'effort_label' in value and ('effort' not in value or not text(value['effort_label'], 200))):
        return False
    rows = value['options']
    if not isinstance(rows, list) or len(rows) > 100:
        return False
    seen = set()
    for row in rows:
        if (not isinstance(row, dict)
                or not {'value', 'label', 'provider_label', 'efforts'} <= row.keys()
                    <= {'value', 'label', 'provider_label', 'efforts', 'effort_labels', 'default_effort'}
                or not text(row.get('value'), 160) or row['value'] in seen
                or not text(row.get('label'), 200) or not text(row.get('provider_label'), 200)):
            return False
        efforts, labels = row['efforts'], row.get('effort_labels', {})
        if (not isinstance(efforts, list) or len(efforts) > 12
                or any(not text(effort, 80) for effort in efforts) or len(set(efforts)) != len(efforts)
                or not isinstance(labels, dict) or set(labels) != set(efforts)
                or any(not text(label, 200) for label in labels.values())
                or ('default_effort' in row and row['default_effort'] not in efforts)):
            return False
        seen.add(row['value'])
    return True


class SessionSettings:
    def __init__(self, permissions):
        self.permissions = permissions
        self.gateway = None
        self.catalog = None
        self.expires = 0
        self.projection_gateway = None
        self.projections = {}
        self.requested_models = {}

    def projected_row(self, gateway, row, hydrate=False, fresh=False):
        """Refresh projections for a bounded set of previously requested chats.

        session/list can omit modelSelection even when the full native
        projection has it. Retain only model fields, with a bounded cache and
        native sequence guard; never retain the full questions/history payload.
        Expiry triggers a new read for a watched chat, rather than making an
        idle detail pane lose its model menu after fifteen seconds.
        """
        if self.projection_gateway is not gateway:
            self.projection_gateway, self.projections, self.requested_models = gateway, {}, {}
        projection = row.get('projections')
        if projection is not None and not isinstance(projection, dict):
            return row
        values = projection.get('values') if isinstance(projection, dict) else {}
        if not isinstance(values, dict):
            return row
        if isinstance(values.get('modelSelection'), dict) and 'next' in values['modelSelection']:
            return row
        sid = row.get('sessionId')
        if not text(sid):
            return row
        if hydrate:
            self.requested_models.pop(sid, None)
            self.requested_models[sid] = True
            if len(self.requested_models) > 16:
                retired = next(iter(self.requested_models))
                self.requested_models.pop(retired)
                self.projections.pop(retired, None)
        sequence = projection.get('asOfSeq') if isinstance(projection, dict) else None
        now = time.monotonic()
        cached = self.projections.get(sid)
        valid = (cached is not None and cached[2] is not None and cached[0] > now
                 and (type(sequence) is not int or sequence <= cached[1]))
        refresh = (fresh or cached is None or cached[0] <= now
                   or cached[2] is not None and type(sequence) is int and sequence > cached[1])
        if sid in self.requested_models and refresh:
            # Retain a short negative-cache deadline on failure. Previously
            # requested chats recover automatically without polling failures
            # every heartbeat or granting writes from an old selection.
            self.projections[sid] = (now + 15, -1, None)
            try:
                result = gateway.call('session/projections', {'request': {'sessionId': sid}})
                native = (result.get('values') or {}).get('modelSelection')
                seq = result.get('asOfSeq')
                if (not isinstance(native, dict) or 'next' not in native
                        or type(seq) is not int or not -1 <= seq <= 2**53-1
                        or (native['next'] is not None and _selection(native['next']) is None)):
                    return row
                selected = {'next': _selection(native['next']) if native['next'] is not None else None}
                if 'lastUsed' in native:
                    selected['lastUsed'] = _selection(native['lastUsed'])
                cached = (now + 15, seq, selected)
                self.projections[sid] = cached
                valid = type(sequence) is not int or sequence <= seq
            except (ControlError, OSError, ValueError, AttributeError, TypeError):
                return row
        if not valid:
            return row
        return {**row, 'projections': {**(projection or {}),
                'values': {**values, 'modelSelection': copy.deepcopy(cached[2])}}}

    def model_catalog(self, gateway, fresh=False):
        if fresh or self.gateway is not gateway or time.monotonic() >= self.expires:
            self.gateway, self.catalog, self.expires = gateway, None, time.monotonic() + 60
            try:
                value = gateway.call('session/modelCatalog', {})
                if isinstance(value, dict):self.catalog = value
            except Exception:
                pass
        return self.catalog

    def model_capability(self, gateway, row, fresh=False):
        projections = row.get('projections')
        values = projections.get('values') if isinstance(projections, dict) else None
        if not isinstance(values, dict):
            return None
        projection = values.get('modelSelection')
        if not isinstance(projection, dict) or 'next' not in projection:
            return None
        catalog = self.model_catalog(gateway, fresh=fresh)
        parsed = _model_options(catalog)
        if parsed is None:
            return None
        options, _ = parsed
        selected = _selection(catalog.get('default') if projection['next'] is None else projection['next'])
        if selected is None:
            return None
        current = _identity(selected)
        option = next((option for option in options if option['value'] == current), None)
        if option is None:
            return None
        effort = selected.get('reasoningEffort', option.get('default_effort'))
        if effort is not None and effort not in option['efforts']:
            return None
        editable = _editable(row)
        result = {'current': current, 'current_label': option['label'], 'scope': SCOPE,
            'revision': hashlib.sha256(encoded([row.get('sessionId'), projection['next'],
                _selection(catalog.get('default')) if projection['next'] is None else None,
                options, editable]).encode()).hexdigest(),
            'options': copy.deepcopy(options) if editable else []}
        if effort is not None:
            result['effort'] = effort
            result['effort_label'] = option['effort_labels'][effort]
            result['effort_status'] = ('explicit' if projection['next'] is not None
                                      and 'reasoningEffort' in selected else 'default')
        else:
            result['effort_status'] = 'unknown' if option['efforts'] else 'unsupported'
        return result if valid_model_capability(result) else None

    def execute(self, provider, command):
        sid, action = command['session_id'], command['action']
        with provider.refresh_lock:
            gateway = provider.gateway
            rows = gateway.call('session/list', {'_request': {}})['items']
            native = next((row for row in rows if row.get('sessionId') == sid), None)
            if native is None:
                raise ControlError('model_changed')
            native = self.projected_row(gateway, native, hydrate=True, fresh=True)
            capability = self.model_capability(gateway, native, fresh=True)
            validate_action({'model': capability}, action)
            parsed = _model_options(self.catalog)
            selected = dict(parsed[1][action['model']])
            if 'effort' in action:
                selected['reasoningEffort'] = action['effort']
            if (action['model'] == capability['current']
                    and action.get('effort') == capability.get('effort')):
                return 'model_updated'
            if gateway is not provider.gateway:
                raise ControlError('model_changed')
            with provider.condition:
                if any(item['frame']['agentId'] == sid for item in provider.pending.values()):
                    raise ControlError('model_changed')
            try:
                # This native route also saves the next default in the
                # background. Advertise that scope explicitly; never pretend
                # it is a session-only operation or restore a default in a race.
                receipt = gateway.call('session/selectModel', {'request': {'sessionId': sid, **selected}})
                if not isinstance(receipt, dict) or _selection(receipt.get('selected')) != selected:
                    return 'unknown'
                self.expires = 0
                self.projections.pop(sid, None)
                updated = provider.session_snapshot(sid)
                current = updated[0].get('model') if len(updated) == 1 else None
                if (gateway is provider.gateway and current and current['current'] == action['model']
                        and current.get('effort') == action.get('effort')):
                    return 'model_updated'
            except Exception:
                # A write may already have landed. Never retry it on an
                # uncertain response, reconnect, or failed native readback.
                pass
            return 'unknown'

    def read(self, gateway, row):
        settings, notes = {'scope':'session'}, []
        projection = row.get('projections')
        values = projection.get('values') if isinstance(projection, dict) else None
        if not isinstance(values, dict):values = {}
        permission = values.get('permissions')
        current = permission.get('currentValue') if isinstance(permission, dict) else None
        if current == 'custom':settings['permission'] = '自定义组合'
        elif text(current, 80):
            try:
                options = self.permissions.catalog_for(gateway) or []
                names = [option['label'] for option in options if option['value'] == current]
                if len(names) == 1:settings['permission'] = names[0]
            except Exception:
                pass
        if 'permission' not in settings:notes.append('原应用当前未提供可确认的会话权限。')
        projected = values.get('modelSelection')
        catalog = self.model_catalog(gateway) if isinstance(projected, dict) else None
        if catalog is not None:
            # Match the native selector: next, or the advertised default when
            # next is explicitly null. lastUsed is a different historical fact.
            selected = projected.get('next')
            if 'next' in projected and selected is None:selected = catalog.get('default')
            groups = catalog.get('groups')
            if (isinstance(selected, dict) and text(selected.get('provider'), 200)
                    and text(selected.get('model'), 200) and isinstance(groups, list) and len(groups) <= 100):
                matches = []
                for group in groups:
                    if not isinstance(group, dict) or group.get('id') != selected.get('provider'):continue
                    models = group.get('models')
                    if not isinstance(models, list) or len(models) > 500:continue
                    matches.extend(model for model in models if isinstance(model, dict) and model.get('id') == selected.get('model'))
                if len(matches) == 1 and text(matches[0].get('name'), 200):
                    model = matches[0]
                    settings['model'] = model['name']
                    reasoning = model.get('reasoning')
                    if isinstance(reasoning, dict):
                        effort = selected.get('reasoningEffort', reasoning.get('defaultEffort'))
                        levels = reasoning.get('efforts')
                        if isinstance(levels, list) and len(levels) <= 100:
                            names = [level.get('name') for level in levels if isinstance(level, dict) and level.get('id') == effort]
                            if len(names) == 1 and text(names[0], 200):settings['reasoning'] = names[0]
        if 'model' not in settings:notes.append('未读取到模型名称，请在 DeepSeek Harness 中查看。')
        if notes:settings['note'] = ' '.join(notes)
        return settings

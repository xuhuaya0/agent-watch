"""Display native session model/permission labels without exposing write actions."""
import time

from .broker import text


class SessionSettings:
    def __init__(self, permissions):
        self.permissions = permissions
        self.gateway = None
        self.catalog = None
        self.expires = 0

    def model_catalog(self, gateway):
        if self.gateway is not gateway or time.monotonic() >= self.expires:
            self.gateway, self.catalog, self.expires = gateway, None, time.monotonic() + 60
            try:
                value = gateway.call('session/modelCatalog', {})
                if isinstance(value, dict):self.catalog = value
            except Exception:
                pass
        return self.catalog

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

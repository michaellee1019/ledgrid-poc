"""Validated Scene patches and active-renderer gestures for automation clients."""
from copy import deepcopy
import math

from animation.core.plant_awareness import PLANT_MODIFIER_IDS
from ipc.scene_contract import canonical_json_sha256, validate_bounded_browser_json
from web.automation_api import AutomationError

PALETTES = ('neutral', 'mist', 'ember', 'spectrum')


def finite(value, minimum, maximum, name):
    if type(value) not in {int, float} or not math.isfinite(value) or not minimum <= value <= maximum:
        raise AutomationError(f'{name} must be a finite number from {minimum} to {maximum}.')
    return value


class AutomationAdvanced:
    def __init__(self, api):
        self.api, self.interface = api, api.interface

    def _selected(self, raw):
        scene = raw.get('scene_state') or raw.get('selected_scene') or raw.get('requested_scene')
        if not isinstance(scene, dict) or scene.get('schema') != 'ledgrid.scene.v2':
            raise AutomationError('A current supported Scene v2 is required.', code='unsupported_scene')
        return scene

    def controls(self, raw):
        try:
            scene = self._selected(raw)
        except AutomationError:
            return None
        component = scene['animation']
        loader = getattr(self.interface.preview_manager, 'plugin_loader', None)
        implementation = loader.get_plugin(component['component_id']) if loader is not None else None
        return {'digest': canonical_json_sha256(scene), 'look': deepcopy(scene['look']),
                'background_brightness': scene['background']['parameters'].get('gain'),
                'plants': deepcopy(scene['plants']['effects']),
                'animation': {key: component[key] for key in ('component_id', 'provider', 'version')},
                'widgets': [{'id': item['id'], 'component_id': item['component']['component_id'],
                             'visible': item['visible'], 'placement': deepcopy(item['placement'])} for item in scene['widgets']],
                'interaction_types': sorted(getattr(implementation, 'INTERACTION_TYPES', ())) if implementation else []}

    def dispatch(self, payload):
        validate_bounded_browser_json(payload, label='automation action')
        action = payload.get('action')
        guard_keys = {'action', 'expected_server_session_id', 'expected_controller_session_id',
                      'expected_controller_state_revision', 'expires_at'}
        if action == 'set_target_fps':
            if set(payload) - guard_keys - {'target_fps'} or type(payload.get('target_fps')) is not int or not 1 <= payload['target_fps'] <= 200:
                raise AutomationError('target_fps must be an integer from 1 to 200.')
            guard, _ = self.api._guard(payload)
            return self.api._enqueue('set_target_fps', {'target_fps': payload['target_fps']}, guard)
        scene_keys = guard_keys | {'expected_composer_revision', 'expected_scene_digest'}
        allowed = {'patch'} if action == 'patch_scene' else {'component_id', 'kind', 'x', 'y', 'strength'}
        if set(payload) - scene_keys - allowed:
            raise AutomationError('Unsupported advanced action fields.')
        guard, current = self.api._guard(payload)
        if type(payload.get('expected_composer_revision')) is not int or payload['expected_composer_revision'] != current['composer_revision']:
            raise AutomationError('Composer revision changed; refresh before writing.', code='conflict', status=409)
        scene = deepcopy(self._selected(self.api.channel.read_status() or {}))
        if payload.get('expected_scene_digest') != canonical_json_sha256(scene):
            raise AutomationError('Observed scene identity changed; refresh before writing.', code='conflict', status=409)
        if action == 'patch_scene':
            self.patch(scene, payload.get('patch'))
            return self.api.publish_scene(scene, payload)
        if not current['settings']['power']:
            raise AutomationError('Gestures require active playback; no scene was started.')
        if payload.get('component_id') != scene['animation']['component_id']:
            raise AutomationError('The active animation component changed.', code='conflict', status=409)
        controls = self.controls({'scene_state': scene})
        if payload.get('kind') not in controls['interaction_types']:
            raise AutomationError('That gesture is unsupported by the active animation.')
        x = finite(payload.get('x'), 0, 33, 'x')
        y = finite(payload.get('y'), 0, 138, 'y')
        if x >= 33 or y >= 138:
            raise AutomationError('Gesture coordinates must be inside the 33 by 138 grid.')
        strength = finite(payload.get('strength', 1), 0, 1, 'strength')
        if strength == 0:
            raise AutomationError('Gesture strength must be greater than zero.')
        return self.api._enqueue('automation_interaction', {'component_id': payload['component_id'],
            'scene_digest': payload['expected_scene_digest'], 'kind': payload['kind'], 'x': payload['x'],
            'y': payload['y'], 'strength': strength}, guard)

    def patch(self, scene, patch):
        if not isinstance(patch, dict) or not patch or set(patch) - {'look', 'background_brightness', 'plants', 'component', 'widget'}:
            raise AutomationError('A patch must name supported Scene fields.')
        if 'look' in patch:
            look = patch['look']
            if not isinstance(look, dict) or not look or set(look) - {'palette_id', 'pace', 'presentation_brightness'}:
                raise AutomationError('Look patch fields are invalid.')
            if 'palette_id' in look and look['palette_id'] not in PALETTES:
                raise AutomationError('Unknown supported palette.')
            for key in ('pace', 'presentation_brightness'):
                if key in look:
                    finite(look[key], 0, 2, key)
            scene['look'].update(look)
        if 'background_brightness' in patch:
            scene['background']['parameters']['gain'] = finite(patch['background_brightness'], 0, 1, 'background brightness')
        if 'plants' in patch:
            updates = patch['plants']
            if not isinstance(updates, dict) or not updates or set(updates) - set(PLANT_MODIFIER_IDS):
                raise AutomationError('Plant updates must name supported effects.')
            effects = scene['plants']['effects']
            active, strengths = set(effects['active']), dict(effects['strengths'])
            for effect, update in updates.items():
                if not isinstance(update, dict) or not update or set(update) - {'enabled', 'strength'}:
                    raise AutomationError('Plant effect update requires enabled and/or strength.')
                if 'enabled' in update:
                    if type(update['enabled']) is not bool:
                        raise AutomationError('Plant effect enabled must be boolean.')
                    if update['enabled']:
                        active.add(effect)
                        strengths.setdefault(effect, 1 if effect == 'obstacle' else .5)
                    else:
                        active.discard(effect)
                        strengths.pop(effect, None)
                if 'strength' in update:
                    finite(update['strength'], 0, 1, 'effect strength')
                    if effect not in active:
                        raise AutomationError('Enable the effect before changing its strength.')
                    strengths[effect] = update['strength']
            effects.update(active=sorted(active), strengths=strengths)
        if 'component' in patch:
            update = patch['component']
            if not isinstance(update, dict) or set(update) - {'target', 'component_id', 'widget_id', 'parameters'}:
                raise AutomationError('Component update fields are invalid.')
            target = update.get('target')
            if target in {'animation', 'background'}:
                if 'widget_id' in update:
                    raise AutomationError('widget_id applies only to Widget targets.')
                component = scene[target]
            elif target == 'widget':
                component = self._widget(scene, update.get('widget_id'))['component']
            else:
                raise AutomationError('Component target must be animation, background or widget.')
            if update.get('component_id') != component['component_id']:
                raise AutomationError('Component identity does not match the observed scene.', code='conflict', status=409)
            self._parameters(component, update.get('parameters'))
        if 'widget' in patch:
            update = patch['widget']
            if not isinstance(update, dict):
                raise AutomationError('Widget action must be an object.')
            operation = update.get('operation')
            if operation == 'add' and set(update) == {'operation', 'widget'}:
                widget = update['widget']
                if not isinstance(widget, dict) or any(item['id'] == widget.get('id') for item in scene['widgets']):
                    raise AutomationError('Widget ID already exists or is invalid.')
                component = widget.get('component')
                if isinstance(component, dict) and isinstance(component.get('parameters'), dict) and component['parameters']:
                    self._parameters(component, component['parameters'])
                scene['widgets'].append(widget)
            elif operation == 'remove' and set(update) == {'operation', 'id'}:
                widget = self._widget(scene, update['id'])
                scene['widgets'].remove(widget)
            elif operation == 'update' and not set(update) - {'operation', 'id', 'component_id', 'parameters', 'placement', 'visible'} and set(update) - {'operation', 'id', 'component_id'}:
                widget = self._widget(scene, update.get('id'))
                if update.get('component_id') != widget['component']['component_id']:
                    raise AutomationError('Widget component identity changed.', code='conflict', status=409)
                if 'parameters' in update:
                    self._parameters(widget['component'], update['parameters'])
                for key in ('placement', 'visible'):
                    if key in update:
                        widget[key] = update[key]
            else:
                raise AutomationError('Widget action must add, update or remove one explicit stable ID.')
        # Run every current component and plant normalizer before publication.
        self.interface._composer_canonical({'origin': 'composer', 'scene': scene})

    @staticmethod
    def _widget(scene, identity):
        if not isinstance(identity, str) or not identity:
            raise AutomationError('A stable widget_id is required.')
        widget = next((item for item in scene['widgets'] if item['id'] == identity), None)
        if widget is None:
            raise AutomationError('That Widget ID is not in the observed scene.', code='conflict', status=409)
        return widget

    def _parameters(self, component, parameters):
        if not isinstance(parameters, dict) or not parameters:
            raise AutomationError('Component parameters must be a nonempty object.')
        descriptor = self.interface.composer_catalog.require(provider=component.get('provider'),
            component_id=component.get('component_id'), version=component.get('version'))
        if set(parameters) - set(descriptor.default_parameters()):
            raise AutomationError('Unknown or non-local component parameter.')
        if component['role'] == 'background':
            if set(parameters) - {'gain', 'seed'}:
                raise AutomationError('Unknown background parameter.')
            if 'gain' in parameters:
                finite(parameters['gain'], 0, 1, 'background brightness')
            if 'seed' in parameters:
                from animation.plugins.solid import SolidColorAnimation
                SolidColorAnimation._normalized_parameters({'seed': parameters['seed']})
        component['parameters'].update(parameters)

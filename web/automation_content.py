"""Current content resolution and observed selection for the automation API."""
from copy import deepcopy
import time
import uuid

from ipc.scene_contract import canonical_json_sha256
from web.automation_api import AutomationError
from web.starter_looks import get_starter


class AutomationContent:
    def __init__(self, api):
        self.api, self.interface = api, api.interface
        self.items = []
        self.matches = {}
        self.follow = None

    def _descriptors(self):
        return {item.component_id: item for item in self.interface.composer_catalog.descriptors}

    def _preset(self, component_id, preset_id):
        preset = self.interface._load_component_preset(component_id, preset_id, 'python')
        if preset is None:
            raise ValueError('That component preset is missing or unsupported.')
        effects = None
        if self.interface.composer_presets.contains(component_id, preset_id):
            effects = self.interface.composer_presets.choice(component_id, preset_id).get('installation_effects')
        return preset, effects

    def _scene(self, raw):
        _document, scene = self.interface._validated_browser_activation_scene(raw)
        return self.interface._composer_canonical({'origin': 'composer', 'scene': scene}).scene

    def catalog(self):
        entries, matches = [], {}
        names = {item['component_id']: item['name'] for item in self.interface._composer_gallery_payload()['entries']}
        for component_id, descriptor in self._descriptors().items():
            if descriptor.role.value == 'animation':
                entries.append({'id': f'animation:python:{component_id}:v{descriptor.version}', 'kind': 'animation',
                                'name': names.get(component_id, component_id), 'supported': True})
            try:
                presets = self.interface._list_component_presets(component_id, 'python')
            except (ValueError, RuntimeError) as exc:
                entries.append({'id': f'catalog-error:{component_id}', 'kind': 'diagnostic',
                                'name': component_id, 'supported': False, 'error': str(exc)})
                continue
            for summary in presets:
                identity = f"preset:python:{component_id}:{summary['preset_id']}"
                try:
                    preset, effects = self._preset(component_id, summary['preset_id'])
                    parameters = descriptor.parameter_normalizer(preset['params']) if descriptor.parameter_normalizer else preset['params']
                    matches[identity] = {'component_id': component_id, 'role': descriptor.role.value,
                                         'parameters': parameters, 'effects': effects}
                    entries.append({'id': identity, 'kind': 'preset', 'name': summary['name'], 'component_id': component_id, 'supported': True})
                except (ValueError, TypeError) as exc:
                    entries.append({'id': identity, 'kind': 'preset', 'name': summary['name'], 'supported': False, 'error': str(exc)})
        for look in self.interface.composer_looks.list():
            identity = 'look:' + look['id']
            try:
                scene = self._scene(self.interface.composer_looks.get(look['id'])['scene'])
                matches[identity] = {'digest': canonical_json_sha256(scene)}
                entries.append({'id': identity, 'kind': 'look', 'name': look['name'], 'supported': True})
            except (ValueError, TypeError) as exc:
                entries.append({'id': identity, 'kind': 'look', 'name': look['name'], 'supported': False, 'error': str(exc)})
        for playlist in self.interface.composer_playlists.list():
            identity = 'playlist:' + playlist['id']
            try:
                self.playlist_entries(playlist['id'])
                entries.append({'id': identity, 'kind': 'playlist', 'name': playlist['name'], 'supported': True})
            except (ValueError, TypeError) as exc:
                entries.append({'id': identity, 'kind': 'playlist', 'name': playlist['name'], 'supported': False, 'error': str(exc)})
        for item in entries:
            item['label'] = f"{item['name'][:max(1, 250-len(item['id']))]} [{item['id']}]"
        self.items, self.matches = entries, matches
        return entries

    def active(self, raw, playlist, *, fresh):
        scene = raw.get('scene_state')
        result = dict.fromkeys(('animation', 'preset', 'look', 'playlist'))
        if not fresh:
            return result
        if isinstance(scene, dict) and scene.get('schema') == 'ledgrid.scene.v2':
            animation = scene['animation']
            result['animation'] = f"animation:{animation['provider']}:{animation['component_id']}:v{animation['version']}"
            digest = canonical_json_sha256(scene)
            looks = [identity for identity, match in self.matches.items() if identity.startswith('look:') and match['digest'] == digest]
            presets = []
            for identity, match in self.matches.items():
                if not identity.startswith('preset:'):
                    continue
                components = [animation] if match['role'] == 'animation' else [widget['component'] for widget in scene['widgets']]
                if any(component['component_id'] == match['component_id'] and component['parameters'] == match['parameters'] for component in components):
                    if match['effects'] is None or scene['plants']['effects'] == match['effects']:
                        presets.append(identity)
            result['look'] = looks[0] if len(looks) == 1 else None
            result['preset'] = presets[0] if len(presets) == 1 else None
        if raw.get('is_running') and isinstance(playlist, dict) and playlist.get('phase') == 'running' and playlist.get('controller_session_id') == raw.get('controller_session_id'):
            result['playlist'] = 'playlist:' + str(playlist['playlist_id'])
        return result

    def playlist_entries(self, playlist_id):
        definition = self.interface.composer_playlists.get(playlist_id)
        return definition, [{**entry, 'scene': self._scene(entry['scene'])} for entry in definition['entries']]

    def play(self, payload):
        identity = payload.get('content_id')
        if not isinstance(identity, str):
            raise AutomationError('content_id must be a stable catalog identity.')
        parts = identity.split(':')
        if parts[0] == 'playlist' and len(parts) == 2:
            return self.playlist_command(payload, playlist_id=parts[1])
        opened_look = None
        try:
            if parts[0] == 'look' and len(parts) == 2:
                scene = self._scene(self.interface.composer_looks.get(parts[1])['scene'])
                opened_look = parts[1]
            elif parts[0] in {'animation', 'preset'} and len(parts) == 4 and parts[1] == 'python':
                descriptor = self._descriptors().get(parts[2])
                if descriptor is None:
                    raise ValueError('That component is unsupported.')
                raw = self.api.channel.read_status() or {}
                scene = raw.get('scene_state') or raw.get('selected_scene') or raw.get('requested_scene')
                if scene is None:
                    # A first explicit content choice gets a complete current
                    # Scene scaffold. Unsupported existing references never do.
                    scene = get_starter('aurora')['scene']
                scene = deepcopy(self._scene(scene))
                if parts[0] == 'animation':
                    if descriptor.role.value != 'animation' or parts[3] != f'v{descriptor.version}':
                        raise ValueError('That animation version is unsupported.')
                    scene['animation'] = {'component_id': descriptor.component_id, 'provider': 'python',
                                          'version': descriptor.version, 'role': 'animation',
                                          'parameters': descriptor.default_parameters()}
                else:
                    preset, effects = self._preset(parts[2], parts[3])
                    component = {'component_id': descriptor.component_id, 'provider': 'python', 'version': descriptor.version,
                                 'role': descriptor.role.value, 'parameters': preset['params']}
                    if descriptor.role.value == 'animation':
                        scene['animation'] = component
                    elif descriptor.role.value == 'widget':
                        widgets = [item for item in scene['widgets'] if item['component']['component_id'] == descriptor.component_id]
                        if len(widgets) > 1:
                            raise ValueError('More than one matching widget exists; choose a widget through Composer.')
                        if widgets:
                            widgets[0]['component'] = component
                        else:
                            scene['widgets'].append({'id': f'automation-{descriptor.component_id}', 'component': component,
                                'visible': True, 'placement': {'mode': 'manual', 'strip_translation': 0, 'led_translation': -8}})
                    else:
                        raise ValueError('That component preset role is unsupported.')
                    if effects is not None:
                        scene['plants']['effects'] = effects
            else:
                raise ValueError('That content identity is unsupported.')
        except (ValueError, TypeError) as exc:
            raise AutomationError(str(exc), code='unsupported_content') from exc
        return self.api.publish_scene(scene, payload, opened_look_id=opened_look)

    def playlist_command(self, payload, *, playlist_id=None):
        guard, current = self.api._guard(payload)
        if type(payload.get('expected_composer_revision')) is not int or payload['expected_composer_revision'] != current['composer_revision']:
            raise AutomationError('Composer revision changed; refresh before writing.', code='conflict', status=409)
        request_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
        command = {'schema': 'ledgrid.playlist-command', 'schema_version': 1, 'request_id': request_id,
                   'requested_at': time.time(), **guard}
        reconciliation = None
        if playlist_id is not None:
            if (self.follow is not None and self.follow['state'] == 'pending'
                    and self.api.channel.read_playlist_request_status(self.follow['request_id']) is None):
                raise AutomationError('A playlist start is awaiting controller acknowledgement; wait for its result before choosing another playlist.', code='conflict', status=409)
            if (self.api.channel.read_playlist_current_status() or {}).get('phase') == 'running':
                raise AutomationError('Another playlist is running; stop it before starting a new playlist.', code='conflict', status=409)
            try:
                definition, entries = self.playlist_entries(playlist_id)
            except (ValueError, TypeError) as exc:
                raise AutomationError(str(exc), code='unsupported_content') from exc
            command.update(action='start', run_id=run_id, playlist_id=playlist_id,
                           playlist_name=definition['name'], entries=entries)
            canonical = self.interface._composer_canonical({'origin': 'composer', 'scene': entries[0]['scene']})
            reconciliation = {'canonical': canonical, 'composer_revision': current['composer_revision'],
                              'recovery_generation': self.interface._composer_recovery_generation, 'opened_look_id': None}
        else:
            playlist = self.api.channel.read_playlist_current_status() or {}
            run_id = payload.get('run_id')
            if not isinstance(run_id, str) or run_id != playlist.get('run_id') or playlist.get('phase') != 'running':
                raise AutomationError('Playlist run is no longer current.', code='conflict', status=409)
            command.update(action='stop', run_id=run_id)
        self.api._reserve_command_slot()
        self.api.channel.enqueue_playlist_command(command)
        receipt = self.api._receipt(request_id, time.time_ns())
        self.api._commands[request_id] = {'receipt': receipt, 'guard': guard, 'playlist_run_id': run_id,
                                        'reconciliation': reconciliation}
        if playlist_id is not None:
            self.follow = {'run_id': run_id, 'request_id': request_id, 'guard': guard, 'composer_revision': current['composer_revision'],
                           'recovery_generation': self.interface._composer_recovery_generation,
                           'scenes': {canonical_json_sha256(entry['scene']): entry['scene'] for entry in entries},
                           'last_digest': None, 'state': 'pending'}
        return dict(receipt)

    def observe_playlist(self, raw, playlist):
        """Reconcile only observed scenes from this accepted immutable run."""
        context = self.follow
        if context is None or not isinstance(playlist, dict) or playlist.get('run_id') != context['run_id']:
            return None
        if context['state'] == 'conflict':
            return {'state': 'conflict', 'error': 'Composer changed during playlist playback; newer editor state was preserved.'}
        if playlist.get('controller_session_id') != raw.get('controller_session_id') or playlist.get('phase') in {'rejected', 'failed', 'overridden'}:
            return {'state': playlist.get('phase'), 'error': playlist.get('error')}
        scene = raw.get('scene_state') or raw.get('selected_scene')
        digest = canonical_json_sha256(scene) if isinstance(scene, dict) else None
        if digest not in context['scenes'] or digest == context['last_digest']:
            return None
        with self.interface.composer_live.publication_lock:
            if (self.interface.composer_live.snapshot()['revision'] != context['composer_revision']
                    or self.interface._composer_recovery_generation != context['recovery_generation']):
                context['state'] = 'conflict'
                return {'state': 'conflict', 'error': 'Composer changed during playlist playback; newer editor state was preserved.'}
            self.interface._composer_submit_scene(context['scenes'][digest], client_id='automation-playlist', preserve_opened_look=False)
            context.update(composer_revision=self.interface.composer_live.snapshot()['revision'],
                           recovery_generation=self.interface._composer_recovery_generation,
                           last_digest=digest, state='following')
        return {'state': 'completed'}

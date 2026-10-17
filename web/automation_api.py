"""Small, guarded automation surface over the existing controller queue.

Reads never publish scenes. An accepted queue entry is not playback evidence;
only its correlated controller result can complete a command. No retry worker or
startup restoration exists here.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from flask import jsonify, request

from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE
from ipc.runtime_control import normalize_controller_command_guard, normalize_automation_device_state
from ipc.scene_contract import canonical_json_sha256

PREFIX = '/api/v1/automation'
GUARD_SECONDS = 10


class AutomationError(ValueError):
    def __init__(self, message, *, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def installation_identity(path: Path) -> str:
    """Create once with an atomic link; corrupt identity never silently changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        value = {'schema': 'ledgrid.installation', 'schema_version': 1,
                 'installation_id': str(uuid.uuid4())}
        descriptor, name = tempfile.mkstemp(prefix='.installation-', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(value, handle)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != 'ledgrid.installation' or value.get('schema_version') != 1:
        raise ValueError('installation identity is invalid; restore its personal-data backup')
    identity = value.get('installation_id')
    if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
        raise ValueError('installation identity is invalid; restore its personal-data backup')
    return identity


class AutomationAPI:
    def __init__(self, interface):
        self.interface = interface
        self.channel = interface.control_channel
        self.server_session_id = str(uuid.uuid4())
        data_root = Path(os.environ.get('LEDGRID_DATA_DIR', str(interface.project_root)))
        self.identity_path = data_root / 'run_state' / 'installation.json'
        self._lock = threading.RLock()
        self._commands = {}
        from web.automation_content import AutomationContent
        self.content = AutomationContent(self)

    @property
    def installation_id(self):
        return installation_identity(self.identity_path)

    def capabilities(self):
        return {'schema': 'ledgrid.automation-capabilities', 'schema_version': 1,
                'installation_id': self.installation_id, 'server_session_id': self.server_session_id,
                'configuration_url': '/composer', 'commands': ['set_device_state', 'play_content', 'stop_playlist'],
                'controls': {'power': {}, 'brightness': {'min': 0, 'max': 255},
                             'pace': {'min': 0.1, 'max': 25}},
                'guard_max_seconds': GUARD_SECONDS, 'completion_wait_max_seconds': 2,
                'physical_display_verified': False}

    @staticmethod
    def _scene_summary(scene):
        if not isinstance(scene, dict):
            return None
        return {'schema': scene.get('schema'), 'digest': canonical_json_sha256(scene),
                'animation': {key: scene['animation'].get(key) for key in ('provider', 'component_id', 'preset_id')} if isinstance(scene.get('animation'), dict) else None,
                'background': {key: scene['background'].get(key) for key in ('provider', 'component_id', 'preset_id')} if isinstance(scene.get('background'), dict) else None,
                'widget_count': len(scene.get('widgets') or [])}

    def status(self):
        # Deliberately bypass the browser status expansion: no frame encoding,
        # image blobs, preview mutations, catalogs or detailed counters here.
        raw = self.channel.read_status() or {}
        timestamp = raw.get('updated_at') or raw.get('timestamp') or raw.get('written_at')
        fresh = bool(isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool)
                     and math.isfinite(timestamp) and 0 <= time.time() - timestamp <= 5
                     and isinstance(raw.get('controller_session_id'), str)
                     and type(raw.get('controller_state_revision')) is int)
        speed = raw.get('animation_speed_scale')
        pace = speed / DEFAULT_ANIMATION_SPEED_SCALE if isinstance(speed, (int, float)) and not isinstance(speed, bool) else None
        devices = raw.get('receiver_connectivity') or raw.get('receiver_status') or []
        receivers = []
        if isinstance(devices, list):
            receivers = [{'device_index': item.get('device_index', index),
                          'connected': item.get('receiver_connected') if fresh else None,
                          'error': item.get('error')} for index, item in enumerate(devices) if isinstance(item, dict)]
        playlist_reader = getattr(self.channel, 'read_playlist_current_status', None)
        playlist = playlist_reader() if callable(playlist_reader) else None
        playback = raw.get('controller_playback') or {}
        scene = raw.get('scene_state')
        scene_digest = canonical_json_sha256(scene) if isinstance(scene, dict) else None
        with self._lock:
            reconciliation = self.content.observe_playlist(raw, playlist) if fresh else None
            active_content = self.content.active(raw, playlist, fresh=fresh)
        return {'schema': 'ledgrid.automation-status', 'schema_version': 1,
                'installation_id': self.installation_id, 'server_session_id': self.server_session_id,
                'observed_at': timestamp, 'fresh': fresh,
                'controller_session_id': raw.get('controller_session_id'),
                'controller_state_revision': raw.get('controller_state_revision'),
                'last_command_id': raw.get('last_command_id'),
                'last_applied_command_id': raw.get('last_applied_command_id'),
                'active_identity': {'scene_digest': scene_digest} if scene_digest else None,
                'scene_digest': scene_digest,
                'is_running': bool(raw.get('is_running')) if fresh else None,
                'brightness': raw.get('brightness'), 'animation_speed_scale': speed,
                'composer_revision': self.interface.composer_live.snapshot()['revision'],
                'settings': {'power': bool(raw.get('is_running')) if fresh else None,
                             'brightness': raw.get('brightness'), 'pace': pace,
                             'animation_speed_scale': speed, 'target_fps': raw.get('target_fps')},
                'requested_scene': self._scene_summary(raw.get('requested_scene') or raw.get('selected_scene')),
                'controller_playback': {'state': 'unavailable' if not fresh else 'running' if raw.get('is_running') else 'stopped',
                                        'running': bool(raw.get('is_running')) if fresh else None,
                                        'scene': self._scene_summary(raw.get('scene_state')),
                                        'error': playback.get('error') or raw.get('last_error'),
                                        'actual_fps': raw.get('actual_fps')},
                'receiver_connectivity': receivers,
                'active_content': active_content,
                'playlist': {**{key: playlist.get(key) for key in ('phase', 'run_id', 'playlist_id', 'playlist_name', 'current_index', 'current_entry', 'next_entry', 'entry_count', 'remaining_seconds', 'error')}, 'reconciliation': reconciliation} if isinstance(playlist, dict) else None,
                'physical_display_verified': False}

    def catalog(self):
        components = [{'component_id': item.component_id, 'provider': item.provider.value,
                       'role': item.role.value, 'version': item.version, 'defaults': item.default_parameters()}
                      for item in self.interface.composer_catalog.descriptors]
        presets, errors = {}, {}
        for item in self.interface.composer_catalog.descriptors:
            try:
                presets[item.component_id] = self.interface.composer_presets.choices(item.component_id)
            except ValueError as exc:
                presets[item.component_id] = []
                errors[item.component_id] = str(exc)
        return {'schema': 'ledgrid.automation-catalog', 'schema_version': 1,
                'installation_id': self.installation_id, 'components': components,
                'presets': presets, 'preset_errors': errors,
                'content': self.content.catalog(),
                'looks': self.interface.composer_looks.list(),
                'playlists': self.interface.composer_playlists.list()}

    def _guard(self, payload):
        if not self.interface.activation_enabled:
            raise AutomationError('Physical-wall activation is disabled.', code='activation_disabled', status=503)
        guard = normalize_controller_command_guard(payload, now=time.time())
        if guard is None:
            raise AutomationError('A current controller guard is required.', code='guard_required', status=428)
        if guard['expires_at'] > time.time() + GUARD_SECONDS:
            raise AutomationError('Command expiry must be within 10 seconds.')
        if payload.get('expected_server_session_id') != self.server_session_id:
            raise AutomationError('Web server session changed; refresh before writing.', code='conflict', status=409)
        current = self.status()
        if not current['fresh']:
            raise AutomationError('Controller observation is stale or unavailable.', code='unavailable', status=503)
        if (guard['expected_controller_session_id'] != current['controller_session_id']
                or guard['expected_controller_state_revision'] != current['controller_state_revision']):
            raise AutomationError('Controller session or revision changed; refresh before writing.', code='conflict', status=409)
        return guard, current

    def submit(self, payload):
        if not isinstance(payload, dict):
            raise AutomationError('Command must be an object.')
        if payload.get('action') in {'play_content', 'stop_playlist'}:
            allowed = {'action', 'content_id' if payload['action'] == 'play_content' else 'run_id',
                       'expected_server_session_id', 'expected_controller_session_id',
                       'expected_controller_state_revision', 'expected_composer_revision', 'expires_at'}
            if set(payload) - allowed:
                raise AutomationError('Unsupported command fields.')
            with self._lock, self.interface.composer_live.publication_lock:
                return self.content.play(payload) if payload['action'] == 'play_content' else self.content.playlist_command(payload)
        allowed = {'action', 'power', 'brightness', 'pace', 'expected_server_session_id',
                   'expected_controller_session_id', 'expected_controller_state_revision', 'expires_at'}
        if set(payload) - allowed or payload.get('action') != 'set_device_state':
            raise AutomationError('Unsupported command or fields.')
        settings = normalize_automation_device_state({key: payload[key] for key in ('power', 'brightness', 'pace') if key in payload})
        with self._lock:
            guard, _ = self._guard(payload)
            return self._enqueue('automation_device_state', settings, guard)

    def _reserve_command_slot(self):
        if len(self._commands) >= 256:
            # Never lose metadata for an accepted, unobserved completion. Client
            # intent cannot silently disappear to make room for another write.
            for request_id in list(self._commands):
                if self._commands[request_id].get('terminal'):
                    self._commands.pop(request_id)
                    break
            else:
                raise AutomationError('Command history is full; observe pending commands first.', code='busy', status=503)
    @staticmethod
    def _receipt(request_id, command_id):
        return {'schema': 'ledgrid.automation-command', 'schema_version': 1,
                'request_id': request_id, 'command_id': command_id, 'state': 'accepted',
                'result_url': f'{PREFIX}/commands/{request_id}'}

    def _enqueue(self, action, data, guard, *, reconciliation=None):
        self._reserve_command_slot()
        command = self.channel.send_command(action, **data, _controller_guard=guard)
        receipt = self._receipt(command['request_id'], command['command_id'])
        self._commands[command['request_id']] = {'receipt': receipt, 'guard': guard, 'reconciliation': reconciliation}
        return dict(receipt)

    def publish_scene(self, scene, payload, *, opened_look_id=None):
        """Reusable complete Scene v2 publication for content/advanced actions.

        Validate before queueing and bind reconciliation to this exact editor
        revision. Only a correlated successful controller result may update
        Composer/recovery. A newer editor publication is never overwritten.
        """
        canonical = self.interface._composer_canonical({'origin': 'composer', 'scene': scene})
        with self._lock, self.interface.composer_live.publication_lock:
            guard, current = self._guard(payload)
            if type(payload.get('expected_composer_revision')) is not int or payload['expected_composer_revision'] != current['composer_revision']:
                raise AutomationError('Composer revision changed; refresh before writing.', code='conflict', status=409)
            return self._enqueue('start_scene', {'scene': canonical.scene}, guard,
                                 reconciliation={'canonical': canonical, 'composer_revision': current['composer_revision'],
                                                 'recovery_generation': self.interface._composer_recovery_generation,
                                                 'opened_look_id': opened_look_id})

    def result(self, request_id, *, wait_seconds=0):
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (TypeError, ValueError, AttributeError) as exc:
            raise AutomationError('Invalid request identity.') from exc
        if not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 2:
            raise AutomationError('wait_seconds must be between 0 and 2.')
        deadline = time.monotonic() + wait_seconds
        while True:
            with self._lock:
                record = self._commands.get(request_id)
                if record is None:
                    raise AutomationError('Request is unknown to this web session; do not replay it.', code='unknown_request', status=404)
                receipt = dict(record['receipt'])
                if 'playlist_run_id' in record:
                    observed = self.channel.read_playlist_request_status(request_id)
                    result = None
                    if observed is not None:
                        if observed.get('request_id') != request_id or observed.get('run_id') != record['playlist_run_id']:
                            raise AutomationError('Playlist result correlation is invalid.', code='invalid_result', status=502)
                        result = {'request_id': request_id, 'command_id': receipt['command_id'],
                                  'state': 'completed' if observed.get('phase') in {'running', 'stopped'} else 'failed',
                                  'error': observed.get('error'), 'completed_at': observed.get('updated_at'),
                                  'controller_session_id': observed.get('controller_session_id'),
                                  'controller_state_revision': observed.get('controller_state_revision')}
                else:
                    result = self.channel.read_command_result(request_id)
                if result is not None:
                    if result.get('request_id') != request_id or result.get('command_id') != receipt['command_id'] or result.get('state') not in {'completed', 'failed'}:
                        raise AutomationError('Controller result correlation is invalid.', code='invalid_result', status=502)
                    receipt.update({key: result.get(key) for key in ('state', 'error', 'completed_at', 'controller_session_id', 'controller_state_revision')})
                    if 'playlist_run_id' in record:
                        current = self.status()
                        receipt['reconciliation'] = (current.get('playlist') or {}).get('reconciliation')
                    else:
                        receipt['reconciliation'] = self._reconcile(record) if result['state'] == 'completed' else None
                    record['terminal'] = True
                    return {**receipt, 'status': self.status()}
                current = self.status()
                guard = record['guard']
                if current['controller_session_id'] != guard['expected_controller_session_id']:
                    receipt.update(state='failed', error='Controller session changed before completion was observed.')
                elif time.time() >= guard['expires_at']:
                    # The queue might have executed before expiry. Without a
                    # receipt this remains uncertain; expiry is not rollback.
                    receipt.update(state='failed', error='Command completion timed out; refresh state before any new intent.')
                if receipt['state'] == 'failed':
                    return {**receipt, 'status': current}
            if time.monotonic() >= deadline:
                return {**receipt, 'status': current}
            time.sleep(min(0.025, max(0, deadline-time.monotonic())))

    def _reconcile(self, record):
        pending = record.get('reconciliation')
        if pending is None:
            return None
        if 'reconciled' in record:
            return record['reconciled']
        with self.interface.composer_live.publication_lock:
            current = self.status()
            if (not current['fresh'] or current['controller_session_id'] != record['guard']['expected_controller_session_id']
                    or (current['requested_scene'] or {}).get('digest') != pending['canonical'].identity.digest):
                result = {'state': 'conflict', 'error': 'Controller scene changed or became unavailable before reconciliation.'}
            elif (self.interface.composer_live.snapshot()['revision'] != pending['composer_revision']
                  or self.interface._composer_recovery_generation != pending['recovery_generation']):
                result = {'state': 'conflict', 'error': 'Composer changed after command acceptance; newer editor state was preserved.'}
            else:
                try:
                    self.interface._composer_submit_scene(pending['canonical'].scene, client_id='automation',
                        opened_look_id=pending['opened_look_id'], preserve_opened_look=False)
                    result = {'state': 'completed', 'composer_revision': self.interface.composer_live.snapshot()['revision']}
                except (OSError, ValueError, RuntimeError) as exc:
                    result = {'state': 'failed', 'error': str(exc)}
            record['reconciled'] = result
            return result


def register_automation_routes(interface):
    api = AutomationAPI(interface)
    interface.automation = api

    def respond(operation, *, status=200):
        try:
            response = jsonify(operation())
        except AutomationError as exc:
            response = jsonify({'error': str(exc), 'code': exc.code})
            status = exc.status
        except (ValueError, TypeError, OSError) as exc:
            response = jsonify({'error': str(exc), 'code': 'invalid_request'})
            status = 400
        response.headers['Cache-Control'] = 'no-store'
        return response, status

    interface.app.add_url_rule(f'{PREFIX}/capabilities', 'automation_capabilities', lambda: respond(api.capabilities))
    interface.app.add_url_rule(f'{PREFIX}/catalog', 'automation_catalog', lambda: respond(api.catalog))
    interface.app.add_url_rule(f'{PREFIX}/status', 'automation_status', lambda: respond(api.status))
    interface.app.add_url_rule(f'{PREFIX}/commands', 'automation_command', lambda: respond(lambda: api.submit(request.get_json(silent=True)), status=202), methods=['POST'])
    interface.app.add_url_rule(f'{PREFIX}/commands/<request_id>', 'automation_result',
        lambda request_id: respond(lambda: api.result(request_id, wait_seconds=float(request.args.get('wait_seconds', 0)))))

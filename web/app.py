#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations and adjusting parameters in
real time.
"""

import inspect
import json
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory

from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
)
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.native_background_library import NativeBackgroundLibrary
from animation.core.plant_awareness import PlantModifierState
from animation.core.preview_assets import load_catalog, merge_catalogs
from drivers.frame_codec import (
    FRAME_ENCODING_NAME,
    decode_frame_data,
    encode_frame_data,
)
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from ipc.control_channel import FileControlChannel
from ipc.scene_contract import (
    DEFAULT_SCENE_PROVIDER_POLICY,
    FIXED_OVERLAY_SLOT,
    SCENE_PRESET_SCHEMA,
    SCENE_PRESET_VERSION,
    SceneProviderPolicy,
    SceneValidationError,
    background_only_scene,
    decorate_catalog,
    filter_catalog,
    normalize_scene_payload,
    scene_preview_identity,
)
from web.preview_worker import RuntimePreviewWorker

PAINTER_MASK_TYPES = (
    {
        'id': 'foliage',
        'label': 'Foliage',
        'description': 'Leaves, vines, and other soft plant cover',
        'color': [48, 220, 96],
    },
    {
        'id': 'planter_bowls',
        'label': 'Planter bowls',
        'description': 'The seven solid rooting globes / planter bowls',
        'color': [255, 72, 190],
    },
)

# Browser execution is deliberately capability-gated. The generated Pyodide
# asset contains the authoritative Python animation-plugin package, so every
# animation component with a valid Python module:Class entrypoint can use the
# universal worker. Separate compatibility tools such as Painter are cataloged
# for product continuity but are not animation runtimes. Receiver-native
# execution remains explicitly capability-bound to separately built Wasm peers.
BROWSER_NATIVE_COMPONENT_ASSETS = {
    'aurora_curtains_native': 'aurora_curtains_native.wasm',
    'compiled_rainbow': 'compiled_rainbow.wasm',
}
BROWSER_NATIVE_COMPONENTS = frozenset(BROWSER_NATIVE_COMPONENT_ASSETS)

class AnimationWebInterface:
    """Web interface for animation management"""

    def __init__(self, control_channel: FileControlChannel,
                 preview_manager: AnimationManager,
                 host: str = '0.0.0.0',
                 port: int = 5000,
                 local_mode: bool = False,
                 release_id: Optional[str] = None):
        """
        Initialize web interface

        Args:
            control_channel: FileControlChannel used to send commands to controller
            preview_manager: AnimationManager instance used only for previews/listing
            host: Host to bind to
            port: Port to listen on
        """
        self.control_channel = control_channel
        self.preview_manager = preview_manager
        self.host = host
        self.port = port
        self.local_mode = bool(local_mode)
        self.release_id = release_id
        self._scene_preview_lock = threading.RLock()
        self.project_root = Path(__file__).resolve().parents[1]
        self.painter_presets_dir = self.project_root / "presets" / "frame_painter"
        self.animation_presets_dir = self.project_root / "presets" / "animations"
        self.scene_presets_dir = self.project_root / "presets" / "scenes"
        self.foliage_mask_path = self.project_root / "config" / "plant_pixel_map_32x138.json"
        self.planter_mask_path = self.project_root / "config" / "plant_globe_map_32x138.json"
        self.deployment_status_path = self.project_root / "run_state" / "deployment.json"
        self.generated_preview_dir = (
            self.project_root / "web" / "static" / "generated" / "animation-previews"
        )
        self.runtime_preview_dir = self.project_root / "run_state" / "animation_previews"
        if self.local_mode:
            self.generated_preview_dir = (
                self.project_root / "run_state" / "mac_animation_previews"
            )
        loader = getattr(self.preview_manager, 'plugin_loader', None)
        self.runtime_preview_worker = None
        if (not self.local_mode and loader is not None
                and os.environ.get("LEDGRID_DISABLE_PREVIEW_WORKER") != "1"):
            self.runtime_preview_worker = RuntimePreviewWorker(
                self.project_root,
                strips=self.preview_manager.controller.strip_count,
                leds_per_strip=self.preview_manager.controller.leds_per_strip,
            )

        # Create Flask app
        self.app = Flask(__name__)
        self.app.secret_key = 'led-grid-secret-key-change-in-production'

        self.painter_presets_dir.mkdir(parents=True, exist_ok=True)
        self.animation_presets_dir.mkdir(parents=True, exist_ok=True)
        self.scene_presets_dir.mkdir(parents=True, exist_ok=True)

        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register Flask routes"""
        
        @self.app.route('/')
        def index():
            """Main dashboard"""
            animations = self._dashboard_animations()
            component_catalog = self._component_catalog()
            component_id_counts: Dict[str, int] = {}
            for component in component_catalog:
                component_id = component.get('plugin_id')
                if isinstance(component_id, str):
                    component_id_counts[component_id] = (
                        component_id_counts.get(component_id, 0) + 1
                    )
            ambiguous_component_ids = {
                component_id
                for component_id, count in component_id_counts.items()
                if count > 1
            }
            animation_by_id = {
                item.get('plugin_name'): item
                for item in animations
                if isinstance(item.get('plugin_name'), str)
            }
            component_previews = {}
            component_presets = {}
            for component in component_catalog:
                component_id = component.get('plugin_id')
                if not isinstance(component_id, str):
                    continue
                provider = component.get('provider')
                if not isinstance(provider, str):
                    continue
                component_key = f'{provider}:{component_id}'
                if component_id in ambiguous_component_ids:
                    # Preview catalogs and preset paths predate provider-qualified
                    # identities. Never decorate the wrong provider by guessing.
                    component_previews[component_key] = None
                    component_presets[component_key] = []
                    continue
                animation = animation_by_id.get(component_id)
                component_previews[component_key] = (
                    animation.get('preview')
                    if animation is not None
                    else self._preview_metadata(component_id)
                )
                component_presets[component_key] = (
                    animation.get('presets', [])
                    if animation is not None
                    else self._list_animation_presets(component_id)
                )
            status = self._status_payload()
            return render_template(
                'index.html',
                animations=[item for item in animations if not item['is_test']],
                test_animations=[item for item in animations if item['is_test']],
                status=status,
                vibe_profiles=self._vibe_profile_catalog(),
                component_catalog=component_catalog,
                component_index={
                    f"{item.get('provider')}:{item['plugin_id']}": item
                    for item in component_catalog
                    if (
                        isinstance(item.get('plugin_id'), str)
                        and isinstance(item.get('provider'), str)
                    )
                },
                component_previews=component_previews,
                component_presets=component_presets,
                ambiguous_component_ids=ambiguous_component_ids,
                receiver_hybrid_enabled=(
                    self._scene_provider_policy().compiled_rainbow_enabled
                ),
                scene_presets=self._list_scene_presets(),
                speed_baseline=DEFAULT_ANIMATION_SPEED_SCALE,
                local_mode=self.local_mode,
            )

        @self.app.route('/studio-next')
        def studio_next():
            """Studio Next shell; authoritative state is fetched after load."""
            return render_template('studio_next.html', local_mode=self.local_mode)

        @self.app.route('/composer')
        def browser_composer():
            """Installable browser-native preset composer shell.

            Rendering, draft persistence, checking, and export happen in the
            browser. Loading this shell never observes or mutates live output.
            """
            return render_template('composer.html', local_mode=self.local_mode)

        @self.app.route('/composer-service-worker.js')
        def browser_composer_service_worker():
            """Serve the composer worker at root scope for installable use."""
            response = send_from_directory(
                self.project_root / 'web' / 'static' / 'js',
                'composer_service_worker.js',
                mimetype='application/javascript',
            )
            response.headers['Service-Worker-Allowed'] = '/'
            response.headers['Cache-Control'] = 'no-cache'
            return response

        @self.app.route('/api/v1/composer/bootstrap')
        def api_browser_composer_bootstrap():
            """Read-only schemas, presets, and explicit browser capabilities."""
            return jsonify(self._browser_composer_bootstrap())

        @self.app.route('/api/v1/composer/connectivity')
        def api_browser_composer_connectivity():
            """Small uncached reachability probe for explicit server actions."""
            response = jsonify({
                'schema': 'ledgrid.browser-composer-connectivity',
                'schema_version': 1,
                'online': True,
                'actions': {
                    'validate_import': True,
                    'save_component_preset': True,
                    'save_scene_preset': True,
                    'activate_scene': True,
                },
            })
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/composer/presets/validate', methods=['POST'])
        def api_browser_composer_validate_preset():
            """Validate an imported component or scene preset without mutation."""
            try:
                validated = self._validated_browser_composer_import(
                    request.get_json(silent=True)
                )
            except (SceneValidationError, ValueError, TypeError) as exc:
                return jsonify({'valid': False, 'error': str(exc)}), 400
            return jsonify({'valid': True, **validated})

        @self.app.route('/api/v1/composer/presets', methods=['POST'])
        def api_browser_composer_save_preset():
            """Persist one component preset without changing live playback."""
            try:
                result, created = self._save_browser_composer_preset(
                    request.get_json(silent=True)
                )
            except FileExistsError as exc:
                preset_id = str(exc)
                return jsonify({
                    'error': f'Preset {preset_id} already exists',
                    'code': 'preset_exists',
                    'preset_id': preset_id,
                }), 409
            except (SceneValidationError, ValueError, TypeError) as exc:
                return jsonify({'error': str(exc)}), 400
            return jsonify({
                'success': True,
                'created': created,
                **result,
            }), 201 if created else 200

        @self.app.route('/api/v1/studio-next/bootstrap')
        def api_studio_next_bootstrap():
            """One provider-safe, non-mutating read model for Studio Next."""
            status = self._status_payload()
            scene = self._current_scene_payload(status)
            return jsonify({
                'schema': 'ledgrid.studio-next-bootstrap',
                'schema_version': 1,
                'local_mode': self.local_mode,
                'generated_at': time.time(),
                'status': status,
                'scene': {
                    'schema': 'ledgrid.scene-api',
                    'schema_version': 1,
                    'scene': scene,
                    'active': scene is not None,
                    'preset_diagnostics': self._scene_preset_diagnostics(scene),
                },
                'vibe_profiles': self._vibe_profile_catalog(),
                'scene_presets': self._list_scene_presets(),
                'catalog': self._studio_next_catalog(),
            })

        @self.app.route('/api/v1/studio-next/take-look', methods=['POST'])
        def api_studio_next_take_look():
            """Start one exact, ready Host Python background preset."""
            payload = request.get_json(silent=True)
            required = {'provider', 'plugin_id', 'preset_id'}
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            if set(payload) != required:
                missing = sorted(required - set(payload))
                unknown = sorted(set(payload) - required)
                details = []
                if missing:
                    details.append(f"missing: {', '.join(missing)}")
                if unknown:
                    details.append(f"unsupported: {', '.join(unknown)}")
                return jsonify({
                    'error': (
                        'take-look requires exactly provider, plugin_id, and preset_id'
                        + (f" ({'; '.join(details)})" if details else '')
                    )
                }), 400

            provider = payload['provider']
            plugin_id = payload['plugin_id']
            preset_id = payload['preset_id']
            if any(
                not isinstance(value, str) or not value
                for value in (provider, plugin_id, preset_id)
            ):
                return jsonify({
                    'error': 'provider, plugin_id, and preset_id must be non-empty strings'
                }), 400
            if self._sanitize_preset_id(preset_id) != preset_id:
                return jsonify({'error': 'preset_id must be a stable identifier'}), 400

            catalog = self._component_catalog()
            providers = {
                str(item.get('provider'))
                for item in catalog
                if item.get('plugin_id') == plugin_id
            }
            if len(providers) > 1:
                return jsonify({
                    'error': (
                        'Look execution is disabled because this plugin ID occurs '
                        'under multiple providers and presets are not provider-qualified'
                    ),
                    'code': 'provider_collision',
                    'plugin_id': plugin_id,
                    'providers': sorted(providers),
                }), 409

            matches = [
                item for item in catalog
                if (
                    item.get('provider') == provider
                    and item.get('plugin_id') == plugin_id
                )
            ]
            if not matches:
                return jsonify({'error': 'Provider-qualified component not found'}), 404
            if len(matches) != 1:
                return jsonify({
                    'error': 'Provider-qualified component identity is ambiguous',
                    'code': 'identity_ambiguous',
                }), 409

            action = self._studio_next_look_action(matches[0])
            if not action['take_look_enabled']:
                return jsonify({
                    'error': action['reason'],
                    'code': action['code'],
                    'identity': {
                        'key': f'{provider}:{plugin_id}:{preset_id}',
                        'provider': provider,
                        'plugin_id': plugin_id,
                        'preset_id': preset_id,
                    },
                }), 409

            preset = self._load_animation_preset(plugin_id, preset_id)
            if preset is None or preset.get('animation') != plugin_id:
                return jsonify({'error': 'Preset not found'}), 404
            validation_error = self._validate_animation_params(
                plugin_id, dict(preset['params'])
            )
            if validation_error:
                return jsonify({
                    'error': f'Preset is not executable: {validation_error}',
                    'code': 'invalid_preset',
                }), 409

            command = self.control_channel.send_command(
                'start', animation=plugin_id, config=dict(preset['params']),
                preset=self._animation_preset_selection(preset),
            )
            identity = {
                'key': f'{provider}:{plugin_id}:{preset_id}',
                'component_key': f'{provider}:{plugin_id}',
                'provider': provider,
                'plugin_id': plugin_id,
                'preset_id': preset_id,
            }
            return jsonify({
                'success': True,
                'identity': identity,
                'preset': self._animation_preset_summary(preset),
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/v1/studio-next/take-scene', methods=['POST'])
        def api_studio_next_take_scene():
            """Start only the deliberately narrow Studio Next scene slice."""
            try:
                scene = self._validated_studio_next_scene_request(
                    request.get_json(silent=True)
                )
            except SceneValidationError as exc:
                return jsonify({'error': str(exc)}), 400
            command = self.control_channel.send_command('start_scene', scene=scene)
            return jsonify({
                'success': True,
                'scene': scene,
                'preset_diagnostics': self._scene_preset_diagnostics(scene),
                'command_id': self._command_id(command),
            })
        
        @self.app.route('/api/animations')
        def api_list_animations():
            """API: Get list of available animations"""
            animations = self._sorted_animations()
            return jsonify(animations)

        @self.app.route('/api/v1/components')
        def api_list_components():
            """Versioned unified catalog, including explicit editor compatibility."""
            try:
                components = filter_catalog(
                    self._component_catalog(),
                    provider=request.args.get('provider'),
                    role=request.args.get('role'),
                    provider_policy=self._scene_provider_policy(),
                )
            except SceneValidationError as exc:
                return jsonify({'error': str(exc)}), 400
            return jsonify({
                'schema': 'ledgrid.component-catalog',
                'schema_version': 1,
                'components': components,
                'filters': {
                    'provider': request.args.get('provider'),
                    'role': request.args.get('role'),
                },
            })

        @self.app.route('/api/v1/scene')
        def api_get_scene():
            scene = self._current_scene_payload()
            return jsonify({
                'schema': 'ledgrid.scene-api', 'schema_version': 1,
                'scene': scene,
                'active': scene is not None,
                'preset_diagnostics': self._scene_preset_diagnostics(scene),
            })

        @self.app.route('/api/v1/scene/validate', methods=['POST'])
        def api_validate_scene():
            try:
                scene = self._validated_scene_request(request.get_json(silent=True))
            except SceneValidationError as exc:
                return jsonify({'valid': False, 'error': str(exc)}), 400
            return jsonify({
                'valid': True, 'scene': scene,
                'preset_diagnostics': self._scene_preset_diagnostics(scene),
            })

        @self.app.route('/api/v1/scene', methods=['PUT', 'POST'])
        def api_start_scene():
            try:
                scene = self._validated_scene_request(request.get_json(silent=True))
            except SceneValidationError as exc:
                return jsonify({'error': str(exc)}), 400
            command = self.control_channel.send_command('start_scene', scene=scene)
            return jsonify({
                'success': True, 'scene': scene,
                'preset_diagnostics': self._scene_preset_diagnostics(scene),
                'command_id': command.get('command_id') if isinstance(command, dict) else None,
            })

        @self.app.route('/api/v1/scene', methods=['DELETE'])
        def api_stop_scene():
            command = self.control_channel.send_command('stop_scene')
            return jsonify({
                'success': True,
                'command_id': command.get('command_id') if isinstance(command, dict) else None,
            })

        @self.app.route('/api/v1/receiver-native/recover', methods=['POST'])
        def api_recover_receiver_native():
            """Explicitly replace native playback with its recorded fallback."""
            command = self.control_channel.send_command('recover_receiver_native')
            return jsonify({
                'success': True,
                'operation': 'recover_to_known_python_fallback',
                'command_id': self._command_id(command),
            }), 202

        @self.app.route(
            '/api/v1/native-backgrounds/<bundle_digest>/<operation>',
            methods=['POST'],
        )
        def api_native_background_operation(bundle_digest: str, operation: str):
            if re.fullmatch(r'[0-9a-f]{64}', bundle_digest) is None:
                return jsonify({'error': 'bundle digest must be lowercase SHA-256'}), 400
            actions = {
                'probe': 'probe_native_background',
                'install': 'install_native_background',
                'clear-quarantine': 'clear_native_background_quarantine',
            }
            action = actions.get(operation)
            if action is None:
                return jsonify({
                    'error': (
                        'native operation must be probe, install, or '
                        'clear-quarantine'
                    ),
                }), 404
            command = self.control_channel.send_command(
                action, bundle_digest=bundle_digest
            )
            return jsonify({
                'success': True,
                'operation': operation,
                'bundle_digest': bundle_digest,
                'command_id': self._command_id(command),
            }), 202

        @self.app.route('/api/v1/scene/components/<target>', methods=['PATCH'])
        def api_update_scene_component(target: str):
            try:
                update = self._validated_scene_update(target, request.get_json(silent=True))
            except SceneValidationError as exc:
                return jsonify({'error': str(exc)}), 400
            command = self.control_channel.send_command(
                'update_scene_component', target=target, update=update
            )
            return jsonify({
                'success': True, 'target': target, 'update': update,
                'command_id': command.get('command_id') if isinstance(command, dict) else None,
            })

        @self.app.route('/api/v1/scene/preview', methods=['POST'])
        def api_preview_scene():
            payload = request.get_json(silent=True)
            try:
                body = payload if isinstance(payload, dict) else {}
                scene = self._validated_scene_request(body.get('scene', body))
                vibe = self._preview_vibe(body.get('vibe'))
                status = self.control_channel.read_status() or {}
                modifiers = PlantModifierState.from_payload(
                    body.get('plant_modifiers', status.get('plant_modifiers', {}))
                ).to_dict()
                preview = self._scene_preview(scene, vibe, modifiers, body.get('elapsed', 0.0))
            except (KeyError, TypeError, ValueError, SceneValidationError) as exc:
                return jsonify({'error': str(exc)}), 400
            preview['preview_identity'] = scene_preview_identity(
                scene, vibe, modifiers, elapsed=float(body.get('elapsed', 0.0)),
                provider_policy=self._scene_provider_policy(),
            )
            if scene['background']['provider'] == 'receiver_native':
                managed = scene['background']['plugin_id'] != 'compiled_rainbow'
                selected = dict(scene['background'].get('resolved_parameters') or {})
                selected.update(scene['background'].get('parameter_overrides') or {})
                descriptor = next((
                    item for item in self._component_catalog()
                    if item.get('provider') == 'receiver_native'
                    and item.get('plugin_id') == scene['background']['plugin_id']
                ), {})
                preview.update({
                    'preview': True,
                    'preview_label': (
                        'Signed build-time bundle preview (authored defaults) — not receiver framebuffer readback'
                        if managed else
                        'Host simulation preview — not receiver framebuffer readback'
                    ),
                    'preview_kind': (
                        'build_time_bundle' if managed else 'host_simulation'
                    ),
                    'parameter_exact': (
                        selected == dict(descriptor.get('defaults') or {})
                        if managed else True
                    ),
                    'vibe_exact': not managed,
                    'background_provider': 'receiver_native',
                    'live_state_mutated': False,
                    'framebuffer_readback': False,
                })
            preview['scene'] = scene
            preview['plant_modifiers'] = modifiers
            return jsonify(preview)

        @self.app.route('/api/v1/components/<component_id>/presets')
        def api_list_component_presets(component_id: str):
            matches = [
                item for item in self._component_catalog()
                if item.get('plugin_id') == component_id
            ]
            if not matches:
                return jsonify({'error': 'Component not found'}), 404
            if len(matches) != 1:
                return jsonify({
                    'error': (
                        'Component preset discovery is ambiguous across providers; '
                        'provider-qualified preset storage is required'
                    ),
                    'component_id': component_id,
                    'providers': sorted({
                        str(item.get('provider')) for item in matches
                    }),
                }), 409
            component = matches[0]
            return jsonify({
                'schema': 'ledgrid.component-preset-list', 'schema_version': 1,
                'component_id': component_id,
                'component': component,
                'presets': self._list_animation_presets(component_id),
            })

        @self.app.route('/api/v1/scene-presets')
        def api_list_scene_presets():
            return jsonify({
                'schema': 'ledgrid.scene-preset-list', 'schema_version': 1,
                'presets': self._list_scene_presets(),
            })

        @self.app.route('/api/v1/scene-presets/<preset_id>')
        def api_get_scene_preset(preset_id: str):
            preset = self._load_scene_preset(preset_id)
            if preset is None:
                return jsonify({'error': 'Scene preset not found'}), 404
            return jsonify(preset)

        @self.app.route('/api/v1/scene-presets', methods=['POST'])
        def api_save_scene_preset():
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            name = str(payload.get('name') or '').strip()
            preset_id = self._sanitize_preset_id(name)
            if not name or not preset_id:
                return jsonify({'error': 'Scene preset name is required'}), 400
            if any(key in payload for key in ('vibe', 'plant_modifiers', 'output')):
                return jsonify({'error': 'Scene presets never capture vibe, plant, or output state'}), 400
            try:
                scene = self._validated_scene_request(payload.get('scene'))
            except SceneValidationError as exc:
                return jsonify({'error': str(exc)}), 400
            existing = self._load_scene_preset(preset_id) or {}
            now = time.time()
            preset = {
                'schema': SCENE_PRESET_SCHEMA,
                'schema_version': SCENE_PRESET_VERSION,
                'preset_id': preset_id,
                'name': name,
                'description': str(payload.get('description') or ''),
                'scene': scene,
                'created_at': existing.get('created_at', now),
                'updated_at': now,
            }
            self._write_scene_preset(preset_id, preset)
            return jsonify({'success': True, 'preset': preset})

        @self.app.route('/api/v1/scene-presets/<preset_id>/apply', methods=['POST'])
        def api_apply_scene_preset(preset_id: str):
            preset = self._load_scene_preset(preset_id)
            if preset is None:
                return jsonify({'error': 'Scene preset not found'}), 404
            try:
                scene = self._validated_scene_request(preset.get('scene'))
            except SceneValidationError as exc:
                return jsonify({'error': f'Invalid stored scene preset: {exc}'}), 409
            command = self.control_channel.send_command(
                'start_scene', scene=scene,
                preset={'preset_id': preset_id, 'name': preset['name']},
            )
            return jsonify({
                'success': True, 'preset': preset,
                'command_id': command.get('command_id') if isinstance(command, dict) else None,
            })

        @self.app.route('/api/v1/scene-presets/<preset_id>', methods=['DELETE'])
        def api_delete_scene_preset(preset_id: str):
            path = self._scene_preset_path(preset_id)
            if path is None or not path.is_file():
                return jsonify({'error': 'Scene preset not found'}), 404
            try:
                path.unlink()
            except OSError:
                return jsonify({'error': 'Failed to delete scene preset'}), 500
            return jsonify({'success': True})

        @self.app.route('/preview-assets/runtime/<path:filename>')
        def runtime_preview_asset(filename: str):
            """Serve protected Pi-generated previews with immutable caching."""
            return send_from_directory(
                self.runtime_preview_dir, filename, max_age=31536000, conditional=True
            )

        @self.app.route('/preview-assets/generated/<path:filename>')
        def generated_preview_asset(filename: str):
            """Serve content-addressed deploy previews with immutable caching."""
            return send_from_directory(
                self.generated_preview_dir, filename, max_age=31536000, conditional=True
            )
        
        @self.app.route('/api/animations/<animation_name>')
        def api_get_animation(animation_name):
            """API: Get detailed info about specific animation"""
            info = self.preview_manager.get_animation_info(animation_name)
            if info:
                return jsonify(info)
            return jsonify({'error': 'Animation not found'}), 404

        @self.app.route('/api/animations/<animation_name>/presets')
        def api_list_animation_presets(animation_name: str):
            """API: List presets for one animation, reading disk on every call."""
            return jsonify({
                'animation': animation_name,
                'presets': self._list_animation_presets(animation_name),
            })

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>')
        def api_get_animation_preset(animation_name: str, preset_id: str):
            """API: Load an animation preset from disk."""
            preset = self._load_animation_preset(animation_name, preset_id)
            if not preset:
                return jsonify({'error': 'Preset not found'}), 404
            return jsonify(preset)

        @self.app.route('/api/animations/<animation_name>/presets', methods=['POST'])
        def api_save_animation_preset(animation_name: str):
            """API: Save or overwrite a named set of animation parameters."""
            if not self._animation_preset_dir(animation_name):
                return jsonify({'error': 'Animation name is invalid'}), 400

            payload = request.get_json(silent=True) or {}
            raw_name = (payload.get('name') or '').strip()
            params = payload.get('params')
            if not raw_name:
                return jsonify({'error': 'Preset name is required'}), 400
            if not isinstance(params, dict):
                return jsonify({'error': 'params must be a JSON object'}), 400
            params = dict(params)
            validation_error = self._validate_animation_params(animation_name, params)
            if validation_error:
                return jsonify({'error': validation_error}), 400

            preset_id = self._sanitize_preset_id(raw_name)
            if not preset_id:
                return jsonify({'error': 'Preset name is invalid'}), 400

            existing = self._load_animation_preset(animation_name, preset_id)
            now = time.time()
            preset_payload = {
                'version': 2,
                'preset_id': preset_id,
                'name': raw_name,
                'animation': animation_name,
                'params': params,
                'created_at': existing.get('created_at', now) if existing else now,
                'updated_at': now,
            }
            for field in ('category', 'description', 'tags', 'palette'):
                if field in payload:
                    preset_payload[field] = payload[field]
                elif existing and field in existing:
                    preset_payload[field] = existing[field]
            self._write_animation_preset(animation_name, preset_id, preset_payload)
            self.control_channel.send_command(
                'set_current_preset', preset=self._animation_preset_selection(preset_payload)
            )
            if self.runtime_preview_worker is not None:
                fallback = self._preview_metadata(animation_name) or {}
                preset_path = self._animation_preset_path(animation_name, preset_id)
                if preset_path is not None:
                    self.runtime_preview_worker.queue(
                        animation_name, preset_id, preset_path, fallback
                    )
            return jsonify({'success': True, 'preset': self._animation_preset_summary(preset_payload)})

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>/apply', methods=['POST'])
        def api_apply_animation_preset(animation_name: str, preset_id: str):
            """API: Re-read a preset from disk and start its animation with those settings."""
            if not self.preview_manager.get_animation_info(animation_name):
                return jsonify({'error': 'Animation not found'}), 404
            preset = self._load_animation_preset(animation_name, preset_id)
            if not preset:
                return jsonify({'error': 'Preset not found'}), 404
            command = self.control_channel.send_command(
                'start', animation=animation_name, config=preset['params'],
                preset=self._animation_preset_selection(preset),
            )
            return jsonify({
                'success': True,
                'preset': preset,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>', methods=['DELETE'])
        def api_delete_animation_preset(animation_name: str, preset_id: str):
            """API: Delete one animation preset."""
            path = self._animation_preset_path(animation_name, preset_id)
            if path is None or not path.is_file():
                return jsonify({'error': 'Preset not found'}), 404
            try:
                path.unlink()
            except OSError:
                return jsonify({'error': 'Failed to delete preset'}), 500
            if self.runtime_preview_worker is not None:
                self.runtime_preview_worker.delete(animation_name, preset_id)
            return jsonify({'success': True})
        
        @self.app.route('/api/start/<animation_name>', methods=['POST'])
        def api_start_animation(animation_name):
            """API: Start an animation"""
            if not self.preview_manager.get_animation_info(animation_name):
                return jsonify({'error': 'Animation not found'}), 404
            config = request.get_json() or {}
            self.control_channel.send_command('start', animation=animation_name, config=config)
            # Controller polls periodically, so assume success if write succeeded
            success = True
            return jsonify({'success': success})
        
        @self.app.route('/api/stop', methods=['POST'])
        def api_stop_animation():
            """API: Stop current animation"""
            command = self.control_channel.send_command('stop')
            return jsonify({
                'success': True,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/device/state', methods=['POST'])
        def api_set_device_state():
            """API: Apply power, brightness, animation, and preset as one command."""
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or not payload:
                return jsonify({'error': 'request body must be a non-empty JSON object'}), 400

            supported = {'power', 'brightness', 'animation', 'preset'}
            unknown = sorted(set(payload) - supported)
            if unknown:
                return jsonify({
                    'error': f"unsupported device state fields: {', '.join(unknown)}"
                }), 400

            command_data: Dict[str, Any] = {}
            if 'power' in payload:
                if not isinstance(payload['power'], bool):
                    return jsonify({'error': 'power must be boolean'}), 400
                command_data['power'] = payload['power']

            if 'brightness' in payload:
                try:
                    command_data['brightness'] = AnimationManager.validate_output_brightness(
                        payload['brightness']
                    )
                except ValueError as exc:
                    return jsonify({'error': str(exc)}), 400

            animation_name = payload.get('animation')
            if 'animation' in payload:
                if not isinstance(animation_name, str) or not animation_name:
                    return jsonify({'error': 'animation must be a non-empty string'}), 400
                if not self.preview_manager.get_animation_info(animation_name):
                    return jsonify({'error': 'Animation not found'}), 404
                if payload.get('power') is False:
                    return jsonify({
                        'error': 'power false cannot be combined with an animation'
                    }), 400
                command_data['animation'] = animation_name

            if 'preset' in payload:
                preset_id = payload['preset']
                if not isinstance(preset_id, str) or not preset_id:
                    return jsonify({'error': 'preset must be a non-empty string'}), 400
                if not animation_name:
                    return jsonify({'error': 'preset requires an animation'}), 400
                preset = self._load_animation_preset(animation_name, preset_id)
                if not preset:
                    return jsonify({'error': 'Preset not found'}), 404
                command_data['config'] = dict(preset['params'])
                command_data['preset'] = self._animation_preset_selection(preset)

            command = self.control_channel.send_command(
                'set_device_state', **command_data
            )
            return jsonify({
                'success': True,
                'state': payload,
                'command_id': self._command_id(command),
            })
        
        @self.app.route('/api/status')
        def api_get_status():
            """API: Get current status"""
            return jsonify(self._status_payload())

        @self.app.route('/api/config/vibe', methods=['GET'])
        @self.app.route('/api/v1/vibe', methods=['GET'])
        def api_get_vibe():
            """API: Read the selected global vibe and stable profile catalog."""
            return jsonify({
                'version': 1,
                'vibe': self._selected_vibe_status(),
                'profiles': self._vibe_profile_catalog(),
            })

        @self.app.route('/api/config/vibe', methods=['POST'])
        @self.app.route('/api/v1/vibe', methods=['PUT', 'POST'])
        def api_set_vibe():
            """API: Validate and independently update the global vibe."""
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            requested = payload.get('vibe')
            if requested is None:
                requested = payload.get('id', payload.get('vibe_id'))
            try:
                state = self._canonical_vibe_state(requested)
            except (KeyError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400
            profile = self._vibe_profile_for_state(state)
            command = self.control_channel.send_command('set_vibe', vibe=state)
            return jsonify({
                'success': True,
                'version': 1,
                'requested_vibe': state,
                'profile': profile,
                'command_id': command.get('command_id') if isinstance(command, dict) else None,
            })
        
        @self.app.route('/api/stats')
        def api_get_stats():
            """API: Runtime stats payload that mirrors /api/status"""
            status = self._status_payload()
            return jsonify(status)

        @self.app.route('/api/metrics')
        def api_get_metrics():
            """API: Summarized performance metrics."""
            status = self._status_payload()
            return jsonify({
                'animation': {
                    'target_fps': status.get('target_fps', 0),
                    'actual_fps': status.get('actual_fps', 0),
                    'uptime': status.get('uptime', 0),
                },
                'performance': status.get('performance', {}),
                'driver': status.get('driver_stats', {}),
                'system': {},
            })

        @self.app.route('/api/v1/receivers/status/refresh', methods=['POST'])
        def api_refresh_receiver_status():
            """Request a fresh controller-side SPI status drain on every receiver."""
            request_id = f"phase3a-{time.time_ns():x}"
            command = self.control_channel.send_command(
                'refresh_receiver_status', request_id=request_id
            )
            return jsonify({
                'accepted': True,
                'request_id': request_id,
                'command_id': (
                    command.get('command_id') if isinstance(command, dict) else None
                ),
            }), 202

        @self.app.route('/api/config/target-fps', methods=['POST'])
        def api_set_target_fps():
            payload = request.get_json(silent=True) or {}
            try:
                target_fps = int(payload.get('target_fps'))
            except (TypeError, ValueError):
                return jsonify({'error': 'target_fps must be an integer'}), 400
            if target_fps < 1 or target_fps > 200:
                return jsonify({'error': 'target_fps must be between 1 and 200'}), 400
            command = self.control_channel.send_command(
                'set_target_fps', target_fps=target_fps
            )
            return jsonify({
                'success': True,
                'target_fps': target_fps,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/config/animation-speed', methods=['POST'])
        def api_set_animation_speed():
            payload = request.get_json(silent=True) or {}
            try:
                multiplier = float(payload.get('multiplier'))
            except (TypeError, ValueError):
                return jsonify({'error': 'multiplier must be numeric'}), 400
            if not math.isfinite(multiplier) or multiplier <= 0:
                return jsonify({'error': 'multiplier must be a positive finite number'}), 400
            speed_scale = DEFAULT_ANIMATION_SPEED_SCALE * multiplier
            command = self.control_channel.send_command(
                'set_animation_speed_scale', animation_speed_scale=speed_scale
            )
            return jsonify({
                'success': True,
                'multiplier': multiplier,
                'animation_speed_scale': speed_scale,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/config/brightness', methods=['POST'])
        def api_set_output_brightness():
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            try:
                brightness = AnimationManager.validate_output_brightness(
                    payload.get('brightness')
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            command = self.control_channel.send_command(
                'set_output_brightness', brightness=brightness
            )
            return jsonify({
                'success': True,
                'brightness': brightness,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/config/plant-aware', methods=['POST'])
        def api_set_plant_aware():
            payload = request.get_json(silent=True) or {}
            enabled = payload.get('plant_aware')
            if not isinstance(enabled, bool):
                return jsonify({'error': 'plant_aware must be boolean'}), 400
            if not self.local_mode and hasattr(self.preview_manager, 'set_plant_aware'):
                self.preview_manager.set_plant_aware(enabled)
            self.control_channel.send_command('set_plant_aware', plant_aware=enabled)
            return jsonify({'success': True, 'plant_aware': enabled})

        @self.app.route('/api/config/plant-modifiers', methods=['POST'])
        def api_set_plant_modifiers():
            payload = request.get_json(silent=True) or {}
            try:
                state = PlantModifierState.from_payload(payload.get('plant_modifiers'))
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            serialized = state.to_dict()
            if not self.local_mode and hasattr(self.preview_manager, 'set_plant_modifiers'):
                self.preview_manager.set_plant_modifiers(serialized)
            command = self.control_channel.send_command(
                'set_plant_modifiers', plant_modifiers=serialized
            )
            return jsonify({
                'success': True,
                'plant_modifiers': serialized,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/hardware/stats')
        def api_get_hardware_stats():
            """API: Hardware stats for SPI devices."""
            status = self._status_payload()
            return jsonify(status.get('driver_stats', {}))

        @self.app.route('/api/hole', methods=['POST'])
        def api_trigger_hole():
            """Punch a random hole or one at the supplied grid coordinate."""
            payload = request.get_json(silent=True) or {}
            data: Dict[str, float] = {}
            for key in ('x', 'y', 'radius'):
                value = payload.get(key)
                if value is not None:
                    if not isinstance(value, (int, float)):
                        return jsonify({'error': f'{key} must be numeric'}), 400
                    data[key] = float(value)
            if ('x' in data) != ('y' in data):
                return jsonify({'error': 'x and y must be provided together'}), 400
            self.control_channel.send_command('puncture_hole', **data)
            return jsonify({'success': True, 'positioned': 'x' in data})

        @self.app.route('/api/interaction', methods=['POST'])
        def api_animation_interaction():
            """Send a logical-grid primary interaction to the live animation."""
            payload = request.get_json(silent=True) or {}
            try:
                kind, x, y, strength = self._validated_interaction_payload(payload)
                raw_status = self.control_channel.read_status() or {}
                layout = self._sync_preview_layout_from_status(raw_status)
                self._validate_interaction_bounds(x, y, layout)
                supported = raw_status.get('interaction_types', [])
                if kind not in supported:
                    raise ValueError(f'interaction {kind!r} is not supported')
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            self.control_channel.send_command(
                'animation_interaction', kind=kind, x=x, y=y, strength=strength
            )
            return jsonify({'success': True, 'accepted': True})

        @self.app.route('/api/frame')
        def api_get_frame():
            """API: Get current animation frame data"""
            return jsonify(self._status_payload(decode_frame=True))

        @self.app.route('/api/painter/updates', methods=['POST'])
        def api_painter_apply_updates():
            """API: Apply sparse frame painter pixel updates."""
            payload = request.get_json(silent=True) or {}
            updates = payload.get('updates')
            if not isinstance(updates, list) or not updates:
                return jsonify({'error': 'updates must be a non-empty list'}), 400

            self.control_channel.send_command('painter_apply_updates', updates=updates)
            return jsonify({'success': True, 'queued_updates': len(updates)})

        @self.app.route('/api/painter/frame', methods=['POST'])
        def api_painter_set_frame():
            """API: Replace the entire frame painter frame."""
            payload = request.get_json(silent=True) or {}
            led_info = self._normalize_led_info(payload.get('led_info'))
            normalized_frame = self._extract_normalized_frame(payload, led_info=led_info)
            if normalized_frame is None:
                return jsonify({'error': 'Provide frame_data or frame_data_encoded'}), 400

            self.control_channel.send_command(
                'painter_set_frame',
                frame_data_encoded=encode_frame_data(normalized_frame),
                frame_data_length=len(normalized_frame),
            )
            return jsonify({'success': True, 'frame_data_length': len(normalized_frame)})

        @self.app.route('/api/painter/clear', methods=['POST'])
        def api_painter_clear():
            """API: Clear the frame painter output to black."""
            self.control_channel.send_command('painter_clear')
            return jsonify({'success': True})

        @self.app.route('/api/painter/masks')
        def api_painter_get_masks():
            """API: Load the two editable semantic plant-mask layers."""
            try:
                return jsonify(self._load_painter_masks())
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 500

        @self.app.route('/api/painter/masks', methods=['POST'])
        def api_painter_save_masks():
            """API: Validate and atomically update the calibrated plant masks."""
            payload = request.get_json(silent=True) or {}
            try:
                saved = self._save_painter_masks(payload)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except OSError as exc:
                return jsonify({'error': f'Failed to save masks: {exc}'}), 500
            return jsonify({'success': True, **saved})

        @self.app.route('/api/painter/presets')
        def api_painter_list_presets():
            """API: List available frame painter presets."""
            return jsonify({'presets': self._list_painter_presets()})

        @self.app.route('/api/painter/presets/<preset_id>')
        def api_painter_get_preset(preset_id: str):
            """API: Load a frame painter preset by id."""
            preset = self._load_painter_preset(preset_id)
            if not preset:
                return jsonify({'error': 'Preset not found'}), 404
            return jsonify(preset)

        @self.app.route('/api/painter/presets', methods=['POST'])
        def api_painter_save_preset():
            """API: Save or overwrite a frame painter preset."""
            payload = request.get_json(silent=True) or {}
            raw_name = (payload.get('name') or '').strip()
            if not raw_name:
                return jsonify({'error': 'Preset name is required'}), 400

            preset_id = self._sanitize_preset_id(raw_name)
            if not preset_id:
                return jsonify({'error': 'Preset name is invalid'}), 400

            status = self._status_payload()
            led_info = self._normalize_led_info(payload.get('led_info') or status.get('led_info'))
            frame_data = self._extract_normalized_frame(payload, led_info=led_info)
            if frame_data is None:
                frame_data = self._extract_normalized_frame(status, led_info=led_info)
            if frame_data is None:
                frame_data = [[0, 0, 0] for _ in range(led_info['total_leds'])]

            existing = self._load_painter_preset(preset_id)
            now = time.time()
            preset_payload = {
                'preset_id': preset_id,
                'name': raw_name,
                'created_at': existing.get('created_at', now) if isinstance(existing, dict) else now,
                'updated_at': now,
                'led_info': led_info,
                'frame_encoding': FRAME_ENCODING_NAME,
                'frame_data_length': len(frame_data),
                'frame_data_encoded': encode_frame_data(frame_data),
            }
            self._write_painter_preset(preset_id, preset_payload)

            return jsonify({
                'success': True,
                'preset': self._preset_summary(preset_payload),
            })

        @self.app.route('/api/preview/<animation_name>')
        def api_get_preview(animation_name):
            """API: Get preview frame data for a specific animation"""
            try:
                self._sync_preview_layout_from_status()
                # Get a sample frame from the animation without starting it
                vibe = self._preview_vibe(request.args.get('vibe'))
                preview_data = self.preview_manager.get_animation_preview(
                    animation_name, vibe=vibe
                )
                return jsonify(preview_data)
            except (KeyError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400
            except Exception as e:
                return jsonify({
                    'error': f'Failed to get preview for {animation_name}: {str(e)}',
                    'frame_data': [],
                    'led_info': self._fallback_led_info(),
                    'is_running': False,
                    'frame_count': 0,
                    'timestamp': time.time()
                }), 500

        @self.app.route('/api/preview/<animation_name>/with_params', methods=['POST'])
        def api_get_preview_with_params(animation_name):
            """API: Get preview frame data for a specific animation with custom parameters"""
            try:
                self._sync_preview_layout_from_status()
                payload = request.get_json(silent=True)
                if payload is None:
                    payload = {}
                if not isinstance(payload, dict):
                    raise ValueError('request body must be a JSON object')
                if isinstance(payload.get('params'), dict):
                    params = dict(payload['params'])
                    requested_vibe = payload.get('vibe', request.args.get('vibe'))
                else:
                    # Preserve the legacy flat parameter payload. Vibe travels in
                    # the query string so a plugin parameter cannot be stolen.
                    params = payload
                    requested_vibe = request.args.get('vibe')
                vibe = self._preview_vibe(requested_vibe)
                preview_data = self.preview_manager.get_animation_preview_with_params(
                    animation_name, params, vibe=vibe
                )
                return jsonify(preview_data)
            except (KeyError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400
            except Exception as e:
                return jsonify({
                    'error': f'Failed to get preview for {animation_name}: {str(e)}',
                    'frame_data': [],
                    'led_info': self._fallback_led_info(),
                    'is_running': False,
                    'frame_count': 0,
                    'timestamp': time.time()
                }), 500

        @self.app.route('/api/preview/<animation_name>/interaction', methods=['POST'])
        def api_preview_interaction(animation_name):
            """Apply an interaction only to the isolated preview session."""
            payload = request.get_json(silent=True) or {}
            try:
                kind, x, y, strength = self._validated_interaction_payload(payload)
                params = payload.get('params')
                if params is not None and not isinstance(params, dict):
                    raise ValueError('params must be an object')
                layout = self._sync_preview_layout_from_status()
                self._validate_interaction_bounds(x, y, layout)
                accepted = self.preview_manager.dispatch_preview_interaction(
                    animation_name, kind, x, y, strength, params=params
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            return jsonify({'success': True, 'accepted': bool(accepted)})
        
        @self.app.route('/api/parameters', methods=['POST'])
        def api_update_parameters():
            """API: Update animation parameters"""
            params = request.get_json() or {}
            self.control_channel.send_command('update_params', params=params)
            return jsonify({'success': True})

        def _handle_dpad(direction: str):
            """API: Send a D-pad input to the running animation."""
            direction = (direction or '').lower().replace('_', '-')
            valid = {'up', 'down', 'left', 'right', 'rotate-left', 'rotate-right', 'drop'}
            if direction not in valid:
                return jsonify({'error': 'Invalid dpad direction'}), 400
            self.control_channel.send_command('dpad', direction=direction)
            return jsonify({'success': True, 'direction': direction})

        @self.app.route('/dpad/<direction>', methods=['POST'])
        def api_dpad(direction):
            return _handle_dpad(direction)

        @self.app.route('/api/dpad/<direction>', methods=['POST'])
        def api_dpad_via_api(direction):
            return _handle_dpad(direction)

        @self.app.route('/api/reload/<animation_name>', methods=['POST'])
        def api_reload_animation(animation_name):
            """API: Reload specific animation plugin"""
            success = self.preview_manager.reload_animation(animation_name)
            if success:
                self.control_channel.send_command('refresh_plugins', animation=animation_name)
            return jsonify({'success': success})
        
        @self.app.route('/api/refresh', methods=['POST'])
        def api_refresh_plugins():
            """API: Refresh all plugins"""
            plugins = self.preview_manager.refresh_plugins()
            self.control_channel.send_command('refresh_plugins')
            return jsonify({'success': True, 'plugins': plugins})
        
        @self.app.route('/control')
        def control_page():
            """Animation control page"""
            animations = self._sorted_animations()
            status = self._status_payload()
            return render_template('control.html', animations=animations, status=status)

        @self.app.route('/emoji')
        def emoji_arranger_page():
            """Emoji arranger page"""
            status = self._status_payload()
            return render_template('emoji_arranger.html', status=status)

        @self.app.route('/painter')
        def frame_painter_page():
            """Frame painter page."""
            status = self._status_payload()
            return render_template('painter.html', status=status)

    def _dashboard_animations(self) -> List[Dict[str, Any]]:
        """Decorate plugin metadata for the dashboard's show/test galleries."""
        catalog = []
        for animation in self._sorted_animations():
            item = dict(animation)
            plugin_name = item.get('plugin_name', '')
            item.setdefault('emoji', '✨')
            item.setdefault('is_test', False)
            item['preview'] = self._preview_metadata(plugin_name)
            presets = self._list_animation_presets(plugin_name)
            for preset in presets:
                preset['emoji'] = self._preset_emoji(preset, item['emoji'])
            item['presets'] = presets
            catalog.append(item)
        return catalog

    @staticmethod
    def _canonical_vibe_state(requested: Any) -> Dict[str, Any]:
        """Resolve untrusted API input through the central versioned registry."""
        from animation.core.presentation_contracts import VibeState, resolve_vibe

        if isinstance(requested, str):
            resolved = resolve_vibe(requested)
        elif isinstance(requested, dict):
            payload = requested.get('state', requested)
            state = VibeState.from_payload(payload)
            resolved = resolve_vibe(
                state.vibe_id,
                revision=state.revision,
                profile_version=state.profile_version,
            )
            if (
                resolved.state.resolved_profile_digest
                != state.resolved_profile_digest
            ):
                raise ValueError('vibe profile digest does not match registry')
        else:
            raise ValueError('vibe must be a stable vibe ID or versioned vibe state')
        return resolved.state.to_dict()

    def _selected_vibe_status(self) -> Dict[str, Any]:
        """Return live controller vibe status, falling back to local neutral."""
        status = self.control_channel.read_status() or {}
        vibe = status.get('vibe')
        if isinstance(vibe, dict):
            return dict(vibe)
        getter = getattr(self.preview_manager, 'get_vibe_status', None)
        if callable(getter):
            return dict(getter())
        return {'state': self._canonical_vibe_state('neutral')}

    def _preview_vibe(self, requested: Any = None) -> Dict[str, Any]:
        """Resolve preview vibe explicitly without mutating the live manager."""
        if requested is not None:
            return self._canonical_vibe_state(requested)
        selected = self._selected_vibe_status()
        return self._canonical_vibe_state(selected.get('state', selected))

    @staticmethod
    def _vibe_profile_catalog() -> List[Dict[str, Any]]:
        """Serialize public profile choices for API and dashboard consumers."""
        from animation.core.presentation_contracts import list_vibe_profiles

        catalog = []
        for profile in list_vibe_profiles():
            payload = profile.to_dict()
            catalog.append(payload)
        return catalog

    @staticmethod
    def _vibe_profile_for_state(state: Dict[str, Any]) -> Dict[str, Any]:
        from animation.core.presentation_contracts import get_vibe_profile

        vibe_id = state.get('id', state.get('vibe_id'))
        profile = get_vibe_profile(vibe_id)
        return profile.to_dict()

    def _preview_catalog(self) -> Dict[str, Any]:
        """Merge deploy-generated previews with target-owned runtime previews."""
        if self.local_mode:
            return load_catalog(self.generated_preview_dir / "catalog.json")
        return merge_catalogs(
            load_catalog(self.generated_preview_dir / "catalog.json"),
            load_catalog(self.runtime_preview_dir / "catalog.json"),
        )

    def _preview_metadata(
        self, animation_name: str, preset_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        catalog = self._preview_catalog()
        animation_preview = catalog.get('animations', {}).get(animation_name)
        if preset_id is None:
            return dict(animation_preview) if isinstance(animation_preview, dict) else None
        preset_preview = (
            catalog.get('presets', {}).get(animation_name, {}).get(preset_id)
        )
        if isinstance(preset_preview, dict):
            return dict(preset_preview)
        if isinstance(animation_preview, dict):
            fallback = dict(animation_preview)
            fallback['status'] = 'pending'
            return fallback
        return None

    def _sorted_animations(self) -> List[Dict[str, Any]]:
        """Return animation metadata alphabetized by its display name."""
        return sorted(
            self.preview_manager.list_animations(),
            key=lambda animation: str(
                animation.get('name') or animation.get('plugin_name') or ''
            ).casefold(),
        )

    def _component_catalog(self) -> List[Dict[str, Any]]:
        """Read the unified descriptor catalog without importing implementations."""
        policy = self._scene_provider_policy()
        getter = getattr(self.preview_manager, 'list_components', None)
        if callable(getter):
            raw = getter()
            if isinstance(raw, dict):
                raw = raw.get('components', [])
            return decorate_catalog(raw or [], provider_policy=policy)
        loader = getattr(self.preview_manager, 'plugin_loader', None)
        if loader is not None:
            catalog_getter = getattr(loader, 'component_catalog', None)
            if callable(catalog_getter):
                return decorate_catalog(
                    catalog_getter(), provider_policy=policy
                )
        # Small test doubles and legacy local integrations may expose only the
        # animation list.  Keep this adapter explicit and Python/background-only.
        return decorate_catalog(({
            **item,
            'plugin_id': item.get('plugin_id', item.get('plugin_name')),
            'provider': item.get('provider', 'python'),
            'role': item.get('role', 'background'),
        } for item in self.preview_manager.list_animations()), provider_policy=policy)

    @staticmethod
    def _command_id(command: Any) -> Any:
        """Extract correlation when the configured control channel supplies it."""
        return command.get('command_id') if isinstance(command, dict) else None

    def _studio_next_look_action(
        self, component: Dict[str, Any], *, provider_collision: bool = False
    ) -> Dict[str, Any]:
        """Return the one fail-closed direct-look execution decision."""
        if provider_collision:
            return {
                'take_look_enabled': False,
                'code': 'provider_collision',
                'reason': (
                    'This plugin ID occurs under multiple providers. Legacy presets '
                    'and previews cannot be assigned safely.'
                ),
            }
        provider = component.get('provider')
        if provider != 'python':
            return {
                'take_look_enabled': False,
                'code': 'unsupported_provider',
                'reason': 'Direct Looks support ready Host Python backgrounds only.',
            }
        if component.get('role') != 'background':
            return {
                'take_look_enabled': False,
                'code': 'unsupported_role',
                'reason': 'Direct Looks require a background component.',
            }
        if component.get('gallery') != 'show' or component.get('is_test') is True:
            return {
                'take_look_enabled': False,
                'code': 'developer_only',
                'reason': 'Test and developer components are available through Tools only.',
            }

        readiness_values = {
            str(component.get(field) or '').strip().casefold().replace('-', '_')
            for field in ('status', 'availability', 'readiness')
        }
        forbidden_readiness = {
            'build_only', 'unavailable', 'quarantined', 'disabled', 'error',
        }
        blocked = sorted(readiness_values & forbidden_readiness)
        if blocked:
            return {
                'take_look_enabled': False,
                'code': blocked[0],
                'reason': f"Component readiness is {blocked[0].replace('_', ' ')}.",
            }

        compatibility = component.get('compatibility')
        if not isinstance(compatibility, dict):
            compatibility = {}
        if compatibility.get('composable') is not True:
            return {
                'take_look_enabled': False,
                'code': 'not_composable',
                'reason': str(
                    compatibility.get('diagnostic')
                    or 'The component is not composable as a background.'
                ),
            }
        if compatibility.get('implementation_loaded') is not True:
            return {
                'take_look_enabled': False,
                'code': 'build_only',
                'reason': 'The Host Python implementation is not loaded.',
            }

        getter = getattr(self.preview_manager, 'get_animation_info', None)
        try:
            loaded = getter(component.get('plugin_id')) if callable(getter) else None
        except (KeyError, TypeError, ValueError):
            loaded = None
        if not loaded:
            return {
                'take_look_enabled': False,
                'code': 'implementation_unavailable',
                'reason': 'The preview manager has not loaded this implementation.',
            }
        return {
            'take_look_enabled': True,
            'code': 'ready',
            'reason': 'Ready Host Python background.',
        }

    @staticmethod
    def _studio_next_preview(
        provider: str,
        descriptor_preview: Any,
        asset_preview: Any,
    ) -> Optional[Dict[str, Any]]:
        """Combine safe preview metadata while preserving explicit provenance."""
        metadata: Dict[str, Any] = {}
        if isinstance(descriptor_preview, dict):
            metadata.update(descriptor_preview)
        if isinstance(asset_preview, dict):
            metadata.update(asset_preview)
        if not metadata:
            return None
        metadata['live_state_mutated'] = False
        metadata.setdefault('framebuffer_readback', False)
        if provider == 'receiver_native':
            metadata['provenance'] = 'receiver_host_simulation'
            metadata['label'] = (
                'Host simulation preview — not receiver framebuffer readback'
            )
        else:
            metadata['provenance'] = 'isolated_host_preview'
            metadata['label'] = (
                'Isolated host preview — never changes the physical wall'
            )
        return metadata

    def _studio_next_catalog(self) -> Dict[str, Any]:
        """Build provider-qualified components and flattened preset records."""
        raw_components = self._component_catalog()
        providers_by_id: Dict[str, set] = {}
        for component in raw_components:
            plugin_id = component.get('plugin_id')
            provider = component.get('provider')
            if isinstance(plugin_id, str) and isinstance(provider, str):
                providers_by_id.setdefault(plugin_id, set()).add(provider)
        collisions = {
            plugin_id: sorted(providers)
            for plugin_id, providers in providers_by_id.items()
            if len(providers) > 1
        }

        components: List[Dict[str, Any]] = []
        presets: List[Dict[str, Any]] = []
        withheld_presets = 0
        diagnostics = []
        for plugin_id, providers in sorted(collisions.items()):
            discovered = self._list_animation_presets(plugin_id)
            withheld_presets += len(discovered)
            diagnostics.append({
                'code': 'provider_collision',
                'plugin_id': plugin_id,
                'providers': providers,
                'withheld_legacy_presets': len(discovered),
                'message': (
                    'Legacy preset and preview records are withheld because their '
                    'provider cannot be determined safely.'
                ),
            })

        for raw in sorted(
            raw_components,
            key=lambda item: (
                str(item.get('name') or item.get('plugin_id') or '').casefold(),
                str(item.get('provider') or ''),
            ),
        ):
            component = json.loads(json.dumps(raw))
            plugin_id = component.get('plugin_id')
            provider = component.get('provider')
            if not isinstance(plugin_id, str) or not isinstance(provider, str):
                continue
            component_key = f'{provider}:{plugin_id}'
            collision = plugin_id in collisions
            descriptor_preview = component.get('preview')
            asset_preview = None if collision else self._preview_metadata(plugin_id)
            component['key'] = component_key
            component['provider_collision'] = collision
            component['preview_contract'] = (
                descriptor_preview if isinstance(descriptor_preview, dict) else {}
            )
            component['preview'] = (
                None
                if collision
                else self._studio_next_preview(
                    provider, descriptor_preview, asset_preview
                )
            )
            component['action'] = self._studio_next_look_action(
                component, provider_collision=collision
            )
            component['preset_keys'] = []

            if not collision:
                for preset in self._list_animation_presets(plugin_id):
                    preset_id = preset.get('preset_id')
                    if not isinstance(preset_id, str):
                        continue
                    preset_key = f'{component_key}:{preset_id}'
                    item = dict(preset)
                    item.update({
                        'key': preset_key,
                        'component_key': component_key,
                        'provider': provider,
                        'plugin_id': plugin_id,
                        'preview': self._studio_next_preview(
                            provider,
                            descriptor_preview,
                            preset.get('preview'),
                        ),
                        'action': dict(component['action']),
                    })
                    component['preset_keys'].append(preset_key)
                    presets.append(item)
            components.append(component)

        presets.sort(
            key=lambda item: (
                str(item.get('name') or item.get('preset_id') or '').casefold(),
                str(item.get('key') or ''),
            )
        )
        provider_totals: Dict[str, int] = {}
        for component in components:
            provider = str(component.get('provider') or 'unknown')
            provider_totals[provider] = provider_totals.get(provider, 0) + 1
        return {
            'schema': 'ledgrid.studio-next-catalog',
            'schema_version': 1,
            'components': components,
            'presets': presets,
            'totals': {
                'components': len(components),
                'presets': len(presets),
                'presets_withheld': withheld_presets,
                'components_by_provider': provider_totals,
                'provider_collisions': len(collisions),
            },
            'diagnostics': diagnostics,
        }

    def _browser_composer_bootstrap(self) -> Dict[str, Any]:
        """Build the complete read model needed after the app shell loads.

        Unlike the gallery summaries, composer presets include their authored
        parameter objects. Identities remain provider-qualified, and legacy
        preset storage is withheld when a plugin ID collides across providers.
        """
        raw_components = self._component_catalog()
        providers_by_id: Dict[str, set] = {}
        for component in raw_components:
            plugin_id = component.get('plugin_id')
            provider = component.get('provider')
            if isinstance(plugin_id, str) and isinstance(provider, str):
                providers_by_id.setdefault(plugin_id, set()).add(provider)
        collisions = {
            plugin_id: sorted(providers)
            for plugin_id, providers in providers_by_id.items()
            if len(providers) > 1
        }

        components: List[Dict[str, Any]] = []
        for raw in sorted(
            raw_components,
            key=lambda item: (
                str(item.get('name') or item.get('plugin_id') or '').casefold(),
                str(item.get('provider') or ''),
            ),
        ):
            plugin_id = raw.get('plugin_id')
            provider = raw.get('provider')
            if not isinstance(plugin_id, str) or not isinstance(provider, str):
                continue

            schema = raw.get('parameter_schema')
            schema = json.loads(json.dumps(schema)) if isinstance(schema, dict) else {}
            declared_defaults = raw.get('defaults')
            defaults = (
                json.loads(json.dumps(declared_defaults))
                if isinstance(declared_defaults, dict)
                else {
                    name: definition.get('default')
                    for name, definition in schema.items()
                    if isinstance(definition, dict) and 'default' in definition
                }
            )
            entrypoint = str(raw.get('entrypoint') or '')
            class_name = (
                entrypoint.rsplit(':', 1)[-1]
                if provider == 'python' and ':' in entrypoint
                else None
            )

            python_entrypoint_ready = bool(re.fullmatch(
                r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*',
                entrypoint,
            ))
            compatibility = (
                raw.get('compatibility')
                if isinstance(raw.get('compatibility'), dict)
                else {}
            )
            is_compatibility_tool = (
                compatibility.get('classification') == 'painter'
            )

            if provider == 'python' and is_compatibility_tool:
                runtime = {
                    'kind': 'python',
                    'supported': False,
                    'reason': (
                        'Painter is a separate compatibility editor, not a '
                        'bundled Python animation runtime.'
                    ),
                }
            elif provider == 'python' and python_entrypoint_ready:
                runtime = {
                    'kind': 'python',
                    'supported': True,
                    'engine': 'python-pyodide-wasm',
                    'worker_url': '/static/js/composer_python_worker.js',
                    'asset_url': (
                        '/static/generated/composer/ledgrid_python_runtime.zip'
                    ),
                }
            elif (
                provider == 'receiver_native'
                and plugin_id in BROWSER_NATIVE_COMPONENTS
            ):
                runtime = {
                    'kind': 'native',
                    'supported': True,
                    'engine': 'receiver-native-cpp-wasm',
                    'worker_url': '/static/js/composer_native_worker.js',
                    'asset_url': (
                        '/static/generated/composer/'
                        f'{BROWSER_NATIVE_COMPONENT_ASSETS[plugin_id]}'
                    ),
                }
            else:
                runtime = {
                    'kind': 'python' if provider == 'python' else 'native',
                    'supported': False,
                    'reason': (
                        'This component does not expose a verified browser-Wasm '
                        'entrypoint. Its generated preview remains available as a fallback.'
                    ),
                }

            preset_records: List[Dict[str, Any]] = []
            if plugin_id not in collisions:
                for summary in self._list_animation_presets(plugin_id):
                    preset_id = summary.get('preset_id')
                    if not isinstance(preset_id, str):
                        continue
                    payload = self._load_animation_preset(plugin_id, preset_id)
                    if payload is None:
                        continue
                    preset = json.loads(json.dumps(summary))
                    preset.update({
                        'key': f'{provider}:{plugin_id}:{preset_id}',
                        'component_key': f'{provider}:{plugin_id}',
                        'provider': provider,
                        'plugin_id': plugin_id,
                        'params': json.loads(json.dumps(payload['params'])),
                        'preset_fingerprint': self._component_preset_fingerprint(payload),
                    })
                    preset_records.append(preset)

            components.append({
                'key': f'{provider}:{plugin_id}',
                'provider': provider,
                'plugin_id': plugin_id,
                'class_name': class_name,
                'name': str(raw.get('name') or plugin_id.replace('_', ' ').title()),
                'description': str(raw.get('description') or ''),
                'role': str(raw.get('role') or 'background'),
                'icon': str(raw.get('icon') or '✦'),
                'parameter_schema': schema,
                'defaults': defaults,
                'presets': preset_records,
                'browser_runtime': runtime,
                'provider_collision': plugin_id in collisions,
                'scene_compatibility': json.loads(json.dumps(
                    raw.get('scene_compatibility') or {}
                )),
                'compatibility': json.loads(json.dumps(
                    raw.get('compatibility') or {}
                )),
                'availability': json.loads(json.dumps(
                    raw.get('availability') or {}
                )),
                'build': json.loads(json.dumps(raw.get('build') or {})),
                'preview': self._studio_next_preview(
                    provider,
                    raw.get('preview'),
                    None if plugin_id in collisions else self._preview_metadata(plugin_id),
                ),
            })

        controller = self.preview_manager.controller
        strip_count = int(controller.strip_count)
        leds_per_strip = int(controller.leds_per_strip)
        return {
            'schema': 'ledgrid.browser-composer-bootstrap',
            'schema_version': 1,
            'generated_at': time.time(),
            'geometry': {
                'strip_count': strip_count,
                'leds_per_strip': leds_per_strip,
                'total_leds': strip_count * leds_per_strip,
            },
            'components': components,
            'capabilities': {
                'rendering': 'browser_webassembly',
                'draft_storage': 'browser_local_storage',
                'checker': 'browser_worker',
                'live_wall_mutated': False,
                'framebuffer_readback': False,
                'server_actions': {
                    'connectivity_url': '/api/v1/composer/connectivity',
                    'validate_import_url': '/api/v1/composer/presets/validate',
                    'save_component_preset_url': '/api/v1/composer/presets',
                    'save_scene_preset_url': '/api/v1/scene-presets',
                    'validate_scene_url': '/api/v1/scene/validate',
                    'activate_scene_url': '/api/v1/scene',
                    'online_required': True,
                },
            },
            'diagnostics': [
                {
                    'code': 'provider_collision',
                    'plugin_id': plugin_id,
                    'providers': providers,
                    'message': (
                        'Presets are withheld because legacy storage is not '
                        'provider-qualified.'
                    ),
                }
                for plugin_id, providers in sorted(collisions.items())
            ],
        }

    def _browser_composer_component(
        self,
        *,
        component_key: Optional[str] = None,
        plugin_id: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve one exact component identity, rejecting provider ambiguity."""
        if component_key is not None:
            if not isinstance(component_key, str) or ':' not in component_key:
                raise ValueError('component_key must be provider:plugin_id')
            key_provider, key_plugin_id = component_key.split(':', 1)
            if provider is not None and provider != key_provider:
                raise ValueError('component provider does not match component_key')
            if plugin_id is not None and plugin_id != key_plugin_id:
                raise ValueError('component plugin_id does not match component_key')
            provider, plugin_id = key_provider, key_plugin_id
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ValueError('component plugin_id is required')

        matches = [
            item for item in self._component_catalog()
            if item.get('plugin_id') == plugin_id
            and (provider is None or item.get('provider') == provider)
        ]
        if provider is None and len(matches) > 1:
            raise ValueError(
                f'Component {plugin_id} exists under multiple providers; '
                'use a provider-qualified identity'
            )
        if len(matches) != 1:
            identity = f'{provider}:{plugin_id}' if provider else plugin_id
            raise ValueError(f'Unknown component: {identity}')
        return matches[0]

    def _validated_browser_composer_import(
        self, payload: Any
    ) -> Dict[str, Any]:
        """Normalize an uploaded preset into a composer draft without writes."""
        if not isinstance(payload, dict):
            raise ValueError('uploaded preset must be a JSON object')

        if payload.get('schema') == SCENE_PRESET_SCHEMA:
            if payload.get('schema_version') != SCENE_PRESET_VERSION:
                raise ValueError('unsupported scene preset schema version')
            scene = self._validated_scene_request(payload.get('scene'))
            background = scene['background']
            descriptor = self._browser_composer_component(
                plugin_id=background['plugin_id'],
                provider=background['provider'],
            )
            params = dict(descriptor.get('defaults') or {})
            params.update(background.get('resolved_parameters') or {})
            params.update(background.get('parameter_overrides') or {})
            return {
                'kind': 'scene_preset',
                'draft': {
                    'component_key': (
                        f"{background['provider']}:{background['plugin_id']}"
                    ),
                    'name': str(payload.get('name') or 'Imported scene'),
                    'description': str(payload.get('description') or ''),
                    'params': params,
                    'scene': scene,
                },
            }

        if payload.get('schema') == 'ledgrid.scene-state' or 'scene' in payload:
            raise ValueError(
                'upload a ledgrid.scene-preset document, not a raw scene envelope'
            )
        params = payload.get('params')
        if not isinstance(params, dict):
            raise ValueError('component preset params must be an object')
        descriptor = self._browser_composer_component(
            component_key=payload.get('component_key'),
            plugin_id=payload.get('plugin_id') or payload.get('animation'),
            provider=payload.get('provider'),
        )
        plugin_id = descriptor['plugin_id']
        provider = descriptor['provider']
        error = self._validate_animation_params(plugin_id, params)
        if error:
            raise ValueError(error)
        return {
            'kind': 'component_preset',
            'draft': {
                'component_key': f'{provider}:{plugin_id}',
                'name': str(payload.get('name') or 'Imported preset'),
                'description': str(payload.get('description') or ''),
                'params': json.loads(json.dumps(params)),
            },
        }

    def _save_browser_composer_preset(
        self, payload: Any
    ) -> tuple[Dict[str, Any], bool]:
        """Persist an exact component preset without issuing a live command."""
        if not isinstance(payload, dict):
            raise ValueError('request body must be a JSON object')
        if (
            payload.get('schema') != 'ledgrid.browser-composer-save'
            or payload.get('schema_version') != 1
        ):
            raise ValueError('unsupported browser composer save schema')
        descriptor = self._browser_composer_component(
            component_key=payload.get('component_key')
        )
        plugin_id = descriptor['plugin_id']
        provider = descriptor['provider']
        provider_count = sum(
            item.get('plugin_id') == plugin_id
            for item in self._component_catalog()
        )
        if provider_count > 1:
            raise ValueError(
                'This plugin ID exists under multiple providers; legacy preset '
                'storage cannot save it safely'
            )

        name = str(payload.get('name') or '').strip()
        if not name:
            raise ValueError('preset name is required')
        if len(name) > 120:
            raise ValueError('preset name must be 120 characters or fewer')
        preset_id = self._sanitize_preset_id(name)
        if not preset_id:
            raise ValueError('preset name must contain letters or numbers')
        if not preset_id[0].isalpha():
            preset_id = f'preset_{preset_id}'[:64]
        params = payload.get('params')
        if not isinstance(params, dict):
            raise ValueError('preset params must be an object')
        error = self._validate_animation_params(plugin_id, params)
        if error:
            raise ValueError(error)
        overwrite = payload.get('overwrite', False)
        if not isinstance(overwrite, bool):
            raise ValueError('overwrite must be a boolean')

        existing = self._load_animation_preset(plugin_id, preset_id)
        if existing is not None and not overwrite:
            raise FileExistsError(preset_id)
        now = time.time()
        preset = {
            'version': 2,
            'preset_id': preset_id,
            'name': name,
            'animation': plugin_id,
            'provider': provider,
            'description': str(payload.get('description') or ''),
            'params': json.loads(json.dumps(params)),
            'created_at': existing.get('created_at', now) if existing else now,
            'updated_at': now,
        }
        self._write_animation_preset(plugin_id, preset_id, preset)
        preset['component_key'] = f'{provider}:{plugin_id}'
        return ({
            'preset': preset,
            'preset_fingerprint': self._component_preset_fingerprint(preset),
        }, existing is None)

    def _scene_provider_policy(self) -> SceneProviderPolicy:
        """Resolve the manager's explicit rollout policy, failing safely off."""
        getter = getattr(self.preview_manager, 'scene_provider_policy', None)
        if callable(getter):
            try:
                policy = getter()
            except (TypeError, ValueError):
                policy = None
            if isinstance(policy, SceneProviderPolicy):
                if not policy.compiled_rainbow_enabled:
                    return policy
                flags = getattr(self.preview_manager, 'feature_flags', None)
                if (
                    isinstance(flags, AnimationPipelineFeatureFlags)
                    and flags.receiver_local_background
                    and flags.receiver_sparse_overlay
                ):
                    return policy
                # Receiver execution requires typed rollout flags and the
                # manager's narrower product policy to agree.
                return DEFAULT_SCENE_PROVIDER_POLICY
        return DEFAULT_SCENE_PROVIDER_POLICY

    def _validated_scene_request(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise SceneValidationError('request body must contain a scene object')
        catalog = self._component_catalog()
        scene = normalize_scene_payload(
            payload,
            catalog=catalog,
            provider_policy=self._scene_provider_policy(),
        )
        descriptors = {
            (item.get('provider'), item.get('plugin_id')): item
            for item in catalog
            if isinstance(item.get('provider'), str)
            and isinstance(item.get('plugin_id'), str)
        }
        components = [scene['background'], scene['known_python_fallback']]
        components.extend(overlay['component'] for overlay in scene['overlays'])
        for component in components:
            component_id = component['plugin_id']
            provider = component['provider']
            descriptor = descriptors.get((provider, component_id))
            if descriptor is None:
                raise SceneValidationError(
                    f'Provider-qualified component {provider}:{component_id} does not exist'
                )
            if provider == 'python':
                getter = getattr(self.preview_manager, 'get_animation_info', None)
                try:
                    loaded = getter(component_id) if callable(getter) else None
                except (KeyError, TypeError, ValueError):
                    loaded = None
                if not loaded:
                    raise SceneValidationError(
                        f'Host Python implementation {component_id} is not loaded'
                    )
            for field in ('parameter_overrides', 'resolved_parameters'):
                params = component.get(field) or {}
                error = self._validate_animation_params(component_id, params)
                if error:
                    raise SceneValidationError(error)
            preset_id = component.get('preset_id')
            if preset_id is not None:
                preset = self._load_animation_preset(component_id, preset_id)
                if preset is None or preset.get('animation') != component_id:
                    raise SceneValidationError(
                        f"Component preset {component_id}/{preset_id} does not exist"
                    )
        return scene

    def _validated_studio_next_scene_request(
        self, payload: Any
    ) -> Dict[str, Any]:
        """Enforce Studio Next's ready Host background plus fixed-clock slice."""
        scene = self._validated_scene_request(payload)
        catalog = self._component_catalog()
        descriptors = {
            (item.get('provider'), item.get('plugin_id')): item
            for item in catalog
            if isinstance(item.get('provider'), str)
            and isinstance(item.get('plugin_id'), str)
        }
        background = scene['background']
        descriptor = descriptors.get(
            (background.get('provider'), background.get('plugin_id'))
        )
        if descriptor is None:
            raise SceneValidationError('Studio Next background is not in the catalog')
        action = self._studio_next_look_action(descriptor)
        if not action['take_look_enabled']:
            raise SceneValidationError(
                f"Studio Next background is unavailable: {action['reason']}"
            )
        fallback = scene['known_python_fallback']
        if (
            fallback.get('provider') != background.get('provider')
            or fallback.get('plugin_id') != background.get('plugin_id')
        ):
            raise SceneValidationError(
                'Studio Next requires the known Python fallback to match its Host background'
            )
        overlays = scene['overlays']
        if len(overlays) > 1:
            raise SceneValidationError('Studio Next supports at most one clock overlay')
        if overlays:
            overlay = overlays[0]
            component = overlay['component']
            overlay_descriptor = descriptors.get(
                (component.get('provider'), component.get('plugin_id'))
            )
            readiness_values = {
                str(overlay_descriptor.get(field) or '')
                .strip().casefold().replace('-', '_')
                for field in ('status', 'availability', 'readiness')
            } if overlay_descriptor is not None else set()
            blocked_readiness = readiness_values & {
                'build_only', 'unavailable', 'quarantined', 'disabled', 'error',
            }
            if (
                overlay.get('slot_id') != 'clock_overlay'
                or component.get('provider') != 'python'
                or component.get('plugin_id') != 'clock_overlay'
                or overlay_descriptor is None
                or overlay_descriptor.get('role') != 'overlay'
                or overlay_descriptor.get('gallery') != 'show'
                or overlay_descriptor.get('is_test') is True
                or blocked_readiness
            ):
                raise SceneValidationError(
                    'Studio Next supports only the ready Host Python clock_overlay slot'
                )
        return scene

    def _scene_preset_diagnostics(
        self, scene: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Report preset drift while preserving the saved canonical snapshot."""
        if not isinstance(scene, dict):
            return []
        diagnostics = []
        components = [('background', scene.get('background'))]
        components.extend(
            (overlay.get('slot_id', 'overlay'), overlay.get('component'))
            for overlay in scene.get('overlays', [])
            if isinstance(overlay, dict)
        )
        for slot, component in components:
            if not isinstance(component, dict) or not component.get('preset_id'):
                continue
            component_id = component.get('plugin_id')
            preset_id = component.get('preset_id')
            preset = self._load_animation_preset(component_id, preset_id)
            expected = self._component_preset_fingerprint(preset) if preset else None
            actual = component.get('preset_fingerprint')
            dirty = preset is None or expected != actual or bool(component.get('parameter_overrides'))
            diagnostics.append({
                'slot': slot,
                'component_id': component_id,
                'preset_id': preset_id,
                'is_dirty': dirty,
                'code': (
                    'preset_missing' if preset is None
                    else 'preset_drift' if expected != actual
                    else 'live_overrides' if component.get('parameter_overrides')
                    else 'preset_match'
                ),
                'message': (
                    'Stored canonical parameters will be used; the selected preset changed.'
                    if dirty else 'Selected preset matches the stored canonical snapshot.'
                ),
            })
        return diagnostics

    @staticmethod
    def _component_preset_fingerprint(preset: Dict[str, Any]) -> str:
        from animation.core.presentation_contracts import component_preset_fingerprint

        return component_preset_fingerprint(
            preset.get('animation'), preset.get('preset_id'), preset.get('params') or {}
        )

    def _current_scene_payload(
        self, status: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        status = status if isinstance(status, dict) else self._status_payload()
        raw_scene = status.get('scene_state')
        if not isinstance(raw_scene, dict) or not raw_scene.get('schema'):
            raw_scene = status.get('scene')
        if isinstance(raw_scene, dict) and raw_scene.get('schema'):
            try:
                return normalize_scene_payload(
                    raw_scene,
                    catalog=self._component_catalog(),
                    provider_policy=self._scene_provider_policy(),
                )
            except SceneValidationError:
                pass
        animation = status.get('current_animation')
        if not status.get('is_running') or not isinstance(animation, str):
            return None
        info = status.get('animation_info') or {}
        params = info.get('current_params') if isinstance(info, dict) else {}
        params = params if isinstance(params, dict) else {}
        preset = status.get('current_preset') or {}
        preset_id = preset.get('preset_id') if isinstance(preset, dict) else None
        fingerprint = None
        if isinstance(preset_id, str):
            stored = self._load_animation_preset(animation, preset_id)
            if stored is not None:
                fingerprint = self._component_preset_fingerprint(stored)
        return background_only_scene(
            animation, params,
            preset_id=preset_id if fingerprint else None,
            preset_fingerprint=fingerprint,
        )

    def _validated_scene_update(self, target: str, value: Any) -> Dict[str, Any]:
        if target not in {'background', FIXED_OVERLAY_SLOT}:
            raise SceneValidationError('scene target must be background or clock_overlay')
        update = value if isinstance(value, dict) else None
        if update is None:
            raise SceneValidationError('scene update must be a JSON object')
        allowed = (
            {'component', 'params', 'parameter_overrides'}
            if target == 'background'
            else {
                'component', 'params', 'parameter_overrides', 'enabled', 'remove',
                'opacity', 'placement', 'stale_policy',
            }
        )
        unknown = sorted(set(update) - allowed)
        if unknown:
            raise SceneValidationError(
                f"unsupported scene update fields: {', '.join(unknown)}"
            )
        if 'remove' in update and not isinstance(update['remove'], bool):
            raise SceneValidationError('remove must be boolean')
        if update.get('remove') and len(update) != 1:
            raise SceneValidationError('remove cannot be combined with other scene updates')

        scene = self._current_scene_payload()
        if scene is None:
            raise SceneValidationError('no live scene is available for a targeted update')
        candidate = json.loads(json.dumps(scene))
        if target == 'background':
            component = candidate['background']
            if 'component' in update:
                raise SceneValidationError(
                    'replace a background by applying a complete scene'
                )
            params = update.get('params', update.get('parameter_overrides'))
            if params is not None:
                if not isinstance(params, dict):
                    raise SceneValidationError('scene component params must be an object')
                component['parameter_overrides'] = dict(params)
        else:
            overlays = candidate['overlays']
            if update.get('remove'):
                return {'remove': True}
            if not overlays:
                if 'component' not in update:
                    raise SceneValidationError('adding the clock overlay requires component')
                overlays.append({
                    'slot_id': FIXED_OVERLAY_SLOT,
                    'component': update['component'],
                    'enabled': update.get('enabled', True),
                    'opacity': update.get('opacity', 255),
                    'placement': update.get('placement', {}),
                    'stale_policy': update.get('stale_policy', {'policy': 'hold'}),
                })
            else:
                overlay = overlays[0]
                for field in ('component', 'enabled', 'opacity', 'placement', 'stale_policy'):
                    if field in update:
                        overlay[field] = update[field]
                params = update.get('params', update.get('parameter_overrides'))
                if params is not None:
                    if not isinstance(params, dict):
                        raise SceneValidationError('scene component params must be an object')
                    overlay['component']['parameter_overrides'] = dict(params)

        normalized = self._validated_scene_request(candidate)
        if target == 'background':
            result: Dict[str, Any] = {'component': normalized['background']}
            if 'params' in update or 'parameter_overrides' in update:
                result['params'] = normalized['background']['parameter_overrides']
            return result
        overlay = normalized['overlays'][0]
        result = {
            key: overlay[key]
            for key in ('component', 'enabled', 'opacity', 'placement', 'stale_policy')
            if key in update or key == 'component'
        }
        if 'params' in update or 'parameter_overrides' in update:
            result['params'] = overlay['component']['parameter_overrides']
        return result

    def _scene_preview(
        self, scene: Dict[str, Any], vibe: Dict[str, Any],
        plant_modifiers: Dict[str, Any], elapsed: Any,
    ) -> Dict[str, Any]:
        try:
            elapsed_value = float(elapsed)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SceneValidationError('preview elapsed must be numeric') from exc
        if not math.isfinite(elapsed_value) or elapsed_value < 0:
            raise SceneValidationError('preview elapsed must be finite and non-negative')
        renderer = self.preview_manager.get_scene_preview
        parameters = inspect.signature(renderer).parameters
        first = next(iter(parameters), '')
        if first in {'scene', 'scene_payload', 'scene_state'}:
            kwargs: Dict[str, Any] = {'vibe': vibe, 'elapsed': elapsed_value}
            if 'plant_modifiers' in parameters:
                kwargs['plant_modifiers'] = plant_modifiers
            return renderer(scene, **kwargs)

        background = scene['background']
        overlays = scene['overlays']
        with self._scene_preview_lock:
            previous = getattr(self.preview_manager, 'plant_modifier_state', None)
            previous_payload = previous.to_dict() if hasattr(previous, 'to_dict') else None
            setter = getattr(self.preview_manager, 'set_plant_modifiers', None)
            if callable(setter):
                setter(plant_modifiers)
            try:
                if not overlays:
                    return self.preview_manager.get_animation_preview_with_params(
                        background['plugin_id'],
                        {**background['resolved_parameters'], **background['parameter_overrides']},
                        vibe=vibe,
                    )
                overlay = overlays[0]
                placement = overlay['placement']
                return renderer(
                    background['plugin_id'],
                    {**background['resolved_parameters'], **background['parameter_overrides']},
                    overlay['component']['plugin_id'],
                    {
                        **overlay['component']['resolved_parameters'],
                        **overlay['component']['parameter_overrides'],
                    },
                    overlay['opacity'],
                    placement['strip_translation'], placement['led_translation'],
                    vibe=vibe, elapsed=elapsed_value,
                )
            finally:
                if callable(setter) and previous_payload is not None:
                    setter(previous_payload)

    def _scene_preset_path(self, preset_id: str) -> Optional[Path]:
        safe_id = self._sanitize_preset_id(preset_id)
        if not safe_id or safe_id != preset_id:
            return None
        return self.scene_presets_dir / f'{safe_id}.json'

    def _load_scene_preset(self, preset_id: str) -> Optional[Dict[str, Any]]:
        path = self._scene_preset_path(preset_id)
        if path is None:
            return None
        payload = self._read_json_file(path)
        if not isinstance(payload, dict):
            return None
        if (
            payload.get('schema') != SCENE_PRESET_SCHEMA
            or payload.get('schema_version') != SCENE_PRESET_VERSION
            or payload.get('preset_id') != preset_id
            or any(key in payload for key in ('vibe', 'plant_modifiers', 'output'))
        ):
            return None
        return payload

    def _list_scene_presets(self) -> List[Dict[str, Any]]:
        presets = []
        if not self.scene_presets_dir.is_dir():
            return presets
        for path in sorted(self.scene_presets_dir.glob('*.json')):
            payload = self._load_scene_preset(path.stem)
            if payload is not None:
                presets.append(payload)
        return sorted(
            presets,
            key=lambda item: str(item.get('name') or item.get('preset_id')).casefold(),
        )

    def _write_scene_preset(self, preset_id: str, payload: Dict[str, Any]) -> None:
        path = self._scene_preset_path(preset_id)
        if path is None:
            raise ValueError('Invalid scene preset id')
        self._atomic_write_json(path, payload)

    @staticmethod
    def _preset_emoji(preset: Dict[str, Any], fallback: str) -> str:
        """Choose a discoverable icon from curated preset language."""
        text = ' '.join([
            str(preset.get('name', '')),
            str(preset.get('category', '')),
            ' '.join(map(str, preset.get('tags') or [])),
        ]).lower()
        choices = (
            (('ice', 'crystal', 'frost'), '❄️'),
            (('fire', 'ember', 'solar', 'gold'), '🔥'),
            (('ocean', 'tide', 'water'), '🌊'),
            (('space', 'star', 'galaxy'), '🌌'),
            (('earth', 'garden', 'orchard'), '🌍'),
            (('neon', 'synthwave', 'arcade'), '🎆'),
            (('quiet', 'calm'), '🌙'),
            (('chaos', 'storm', 'finale'), '⚡'),
        )
        return next((emoji for terms, emoji in choices if any(term in text for term in terms)), fallback)

    @staticmethod
    def _preset_swatches(preset: Dict[str, Any]) -> List[str]:
        """Extract up to three representative colors from preset parameters."""
        palette = preset.get('palette')
        if isinstance(palette, dict) and isinstance(palette.get('colors'), list):
            colors = [
                color.upper() for color in palette['colors']
                if isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color)
            ]
            if colors:
                return colors[:3]

        params = preset.get('params') or {}
        colors = []
        for red_name, red_value in params.items():
            if not red_name.endswith('red'):
                continue
            prefix = red_name[:-3]
            green_name, blue_name = f'{prefix}green', f'{prefix}blue'
            if green_name not in params or blue_name not in params:
                continue
            try:
                channels = [int(red_value), int(params[green_name]), int(params[blue_name])]
            except (TypeError, ValueError):
                continue
            if all(0 <= channel <= 255 for channel in channels):
                colors.append('#' + ''.join(f'{channel:02X}' for channel in channels))
        return colors[:3]

    def _validate_animation_params(
        self, animation_name: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """Validate runtime preset parameters against the plugin schema."""
        info = self.preview_manager.get_animation_info(animation_name)
        if not info:
            info = next((
                item for item in self._component_catalog()
                if item.get('plugin_id') == animation_name
            ), None)
        if not info:
            return f"Unknown animation: {animation_name}"
        schema = info.get('parameters', info.get('parameter_schema'))
        if not isinstance(schema, dict):
            return f"Animation schema is unavailable: {animation_name}"

        expected_types = {
            'bool': lambda value: isinstance(value, bool),
            'int': lambda value: isinstance(value, int) and not isinstance(value, bool),
            'float': lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            'str': lambda value: isinstance(value, str),
        }
        for name, value in params.items():
            definition = schema.get(name)
            if not isinstance(definition, dict):
                return f"Unsupported parameter for {animation_name}: {name}"
            type_name = definition.get('type')
            validator = expected_types.get(type_name)
            if validator and not validator(value):
                return f"Parameter {name} must be {type_name}"
            if 'options' in definition and value not in definition['options']:
                return f"Parameter {name} must be one of {definition['options']}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    finite = math.isfinite(float(value))
                except OverflowError:
                    finite = False
                if not finite:
                    return f"Parameter {name} must be finite"
                if 'min' in definition and value < definition['min']:
                    return f"Parameter {name} must be at least {definition['min']}"
                if 'max' in definition and value > definition['max']:
                    return f"Parameter {name} must be at most {definition['max']}"
        return None
    
    def run(self, debug=False):
        """Start the web server"""
        print(f"🌐 Starting web interface at http://{self.host}:{self.port}")
        print(f"   Dashboard: http://{self.host}:{self.port}/")
        print(f"   Control:   http://{self.host}:{self.port}/control")
        print(f"   Painter:   http://{self.host}:{self.port}/painter")
        print(f"   Emoji:     http://{self.host}:{self.port}/emoji")

        self.app.run(host=self.host, port=self.port, debug=debug, threaded=True)

    def _fallback_led_info(self) -> Dict[str, int]:
        """Current preview-manager dimensions used as a fallback layout."""
        return {
            'total_leds': self.preview_manager.controller.total_leds,
            'strip_count': self.preview_manager.controller.strip_count,
            'leds_per_strip': self.preview_manager.controller.leds_per_strip,
        }

    @staticmethod
    def _coerce_positive_int(value: Any, fallback: int) -> int:
        """Parse positive integers from untrusted payloads."""
        try:
            parsed = int(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        return fallback

    def _normalize_led_info(self, led_info: Any) -> Dict[str, int]:
        """Normalize LED layout payloads into a validated shape."""
        fallback = self._fallback_led_info()
        if not isinstance(led_info, dict):
            return fallback

        strip_count = self._coerce_positive_int(led_info.get('strip_count'), fallback['strip_count'])
        leds_per_strip = self._coerce_positive_int(led_info.get('leds_per_strip'), fallback['leds_per_strip'])
        return {
            'strip_count': strip_count,
            'leds_per_strip': leds_per_strip,
            'total_leds': strip_count * leds_per_strip,
        }

    @staticmethod
    def _coerce_byte(value: Any) -> int:
        """Clamp any input to an 8-bit channel value."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 0
        return max(0, min(255, parsed))

    def _normalize_frame_data(self, frame_data: Any, led_info: Optional[Dict[str, int]] = None) -> Optional[List[List[int]]]:
        """Normalize incoming frame payloads to a fixed-length RGB list."""
        if not isinstance(frame_data, list):
            return None

        layout = self._normalize_led_info(led_info)
        total_leds = layout['total_leds']
        normalized: List[List[int]] = []

        for pixel in frame_data[:total_leds]:
            if isinstance(pixel, (list, tuple)) and len(pixel) >= 3:
                normalized.append([
                    self._coerce_byte(pixel[0]),
                    self._coerce_byte(pixel[1]),
                    self._coerce_byte(pixel[2]),
                ])
            else:
                normalized.append([0, 0, 0])

        if len(normalized) < total_leds:
            normalized.extend([[0, 0, 0] for _ in range(total_leds - len(normalized))])

        return normalized

    def _extract_normalized_frame(self, payload: Dict[str, Any], led_info: Optional[Dict[str, int]] = None) -> Optional[List[List[int]]]:
        """Read either raw or encoded frame payloads and normalize the result."""
        if not isinstance(payload, dict):
            return None

        frame_data = payload.get('frame_data')
        normalized = self._normalize_frame_data(frame_data, led_info=led_info)
        if normalized is not None:
            return normalized

        encoded = payload.get('frame_data_encoded')
        if isinstance(encoded, str) and encoded:
            decoded = decode_frame_data(encoded)
            return self._normalize_frame_data(decoded, led_info=led_info)

        return None

    @staticmethod
    def _sanitize_preset_id(raw_name: str) -> str:
        """Convert user-provided preset names to a filesystem-safe id."""
        cleaned = re.sub(r'[^a-zA-Z0-9_-]+', '_', (raw_name or '').strip().lower())
        cleaned = re.sub(r'_+', '_', cleaned).strip('_')
        return cleaned[:64]

    def _preset_path(self, preset_id: str) -> Optional[Path]:
        """Resolve a preset id to a file path in the painter preset directory."""
        safe_id = self._sanitize_preset_id(preset_id)
        if not safe_id:
            return None
        return self.painter_presets_dir / f"{safe_id}.json"

    def _read_json_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a JSON object from disk."""
        try:
            raw = path.read_text(encoding='utf-8')
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _preset_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return a concise summary shape for preset list responses."""
        return {
            'preset_id': payload.get('preset_id'),
            'name': payload.get('name'),
            'updated_at': payload.get('updated_at'),
            'created_at': payload.get('created_at'),
            'led_info': self._normalize_led_info(payload.get('led_info')),
            'frame_data_length': self._coerce_positive_int(payload.get('frame_data_length'), 0),
        }

    def _list_painter_presets(self) -> List[Dict[str, Any]]:
        """Read and summarize all painter presets from disk."""
        summaries: List[Dict[str, Any]] = []
        for path in sorted(self.painter_presets_dir.glob('*.json')):
            payload = self._read_json_file(path)
            if not payload:
                continue
            payload.setdefault('preset_id', path.stem)
            payload.setdefault('name', path.stem)
            summaries.append(self._preset_summary(payload))
        summaries.sort(
            key=lambda preset: str(preset.get('name') or preset.get('preset_id') or '').casefold()
        )
        return summaries

    def _load_painter_preset(self, preset_id: str) -> Optional[Dict[str, Any]]:
        """Load and decode a painter preset for editing."""
        path = self._preset_path(preset_id)
        if path is None or not path.exists():
            return None

        payload = self._read_json_file(path)
        if not payload:
            return None

        payload.setdefault('preset_id', path.stem)
        payload.setdefault('name', path.stem)
        led_info = self._normalize_led_info(payload.get('led_info'))
        frame_data = self._extract_normalized_frame(payload, led_info=led_info)
        if frame_data is None:
            frame_data = [[0, 0, 0] for _ in range(led_info['total_leds'])]

        return {
            **payload,
            'led_info': led_info,
            'frame_data': frame_data,
            'frame_data_length': len(frame_data),
            'frame_encoding': FRAME_ENCODING_NAME,
        }

    def _write_painter_preset(self, preset_id: str, payload: Dict[str, Any]):
        """Persist a painter preset atomically."""
        path = self._preset_path(preset_id)
        if path is None:
            raise ValueError("Invalid preset id")

        tmp_path = path.with_suffix('.json.tmp')
        tmp_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        tmp_path.replace(path)

    @staticmethod
    def _mask_geometry(payload: Dict[str, Any]) -> Optional[Dict[str, int]]:
        """Read a complete, internally consistent strip geometry."""
        geometry = payload.get('geometry') if isinstance(payload, dict) else None
        if not isinstance(geometry, dict):
            return None
        try:
            strip_count = int(geometry.get('strip_count'))
            leds_per_strip = int(geometry.get('leds_per_strip'))
            total_leds = int(geometry.get('total_leds'))
        except (TypeError, ValueError):
            return None
        if strip_count <= 0 or leds_per_strip <= 0 or total_leds != strip_count * leds_per_strip:
            return None
        return {
            'strip_count': strip_count,
            'leds_per_strip': leds_per_strip,
            'total_leds': total_leds,
        }

    @staticmethod
    def _mask_indices(payload: Dict[str, Any], keys: tuple, total_leds: int) -> List[int]:
        """Return sorted, unique in-range indices from the first supported key."""
        values: Any = []
        for key in keys:
            if isinstance(payload.get(key), list):
                values = payload[key]
                break
        indices = set()
        for value in values:
            if isinstance(value, bool):
                continue
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < total_leds:
                indices.add(index)
        return sorted(indices)

    def _load_painter_masks(self) -> Dict[str, Any]:
        """Load mask files as the painter's compact semantic state."""
        foliage_payload = self._read_json_file(self.foliage_mask_path)
        planter_payload = self._read_json_file(self.planter_mask_path)
        if foliage_payload is None:
            raise ValueError(f'Unable to read {self.foliage_mask_path.name}')
        if planter_payload is None:
            raise ValueError(f'Unable to read {self.planter_mask_path.name}')

        foliage_geometry = self._mask_geometry(foliage_payload)
        planter_geometry = self._mask_geometry(planter_payload)
        if foliage_geometry is None or planter_geometry is None:
            raise ValueError('Mask files must contain valid geometry')
        if foliage_geometry != planter_geometry:
            raise ValueError('Foliage and planter mask geometry do not match')

        total_leds = foliage_geometry['total_leds']
        planter = self._mask_indices(
            planter_payload, ('globe_indices', 'covered_indices'), total_leds
        )
        planter_set = set(planter)
        foliage = [
            index for index in self._mask_indices(
                foliage_payload, ('covered_indices', 'occluded_indices'), total_leds
            )
            if index not in planter_set
        ]
        updated_at = max(
            self.foliage_mask_path.stat().st_mtime,
            self.planter_mask_path.stat().st_mtime,
        )
        return {
            'version': 1,
            'led_info': foliage_geometry,
            'mask_types': [dict(mask_type) for mask_type in PAINTER_MASK_TYPES],
            'masks': {
                'foliage': foliage,
                'planter_bowls': planter,
            },
            'updated_at': updated_at,
        }

    @staticmethod
    def _validated_submitted_indices(
        values: Any, label: str, total_leds: int
    ) -> List[int]:
        if not isinstance(values, list):
            raise ValueError(f'masks.{label} must be an array')
        indices = set()
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f'masks.{label} must contain integer pixel indices')
            if value < 0 or value >= total_leds:
                raise ValueError(f'masks.{label} contains out-of-range pixel {value}')
            indices.add(value)
        return sorted(indices)

    @staticmethod
    def _nearest_planter_region(
        index: int, regions: List[Dict[str, Any]], leds_per_strip: int
    ) -> Optional[str]:
        """Assign newly painted bowl pixels to the nearest configured globe."""
        strip = index // leds_per_strip
        led = index % leds_per_strip
        candidates = []
        for position, region in enumerate(regions):
            try:
                name = str(region['id'])
                strip_start = int(region['strip_start'])
                led_start = int(region['led_start'])
                width = int(region['width'])
                height = int(region['height'])
            except (KeyError, TypeError, ValueError):
                continue
            inside = (
                strip_start <= strip < strip_start + width
                and led_start <= led < led_start + height
            )
            center_strip = strip_start + (width - 1) / 2.0
            center_led = led_start + (height - 1) / 2.0
            distance = (strip - center_strip) ** 2 + (led - center_led) ** 2
            candidates.append((0 if inside else 1, distance, position, name))
        return min(candidates)[3] if candidates else None

    @staticmethod
    def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
        """Write one JSON document durably before replacing its destination."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _save_painter_masks(self, submitted: Dict[str, Any]) -> Dict[str, Any]:
        """Update mask indices while preserving useful calibration metadata."""
        current = self._load_painter_masks()
        expected_geometry = current['led_info']
        submitted_geometry = self._normalize_led_info(submitted.get('led_info'))
        if submitted_geometry != expected_geometry:
            raise ValueError('Submitted geometry does not match the calibrated mask files')

        masks = submitted.get('masks')
        if not isinstance(masks, dict):
            raise ValueError('masks must be an object')
        total_leds = expected_geometry['total_leds']
        foliage = self._validated_submitted_indices(
            masks.get('foliage'), 'foliage', total_leds
        )
        planter = self._validated_submitted_indices(
            masks.get('planter_bowls'), 'planter_bowls', total_leds
        )
        overlap = set(foliage) & set(planter)
        if overlap:
            raise ValueError(f'Mask layers overlap at pixel {min(overlap)}')

        foliage_payload = self._read_json_file(self.foliage_mask_path)
        planter_payload = self._read_json_file(self.planter_mask_path)
        if foliage_payload is None or planter_payload is None:
            raise ValueError('Mask files changed or became unreadable before save')

        foliage_set = set(foliage)
        foliage_payload['covered_indices'] = foliage
        foliage_payload['occluded_indices'] = foliage
        foliage_payload['covered_count'] = len(foliage)
        foliage_payload['occluded_count'] = len(foliage)
        if isinstance(foliage_payload.get('pixels'), list):
            for pixel in foliage_payload['pixels']:
                if not isinstance(pixel, dict):
                    continue
                try:
                    pixel_index = int(pixel.get('index'))
                except (TypeError, ValueError):
                    continue
                pixel['occluded'] = pixel_index in foliage_set

        old_planter_pixels = {
            pixel.get('index'): pixel
            for pixel in planter_payload.get('pixels', [])
            if isinstance(pixel, dict) and isinstance(pixel.get('index'), int)
        }
        regions = planter_payload.get('regions')
        if not isinstance(regions, list):
            regions = []
        leds_per_strip = expected_geometry['leds_per_strip']
        rebuilt_pixels = []
        region_counts = {
            str(region.get('id')): 0
            for region in regions
            if isinstance(region, dict) and region.get('id')
        }
        for index in planter:
            old_pixel = old_planter_pixels.get(index)
            pixel = dict(old_pixel) if old_pixel is not None else {
                'index': index,
                'strip': index // leds_per_strip,
                'led': index % leds_per_strip,
            }
            region = pixel.get('region')
            if region not in region_counts:
                region = self._nearest_planter_region(index, regions, leds_per_strip)
                if region is not None:
                    pixel['region'] = region
            if region in region_counts:
                region_counts[region] += 1
            rebuilt_pixels.append(pixel)

        planter_payload['globe_indices'] = planter
        planter_payload['covered_indices'] = planter
        planter_payload['globe_count'] = len(planter)
        planter_payload['covered_count'] = len(planter)
        planter_payload['pixels'] = rebuilt_pixels
        planter_payload['region_count'] = len(regions)
        planter_payload['region_pixel_counts'] = region_counts

        edited_at = time.time()
        edit_metadata = {'tool': 'mask_painter', 'updated_at': edited_at}
        foliage_payload['manual_edit'] = edit_metadata
        planter_payload['manual_edit'] = edit_metadata

        self._atomic_write_json(self.foliage_mask_path, foliage_payload)
        self._atomic_write_json(self.planter_mask_path, planter_payload)
        return {
            'counts': {
                'foliage': len(foliage),
                'planter_bowls': len(planter),
            },
            'updated_at': edited_at,
        }

    def _animation_preset_dir(self, animation_name: str) -> Optional[Path]:
        """Resolve the writable runtime-preset directory for an animation."""
        safe_name = self._sanitize_preset_id(animation_name)
        if not safe_name or safe_name != animation_name:
            return None
        return self.animation_presets_dir / safe_name

    def _curated_animation_preset_dir(self, animation_name: str) -> Optional[Path]:
        """Resolve the read-only preset directory owned by a plugin package."""
        safe_name = self._sanitize_preset_id(animation_name)
        if not safe_name or safe_name != animation_name:
            return None
        loader = getattr(self.preview_manager, 'plugin_loader', None)
        if loader is None:
            return None
        component_dir_getter = getattr(loader, 'get_component_dir', None)
        component_dir = (
            component_dir_getter(animation_name)
            if callable(component_dir_getter)
            else loader.get_plugin_dir(animation_name)
        )
        if component_dir is None:
            return None
        return component_dir / 'presets'

    def _animation_preset_path(self, animation_name: str, preset_id: str) -> Optional[Path]:
        """Resolve an animation/preset pair without allowing path traversal."""
        preset_dir = self._animation_preset_dir(animation_name)
        safe_id = self._sanitize_preset_id(preset_id)
        if preset_dir is None or not safe_id:
            return None
        return preset_dir / f"{safe_id}.json"

    def _animation_preset_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary = {
            'version': payload.get('version', 1),
            'preset_id': payload.get('preset_id'),
            'name': payload.get('name'),
            'animation': payload.get('animation'),
            'created_at': payload.get('created_at'),
            'updated_at': payload.get('updated_at'),
            'category': payload.get('category'),
            'description': payload.get('description'),
            'tags': payload.get('tags', []),
            'palette': payload.get('palette'),
            'swatches': self._preset_swatches(payload),
        }
        animation_name = payload.get('animation')
        preset_id = payload.get('preset_id')
        if isinstance(animation_name, str) and isinstance(preset_id, str):
            summary['preview'] = self._preview_metadata(animation_name, preset_id)
        return summary

    @staticmethod
    def _animation_preset_selection(payload: Dict[str, Any]) -> Dict[str, str]:
        """Return the identity stored with the active animation runtime state."""
        return {
            'preset_id': str(payload.get('preset_id') or ''),
            'name': str(payload.get('name') or ''),
            'animation': str(payload.get('animation') or ''),
        }

    @staticmethod
    def _validated_interaction_payload(
        payload: Dict[str, Any]
    ) -> tuple[str, float, float, float]:
        if not isinstance(payload, dict):
            raise ValueError('interaction payload must be an object')
        kind = str(payload.get('kind') or 'primary')
        if 'x' not in payload or 'y' not in payload:
            raise ValueError('x and y are required')
        raw_values = (payload['x'], payload['y'], payload.get('strength', 1.0))
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_values):
            raise ValueError('x, y, and strength must be numeric')
        x, y, strength = map(float, raw_values)
        if not all(math.isfinite(value) for value in (x, y, strength)):
            raise ValueError('x, y, and strength must be finite')
        if not 0.0 <= strength <= 1.0:
            raise ValueError('strength must be between 0 and 1')
        return kind, x, y, strength

    @staticmethod
    def _validate_interaction_bounds(
        x: float, y: float, layout: Dict[str, int]
    ) -> None:
        width = int(layout['strip_count'])
        height = int(layout['leds_per_strip'])
        if not 0.0 <= x < width or not 0.0 <= y < height:
            raise ValueError('interaction coordinates are outside the animation grid')

    def _list_animation_presets(self, animation_name: str) -> List[Dict[str, Any]]:
        """List curated and runtime presets, with runtime files overriding IDs."""
        paths: Dict[str, Path] = {}
        curated_dir = self._curated_animation_preset_dir(animation_name)
        runtime_dir = self._animation_preset_dir(animation_name)
        for preset_dir in (curated_dir, runtime_dir):
            if preset_dir is not None and preset_dir.is_dir():
                paths.update({path.stem: path for path in sorted(preset_dir.glob('*.json'))})

        summaries: List[Dict[str, Any]] = []
        for path in paths.values():
            payload = self._read_json_file(path)
            if payload and payload.get('animation', animation_name) == animation_name:
                payload.setdefault('preset_id', path.stem)
                payload.setdefault('name', path.stem)
                summaries.append(self._animation_preset_summary(payload))
        summaries.sort(
            key=lambda preset: str(preset.get('name') or preset.get('preset_id') or '').casefold()
        )
        return summaries

    def _load_animation_preset(self, animation_name: str, preset_id: str) -> Optional[Dict[str, Any]]:
        """Read a runtime preset or fall back to its curated package preset."""
        path = self._animation_preset_path(animation_name, preset_id)
        if path is None:
            return None
        if not path.is_file():
            curated_dir = self._curated_animation_preset_dir(animation_name)
            path = curated_dir / path.name if curated_dir is not None else path
        if not path.is_file():
            return None
        payload = self._read_json_file(path)
        if not payload or not isinstance(payload.get('params'), dict):
            return None
        if payload.get('animation', animation_name) != animation_name:
            return None
        payload.setdefault('preset_id', path.stem)
        payload.setdefault('name', path.stem)
        payload.setdefault('animation', animation_name)
        return payload

    def _write_animation_preset(
        self, animation_name: str, preset_id: str, payload: Dict[str, Any]
    ):
        """Persist an animation preset atomically."""
        path = self._animation_preset_path(animation_name, preset_id)
        if path is None:
            raise ValueError("Invalid animation preset path")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix('.json.tmp')
        tmp_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        tmp_path.replace(path)

    def _apply_preview_layout(self, led_info: Dict[str, int]):
        """Keep preview manager/controller dimensions in lock-step."""
        self.preview_manager.controller.strip_count = led_info['strip_count']
        self.preview_manager.controller.leds_per_strip = led_info['leds_per_strip']
        self.preview_manager.controller.total_leds = led_info['total_leds']

        preview_controller = getattr(self.preview_manager, 'preview_controller', None)
        if preview_controller is not None:
            preview_controller.strip_count = led_info['strip_count']
            preview_controller.leds_per_strip = led_info['leds_per_strip']
            preview_controller.total_leds = led_info['total_leds']

    def _sync_preview_layout_from_status(self, raw_status: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """
        Sync preview dimensions from controller status so preview and live frames
        use the same geometry.
        """
        status = raw_status if isinstance(raw_status, dict) else (self.control_channel.read_status() or {})
        led_info = self._normalize_led_info(status.get('led_info'))
        self._apply_preview_layout(led_info)
        if not self.local_mode and hasattr(self.preview_manager, 'set_plant_modifiers'):
            try:
                if 'plant_modifiers' in status:
                    self.preview_manager.set_plant_modifiers(status['plant_modifiers'])
                elif isinstance(status.get('plant_aware'), bool):
                    self.preview_manager.set_plant_aware(status['plant_aware'])
            except ValueError:
                pass
        if not self.local_mode and 'installation_profile_digest' in status:
            selector = getattr(
                self.preview_manager, 'select_installation_profile', None
            )
            if callable(selector):
                requested_digest = status['installation_profile_digest']
                try:
                    selection = selector(requested_digest)
                except (TypeError, ValueError, RuntimeError) as exc:
                    # Selection is validate-before-mutation.  Preserve the last
                    # valid preview authority while still surfacing a useful
                    # diagnostic on this normalized status response.
                    status['installation_profile_preview'] = {
                        'state': 'rejected',
                        'requested_digest': requested_digest,
                        'error': str(exc),
                    }
                else:
                    status['installation_profile_preview'] = {
                        'state': 'selected',
                        'requested_digest': requested_digest,
                        'selection': selection,
                    }
        return led_info

    def _status_payload(self, decode_frame: bool = False) -> Dict[str, Any]:
        """Normalize the controller status so every consumer sees the same structure."""
        raw_status = self.control_channel.read_status()
        if not raw_status:
            return self._empty_status()

        status = dict(raw_status)
        controller_release_id = status.get('release_id')
        status['controller_release_id'] = controller_release_id
        status['release_id'] = self.release_id
        status['release_consistent'] = controller_release_id == self.release_id
        status['led_info'] = self._sync_preview_layout_from_status(status)
        stats = status.get('animation_stats') or status.get('stats') or {}
        status['animation_stats'] = stats
        status['stats'] = stats
        status.setdefault('animation_hash', None)
        status.setdefault('animation_info', None)
        status.setdefault('performance', {})
        status.setdefault('driver_stats', {})
        status.setdefault('current_animation', None)
        status.setdefault('current_preset', None)
        status.setdefault('brightness', None)
        status.setdefault('is_running', False)
        status.setdefault('mode', 'animation' if status.get('is_running') else 'idle')
        status.setdefault('painter_active', status.get('mode') == 'painter')
        status.setdefault('painter_updated_at', None)
        status.setdefault('frame_count', 0)
        status.setdefault('target_fps', 0)
        status.setdefault('animation_speed_scale', DEFAULT_ANIMATION_SPEED_SCALE)
        status.setdefault('plant_aware', DEFAULT_PLANT_AWARE)
        status.setdefault(
            'plant_modifiers',
            PlantModifierState.from_legacy(DEFAULT_PLANT_AWARE).to_dict(),
        )
        if not isinstance(status.get('vibe'), dict):
            status['vibe'] = self._selected_vibe_status()
        if not self.local_mode and hasattr(self.preview_manager, 'set_plant_modifiers'):
            try:
                self.preview_manager.set_plant_modifiers(status['plant_modifiers'])
            except ValueError:
                pass
        status.setdefault('actual_fps', 0)
        status.setdefault('uptime', 0)
        status['deploy_timestamp'] = self._deploy_timestamp()
        timestamp = status.get('updated_at') or status.get('timestamp')
        if not timestamp:
            timestamp = time.time()
        status['timestamp'] = timestamp

        encoded_frame = raw_status.get('frame_data_encoded')
        raw_frame_list = raw_status.get('frame_data')
        frame_length = raw_status.get('frame_data_length')

        if isinstance(raw_frame_list, list):
            frame_length = len(raw_frame_list)
            if not encoded_frame:
                encoded_frame = encode_frame_data(raw_frame_list)
        elif isinstance(raw_frame_list, str) and not encoded_frame:
            # Backwards compatibility: some snapshots may have stored the encoded
            # string under frame_data.
            encoded_frame = raw_frame_list

        status['frame_data_encoded'] = encoded_frame or ''
        status['frame_data_length'] = frame_length or 0
        status['frame_encoding'] = raw_status.get('frame_encoding') or (
            FRAME_ENCODING_NAME if encoded_frame else None
        )

        if decode_frame:
            if isinstance(raw_frame_list, list):
                status['frame_data'] = raw_frame_list
            else:
                status['frame_data'] = decode_frame_data(encoded_frame or '')
        else:
            status['frame_data'] = []

        return status

    def _deploy_timestamp(self) -> Optional[float]:
        """Read the most recent successful fast-deploy timestamp from disk."""
        try:
            payload = json.loads(self.deployment_status_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        deploy_timestamp = payload.get('deploy_timestamp') if isinstance(payload, dict) else None
        if isinstance(deploy_timestamp, bool) or not isinstance(deploy_timestamp, (int, float)):
            return None
        return deploy_timestamp

    def _empty_status(self):
        """Fallback status when controller process has not written a status file yet."""
        return {
            'is_running': False,
            'mode': 'idle',
            'painter_active': False,
            'painter_updated_at': None,
            'current_animation': None,
            'current_preset': None,
            'brightness': None,
            'frame_count': 0,
            'uptime': 0,
            'target_fps': 0,
            'animation_speed_scale': DEFAULT_ANIMATION_SPEED_SCALE,
            'plant_aware': DEFAULT_PLANT_AWARE,
            'plant_modifiers': PlantModifierState.from_legacy(DEFAULT_PLANT_AWARE).to_dict(),
            'vibe': self._selected_vibe_status(),
            'actual_fps': 0,
            'animation_stats': {},
            'stats': {},
            'animation_hash': None,
            'animation_info': None,
            'led_info': self._fallback_led_info(),
            'driver_stats': {},
            'frame_data': [],
            'frame_data_encoded': '',
            'frame_data_length': 0,
            'frame_encoding': None,
            'deploy_timestamp': self._deploy_timestamp(),
            'release_id': self.release_id,
            'controller_release_id': None,
            'release_consistent': self.release_id is None,
            'timestamp': time.time()
        }


def create_app(control_channel: FileControlChannel = None,
               host: str = '0.0.0.0',
               port: int = 5000,
               strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
               animations_dir: str = None,
               animation_speed_scale: float = DEFAULT_ANIMATION_SPEED_SCALE,
               plant_aware: bool = DEFAULT_PLANT_AWARE,
               release_id: Optional[str] = None,
               feature_flags: Optional[AnimationPipelineFeatureFlags] = None,
               installation_profile_topology: InstallationProfileTopology = (
                   IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
               ),
               project_root: Optional[Path] = None):
    """Factory function to create the web application"""
    if control_channel is None:
        control_channel = FileControlChannel()

    # Preview-only controller keeps renderer and plugin listing in this process
    preview_controller = PreviewLEDController(strips, leds_per_strip)
    preview_project_root = (
        Path(project_root)
        if project_root is not None
        else Path(__file__).resolve().parents[1]
    )

    # Create animation manager (preview only, no hardware access)
    manager_kwargs: Dict[str, Any] = {
        'plugins_dir': animations_dir,
        'animation_speed_scale': animation_speed_scale,
        'plant_aware': plant_aware,
        'installation_profile_library': InstallationProfileLibrary(
            preview_project_root / 'installation_profile_library'
        ),
        'installation_profile_topology': installation_profile_topology,
        'native_background_library': NativeBackgroundLibrary(
            preview_project_root / 'receiver_library/native_backgrounds'
        ),
        'auto_start': False,
    }
    if feature_flags is not None:
        manager_kwargs['feature_flags'] = feature_flags
    animation_manager = AnimationManager(
        preview_controller,
        **manager_kwargs,
    )

    # Create web interface
    web_interface = AnimationWebInterface(
        control_channel,
        animation_manager,
        host=host,
        port=port,
        release_id=release_id,
    )

    return web_interface


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='LED Animation Web Interface')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=5000, help='Port to listen on')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    # LED layout for previews (does not touch hardware)
    parser.add_argument('--strips', type=int, default=DEFAULT_STRIP_COUNT, help='Number of strips')
    parser.add_argument('--leds-per-strip', type=int, default=DEFAULT_LEDS_PER_STRIP, help='LEDs per strip')
    parser.add_argument('--animation-speed-scale', type=float, default=DEFAULT_ANIMATION_SPEED_SCALE,
                        help='Speed multiplier applied to preview animations')
    
    args = parser.parse_args()
    
    # Create and run web interface
    web_interface = create_app(
        host=args.host,
        port=args.port,
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
        animation_speed_scale=args.animation_speed_scale
    )
    web_interface.run(debug=args.debug)

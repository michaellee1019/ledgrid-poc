#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations and adjusting parameters in
real time.
"""

import inspect
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory

from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.activation_qualification import (
    QUALIFICATION_RECORD_SCHEMA,
    QUALIFICATION_RECORD_VERSION,
    QualificationValidationError,
    activation_qualification_binding_digest,
    activation_qualification_record_digest,
    evaluate_activation_qualification,
    installation_qualification_budget_digest,
    load_installation_qualification_budget,
    load_target_qualification_evidence,
)
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.installation_profile_authoring import (
    InstallationProfileAuthoring,
    InstallationProfileAuthoringError,
    InstallationProfileDraftConflict,
)
from animation.core.installation_profile_library import (
    InstallationProfileLibrary,
    InstallationProfileLibraryError,
    InstallationProfileNotFoundError,
)
from animation.core.installation_profile_runtime import EMPTY_INSTALLATION_PROFILE_DIGEST
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
)
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.native_background_library import NativeBackgroundLibrary
from animation.core.plant_awareness import (
    FIELD_MODIFIERS,
    GLOBE_REGION_ORDER,
    LEGACY_PLANT_MASK_PATH_PARAMETERS,
    PLANT_MODIFIER_IDS,
    SURFACE_MODIFIERS,
    PlantModifierState,
)
from animation.core.preview_assets import load_catalog, merge_catalogs
from drivers.frame_codec import (
    FRAME_ENCODING_NAME,
    decode_frame_data,
    encode_frame_data,
)
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from ipc.control_channel import FileControlChannel
from ipc.runtime_control import manager_controller_runtime_digests
from ipc.scene_contract import (
    BROWSER_SCENE_MAX_BYTES,
    BROWSER_SCENE_SCHEMA,
    DEFAULT_SCENE_PROVIDER_POLICY,
    FIXED_OVERLAY_SLOT,
    SCENE_PRESET_SCHEMA,
    SCENE_PRESET_VERSION,
    SceneProviderPolicy,
    SceneValidationError,
    activation_identity_from_basis,
    background_only_scene,
    build_scene_activation_basis,
    browser_scene_to_host_scene,
    canonical_json_sha256,
    decorate_browser_component,
    decorate_catalog,
    filter_catalog,
    normalize_browser_scene_document,
    build_composer_operations_status,
    normalize_global_settings_payload,
    normalize_scene_activation_command,
    normalize_scene_activation_status,
    normalize_scene_payload,
    scene_activation_basis_digest,
    scene_preview_identity,
    validate_bounded_browser_json,
)
from web.preview_worker import RuntimePreviewWorker
from web.activation_token_store import (
    ActivationTokenConflict,
    ActivationTokenExpired,
    ActivationTokenStore,
    canonical_digest,
)

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
                 release_id: Optional[str] = None,
                 activation_token_store_path: Optional[Path] = None,
                 activation_enabled: Optional[bool] = None,
                 installation_profile_authoring: Optional[
                     InstallationProfileAuthoring
                 ] = None,
                 project_root: Optional[Path] = None):
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
        self.activation_enabled = (
            bool(activation_enabled)
            if activation_enabled is not None
            else os.environ.get('LEDGRID_GUARDED_ACTIVATION_CANARY') == '1'
        )
        self.activation_mode = (
            'development_canary' if self.activation_enabled else 'disabled'
        )
        self._scene_preview_lock = threading.RLock()
        self.project_root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[1]
        )
        self.painter_presets_dir = self.project_root / "presets" / "frame_painter"
        self.animation_presets_dir = self.project_root / "presets" / "animations"
        self.scene_presets_dir = self.project_root / "presets" / "scenes"
        self.deployment_status_path = self.project_root / "run_state" / "deployment.json"
        self.target_qualification_evidence_path = (
            self.project_root
            / "run_state"
            / "activation_qualification_evidence.json"
        )
        profile_library = getattr(
            self.preview_manager, '_installation_profile_library', None
        )
        self.installation_profile_authoring = installation_profile_authoring
        if self.installation_profile_authoring is None and isinstance(
            profile_library, InstallationProfileLibrary
        ):
            self.installation_profile_authoring = InstallationProfileAuthoring(
                profile_library,
                self.project_root / 'run_state' / 'installation_profile_authoring',
            )
        self.activation_token_store_path = (
            Path(activation_token_store_path)
            if activation_token_store_path is not None
            else self.project_root / "run_state" / "activation_tokens.sqlite3"
        )
        self._activation_token_store: Optional[ActivationTokenStore] = None
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
        # Installation-profile globe regions have a frozen user-facing order.
        # Flask's default key sorting would destroy it in the JSON response.
        self.app.json.sort_keys = False

        self.painter_presets_dir.mkdir(parents=True, exist_ok=True)
        self.animation_presets_dir.mkdir(parents=True, exist_ok=True)
        self.scene_presets_dir.mkdir(parents=True, exist_ok=True)

        if self.activation_enabled:
            self._activation_token_store = ActivationTokenStore(
                self.activation_token_store_path
            )
            self._recover_activation_outbox()

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
                    else self._list_animation_presets(component_id, provider)
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

        @self.app.route('/composer-app.js')
        def browser_composer_application():
            """Serve the Composer program outside older offline-shell paths.

            Earlier workers cached ``/static/js/composer.js`` by pathname.
            Keeping this application entrypoint at a separate, revalidated path
            lets an existing installed Composer receive an urgent UI fix before
            its worker has completed a normal shell upgrade.
            """
            response = send_from_directory(
                self.project_root / 'web' / 'static' / 'js',
                'composer.js',
                mimetype='application/javascript',
            )
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/composer/bootstrap')
        def api_browser_composer_bootstrap():
            """Read-only schemas, presets, and explicit browser capabilities."""
            catalog_only = request.args.get('catalog_only') == '1'
            response = jsonify(self._browser_composer_bootstrap(
                observe_installation_profile=not catalog_only,
            ))
            response.headers['Cache-Control'] = 'no-store'
            return response

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
                    'live_edit_component': True,
                    'check_scene': self.activation_enabled,
                    'activate_scene': self.activation_enabled,
                    'activation_status': self.activation_enabled,
                },
                'activation_mode': self.activation_mode,
                'bootstrap_url': '/api/v1/composer/bootstrap?catalog_only=1',
            })
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/composer/operations/status')
        def api_browser_composer_operations_status():
            """Revision-qualified observed output and bounded health evidence."""
            response = jsonify(build_composer_operations_status(
                self._status_payload(), now_ms=int(time.time() * 1000),
            ))
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/composer/presets/validate', methods=['POST'])
        def api_browser_composer_validate_preset():
            """Validate an imported component or scene preset without mutation."""
            try:
                if (
                    request.content_length is not None
                    and request.content_length > BROWSER_SCENE_MAX_BYTES
                ):
                    raise SceneValidationError(
                        f'uploaded preset exceeds the {BROWSER_SCENE_MAX_BYTES}-byte limit'
                    )
                validated = self._validated_browser_composer_import(
                    request.get_json(silent=True),
                    encoded_size=request.content_length,
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
            """Reject the former unguarded single-look execution alias."""
            return self._guarded_scene_error(
                'Studio Looks require Composer Check and guarded activation.'
            )

        @self.app.route('/api/v1/studio-next/take-scene', methods=['POST'])
        def api_studio_next_take_scene():
            """Reject the former unguarded scene-start alias."""
            return self._guarded_scene_error(
                'Studio Next scenes require a server Check and guarded activation.'
            )
        
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
                    self._browser_scene_catalog(),
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

        @self.app.route('/api/v1/scene/checks', methods=['POST'])
        def api_check_scene_activation():
            """Authorize one exact scene/global/controller basis for 120 seconds."""
            if not self.activation_enabled:
                return self._activation_unavailable()
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            unknown = sorted(set(payload) - {
                'scene', 'global_settings', 'browser_evidence',
            })
            if unknown:
                return jsonify({
                    'error': f"unsupported Check fields: {', '.join(unknown)}"
                }), 400
            expires_at_ms = int((time.time() + 120) * 1000)
            try:
                controller_status = dict(self.control_channel.read_status() or {})
                current_scene = controller_status.get('scene_state')
                current_powered = bool(
                    controller_status.get('is_running', False)
                    or controller_status.get('painter_active', False)
                )
                if current_powered and not isinstance(current_scene, dict):
                    response = jsonify({
                        'error': (
                            'Guarded activation is unavailable while the wall is '
                            'showing a legacy animation or Painter frame because '
                            'that complete prior state cannot be restored exactly. '
                            'Stop live output before running Check.'
                        ),
                        'code': 'activation_snapshot_unavailable',
                    })
                    response.headers['Cache-Control'] = 'no-store'
                    return response, 409
                basis, _scene, settings, qualification_record, qualification_result = (
                    self._activation_basis_for_request(
                        browser_scene=payload.get('scene'),
                        global_settings=payload.get('global_settings'),
                        browser_evidence=payload.get('browser_evidence'),
                        expires_at_ms=expires_at_ms,
                        status=controller_status,
                    )
                )
                issued = self._activation_tokens().issue(basis)
            except RuntimeError as exc:
                response = jsonify({
                    'error': str(exc),
                    'code': 'controller_state_unavailable',
                })
                response.headers['Cache-Control'] = 'no-store'
                return response, 503
            except (SceneValidationError, TypeError, ValueError) as exc:
                response = jsonify({'error': str(exc), 'code': 'invalid_check'})
                response.headers['Cache-Control'] = 'no-store'
                return response, 400
            expected_digest = scene_activation_basis_digest(basis)
            if issued.basis_digest != expected_digest:
                response = jsonify({
                    'error': 'server Check basis serialization is inconsistent',
                    'code': 'check_internal_error',
                })
                response.headers['Cache-Control'] = 'no-store'
                return response, 500
            response = jsonify({
                'schema': 'ledgrid.scene-check',
                'schema_version': 1,
                'check_token': issued.token,
                'basis': basis,
                'basis_digest': expected_digest,
                'expires_at': issued.expires_at,
                'qualification': {
                    'version': basis['qualification']['version'],
                    'status': (
                        'passed'
                        if qualification_result['qualified']
                        else 'development_canary'
                    ),
                    'production_qualified': qualification_result['qualified'],
                    'record_digest': activation_qualification_record_digest(
                        qualification_record
                    ),
                    'binding_digest': qualification_result['binding_digest'],
                    'budget_digest': qualification_result['budget_digest'],
                    'gates': qualification_result['gates'],
                    'blockers': qualification_result['reasons'],
                    'browser_evidence': 'advisory',
                    'global_settings_digest': canonical_json_sha256(settings),
                },
            })
            response.headers['Cache-Control'] = 'no-store'
            return response, 201

        @self.app.route('/api/v1/scene', methods=['PUT', 'POST'])
        def api_start_scene():
            if not self.activation_enabled:
                return self._activation_unavailable()
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            token = payload.get('check_token')
            expected_session = payload.get('expected_controller_session_id')
            expected_revision = payload.get('expected_controller_state_revision')
            idempotency_key = request.headers.get('Idempotency-Key')
            if not all(value is not None for value in (
                token, expected_session, expected_revision
            )) or not idempotency_key:
                return jsonify({
                    'error': (
                        'check_token, expected controller session/revision, and '
                        'Idempotency-Key are required'
                    ),
                    'code': 'activation_precondition_required',
                }), 428
            try:
                if (
                    not isinstance(idempotency_key, str)
                    or not 1 <= len(idempotency_key.encode('utf-8')) <= 256
                    or any(ord(character) < 32 for character in idempotency_key)
                ):
                    raise SceneValidationError('Idempotency-Key is invalid')
                stored = self._activation_tokens().inspect(
                    token, allow_bound_expired=True
                )
            except ActivationTokenExpired as exc:
                return jsonify({'error': str(exc), 'code': 'check_expired'}), 410
            except ActivationTokenConflict as exc:
                return jsonify({'error': str(exc), 'code': 'check_conflict'}), 409
            except SceneValidationError as exc:
                return jsonify({'error': str(exc)}), 400
            basis = stored.basis
            if (
                expected_session != basis['controller']['session_id']
                or expected_revision != basis['controller']['state_revision']
            ):
                return jsonify({
                    'error': 'activation controller precondition changed after Check',
                    'code': 'activation_conflict',
                }), 409
            request_digest = canonical_digest({
                'basis_digest': stored.basis_digest,
                'scene': payload.get('scene'),
                'global_settings': payload.get('global_settings'),
                'expected_controller_session_id': expected_session,
                'expected_controller_state_revision': expected_revision,
            })
            if stored.activation_id is not None:
                try:
                    bound = self._activation_tokens().bind(
                        token,
                        basis_digest=scene_activation_basis_digest(basis),
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                        activation_id_factory=lambda: str(uuid.uuid4()),
                    )
                    existing_status = self._deliver_activation_outbox(bound.token)
                except ActivationTokenConflict as exc:
                    return jsonify({
                        'error': str(exc), 'code': 'activation_conflict'
                    }), 409
                except (FileExistsError, OSError, SceneValidationError, ValueError) as exc:
                    return jsonify({
                        'error': f'activation could not be queued durably: {exc}',
                        'code': 'activation_queue_failed',
                    }), 500
                status_url = f'/api/v1/scene/activations/{bound.activation_id}'
                response = jsonify({
                    'schema': 'ledgrid.scene-activation-accepted',
                    'schema_version': 1,
                    'activation_id': bound.activation_id,
                    'phase': existing_status['phase'],
                    'pending': existing_status['phase'] not in {
                        'active', 'rolled_back', 'failed', 'timed_out'
                    },
                    'status_url': status_url,
                    'exact_retry': True,
                })
                response.status_code = 202
                response.headers['Location'] = status_url
                response.headers['Cache-Control'] = 'no-store'
                return response
            try:
                document, scene = self._validated_browser_scene_document(
                    payload.get('scene'), purpose='activation'
                )
                settings = self._canonical_activation_global_settings(
                    payload.get('global_settings')
                )
            except SceneValidationError as exc:
                return jsonify({
                    'error': f'activation no longer matches its Check: {exc}',
                    'code': 'activation_conflict',
                }), 409
            except (TypeError, ValueError) as exc:
                return jsonify({'error': str(exc), 'code': 'invalid_activation'}), 400
            if (
                canonical_json_sha256(document) != basis['browser_scene']['digest']
                or canonical_json_sha256(scene) != basis['host_scene']['digest']
                or canonical_json_sha256(settings) != basis['global_settings']['digest']
                or document['installation_profile']['digest']
                != basis['installation_profile_digest']
            ):
                return jsonify({
                    'error': 'scene, globals, runtime, or profile changed after Check',
                    'code': 'activation_conflict',
                }), 409
            try:
                status = dict(self.control_channel.read_status() or {})
                current_session, current_revision, current_identity = (
                    self._activation_controller_identity(status)
                )
            except RuntimeError as exc:
                return jsonify({
                    'error': str(exc), 'code': 'controller_state_unavailable'
                }), 503
            if (
                current_session != basis['controller']['session_id']
                or current_revision != basis['controller']['state_revision']
                or current_identity
                != basis['controller']['current_identity_digest']
            ):
                return jsonify({
                    'error': 'controller state changed after Check',
                    'code': 'activation_conflict',
                }), 409

            def activation_outbox(activation_id: str) -> Dict[str, Any]:
                command = normalize_scene_activation_command({
                    'schema': 'ledgrid.scene-activation-command',
                    'schema_version': 1,
                    'activation_id': activation_id,
                    'check_token_digest': hashlib.sha256(
                        token.encode('utf-8')
                    ).hexdigest(),
                    'basis': basis,
                    'basis_digest': stored.basis_digest,
                    'desired': {
                        'scene': scene,
                        'global_settings': settings,
                        'installation_profile_digest': basis[
                            'installation_profile_digest'
                        ],
                    },
                }, catalog=self._component_catalog(),
                    provider_policy=self._scene_provider_policy())
                identity = activation_identity_from_basis(basis)
                queued = normalize_scene_activation_status({
                    'schema': 'ledgrid.scene-activation-status',
                    'schema_version': 1,
                    'activation_id': activation_id,
                    'basis_digest': stored.basis_digest,
                    'command_id': activation_id,
                    'phase': 'queued',
                    'requested_identity': identity,
                    'normalized_identity': identity,
                    'observed_identity': None,
                    'controller': {
                        'session_id': expected_session,
                        'state_revision_before': expected_revision,
                        'state_revision_after': None,
                    },
                    'telemetry': {
                        'complete': False, 'fresh': False, 'observed_at': None,
                    },
                    'rollback': {
                        'available': False, 'snapshot_id': None,
                        'result': None, 'error': None,
                    },
                    'camera_observation': None,
                    'error': None,
                })
                return {'command': command, 'status': queued}
            try:
                bound = self._activation_tokens().bind(
                    token,
                    basis_digest=scene_activation_basis_digest(basis),
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    activation_id_factory=lambda: str(uuid.uuid4()),
                    outbox_factory=activation_outbox,
                )
            except ActivationTokenExpired as exc:
                return jsonify({'error': str(exc), 'code': 'check_expired'}), 410
            except ActivationTokenConflict as exc:
                return jsonify({'error': str(exc), 'code': 'activation_conflict'}), 409

            activation_id = bound.activation_id
            try:
                existing_status = self._deliver_activation_outbox(bound.token)
            except (FileExistsError, OSError, SceneValidationError, ValueError) as exc:
                return jsonify({
                    'error': f'activation could not be queued durably: {exc}',
                    'code': 'activation_queue_failed',
                }), 500

            status_url = f'/api/v1/scene/activations/{activation_id}'
            response = jsonify({
                'schema': 'ledgrid.scene-activation-accepted',
                'schema_version': 1,
                'activation_id': activation_id,
                'phase': existing_status['phase'],
                'pending': existing_status['phase'] not in {
                    'active', 'rolled_back', 'failed', 'timed_out'
                },
                'status_url': status_url,
                'exact_retry': bound.exact_retry,
            })
            response.status_code = 202
            response.headers['Location'] = status_url
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/scene', methods=['DELETE'])
        def api_stop_scene():
            return self._guarded_scene_error(
                'Stopping a complete scene requires the guarded activation path.'
            )

        @self.app.route('/api/v1/scene/activations/<activation_id>')
        def api_get_scene_activation(activation_id: str):
            if not self.activation_enabled:
                return self._activation_unavailable()
            try:
                status = self.control_channel.read_activation_status(activation_id)
                if status is None:
                    return jsonify({'error': 'Activation not found'}), 404
                status = normalize_scene_activation_status(status)
            except (SceneValidationError, ValueError) as exc:
                return jsonify({
                    'error': f'Activation status is invalid: {exc}'
                }), 500
            response = jsonify(status)
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route(
            '/api/v1/scene/activations/<activation_id>', methods=['DELETE']
        )
        def api_cancel_scene_activation(activation_id: str):
            if not self.activation_enabled:
                return self._activation_unavailable()
            try:
                status = self.control_channel.read_activation_status(activation_id)
                if status is None:
                    return jsonify({'error': 'Activation not found'}), 404
                status = normalize_scene_activation_status(status)
                cancel = self.control_channel.read_activation_cancel(activation_id)
                if cancel is None and status['phase'] not in {'queued', 'preflighting'}:
                    return jsonify({
                        'error': 'Activation can no longer be canceled without mutation',
                        'code': 'activation_cancel_conflict',
                    }), 409
                if cancel is None:
                    cancel = self.control_channel.request_activation_cancel(activation_id)
            except (SceneValidationError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400
            request_status_url = (
                f'/api/v1/scene/activations/{activation_id}'
                f'/cancel-requests/{cancel["request_id"]}'
            )
            response = jsonify({
                'activation_id': activation_id,
                'request_id': cancel['request_id'],
                'phase': status['phase'],
                'cancel_requested': True,
                'requested_at': cancel['requested_at'],
                'request_status_url': request_status_url,
            })
            response.status_code = 202
            response.headers['Location'] = request_status_url
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route(
            '/api/v1/scene/activations/<activation_id>'
            '/cancel-requests/<request_id>'
        )
        def api_get_scene_activation_cancel(activation_id: str, request_id: str):
            if not self.activation_enabled:
                return self._activation_unavailable()
            try:
                cancel = self.control_channel.read_activation_cancel(activation_id)
                if cancel is None or cancel.get('request_id') != request_id:
                    return jsonify({'error': 'Cancellation request not found'}), 404
                result = self.control_channel.read_activation_cancel_result(
                    activation_id
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            payload = result or {
                'schema': 'ledgrid.scene-activation-cancel-result',
                'schema_version': 1,
                'request_id': request_id,
                'activation_id': activation_id,
                'outcome': 'pending',
                'status_phase': None,
                'error': None,
                'completed_at': None,
            }
            response = jsonify(payload)
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route(
            '/api/v1/scene/activations/<activation_id>/rollback',
            methods=['POST'],
        )
        def api_rollback_scene_activation(activation_id: str):
            if not self.activation_enabled:
                return self._activation_unavailable()
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or (
                body.get('expected_controller_session_id') is None
                or body.get('expected_controller_state_revision') is None
            ):
                return jsonify({
                    'error': 'rollback controller session and revision are required',
                    'code': 'activation_precondition_required',
                }), 428
            try:
                status = self.control_channel.read_activation_status(activation_id)
                if status is None:
                    return jsonify({'error': 'Activation not found'}), 404
                status = normalize_scene_activation_status(status)
                existing_rollback = self.control_channel.read_activation_rollback(
                    activation_id
                )
            except (SceneValidationError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400
            if existing_rollback is not None:
                if (
                    existing_rollback['expected_controller_session_id']
                    != body['expected_controller_session_id']
                    or existing_rollback['expected_controller_state_revision']
                    != body['expected_controller_state_revision']
                ):
                    return jsonify({
                        'error': 'activation already has a different rollback request',
                        'code': 'activation_conflict',
                    }), 409
                rollback = existing_rollback
                request_status_url = (
                    f'/api/v1/scene/activations/{activation_id}'
                    f'/rollback-requests/{rollback["request_id"]}'
                )
                response = jsonify({
                    'activation_id': activation_id,
                    'request_id': rollback['request_id'],
                    'rollback_requested': True,
                    'snapshot_id': rollback['snapshot_id'],
                    'request_status_url': request_status_url,
                    'exact_retry': True,
                })
                response.status_code = 202
                response.headers['Location'] = request_status_url
                response.headers['Cache-Control'] = 'no-store'
                return response
            if not status['rollback']['available']:
                return jsonify({
                    'error': 'Exact rollback snapshot is unavailable',
                    'code': 'rollback_unavailable',
                }), 409
            if (
                body['expected_controller_session_id']
                != status['controller']['session_id']
                or body['expected_controller_state_revision']
                != status['controller']['state_revision_after']
            ):
                return jsonify({
                    'error': 'controller state changed after activation',
                    'code': 'activation_conflict',
                }), 409
            try:
                current_session, current_revision, _current_identity = (
                    self._activation_controller_identity(
                        dict(self.control_channel.read_status() or {})
                    )
                )
            except RuntimeError as exc:
                return jsonify({
                    'error': str(exc), 'code': 'controller_state_unavailable'
                }), 503
            if (
                current_session != body['expected_controller_session_id']
                or current_revision != body['expected_controller_state_revision']
            ):
                return jsonify({
                    'error': 'controller state changed before rollback was queued',
                    'code': 'activation_conflict',
                }), 409
            try:
                rollback = self.control_channel.request_activation_rollback(
                    activation_id,
                    snapshot_id=status['rollback']['snapshot_id'],
                    expected_controller_session_id=(
                        body['expected_controller_session_id']
                    ),
                    expected_controller_state_revision=(
                        body['expected_controller_state_revision']
                    ),
                )
            except (FileExistsError, OSError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 409
            request_status_url = (
                f'/api/v1/scene/activations/{activation_id}'
                f'/rollback-requests/{rollback["request_id"]}'
            )
            response = jsonify({
                'activation_id': activation_id,
                'request_id': rollback['request_id'],
                'rollback_requested': True,
                'snapshot_id': rollback['snapshot_id'],
                'status_url': f'/api/v1/scene/activations/{activation_id}',
                'request_status_url': request_status_url,
                'exact_retry': False,
            })
            response.status_code = 202
            response.headers['Location'] = request_status_url
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route(
            '/api/v1/scene/activations/<activation_id>'
            '/rollback-requests/<request_id>'
        )
        def api_get_scene_activation_rollback(activation_id: str, request_id: str):
            if not self.activation_enabled:
                return self._activation_unavailable()
            try:
                rollback = self.control_channel.read_activation_rollback(
                    activation_id
                )
                if rollback is None or rollback.get('request_id') != request_id:
                    return jsonify({'error': 'Rollback request not found'}), 404
                result = self.control_channel.read_activation_rollback_result(
                    activation_id
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            payload = result or {
                'schema': 'ledgrid.scene-activation-rollback-result',
                'schema_version': 1,
                'request_id': request_id,
                'activation_id': activation_id,
                'outcome': 'pending',
                'status_phase': None,
                'error': None,
                'completed_at': None,
            }
            response = jsonify(payload)
            response.headers['Cache-Control'] = 'no-store'
            return response

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
            """Apply an explicit live-editor parameter update to the active scene.

            Ordinary direct PATCH calls remain fail-closed.  Composer live edit
            opts in per request, names the component it expects to be live, and
            may update parameters only; replacing a scene still uses guarded
            activation.
            """
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or payload.get('live_edit') is not True:
                return self._guarded_scene_error(
                    f'Updating scene component {target!r} requires a complete guarded activation.'
                )
            unknown = sorted(set(payload) - {
                'live_edit', 'expected_component', 'params',
            })
            if unknown:
                return jsonify({
                    'error': f"unsupported live-edit fields: {', '.join(unknown)}",
                    'code': 'invalid_live_edit',
                }), 400
            expected = payload.get('expected_component')
            if not isinstance(expected, dict):
                return jsonify({
                    'error': 'live edit requires the expected active component',
                    'code': 'live_edit_precondition_required',
                }), 428
            expected_provider = expected.get('provider')
            expected_component_id = expected.get('component_id')
            if not isinstance(expected_provider, str) or not isinstance(expected_component_id, str):
                return jsonify({
                    'error': 'expected active component must include provider and component_id',
                    'code': 'invalid_live_edit',
                }), 400
            try:
                scene = self._current_scene_payload()
                component = scene.get(target) if isinstance(scene, dict) else None
                if not isinstance(component, dict) or (
                    component.get('provider') != expected_provider
                    or component.get('plugin_id') != expected_component_id
                ):
                    raise SceneValidationError(
                        'the wall is no longer running the Composer renderer selected for live edit'
                    )
                update = self._validated_scene_update(target, {
                    'params': payload.get('params'),
                }, scene=scene)
                # ``component`` is the validated active-scene reference used
                # above for identity matching.  Do not send it back through
                # the targeted updater: a background component object means
                # replacement, whereas live edit deliberately changes only
                # the existing component's parameters.
                command = self.control_channel.send_command(
                    'update_scene_component', target=target,
                    update={'params': update['params']},
                )
            except SceneValidationError as exc:
                return jsonify({
                    'error': str(exc), 'code': 'live_edit_conflict',
                }), 409
            except (TypeError, ValueError) as exc:
                return jsonify({'error': str(exc), 'code': 'invalid_live_edit'}), 400
            response = jsonify({
                'success': True,
                'target': target,
                'component': component,
                'command_id': self._command_id(command),
            })
            response.headers['Cache-Control'] = 'no-store'
            return response, 202

        @self.app.route('/api/v1/scene/preview', methods=['POST'])
        def api_preview_scene():
            payload = request.get_json(silent=True)
            try:
                body = payload if isinstance(payload, dict) else {}
                scene = self._validated_scene_request(
                    body.get('scene', body), browser_purpose='preview'
                )
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
            provider = request.args.get('provider')
            if provider is None and len(matches) == 1:
                provider = matches[0].get('provider')
            if not isinstance(provider, str) or not any(
                item.get('provider') == provider for item in matches
            ):
                return jsonify({
                    'error': (
                        'Component preset discovery requires a provider-qualified '
                        'identity'
                    ),
                    'component_id': component_id,
                    'providers': sorted({
                        str(item.get('provider')) for item in matches
                    }),
                }), 409
            component = next(item for item in matches if item.get('provider') == provider)
            return jsonify({
                'schema': 'ledgrid.component-preset-list', 'schema_version': 1,
                'component_id': component_id,
                'provider': provider,
                'component': component,
                'presets': self._list_animation_presets(component_id, provider),
            })

        @self.app.route(
            '/api/v1/components/<component_id>/presets/<preset_id>',
            methods=['GET', 'DELETE'],
        )
        def api_component_preset_record(component_id: str, preset_id: str):
            """Read or remove one exact-provider Composer preset record.

            Runtime records are the only mutable user-owned records. Curated
            plugin presets and legacy records remain read-only, including when
            their names match a record for another provider.
            """
            matches = [
                item for item in self._component_catalog()
                if item.get('plugin_id') == component_id
            ]
            if not matches:
                return jsonify({'error': 'Component not found'}), 404
            provider = request.args.get('provider')
            if provider is None and len(matches) == 1:
                provider = matches[0].get('provider')
            if not isinstance(provider, str) or not any(
                item.get('provider') == provider for item in matches
            ):
                return jsonify({
                    'error': (
                        'Component preset record requires a provider-qualified '
                        'identity'
                    ),
                    'component_id': component_id,
                    'providers': sorted({
                        str(item.get('provider')) for item in matches
                    }),
                }), 409
            safe_id = self._sanitize_preset_id(preset_id)
            if not safe_id or safe_id != preset_id:
                return jsonify({'error': 'Preset ID is invalid'}), 400
            component_key = f'{provider}:{component_id}'
            runtime_path = self._animation_preset_path(
                component_id, preset_id, provider
            )
            if request.method == 'DELETE':
                if runtime_path is not None and runtime_path.is_file():
                    try:
                        runtime_path.unlink()
                    except OSError:
                        return jsonify({'error': 'Failed to delete preset'}), 500
                    if provider == 'python' and self.runtime_preview_worker is not None:
                        self.runtime_preview_worker.delete(component_id, preset_id)
                    return jsonify({
                        'success': True,
                        'component_key': component_key,
                        'preset_id': preset_id,
                    })
                if self._load_animation_preset(component_id, preset_id, provider):
                    return jsonify({
                        'error': 'Built-in and legacy preset records are read-only.',
                        'code': 'preset_immutable',
                        'component_key': component_key,
                        'preset_id': preset_id,
                    }), 409
                return jsonify({'error': 'Preset not found'}), 404

            preset = self._load_animation_preset(component_id, preset_id, provider)
            if preset is None:
                return jsonify({'error': 'Preset not found'}), 404
            preset = dict(preset)
            preset['component_key'] = component_key
            preset['ownership'] = self._component_preset_ownership(
                component_id, preset_id, provider
            )
            preset['preset_fingerprint'] = self._component_preset_fingerprint(preset)
            response = jsonify({'preset': preset})
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/presets/legacy/<component_id>/export')
        def api_export_legacy_component_presets(component_id: str):
            """Export withheld pre-provider records so they can be reimported."""
            if not self._legacy_preset_is_ambiguous(component_id):
                return jsonify({'error': 'No ambiguous legacy presets exist'}), 404
            legacy_dir = self._legacy_animation_preset_dir(component_id)
            records = []
            if legacy_dir is not None and legacy_dir.is_dir():
                for path in sorted(legacy_dir.glob('*.json')):
                    payload = self._read_json_file(path)
                    if payload is not None:
                        records.append(payload)
            return jsonify({
                'schema': 'ledgrid.legacy-component-preset-export',
                'schema_version': 1,
                'component_id': component_id,
                'records': records,
                'recovery': self._legacy_preset_recovery(component_id),
            })

        @self.app.route('/api/v1/presets/legacy/<component_id>', methods=['DELETE'])
        def api_discard_legacy_component_presets(component_id: str):
            """Discard only explicitly selected ambiguous legacy records."""
            if not self._legacy_preset_is_ambiguous(component_id):
                return jsonify({'error': 'No ambiguous legacy presets exist'}), 404
            legacy_dir = self._legacy_animation_preset_dir(component_id)
            if legacy_dir is None or not legacy_dir.is_dir():
                return jsonify({'success': True, 'discarded': 0})
            discarded = 0
            for path in legacy_dir.glob('*.json'):
                path.unlink()
                discarded += 1
            try:
                legacy_dir.rmdir()
            except OSError:
                pass
            return jsonify({'success': True, 'discarded': discarded})

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
            try:
                validate_bounded_browser_json(
                    payload,
                    label='scene preset save',
                    encoded_size=request.content_length,
                )
            except SceneValidationError as exc:
                return jsonify({'error': str(exc)}), 400
            name = str(payload.get('name') or '').strip()
            preset_id = self._sanitize_preset_id(name)
            if not name or not preset_id:
                return jsonify({'error': 'Scene preset name is required'}), 400
            if any(key in payload for key in ('vibe', 'plant_modifiers', 'output')):
                return jsonify({'error': 'Scene presets never capture vibe, plant, or output state'}), 400
            try:
                raw_scene = payload.get('scene')
                if (
                    isinstance(raw_scene, dict)
                    and raw_scene.get('schema') == BROWSER_SCENE_SCHEMA
                ):
                    stored_scene, _host_scene = self._validated_browser_scene_document(
                        raw_scene, purpose='save'
                    )
                else:
                    stored_scene = self._validated_scene_request(
                        raw_scene, browser_purpose='save'
                    )
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
                'scene': stored_scene,
                'component_identities': self._scene_preset_component_identities(
                    stored_scene
                ),
                'created_at': existing.get('created_at', now),
                'updated_at': now,
            }
            self._write_scene_preset(preset_id, preset)
            return jsonify({'success': True, 'preset': preset})

        @self.app.route('/api/v1/scene-presets/<preset_id>/apply', methods=['POST'])
        def api_apply_scene_preset(preset_id: str):
            return self._guarded_scene_error(
                f'Scene preset {preset_id!r} requires Check and guarded activation.'
            )

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

            existing = self._load_animation_preset(
                animation_name, preset_id, provider='python'
            )
            now = time.time()
            preset_payload = {
                'version': 2,
                'preset_id': preset_id,
                'name': raw_name,
                'animation': animation_name,
                'provider': 'python',
                'params': params,
                'created_at': existing.get('created_at', now) if existing else now,
                'updated_at': now,
            }
            for field in ('category', 'description', 'tags', 'palette'):
                if field in payload:
                    preset_payload[field] = payload[field]
                elif existing and field in existing:
                    preset_payload[field] = existing[field]
            self._write_animation_preset(
                animation_name, preset_id, preset_payload, provider='python'
            )
            if self.runtime_preview_worker is not None:
                fallback = self._preview_metadata(animation_name) or {}
                preset_path = self._animation_preset_path(
                    animation_name, preset_id, provider='python'
                )
                if preset_path is not None:
                    self.runtime_preview_worker.queue(
                        animation_name, preset_id, preset_path, fallback
                    )
            return jsonify({'success': True, 'preset': self._animation_preset_summary(preset_payload)})

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>/apply', methods=['POST'])
        def api_apply_animation_preset(animation_name: str, preset_id: str):
            """Reject the former unguarded animation-preset execution alias."""
            return self._guarded_scene_error(
                'Animation presets require Composer Check and guarded activation.'
            )

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>', methods=['DELETE'])
        def api_delete_animation_preset(animation_name: str, preset_id: str):
            """API: Delete one animation preset."""
            path = self._animation_preset_path(
                animation_name, preset_id, provider='python'
            )
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
            """Reject the former unguarded animation-start alias."""
            return self._guarded_scene_error(
                'Starting an animation requires Composer Check and guarded activation.'
            )
        
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
            """Apply operational power/brightness without bypassing activation."""
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or not payload:
                return jsonify({'error': 'request body must be a non-empty JSON object'}), 400

            if 'animation' in payload or 'preset' in payload:
                return self._guarded_scene_error(
                    'Selecting an animation or preset requires Composer Check and '
                    'guarded activation.'
                )

            supported = {'power', 'brightness'}
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

        @self.app.route(
            '/api/v1/installation-profiles/<digest>/draft',
            methods=['GET'],
        )
        def api_installation_profile_get_draft(digest: str):
            """Load a revisioned draft derived from this exact immutable artifact."""
            try:
                draft = self._installation_profile_authoring().load(digest)
            except InstallationProfileNotFoundError as exc:
                return jsonify({'error': str(exc)}), 404
            except InstallationProfileAuthoringError as exc:
                return jsonify({'error': str(exc)}), 400
            except InstallationProfileLibraryError as exc:
                return jsonify({'error': str(exc)}), 500
            response = jsonify(draft)
            response.headers['ETag'] = f'"{draft["revision"]}"'
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route(
            '/api/v1/installation-profiles/<digest>/draft',
            methods=['PUT'],
        )
        def api_installation_profile_update_draft(digest: str):
            """Replace a draft using restart-safe optimistic concurrency."""
            expected_revision = self._installation_profile_if_match()
            if expected_revision is None:
                return jsonify({
                    'error': 'If-Match is required for installation-profile draft updates',
                    'code': 'precondition_required',
                }), 428
            payload = request.get_json(silent=True)
            if payload is None:
                return jsonify({'error': 'A complete JSON draft is required'}), 400
            try:
                draft = self._installation_profile_authoring().update(
                    digest,
                    expected_revision=expected_revision,
                    draft=payload,
                )
            except InstallationProfileDraftConflict as exc:
                response = jsonify({
                    'error': str(exc),
                    'code': 'revision_conflict',
                    'current_revision': exc.current_revision,
                })
                response.status_code = 409
                response.headers['ETag'] = f'"{exc.current_revision}"'
                return response
            except InstallationProfileNotFoundError as exc:
                return jsonify({'error': str(exc)}), 404
            except InstallationProfileAuthoringError as exc:
                return jsonify({'error': str(exc)}), 400
            except InstallationProfileLibraryError as exc:
                return jsonify({'error': str(exc)}), 500
            response = jsonify(draft)
            response.headers['ETag'] = f'"{draft["revision"]}"'
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route(
            '/api/v1/installation-profiles/<digest>/publish',
            methods=['POST'],
        )
        def api_installation_profile_publish(digest: str):
            """Compile and publish a candidate without selecting the live profile."""
            expected_revision = self._installation_profile_if_match()
            if expected_revision is None:
                return jsonify({
                    'error': 'If-Match is required for installation-profile publication',
                    'code': 'precondition_required',
                }), 428
            try:
                receipt, draft = self._installation_profile_authoring().publish(
                    digest,
                    expected_revision=expected_revision,
                )
            except InstallationProfileDraftConflict as exc:
                response = jsonify({
                    'error': str(exc),
                    'code': 'revision_conflict',
                    'current_revision': exc.current_revision,
                })
                response.status_code = 409
                response.headers['ETag'] = f'"{exc.current_revision}"'
                return response
            except InstallationProfileNotFoundError as exc:
                return jsonify({'error': str(exc)}), 404
            except InstallationProfileAuthoringError as exc:
                return jsonify({'error': str(exc)}), 400
            except InstallationProfileLibraryError as exc:
                return jsonify({'error': str(exc)}), 500
            response = jsonify({
                'published_digest': receipt.content_digest,
                'artifact_url': (
                    f'/api/v1/installation-profiles/{receipt.content_digest}/artifact'
                ),
                'selected': False,
                'revision': draft['revision'],
                'receipt': receipt.to_dict(),
            })
            response.headers['ETag'] = f'"{draft["revision"]}"'
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route(
            '/api/v1/installation-profiles/<digest>/artifact',
            methods=['GET'],
        )
        def api_installation_profile_artifact(digest: str):
            """Serve one validated content-addressed LGIP artifact read-only."""
            try:
                resolved = self._installation_profile_authoring().library.resolve(digest)
            except InstallationProfileNotFoundError as exc:
                return jsonify({'error': str(exc)}), 404
            except InstallationProfileAuthoringError as exc:
                return jsonify({'error': str(exc)}), 400
            except InstallationProfileLibraryError as exc:
                return jsonify({'error': str(exc)}), 500
            if request.if_none_match.contains(resolved.content_digest):
                response = self.app.response_class(status=304)
            else:
                response = self.app.response_class(
                    resolved.encoded,
                    status=200,
                    mimetype='application/octet-stream',
                )
            response.headers['ETag'] = f'"{resolved.content_digest}"'
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            response.headers['X-Installation-Profile-Digest'] = resolved.content_digest
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response

        @self.app.route('/api/painter/masks', methods=['GET'])
        def api_painter_get_masks():
            """Read-only compatibility view backed by one managed profile draft."""
            digest = request.args.get('profile_digest')
            try:
                return jsonify(self._load_painter_masks(digest))
            except InstallationProfileNotFoundError as exc:
                return jsonify({'error': str(exc)}), 404
            except InstallationProfileAuthoringError as exc:
                return jsonify({'error': str(exc)}), 400
            except InstallationProfileLibraryError as exc:
                return jsonify({'error': str(exc)}), 500

        @self.app.route('/api/painter/masks', methods=['POST'])
        def api_painter_save_masks():
            """Fail closed: legacy mask files are no longer an update authority."""
            response = jsonify({
                'error': (
                    'Direct mask saves are retired; use the revisioned managed '
                    'installation-profile draft and publish workflow'
                ),
                'code': 'managed_profile_required',
            })
            response.status_code = 405
            response.headers['Allow'] = 'GET'
            return response

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
            payload['resolved_profile_digest'] = profile.resolved_profile_digest
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

    def _activation_tokens(self) -> ActivationTokenStore:
        """Lazily open the durable hashed-token store only when Check is used."""

        if self._activation_token_store is None:
            self._activation_token_store = ActivationTokenStore(
                self.activation_token_store_path
            )
        return self._activation_token_store

    @staticmethod
    def _activation_unavailable() -> tuple[Any, int]:
        response = jsonify({
            'error': 'Guarded physical-wall activation is disabled on this server.',
            'code': 'activation_unavailable',
        })
        response.headers['Cache-Control'] = 'no-store'
        return response, 503

    def _deliver_activation_outbox(self, stored: Any) -> Dict[str, Any]:
        """Project one SQLite-committed activation into durable IPC idempotently."""

        command = stored.outbox_command
        status = stored.outbox_status
        if not isinstance(command, dict) or not isinstance(status, dict):
            raise ValueError('activation outbox is incomplete')
        activation_id = stored.activation_id
        existing_status = self.control_channel.read_activation_status(activation_id)
        if existing_status is None:
            existing_status = self.control_channel.write_activation_status(status)
        else:
            existing_status = normalize_scene_activation_status(existing_status)
        self.control_channel.enqueue_activation(command)
        self._activation_tokens().mark_outbox_delivered(activation_id)
        return existing_status

    def _recover_activation_outbox(self) -> None:
        required = (
            'read_activation_status', 'write_activation_status',
            'enqueue_activation',
        )
        if not all(callable(getattr(self.control_channel, name, None)) for name in required):
            return
        store = self._activation_token_store
        if store is None:
            return
        for pending in store.pending_outbox():
            try:
                self._deliver_activation_outbox(pending)
            except (FileExistsError, OSError, SceneValidationError, ValueError):
                # Remains pending in SQLite and is repaired on the next startup
                # or exact retry. Never pretend it reached the controller queue.
                continue

    def _activation_runtime_digests(
        self, catalog: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """Bind Check to the controller's authoritative runtime derivation."""

        result = manager_controller_runtime_digests(self.preview_manager)
        required = {
            f"{component.get('provider')}:{component.get('plugin_id')}"
            for component in catalog
            if isinstance(component.get('provider'), str)
            and isinstance(component.get('plugin_id'), str)
        }
        missing = sorted(required - result.keys())
        if missing:
            raise SceneValidationError(
                'controller runtime identity is unavailable for '
                + ', '.join(missing)
            )
        return result

    @staticmethod
    def _activation_controller_identity(status: Dict[str, Any]) -> tuple[str, int, Optional[str]]:
        session_id = status.get('controller_session_id')
        state_revision = status.get('controller_state_revision')
        active_identity = status.get('active_identity')
        current_identity_digest = status.get('current_identity_digest')
        if current_identity_digest is None and isinstance(active_identity, dict):
            current_identity_digest = active_identity.get(
                'current_identity', active_identity.get('current_identity_digest')
            )
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError('controller session identity is unavailable')
        if (
            isinstance(state_revision, bool)
            or not isinstance(state_revision, int)
            or state_revision < 0
        ):
            raise RuntimeError('controller state revision is unavailable')
        if current_identity_digest is not None and (
            not isinstance(current_identity_digest, str)
            or re.fullmatch(r'[0-9a-f]{64}', current_identity_digest) is None
        ):
            raise RuntimeError('controller active identity is invalid')
        if isinstance(active_identity, dict):
            derived_identity_digest = canonical_json_sha256(active_identity)
            if current_identity_digest != derived_identity_digest:
                raise RuntimeError(
                    'controller active identity digest does not match its payload'
                )
        return session_id, state_revision, current_identity_digest

    def _require_activation_release_identity(
        self, status: Dict[str, Any]
    ) -> str:
        """Require web and controller to execute one immutable release."""

        web_release = self.release_id
        controller_release = status.get('release_id')
        if (
            not isinstance(web_release, str)
            or re.fullmatch(r'[0-9a-f]{64}', web_release) is None
        ):
            raise RuntimeError(
                'web release identity is unavailable for guarded activation'
            )
        if (
            not isinstance(controller_release, str)
            or re.fullmatch(r'[0-9a-f]{64}', controller_release) is None
        ):
            raise RuntimeError(
                'controller release identity is unavailable for guarded activation'
            )
        if controller_release != web_release:
            raise RuntimeError(
                'web and controller release identities do not match'
            )
        return web_release

    def _canonical_activation_global_settings(self, payload: Any) -> Dict[str, Any]:
        settings = normalize_global_settings_payload(payload)
        vibe = settings['vibe']
        # Resolve through the authoritative registry so a syntactically valid
        # but invented vibe digest cannot acquire a server Check token.
        self._canonical_vibe_state({
            **vibe,
            'revision': settings['revision'],
        })
        return settings

    @staticmethod
    def _browser_activation_evidence(
        value: Any, *, binding_digest: str
    ) -> Optional[Dict[str, Any]]:
        """Adapt one completed local Check into advisory browser evidence."""
        if not isinstance(value, dict) or value.get('source') != 'browser':
            return None
        frame_time = value.get('frameTimeMs')
        cadence = value.get('cadence')
        electrical = value.get('electrical')
        if not all(isinstance(item, dict) for item in (
            frame_time, cadence, electrical,
        )):
            return None
        environment = value.get('environment')
        user_agent = (
            environment.get('userAgent')
            if isinstance(environment, dict)
            else None
        )
        if not isinstance(user_agent, str) or not user_agent.strip():
            user_agent = 'browser environment not reported'
        current_mean = electrical.get('meanCurrentAmps')
        current_peak = electrical.get('peakCurrentAmps')
        nominal_voltage = electrical.get('nominalVoltageVolts')
        if any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in (current_mean, current_peak, nominal_voltage)
        ):
            return None
        return {
            'source': 'browser',
            'binding_digest': binding_digest,
            'captured_at': value.get('capturedAt'),
            'environment': f'Browser local Check: {user_agent}',
            'sample_count': value.get('sampleCount'),
            'frame_time_ms': {
                'mean': frame_time.get('mean'),
                'p95': frame_time.get('p95'),
                'p99': frame_time.get('p99'),
                'max': frame_time.get('max'),
            },
            'cadence': {
                'observed_fps': cadence.get('observedFps'),
                'missed_frame_ratio': cadence.get('missedFrameRatio'),
                'changed_frame_ratio': cadence.get('changedFrameRatio'),
            },
            'electrical': {
                'kind': 'uncalibrated_estimate',
                'budget_digest': None,
                'brightness': electrical.get('brightness'),
                'voltage_v': {
                    'mean': nominal_voltage,
                    'p95': nominal_voltage,
                    'p99': nominal_voltage,
                    'max': nominal_voltage,
                },
                'current_a': {
                    'mean': current_mean,
                    'p95': current_peak,
                    'p99': current_peak,
                    'max': current_peak,
                },
            },
        }

    def _activation_qualification(
        self,
        *,
        document: Dict[str, Any],
        settings: Dict[str, Any],
        controller_status: Dict[str, Any],
        browser_evidence: Any,
        runtime_identity: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Build and evaluate one exact, server-owned qualification record."""
        geometry = self._normalize_led_info({
            'strip_count': self.preview_manager.controller.strip_count,
            'leds_per_strip': self.preview_manager.controller.leds_per_strip,
            'total_leds': self.preview_manager.controller.total_leds,
        })
        if geometry is None:
            raise SceneValidationError('qualification geometry is unavailable')
        binding = {
            'browser_scene': {
                'revision': document['revision'],
                'digest': canonical_json_sha256(document),
            },
            'installation_profile_digest': document[
                'installation_profile'
            ]['digest'],
            'global_settings': {
                'revision': settings['revision'],
                'digest': canonical_json_sha256(settings),
            },
            'geometry': {
                'strip_count': geometry['strip_count'],
                'leds_per_strip': geometry['leds_per_strip'],
            },
            'brightness': settings['output']['brightness'],
            'vibe': settings['vibe'],
            'plant_modifiers': settings['plant_modifiers'],
            'target_fps': settings['output']['target_fps'],
        }
        binding_digest = activation_qualification_binding_digest(binding)
        evidence = []
        browser = self._browser_activation_evidence(
            browser_evidence, binding_digest=binding_digest
        )
        if browser is not None:
            evidence.append(browser)
        try:
            retained_envelope = load_target_qualification_evidence(
                self.target_qualification_evidence_path
            )
        except QualificationValidationError:
            # Missing, malformed, stale-binding, and partial evidence all fail
            # closed as missing target evidence in the qualification result.
            retained_envelope = None
        if (
            retained_envelope is not None
            and retained_envelope['binding_digest'] == binding_digest
            and retained_envelope['runtime_identity'] == runtime_identity
        ):
            transport_digest = canonical_json_sha256(
                retained_envelope['transport']
            )
            for item in retained_envelope['evidence']:
                retained_item = dict(item)
                if retained_item['source'] == 'receiver':
                    if retained_item['transport_digest'] != transport_digest:
                        raise QualificationValidationError(
                            'receiver transport digest does not match normalized proof'
                        )
                evidence.append(retained_item)

        budget = load_installation_qualification_budget()
        record = {
            'schema': QUALIFICATION_RECORD_SCHEMA,
            'schema_version': QUALIFICATION_RECORD_VERSION,
            'revision': 1,
            'qualification_version': 'server-check-v2',
            'binding': binding,
            'budget': {
                'revision': budget['revision'],
                'digest': installation_qualification_budget_digest(budget),
            },
            'evidence': evidence,
        }
        result = evaluate_activation_qualification(
            record, budget, now_ms=int(time.time() * 1000)
        )
        return record, result

    def _activation_basis_for_request(
        self,
        *,
        browser_scene: Any,
        global_settings: Any,
        browser_evidence: Any = None,
        expires_at_ms: int,
        status: Optional[Dict[str, Any]] = None,
    ) -> tuple[
        Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any],
        Dict[str, Any],
    ]:
        catalog = self._browser_scene_catalog()
        document, host_scene = self._validated_browser_scene_document(
            browser_scene, purpose='activation'
        )
        settings = self._canonical_activation_global_settings(global_settings)
        controller_status = (
            dict(status) if isinstance(status, dict)
            else dict(self.control_channel.read_status() or {})
        )
        release_id = self._require_activation_release_identity(controller_status)
        session_id, state_revision, current_identity = (
            self._activation_controller_identity(controller_status)
        )
        if current_identity is None:
            raise RuntimeError(
                'controller runtime identity is unavailable for guarded activation'
            )
        qualification_record, qualification_result = self._activation_qualification(
            document=document,
            settings=settings,
            controller_status=controller_status,
            browser_evidence=browser_evidence,
            runtime_identity={
                'release_id': release_id,
                'controller_session_id': session_id,
                'controller_state_revision': state_revision,
                'current_identity_digest': current_identity,
            },
        )
        basis = build_scene_activation_basis(
            browser_scene=document,
            catalog=catalog,
            global_settings=settings,
            controller_runtime_digests=self._activation_runtime_digests(catalog),
            controller_session_id=session_id,
            controller_state_revision=state_revision,
            current_identity_digest=current_identity,
            qualification_version=qualification_record['qualification_version'],
            qualification_record_digest=activation_qualification_record_digest(
                qualification_record
            ),
            expires_at=expires_at_ms,
            host_scene=host_scene,
            provider_policy=self._scene_provider_policy(),
        )
        return (
            basis, host_scene, settings, qualification_record,
            qualification_result,
        )

    @staticmethod
    def _guarded_scene_error(message: str) -> tuple[Any, int]:
        return jsonify({
            'error': message,
            'code': 'guarded_activation_required',
            'check_url': '/api/v1/scene/checks',
            'activation_url': '/api/v1/scene',
        }), 428

    def _studio_next_composer_eligibility(
        self, component: Dict[str, Any], *, provider_collision: bool = False
    ) -> Dict[str, Any]:
        """Return read-only Composer handoff eligibility, never activation authority."""

        def decision(code: str, reason: str, *, eligible: bool = False) -> Dict[str, Any]:
            return {
                # Retained for older Studio clients; it must never regain authority.
                'take_look_enabled': False,
                'composer_check_eligible': eligible,
                'code': code,
                'reason': reason,
            }

        if provider_collision:
            return decision(
                'provider_collision',
                (
                    'This plugin ID occurs under multiple providers. Legacy presets '
                    'and previews cannot be assigned safely.'
                ),
            )
        provider = component.get('provider')
        if provider != 'python':
            return decision(
                'unsupported_provider',
                'Composer Look handoff supports ready Host Python backgrounds only.',
            )
        if component.get('role') != 'background':
            return decision(
                'unsupported_role',
                'Composer Look handoff requires a background component.',
            )
        if component.get('gallery') != 'show' or component.get('is_test') is True:
            return decision(
                'developer_only',
                'Test and developer components are available through Tools only.',
            )

        readiness_values = {
            str(component.get(field) or '').strip().casefold().replace('-', '_')
            for field in ('status', 'availability', 'readiness')
        }
        forbidden_readiness = {
            'build_only', 'unavailable', 'quarantined', 'disabled', 'error',
        }
        blocked = sorted(readiness_values & forbidden_readiness)
        if blocked:
            return decision(
                blocked[0], f"Component readiness is {blocked[0].replace('_', ' ')}."
            )

        compatibility = component.get('compatibility')
        if not isinstance(compatibility, dict):
            compatibility = {}
        if compatibility.get('composable') is not True:
            return decision(
                'not_composable',
                str(
                    compatibility.get('diagnostic')
                    or 'The component is not composable as a background.'
                ),
            )
        if compatibility.get('implementation_loaded') is not True:
            return decision(
                'build_only', 'The Host Python implementation is not loaded.'
            )

        getter = getattr(self.preview_manager, 'get_animation_info', None)
        try:
            loaded = getter(component.get('plugin_id')) if callable(getter) else None
        except (KeyError, TypeError, ValueError):
            loaded = None
        if not loaded:
            return decision(
                'implementation_unavailable',
                'The preview manager has not loaded this implementation.',
            )
        return decision(
            'guarded_activation_required',
            (
                'Preview is ready. Taking it live requires Composer Check and '
                'guarded activation.'
            ),
            eligible=True,
        )

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
            legacy_dir = self._legacy_animation_preset_dir(plugin_id)
            discovered = (
                list(legacy_dir.glob('*.json'))
                if legacy_dir is not None and legacy_dir.is_dir() else []
            )
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
            component['action'] = self._studio_next_composer_eligibility(
                component, provider_collision=collision
            )
            component['preset_keys'] = []

            for preset in self._list_animation_presets(plugin_id, provider):
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

    def _browser_composer_bootstrap(
        self, *, observe_installation_profile: bool = True
    ) -> Dict[str, Any]:
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
        runtime_digests: Dict[Path, str] = {}
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
            if provider == 'python':
                for name in LEGACY_PLANT_MASK_PATH_PARAMETERS:
                    schema.pop(name, None)
                    defaults.pop(name, None)
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

            if runtime.get('supported'):
                asset_url = runtime.get('asset_url')
                asset_path = (
                    self.project_root / 'web' / str(asset_url).lstrip('/')
                    if isinstance(asset_url, str)
                    else None
                )
                if asset_path is not None and asset_path.is_file():
                    runtime_digest = runtime_digests.get(asset_path)
                    if runtime_digest is None:
                        runtime_digest = hashlib.sha256(
                            asset_path.read_bytes()
                        ).hexdigest()
                        runtime_digests[asset_path] = runtime_digest
                    runtime['digest'] = runtime_digest
                else:
                    runtime['supported'] = False
                    runtime['reason'] = (
                        'The verified browser runtime asset is not available.'
                    )
                    runtime['digest'] = None
            else:
                runtime['digest'] = None

            preset_records: List[Dict[str, Any]] = []
            for summary in self._list_animation_presets(plugin_id, provider):
                preset_id = summary.get('preset_id')
                if not isinstance(preset_id, str):
                    continue
                payload = self._load_animation_preset(
                    plugin_id, preset_id, provider
                )
                if payload is None:
                    continue
                preset = json.loads(json.dumps(summary))
                preset.update({
                    'key': f'{provider}:{plugin_id}:{preset_id}',
                    'component_key': f'{provider}:{plugin_id}',
                    'provider': provider,
                    'plugin_id': plugin_id,
                    'params': self._browser_composer_params(
                        provider, payload['params']
                    ),
                    'preset_fingerprint': self._component_preset_fingerprint(payload),
                })
                preset_records.append(preset)

            component = {
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
                'interaction_capabilities': json.loads(json.dumps(
                    raw.get('interaction_capabilities') or {}
                )),
                'presentation': {
                    'timing_adapter': str(
                        (
                            (raw.get('vibe') or {}).get('timing_adapter')
                            if isinstance(raw.get('vibe'), dict)
                            else None
                        ) or 'legacy_speed_param'
                    ),
                    'vibe_color_policy': str(
                        (
                            (raw.get('vibe') or {}).get('color_policy')
                            if isinstance(raw.get('vibe'), dict)
                            else None
                        ) or 'preserve'
                    ),
                    'vibe_capabilities': json.loads(json.dumps(
                        (raw.get('vibe') or {}).get('capabilities') or []
                        if isinstance(raw.get('vibe'), dict)
                        else []
                    )),
                },
                'preview': self._studio_next_preview(
                    provider,
                    raw.get('preview'),
                    None if plugin_id in collisions else self._preview_metadata(plugin_id),
                ),
            }
            components.append(decorate_browser_component(
                component,
                browser_runtime=runtime,
                provider_collision=plugin_id in collisions,
            ))

        controller = self.preview_manager.controller
        strip_count = int(controller.strip_count)
        leds_per_strip = int(controller.leds_per_strip)
        profile_status_getter = getattr(
            self.preview_manager, 'get_installation_profile_status', None
        )
        profile_status = (
            profile_status_getter()
            if observe_installation_profile and callable(profile_status_getter)
            else {}
        )
        profile_digest = profile_status.get(
            'selected_digest', EMPTY_INSTALLATION_PROFILE_DIGEST
        )
        managed_profile_selected = (
            isinstance(profile_digest, str)
            and profile_digest != EMPTY_INSTALLATION_PROFILE_DIGEST
            and re.fullmatch(r'[0-9a-f]{64}', profile_digest) is not None
            and self.installation_profile_authoring is not None
        )
        profile_draft_url = (
            f'/api/v1/installation-profiles/{profile_digest}/draft'
            if managed_profile_selected else None
        )
        profile_publish_url = (
            f'/api/v1/installation-profiles/{profile_digest}/publish'
            if managed_profile_selected else None
        )
        profile_artifact_url = (
            f'/api/v1/installation-profiles/{profile_digest}/artifact'
            if managed_profile_selected else None
        )
        plant_state = (
            getattr(self.preview_manager, 'plant_modifier_state', None)
            if observe_installation_profile else None
        )
        plant_modifiers = (
            plant_state.to_dict()
            if isinstance(plant_state, PlantModifierState)
            else PlantModifierState.from_legacy(DEFAULT_PLANT_AWARE).to_dict()
        )
        return {
            'schema': 'ledgrid.browser-composer-bootstrap',
            'schema_version': 1,
            'generated_at': time.time(),
            'geometry': {
                'strip_count': strip_count,
                'leds_per_strip': leds_per_strip,
                'total_leds': strip_count * leds_per_strip,
            },
            'installation_profile': {
                'digest': profile_digest,
                'authority': 'host',
                'plant_modifiers': plant_modifiers,
                'draft_url': profile_draft_url,
                'publish_url': profile_publish_url,
                'artifact_url': profile_artifact_url,
            },
            'vibe_profiles': self._vibe_profile_catalog(),
            'global_control_contract': {
                'operator_speed_baseline': DEFAULT_ANIMATION_SPEED_SCALE,
                'plant_modifier_ids': list(PLANT_MODIFIER_IDS),
                'field_modifiers': sorted(FIELD_MODIFIERS),
                'surface_modifiers': sorted(SURFACE_MODIFIERS),
            },
            'components': components,
            'capabilities': {
                'rendering': 'browser_webassembly',
                'draft_storage': 'browser_local_storage',
                'checker': 'browser_worker',
                'live_wall_mutated': False,
                'framebuffer_readback': False,
                'server_actions': {
                    'activation_available': self.activation_enabled,
                    'activation_mode': self.activation_mode,
                    'connectivity_url': '/api/v1/composer/connectivity',
                    'bootstrap_url': (
                        '/api/v1/composer/bootstrap?catalog_only=1'
                    ),
                    'validate_import_url': '/api/v1/composer/presets/validate',
                    'save_component_preset_url': '/api/v1/composer/presets',
                    'save_scene_preset_url': '/api/v1/scene-presets',
                    'live_edit_component_url_template': (
                        '/api/v1/scene/components/{target}'
                    ),
                    'live_edit_available': True,
                    'validate_scene_url': '/api/v1/scene/validate',
                    'check_scene_url': '/api/v1/scene/checks',
                    'activate_scene_url': '/api/v1/scene',
                    'activation_status_url_template': (
                        '/api/v1/scene/activations/{activation_id}'
                    ),
                    'status_url': '/api/status',
                    'operations_status_url': '/api/v1/composer/operations/status',
                    'vibe_url': '/api/v1/vibe',
                    'plant_modifiers_url': '/api/config/plant-modifiers',
                    'brightness_url': '/api/config/brightness',
                    'target_fps_url': '/api/config/target-fps',
                    'operator_speed_url': '/api/config/animation-speed',
                    # Retained only as a read-only adapter for older Painter
                    # clients; it resolves the managed selected draft and is
                    # never a legacy-file authority.
                    'masks_url': '/api/painter/masks',
                    'installation_profile_draft_url': profile_draft_url,
                    'installation_profile_publish_url': profile_publish_url,
                    'installation_profile_artifact_url': profile_artifact_url,
                    'online_required': True,
                },
            },
            'diagnostics': [
                {
                    'code': 'provider_collision',
                    'plugin_id': plugin_id,
                    'providers': providers,
                    'message': (
                        'Only ambiguous legacy presets are withheld; '
                        'provider-qualified presets remain available.'
                    ),
                    'recovery': self._legacy_preset_recovery(plugin_id),
                }
                for plugin_id, providers in sorted(collisions.items())
                if (
                    (legacy_dir := self._legacy_animation_preset_dir(plugin_id))
                    is not None
                    and legacy_dir.is_dir()
                    and any(legacy_dir.glob('*.json'))
                )
            ],
        }

    def _browser_composer_component(
        self,
        *,
        component_key: Optional[str] = None,
        plugin_id: Optional[str] = None,
        provider: Optional[str] = None,
        catalog: Optional[List[Dict[str, Any]]] = None,
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
            item for item in (catalog if catalog is not None else self._component_catalog())
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

    @staticmethod
    def _browser_composer_params(
        provider: str, params: Any
    ) -> Dict[str, Any]:
        """Copy parameters while removing host-only mask paths from Python."""
        result = json.loads(json.dumps(params)) if isinstance(params, dict) else {}
        if provider == 'python':
            for name in LEGACY_PLANT_MASK_PATH_PARAMETERS:
                result.pop(name, None)
        return result

    @staticmethod
    def _reject_retired_browser_composer_params(
        provider: str, params: Any
    ) -> None:
        """Fail closed when a browser input injects a host filesystem path."""
        if provider != 'python' or not isinstance(params, dict):
            return
        retired = sorted(LEGACY_PLANT_MASK_PATH_PARAMETERS & params.keys())
        if retired:
            raise ValueError(
                'browser Composer rejects retired plant-mask path parameters '
                f"({', '.join(retired)}); use managed installation-profile geometry"
            )

    def _reject_retired_browser_scene_params(self, scene: Dict[str, Any]) -> None:
        components = [scene.get('background'), scene.get('known_python_fallback')]
        components.extend(
            overlay.get('component')
            for overlay in scene.get('overlays', [])
            if isinstance(overlay, dict)
        )
        for component in components:
            if not isinstance(component, dict):
                continue
            provider = component.get('provider')
            for field in ('parameter_overrides', 'resolved_parameters'):
                self._reject_retired_browser_composer_params(
                    provider, component.get(field)
                )

    def _browser_scene_catalog(self) -> List[Dict[str, Any]]:
        """Return catalog records with runtime-bound browser capabilities."""
        return self._browser_composer_bootstrap()['components']

    def _validated_browser_scene_document(
        self, payload: Any, *, purpose: str
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        catalog = self._browser_scene_catalog()
        document = normalize_browser_scene_document(
            payload, catalog=catalog, purpose=purpose
        )
        if purpose == 'activation':
            profile_digest = document['installation_profile']['digest']
            preflight = getattr(
                self.preview_manager, 'preflight_installation_profile', None
            )
            if callable(preflight):
                try:
                    preflight(profile_digest)
                except (KeyError, TypeError, ValueError) as exc:
                    raise SceneValidationError(
                        'browser scene.installation_profile.digest is not a '
                        f'managed installation profile: {exc}'
                    ) from exc
            elif profile_digest != EMPTY_INSTALLATION_PROFILE_DIGEST:
                raise SceneValidationError(
                    'browser scene.installation_profile.digest cannot be '
                    'resolved by this manager'
                )
        scene = browser_scene_to_host_scene(document, catalog=catalog)
        return document, scene

    def _validated_browser_composer_import(
        self, payload: Any, *, encoded_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """Normalize an uploaded preset into a composer draft without writes."""
        validate_bounded_browser_json(
            payload, label='uploaded preset', encoded_size=encoded_size
        )
        if not isinstance(payload, dict):
            raise ValueError('uploaded preset must be a JSON object')

        if payload.get('schema') == BROWSER_SCENE_SCHEMA:
            document, scene = self._validated_browser_scene_document(
                payload, purpose='import'
            )
            background = document['background']
            return {
                'kind': 'browser_scene',
                'draft': {
                    'component_key': (
                        f"{background['provider']}:{background['component_id']}"
                    ),
                    'name': 'Imported scene',
                    'description': '',
                    'params': dict(background['parameters']),
                    'browser_scene': document,
                    'scene': scene,
                },
            }

        if payload.get('schema') == SCENE_PRESET_SCHEMA:
            if payload.get('schema_version') != SCENE_PRESET_VERSION:
                raise ValueError('unsupported scene preset schema version')
            raw_scene = payload.get('scene')
            browser_document = None
            if (
                isinstance(raw_scene, dict)
                and raw_scene.get('schema') == BROWSER_SCENE_SCHEMA
            ):
                browser_document, scene = self._validated_browser_scene_document(
                    raw_scene, purpose='import'
                )
            else:
                scene = self._validated_scene_request(
                    raw_scene, browser_purpose='import'
                )
            self._reject_retired_browser_scene_params(scene)
            background = scene['background']
            browser_catalog = self._browser_scene_catalog()
            descriptor = self._browser_composer_component(
                plugin_id=background['plugin_id'],
                provider=background['provider'],
                catalog=browser_catalog,
            )
            capabilities = descriptor.get('browser_capabilities') or {}
            if capabilities.get('previewable') is not True:
                raise ValueError(
                    capabilities.get('reason')
                    or 'The imported scene background is not previewable.'
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
                    **(
                        {'browser_scene': browser_document}
                        if browser_document is not None else {}
                    ),
                },
            }

        if payload.get('schema') == 'ledgrid.scene-state' or 'scene' in payload:
            raise ValueError(
                'upload a ledgrid.scene-preset document, not a raw scene envelope'
            )
        params = payload.get('params')
        if not isinstance(params, dict):
            raise ValueError('component preset params must be an object')
        browser_catalog = self._browser_scene_catalog()
        descriptor = self._browser_composer_component(
            component_key=payload.get('component_key'),
            plugin_id=payload.get('plugin_id') or payload.get('animation'),
            provider=payload.get('provider'),
            catalog=browser_catalog,
        )
        capabilities = descriptor.get('browser_capabilities') or {}
        if capabilities.get('previewable') is not True:
            raise ValueError(
                capabilities.get('reason')
                or 'The imported component is not previewable.'
            )
        plugin_id = descriptor['plugin_id']
        provider = descriptor['provider']
        self._reject_retired_browser_composer_params(provider, params)
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
        validate_bounded_browser_json(payload, label='browser composer save')
        if not isinstance(payload, dict):
            raise ValueError('request body must be a JSON object')
        if (
            payload.get('schema') != 'ledgrid.browser-composer-save'
            or payload.get('schema_version') != 1
        ):
            raise ValueError('unsupported browser composer save schema')
        browser_catalog = self._browser_scene_catalog()
        descriptor = self._browser_composer_component(
            component_key=payload.get('component_key'), catalog=browser_catalog
        )
        capabilities = descriptor.get('browser_capabilities') or {}
        if capabilities.get('saveable') is not True:
            raise ValueError(
                capabilities.get('reason')
                or 'This component is not saveable from the browser composer.'
            )
        plugin_id = descriptor['plugin_id']
        provider = descriptor['provider']
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
        self._reject_retired_browser_composer_params(provider, params)
        error = self._validate_animation_params(plugin_id, params)
        if error:
            raise ValueError(error)
        overwrite = payload.get('overwrite', False)
        if not isinstance(overwrite, bool):
            raise ValueError('overwrite must be a boolean')

        existing = self._load_animation_preset(plugin_id, preset_id, provider)
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
        self._write_animation_preset(plugin_id, preset_id, preset, provider)
        preset['component_key'] = f'{provider}:{plugin_id}'
        preset['ownership'] = 'user'
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

    def _validated_scene_request(
        self, payload: Any, *, browser_purpose: str = 'activation'
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise SceneValidationError('request body must contain a scene object')
        if payload.get('schema') == BROWSER_SCENE_SCHEMA:
            _document, payload = self._validated_browser_scene_document(
                payload, purpose=browser_purpose
            )
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
                preset = self._load_animation_preset(
                    component_id, preset_id, component.get('provider', 'python')
                )
                if preset is None or preset.get('animation') != component_id:
                    raise SceneValidationError(
                        f"Component preset {component_id}/{preset_id} does not exist"
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
            preset = self._load_animation_preset(
                component_id, preset_id, component.get('provider', 'python')
            )
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

    def _validated_scene_update(
        self, target: str, value: Any, *, scene: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
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

        scene = scene if isinstance(scene, dict) else self._current_scene_payload()
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

    @staticmethod
    def _scene_preset_component_identities(scene: Dict[str, Any]) -> List[Dict[str, str]]:
        """Persist the exact provider/component tuple behind every saved scene."""
        if not isinstance(scene, dict):
            raise ValueError('scene preset must contain a scene object')
        browser_document = scene.get('schema') == BROWSER_SCENE_SCHEMA
        background = scene.get('background')
        overlays = scene.get('layers') if browser_document else scene.get('overlays')
        if not isinstance(background, dict) or not isinstance(overlays, list):
            raise ValueError('scene preset component identity is invalid')
        components = [background]
        for overlay in overlays:
            component = overlay.get('component') if isinstance(overlay, dict) else None
            if not isinstance(component, dict):
                raise ValueError('scene preset component identity is invalid')
            components.append(component)
        identities = []
        for component in components:
            provider = component.get('provider')
            plugin_id = component.get('component_id' if browser_document else 'plugin_id')
            if not isinstance(provider, str) or not isinstance(plugin_id, str):
                raise ValueError('scene preset component identity is invalid')
            identity = {'provider': provider, 'plugin_id': plugin_id}
            if isinstance(component.get('preset_id'), str):
                identity['preset_id'] = component['preset_id']
            identities.append(identity)
        return identities

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
        stored_identities = payload.get('component_identities')
        if stored_identities is not None:
            try:
                if stored_identities != self._scene_preset_component_identities(
                    payload.get('scene')
                ):
                    return None
            except (TypeError, ValueError):
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

    def _installation_profile_authoring(self) -> InstallationProfileAuthoring:
        service = self.installation_profile_authoring
        if not isinstance(service, InstallationProfileAuthoring):
            raise InstallationProfileAuthoringError(
                'Managed installation-profile authoring is unavailable'
            )
        return service

    @staticmethod
    def _installation_profile_if_match() -> Optional[str]:
        raw = request.headers.get('If-Match')
        if raw is None or not raw.strip():
            return None
        value = raw.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        return value

    def _selected_installation_profile_digest(self) -> str:
        getter = getattr(
            self.preview_manager, 'get_installation_profile_status', None
        )
        status = getter() if callable(getter) else {}
        digest = status.get('selected_digest') if isinstance(status, dict) else None
        if (
            not isinstance(digest, str)
            or digest == EMPTY_INSTALLATION_PROFILE_DIGEST
        ):
            raise InstallationProfileAuthoringError(
                'No managed installation profile is selected'
            )
        return digest

    def _load_painter_masks(
        self, digest: Optional[str] = None
    ) -> Dict[str, Any]:
        """Adapt one managed draft to the retired painter's read-only shape."""
        selected_digest = digest or self._selected_installation_profile_digest()
        draft = self._installation_profile_authoring().load(selected_digest)
        masks = draft['masks']
        assert isinstance(masks, dict)
        globe_regions = masks['globes']
        assert isinstance(globe_regions, dict)
        planter = sorted(
            index
            for name in GLOBE_REGION_ORDER
            for index in globe_regions[name]
        )
        return {
            'version': 1,
            'profile_digest': selected_digest,
            'revision': draft['revision'],
            'read_only': True,
            'draft_url': (
                f'/api/v1/installation-profiles/{selected_digest}/draft'
            ),
            'publish_url': (
                f'/api/v1/installation-profiles/{selected_digest}/publish'
            ),
            'led_info': draft['led_info'],
            'mask_types': [dict(mask_type) for mask_type in PAINTER_MASK_TYPES],
            'masks': {
                'foliage': list(masks['foliage']),
                'planter_bowls': planter,
            },
        }

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

    def _animation_preset_dir(
        self, animation_name: str, provider: str = 'python'
    ) -> Optional[Path]:
        """Resolve provider-qualified writable preset storage."""
        safe_name = self._sanitize_preset_id(animation_name)
        safe_provider = self._sanitize_preset_id(provider)
        if (
            not safe_name or safe_name != animation_name
            or not safe_provider or safe_provider != provider
        ):
            return None
        return self.animation_presets_dir / safe_provider / safe_name

    def _legacy_animation_preset_dir(self, animation_name: str) -> Optional[Path]:
        """Return the pre-provider storage location without ever writing it."""
        safe_name = self._sanitize_preset_id(animation_name)
        if not safe_name or safe_name != animation_name:
            return None
        return self.animation_presets_dir / safe_name

    def _legacy_preset_is_ambiguous(self, animation_name: str) -> bool:
        """An old plugin-id-only file cannot be attributed after a collision."""
        providers = {
            item.get('provider') for item in self._component_catalog()
            if item.get('plugin_id') == animation_name
            and isinstance(item.get('provider'), str)
        }
        return len(providers) > 1

    @staticmethod
    def _legacy_preset_recovery(animation_name: str) -> Dict[str, str]:
        """Stable user-facing migration paths for intentionally withheld data."""
        quoted = animation_name.replace('/', '')
        return {
            'export_url': f'/api/v1/presets/legacy/{quoted}/export',
            'discard_url': f'/api/v1/presets/legacy/{quoted}',
            'reimport_url': '/api/v1/composer/presets',
        }

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

    def _clock_overlay_conversion_preset_ids(self) -> Optional[frozenset[str]]:
        """Read the plugin-owned Clock conversion manifest without rewriting it."""
        manifest_path = (
            self.project_root / 'animation' / 'plugins' / 'clock_overlay'
            / 'clock_preset_conversion.v1.json'
        )
        manifest = self._read_json_file(manifest_path)
        if not isinstance(manifest, dict):
            return None
        policy = manifest.get('policy')
        entries = manifest.get('entries')
        if (
            manifest.get('schema') != 'ledgrid.clock-preset-conversion'
            or manifest.get('version') != 1
            or not isinstance(policy, dict)
            or policy.get('target_component') != 'clock_overlay'
            or not isinstance(entries, list)
            or len(entries) != 24
        ):
            return None
        preset_ids = {
            entry.get('target_preset_id')
            for entry in entries
            if isinstance(entry, dict) and entry.get('status') == 'converted'
            and isinstance(entry.get('target_preset_id'), str)
        }
        return frozenset(preset_ids) if len(preset_ids) == 24 else None

    def _animation_preset_path(
        self, animation_name: str, preset_id: str, provider: str = 'python'
    ) -> Optional[Path]:
        """Resolve a provider/component/preset path without traversal."""
        preset_dir = self._animation_preset_dir(animation_name, provider)
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
            'provider': payload.get('provider'),
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

    def _component_preset_ownership(
        self, animation_name: str, preset_id: str, provider: str
    ) -> str:
        """Classify the backing record without ever resolving a provider guess."""
        runtime_path = self._animation_preset_path(animation_name, preset_id, provider)
        if runtime_path is not None and runtime_path.is_file():
            return 'user'
        curated_dir = (
            self._curated_animation_preset_dir(animation_name)
            if provider == 'python' else None
        )
        if curated_dir is not None and (curated_dir / f'{preset_id}.json').is_file():
            return 'built_in'
        legacy_dir = (
            self._legacy_animation_preset_dir(animation_name)
            if not self._legacy_preset_is_ambiguous(animation_name) else None
        )
        if legacy_dir is not None and (legacy_dir / f'{preset_id}.json').is_file():
            return 'legacy'
        return 'unknown'

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

    def _list_animation_presets(
        self, animation_name: str, provider: str = 'python'
    ) -> List[Dict[str, Any]]:
        """List exact-provider presets; never guess an ambiguous legacy owner."""
        paths: Dict[str, Path] = {}
        curated_dir = (
            self._curated_animation_preset_dir(animation_name)
            if provider == 'python' else None
        )
        legacy_dir = (
            self._legacy_animation_preset_dir(animation_name)
            if not self._legacy_preset_is_ambiguous(animation_name) else None
        )
        runtime_dir = self._animation_preset_dir(animation_name, provider)
        for preset_dir in (curated_dir, legacy_dir, runtime_dir):
            if preset_dir is not None and preset_dir.is_dir():
                paths.update({path.stem: path for path in sorted(preset_dir.glob('*.json'))})

        if animation_name == 'clock_overlay' and provider == 'python':
            # The plugin-owned manifest is the source of truth for curated Clock
            # conversion. Runtime provider-qualified records remain independent.
            converted_ids = self._clock_overlay_conversion_preset_ids()
            paths = {
                preset_id: path for preset_id, path in paths.items()
                if path.parent != curated_dir
                or converted_ids is not None and preset_id in converted_ids
            }

        summaries: List[Dict[str, Any]] = []
        for path in paths.values():
            # Deployment recovery snapshots are controller bookkeeping, not
            # authored looks.  Never present them as Composer starting points.
            if path.stem == 'before-deploy':
                continue
            payload = self._read_json_file(path)
            if (
                payload
                and payload.get('animation', animation_name) == animation_name
                and payload.get('provider', provider) == provider
            ):
                payload.setdefault('preset_id', path.stem)
                payload.setdefault('name', path.stem)
                payload.setdefault('animation', animation_name)
                payload.setdefault('provider', provider)
                summary = self._animation_preset_summary(payload)
                summary['ownership'] = self._component_preset_ownership(
                    animation_name, str(payload['preset_id']), provider
                )
                summaries.append(summary)
        summaries.sort(
            key=lambda preset: str(preset.get('name') or preset.get('preset_id') or '').casefold()
        )
        return summaries

    def _load_animation_preset(
        self, animation_name: str, preset_id: str, provider: str = 'python'
    ) -> Optional[Dict[str, Any]]:
        """Read an exact-provider preset, safely migrating only unique legacy data."""
        path = self._animation_preset_path(animation_name, preset_id, provider)
        if path is None:
            return None
        if not path.is_file():
            legacy_dir = (
                self._legacy_animation_preset_dir(animation_name)
                if not self._legacy_preset_is_ambiguous(animation_name) else None
            )
            path = legacy_dir / path.name if legacy_dir is not None else path
        if not path.is_file() and provider == 'python':
            curated_dir = self._curated_animation_preset_dir(animation_name)
            path = curated_dir / path.name if curated_dir is not None else path
            if animation_name == 'clock_overlay':
                converted_ids = self._clock_overlay_conversion_preset_ids()
                if converted_ids is None or preset_id not in converted_ids:
                    return None
        if not path.is_file():
            return None
        payload = self._read_json_file(path)
        if not payload or not isinstance(payload.get('params'), dict):
            return None
        if (
            payload.get('animation', animation_name) != animation_name
            or payload.get('provider', provider) != provider
        ):
            return None
        payload.setdefault('preset_id', path.stem)
        payload.setdefault('name', path.stem)
        payload.setdefault('animation', animation_name)
        payload.setdefault('provider', provider)
        return payload

    def _write_animation_preset(
        self, animation_name: str, preset_id: str, payload: Dict[str, Any],
        provider: str = 'python',
    ):
        """Persist an animation preset atomically."""
        path = self._animation_preset_path(animation_name, preset_id, provider)
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
        project_root=preview_project_root,
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

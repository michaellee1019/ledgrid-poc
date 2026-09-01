#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations and adjusting parameters in
real time.
"""

import base64
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.plant_awareness import PlantModifierState
from animation.core.preview_assets import load_catalog, merge_catalogs
from animation.plugins.emoji_arranger import EmojiArrangerAnimation
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.fireworks import FireworksAnimation
from ipc.control_channel import FileControlChannel
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP
from drivers.frame_codec import (
    decode_frame_data,
    encode_frame_data,
    FRAME_ENCODING_NAME,
)
from web.preview_worker import RuntimePreviewWorker
from animation.core.scene_runtime import CanonicalSceneRuntimeError
from ipc.scene_contract import LocalSceneAdapter, SceneContractError, normalize_composer_scene
from web.composer_component_editor import editor_catalog
from web.live_scene_state import LiveSceneBlocked, LiveSceneStale, LiveSceneState
from web.composer_library_state import ComposerLibraryState, ComposerLibraryStateError
from web.composer_component_presets import ComponentPresetCatalog
from web.scene_look_store import SceneLookStore, SceneLookStoreError
from web.starter_looks import get_starter, list_starters
from web.working_draft_store import WorkingDraftStore, WorkingDraftError
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


COMPOSER_SHELL_VERSION = "composer-shell-v8"

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


class _ComposerLocalControlChannel:
    """In-memory Scene-v1 control sink used by the local Composer demo.

    This is deliberately separate from the application's historical controller
    channel: Composer activation records a checked command for inspection but
    cannot reach a wall, receiver, camera, or deployment service.
    """

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def send_command(self, action: str, **data: Any) -> dict[str, Any]:
        command = {"action": action, **data}
        self.commands.append(command)
        return command

class AnimationWebInterface:
    """Web interface for animation management"""

    def __init__(self, control_channel: FileControlChannel,
                 preview_manager: AnimationManager,
                 host: str = '0.0.0.0',
                 port: int = 5000,
                 local_mode: bool = False):
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
        self.project_root = Path(__file__).resolve().parents[1]
        self.painter_presets_dir = self.project_root / "presets" / "frame_painter"
        self.animation_presets_dir = self.project_root / "presets" / "animations"
        self.foliage_mask_path = self.project_root / "config" / "plant_pixel_map_32x138.json"
        self.planter_mask_path = self.project_root / "config" / "plant_globe_map_32x138.json"
        self.deployment_status_path = self.project_root / "run_state" / "deployment.json"
        self.generated_preview_dir = (
            self.project_root / "web" / "static" / "generated" / "animation-previews"
        )
        self.runtime_preview_dir = self.project_root / "run_state" / "animation_previews"
        # Composer preview and publication use the same current Scene v2
        # catalog.  Preview owns no wall channel and therefore remains inert.
        self.composer_catalog = current_component_catalog()
        self.composer_presets = ComponentPresetCatalog(
            self.project_root,
            {FireworksAnimation.COMPONENT_ID: FireworksAnimation._normalized_parameters},
        )
        self.composer_adapter = LocalSceneAdapter(self.composer_catalog)
        self.composer_control = _ComposerLocalControlChannel()
        self.composer_live = LiveSceneState(
            self.composer_catalog, self.composer_adapter, self.composer_control,
        )
        self.composer_looks = SceneLookStore(self.project_root / "run_state" / "composer_looks.json")
        self.composer_library = ComposerLibraryState(self.project_root / "run_state" / "composer_library.json")
        self.working_draft = WorkingDraftStore(self.project_root / 'run_state' / 'composer_draft.json')
        # A saved look is editable only while it remains the opened user look.
        # Built-ins have no id here, which makes Save require Save As.
        self._composer_opened_look_id: str | None = None
        self.composer_preview = ComposerFinalPreview(self.composer_catalog, self.project_root)
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

        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register Flask routes"""
        
        @self.app.route('/')
        def index():
            """The one local authoring surface for the first Composer slice."""
            return render_template('composer.html', shell_version=COMPOSER_SHELL_VERSION)

        @self.app.route('/composer-sw.js')
        def composer_service_worker():
            response = send_from_directory(
                self.project_root / 'web' / 'static' / 'composer',
                'composer_sw.js',
                mimetype='application/javascript',
            )
            response.headers['Service-Worker-Allowed'] = '/'
            response.headers['Cache-Control'] = 'no-cache'
            return response

        @self.app.route('/api/composer/status')
        def api_composer_status():
            """Read current desired/observed Scene v2 publication state."""
            return jsonify(self._composer_status_payload(request.args.get('client_id')))

        @self.app.route('/api/composer/components')
        def api_composer_components():
            """Return the closed local chooser from qualified descriptors."""
            return jsonify(editor_catalog(self.composer_catalog))

        @self.app.route('/api/composer/components/<component_id>/presets')
        def api_composer_component_presets(component_id: str):
            """Read authored component choices without treating them as Looks."""
            try:
                return jsonify({'component_id': component_id, 'presets': self.composer_presets.choices(component_id)})
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/draft')
        def api_composer_draft():
            try:
                value = self.working_draft.get()
                if value is not None:
                    canonical = self._composer_recovery_scene(value['scene'])
                    if canonical.identity.to_dict() != value['basis']:
                        raise WorkingDraftError('Crash recovery no longer matches its basis; discard it.')
                    self._composer_opened_look_id = value['opened_look_id']
                return jsonify({'draft': value})
            except (WorkingDraftError, SceneContractError, SceneLookStoreError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/recovery')
        def api_composer_recovery():
            try:
                status = self._composer_status_payload(
                    request.args.get('client_id'), include_current_scene=True,
                )
                current_scene = status.pop('current_scene')
                if current_scene is not None:
                    return jsonify({'recovery': {
                        'scene': current_scene, 'basis': status['current'],
                        'opened_look_id': self._composer_opened_look_id,
                        'authoritative': True,
                    }, 'status': status})
                value = self.working_draft.get()
                if value is None:
                    return jsonify({'recovery': None, 'status': status})
                canonical = self._composer_recovery_scene(value['scene'])
                if canonical.identity.to_dict() != value['basis']:
                    raise WorkingDraftError('Current scene recovery no longer matches its basis.')
                self._composer_opened_look_id = value['opened_look_id']
                return jsonify({'recovery': {**value, 'authoritative': False}, 'status': status})
            except (WorkingDraftError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload(request.args.get('client_id'))}), 400

        @self.app.route('/api/composer/draft', methods=['POST','DELETE'])
        def api_composer_change_draft():
            try:
                if request.method == 'DELETE':
                    self.working_draft.discard()
                    self._composer_opened_look_id = None
                    return jsonify({'discarded': True})
                raise WorkingDraftError('Crash recovery is updated only after a valid Scene v2 edit.')
            except (WorkingDraftError, SceneContractError, TypeError, ValueError) as exc: return jsonify({'error':str(exc)}),400

        @self.app.route('/api/composer/preview', methods=['POST'])
        def api_composer_preview():
            """Render one inert, installed-final Scene v2 frame."""
            try:
                return jsonify(self._composer_preview_payload(request.get_json(silent=True)))
            except (CanonicalSceneRuntimeError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/looks')
        def api_composer_looks():
            try:
                return jsonify({'looks': self.composer_looks.list()})
            except SceneLookStoreError as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/library')
        def api_composer_library():
            """Project the immutable starters and current local looks into one library."""
            try:
                return jsonify(ComposerLibraryState.project(
                    self.composer_library.get(), self._composer_library_items(),
                ))
            except (ComposerLibraryStateError, SceneLookStoreError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/library/favorites', methods=['POST', 'DELETE'])
        def api_composer_library_favorites():
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'reference'}:
                    raise ComposerLibraryStateError('Choose one library item.')
                reference = self._composer_library_reference(payload['reference'])
                state = (self.composer_library.favorite(reference)
                         if request.method == 'POST' else self.composer_library.unfavorite(reference))
                return jsonify(ComposerLibraryState.project(state, self._composer_library_items()))
            except (ComposerLibraryStateError, SceneLookStoreError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/library/recents', methods=['POST'])
        def api_composer_library_recents():
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'reference'}:
                    raise ComposerLibraryStateError('Choose one library item.')
                reference = self._composer_library_reference(payload['reference'])
                return jsonify(ComposerLibraryState.project(
                    self.composer_library.revisit(reference), self._composer_library_items(),
                ))
            except (ComposerLibraryStateError, SceneLookStoreError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/library/preflight', methods=['POST'])
        def api_composer_library_preflight():
            """Verify a referenced library action can remain local before it begins."""
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'reference'}:
                    raise ComposerLibraryStateError('Choose one library item.')
                reference = self._composer_library_reference(payload['reference'])
                self.composer_library.get()
                return jsonify({'reference': reference})
            except (ComposerLibraryStateError, SceneLookStoreError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/library/cards', methods=['POST'])
        def api_composer_library_card():
            """Render one current library item without opening or recording it."""
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'reference'}:
                    raise ComposerLibraryStateError('Choose one library item.')
                reference = self._composer_library_reference(payload['reference'])
                return jsonify(self._composer_library_card_payload(reference))
            except (CanonicalSceneRuntimeError, ComposerLibraryStateError, SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/starters')
        def api_composer_starters(): return jsonify({'starters': list_starters()})

        @self.app.route('/api/composer/starters/<starter_id>')
        def api_composer_starter(starter_id):
            try: return jsonify({'starter': self._composer_starter(get_starter(starter_id))})
            except ValueError as exc: return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/starters/<starter_id>/remix', methods=['POST'])
        def api_composer_remix_starter(starter_id):
            payload = request.get_json(silent=True) or {}
            try:
                self._composer_starter(get_starter(starter_id))
                if set(payload) != {'name', 'draft'}: raise SceneLookStoreError('A remix needs a name and current draft.')
                canonical = self._composer_canonical(payload['draft'])
                # Validate local library state before saving so corrupt state cannot
                # create a saved look while this explicit remix cannot be recorded.
                self.composer_library.get()
                look = self._composer_look_payload(self.composer_looks.save(payload['name'], canonical))
                self.composer_library.revisit({'kind': 'look', 'id': look['id']})
                return jsonify({'look': look})
            except (ComposerLibraryStateError, ValueError, SceneLookStoreError, SceneContractError, TypeError) as exc: return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/looks', methods=['POST'])
        def api_composer_save_look():
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'name', 'scene'}:
                    raise SceneLookStoreError('Save As needs a name and current Scene v2.')
                canonical = self._composer_canonical({'origin': 'composer', 'scene': payload['scene']})
                look = self.composer_looks.save_as(payload['name'], canonical)
                self._persist_composer_recovery(canonical, look['id'])
                return jsonify({'look': self._composer_look_payload(look)})
            except (SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/looks/<look_id>')
        def api_composer_open_look(look_id: str):
            try:
                return jsonify({'look': self._composer_look_payload(self.composer_looks.get(look_id))})
            except (SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/looks/<look_id>/open', methods=['POST'])
        def api_composer_select_look(look_id: str):
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) - {'client_id', 'mutation_id', 'client_sequence'}:
                    raise ValueError('look selection contains unknown fields')
                look = self._composer_look_payload(self.composer_looks.get(look_id))
                status = self._composer_submit_scene(
                    look['scene'], client_id=payload.get('client_id', 'composer'),
                    mutation_id=payload.get('mutation_id'), client_sequence=payload.get('client_sequence'),
                    opened_look_id=look['id'], preserve_opened_look=False,
                )
                self.composer_library.revisit({'kind': 'look', 'id': look['id']})
                return jsonify({'look': look, 'status': status})
            except LiveSceneStale as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 409
            except (ComposerLibraryStateError, SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 400

        @self.app.route('/api/composer/built-ins/open', methods=['POST'])
        def api_composer_select_builtin():
            """Open a current Scene v2 built-in without making it saveable.

            Built-in catalogs are supplied by the composition chooser. This
            endpoint is the explicit selection boundary that clears any prior
            user-look save target while retaining ordinary live/stopped state.
            """
            payload = request.get_json(silent=True) or {}
            try:
                allowed = {'scene', 'client_id', 'mutation_id', 'client_sequence'}
                if set(payload) - allowed or 'scene' not in payload:
                    raise ValueError('built-in selection needs a Scene v2 and optional client metadata')
                status = self._composer_submit_scene(
                    payload['scene'], client_id=payload.get('client_id', 'composer'),
                    mutation_id=payload.get('mutation_id'), client_sequence=payload.get('client_sequence'),
                    opened_look_id=None, preserve_opened_look=False,
                )
                return jsonify({'builtin': True, 'status': status})
            except LiveSceneStale as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 409
            except (SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 400

        @self.app.route('/api/composer/looks/save', methods=['POST'])
        def api_composer_save_opened_look():
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'scene'}:
                    raise SceneLookStoreError('Save needs the current Scene v2.')
                if self._composer_opened_look_id is None:
                    raise SceneLookStoreError('This built-in look is immutable; use Save As.')
                canonical = self._composer_canonical({'origin': 'composer', 'scene': payload['scene']})
                look = self.composer_looks.update(self._composer_opened_look_id, canonical)
                self._persist_composer_recovery(canonical, look['id'])
                return jsonify({'look': self._composer_look_payload(look)})
            except (SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 409

        @self.app.route('/api/composer/looks/import-legacy', methods=['POST'])
        def api_composer_import_legacy_looks():
            """One explicit, all-or-nothing import of reviewed legacy exports."""
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'looks'}:
                    raise SceneLookStoreError('Legacy import needs only its reviewed looks.')
                imported = self.composer_looks.import_legacy_once(payload['looks'], self._translate_legacy_look)
                return jsonify({'looks': [self._composer_look_payload(look) for look in imported]})
            except (SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/looks/<look_id>/duplicate', methods=['POST'])
        def api_composer_duplicate_look(look_id: str):
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'name'}:
                    raise SceneLookStoreError('A duplicate needs a new name.')
                return jsonify({'look': self._composer_look_payload(self.composer_looks.duplicate(look_id, payload['name']))})
            except (SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/looks/<look_id>', methods=['PATCH', 'PUT', 'DELETE'])
        def api_composer_change_look(look_id: str):
            try:
                if request.method == 'DELETE':
                    # Composer's only library/looks compatibility seam: reject a
                    # corrupt library before changing the saved-look store, then
                    # remove this deleted look from persistent library references.
                    self.composer_library.get()
                    if self._composer_opened_look_id == look_id:
                        self._clear_opened_look_recovery()
                    self.composer_looks.delete(look_id)
                    self.composer_library.prune_look(look_id)
                    return jsonify({'deleted': look_id})
                payload = request.get_json(silent=True) or {}
                if set(payload) == {'name'}:
                    return jsonify({'look': self._composer_look_payload(self.composer_looks.rename(look_id, payload['name']))})
                if set(payload) == {'scene'}:
                    canonical = self._composer_canonical({'origin': 'composer', 'scene': payload['scene']})
                    look = self.composer_looks.update(look_id, canonical)
                    if self._composer_opened_look_id == look_id:
                        self._persist_composer_recovery(canonical, look_id)
                    return jsonify({'look': self._composer_look_payload(look)})
                raise SceneLookStoreError('A look change needs a name or current Scene v2.')
            except (ComposerLibraryStateError, SceneLookStoreError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400

        @self.app.route('/api/composer/check', methods=['POST'])
        def api_composer_check():
            """Run advisory Scene v2 diagnostics without gating publication."""
            payload = request.get_json(silent=True)
            try:
                return jsonify(self.composer_live.check(payload))
            except (SceneContractError, ValueError, TypeError) as exc:
                return jsonify({
                    'error': str(exc), 'status': self._composer_status_payload(),
                }), 400

        @self.app.route('/api/composer/scene', methods=['POST'])
        def api_composer_scene():
            """Accept the newest valid scene and publish it when Composer is armed."""
            payload = request.get_json(silent=True) or {}
            try:
                allowed = {'origin', 'scene', 'client_id', 'mutation_id', 'client_sequence'}
                if set(payload) - allowed:
                    raise ValueError('scene request contains unknown fields')
                return jsonify(self._composer_submit_scene(
                    payload.get('scene'),
                    client_id=payload.get('client_id', 'composer'),
                    mutation_id=payload.get('mutation_id'),
                    client_sequence=payload.get('client_sequence'),
                ))
            except LiveSceneStale as exc:
                return jsonify({
                    'error': str(exc), 'status': self._composer_status_payload(),
                }), 409
            except (SceneContractError, ValueError, TypeError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 400
            except TimeoutError as exc:
                return jsonify({'error': str(exc) or 'Scene acknowledgement timed out.',
                                'status': self._composer_status_payload()}), 504

        @self.app.route('/api/composer/go-live', methods=['POST'])
        def api_composer_go_live():
            payload = request.get_json(silent=True) or {}
            try:
                return jsonify({'status': self.composer_live.go_live(client_id=payload.get('client_id', 'composer'))})
            except LiveSceneBlocked as exc:
                return jsonify({'error': str(exc), 'blockers': exc.blockers,
                                'status': self._composer_status_payload()}), 409
            except TimeoutError as exc:
                return jsonify({'error': str(exc) or 'Scene acknowledgement timed out.',
                                'status': self._composer_status_payload()}), 504

        @self.app.route('/api/composer/connection', methods=['POST'])
        def api_composer_connection():
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'connected'}:
                    raise ValueError('connection request must contain connected')
                return jsonify({'status': self.composer_live.set_connected(payload['connected'])})
            except (ValueError, TypeError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 400

        @self.app.route('/api/composer/undo-ack', methods=['POST'])
        def api_composer_undo_ack():
            """Acknowledge a remote scene revision after clearing local undo."""
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'client_id', 'revision'}:
                    raise ValueError('undo acknowledgement needs client_id and revision')
                return jsonify({'status': self.composer_live.acknowledge_undo_invalidation(
                    client_id=payload['client_id'], revision=payload['revision'],
                )})
            except (ValueError, TypeError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 400

        @self.app.route('/api/composer/stop', methods=['POST'])
        def api_composer_stop():
            """Stop output while retaining the current editable scene."""
            payload = request.get_json(silent=True) or {}
            try:
                return jsonify({'status': self.composer_live.stop(client_id=payload.get('client_id', 'composer'))})
            except TimeoutError as exc:
                return jsonify({'error': str(exc) or 'Stop acknowledgement timed out.', 'status': self._composer_status_payload()}), 504
            except (SceneContractError, ValueError, TypeError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 409
        
        @self.app.route('/api/animations')
        def api_list_animations():
            """API: Get list of available animations"""
            animations = self._sorted_animations()
            return jsonify(animations)

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
            preset = self._load_animation_preset(animation_name, preset_id)
            if not preset:
                return jsonify({'error': 'Preset not found'}), 404
            self.control_channel.send_command(
                'start', animation=animation_name, config=preset['params'],
                preset=self._animation_preset_selection(preset),
            )
            return jsonify({'success': True, 'preset': preset})

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
            config = request.get_json() or {}
            self.control_channel.send_command('start', animation=animation_name, config=config)
            # Controller polls periodically, so assume success if write succeeded
            success = True
            return jsonify({'success': success})
        
        @self.app.route('/api/stop', methods=['POST'])
        def api_stop_animation():
            """API: Stop current animation"""
            self.control_channel.send_command('stop')
            return jsonify({'success': True})

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

            self.control_channel.send_command('set_device_state', **command_data)
            return jsonify({'success': True, 'state': payload})
        
        @self.app.route('/api/status')
        def api_get_status():
            """API: Get current status"""
            return jsonify(self._status_payload())
        
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

        @self.app.route('/api/config/target-fps', methods=['POST'])
        def api_set_target_fps():
            payload = request.get_json(silent=True) or {}
            try:
                target_fps = int(payload.get('target_fps'))
            except (TypeError, ValueError):
                return jsonify({'error': 'target_fps must be an integer'}), 400
            if target_fps < 1 or target_fps > 200:
                return jsonify({'error': 'target_fps must be between 1 and 200'}), 400
            self.control_channel.send_command(
                'set_target_fps', target_fps=target_fps
            )
            return jsonify({'success': True, 'target_fps': target_fps})

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
            self.control_channel.send_command(
                'set_animation_speed_scale', animation_speed_scale=speed_scale
            )
            return jsonify({
                'success': True,
                'multiplier': multiplier,
                'animation_speed_scale': speed_scale,
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
            self.control_channel.send_command(
                'set_output_brightness', brightness=brightness
            )
            return jsonify({'success': True, 'brightness': brightness})

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
            self.control_channel.send_command(
                'set_plant_modifiers', plant_modifiers=serialized
            )
            return jsonify({'success': True, 'plant_modifiers': serialized})

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
                preview_data = self.preview_manager.get_animation_preview(animation_name)
                return jsonify(preview_data)
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
                params = request.get_json() or {}
                preview_data = self.preview_manager.get_animation_preview_with_params(animation_name, params)
                return jsonify(preview_data)
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
            return f"Unknown animation: {animation_name}"
        schema = info.get('parameters')
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

    def _composer_status_payload(self, client_id: Optional[str] = None, *, include_current_scene: bool = False) -> Dict[str, Any]:
        """Describe live-first current/desired/observed reconciliation.

        This stays explicitly local: the acknowledgement comes from the
        topology-neutral adapter and never claims a receiver or wall mutation.
        """
        return self.composer_live.snapshot(client_id=client_id, include_current_scene=include_current_scene)

    def _composer_submit_scene(
        self, scene: Any, *, client_id: str, mutation_id: str | None = None,
        client_sequence: int | None = None, opened_look_id: str | None = None,
        preserve_opened_look: bool = True,
    ) -> Dict[str, Any]:
        """Commit one valid scene, then atomically refresh hidden recovery.

        The live coordinator is the acceptance boundary.  Recovery is never
        updated before it accepts the same current-only Scene v2, which means a
        rejected edit cannot overwrite the last safely recoverable wall state.
        """
        canonical = self._composer_canonical({'origin': 'composer', 'scene': scene})
        next_opened_look_id = self._composer_opened_look_id if preserve_opened_look else opened_look_id
        try:
            result = self.composer_live.submit(
                {'origin': 'composer', 'scene': canonical.scene}, client_id=client_id,
                mutation_id=mutation_id, client_sequence=client_sequence,
            )
        except TimeoutError:
            # LiveSceneState accepts desired state before attempting output
            # acknowledgement. A timeout therefore leaves a valid newer scene
            # editable in recovery, even while observed output remains prior.
            self._persist_composer_recovery(canonical, next_opened_look_id)
            raise
        # An exact retry may describe an older mutation after another client
        # has already won. It is not a new local edit and must not roll crash
        # recovery or the opened-look cursor backward.
        if result['exact_retry']:
            return result
        self._persist_composer_recovery(canonical, next_opened_look_id)
        return result

    def _persist_composer_recovery(self, canonical, opened_look_id: str | None) -> dict[str, Any]:
        """Write only complete current scenes; output/calibration cannot enter."""
        self._composer_opened_look_id = opened_look_id
        return self.working_draft.save(
            canonical.scene, canonical.identity.to_dict(), opened_look_id, time.time(),
        )

    def _composer_recovery_scene(self, scene: Any):
        return self._composer_canonical({'origin': 'composer', 'scene': scene})

    def _clear_opened_look_recovery(self) -> None:
        """Clear a deleted look cursor before the look can cease to exist."""
        recovery = self.working_draft.get()
        if recovery is None:
            self._composer_opened_look_id = None
            return
        canonical = self._composer_recovery_scene(recovery['scene'])
        if canonical.identity.to_dict() != recovery['basis']:
            raise WorkingDraftError('Crash recovery no longer matches its basis; discard it.')
        self._persist_composer_recovery(canonical, None)

    def _translate_legacy_look(self, value: Dict[str, Any]):
        """Import only explicitly selected, pre-translated current-scene exports.

        Old Scene v1 payloads are intentionally not interpreted at runtime.
        A migration tool or operator must classify a useful legacy look first,
        then place its validated Scene v2 candidate in ``scene_v2``.
        """
        if set(value) != {'name', 'selected', 'scene_v2'} or not isinstance(value['selected'], bool):
            raise SceneLookStoreError('A legacy look must include name, selected, and scene_v2.')
        if not value['selected']:
            return None
        canonical = self._composer_canonical({'origin': 'composer', 'scene': value['scene_v2']})
        return value['name'], canonical

    def _composer_preview_payload(self, payload: Any) -> Dict[str, Any]:
        """Use an inert canonical runtime to render an authored Composer draft.

        The runtime's ``activate`` method only establishes an in-memory basis
        for its render instances.  This method deliberately does not use the
        token store, adapter, or historical controller channel.
        """
        if not isinstance(payload, dict) or set(payload) - {'origin', 'scene', 'preview'}:
            raise SceneContractError('preview request must contain Composer scene and optional preview time')
        preview = payload.get('preview', {})
        if not isinstance(preview, dict) or set(preview) - {'monotonic_elapsed', 'wall_time'}:
            raise SceneContractError('preview time is malformed')
        request_scene = {'origin': payload.get('origin'), 'scene': payload.get('scene')}
        canonical = self._composer_canonical(request_scene)
        elapsed = preview.get('monotonic_elapsed', time.monotonic())
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            raise SceneContractError('preview monotonic_elapsed must be numeric')
        raw_wall_time = preview.get('wall_time')
        if raw_wall_time is None:
            wall_time = datetime.now().astimezone()
        elif isinstance(raw_wall_time, str):
            try:
                wall_time = datetime.fromisoformat(raw_wall_time.replace('Z', '+00:00'))
            except ValueError as exc:
                raise SceneContractError('preview wall_time must be ISO-8601') from exc
            if wall_time.tzinfo is None:
                raise SceneContractError('preview wall_time must include a timezone')
        else:
            raise SceneContractError('preview wall_time must be ISO-8601')
        frame = self.composer_preview.render(canonical, float(elapsed), wall_time)
        return {
            'basis': frame.basis.to_dict(),
            'frame': {
                'width': 33,
                'height': 138,
                'encoding': 'rgb_u8_base64',
                'orientation': 'strip_major_led_zero_bottom',
                'pixels': base64.b64encode(frame.pixels.tobytes()).decode('ascii'),
            },
            'wall_mutations': 0,
            'widget_placements': {
                widget_id: {
                    'strip_translation': value.strip_translation,
                    'led_translation': value.led_translation,
                    'clamped': value.clamped,
                    'used_fallback': value.used_fallback,
                    'overlap_pixels': value.overlap_pixels,
                    'plant_overlap_pixels': value.plant_overlap_pixels,
                    'widget_overlap_pixels': value.widget_overlap_pixels,
                    'warning': value.warning,
                }
                for widget_id, value in frame.widget_placements.items()
            },
        }

    def _composer_library_card_payload(self, reference: dict[str, str]) -> Dict[str, Any]:
        """Return a fixed-time, inert frame for a current library reference.

        This is intentionally a read-only Composer-library seam: resolving an
        item must not open it into the draft, update recents/favorites, or use
        activation and reconciliation state.
        """
        preview_time = {
            'monotonic_elapsed': 12.0,
            'wall_time': '2026-08-31T12:00:00+00:00',
        }
        if reference['kind'] == 'starter':
            scene = self._composer_starter(get_starter(reference['id']))['scene']
        else:
            scene = self._composer_look_payload(self.composer_looks.get(reference['id']))['scene']
        preview = self._composer_preview_payload({
            'origin': 'composer', 'scene': scene, 'preview': preview_time,
        })
        return {'reference': reference, 'preview_time': preview_time, **preview}

    def _composer_look_payload(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Reject old, corrupt, or non-current whole-scene look records."""
        scene = record['scene']
        canonical = self._composer_canonical({'origin': 'composer', 'scene': scene})
        if canonical.scene != scene or canonical.identity.to_dict() != record['basis']:
            raise SceneLookStoreError('Saved look has changed or is corrupt; recreate it.')
        return {'id': record['id'], 'name': record['name'], 'basis': record['basis'], 'scene': scene}

    def _composer_library_items(self) -> list[dict[str, str]]:
        """Names are deliberately current projections, never copied into preferences."""
        starters = [{'kind': 'starter', **item} for item in list_starters()]
        looks = [{'kind': 'look', 'id': item['id'], 'name': item['name']} for item in self.composer_looks.list()]
        return [*starters, *looks]

    def _composer_library_reference(self, value: Any) -> dict[str, str]:
        """Require a current typed reference before a preference can be persisted."""
        reference = ComposerLibraryState._reference(value)
        if (reference['kind'], reference['id']) not in {
            (item['kind'], item['id']) for item in self._composer_library_items()
        }:
            raise ComposerLibraryStateError('That library item no longer exists.')
        return reference

    def _composer_starter(self, starter: Dict[str, Any]) -> Dict[str, Any]:
        """Reject invalid current built-ins before they reach selection."""
        self._composer_canonical({'origin': 'composer', 'scene': starter['scene']})
        return starter

    def _composer_canonical(self, request_value: Any):
        """Canonicalize the one current Scene v2 representation for Composer."""
        canonical = normalize_composer_scene(request_value, self.composer_catalog)
        # The current-only schema intentionally does not carry per-component
        # validator hooks. Keep Emoji Message's compact, local controls at the
        # Composer boundary so an invalid text or position cannot replace the
        # last live/recoverable scene before the final-preview runtime sees it.
        for widget in canonical.scene["widgets"]:
            component = widget["component"]
            if component["component_id"] == EmojiArrangerAnimation.COMPONENT_ID:
                EmojiArrangerAnimation._normalized_parameters(component["parameters"])
        if canonical.scene["animation"]["component_id"] == FireflySynchronyAnimation.COMPONENT_ID:
            FireflySynchronyAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == FireworksAnimation.COMPONENT_ID:
            FireworksAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        return canonical

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
        plugin_dir = loader.get_plugin_dir(animation_name) if loader is not None else None
        if plugin_dir is None:
            return None
        return plugin_dir / 'presets'

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
        return led_info

    def _status_payload(self, decode_frame: bool = False) -> Dict[str, Any]:
        """Normalize the controller status so every consumer sees the same structure."""
        raw_status = self.control_channel.read_status()
        if not raw_status:
            return self._empty_status()

        status = dict(raw_status)
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
            'timestamp': time.time()
        }


def create_app(control_channel: FileControlChannel = None,
               host: str = '0.0.0.0',
               port: int = 5000,
               strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
               animations_dir: str = None,
               animation_speed_scale: float = DEFAULT_ANIMATION_SPEED_SCALE,
               plant_aware: bool = DEFAULT_PLANT_AWARE):
    """Factory function to create the web application"""
    if control_channel is None:
        control_channel = FileControlChannel()

    # Preview-only controller keeps renderer and plugin listing in this process
    preview_controller = PreviewLEDController(strips, leds_per_strip)

    # Create animation manager (preview only, no hardware access)
    animation_manager = AnimationManager(
        preview_controller,
        plugins_dir=animations_dir,
        animation_speed_scale=animation_speed_scale,
        plant_aware=plant_aware,
        auto_start=False,
    )

    # Create web interface
    web_interface = AnimationWebInterface(control_channel, animation_manager, host=host, port=port)

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

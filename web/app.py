#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations and adjusting parameters in
real time.
"""

import base64
from copy import deepcopy
import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from flask import Flask, has_request_context, jsonify, redirect, render_template, request, send_from_directory

from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.installation_profile_runtime import EMPTY_INSTALLATION_PROFILE_DIGEST
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.plant_awareness import (
    FIELD_MODIFIERS,
    LEGACY_PLANT_MASK_PATH_PARAMETERS,
    PLANT_MODIFIER_IDS,
    SURFACE_MODIFIERS,
    PlantModifierState,
)
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.canopy_cup import CanopyCupAnimation
from animation.plugins.ascii_drop import AsciiDropAnimation
from animation.plugins.christmas_tree import ChristmasTreeAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.emoji_arranger import EmojiArrangerAnimation
from animation.plugins.emoji import EmojiAnimation
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.fireworks import FireworksAnimation
from animation.plugins.flame_burst import FlameBurstAnimation
from animation.plugins.fluid_tank import FluidTankAnimation
from animation.plugins.cyclic_reef import CyclicReefAnimation
from animation.plugins.living_ecosystem import LivingEcosystemAnimation
from animation.plugins.physarum_network import PhysarumNetworkAnimation
from animation.plugins.reaction_diffusion_garden import ReactionDiffusionGardenAnimation
from animation.plugins.wind_in_the_reeds import WindInTheReedsAnimation
from animation.plugins.lava_lamp import LavaLampAnimation
from animation.plugins.maze_chase import MazeChaseAnimation
from animation.plugins.night_train_windows import NightTrainWindowsAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.pixel_quest import PixelQuestAnimation
from animation.plugins.pixel_chase import PixelChaseAnimation
from animation.plugins.plant_glow import PlantGlowAnimation
from animation.plugins.gif_animation import GifAnimation
from animation.plugins.snake import SnakeAnimation
from animation.plugins.tetris import TetrisAnimation
from animation.plugins.gradient import GradientAnimation
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.solid import SolidColorAnimation
from animation.plugins.sparkle import SparkleAnimation
from animation.plugins.wave import WaveAnimation
from animation.plugins.circadian_window import CircadianWindowAnimation
from animation.plugins.cloud_canyon import CloudCanyonAnimation
from animation.plugins.desert_wind import DesertWindAnimation
from animation.plugins.moonlit_fog_banks import MoonlitFogBanksAnimation
from animation.plugins.rain_on_glass import RainOnGlassAnimation
from animation.plugins.tidal_bioluminescence import TidalBioluminescenceAnimation
from animation.plugins.waterfall_veil import WaterfallVeilAnimation
from animation.plugins.cellular_tapestry import CellularTapestryAnimation
from animation.plugins.flow_field_silk import FlowFieldSilkAnimation
from animation.plugins.frostwork import FrostworkAnimation
from animation.plugins.living_stained_glass import LivingStainedGlassAnimation
from animation.plugins.quasicrystal_bloom import QuasicrystalBloomAnimation
from drivers.frame_codec import (
    FRAME_ENCODING_NAME,
    decode_frame_data,
    encode_frame_data,
)
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from ipc.control_channel import FileControlChannel
from ipc.runtime_control import (
    normalize_controller_command_guard,
)
from ipc.scene_contract import (
    BROWSER_SCENE_MAX_BYTES,
    BROWSER_SCENE_SCHEMA,
    DEFAULT_SCENE_PROVIDER_POLICY,
    FIXED_OVERLAY_SLOT,
    SCENE_PRESET_SCHEMA,
    SCENE_PRESET_VERSION,
    SceneProviderPolicy,
    SceneValidationError,
    background_only_scene,
    browser_scene_to_host_scene,
    canonical_json_sha256,
    decorate_browser_component,
    decorate_catalog,
    filter_catalog,
    normalize_browser_scene_document,
    normalize_global_settings_payload,
    normalize_scene_payload,
    validate_bounded_browser_json,
    LocalSceneAdapter,
    SceneContractError,
    normalize_composer_scene,
)
from animation.core.scene_runtime import CanonicalSceneRuntimeError
from web.composer_component_editor import editor_catalog
from web.live_scene_state import LiveSceneStale, LiveSceneState
from web.composer_library_state import ComposerLibraryState, ComposerLibraryStateError
from web.composer_playlist_store import ComposerPlaylistStore, ComposerPlaylistStoreError
from web.composer_component_presets import ComponentPresetCatalog
from web.scene_look_store import SceneLookStore, SceneLookStoreError
from web.starter_looks import get_starter, list_starters
from web.working_draft_store import WorkingDraftStore, WorkingDraftError
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


COMPOSER_SHELL_VERSION = "composer-shell-v28"
CANONICAL_BROWSER_SCENE_SCHEMA = "ledgrid.browser-scene-v2"

# The Gallery stays projected from the Scene v2 packet, while this small map
# supplies human-facing catalog metadata without reopening legacy discovery.
_COMPOSER_GALLERY_CLASSES = (
    AuroraCurtainsAnimation, CanopyCupAnimation, AsciiDropAnimation,
    EmojiAnimation, ChristmasTreeAnimation, NightTrainWindowsAnimation,
    ConwayLifeAnimation, TetrisAnimation, FireflySynchronyAnimation,
    FireworksAnimation, FlameBurstAnimation, FluidTankAnimation,
    CyclicReefAnimation, LavaLampAnimation, SnakeAnimation, MazeChaseAnimation,
    PinballAnimation, PixelQuestAnimation, GradientAnimation, RainbowAnimation,
    SolidColorAnimation, SparkleAnimation, WaveAnimation, CircadianWindowAnimation,
    CloudCanyonAnimation, DesertWindAnimation, MoonlitFogBanksAnimation,
    RainOnGlassAnimation, TidalBioluminescenceAnimation, WaterfallVeilAnimation,
    CellularTapestryAnimation, FlowFieldSilkAnimation, FrostworkAnimation,
    LivingStainedGlassAnimation, QuasicrystalBloomAnimation, LivingEcosystemAnimation,
    PhysarumNetworkAnimation, ReactionDiffusionGardenAnimation, WindInTheReedsAnimation,
)
_COMPOSER_GALLERY_METADATA = {
    item.COMPONENT_ID: {
        'name': str(getattr(item, 'ANIMATION_NAME', item.COMPONENT_ID.replace('_', ' ').title())),
        'description': str(getattr(item, 'ANIMATION_DESCRIPTION', 'A live Scene animation.')),
    }
    for item in _COMPOSER_GALLERY_CLASSES
}

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
# universal worker.
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
                 local_mode: bool = False,
                 release_id: Optional[str] = None,
                 activation_enabled: Optional[bool] = None,
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
            else not self.local_mode
        )
        self.activation_mode = (
            'host_full_rgb' if self.activation_enabled else 'local_preview'
        )
        self.project_root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[1]
        )
        self.animation_presets_dir = self.project_root / "presets" / "animations"
        self.scene_presets_dir = self.project_root / "presets" / "scenes"
        self.deployment_status_path = self.project_root / "run_state" / "deployment.json"
        self._bundled_composer_catalog_digest_cache: Optional[str] = None
        self._bundled_composer_catalog_cache: Optional[List[Dict[str, Any]]] = None
        self._bundled_composer_catalog_checked = False
        self._controller_runtime_digests_cache: Optional[Dict[str, str]] = None
        self.painter_presets_dir = self.project_root / "presets" / "frame_painter"
        self.foliage_mask_path = self.project_root / "config" / "plant_pixel_map_32x138.json"
        self.planter_mask_path = self.project_root / "config" / "plant_globe_map_32x138.json"
        self.generated_preview_dir = (
            self.project_root / "web" / "static" / "generated" / "animation-previews"
        )
        self.runtime_preview_dir = self.project_root / "run_state" / "animation_previews"
        # Composer preview and publication use the same current Scene v2
        # catalog.  Preview owns no wall channel and therefore remains inert.
        self.composer_catalog = current_component_catalog()
        self.composer_presets = ComponentPresetCatalog(
            self.project_root,
            {
                AuroraCurtainsAnimation.COMPONENT_ID: AuroraCurtainsAnimation._normalized_parameters,
                CanopyCupAnimation.COMPONENT_ID: CanopyCupAnimation._normalized_parameters,
                AsciiDropAnimation.COMPONENT_ID: AsciiDropAnimation._normalized_parameters,
                EmojiAnimation.COMPONENT_ID: EmojiAnimation._normalized_parameters,
                ChristmasTreeAnimation.COMPONENT_ID: ChristmasTreeAnimation._normalized_parameters,
                ClockOverlayAnimation.COMPONENT_ID: ClockOverlayAnimation._normalized_parameters,
                NightTrainWindowsAnimation.COMPONENT_ID: NightTrainWindowsAnimation._normalized_parameters,
                ConwayLifeAnimation.COMPONENT_ID: ConwayLifeAnimation._normalized_parameters,
                TetrisAnimation.COMPONENT_ID: TetrisAnimation._normalized_parameters,
                FireflySynchronyAnimation.COMPONENT_ID: FireflySynchronyAnimation._normalized_parameters,
                FireworksAnimation.COMPONENT_ID: FireworksAnimation._normalized_parameters,
                FlameBurstAnimation.COMPONENT_ID: FlameBurstAnimation._normalized_parameters,
                FluidTankAnimation.COMPONENT_ID: FluidTankAnimation._normalized_parameters,
                CyclicReefAnimation.COMPONENT_ID: CyclicReefAnimation._normalized_parameters,
                LavaLampAnimation.COMPONENT_ID: LavaLampAnimation._normalized_parameters,
                SnakeAnimation.COMPONENT_ID: SnakeAnimation._normalized_parameters,
                MazeChaseAnimation.COMPONENT_ID: MazeChaseAnimation._normalized_parameters,
                PinballAnimation.COMPONENT_ID: PinballAnimation._normalized_parameters,
                PixelQuestAnimation.COMPONENT_ID: PixelQuestAnimation._normalized_parameters,
                PixelChaseAnimation.COMPONENT_ID: PixelChaseAnimation._normalized_parameters,
                PlantGlowAnimation.COMPONENT_ID: PlantGlowAnimation._normalized_parameters,
                GifAnimation.COMPONENT_ID: GifAnimation._normalized_parameters,
                GradientAnimation.COMPONENT_ID: GradientAnimation._normalized_parameters,
                RainbowAnimation.COMPONENT_ID: RainbowAnimation._normalized_parameters,
                SolidColorAnimation.COMPONENT_ID: SolidColorAnimation._normalized_parameters,
                SparkleAnimation.COMPONENT_ID: SparkleAnimation._normalized_parameters,
                WaveAnimation.COMPONENT_ID: WaveAnimation._normalized_parameters,
                CircadianWindowAnimation.COMPONENT_ID: CircadianWindowAnimation._normalized_parameters,
                CloudCanyonAnimation.COMPONENT_ID: CloudCanyonAnimation._normalized_parameters,
                DesertWindAnimation.COMPONENT_ID: DesertWindAnimation._normalized_parameters,
                MoonlitFogBanksAnimation.COMPONENT_ID: MoonlitFogBanksAnimation._normalized_parameters,
                RainOnGlassAnimation.COMPONENT_ID: RainOnGlassAnimation._normalized_parameters,
                TidalBioluminescenceAnimation.COMPONENT_ID: TidalBioluminescenceAnimation._normalized_parameters,
                WaterfallVeilAnimation.COMPONENT_ID: WaterfallVeilAnimation._normalized_parameters,
                CellularTapestryAnimation.COMPONENT_ID: CellularTapestryAnimation._normalized_parameters,
                FlowFieldSilkAnimation.COMPONENT_ID: FlowFieldSilkAnimation._normalized_parameters,
                FrostworkAnimation.COMPONENT_ID: FrostworkAnimation._normalized_parameters,
                LivingStainedGlassAnimation.COMPONENT_ID: LivingStainedGlassAnimation._normalized_parameters,
                QuasicrystalBloomAnimation.COMPONENT_ID: QuasicrystalBloomAnimation._normalized_parameters,
                LivingEcosystemAnimation.COMPONENT_ID: LivingEcosystemAnimation._normalized_parameters,
                PhysarumNetworkAnimation.COMPONENT_ID: PhysarumNetworkAnimation._normalized_parameters,
                ReactionDiffusionGardenAnimation.COMPONENT_ID: ReactionDiffusionGardenAnimation._normalized_parameters,
                WindInTheReedsAnimation.COMPONENT_ID: WindInTheReedsAnimation._normalized_parameters,
            },
        )
        self.composer_adapter = LocalSceneAdapter(self.composer_catalog)
        self.composer_control = _ComposerLocalControlChannel()
        self.composer_live = LiveSceneState(
            self.composer_catalog, self.composer_adapter, self.composer_control,
        )
        self.composer_looks = SceneLookStore(self.project_root / "run_state" / "composer_looks.json")
        self.composer_library = ComposerLibraryState(self.project_root / "run_state" / "composer_library.json")
        self.composer_playlists = ComposerPlaylistStore(
            self.project_root / "run_state" / "composer_playlists.json"
        )
        self.working_draft = WorkingDraftStore(self.project_root / 'run_state' / 'composer_draft.json')
        # A saved look is editable only while it remains the opened user look.
        # Built-ins have no id here, which makes Save require Save As.
        self._composer_opened_look_id: str | None = None
        self.composer_preview = ComposerFinalPreview(self.composer_catalog, self.project_root)
        if self.local_mode:
            self.generated_preview_dir = (
                self.project_root / "run_state" / "mac_animation_previews"
            )
        # Create Flask app
        self.app = Flask(__name__)
        self.app.secret_key = 'led-grid-secret-key-change-in-production'
        # Fixed calibration globe regions have a frozen user-facing order.
        # Flask's default key sorting would destroy it in the JSON response.
        self.app.json.sort_keys = False

        self.animation_presets_dir.mkdir(parents=True, exist_ok=True)
        self.scene_presets_dir.mkdir(parents=True, exist_ok=True)
        self.painter_presets_dir.mkdir(parents=True, exist_ok=True)

        # Register routes
        self._register_routes()

    def _bundled_composer_catalog_digest(self) -> Optional[str]:
        """Return the deployed browser catalog identity without rebuilding it."""
        if self._bundled_composer_catalog_digest_cache is not None:
            return self._bundled_composer_catalog_digest_cache
        path = (
            self.project_root / "web" / "static" / "generated"
            / "composer" / "bootstrap.v1.json"
        )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            digest = payload.get("artifact", {}).get("catalog_digest")
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return None
        self._bundled_composer_catalog_digest_cache = digest
        return digest

    def _matching_bundled_browser_catalog(self) -> Optional[List[Dict[str, Any]]]:
        """Reuse the deployed catalog when it matches the running manager."""
        if self._bundled_composer_catalog_checked:
            return self._bundled_composer_catalog_cache
        self._bundled_composer_catalog_checked = True
        path = (
            self.project_root / "web" / "static" / "generated"
            / "composer" / "bootstrap.v1.json"
        )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            bundled = payload.get("components")
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(bundled, list):
            return None
        if not self.local_mode:
            self._bundled_composer_catalog_cache = bundled
            return bundled
        identity = lambda item: (item.get("provider"), item.get("plugin_id"))
        bundled_identities = {identity(item) for item in bundled if isinstance(item, dict)}
        running = self._component_catalog()
        running_identities = {identity(item) for item in running if isinstance(item, dict)}
        if not bundled_identities or bundled_identities != running_identities:
            return None
        self._bundled_composer_catalog_cache = bundled
        return bundled

    def _register_routes(self):
        """Register Flask routes"""

        def validated_playlist_definition(value: Any) -> Any:
            if isinstance(value, dict) and isinstance(value.get("entries"), list):
                for entry in value["entries"]:
                    if isinstance(entry, dict) and "scene" in entry:
                        # Store the immutable browser document, but accept it
                        # only if the current runtime can resolve its host Scene.
                        self._validated_browser_activation_scene(entry["scene"])
            return value

        @self.app.route('/')
        def index():
            """Render the sole local Composer product."""
            return render_template(
                'composer.html',
                shell_version=COMPOSER_SHELL_VERSION,
                local_mode=self.local_mode,
            )

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

        @self.app.route('/api/composer/playlists')
        def api_composer_playlists():
            try:
                return jsonify({"playlists": self.composer_playlists.list()})
            except ComposerPlaylistStoreError as exc:
                return jsonify({"error": str(exc)}), 400

        @self.app.route('/api/composer/playlists', methods=['POST'])
        def api_composer_create_playlist():
            try:
                return jsonify({"playlist": self.composer_playlists.save(
                    validated_playlist_definition(request.get_json(silent=True))
                )})
            except (ComposerPlaylistStoreError, SceneValidationError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({"error": str(exc)}), 400

        @self.app.route('/api/composer/playlists/<playlist_id>')
        def api_composer_playlist(playlist_id: str):
            try:
                return jsonify({"playlist": self.composer_playlists.get(playlist_id)})
            except ComposerPlaylistStoreError as exc:
                return jsonify({"error": str(exc)}), 404

        @self.app.route('/api/composer/playlists/<playlist_id>', methods=['PUT', 'DELETE'])
        def api_composer_change_playlist(playlist_id: str):
            try:
                if request.method == 'DELETE':
                    self.composer_playlists.delete(playlist_id)
                    return jsonify({"deleted": True})
                return jsonify({"playlist": self.composer_playlists.save(
                    validated_playlist_definition(request.get_json(silent=True)),
                    playlist_id=playlist_id,
                )})
            except (ComposerPlaylistStoreError, SceneValidationError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({"error": str(exc)}), 400

        @self.app.route('/api/composer/playlists/run', methods=['POST'])
        def api_composer_run_playlist():
            try:
                payload = request.get_json(silent=True) or {}
                if set(payload) != {"playlist_id"}:
                    raise ComposerPlaylistStoreError("Choose one saved playlist to start.")
                definition = self.composer_playlists.get(payload["playlist_id"])
                status = dict(self.control_channel.read_status() or {})
                session_id = status.get("controller_session_id")
                revision = status.get("controller_state_revision")
                if not isinstance(session_id, str) or type(revision) is not int:
                    raise ComposerPlaylistStoreError("Controller ownership is unavailable; refresh and try again.")
                entries = []
                for entry in definition["entries"]:
                    _document, host_scene = self._validated_browser_activation_scene(entry["scene"])
                    entries.append({**entry, "scene": host_scene})
                request_id = request.headers.get("Idempotency-Key") or str(uuid.uuid4())
                try:
                    request_id = str(uuid.UUID(request_id))
                except (ValueError, AttributeError) as exc:
                    raise ComposerPlaylistStoreError("Playlist request identity is invalid.") from exc
                existing = self.control_channel.read_playlist_request_status(request_id)
                if existing is not None:
                    return jsonify({"accepted": existing})
                command_reader = getattr(self.control_channel, "read_playlist_command", None)
                existing_command = command_reader(request_id) if callable(command_reader) else None
                if existing_command is not None:
                    if existing_command.get("playlist_id") != definition["id"]:
                        return jsonify({"error": "Playlist request identity already names another playlist."}), 409
                    return jsonify({"accepted": {
                        "phase": "queued", "request_id": request_id,
                        "run_id": existing_command.get("run_id"),
                        "playlist_id": definition["id"],
                    }}), 202
                command = {
                    "schema": "ledgrid.playlist-command", "schema_version": 1,
                    "request_id": request_id, "run_id": str(uuid.uuid4()), "action": "start",
                    "requested_at": time.time(), "playlist_id": definition["id"],
                    "playlist_name": definition["name"],
                    "expected_controller_session_id": session_id,
                    "expected_controller_state_revision": revision,
                    "entries": entries,
                }
                self.control_channel.enqueue_playlist_command(command)
                return jsonify({"accepted": {"phase": "queued", "request_id": request_id,
                                               "run_id": command["run_id"],
                                               "playlist_id": definition["id"]}}), 202
            except (ComposerPlaylistStoreError, SceneValidationError, SceneContractError, TypeError, ValueError, FileExistsError) as exc:
                return jsonify({"error": str(exc)}), 400

        @self.app.route('/api/composer/playlists/stop', methods=['POST'])
        def api_composer_stop_playlist():
            try:
                payload = request.get_json(silent=True) or {}
                if set(payload) != {"run_id"}:
                    raise ValueError("Choose the pending or running playlist to stop.")
                try:
                    run_id = str(uuid.UUID(payload["run_id"]))
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValueError("Playlist run identity is invalid.") from exc
                if run_id != payload["run_id"]:
                    raise ValueError("Playlist run identity is invalid.")
                request_id = str(uuid.uuid4())
                command = {"schema": "ledgrid.playlist-command", "schema_version": 1,
                           "request_id": request_id, "action": "stop",
                           "requested_at": time.time(), "run_id": run_id}
                self.control_channel.enqueue_playlist_command(command)
                return jsonify({"accepted": {"phase": "queued", "request_id": request_id,
                                               "run_id": run_id}}), 202
            except (TypeError, ValueError, FileExistsError) as exc:
                return jsonify({"error": str(exc)}), 400

        @self.app.route('/api/composer/playlists/status')
        def api_composer_playlist_status():
            try:
                current = self.control_channel.read_playlist_current_status()
                request_id = request.args.get("request_id")
                request_status = (self.control_channel.read_playlist_request_status(request_id)
                                  if request_id else None)
                response = jsonify({"current": current, "request": request_status})
                response.headers['Cache-Control'] = 'no-store'
                return response
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @self.app.route('/api/composer/components')
        def api_composer_components():
            """Return the closed local chooser from qualified descriptors."""
            return jsonify(editor_catalog(self.composer_catalog))

        @self.app.route('/api/composer/gallery')
        def api_composer_gallery():
            """Read the inert, finite Scene v2 animation Gallery projection."""
            return jsonify(self._composer_gallery_payload())

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
            """Accept and immediately publish the newest valid Composer scene."""
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

        @self.app.route('/api/composer/connection', methods=['POST'])
        def api_composer_connection():
            payload = request.get_json(silent=True) or {}
            try:
                if set(payload) != {'connected'}:
                    raise ValueError('connection request must contain connected')
                return jsonify({'status': self.composer_live.set_connected(payload['connected'])})
            except (ValueError, TypeError) as exc:
                return jsonify({'error': str(exc), 'status': self._composer_status_payload()}), 400
            except TimeoutError as exc:
                return jsonify({'error': str(exc) or 'Scene acknowledgement timed out.',
                                'status': self._composer_status_payload()}), 504

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

        @self.app.route('/composer')
        def browser_composer():
            """Installable browser-native preset composer shell.

            Rendering, draft persistence, checking, and export happen in the
            browser. Loading this shell never observes or mutates live output.
            """
            return render_template(
                'composer.html',
                shell_version=COMPOSER_SHELL_VERSION,
                local_mode=self.local_mode,
            )

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
                'composer_slice.js',
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
                    'activate_scene': self.activation_enabled,
                    'playlists': True,
                },
                'activation_mode': self.activation_mode,
                'catalog_digest': self._bundled_composer_catalog_digest(),
                'bootstrap_url': '/api/v1/composer/bootstrap?catalog_only=1',
            })
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/composer/operations/status')
        def api_browser_composer_operations_status():
            """Revision-qualified observed output and bounded health evidence."""
            response = jsonify(self._composer_playback_status())
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/composer/settings/observed')
        def api_browser_composer_observed_settings():
            """Return the bounded controller observation used by Composer settings."""
            try:
                response = jsonify(self._composer_settings_observation_payload())
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            response.headers['Cache-Control'] = 'no-store'
            return response

        @self.app.route('/api/v1/composer/operations/telemetry')
        def api_browser_composer_operations_telemetry():
            """Versioned controller evidence for deployment and retained diagnostics.

            This is deliberately separate from the browser-facing operations
            summary: deployment and receiver diagnostics need a stable,
            explicit roster and counter contract, while Composer only needs a
            bounded health projection.  Keeping that projection and this
            telemetry document separate lets the old status family be
            retired without teaching non-browser callers to scrape UI data.
            """
            response = jsonify(self._operations_telemetry_payload())
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


        @self.app.route('/api/v1/scene', methods=['PUT', 'POST'])
        def api_start_scene():
            """Queue a host-rendered scene; controller playback is observed separately."""
            if not self.activation_enabled:
                return self._activation_unavailable()
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) - {'scene', 'global_settings'}:
                return jsonify({'error': 'request must contain scene and optional global_settings'}), 400
            try:
                raw_scene = payload.get('scene')
                if isinstance(raw_scene, dict) and raw_scene.get('schema') == CANONICAL_BROWSER_SCENE_SCHEMA:
                    raw_scene = raw_scene.get('scene')
                if isinstance(raw_scene, dict) and raw_scene.get('schema') == 'ledgrid.scene.v2':
                    scene = self._composer_canonical({'origin': 'composer', 'scene': raw_scene}).scene
                else:
                    scene = self._validated_scene_request(raw_scene)
                settings = normalize_global_settings_payload(payload['global_settings']) if payload.get('global_settings') is not None else None
                command = self.control_channel.send_command('start_scene', scene=scene, **({'global_settings': settings} if settings is not None else {}))
            except (SceneValidationError, SceneContractError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc), 'code': 'invalid_scene'}), 400
            response = jsonify({'success': True, 'state': 'requested', 'command_id': self._command_id(command), 'request_id': command.get('request_id') if isinstance(command, dict) else None, 'requested_scene': scene})
            response.headers['Cache-Control'] = 'no-store'
            return response, 202

        @self.app.route('/api/v1/scene', methods=['DELETE'])
        def api_stop_scene():
            command = self.control_channel.send_command('stop_scene')
            return jsonify({'success': True, 'state': 'requested', 'command_id': self._command_id(command), 'request_id': command.get('request_id') if isinstance(command, dict) else None}), 202








        @self.app.route('/api/v1/scene/components/<target>', methods=['PATCH'])
        def api_update_scene_component(target: str):
            """Apply an explicit live-editor parameter update to the active scene.

            Ordinary direct PATCH calls remain fail-closed.  Composer live edit
            opts in per request, names the component it expects to be live, and
            may update parameters only; replacing a scene uses a complete Scene request.
            """
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or payload.get('live_edit') is not True:
                return self._guarded_scene_error(
                    f'Updating scene component {target!r} requires a complete Scene request.'
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
                'presets': self._list_component_presets(component_id, provider),
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
                    return jsonify({
                        'success': True,
                        'component_key': component_key,
                        'preset_id': preset_id,
                    })
                if self._load_component_preset(component_id, preset_id, provider):
                    return jsonify({
                        'error': 'Built-in and legacy preset records are read-only.',
                        'code': 'preset_immutable',
                        'component_key': component_key,
                        'preset_id': preset_id,
                    }), 409
                return jsonify({'error': 'Preset not found'}), 404

            preset = self._load_component_preset(component_id, preset_id, provider)
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
            preset = self._load_scene_preset(preset_id)
            if preset is None:
                return jsonify({'error': 'Scene preset not found'}), 404
            try:
                scene = self._validated_scene_request(preset.get('scene'))
                command = self.control_channel.send_command('start_scene', scene=scene)
            except (SceneValidationError, TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400
            return jsonify({'success': True, 'state': 'requested', 'command_id': self._command_id(command)}), 202

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

        @self.app.route('/api/stop', methods=['POST'])
        def api_stop_animation():
            """API: Stop current animation"""
            command = self.control_channel.send_command('stop')
            return jsonify({
                'success': True,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/v1/vibe', methods=['GET'])
        def api_get_vibe():
            """API: Read the selected global vibe and stable profile catalog."""
            return jsonify({
                'version': 1,
                'vibe': self._selected_vibe_status(),
                'profiles': self._vibe_profile_catalog(),
            })

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
        
        @self.app.route('/api/v1/receivers/status/refresh', methods=['POST'])
        def api_refresh_receiver_status():
            """Request a fresh controller-side SPI status drain on every receiver."""
            command = self.control_channel.send_command('refresh_receiver_status')
            return jsonify({
                'accepted': True,
                'request_id': command.get('request_id') if isinstance(command, dict) else None,
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
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            try:
                guard = self._settings_command_guard(payload, {'multiplier'})
                multiplier = float(payload.get('multiplier'))
            except (TypeError, ValueError) as exc:
                return jsonify({'error': str(exc)}), 400
            if not math.isfinite(multiplier) or multiplier <= 0:
                return jsonify({'error': 'multiplier must be a positive finite number'}), 400
            speed_scale = DEFAULT_ANIMATION_SPEED_SCALE * multiplier
            command_data = {'animation_speed_scale': speed_scale}
            if guard is not None:
                command_data['_controller_guard'] = guard
            command = self.control_channel.send_command(
                'set_animation_speed_scale', **command_data
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
                guard = self._settings_command_guard(payload, {'brightness'})
                brightness = AnimationManager.validate_output_brightness(
                    payload.get('brightness')
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            command_data = {'brightness': brightness}
            if guard is not None:
                command_data['_controller_guard'] = guard
            command = self.control_channel.send_command(
                'set_output_brightness', **command_data
            )
            return jsonify({
                'success': True,
                'brightness': brightness,
                'command_id': self._command_id(command),
            })

        @self.app.route('/api/config/power', methods=['POST'])
        def api_set_power():
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'request body must be a JSON object'}), 400
            try:
                guard = self._settings_command_guard(payload, {'power'})
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            power = payload.get('power')
            if type(power) is not bool:
                return jsonify({'error': 'power must be boolean'}), 400
            command_data = {'power': power}
            if guard is not None:
                command_data['_controller_guard'] = guard
            command = self.control_channel.send_command(
                'set_device_state', **command_data
            )
            return jsonify({
                'success': True,
                'power': power,
                'command_id': self._command_id(command),
            })

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
            """Send a bounded gesture to the live Composer basis or legacy animation."""
            payload = request.get_json(silent=True) or {}
            try:
                kind, x, y, strength = self._validated_interaction_payload(payload)
                composer = self.composer_live.snapshot(include_current_scene=True)
                if composer['current'] is not None:
                    if not (composer['running'] and composer['armed'] and composer['observed'] == composer['current']):
                        raise ValueError('Composer interaction requires a live observed scene')
                    canonical = self._composer_recovery_scene(composer['current_scene'])
                    component_id = canonical.scene['animation']['component_id']
                    if component_id not in {"lava_lamp", "flame_burst", "fluid_tank", "pinball", "cellular_tapestry", "frostwork", "reaction_diffusion_garden", "wind_in_the_reeds"}:
                        raise ValueError('the published Composer animation does not accept primary interaction')
                    if kind != 'primary':
                        raise ValueError("interaction 'primary' is required by the published Composer animation")
                    if not self.composer_preview.dispatch_animation_interaction(canonical, kind, x, y, strength):
                        raise ValueError('the published Composer animation rejected that interaction')
                    return jsonify({'success': True, 'accepted': True, 'component_id': component_id,
                                    'basis': canonical.identity.to_dict()})
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
        loader = getattr(self.preview_manager, 'plugin_loader', None)
        raw_getter = getattr(loader, 'component_catalog', None)
        discovered = raw_getter() if callable(raw_getter) else []
        discovered_by_id = {item.get('plugin_id'): item for item in discovered}
        records = []
        for descriptor in self.composer_catalog.descriptors:
            component_id = descriptor.component_id
            source_id = 'solid' if component_id == 'solid_background' else component_id
            source = dict(discovered_by_id.get(source_id, {}))
            source.update(plugin_id=component_id, provider='python', role=descriptor.role.value,
                          name='Solid background' if component_id == 'solid_background' else source.get('name', component_id.replace('_', ' ').title()),
                          defaults=descriptor.default_parameters())
            if component_id == 'solid_background':
                source['parameter_schema'] = {
                    'gain': {'type': 'float', 'min': 0, 'max': 1, 'default': .62},
                    'seed': {'type': 'int', 'min': 0, 'max': 999999, 'default': 4201},
                }
                source['entrypoint'] = ''
            records.append(source)
        return self._scene_v1_component_roles(decorate_catalog(records, provider_policy=DEFAULT_SCENE_PROVIDER_POLICY))

    def _composer_gallery_payload(self) -> Dict[str, Any]:
        """Project every eligible Composer Animation exactly once.

        Gallery membership intentionally comes from the current Scene v2
        packet, not plugin discovery.  Test and calibration renderers are not
        in that packet, so they cannot leak into an operator-facing chooser.
        This reader never touches the working draft, library, live adapter, or
        wall channel; thumbnails are shipped catalog illustrations generated
        through the inert production renderer.
        """
        entries: List[Dict[str, Any]] = []
        for descriptor in self.composer_catalog.descriptors:
            if descriptor.role.value != 'animation':
                continue
            component_id = descriptor.component_id
            metadata = _COMPOSER_GALLERY_METADATA.get(component_id, {})
            try:
                preset_count = len(self.composer_presets.choices(component_id))
            except ValueError:
                preset_count = 0
            entries.append({
                'key': f'{descriptor.provider.value}:{component_id}:v{descriptor.version}',
                'component_id': component_id,
                'provider': descriptor.provider.value,
                'version': descriptor.version,
                'role': descriptor.role.value,
                'name': metadata.get('name', component_id.replace('_', ' ').title()),
                'description': metadata.get('description', 'A live Scene animation.'),
                'parameters': descriptor.default_parameters(),
                'available': True,
                'preset_count': preset_count,
            })
        entries.sort(key=lambda item: (str(item['name']).casefold(), str(item['key'])))
        digest = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        return {
            'schema': 'ledgrid.composer-gallery',
            'schema_version': 1,
            'catalog_digest': digest,
            'entries': entries,
        }

    @staticmethod
    def _scene_v1_component_roles(
        catalog: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Adapt Scene v2 Widget ownership to the browser-scene v1 overlay slot."""
        adapted = []
        for component in catalog:
            if (
                component.get('provider') == 'python'
                and component.get('plugin_id') == 'clock_overlay'
            ):
                component = {**component, 'role': 'overlay'}
            adapted.append(component)
        return adapted

    @staticmethod
    def _command_id(command: Any) -> Any:
        """Extract correlation when the configured control channel supplies it."""
        return command.get('command_id') if isinstance(command, dict) else None

    def _settings_command_guard(
        self, payload: Mapping[str, Any], setting_keys: set[str]
    ) -> Optional[Dict[str, Any]]:
        """Validate a narrow setting body and optional controller guard."""
        guard_keys = {
            'expected_controller_session_id',
            'expected_controller_state_revision',
            'expires_at',
        }
        unknown = sorted(set(payload) - setting_keys - guard_keys)
        if unknown:
            raise ValueError(f"unsupported request fields: {', '.join(unknown)}")
        guard = normalize_controller_command_guard(payload, now=time.time())
        if guard is None:
            return None
        status = self.control_channel.read_status()
        applied_id = (
            status.get('last_applied_command_id')
            if isinstance(status, Mapping)
            else None
        )
        if (
            isinstance(applied_id, bool)
            or not isinstance(applied_id, (int, float))
            or not math.isfinite(float(applied_id))
        ):
            raise ValueError(
                "controller does not advertise guarded settings command support"
            )
        return guard


    @staticmethod
    def _activation_unavailable() -> tuple[Any, int]:
        response = jsonify({
            'error': 'Guarded physical-wall activation is disabled on this server.',
            'code': 'activation_unavailable',
        })
        response.headers['Cache-Control'] = 'no-store'
        return response, 503

    @staticmethod


    @staticmethod

    @staticmethod

    @staticmethod

    @staticmethod











    @staticmethod
    def _guarded_scene_error(message: str) -> tuple[Any, int]:
        return jsonify({
            'error': message,
            'code': 'scene_request_required',
            'activation_url': '/api/v1/scene',
        }), 428

    def _browser_composer_bootstrap(
        self, *, observe_installation_profile: bool = True,
        runtime_asset_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Build the complete read model needed after the app shell loads.

        Unlike the gallery summaries, composer presets include their authored
        parameter objects. Built-ins come only from the finite current catalog;
        retained user records stay provider-qualified and separate.
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
        canonical_descriptors = {
            (descriptor.provider.value, descriptor.component_id): descriptor
            for descriptor in self.composer_catalog.descriptors
        }
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
            descriptor = canonical_descriptors.get((provider, plugin_id))
            canonical_animation = bool(
                descriptor is not None
                and descriptor.role.value == 'animation'
            )
            canonical_role = (
                descriptor.role.value if descriptor is not None
                else str(raw.get('role') or 'background')
            )
            # Browser scene v1 carries Widgets through its fixed overlay slot;
            # every other Composer role is published verbatim.
            browser_role = 'overlay' if canonical_role == 'widget' else canonical_role
            scene_compatibility = json.loads(json.dumps(
                raw.get('scene_compatibility') or {}
            ))
            if descriptor is not None:
                # The legacy loader decorates its flattened manifests before
                # this browser projection.  Replace that stale compatibility
                # result with the resolved Composer descriptor's exact slot.
                scene_compatibility = {
                    'selectable': True,
                    'slots': [
                        plugin_id if canonical_role == 'widget' else browser_role
                    ],
                    'diagnostic': None,
                }

            schema = raw.get('parameter_schema')
            schema = json.loads(json.dumps(schema)) if isinstance(schema, dict) else {}
            if descriptor is not None:
                # Parameter membership and defaults are part of the same
                # canonical descriptor contract as provider and role.  Keep
                # the mature loader's UI annotations, but exclude retired
                # legacy globals and replace any stale schema defaults.
                defaults = descriptor.default_parameters()
                schema = {
                    name: definition
                    for name, definition in schema.items()
                    if name in defaults
                }
                for name, value in defaults.items():
                    definition = schema.get(name)
                    if isinstance(definition, dict):
                        definition['default'] = json.loads(json.dumps(value))
            else:
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
            if canonical_animation:
                vibe_capabilities = []
                if descriptor.palette_policy.value == 'semantic':
                    vibe_capabilities.append('palette_roles')
                if descriptor.timing_policy.value == 'scaled_context':
                    vibe_capabilities.append('tempo')
            else:
                vibe_capabilities = json.loads(json.dumps(
                    raw.get('vibe_capabilities') or []
                ))
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
            if provider == 'python' and python_entrypoint_ready and plugin_id != 'solid_background':
                runtime = {
                    'kind': 'python',
                    'supported': True,
                    'engine': 'python-pyodide-wasm',
                    'worker_url': '/static/js/composer_python_worker.js',
                    'asset_url': (
                        '/static/generated/composer/ledgrid_python_runtime.zip'
                    ),
                }
            else:
                runtime = {
                    'kind': 'python' if provider == 'python' else 'native',
                    'supported': False,
                    'reason': (
                        'This component does not expose a verified browser-Wasm '
                        'entrypoint for local rendering.'
                    ),
                }

            if runtime.get('supported'):
                asset_url = runtime.get('asset_url')
                asset_path = (
                    (
                        runtime_asset_root / Path(asset_url).name
                        if runtime_asset_root is not None
                        else self.project_root / 'web' / asset_url.lstrip('/')
                    )
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
            for summary in self._list_component_presets(plugin_id, provider):
                preset_id = summary.get('preset_id')
                if not isinstance(preset_id, str):
                    continue
                payload = self._load_component_preset(
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
                'role': browser_role,
                'icon': str(raw.get('icon') or '✦'),
                'parameter_schema': schema,
                'defaults': defaults,
                'presets': preset_records,
                'browser_runtime': runtime,
                'provider_collision': plugin_id in collisions,
                'scene_compatibility': scene_compatibility,
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
                    'timing_adapter': (
                        descriptor.timing_policy.value
                        if canonical_animation else str(
                            raw.get('timing_adapter') or 'legacy_speed_param'
                        )
                    ),
                    'vibe_color_policy': (
                        descriptor.palette_policy.value
                        if canonical_animation else str(
                            raw.get('vibe_color_policy') or 'preserve'
                        )
                    ),
                    'vibe_capabilities': vibe_capabilities,
                },
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
        profile_draft_url = profile_publish_url = profile_artifact_url = None
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
                'draft_storage': 'controller',
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
                    'playlists_url': '/api/composer/playlists',
                    'playlist_run_url': '/api/composer/playlists/run',
                    'playlist_stop_url': '/api/composer/playlists/stop',
                    'playlist_status_url': '/api/composer/playlists/status',
                    'live_edit_component_url_template': (
                        '/api/v1/scene/components/{target}'
                    ),
                    'live_edit_available': True,
                    'validate_scene_url': '/api/v1/scene/validate',
                    'activate_scene_url': '/api/v1/scene',
                    'current_scene_url': '/api/v1/scene',
                    'status_url': '/api/v1/composer/settings/observed',
                    'operations_status_url': '/api/v1/composer/operations/status',
                    'vibe_url': '/api/v1/vibe',
                    'plant_modifiers_url': '/api/config/plant-modifiers',
                    'brightness_url': '/api/config/brightness',
                    'target_fps_url': '/api/config/target-fps',
                    'operator_speed_url': '/api/config/animation-speed',
                    'installation_profile_draft_url': profile_draft_url,
                    'installation_profile_publish_url': profile_publish_url,
                    'installation_profile_artifact_url': profile_artifact_url,
                    'online_required': True,
                },
            },
            'diagnostics': [],
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
        bundled = self._matching_bundled_browser_catalog()
        if bundled is not None:
            return bundled
        return self._browser_composer_bootstrap()['components']

    def _validated_browser_scene_document(
        self, payload: Any, *, purpose: str
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        catalog = self._browser_scene_catalog()
        document = normalize_browser_scene_document(
            payload, catalog=catalog, purpose=purpose
        )
        scene = browser_scene_to_host_scene(document, catalog=catalog)
        return document, scene



    def _validated_browser_activation_scene(self, payload: Any) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if isinstance(payload, dict) and payload.get('schema') == CANONICAL_BROWSER_SCENE_SCHEMA:
            scene = payload.get('scene')
        else:
            scene = payload
        if isinstance(scene, dict) and scene.get('schema') == 'ledgrid.scene.v2':
            try:
                canonical = self._composer_canonical({'origin': 'composer', 'scene': scene})
            except SceneContractError as exc:
                raise SceneValidationError(str(exc)) from exc
            return {'scene': canonical.scene}, canonical.scene
        document, host_scene = self._validated_browser_scene_document(scene, purpose='activation')
        return document, host_scene

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
        self._require_current_component_preset_params(plugin_id, provider, params)
        overwrite = payload.get('overwrite', False)
        if not isinstance(overwrite, bool):
            raise ValueError('overwrite must be a boolean')

        existing = self._load_component_preset(plugin_id, preset_id, provider)
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
                loader = getattr(self.preview_manager, 'plugin_loader', None)
                getter = getattr(loader, 'get_plugin', None)
                if not callable(getter):
                    # Compatibility for small integrations that expose only
                    # the legacy background-animation information surface.
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
                preset = self._load_component_preset(
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
            preset = self._load_component_preset(
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
                if raw_scene.get("schema") == "ledgrid.scene.v2":
                    return self._composer_canonical({"origin": "composer", "scene": raw_scene}).scene
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
            stored = self._load_component_preset(animation, preset_id)
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
        print(f"   Composer:  http://{self.host}:{self.port}/composer")
        print("   Root URL redirects to Composer")

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
        scene = request_value.get('scene') if isinstance(request_value, Mapping) else None
        if isinstance(scene, Mapping):
            references = [('background', scene.get('background')), ('animation', scene.get('animation'))]
            references.extend((f"widget:{widget.get('id', '?')}", widget.get('component')) for widget in scene.get('widgets', []) if isinstance(widget, Mapping))
            supported = {(descriptor.provider.value, descriptor.component_id) for descriptor in self.composer_catalog.descriptors}
            for slot, reference in references:
                if isinstance(reference, Mapping) and (reference.get('provider'), reference.get('component_id')) not in supported:
                    raise SceneContractError(f"Unsupported saved component {reference.get('provider')}:{reference.get('component_id')} in {slot}; choose a supported component explicitly.")
        canonical = normalize_composer_scene(request_value, self.composer_catalog)
        # Keep Emoji Message's compact, local controls at the Composer boundary
        # so an invalid text or position cannot replace the last
        # live/recoverable scene before the final-preview runtime sees it.
        for widget in canonical.scene["widgets"]:
            component = widget["component"]
            if component["component_id"] == EmojiArrangerAnimation.COMPONENT_ID:
                EmojiArrangerAnimation._normalized_parameters(component["parameters"])
        if canonical.scene["animation"]["component_id"] == AuroraCurtainsAnimation.COMPONENT_ID:
            AuroraCurtainsAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == CanopyCupAnimation.COMPONENT_ID:
            CanopyCupAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == AsciiDropAnimation.COMPONENT_ID:
            AsciiDropAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == EmojiAnimation.COMPONENT_ID:
            EmojiAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == ChristmasTreeAnimation.COMPONENT_ID:
            ChristmasTreeAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == NightTrainWindowsAnimation.COMPONENT_ID:
            NightTrainWindowsAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == CellularTapestryAnimation.COMPONENT_ID:
            CellularTapestryAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == FlowFieldSilkAnimation.COMPONENT_ID:
            FlowFieldSilkAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == FrostworkAnimation.COMPONENT_ID:
            FrostworkAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == LivingStainedGlassAnimation.COMPONENT_ID:
            LivingStainedGlassAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == QuasicrystalBloomAnimation.COMPONENT_ID:
            QuasicrystalBloomAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == LivingEcosystemAnimation.COMPONENT_ID:
            LivingEcosystemAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == PhysarumNetworkAnimation.COMPONENT_ID:
            PhysarumNetworkAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == ReactionDiffusionGardenAnimation.COMPONENT_ID:
            ReactionDiffusionGardenAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == WindInTheReedsAnimation.COMPONENT_ID:
            WindInTheReedsAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == ConwayLifeAnimation.COMPONENT_ID:
            ConwayLifeAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == TetrisAnimation.COMPONENT_ID:
            TetrisAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == FireflySynchronyAnimation.COMPONENT_ID:
            FireflySynchronyAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == FireworksAnimation.COMPONENT_ID:
            FireworksAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == FlameBurstAnimation.COMPONENT_ID:
            FlameBurstAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == FluidTankAnimation.COMPONENT_ID:
            FluidTankAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == CyclicReefAnimation.COMPONENT_ID:
            CyclicReefAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == LavaLampAnimation.COMPONENT_ID:
            LavaLampAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == SnakeAnimation.COMPONENT_ID:
            SnakeAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == MazeChaseAnimation.COMPONENT_ID:
            MazeChaseAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == PinballAnimation.COMPONENT_ID:
            PinballAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == PixelQuestAnimation.COMPONENT_ID:
            PixelQuestAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == GradientAnimation.COMPONENT_ID:
            GradientAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == RainbowAnimation.COMPONENT_ID:
            RainbowAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == SolidColorAnimation.COMPONENT_ID:
            SolidColorAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == SparkleAnimation.COMPONENT_ID:
            SparkleAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
        if canonical.scene["animation"]["component_id"] == WaveAnimation.COMPONENT_ID:
            WaveAnimation._normalized_parameters(canonical.scene["animation"]["parameters"])
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
    def _sanitize_preset_id(raw_name: str) -> str:
        """Convert user-provided preset names to a filesystem-safe id."""
        cleaned = re.sub(r'[^a-zA-Z0-9_-]+', '_', (raw_name or '').strip().lower())
        cleaned = re.sub(r'_+', '_', cleaned).strip('_')
        return cleaned[:64]

    def _read_json_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a JSON object from disk."""
        try:
            raw = path.read_text(encoding='utf-8')
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None



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
        return summary

    def _component_preset_ownership(
        self, animation_name: str, preset_id: str, provider: str
    ) -> str:
        """Classify a current provider-qualified record without disk discovery."""
        runtime_path = self._animation_preset_path(animation_name, preset_id, provider)
        if runtime_path is not None and runtime_path.is_file():
            return 'user'
        if self._builtin_component_preset(animation_name, preset_id, provider) is not None:
            return 'built_in'
        return 'unknown'


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

    def _builtin_component_preset(
        self, component_id: str, preset_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Project one checked catalog choice into the retained API record shape."""
        if provider != 'python':
            return None
        try:
            catalog_provider = self.composer_presets.provider(component_id)
        except ValueError:
            return None
        if catalog_provider != provider:
            return None
        if not self.composer_presets.contains(component_id, preset_id):
            return None
        try:
            choice = self.composer_presets.choice(component_id, preset_id)
        except ValueError as exc:
            raise RuntimeError(
                f'Current Composer catalog is invalid for {component_id}/{preset_id}'
            ) from exc
        return {
            'version': 2,
            'preset_id': choice['preset_id'],
            'name': choice['name'],
            'description': choice['description'],
            'animation': component_id,
            'provider': provider,
            'params': choice['parameters'],
        }

    def _runtime_component_preset(
        self, component_id: str, preset_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Read only a provider-qualified user record retained by the API contract."""
        path = self._animation_preset_path(component_id, preset_id, provider)
        if path is None or not path.is_file():
            return None
        payload = self._read_json_file(path)
        if (
            not payload
            or payload.get('preset_id') != preset_id
            or payload.get('animation') != component_id
            or payload.get('provider') != provider
            or not isinstance(payload.get('name'), str)
            or not isinstance(payload.get('params'), dict)
        ):
            return None
        try:
            self._require_current_component_preset_params(
                component_id, provider, payload['params']
            )
        except ValueError:
            return None
        return payload

    def _require_current_component_preset_params(
        self, component_id: str, provider: str, params: Mapping[str, Any]
    ) -> None:
        """Accept only exact current component-local controls for user records."""
        if provider != 'python':
            raise ValueError(
                'Component preset records require a current Python component identity'
            )
        try:
            if self.composer_presets.provider(component_id) != provider:
                raise ValueError('component provider does not match the current catalog')
            normalized = self.composer_presets.normalize_parameters(component_id, params)
        except ValueError as exc:
            raise ValueError(
                'Component preset parameters must be local to the current '
                f'provider-qualified component {provider}:{component_id}'
            ) from exc
        # Some normalizers retain a deprecated compatibility input only to
        # migrate old Scene documents. A current saved card cannot carry it.
        if set(params) - set(normalized):
            raise ValueError(
                'Component preset parameters must be local to the current '
                f'provider-qualified component {provider}:{component_id}'
            )

    def _load_component_preset(
        self, component_id: str, preset_id: str, provider: str = 'python'
    ) -> Optional[Dict[str, Any]]:
        """Read a current user record or the one checked built-in catalog choice."""
        return (
            self._runtime_component_preset(component_id, preset_id, provider)
            or self._builtin_component_preset(component_id, preset_id, provider)
        )

    def _list_component_presets(
        self, component_id: str, provider: str = 'python'
    ) -> List[Dict[str, Any]]:
        """List current catalog choices and retained user records for one identity."""
        records: Dict[str, Dict[str, Any]] = {}
        if provider == 'python':
            try:
                catalog_provider = self.composer_presets.provider(component_id)
            except ValueError:
                catalog_provider = None
            if catalog_provider == provider:
                try:
                    choices = self.composer_presets.choices(component_id)
                except ValueError as exc:
                    raise RuntimeError(
                        f'Current Composer catalog is invalid for {component_id}'
                    ) from exc
                for choice in choices:
                    preset = self._builtin_component_preset(
                        component_id, choice['preset_id'], provider
                    )
                    if preset is not None:
                        preset['ownership'] = 'built_in'
                        records[choice['preset_id']] = preset

        runtime_dir = self._animation_preset_dir(component_id, provider)
        if runtime_dir is not None and runtime_dir.is_dir():
            for path in sorted(runtime_dir.glob('*.json')):
                if path.stem == 'before-deploy':
                    continue
                preset = self._runtime_component_preset(
                    component_id, path.stem, provider
                )
                if preset is not None:
                    preset['ownership'] = 'user'
                    records[path.stem] = preset

        summaries = []
        for preset in records.values():
            summary = self._animation_preset_summary(preset)
            summary['ownership'] = preset['ownership']
            summaries.append(summary)
        summaries.sort(
            key=lambda preset: str(
                preset.get('name') or preset.get('preset_id') or ''
            ).casefold()
        )
        return summaries

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
                    selected_view = getattr(
                        self.preview_manager,
                        'get_installation_profile_runtime_view',
                        lambda: None,
                    )()
                    self.composer_preview.set_installation_profile(selected_view)
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
        timestamp = status.get('updated_at') or status.get('timestamp') or status.get('written_at')
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            timestamp = 0
        status['controller_observation_fresh'] = bool(
            status.get('controller_session_id') and 0 <= time.time() - timestamp <= 5
        )
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

    def _composer_playback_status(self) -> Dict[str, Any]:
        status = self._status_payload()
        return {
            'schema': 'ledgrid.composer-playback-status', 'schema_version': 1,
            'requested_scene': status.get('requested_scene'),
            'controller_playback': {
                'state': 'unavailable' if not status.get('controller_observation_fresh') else 'running' if status.get('is_running') else 'stopped',
                'running': bool(status.get('is_running')) if status.get('controller_observation_fresh') else None,
                'animation': status.get('current_animation'),
                'scene': status.get('scene_state'),
                'error': (status.get('controller_playback') or {}).get('error') or status.get('last_error'),
                'actual_fps': status.get('actual_fps'),
                'updated_at': status.get('timestamp'),
            },
            'receiver_connectivity': status.get('receiver_connectivity') or status.get('receiver_status') or [],
        }

    def _operations_telemetry_payload(self) -> Dict[str, Any]:
        """Return the explicit non-browser controller read contract.

        Do not return the legacy status document wholesale.  Each retained
        owner gets only its declared deployment, receiver-diagnostic,
        calibration and playback evidence here.
        """
        status = self._status_payload()
        driver = status.get('driver_stats')
        driver = dict(driver) if isinstance(driver, dict) else {}
        receiver_hybrid = status.get('receiver_hybrid')
        receiver_hybrid = (
            dict(receiver_hybrid) if isinstance(receiver_hybrid, dict) else None
        )

        return {
            'schema': 'ledgrid.composer-operations-telemetry',
            'schema_version': 1,
            'controller': {
                'updated_at': status.get('timestamp'),
                'release_id': status.get('release_id'),
                'release_consistent': status.get('release_consistent'),
                'controller_release_id': status.get('controller_release_id'),
                'led_info': status.get('led_info'),
                'mode': status.get('mode'),
                'is_running': status.get('is_running'),
                'current_animation': status.get('current_animation'),
                'brightness': status.get('brightness'),
                'target_fps': status.get('target_fps'),
                'actual_fps': status.get('actual_fps'),
                'pipeline_fps': status.get('pipeline_fps'),
                'uptime': status.get('uptime'),
                'last_command_id': status.get('last_command_id'),
                'controller_session_id': status.get('controller_session_id'),
                'controller_state_revision': status.get(
                    'controller_state_revision'
                ),
                'current_identity_digest': status.get(
                    'current_identity_digest'
                ),
            },
            'deployment': {
                'release_id': status.get('release_id'),
                'release_consistent': status.get('release_consistent'),
                'deploy_timestamp': status.get('deploy_timestamp'),
            },
            'diagnostics': {
                'performance': status.get('performance'),
                'driver_stats': driver,
            },
            'calibration': {
                'installation_profile_digest': status.get(
                    'installation_profile_digest'
                ),
                'plant_modifiers': status.get('plant_modifiers'),
            },
        }

    def _composer_settings_observation_payload(self) -> Dict[str, Any]:
        """Return only the live fields Composer reconciles before activation.

        This is deliberately distinct from deployment telemetry and from the
        retired, unbounded status document. The browser needs the exact
        revision and setting projection that it will use in a guarded Check;
        it does not need frames, counters, receiver diagnostics, or catalog
        details.
        """
        status = self._status_payload()
        requested_id = request.args.get('request_id') if has_request_context() else None
        if requested_id:
            requested_id = str(uuid.UUID(requested_id))
        result_reader = getattr(self.control_channel, 'read_command_result', None)
        command_result = result_reader(requested_id) if requested_id and callable(result_reader) else status.get('command_result')
        raw_global_settings = status.get('global_settings')
        global_settings = (
            dict(raw_global_settings)
            if isinstance(raw_global_settings, dict)
            else {}
        )
        return {
            'schema': 'ledgrid.composer-settings-observation',
            'schema_version': 1,
            'observed_at': status.get('timestamp'),
            'freshness': 'fresh' if status.get('controller_observation_fresh') else 'stale',
            'controller_session_id': status.get('controller_session_id'),
            'controller_state_revision': status.get('controller_state_revision'),
            'last_command_id': status.get('last_command_id'),
            'command_result': command_result,
            'last_applied_command_id': status.get('last_applied_command_id'),
            'scene_digest': status.get('scene_digest'),
            'scene': status.get('scene_state'),
            'last_error': (status.get('controller_playback') or {}).get('error') or status.get('last_error'),
            'receiver_connectivity': status.get('receiver_status') or status.get('receiver_connectivity') or {},
            'installation_profile_digest': status.get(
                'installation_profile_digest'
            ),
            'global_settings': global_settings,
            'is_running': bool(status.get('is_running', False)),
            'brightness': status.get('brightness'),
            'target_fps': status.get('target_fps'),
            # This is intentionally just the measured presentation cadence.
            # Composer needs it beside the authored target for an operator to
            # diagnose the output loop; renderer source rates and Scene pace
            # remain component/Scene concerns and do not belong here.
            'actual_fps': status.get('actual_fps'),
            'animation_speed_scale': status.get('animation_speed_scale'),
            'vibe': status.get('vibe'),
            'plant_modifiers': status.get('plant_modifiers'),
        }

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

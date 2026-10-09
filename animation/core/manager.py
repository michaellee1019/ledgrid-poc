"""
Animation Manager Service

Coordinates between LED controller, animation plugins, and web interface.
Handles animation switching, parameter updates, and frame generation.
"""
import hashlib
import copy
from io import BytesIO
import json
import math
import sys
import time
import threading
import traceback
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List
import numpy as np
from PIL import Image
from animation.component_parameters import SCENE_EXTERNAL_COMPONENT_PARAMETERS
from animation.core import AnimationBase, RenderedFrame, StatefulAnimationBase, AnimationPluginLoader
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import HostForegroundCompositor, HostSceneCompositor, PlacedOverlay
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.installation_profile_topology import IDENTITY_INSTALLATION_PROFILE_TOPOLOGY, InstallationProfileTopology
from animation.core.plant_awareness import InstallationGeometryContact, PlantModifierState
from animation.core.presentation_contracts import AGGREGATE_OVERLAY_SLOT_ID, AnimationRuntimeContext, BaseFrame, ClipPolicy, ComponentProvider, ComponentRef, ForegroundStalePolicy, OverlayFrame, OverlayPlacement, OverlayRef, NEUTRAL_PLANT_INPUTS, ResolvedScene, ResolvedVibe, SceneState, StalePolicy, VibeState, component_preset_fingerprint, list_vibe_profiles, resolve_vibe
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP
from drivers.frame_codec import encode_frame_data, FRAME_ENCODING_NAME
from ipc.scene_contract import SceneProviderPolicy
FRAME_SCHEDULER_HEADROOM_RATIO = 0.05
FRAME_SCHEDULER_MAX_FPS = 200.0
FRAME_DEADLINE_COARSE_WINDOW_SECONDS = 0.002
FRAME_DEADLINE_SPIN_SECONDS = 0.0005
MAINTENANCE_PAUSE_ACK_TIMEOUT_SECONDS = 2.0
try:
    from drivers.multi_device import MultiDeviceLEDController as LEDController
except ImportError:
    try:
        from drivers.spi_controller import LEDController
    except ImportError:

        class LEDController:

            def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP, **kwargs):
                self.strip_count = strips
                self.leds_per_strip = leds_per_strip
                self.total_leds = strips * leds_per_strip
                self.debug = kwargs.get('debug', False)
                print(f'🔧 Mock LED Controller: {strips} strips × {leds_per_strip} LEDs = {self.total_leds} total')

            def set_all_pixels(self, pixel_data):
                """Mock set all pixels"""
                if self.debug and len(pixel_data) > 0:
                    r, g, b = pixel_data[0]
                    print(f'📊 Frame: First pixel = RGB({r}, {g}, {b})')

            def show(self):
                """Mock show"""
                pass

            def clear(self):
                """Mock clear"""
                if self.debug:
                    print('🧹 Cleared LEDs')

            def configure(self):
                """Mock configure"""
                pass

def _plan_frame_deadline(prior_deadline: Optional[float], prior_target_fps: Optional[int], *, frame_started: float, work_finished: float, target_fps: int) -> tuple[float, float]:
    """Plan one absolute frame deadline without accumulating sleep drift.

    Ordinary scheduler oversleep is paid back by the next frame. If execution
    falls at least one whole period behind, expired deadlines are skipped so a
    stalled process never emits an unbounded catch-up burst. A live target-FPS
    change starts a new cadence epoch at the current frame boundary.
    """
    bounded_target = max(1, int(target_fps) or 1)
    nominal_period = 1.0 / bounded_target
    period = max(1.0 / FRAME_SCHEDULER_MAX_FPS, nominal_period * (1.0 - FRAME_SCHEDULER_HEADROOM_RATIO))
    if prior_deadline is None or prior_target_fps != bounded_target:
        deadline = frame_started + period
    else:
        deadline = prior_deadline + period
    if work_finished - deadline >= period:
        expired = math.floor((work_finished - deadline) / period) + 1
        deadline += expired * period
    return (deadline, max(0.0, deadline - work_finished))

def _wait_for_frame_deadline(deadline: float, *, clock=None, sleeper=None, coarse_window: float=FRAME_DEADLINE_COARSE_WINDOW_SECONDS, spin_window: float=FRAME_DEADLINE_SPIN_SECONDS) -> float:
    """Coarse-sleep, yield, then spin briefly to reduce deadline jitter."""
    clock = clock or time.perf_counter
    sleeper = sleeper or time.sleep
    while True:
        now = clock()
        remaining = deadline - now
        if remaining <= 0:
            return now
        if remaining > coarse_window:
            sleeper(remaining - coarse_window)
        elif remaining > spin_window:
            sleeper(0)

class PreviewLEDController:
    """
    Lightweight controller used for preview generation.
    Mirrors the dimensions of the real controller but performs no I/O so preview
    requests can never block or interfere with the SPI device.
    """

    def __init__(self, strips: int, leds_per_strip: int, debug: bool=False):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.debug = debug
        self.current_brightness: Optional[int] = None

    def set_all_pixels(self, *_args, **_kwargs):
        pass

    def set_pixel(self, *_args, **_kwargs):
        pass

    def set_range(self, *_args, **_kwargs):
        pass

    def set_brightness(self, brightness, *_args, **_kwargs):
        self.current_brightness = int(brightness)

    def show(self, *_args, **_kwargs):
        pass

    def clear(self, *_args, **_kwargs):
        pass

    def configure(self, *_args, **_kwargs):
        pass
from animation.core.canonical_receiver_scene import CanonicalReceiverSceneMixin

class AnimationManager(CanonicalReceiverSceneMixin):
    """Manages animation playback and plugin system"""
    ALLOWED_PLUGINS = set(AnimationPluginLoader.shipped_plugin_ids())
    DEFAULT_ANIMATION = 'sparkle'

    def __init__(self, controller: LEDController, plugins_dir: Optional[str]=None, animation_speed_scale: float=DEFAULT_ANIMATION_SPEED_SCALE, plant_aware: bool=DEFAULT_PLANT_AWARE, plant_modifiers: Optional[Dict[str, Any]]=None, vibe: Optional[Any]=None, default_animation: Optional[str]=None, default_animation_config: Optional[Dict[str, Any]]=None, default_animation_preset: Optional[Dict[str, Any]]=None, feature_flags: Optional[Any]=None, installation_profile_topology: InstallationProfileTopology=IDENTITY_INSTALLATION_PROFILE_TOPOLOGY, auto_start: bool=True):
        """
        Initialize animation manager

        Args:
            controller: LED controller instance
            plugins_dir: Directory containing animation plugins
            animation_speed_scale: Operator tempo multiplier applied at render time
            plant_aware: Global plant-aware state applied to every animation
            default_animation: Animation to auto-start on init (None = use DEFAULT_ANIMATION)
            default_animation_config: Parameters to apply to the default animation
            installation_profile_library: Optional managed profile library
            installation_profile_digest: Initial managed profile content digest
            installation_profile_topology: Receiver topology used while resolving
            auto_start: Whether to start the default animation during construction
        """
        self.controller = controller
        self.plugin_loader = AnimationPluginLoader(plugins_dir, allowed_plugins=self.ALLOWED_PLUGINS)
        self._default_animation = default_animation or self.DEFAULT_ANIMATION
        self._default_animation_config = default_animation_config or {}
        self._default_animation_preset = default_animation_preset
        self.feature_flags = feature_flags if isinstance(feature_flags, AnimationPipelineFeatureFlags) else AnimationPipelineFeatureFlags.from_mapping(feature_flags)
        self.current_animation: Optional[AnimationBase] = None
        self.current_animation_name: Optional[str] = None
        self.current_animation_hash: Optional[str] = None
        self.current_preset: Optional[Dict[str, Any]] = None
        self.output_brightness: Optional[int] = getattr(controller, 'current_brightness', None)
        self._last_active_state: Optional[Dict[str, Any]] = None
        self.is_running = False
        self.target_fps = 200
        self.frame_count = 0
        self.frames_presented = 0
        self.unchanged_frames_skipped = 0
        self.start_time = 0.0
        self._run_state_lock = threading.RLock()
        self._run_generation = 0
        self._presentation_io_lock = threading.Lock()
        self._presentation_state_lock = threading.RLock()
        self.animation_speed_scale = self._validate_tempo_scale(animation_speed_scale)
        self._resolved_vibe, self._vibe_diagnostic = self._resolve_initial_vibe(vibe)
        self._presentation_revision = 0
        self._scaled_elapsed = 0.0
        self._last_unscaled_elapsed = 0.0
        self._scene_epoch = time.time_ns() & (1 << 64) - 1
        self._presentation_refresh_pending = True
        self._live_presentation_state = self._empty_presentation_state()
        self._scene_lock = threading.RLock()
        self._canonical_receiver_scene = None
        self._canonical_receiver_runtime = None
        self._requested_scene = None
        self._playback_error = None
        self._scene_mode = False
        self._scene_background: Optional[Dict[str, Any]] = None
        self._scene_overlay: Optional[Dict[str, Any]] = None
        self._scene_compositor: Optional[HostSceneCompositor] = None
        self._active_scene_state: Optional[SceneState] = None
        self._scene_compatibility_mode = False
        self._scene_allows_compatibility_components = False
        self._scene_final_presentation_state = self._empty_presentation_state()
        self._canonical_host_full_mode = False
        self.plant_modifier_state = PlantModifierState.from_payload(plant_modifiers) if plant_modifiers is not None else PlantModifierState.from_legacy(bool(plant_aware))
        self._legacy_plant_aware_bridge = plant_modifiers is None
        self.plant_aware = bool(self.plant_modifier_state.active)
        self.animation_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.frame_timestamps = deque(maxlen=1000)
        self.perf_samples = deque(maxlen=300)
        self.perf_lock = threading.Lock()
        self._last_perf_sample: Dict[str, float] = {}
        self._driver_fps = 0.0
        self._driver_fps_last_frames: Optional[int] = None
        self._driver_fps_last_time: Optional[float] = None
        self._driver_device_last_frames: Dict[int, int] = {}
        self._driver_device_last_time: Dict[int, float] = {}
        self.current_frame_data = []
        self.frame_data_lock = threading.Lock()
        self.preview_controller = PreviewLEDController(self.controller.strip_count, self.controller.leds_per_strip, getattr(self.controller, 'debug', False))
        self._preview_lock = threading.RLock()
        self._preview_session: Optional[Dict[str, Any]] = None
        self._preview_session_ttl = 300.0
        self.refresh_plugins()
        if auto_start and self._default_animation:
            if self.start_animation(self._default_animation, self._default_animation_config, preset=self._default_animation_preset):
                print(f'▶️  Auto-started default animation: {self._default_animation}')
            else:
                print(f'⚠️  Could not auto-start default animation: {self._default_animation}')

    def refresh_plugins(self) -> Dict[str, Any]:
        """Reload all animation plugins"""
        try:
            plugins = self.plugin_loader.load_all_plugins()
            print(f'✓ Loaded {len(plugins)} animation plugins')
            return {name: self.plugin_loader.get_plugin_info(name) for name in plugins if self._plugin_role(name) != 'overlay'}
        except Exception as e:
            print(f'✗ Error loading plugins: {e}')
            traceback.print_exc()
            return {}

    def set_animation_speed_scale(self, speed_scale: float) -> float:
        """Update operator tempo without mutating authored animation state."""
        requested = self._validate_tempo_scale(speed_scale)
        changed = False
        with self._presentation_state_guard():
            if requested != self.animation_speed_scale:
                self.animation_speed_scale = requested
                self._presentation_revision = getattr(self, '_presentation_revision', 0) + 1
                self._presentation_refresh_pending = True
                changed = True
        if changed:
            self._refresh_active_presentation_context()
        return self.animation_speed_scale

    def _presentation_state_guard(self) -> threading.RLock:
        """Return the manager presentation-state lock, including old test doubles."""
        lock = getattr(self, '_presentation_state_lock', None)
        if lock is None:
            lock = threading.RLock()
            self._presentation_state_lock = lock
        return lock

    @staticmethod
    def _validate_tempo_scale(value: Any) -> float:
        requested = float(value)
        if not math.isfinite(requested) or requested <= 0:
            raise ValueError('animation speed scale must be a positive finite number')
        return requested

    @staticmethod
    def _wall_time() -> float:
        """Return presentation wall time; benchmarks can replace this clock."""
        return time.time()

    @staticmethod
    def _canonical_vibe(payload: Any, *, revision: Optional[int]=None) -> ResolvedVibe:
        if payload is None:
            return resolve_vibe('neutral', revision=0 if revision is None else revision)
        if isinstance(payload, str):
            return resolve_vibe(payload, revision=0 if revision is None else revision)
        if not isinstance(payload, dict):
            raise TypeError('vibe must be a stable ID or versioned state')
        state = VibeState.from_payload(payload.get('state', payload))
        resolved = resolve_vibe(state.vibe_id, revision=state.revision if revision is None else revision, profile_version=state.profile_version)
        if state.resolved_profile_digest != resolved.state.resolved_profile_digest:
            raise ValueError('persisted vibe profile digest does not match the registry')
        return resolved

    @classmethod
    def _resolve_initial_vibe(cls, payload: Any) -> tuple[ResolvedVibe, Optional[Dict[str, str]]]:
        if payload is None:
            return (resolve_vibe('neutral'), None)
        try:
            return (cls._canonical_vibe(payload), None)
        except (KeyError, TypeError, ValueError) as exc:
            revision = 0
            raw = payload.get('state', payload) if isinstance(payload, dict) else {}
            raw_revision = raw.get('revision') if isinstance(raw, dict) else None
            if isinstance(raw_revision, int) and (not isinstance(raw_revision, bool)) and (0 <= raw_revision <= 2 ** 64 - 1):
                revision = raw_revision
            return (resolve_vibe('neutral', revision=revision), {'code': 'vibe_profile_fallback', 'message': f'Saved vibe was incompatible; using neutral: {exc}'})

    def get_vibe_state(self) -> Dict[str, Any]:
        with self._presentation_state_guard():
            return self._resolved_vibe.state.to_dict()

    def get_vibe_status(self) -> Dict[str, Any]:
        with self._presentation_state_guard():
            status: Dict[str, Any] = {'state': self._resolved_vibe.state.to_dict(), 'profile': self._resolved_vibe.profile.to_dict()}
            if self._vibe_diagnostic:
                status['diagnostic'] = dict(self._vibe_diagnostic)
            return status

    @staticmethod
    def list_vibe_profiles() -> List[Dict[str, Any]]:
        return [profile.to_dict() for profile in list_vibe_profiles()]

    def set_vibe(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            requested, diagnostic = self._resolve_initial_vibe(payload)
        else:
            requested = self._canonical_vibe(payload)
            diagnostic = None
        presentation_changed = False
        with self._presentation_state_guard():
            current = self._resolved_vibe
            same_profile = requested.state.vibe_id == current.state.vibe_id and requested.state.profile_version == current.state.profile_version and (requested.state.resolved_profile_digest == current.state.resolved_profile_digest)
            if not same_profile:
                requested = resolve_vibe(requested.state.vibe_id, revision=current.state.revision + 1, profile_version=requested.state.profile_version)
                self._resolved_vibe = requested
                self._presentation_revision += 1
                self._presentation_refresh_pending = True
                presentation_changed = True
            elif diagnostic and requested.state.revision != current.state.revision:
                self._resolved_vibe = requested
            self._vibe_diagnostic = diagnostic
        if presentation_changed:
            self._refresh_active_presentation_context()
        return self.get_vibe_status()


    def get_installation_profile_runtime_view(self):
        """Installed calibration comes directly from the wall config maps."""
        return None


    def _refresh_preview_presentation_context(self) -> None:
        """Refresh an existing preview without replacing or advancing it."""
        lock = getattr(self, '_preview_lock', None)
        if lock is None:
            return
        with lock:
            session = self._preview_session
            if not session:
                return
            animation = session['animation']
            with self._presentation_state_guard():
                resolved = self._resolved_vibe
                operator_tempo = self.animation_speed_scale
            context = self._runtime_context(animation, unscaled_elapsed=session['last_unscaled_elapsed'], scaled_elapsed=session['scaled_elapsed'], frame_index=session['frame_count'], resolved_vibe=resolved, operator_tempo_scale=operator_tempo)
            animation.set_presentation_context(context)
            session['presentation_state'] = self._empty_presentation_state()
            session['force_refresh'] = True

    def _installation_profile_view(self):
        return {}

    def _installation_geometry_contact(self, animation: AnimationBase) -> InstallationGeometryContact | None:
        """Resolve geometry only for a provider that explicitly declares it."""
        if not bool(getattr(animation, 'INSTALLATION_GEOMETRY_CONTACT', False)):
            return None
        geometry = animation.get_plant_masks()
        cache = getattr(self, '_fixed_geometry_contacts', None)
        if cache is None:
            cache = {}
            self._fixed_geometry_contacts = cache
        key = id(geometry)
        contact = cache.get(key)
        if contact is None:
            contact = InstallationGeometryContact.from_geometry(geometry, identity=('wall-calibration', key))
            cache[key] = contact
        return contact


    @staticmethod
    def _animation_authored_speed(animation: AnimationBase) -> float:
        try:
            value = float(animation.get_authored_parameter('speed', 1.0))
        except (TypeError, ValueError):
            return 1.0
        return value if math.isfinite(value) and value > 0 else 1.0

    @staticmethod
    def _component_tempo(profile, animation: AnimationBase) -> float:
        return profile.tempo_scale if 'tempo' in animation.VIBE_CAPABILITIES else 1.0

    def _runtime_context(self, animation: AnimationBase, *, unscaled_elapsed: float, scaled_elapsed: float, frame_index: int, resolved_vibe: Optional[ResolvedVibe]=None, operator_tempo_scale: Optional[float]=None, plant_modifiers: Optional[Dict[str, Any]]=None) -> AnimationRuntimeContext:
        if resolved_vibe is None or operator_tempo_scale is None:
            with self._presentation_state_guard():
                resolved = resolved_vibe or self._resolved_vibe
                operator_tempo = self.animation_speed_scale if operator_tempo_scale is None else operator_tempo_scale
        else:
            resolved = resolved_vibe
            operator_tempo = operator_tempo_scale
        authored_speed = self._animation_authored_speed(animation)
        vibe_tempo = self._component_tempo(resolved.profile, animation)
        return AnimationRuntimeContext(wall_time=self._wall_time(), unscaled_elapsed=max(0.0, float(unscaled_elapsed)), scaled_elapsed=max(0.0, float(scaled_elapsed)), frame_index=max(0, int(frame_index)), scene_epoch=self._scene_epoch, global_width=int(self.controller.strip_count), height=int(self.controller.leds_per_strip), local_strip_offset=0, local_width=int(self.controller.strip_count), vibe_id=resolved.state.vibe_id, vibe_profile_version=resolved.state.profile_version, resolved_profile_digest=resolved.state.resolved_profile_digest, palette_roles=resolved.profile.palette_roles, capability_values=resolved.profile.capability_values, tempo_scale=vibe_tempo, luminance_scale=resolved.profile.luminance_scale if 'luminance' in animation.VIBE_CAPABILITIES else 1.0, operator_tempo_scale=operator_tempo, authored_speed=authored_speed, effective_time_scale=authored_speed * vibe_tempo * operator_tempo, installation_profile_view=self._installation_profile_view(), plant_modifiers=self.plant_modifier_state.to_dict() if plant_modifiers is None else plant_modifiers, installation_geometry_contact=self._installation_geometry_contact(animation))

    def _advance_runtime_context(self, animation: AnimationBase, unscaled_elapsed: float, frame_index: int, *, resolved_vibe: Optional[ResolvedVibe]=None, operator_tempo_scale: Optional[float]=None) -> AnimationRuntimeContext:
        if resolved_vibe is None or operator_tempo_scale is None:
            with self._presentation_state_guard():
                resolved = resolved_vibe or self._resolved_vibe
                operator_tempo = self.animation_speed_scale if operator_tempo_scale is None else operator_tempo_scale
        else:
            resolved = resolved_vibe
            operator_tempo = operator_tempo_scale
        unscaled = max(0.0, float(unscaled_elapsed))
        delta = max(0.0, unscaled - self._last_unscaled_elapsed)
        authored_speed = self._animation_authored_speed(animation)
        vibe_tempo = self._component_tempo(resolved.profile, animation)
        self._scaled_elapsed += delta * authored_speed * vibe_tempo * operator_tempo
        self._last_unscaled_elapsed = unscaled
        return self._runtime_context(animation, unscaled_elapsed=unscaled, scaled_elapsed=self._scaled_elapsed, frame_index=frame_index, resolved_vibe=resolved, operator_tempo_scale=operator_tempo)

    def _refresh_active_presentation_context(self) -> None:
        with self._presentation_state_guard():
            resolved = getattr(self, '_resolved_vibe', resolve_vibe('neutral'))
            operator_tempo = self.animation_speed_scale
        if getattr(self, '_scene_mode', False):
            with self._scene_state_guard():
                components = tuple((component for component in (self._scene_background, self._scene_overlay) if component is not None and component.get('animation') is not None))
                for component in components:
                    animation = component['animation']
                    context = self._runtime_context(animation, unscaled_elapsed=component['last_unscaled_elapsed'], scaled_elapsed=component['scaled_elapsed'], frame_index=component['frame_index'], resolved_vibe=resolved, operator_tempo_scale=operator_tempo)
                    animation.set_runtime_plant_modifiers(context.plant_modifiers)
                    animation.set_runtime_installation_profile(context.installation_profile_view)
                    if callable(getattr(animation, 'render_resolved_scene', None)) and (not self._scene_allows_compatibility_components):
                        component['force_changed'] = True
                    else:
                        animation.set_presentation_context(context)
            return
        animation = self.current_animation
        if isinstance(animation, AnimationBase):
            context = self._runtime_context(animation, unscaled_elapsed=self._last_unscaled_elapsed, scaled_elapsed=self._scaled_elapsed, frame_index=self.frame_count, resolved_vibe=resolved, operator_tempo_scale=operator_tempo)
            animation.set_presentation_context(context)

    @staticmethod
    def _empty_presentation_state() -> Dict[str, Any]:
        return {'buffers': [], 'index': 0, 'geometry': None, 'cached': None, 'identity': None}

    @staticmethod
    def _apply_vibe_presentation(animation: AnimationBase, pixels: Any, *, profile, changed: bool, state: Dict[str, Any], force_refresh: bool=False, include_grade: bool=True, include_luminance: bool=True) -> tuple[Any, bool]:
        capabilities = animation.VIBE_CAPABILITIES
        policy = animation.VIBE_COLOR_POLICY
        grade = include_grade and profile.vibe_id != 'neutral' and (policy == 'grade') and ('palette_roles' in capabilities)
        luminance = profile.luminance_scale if include_luminance and 'luminance' in capabilities else 1.0
        identity = (profile.resolved_profile_digest, policy, tuple(sorted(capabilities)), bool(include_grade), bool(include_luminance))
        refresh = force_refresh or state.get('identity') != identity
        state['identity'] = identity
        if not grade and luminance == 1.0:
            state['cached'] = None
            return (pixels, changed or refresh)
        if not changed and (not refresh) and (state.get('cached') is not None):
            return (state['cached'], False)
        array = np.asarray(pixels, dtype=np.uint8)
        if array.ndim != 2 or array.shape[1] not in (3, 4):
            raise ValueError('vibe presentation requires an RGB or RGBA frame')
        count = array.shape[0]
        channels = array.shape[1]
        geometry = (count, channels)
        if state.get('geometry') != geometry:
            state['buffers'] = [np.empty(geometry, dtype=np.uint8) for _ in range(2)]
            state['index'] = 0
            state['geometry'] = geometry
        output = state['buffers'][state['index']]
        state['index'] = (state['index'] + 1) % len(state['buffers'])
        working = array[:, :3].astype(np.float32)
        if grade:
            chroma = float(profile.capability_values.get('chroma_scale', 1.0))
            luma = (working[:, 0] * 0.299 + working[:, 1] * 0.587 + working[:, 2] * 0.114)[:, None]
            working = luma + (working - luma) * chroma
            energy = float(profile.capability_values.get('energy', 0.5))
            tint_weight = min(0.12, max(0.0, abs(energy - 0.5) * 0.18))
            if tint_weight:
                tint = np.asarray(profile.palette_roles['primary'], dtype=np.float32)
                if channels == 4:
                    tint = tint[None, :] * (array[:, 3:4].astype(np.float32) / 255.0)
                working = working * (1.0 - tint_weight) + tint * tint_weight
        if luminance != 1.0:
            working *= luminance
        np.clip(working, 0.0, 255.0, out=working)
        if channels == 4:
            np.minimum(working, array[:, 3:4], out=working)
            np.copyto(output[:, 3], array[:, 3])
        np.copyto(output[:, :3], np.rint(working), casting='unsafe')
        state['cached'] = output
        return (output, changed or refresh)

    @staticmethod
    def validate_output_brightness(brightness: Any) -> int:
        """Return a valid hardware brightness level without numeric coercion."""
        if isinstance(brightness, bool) or not isinstance(brightness, int):
            raise ValueError('brightness must be an integer between 0 and 255')
        if brightness < 0 or brightness > 255:
            raise ValueError('brightness must be between 0 and 255')
        return brightness

    def set_output_brightness(self, brightness: Any) -> int:
        """Apply the installation-wide receiver brightness at runtime."""
        level = self.validate_output_brightness(brightness)
        setter = getattr(self.controller, 'set_brightness', None)
        if not callable(setter):
            raise RuntimeError('LED controller does not support global brightness')
        setter(level)
        self.output_brightness = level
        return level

    def _remember_active_state(self, animation_name: str, config: Dict[str, Any], preset: Optional[Dict[str, Any]]) -> None:
        """Keep the last playable state so a device-level power-on can resume it."""
        self._last_active_state = {'animation': animation_name, 'config': dict(config), 'preset': dict(preset) if preset else None}

    def _sync_last_active_preset(self) -> None:
        if self._last_active_state is not None and self._last_active_state.get('animation') == self.current_animation_name:
            self._last_active_state['preset'] = dict(self.current_preset) if self.current_preset else None

    def apply_device_state(self, state: Dict[str, Any]) -> bool:
        """Atomically validate and apply a device-level control request.

        The IPC transport carries this request as one command. Hardware changes
        are then applied together by the controller process, so a rapid HA
        power/effect/brightness update cannot lose one of its fields.
        """
        if not isinstance(state, dict) or not state:
            raise ValueError('device state must contain at least one field')
        supported = {'power', 'brightness', 'animation', 'config', 'preset'}
        unknown = sorted(set(state) - supported)
        if unknown:
            raise ValueError(f"unsupported device state fields: {', '.join(unknown)}")
        has_power = 'power' in state
        power = state.get('power')
        if has_power and (not isinstance(power, bool)):
            raise ValueError('power must be boolean')
        has_brightness = 'brightness' in state
        brightness = self.validate_output_brightness(state.get('brightness')) if has_brightness else None
        has_animation = 'animation' in state
        animation = state.get('animation')
        if has_animation:
            if not isinstance(animation, str) or not animation:
                raise ValueError('animation must be a non-empty string')
            if self.plugin_loader.get_plugin(animation) is None:
                raise ValueError(f'animation not found: {animation}')
            if self._plugin_role(animation) == 'overlay':
                raise ValueError(f'overlay component {animation} requires a composed scene')
        config = state.get('config', {})
        if not isinstance(config, dict):
            raise ValueError('config must be an object')
        if 'config' in state and (not has_animation):
            raise ValueError('config requires an animation')
        preset = state.get('preset')
        if 'preset' in state:
            if not has_animation:
                raise ValueError('preset requires an animation')
            if self._normalize_current_preset(preset, animation) is None:
                raise ValueError('preset metadata is invalid')
        if power is False and has_animation:
            raise ValueError('power false cannot be combined with an animation')
        if has_brightness:
            self.set_output_brightness(brightness)
        if power is False:
            self.stop_animation()
            return True
        if has_animation:
            return self.start_animation(animation, config, preset=preset)
        if power is True and (not self.is_running):
            restore = self._last_active_state or {'animation': self._default_animation, 'config': self._default_animation_config, 'preset': None}
            return self.start_animation(restore['animation'], dict(restore.get('config') or {}), preset=restore.get('preset'))
        return True

    def set_plant_aware(self, enabled: bool) -> bool:
        """Compatibility boundary translating the old global boolean."""
        state = PlantModifierState.from_legacy(enabled)
        prior_state = getattr(self, 'plant_modifier_state', PlantModifierState.empty())
        changed = state.to_dict() != prior_state.to_dict()
        self.plant_modifier_state = state
        self.plant_aware = bool(state.active)
        self._legacy_plant_aware_bridge = True
        with self._scene_state_guard():
            scene_mode = bool(getattr(self, '_scene_mode', False))
            if self.current_animation and (not scene_mode):
                self.current_animation.update_parameters({'plant_aware': self.plant_aware, 'plant_modifiers': state.to_dict()})
        if scene_mode:
            self._refresh_active_presentation_context()
        self._update_preview_plant_state()
        return self.plant_aware

    def set_plant_modifiers(self, state: Any) -> Dict[str, Any]:
        """Validate and apply modifier authority live and to every future start."""
        requested = PlantModifierState.from_payload(state)
        prior_state = getattr(self, 'plant_modifier_state', PlantModifierState.empty())
        changed = requested.to_dict() != prior_state.to_dict()
        self.plant_modifier_state = requested
        self._legacy_plant_aware_bridge = False
        self.plant_aware = bool(self.plant_modifier_state.active)
        with self._scene_state_guard():
            scene_mode = bool(getattr(self, '_scene_mode', False))
            if self.current_animation and (not scene_mode):
                self.current_animation.update_parameters({'plant_aware': False, 'plant_modifiers': self.plant_modifier_state.to_dict()})
        if scene_mode:
            self._refresh_active_presentation_context()
        self._update_preview_plant_state()
        return self.plant_modifier_state.to_dict()

    def _update_preview_plant_state(self) -> None:
        """Apply global installation state without resetting preview semantics."""
        lock = getattr(self, '_preview_lock', None)
        if lock is None:
            return
        with lock:
            session = self._preview_session
            if not session:
                return
            session['animation'].update_parameters({'plant_aware': self.plant_aware if self._legacy_plant_aware_bridge else False, 'plant_modifiers': self.plant_modifier_state.to_dict()})

    def list_animations(self) -> List[Dict[str, Any]]:
        """Get list of available animations with metadata"""
        animations = []
        for plugin_name in self.plugin_loader.list_plugins():
            if self._plugin_role(plugin_name) == 'overlay':
                continue
            info = self.plugin_loader.get_plugin_info(plugin_name)
            if info:
                animations.append(info)
        return animations

    def list_components(self, provider=None, role=None):
        catalog = [self._scene_v1_component_descriptor(item) for item in self.plugin_loader.component_catalog()
                   if item.get('provider', 'python') == 'python']
        return [item for item in catalog if (provider is None or item.get('provider') == provider)
                and (role is None or item.get('role') == role)]

    def scene_provider_policy(self):
        return SceneProviderPolicy()

    @staticmethod
    def _scene_parameter_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise TypeError('component parameters must be an object')
        return {key: value for key, value in payload.items() if key not in SCENE_EXTERNAL_COMPONENT_PARAMETERS}

    @staticmethod
    def _component_snapshot_fingerprint(parameters: Dict[str, Any]) -> str:
        encoded = json.dumps(parameters, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def _scene_descriptor(self, ref: ComponentRef, expected_role: str, *, allow_compatibility_component: bool=False) -> Dict[str, Any]:
        if ref.provider is not ComponentProvider.PYTHON:
            raise ValueError(f'unsupported live scene provider: {ref.provider.value}')
        descriptor = self.plugin_loader.get_component_descriptor(ref.plugin_id)
        if descriptor is not None:
            descriptor = self._scene_v1_component_descriptor(descriptor)
        if descriptor is None and allow_compatibility_component and (ref.plugin_id in self.plugin_loader.loaded_plugins):
            role = self._plugin_role(ref.plugin_id)
            descriptor = {'plugin_id': ref.plugin_id, 'provider': 'python', 'role': role, 'defaults': {}, 'compatibility': {'classification': 'legacy_runtime_adapter', 'composable': role in {'background', 'overlay'}}}
        if descriptor is None:
            raise ValueError(f'scene component not found: {ref.plugin_id}')
        if descriptor.get('provider') != ComponentProvider.PYTHON.value:
            raise ValueError(f'scene component {ref.plugin_id!r} does not use the python provider')
        actual_role = descriptor.get('role')
        if actual_role != expected_role:
            raise ValueError(f'scene {expected_role} {ref.plugin_id!r} is declared as {actual_role!r}')
        compatibility = descriptor.get('compatibility') or {}
        if compatibility.get('composable') is not True:
            classification = compatibility.get('classification', 'incompatible')
            raise ValueError(f'scene component {ref.plugin_id!r} is not composable ({classification})')
        animation_class = self.plugin_loader.get_plugin(ref.plugin_id)
        if animation_class is None:
            raise ValueError(f'scene component implementation not found: {ref.plugin_id}')
        if issubclass(animation_class, StatefulAnimationBase):
            raise TypeError('Stateful animations cannot participate in scenes')
        return descriptor

    def _resolve_component_ref(self, ref: ComponentRef, *, expected_role: str, allow_compatibility_component: bool=False) -> tuple[ComponentRef, Dict[str, Any]]:
        descriptor = self._scene_descriptor(ref, expected_role, allow_compatibility_component=allow_compatibility_component)
        defaults = self._scene_parameter_payload(dict(descriptor.get('defaults') or {}))
        snapshot = self._scene_parameter_payload(dict(ref.resolved_parameters))
        overrides = self._scene_parameter_payload(dict(ref.parameter_overrides))
        resolved = snapshot if snapshot else defaults
        resolved.update(overrides)
        try:
            resolved = self.plugin_loader.validate_component_parameters(ref.plugin_id, resolved)
        except ValueError:
            if not allow_compatibility_component:
                raise
        canonical = ComponentRef(plugin_id=ref.plugin_id, provider=ref.provider, preset_id=ref.preset_id, preset_fingerprint=ref.preset_fingerprint, parameter_overrides=overrides, resolved_parameters=resolved, bundle_digest=ref.bundle_digest, expected_payload_digest=ref.expected_payload_digest)
        return (canonical, resolved)

    def _resolve_scene_state(self, payload: Any, *, allow_compatibility_components: bool=False) -> SceneState:
        scene = payload if isinstance(payload, SceneState) else SceneState.from_payload(payload)
        if len(scene.overlays) > 1:
            raise ValueError('live scene version 1 supports at most one overlay')
        if scene.overlays and scene.overlays[0].slot_id != AGGREGATE_OVERLAY_SLOT_ID:
            raise ValueError(f'live scene version 1 supports only the fixed {AGGREGATE_OVERLAY_SLOT_ID!r} overlay slot')
        background, _ = self._resolve_component_ref(scene.background, expected_role='background', allow_compatibility_component=allow_compatibility_components)
        fallback, _ = self._resolve_component_ref(scene.known_python_fallback, expected_role='background', allow_compatibility_component=allow_compatibility_components)
        overlays = []
        for overlay in scene.overlays:
            component, _ = self._resolve_component_ref(overlay.component, expected_role='overlay', allow_compatibility_component=allow_compatibility_components)
            overlays.append(OverlayRef(slot_id=overlay.slot_id, component=component, enabled=overlay.enabled, opacity=overlay.opacity, placement=overlay.placement, stale_policy=overlay.stale_policy))
        return SceneState(revision=scene.revision, background=background, overlays=tuple(overlays), known_python_fallback=fallback)

    @staticmethod
    def _component_preset_status(ref: ComponentRef) -> Dict[str, Any]:
        resolved = dict(ref.resolved_parameters)
        dirty = bool(ref.parameter_overrides)
        diagnostic = 'live_overrides' if dirty else 'preset_snapshot' if ref.preset_id else 'direct_parameters'
        return {'preset_id': ref.preset_id, 'preset_fingerprint': ref.preset_fingerprint, 'resolved_fingerprint': AnimationManager._component_snapshot_fingerprint(resolved), 'is_dirty': dirty, 'diagnostic': diagnostic}

    def _plugin_role(self, plugin_name: str) -> str:
        """Resolve a role through the Phase 2B Python compatibility adapter."""
        if plugin_name == 'clock_overlay':
            return 'overlay'
        manifest = self.plugin_loader.plugin_manifests.get(plugin_name) or {}
        role = manifest.get('role')
        if role is None and isinstance(manifest.get('component'), dict):
            role = manifest['component'].get('role')
        if role is not None:
            return str(role)
        animation_class = self.plugin_loader.loaded_plugins.get(plugin_name)
        if plugin_name == 'clock' or (isinstance(animation_class, type) and issubclass(animation_class, StatefulAnimationBase)):
            return 'full_scene'
        return 'background'

    def _scene_v1_component_descriptor(self, descriptor: Dict[str, Any]) -> Dict[str, Any]:
        """Translate the strict product descriptor at the Scene v1 boundary."""
        item = dict(descriptor)
        if item.get('provider', 'python') == 'python':
            component_id = item.get('plugin_id')
            animation_class = self.plugin_loader.loaded_plugins.get(component_id)
            descriptor_getter = getattr(animation_class, 'component_descriptor', None)
            try:
                product_descriptor = descriptor_getter() if callable(descriptor_getter) else None
            except (AttributeError, TypeError, ValueError):
                product_descriptor = None
            product_defaults = product_descriptor.default_parameters() if isinstance(product_descriptor, ComponentDescriptor) else None
            product_parameters = set(product_defaults) if product_defaults is not None else None
            schema = item.get('parameter_schema')
            if isinstance(schema, dict):
                item['parameter_schema'] = {name: definition for name, definition in schema.items() if name not in SCENE_EXTERNAL_COMPONENT_PARAMETERS and (product_parameters is None or name in product_parameters)}
            if product_defaults is not None:
                item['defaults'] = product_defaults
            elif isinstance(item.get('defaults'), dict):
                item['defaults'] = {name: value for name, value in item['defaults'].items() if name not in SCENE_EXTERNAL_COMPONENT_PARAMETERS}
            if component_id == 'clock_overlay':
                item['role'] = 'overlay'
        return item

    def get_animation_info(self, animation_name: str) -> Optional[Dict[str, Any]]:
        """Get legacy RGB-animation details, excluding scene-only overlays."""
        if self._plugin_role(animation_name) == 'overlay':
            return None
        return self.plugin_loader.get_plugin_info(animation_name)

    def _component_config(self, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        effective = dict(config or {})
        effective['plant_aware'] = self.plant_aware if self._legacy_plant_aware_bridge else False
        effective['plant_modifiers'] = self.plant_modifier_state.to_dict()
        return effective

    def _scene_component_config(self, config: Optional[Dict[str, Any]], *, allow_compatibility: Optional[bool]=None) -> Dict[str, Any]:
        """Keep installation-owned state out of strict Scene v2 parameters."""
        compatibility = getattr(self, '_scene_allows_compatibility_components', False) if allow_compatibility is None else bool(allow_compatibility)
        return self._component_config(config) if compatibility else dict(config or {})

    def _scene_state_guard(self) -> threading.RLock:
        lock = getattr(self, '_scene_lock', None)
        if lock is None:
            lock = threading.RLock()
            self._scene_lock = lock
        return lock

    def _new_scene_component(self, name: str, animation: AnimationBase, config: Optional[Dict[str, Any]], *, started_at: float, ref: Optional[ComponentRef]=None) -> Dict[str, Any]:
        return {'name': name, 'animation': animation, 'config': dict(config or {}), 'ref': ref, 'started_at': started_at, 'last_unscaled_elapsed': 0.0, 'scaled_elapsed': 0.0, 'frame_index': 0, 'cached_frame': None, 'grade_state': self._empty_presentation_state(), 'force_changed': True, 'calls': 0, 'changed_calls': 0, 'render_count': 0, 'last_revision': None}

    @staticmethod
    def _cleanup_scene_component(component: Optional[Dict[str, Any]]) -> None:
        if not component or component.get('cleaned'):
            return
        component['cleaned'] = True
        animation = component.get('animation')
        if animation is None:
            return
        try:
            animation.stop()
        finally:
            animation.cleanup()

    def _reset_run_counters(self) -> None:
        with self._run_state_guard():
            self._run_generation = getattr(self, '_run_generation', 0) + 1
            self.is_running = True
            self.stop_event.clear()
        self.frame_count = 0
        self.frames_presented = 0
        self.unchanged_frames_skipped = 0
        self.frame_timestamps.clear()
        with self.perf_lock:
            self.perf_samples.clear()
            self._last_perf_sample = {}
        self.start_time = time.perf_counter()
        self._scaled_elapsed = 0.0
        self._last_unscaled_elapsed = 0.0
        self._scene_epoch = time.time_ns() & (1 << 64) - 1
        self._presentation_refresh_pending = True
        self._live_presentation_state = self._empty_presentation_state()

    def _launch_animation_loop(self) -> None:
        with self._run_state_guard():
            run_generation = self._run_generation
        self.animation_thread = threading.Thread(target=self._animation_loop, args=(run_generation,), daemon=True)
        self.animation_thread.start()

    def _run_state_guard(self) -> threading.RLock:
        lock = getattr(self, '_run_state_lock', None)
        if lock is None:
            lock = threading.RLock()
            self._run_state_lock = lock
        return lock

    def _run_is_active(self, run_generation: int) -> bool:
        with self._run_state_guard():
            return bool(self.is_running and (not self.stop_event.is_set()) and (getattr(self, '_run_generation', 0) == run_generation))

    def _run_owns_generation(self, run_generation: int) -> bool:
        """Return false only when an external stop/restart revoked this loop."""
        with self._run_state_guard():
            return bool(not self.stop_event.is_set() and getattr(self, '_run_generation', 0) == run_generation)

    def start_composed_scene(self, background_name: str, background_config: Optional[Dict[str, Any]]=None, overlay_name: str='clock_overlay', overlay_config: Optional[Dict[str, Any]]=None, overlay_opacity: int=255, strip_offset: int=0, led_offset: int=0) -> bool:
        """Compatibility wrapper for the fixed Phase 2B composed-scene API."""
        try:
            background_parameters = self._scene_parameter_payload(background_config)
            overlay_parameters = self._scene_parameter_payload(overlay_config)
            background = ComponentRef(plugin_id=background_name, provider=ComponentProvider.PYTHON, resolved_parameters=background_parameters)
            scene = SceneState(revision=0, background=background, overlays=(OverlayRef(slot_id=AGGREGATE_OVERLAY_SLOT_ID, component=ComponentRef(plugin_id=overlay_name, provider=ComponentProvider.PYTHON, resolved_parameters=overlay_parameters), enabled=True, opacity=overlay_opacity, placement=OverlayPlacement(strip_translation=strip_offset, led_translation=led_offset, clip_policy=ClipPolicy.CLIP_TO_WALL), stale_policy=StalePolicy(ForegroundStalePolicy.HOLD)),), known_python_fallback=background)
        except (TypeError, ValueError) as exc:
            print(f'✗ Invalid composed scene: {exc}')
            return False
        return self.start_scene(scene, _allow_compatibility_components=True)

    def start_scene(self, scene_payload: Any, *, _compatibility_animation: bool=False, _allow_compatibility_components: bool=False) -> bool:
        """Validate and start a complete fixed-slot scene atomically."""
        if isinstance(scene_payload, dict) and scene_payload.get('schema') == 'ledgrid.scene.v2':
            return self.start_canonical_scene(scene_payload)
        try:
            scene = self._resolve_scene_state(scene_payload, allow_compatibility_components=_allow_compatibility_components)
        except (TypeError, ValueError) as exc:
            print(f'✗ Invalid scene: {exc}')
            return False
        background_class = self.plugin_loader.get_plugin(scene.background.plugin_id)
        overlay_ref = scene.overlays[0] if scene.overlays else None
        overlay_class = self.plugin_loader.get_plugin(overlay_ref.component.plugin_id) if overlay_ref else None
        if background_class is None or (overlay_ref and overlay_class is None):
            return False
        background = None
        overlay = None
        background_component = None
        overlay_component = None
        presentation_taken_over = False
        try:
            background_config = dict(scene.background.resolved_parameters)
            background = background_class(self.controller, self._scene_component_config(background_config, allow_compatibility=_allow_compatibility_components))
            if isinstance(background, StatefulAnimationBase):
                raise TypeError('Stateful animations cannot participate in composed scenes')
            if overlay_ref is not None:
                overlay_config = dict(overlay_ref.component.resolved_parameters)
                overlay = overlay_class(self.controller, self._scene_component_config(overlay_config, allow_compatibility=_allow_compatibility_components))
                if isinstance(overlay, StatefulAnimationBase):
                    raise TypeError('Stateful animations cannot participate in composed scenes')
            if hasattr(self.controller, 'configure'):
                try:
                    with self._presentation_io_guard():
                        self.controller.configure()
                except Exception as controller_error:
                    print(f'⚠️ Controller configure failed: {controller_error}')
            background.start()
            started_at = time.perf_counter()
            background_component = self._new_scene_component(scene.background.plugin_id, background, background_config, started_at=started_at, ref=scene.background)
            if overlay_ref is not None:
                overlay.start()
                overlay_component = self._new_scene_component(overlay_ref.component.plugin_id, overlay, overlay_config, started_at=time.perf_counter(), ref=overlay_ref.component)
                overlay_component.update({'enabled': overlay_ref.enabled, 'opacity': overlay_ref.opacity, 'strip_offset': overlay_ref.placement.strip_translation, 'led_offset': overlay_ref.placement.led_translation, 'stale_policy': overlay_ref.stale_policy})
            self.stop_animation(clear_leds=True)
            presentation_taken_over = True
            self._reset_run_counters()
            with self._scene_lock:
                self._scene_mode = True
                self._scene_compatibility_mode = bool(_compatibility_animation)
                self._scene_allows_compatibility_components = bool(_allow_compatibility_components)
                self._scene_background = background_component
                self._scene_overlay = overlay_component
                self._requested_scene = None
                self._active_scene_state = scene
                self._scene_compositor = HostSceneCompositor(self.controller.strip_count, self.controller.leds_per_strip)
                self._scene_final_presentation_state = self._empty_presentation_state()
                self.current_animation = background
                self.current_animation_name = scene.background.plugin_id
                self.current_animation_hash = self._compute_animation_hash(scene.background.plugin_id)
                self.current_preset = self._legacy_preset_from_ref(scene.background)
                frame, _changed, _dirty = self._render_composed_scene_frame()
                with self.frame_data_lock:
                    self.current_frame_data = frame
            self._remember_active_state(scene.background.plugin_id, background_config, self.current_preset)
            self._launch_animation_loop()
            label = scene.background.plugin_id
            if overlay_ref:
                label += f' + {overlay_ref.component.plugin_id}'
            print(f'✓ Started scene: {label}')
            return True
        except Exception as exc:
            if presentation_taken_over:
                self.is_running = False
                self.stop_event.set()
            for component in (overlay_component, background_component):
                try:
                    self._cleanup_scene_component(component)
                except Exception:
                    traceback.print_exc()
            for animation, component in ((overlay, overlay_component), (background, background_component)):
                if animation is not None and component is None:
                    try:
                        animation.stop()
                        animation.cleanup()
                    except Exception:
                        traceback.print_exc()
            if presentation_taken_over:
                with self._scene_lock:
                    self._clear_scene_state()
            print(f'✗ Failed to start composed scene: {exc}')
            traceback.print_exc()
            return False


    def _clear_scene_state(self) -> None:
        self._canonical_receiver_scene = None
        self._canonical_receiver_runtime = None
        self._canonical_host_full_mode = False
        self._scene_mode = False
        self._scene_background = None
        self._scene_overlay = None
        self._scene_compositor = None
        self._active_scene_state = None
        self._scene_compatibility_mode = False
        self._scene_allows_compatibility_components = False
        self._scene_final_presentation_state = self._empty_presentation_state()
        self.current_animation = None
        self.current_animation_name = None
        self.current_animation_hash = None
        self.current_preset = None

    def start_animation(self, animation_name: str, config: Dict[str, Any]=None, preset: Optional[Dict[str, Any]]=None) -> bool:
        """
        Start playing an animation

        Args:
            animation_name: Name of animation plugin to start
            config: Animation configuration parameters
            preset: Optional selected-preset metadata for dashboard status

        Returns:
            True if started successfully
        """
        restore_config = dict(config or {})
        try:
            # Home Assistant and saved animation selections use the same host
            # Scene renderer as Composer when the catalog has a current instrument.
            catalog = self.scene_v2_component_catalog()
            descriptor = next((item for item in catalog.descriptors
                if item.component_id == animation_name and item.provider.value == 'python'
                and item.role.value == 'animation'), None)
            if descriptor is not None:
                scene = {
                    'schema': 'ledgrid.scene.v2',
                    'background': {'component_id':'solid_background','version':1,
                        'provider':'python','role':'background','parameters':{}},
                    'animation': {'component_id':descriptor.component_id,'version':descriptor.version,
                        'provider':'python','role':'animation','parameters':self._scene_parameter_payload(config)},
                    'widgets': [], 'plants': {'effects':self.plant_modifier_state.to_dict()},
                    'look': {'palette_id':'neutral','pace':1.0,'presentation_brightness':1.0},
                }
                started = self.start_canonical_scene(scene)
                if started:
                    self.current_preset = self._normalize_current_preset(preset, animation_name)
                    self._remember_active_state(animation_name,restore_config,self.current_preset)
                return started
            if self._plugin_role(animation_name) == 'overlay':
                print(f'✗ Overlay component {animation_name} requires start_composed_scene()')
                return False
            animation_class = self.plugin_loader.get_plugin(animation_name)
            if animation_class is None:
                print(f'✗ Animation not found: {animation_name}')
                return False
            if self._plugin_role(animation_name) == 'background' and (not issubclass(animation_class, StatefulAnimationBase)):
                parameters = self._scene_parameter_payload(config)
                selection = self._normalize_current_preset(preset, animation_name)
                preset_id = selection['preset_id'] if selection else None
                preset_fingerprint = None
                if selection:
                    preset_fingerprint = component_preset_fingerprint(animation_name, selection['preset_id'], parameters)
                ref = ComponentRef(plugin_id=animation_name, provider=ComponentProvider.PYTHON, preset_id=preset_id, preset_fingerprint=preset_fingerprint, resolved_parameters=parameters)
                started = self.start_scene(SceneState(0, ref, (), ref), _compatibility_animation=True, _allow_compatibility_components=True)
                if started:
                    self.current_preset = selection
                    self._sync_last_active_preset()
                return started
            self.stop_animation(clear_leds=True)
            effective_config = self._component_config(config)
            self._requested_scene = None
            self.current_animation = animation_class(self.controller, effective_config)
            self.current_animation_name = animation_name
            self.current_animation_hash = self._compute_animation_hash(animation_name)
            self.current_preset = self._normalize_current_preset(preset, animation_name)
            print(f'🔍 Animation instance created: {type(self.current_animation)}')
            print(f'🔍 Is StatefulAnimationBase? {isinstance(self.current_animation, StatefulAnimationBase)}')
            if hasattr(self.controller, 'configure'):
                try:
                    with self._presentation_io_guard():
                        self.controller.configure()
                except Exception as controller_error:
                    print(f'⚠️ Controller configure failed: {controller_error}')
            self.current_animation.start()
            self._reset_run_counters()
            self._refresh_active_presentation_context()
            if isinstance(self.current_animation, StatefulAnimationBase):
                print(f'✓ Started stateful animation: {animation_name}')
            else:
                self._launch_animation_loop()
                print(f'✓ Started frame-based animation: {animation_name}')
            self._remember_active_state(animation_name, restore_config, self.current_preset)
            return True
        except Exception as e:
            print(f'✗ Failed to start animation {animation_name}: {e}')
            traceback.print_exc()
            return False

    @staticmethod
    def _legacy_preset_from_ref(ref: ComponentRef) -> Optional[Dict[str, Any]]:
        if ref.preset_id is None:
            return None
        return {'preset_id': ref.preset_id, 'name': ref.preset_id.replace('_', ' ').replace('-', ' ').title(), 'animation': ref.plugin_id, 'is_dirty': bool(ref.parameter_overrides)}

    def stop_animation(self, clear_leds=True):
        """Stop host playback. A failed output command may leave partial output."""
        with self._run_state_guard():
            self.is_running = False
            self.stop_event.set()
            self._run_generation = getattr(self, "_run_generation", 0) + 1
        thread = self.animation_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
            if thread.is_alive():
                self._playback_error = 'renderer did not stop within two seconds'
                return False
        with self._scene_state_guard():
            if self._scene_mode:
                self._cleanup_scene_component(self._scene_overlay)
                self._cleanup_scene_component(self._scene_background)
            elif self.current_animation is not None:
                self.current_animation.stop()
                self.current_animation.cleanup()
            self._clear_scene_state()
            self.animation_thread = None
        if clear_leds:
            with self._presentation_io_guard():
                self.controller.clear()
        return True

    def update_animation_parameters(self, params: Dict[str, Any]) -> bool:
        """Update current animation parameters in real-time"""
        if self._scene_mode:
            return self.update_scene_component('background', params=params)
        if self.current_animation:
            try:
                requested_params = dict(params)
                effective_params = dict(requested_params)
                effective_params['plant_aware'] = self.plant_aware if self._legacy_plant_aware_bridge else False
                effective_params['plant_modifiers'] = self.plant_modifier_state.to_dict()
                self.current_animation.update_parameters(effective_params)
                if self.current_preset is not None:
                    self.current_preset['is_dirty'] = True
                if self._last_active_state is not None and self._last_active_state.get('animation') == self.current_animation_name:
                    restore_params = {key: value for key, value in requested_params.items() if key not in {'plant_aware', 'plant_modifiers'}}
                    self._last_active_state['config'].update(restore_params)
                    self._sync_last_active_preset()
                print(f'✓ Updated animation parameters: {effective_params}')
                return True
            except Exception as e:
                print(f'✗ Failed to update parameters: {e}')
                return False
        return False

    def update_overlay(self, params: Optional[Dict[str, Any]]=None, *, opacity: Optional[int]=None, strip_offset: Optional[int]=None, led_offset: Optional[int]=None) -> bool:
        """Update the fixed foreground without restarting the background."""
        placement = None
        if strip_offset is not None or led_offset is not None:
            current = self._scene_overlay
            if current is None:
                return False
            placement = {'strip_translation': current['strip_offset'] if strip_offset is None else strip_offset, 'led_translation': current['led_offset'] if led_offset is None else led_offset, 'clip_policy': ClipPolicy.CLIP_TO_WALL.value}
        return self.update_scene_component('overlay', params=params, opacity=opacity, placement=placement)

    def update_overlay_parameters(self, params: Dict[str, Any]) -> bool:
        return self.update_overlay(params)

    def set_overlay_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise TypeError('overlay enabled state must be boolean')
        return self.update_scene_component('overlay', enabled=enabled)

    def enable_overlay(self) -> bool:
        return self.set_overlay_enabled(True)

    def disable_overlay(self) -> bool:
        return self.set_overlay_enabled(False)

    def remove_overlay(self) -> bool:
        return self.update_scene_component('overlay', remove=True)

    def get_scene_state(self) -> Optional[Dict[str, Any]]:
        """Return a detached, scene-only serialized snapshot of live state."""
        with self._scene_state_guard():
            canonical = getattr(self, '_canonical_receiver_scene', None)
            if canonical is not None and self._scene_mode:
                return copy.deepcopy(canonical.scene)
            return self._active_scene_state.to_dict() if self._scene_mode and self._active_scene_state is not None else None

    def read_scene(self) -> Optional[Dict[str, Any]]:
        return self.get_scene_state()

    def stop_scene(self, clear_leds: bool=True) -> bool:
        if not self._scene_mode:
            return False
        return bool(self.stop_animation(clear_leds=clear_leds))

    def update_scene_component(self, target: str, params: Optional[Dict[str, Any]]=None, *, component: Optional[Any]=None, enabled: Optional[bool]=None, opacity: Optional[int]=None, placement: Optional[Any]=None, stale_policy: Optional[Any]=None, remove: bool=False) -> bool:
        """Apply one live component edit without restarting the other component."""
        if isinstance(params, dict) and set(params) & {'params', 'parameter_overrides', 'component', 'enabled', 'opacity', 'placement', 'stale_policy', 'remove'}:
            update = dict(params)
            params = update.get('params', update.get('parameter_overrides'))
            component = update.get('component', component)
            enabled = update.get('enabled', enabled)
            opacity = update.get('opacity', opacity)
            placement = update.get('placement', placement)
            stale_policy = update.get('stale_policy', stale_policy)
            remove = update.get('remove', remove)
        if not isinstance(remove, bool):
            raise TypeError('scene overlay remove state must be boolean')
        if target not in {'background', 'overlay', AGGREGATE_OVERLAY_SLOT_ID}:
            raise ValueError("scene component target must be 'background' or 'overlay'")
        target = 'overlay' if target == AGGREGATE_OVERLAY_SLOT_ID else target
        if remove and target != 'overlay':
            raise ValueError('only the overlay component may be removed')
        if component is not None and target == 'background':
            raise ValueError('replace a background by applying a complete scene')
        with self._scene_state_guard():
            if not self._scene_mode or self._active_scene_state is None:
                return False
            current_scene = self._active_scene_state
            runtime = self._scene_background if target == 'background' else self._scene_overlay
            if remove:
                if runtime is None:
                    return False
                self._scene_overlay = None
                self._active_scene_state = SceneState(current_scene.revision + 1, current_scene.background, (), current_scene.known_python_fallback)
                try:
                    self._cleanup_scene_component(runtime)
                except Exception as exc:
                    print(f'⚠️ Overlay cleanup failed: {exc}')
                return True
            if target == 'overlay' and runtime is None and (component is None):
                return False
            try:
                if target == 'background':
                    assert runtime is not None
                    old_ref = current_scene.background
                    requested = self._scene_parameter_payload(params)
                    if not self._scene_allows_compatibility_components:
                        self.plugin_loader.validate_component_parameters(old_ref.plugin_id, {**dict(old_ref.resolved_parameters), **requested})
                    resolved = dict(old_ref.resolved_parameters)
                    resolved.update(requested)
                    overrides = dict(old_ref.parameter_overrides)
                    overrides.update(requested)
                    new_ref = ComponentRef(plugin_id=old_ref.plugin_id, provider=old_ref.provider, preset_id=old_ref.preset_id, preset_fingerprint=old_ref.preset_fingerprint, parameter_overrides=overrides, resolved_parameters=resolved)
                    runtime['animation'].update_parameters(self._component_config(requested))
                    runtime['config'] = resolved
                    runtime['ref'] = new_ref
                    self._active_scene_state = SceneState(current_scene.revision + 1, new_ref, current_scene.overlays, current_scene.known_python_fallback)
                    if self.current_preset is not None:
                        self.current_preset['is_dirty'] = bool(overrides)
                    self._remember_active_state(new_ref.plugin_id, resolved, self.current_preset)
                    return True
                old_overlay = current_scene.overlays[0] if current_scene.overlays else None
                if component is None:
                    assert old_overlay is not None and runtime is not None
                    new_component = old_overlay.component
                else:
                    new_component = component if isinstance(component, ComponentRef) else ComponentRef.from_payload(component)
                requested = self._scene_parameter_payload(params)
                if not self._scene_allows_compatibility_components:
                    self.plugin_loader.validate_component_parameters(new_component.plugin_id, {**dict(new_component.resolved_parameters), **requested})
                resolved = dict(new_component.resolved_parameters)
                resolved.update(requested)
                overrides = dict(new_component.parameter_overrides)
                overrides.update(requested)
                new_component = ComponentRef(plugin_id=new_component.plugin_id, provider=new_component.provider, preset_id=new_component.preset_id, preset_fingerprint=new_component.preset_fingerprint, parameter_overrides=overrides, resolved_parameters=resolved)
                old_placement = old_overlay.placement if old_overlay else OverlayPlacement()
                resolved_placement = placement if isinstance(placement, OverlayPlacement) else OverlayPlacement.from_payload(placement) if placement is not None else old_placement
                old_stale = old_overlay.stale_policy if old_overlay else StalePolicy(ForegroundStalePolicy.HOLD)
                resolved_stale = stale_policy if isinstance(stale_policy, StalePolicy) else StalePolicy.from_payload(stale_policy) if stale_policy is not None else old_stale
                overlay_ref = OverlayRef(slot_id=AGGREGATE_OVERLAY_SLOT_ID, component=new_component, enabled=(old_overlay.enabled if enabled is None and old_overlay else True) if enabled is None else enabled, opacity=(old_overlay.opacity if opacity is None and old_overlay else 255) if opacity is None else opacity, placement=resolved_placement, stale_policy=resolved_stale)
                candidate = self._resolve_scene_state(SceneState(current_scene.revision + 1, current_scene.background, (overlay_ref,), current_scene.known_python_fallback), allow_compatibility_components=self._scene_allows_compatibility_components)
                overlay_ref = candidate.overlays[0]
                replacing = runtime is None or runtime['name'] != new_component.plugin_id
                if replacing:
                    animation_class = self.plugin_loader.get_plugin(overlay_ref.component.plugin_id)
                    animation = animation_class(self.controller, self._component_config(dict(overlay_ref.component.resolved_parameters)))
                    new_runtime = None
                    try:
                        animation.start()
                        new_runtime = self._new_scene_component(overlay_ref.component.plugin_id, animation, dict(overlay_ref.component.resolved_parameters), started_at=time.perf_counter(), ref=overlay_ref.component)
                        new_runtime.update({'enabled': overlay_ref.enabled, 'opacity': overlay_ref.opacity, 'strip_offset': overlay_ref.placement.strip_translation, 'led_offset': overlay_ref.placement.led_translation, 'stale_policy': overlay_ref.stale_policy})
                        with self._presentation_state_guard():
                            self._render_scene_component(new_runtime, now=new_runtime['started_at'], resolved_vibe=self._resolved_vibe, operator_tempo=self.animation_speed_scale, overlay=True)
                    except Exception:
                        if new_runtime is not None:
                            self._cleanup_scene_component(new_runtime)
                        else:
                            animation.stop()
                            animation.cleanup()
                        raise
                    old_runtime = runtime
                    self._scene_overlay = new_runtime
                    runtime = new_runtime
                    if old_runtime is not None:
                        self._cleanup_scene_component(old_runtime)
                else:
                    runtime['animation'].update_parameters(self._component_config(requested))
                    runtime['config'] = dict(overlay_ref.component.resolved_parameters)
                    runtime['ref'] = overlay_ref.component
                    runtime['enabled'] = overlay_ref.enabled
                    runtime['opacity'] = overlay_ref.opacity
                    runtime['strip_offset'] = overlay_ref.placement.strip_translation
                    runtime['led_offset'] = overlay_ref.placement.led_translation
                    runtime['stale_policy'] = overlay_ref.stale_policy
                self._active_scene_state = candidate
                return True
            except Exception as exc:
                print(f'✗ Failed to update scene {target}: {exc}')
                return False

    def apply_scene(self, scene_payload: Any) -> bool:
        """Reconcile a scene, retaining matching live component instances."""
        try:
            scene = self._resolve_scene_state(scene_payload)
        except (TypeError, ValueError) as exc:
            print(f'✗ Invalid scene: {exc}')
            return False
        with self._scene_state_guard():
            current = self._active_scene_state
            active = self._scene_mode and current is not None
        if not active or current.background.plugin_id != scene.background.plugin_id:
            return self.start_scene(scene)
        if not self.update_scene_component('background', params=dict(scene.background.resolved_parameters)):
            return False
        old_overlay = current.overlays[0] if current.overlays else None
        new_overlay = scene.overlays[0] if scene.overlays else None
        if new_overlay is None:
            if old_overlay is not None and (not self.remove_overlay()):
                return False
        elif not self.update_scene_component('overlay', params=dict(new_overlay.component.resolved_parameters), component=new_overlay.component if old_overlay is None or old_overlay.component.plugin_id != new_overlay.component.plugin_id else None, enabled=new_overlay.enabled, opacity=new_overlay.opacity, placement=new_overlay.placement, stale_policy=new_overlay.stale_policy):
            return False
        with self._scene_state_guard():
            self._active_scene_state = scene
            if self._scene_background is not None:
                self._scene_background['ref'] = scene.background
            if self._scene_overlay is not None and scene.overlays:
                self._scene_overlay['ref'] = scene.overlays[0].component
        return True

    @staticmethod
    def _normalize_current_preset(preset: Optional[Dict[str, Any]], animation_name: str) -> Optional[Dict[str, Any]]:
        """Return the small, safe preset selection shape exposed in status."""
        if not isinstance(preset, dict):
            return None
        preset_id = preset.get('preset_id')
        name = preset.get('name')
        preset_animation = preset.get('animation', animation_name)
        if not all((isinstance(value, str) and value for value in (preset_id, name, preset_animation))):
            return None
        if preset_animation != animation_name:
            return None
        return {'preset_id': preset_id, 'name': name, 'animation': preset_animation, 'is_dirty': bool(preset.get('is_dirty', False))}

    def set_current_preset(self, preset: Dict[str, Any]) -> bool:
        """Mark the running animation as the saved preset without restarting it."""
        if not self.is_running or not self.current_animation_name:
            return False
        selection = self._normalize_current_preset(preset, self.current_animation_name)
        if selection is None:
            return False
        selection['is_dirty'] = False
        self.current_preset = selection
        self._sync_last_active_preset()
        return True

    def get_current_status(self):
        scene = self.get_scene_state()
        stats = self.controller.get_stats() if hasattr(self.controller, 'get_stats') else {}
        animation_info = self.current_animation.get_info() if self.current_animation else None
        runtime_stats = self.current_animation.get_runtime_stats() if self.current_animation else {}
        status = {
            'is_running': self.is_running, 'mode': 'scene' if self.is_running and self._scene_mode and not self._scene_compatibility_mode else 'animation' if self.is_running else 'idle',
            'current_animation': self.current_animation_name if self.is_running else None,
            'current_preset': copy.deepcopy(self.current_preset), 'scene_state': scene,
            'requested_scene': copy.deepcopy(getattr(self, '_requested_scene', None)),
            'controller_playback': {'state': 'running' if self.is_running else 'stopped',
                'render_mode': 'host_full_rgb', 'error': getattr(self, '_playback_error', None)},
            'receiver_connectivity': stats.get('devices', []),
            'brightness': self.output_brightness, 'target_fps': self.target_fps,
            'animation_speed_scale': self.animation_speed_scale,
            'frame_count': self.frame_count, 'frames_presented': self.frames_presented,
            'unchanged_frames_skipped': self.unchanged_frames_skipped,
            'actual_fps': self._calculate_fps(), 'pipeline_fps': self._compute_driver_fps(stats),
            'uptime': time.perf_counter()-self.start_time if self.is_running else 0,
            'animation_hash': self.current_animation_hash, 'animation_info': animation_info,
            'animation_stats': runtime_stats, 'interaction_types': (animation_info or {}).get('interaction_types', []),
            'vibe': self.get_vibe_status(), 'plant_aware': self.plant_aware,
            'plant_modifiers': self.plant_modifier_state.to_dict(),
            'led_info': {'total_leds': self.controller.total_leds, 'strip_count': self.controller.strip_count, 'leds_per_strip': self.controller.leds_per_strip},
            'driver_stats': stats, 'performance': self._get_perf_summary(),
        }
        if scene is not None:
            status['scene'] = self._scene_status_snapshot()
        return status

    def _scene_status_snapshot(self):
        with self._scene_lock:
            if self._canonical_host_full_mode:
                return {'provider_mode':'host_full_rgb', 'state':'running' if self.is_running else 'stopped',
                    'background':copy.deepcopy(self._canonical_receiver_scene.scene['background']),
                    'animation':copy.deepcopy(self._canonical_receiver_scene.scene['animation'])}
            if not self._scene_mode or self._scene_background is None:
                return None
            background=self._scene_background
            overlay=self._scene_overlay
            return {'provider_mode':'host_full_rgb','background':{'name':background['name'],
                'frame_count':background['frame_index'],'calls':background['calls'],
                'component':background['ref'].to_dict() if background.get('ref') else None},
                'overlay':None if overlay is None else {'name':overlay['name'],'enabled':overlay['enabled'],
                    'opacity':overlay['opacity'],'strip_offset':overlay['strip_offset'],'led_offset':overlay['led_offset'],
                    'component':overlay['ref'].to_dict() if overlay.get('ref') else None}}

    def trigger_random_hole(self):
        """Request the current animation to spawn a random puncture if supported."""
        if not self.current_animation:
            return False
        if hasattr(self.current_animation, 'trigger_random_hole'):
            try:
                self.current_animation.trigger_random_hole()
                return True
            except Exception as exc:
                print(f'⚠️ Failed to trigger hole: {exc}')
        return False

    def trigger_hole(self, x: float, y: float, radius: Optional[float]=None):
        """Request a puncture at an exact animation-grid coordinate."""
        if not self.current_animation or not hasattr(self.current_animation, 'trigger_hole'):
            return False
        try:
            return bool(self.current_animation.trigger_hole(x, y, radius))
        except Exception as exc:
            print(f'⚠️ Failed to trigger positioned hole: {exc}')
            return False

    @staticmethod
    def _validated_interaction(animation: AnimationBase, kind: str, x: float, y: float, strength: float) -> tuple[str, float, float, float]:
        kind = str(kind or 'primary')
        if kind not in animation.INTERACTION_TYPES:
            raise ValueError(f'interaction {kind!r} is not supported')
        values = (float(x), float(y), float(strength))
        if not all((math.isfinite(value) for value in values)):
            raise ValueError('interaction coordinates and strength must be finite')
        x_value, y_value, strength_value = values
        width, height = animation.get_strip_info()
        if not 0.0 <= x_value < width or not 0.0 <= y_value < height:
            raise ValueError('interaction coordinates are outside the animation grid')
        if not 0.0 <= strength_value <= 1.0:
            raise ValueError('interaction strength must be between 0 and 1')
        return (kind, x_value, y_value, strength_value)

    def dispatch_interaction(self, kind: str, x: float, y: float, strength: float=1.0, *, target: str='background') -> bool:
        """Dispatch to one explicit scene component; legacy defaults to background."""
        if target not in {'background', 'overlay'}:
            raise ValueError("interaction target must be 'background' or 'overlay'")
        if self._scene_mode:
            with self._scene_lock:
                component = self._scene_background if target == 'background' else self._scene_overlay
                animation = component['animation'] if component else None
        else:
            animation = self.current_animation if target == 'background' else None
        if not animation:
            return False
        event = self._validated_interaction(animation, kind, x, y, strength)
        return bool(animation.handle_interaction(*event))

    def _compute_animation_hash(self, animation_name: str) -> Optional[str]:
        path = self.plugin_loader.get_plugin_file(animation_name)
        if not path:
            return None
        try:
            hasher = hashlib.sha256()
            with open(path, 'rb') as fh:
                for chunk in iter(lambda: fh.read(8192), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError as exc:
            print(f'⚠️ Failed to hash animation file {path}: {exc}')
            return None

    def get_current_frame(self) -> Dict[str, Any]:
        """Get current animation frame data for web rendering"""
        with self.frame_data_lock:
            raw = self.current_frame_data
            if isinstance(raw, np.ndarray):
                frame_data = raw.tolist()
            else:
                frame_data = list(raw)
        encoded_frame = encode_frame_data(frame_data)
        mode = 'animation' if self.is_running and self._scene_compatibility_mode else 'scene' if self.is_running and self._scene_mode else 'animation' if self.is_running else 'idle'
        displayed_animation = self.current_animation_name if self.is_running else None
        return {'frame_data_encoded': encoded_frame, 'frame_data_length': len(frame_data), 'frame_encoding': FRAME_ENCODING_NAME if encoded_frame else None, 'mode': mode, 'led_info': {'total_leds': self.controller.total_leds, 'strip_count': self.controller.strip_count, 'leds_per_strip': self.controller.leds_per_strip}, 'is_running': self.is_running, 'frame_count': self.frame_count, 'current_animation': displayed_animation, 'scene': None if self._scene_compatibility_mode else self._scene_status_snapshot(), 'timestamp': time.time()}

    def _preview_config(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        effective = dict(params or {})
        effective['plant_aware'] = self.plant_aware if self._legacy_plant_aware_bridge else False
        effective['plant_modifiers'] = self.plant_modifier_state.to_dict()
        return effective

    def _preview_session_for(self, animation_name: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if animation_name not in self.plugin_loader.loaded_plugins:
            raise ValueError(f"Animation '{animation_name}' not found")
        if self._plugin_role(animation_name) == 'overlay':
            raise ValueError(f'Overlay component {animation_name!r} requires get_scene_preview()')
        self.preview_controller.strip_count = self.controller.strip_count
        self.preview_controller.leds_per_strip = self.controller.leds_per_strip
        self.preview_controller.total_leds = self.controller.total_leds
        authored = dict(params or {})
        fingerprint = hashlib.sha256(json.dumps(authored, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
        geometry = (self.preview_controller.strip_count, self.preview_controller.leds_per_strip)
        now = time.monotonic()
        session = self._preview_session
        expired = bool(session and now - float(session['last_access']) > self._preview_session_ttl)
        if session is None or expired or session['animation_name'] != animation_name or (session['fingerprint'] != fingerprint) or (session['geometry'] != geometry):
            animation_class = self.plugin_loader.loaded_plugins[animation_name]
            animation = animation_class(self.preview_controller, self._preview_config(authored))
            session = {'animation_name': animation_name, 'fingerprint': fingerprint, 'geometry': geometry, 'animation': animation, 'started_at': now, 'last_access': now, 'frame_count': 0, 'last_unscaled_elapsed': 0.0, 'scaled_elapsed': 0.0, 'presentation_state': self._empty_presentation_state()}
            self._preview_session = session
        session['last_access'] = now
        return session

    def _render_preview(self, animation_name: str, params: Optional[Dict[str, Any]]=None, *, vibe: Optional[Any]=None) -> Dict[str, Any]:
        with self._preview_lock:
            session = self._preview_session_for(animation_name, params)
            animation = session['animation']
            elapsed = max(0.0, time.monotonic() - session['started_at'])
            with self._presentation_state_guard():
                resolved = self._resolved_vibe if vibe is None else self._canonical_vibe(vibe)
                operator_tempo = self.animation_speed_scale
            delta = max(0.0, elapsed - float(session['last_unscaled_elapsed']))
            authored_speed = self._animation_authored_speed(animation)
            vibe_tempo = self._component_tempo(resolved.profile, animation)
            session['scaled_elapsed'] += delta * authored_speed * vibe_tempo * operator_tempo
            session['last_unscaled_elapsed'] = elapsed
            context = self._runtime_context(animation, unscaled_elapsed=elapsed, scaled_elapsed=session['scaled_elapsed'], frame_index=session['frame_count'], resolved_vibe=resolved, operator_tempo_scale=operator_tempo)
            rendered = animation.generate_frame_with_context(context)
            changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
            frame_data = self._normalize_frame(rendered)
            frame_data = animation.apply_framework_plant_modifiers(frame_data, changed=changed)
            frame_data, changed = self._apply_vibe_presentation(animation, frame_data, profile=resolved.profile, changed=changed, state=session['presentation_state'], force_refresh=bool(session.pop('force_refresh', False)))
            if isinstance(frame_data, np.ndarray):
                frame_data = frame_data.tolist()
            session['frame_count'] += 1
            return {'frame_data': frame_data, 'led_info': {'total_leds': self.controller.total_leds, 'strip_count': self.controller.strip_count, 'leds_per_strip': self.controller.leds_per_strip}, 'is_running': False, 'frame_count': session['frame_count'], 'current_animation': animation_name, 'interaction_types': sorted(animation.INTERACTION_TYPES), 'timestamp': time.time(), 'preview': True, 'params': dict(params or {}), 'changed': changed, 'vibe': {'state': resolved.state.to_dict(), 'profile': resolved.profile.to_dict()}}

    def get_animation_preview(self, animation_name: str, *, vibe: Optional[Any]=None) -> Dict[str, Any]:
        """Advance and return the process-local dashboard preview session."""
        return self._render_preview(animation_name, vibe=vibe)

    def get_animation_preview_with_params(self, animation_name: str, params: Dict[str, Any], *, vibe: Optional[Any]=None) -> Dict[str, Any]:
        """Advance a preview, resetting only when authored parameters change."""
        return self._render_preview(animation_name, params, vibe=vibe)

    def get_scene_preview(self, scene_payload: Any, background_config: Optional[Dict[str, Any]]=None, overlay_name: str='clock_overlay', overlay_config: Optional[Dict[str, Any]]=None, overlay_opacity: int=255, strip_offset: int=0, led_offset: int=0, *, vibe: Optional[Any]=None, plant_modifiers: Optional[Any]=None, elapsed: float=0.0, elapsed_seconds: Optional[float]=None) -> Dict[str, Any]:
        """Render an isolated scene through the same resolver as live starts."""
        if elapsed_seconds is not None:
            elapsed = elapsed_seconds
        if not math.isfinite(float(elapsed)) or float(elapsed) < 0.0:
            raise ValueError('preview elapsed time must be finite and non-negative')
        structured = isinstance(scene_payload, (SceneState, dict))
        scene = None
        if structured:
            scene = self._resolve_scene_state(scene_payload)
            background_name = scene.background.plugin_id
            background_config = dict(scene.background.resolved_parameters)
            overlay_ref = scene.overlays[0] if scene.overlays else None
            overlay_name = overlay_ref.component.plugin_id if overlay_ref is not None else None
            overlay_config = dict(overlay_ref.component.resolved_parameters) if overlay_ref is not None else None
            if overlay_ref is not None:
                overlay_opacity = overlay_ref.opacity
                strip_offset = overlay_ref.placement.strip_translation
                led_offset = overlay_ref.placement.led_translation
        else:
            background_name = scene_payload
            overlay_ref = None
        background_class = self.plugin_loader.get_plugin(background_name)
        overlay_class = self.plugin_loader.get_plugin(overlay_name) if overlay_name else None
        if background_class is None or (not structured and self._plugin_role(background_name) != 'background'):
            raise ValueError(f'invalid scene background {background_name!r}')
        if overlay_name and (overlay_class is None or (not structured and self._plugin_role(overlay_name) != 'overlay')):
            raise ValueError(f'invalid scene overlay {overlay_name!r}')
        if issubclass(background_class, StatefulAnimationBase) or (overlay_class is not None and issubclass(overlay_class, StatefulAnimationBase)):
            raise TypeError('Stateful animations cannot participate in scene previews')
        preview_plant_state = self.plant_modifier_state if plant_modifiers is None else PlantModifierState.from_payload(plant_modifiers)

        def preview_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            effective = dict(config or {})
            effective['plant_aware'] = self.plant_aware if plant_modifiers is None and self._legacy_plant_aware_bridge else False
            effective['plant_modifiers'] = preview_plant_state.to_dict()
            return effective
        background = background_class(self.preview_controller, preview_config(background_config))
        overlay_animation = overlay_class(self.preview_controller, preview_config(overlay_config)) if overlay_class is not None else None
        components: List[Dict[str, Any]] = []
        try:
            background.start()
            background_component = self._new_scene_component(background_name, background, background_config, started_at=0.0, ref=scene.background if scene else None)
            components.append(background_component)
            overlay_component = None
            if overlay_animation is not None:
                overlay_animation.start()
                overlay_component = self._new_scene_component(overlay_name, overlay_animation, overlay_config, started_at=0.0, ref=overlay_ref.component if overlay_ref else None)
                overlay_component.update({'enabled': overlay_ref.enabled if overlay_ref else True, 'opacity': overlay_opacity, 'strip_offset': strip_offset, 'led_offset': led_offset, 'stale_policy': overlay_ref.stale_policy if overlay_ref else StalePolicy(ForegroundStalePolicy.HOLD)})
                components.append(overlay_component)
            with self._presentation_state_guard():
                resolved = self._resolved_vibe if vibe is None else self._canonical_vibe(vibe)
                operator_tempo = self.animation_speed_scale
            frame, changed, dirty_ranges = self._compose_scene_components(background_component, overlay_component, HostSceneCompositor(self.preview_controller.strip_count, self.preview_controller.leds_per_strip), self._empty_presentation_state(), now=float(elapsed), resolved_vibe=resolved, operator_tempo=operator_tempo, plant_modifiers=preview_plant_state.to_dict())
            return {'frame_data': frame.tolist(), 'led_info': {'total_leds': self.preview_controller.total_leds, 'strip_count': self.preview_controller.strip_count, 'leds_per_strip': self.preview_controller.leds_per_strip}, 'is_running': False, 'mode': 'scene', 'current_animation': background_name, 'scene': {'background': background_name, 'overlay': overlay_name, 'overlay_opacity': overlay_opacity, 'strip_offset': strip_offset, 'led_offset': led_offset}, 'frame_count': 1, 'changed': changed, 'dirty_ranges': dirty_ranges, 'preview': True, 'timestamp': time.time(), 'vibe': {'state': resolved.state.to_dict(), 'profile': resolved.profile.to_dict()}}
        finally:
            for component in reversed(components):
                self._cleanup_scene_component(component)

    def dispatch_preview_interaction(self, animation_name: str, kind: str, x: float, y: float, strength: float=1.0, params: Optional[Dict[str, Any]]=None) -> bool:
        """Apply an interaction to the isolated dashboard preview session."""
        with self._preview_lock:
            session = self._preview_session_for(animation_name, params)
            animation = session['animation']
            event = self._validated_interaction(animation, kind, x, y, strength)
            return bool(animation.handle_interaction(*event))

    def _render_scene_component(self, component: Dict[str, Any], *, now: float, resolved_vibe: ResolvedVibe, operator_tempo: float, overlay: bool, plant_modifiers: Optional[Dict[str, Any]]=None) -> BaseFrame | OverlayFrame:
        animation = component['animation']
        elapsed = max(0.0, float(now) - float(component['started_at']))
        delta = max(0.0, elapsed - component['last_unscaled_elapsed'])
        authored_speed = self._animation_authored_speed(animation)
        vibe_tempo = self._component_tempo(resolved_vibe.profile, animation)
        component['scaled_elapsed'] += delta * authored_speed * vibe_tempo * operator_tempo
        component['last_unscaled_elapsed'] = elapsed
        context = self._runtime_context(animation, unscaled_elapsed=elapsed, scaled_elapsed=component['scaled_elapsed'], frame_index=component['frame_index'], resolved_vibe=resolved_vibe, operator_tempo_scale=operator_tempo, plant_modifiers=plant_modifiers)
        animation.set_runtime_plant_modifiers(context.plant_modifiers)
        animation.set_runtime_installation_profile(context.installation_profile_view)
        resolved_renderer = getattr(animation, 'render_resolved_scene', None)
        if callable(resolved_renderer) and (not self._scene_allows_compatibility_components):
            descriptor = animation.component_descriptor()
            palette_id = {'neutral': 'neutral', 'quiet': 'mist', 'cozy': 'ember', 'vivid': 'spectrum', 'celebration': 'spectrum'}.get(resolved_vibe.state.vibe_id, 'neutral')
            canonical_scene = {'animation': {'provider': descriptor.provider.value, 'component_id': descriptor.component_id, 'version': descriptor.version, 'role': descriptor.role.value, 'parameters': dict(component['config'])}, 'look': {'palette_id': palette_id, 'pace': 1.0}, 'plants': {'effects': PlantModifierState.from_payload(context.plant_modifiers).to_dict()}}
            canonical_bytes = json.dumps(canonical_scene, sort_keys=True, separators=(',', ':')).encode('utf-8')
            required_inputs = descriptor.required_simulation_inputs + descriptor.optional_simulation_inputs
            resolved_context = ResolvedScene(canonical_scene=canonical_scene, canonical_bytes=canonical_bytes, digest=hashlib.sha256(canonical_bytes).hexdigest(), descriptor=descriptor, parameters=dict(component['config']), palette={'palette_id': palette_id} if descriptor.palette_policy.value == 'semantic' else None, phase_time=context.scaled_elapsed, plant_inputs={name: NEUTRAL_PLANT_INPUTS.get(name, 0.0) for name in required_inputs}, installation_geometry=context.installation_geometry_contact if descriptor.accepts_installation_geometry_contact else None)
            rendered = resolved_renderer(resolved_context)
        else:
            rendered = animation.generate_frame_with_context(context)
        component['calls'] += 1
        component['frame_index'] += 1
        force_changed = bool(component.pop('force_changed', False))
        if overlay:
            if not isinstance(rendered, OverlayFrame):
                raise TypeError(f"overlay {component['name']} returned {type(rendered).__name__}; expected OverlayFrame")
            if rendered.pixels.shape[0] != self.controller.total_leds:
                raise ValueError(f"overlay {component['name']} returned {rendered.pixels.shape[0]} pixels; expected {self.controller.total_leds}")
            source_changed = rendered.changed or force_changed
            if rendered.revision != component['last_revision']:
                component['render_count'] += 1
                component['last_revision'] = rendered.revision
            frame: BaseFrame | OverlayFrame = OverlayFrame(rendered.pixels, revision=rendered.revision, changed=source_changed, dirty_ranges=None if force_changed else rendered.dirty_ranges)
        else:
            if isinstance(rendered, OverlayFrame):
                descriptor_getter = getattr(animation, 'component_descriptor', None)
                descriptor = descriptor_getter() if callable(descriptor_getter) else None
                if descriptor is None or getattr(descriptor.role, 'value', descriptor.role) != 'animation' or getattr(descriptor.alpha_behavior, 'value', descriptor.alpha_behavior) != 'premultiplied_rgba':
                    raise TypeError(f"background {component['name']} returned OverlayFrame")
                pixels = component.get('opaque_background_pixels')
                if pixels is None:
                    pixels = np.empty((self.controller.total_leds, 3), dtype=np.uint8)
                    component['opaque_background_pixels'] = pixels
                if rendered.changed or force_changed:
                    np.copyto(pixels, rendered.pixels[:, :3])
                frame = BaseFrame(pixels, changed=rendered.changed or force_changed, dirty_ranges=None if force_changed else rendered.dirty_ranges)
            elif isinstance(rendered, BaseFrame):
                frame = rendered
            else:
                changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
                dirty_ranges = rendered.dirty_ranges if isinstance(rendered, RenderedFrame) else None
                pixels = self._normalize_frame(rendered)
                pixels = np.asarray(pixels, dtype=np.uint8)
                if pixels.shape != (self.controller.total_leds, 3):
                    raise ValueError(f"background {component['name']} returned shape {pixels.shape}")
                if not pixels.flags.c_contiguous:
                    pixels = np.ascontiguousarray(pixels)
                frame = BaseFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)
            if frame.pixels.shape[0] != self.controller.total_leds:
                raise ValueError(f"background {component['name']} returned {frame.pixels.shape[0]} pixels; expected {self.controller.total_leds}")
            source_changed = frame.changed or force_changed
            if force_changed:
                frame = BaseFrame(frame.pixels, changed=True, dirty_ranges=None)
        graded, graded_changed = self._apply_vibe_presentation(animation, frame.pixels, profile=resolved_vibe.profile, changed=source_changed, state=component['grade_state'], include_grade=True, include_luminance=False)
        dirty_ranges = frame.dirty_ranges
        if graded_changed and (not source_changed):
            dirty_ranges = None
        if overlay:
            result: BaseFrame | OverlayFrame = OverlayFrame(graded, revision=frame.revision, changed=graded_changed, dirty_ranges=dirty_ranges)
        else:
            result = BaseFrame(graded, changed=graded_changed, dirty_ranges=dirty_ranges)
        component['cached_frame'] = result
        component['changed_calls'] += int(result.changed)
        component['last_dirty_ranges'] = result.dirty_ranges
        return result

    @staticmethod
    def _cached_overlay_frame(frame: OverlayFrame) -> OverlayFrame:
        return OverlayFrame(frame.pixels, revision=frame.revision, changed=False, dirty_ranges=())

    def _compose_scene_components(self, background: Dict[str, Any], overlay: Optional[Dict[str, Any]], compositor: HostSceneCompositor, final_presentation_state: Dict[str, Any], *, now: float, resolved_vibe: ResolvedVibe, operator_tempo: float, force_refresh: bool=False, plant_modifiers: Optional[Dict[str, Any]]=None) -> tuple[np.ndarray, bool, Optional[tuple[tuple[int, int], ...]]]:
        base = self._render_scene_component(background, now=now, resolved_vibe=resolved_vibe, operator_tempo=operator_tempo, overlay=False, plant_modifiers=plant_modifiers)
        placed = ()
        if overlay is not None:
            if overlay['enabled']:
                overlay_frame = self._render_scene_component(overlay, now=now, resolved_vibe=resolved_vibe, operator_tempo=operator_tempo, overlay=True, plant_modifiers=plant_modifiers)
            else:
                cached = overlay.get('cached_frame')
                if not isinstance(cached, OverlayFrame):
                    raise RuntimeError('overlay was disabled before its initial frame')
                overlay_frame = self._cached_overlay_frame(cached)
            placed = (PlacedOverlay(overlay_frame, opacity=overlay['opacity'], strip_offset=overlay['strip_offset'], led_offset=overlay['led_offset'], enabled=overlay['enabled']),)
        composed = compositor.compose(base, placed)
        changed = composed.changed
        dirty_ranges = composed.dirty_ranges
        pixels = composed.pixels
        animation = background['animation']
        framework_refresh = animation.framework_plant_modifier_refresh_pending()
        pixels = animation.apply_framework_plant_modifiers(pixels, changed=changed)
        if animation.framework_plant_modifiers_active():
            changed = changed or framework_refresh
            dirty_ranges = None
        luminance_component = next((component['animation'] for component in (background, overlay) if component is not None and 'luminance' in component['animation'].VIBE_CAPABILITIES), None)
        if luminance_component is not None:
            source_changed = changed
            pixels, changed = self._apply_vibe_presentation(luminance_component, pixels, profile=resolved_vibe.profile, changed=changed, state=final_presentation_state, force_refresh=force_refresh, include_grade=False, include_luminance=True)
            if changed and (not source_changed):
                dirty_ranges = None
        return (np.asarray(pixels, dtype=np.uint8), changed, dirty_ranges)

    def _render_composed_scene_frame(self, *, now: Optional[float]=None) -> tuple[np.ndarray, bool, Optional[tuple[tuple[int, int], ...]]]:
        with self._scene_lock:
            if not self._scene_mode or not self._scene_background or (not self._scene_compositor):
                raise RuntimeError('no composed scene is active')
            with self._presentation_state_guard():
                resolved = self._resolved_vibe
                operator_tempo = self.animation_speed_scale
                presentation_revision = self._presentation_revision
                force_refresh = bool(self._presentation_refresh_pending)
            result = self._compose_scene_components(self._scene_background, self._scene_overlay, self._scene_compositor, self._scene_final_presentation_state, now=time.perf_counter() if now is None else now, resolved_vibe=resolved, operator_tempo=operator_tempo, force_refresh=force_refresh)
            with self._presentation_state_guard():
                if self._presentation_revision == presentation_revision:
                    self._presentation_refresh_pending = False
            return result

    def render_composed_scene_frame(self, *, now: Optional[float]=None) -> BaseFrame:
        """Synchronously render one active scene frame for diagnostics/tests."""
        pixels, changed, dirty_ranges = self._render_composed_scene_frame(now=now)
        return BaseFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)

    def render_scene_v2_presentation(self, presentation_runtime: Any, canonical: Any, *, monotonic_elapsed: float, wall_time: Any, require_opaque_full: bool=False) -> BaseFrame:
        """Adapt one current Scene v2 presentation into the live host loop.

        The installed runtime owns canonical composition and final optics.  The
        manager deliberately only consumes its returned pixels here, so it
        cannot add a second grade, brightness, plant, or timing pass.
        """
        from animation.core.scene_runtime import ScenePresentationContext
        render = getattr(presentation_runtime, 'render_opaque_full' if require_opaque_full else 'render', None)
        if not callable(render):
            raise TypeError('presentation_runtime must render Scene v2 contexts')
        set_installation_profile = getattr(presentation_runtime, 'set_installation_profile', None)
        if callable(set_installation_profile):
            set_installation_profile(self.get_installation_profile_runtime_view())
        frame = render(ScenePresentationContext(canonical, monotonic_elapsed, wall_time))
        pixels = getattr(frame, 'pixels', None)
        if not isinstance(pixels, np.ndarray) or pixels.shape != (self.controller.total_leds, 3) or pixels.dtype != np.uint8:
            raise ValueError('Scene v2 presentation returned invalid host RGB pixels')
        self.current_frame_data = pixels
        return BaseFrame(pixels, changed=bool(getattr(frame, 'changed', True)), dirty_ranges=getattr(frame, 'dirty_ranges', None))

    def _render_compatibility_frame(self, time_elapsed: float) -> tuple[Any, bool, Optional[tuple[tuple[int, int], ...]]]:
        """Render the unchanged single-animation/background-only pipeline."""
        animation = self.current_animation
        if animation is None:
            raise RuntimeError('no animation is active')
        if isinstance(animation, AnimationBase):
            if not hasattr(self, '_resolved_vibe'):
                self._resolved_vibe = resolve_vibe('neutral')
                self._vibe_diagnostic = None
                self.animation_speed_scale = getattr(self, 'animation_speed_scale', 1.0)
                self.plant_modifier_state = getattr(self, 'plant_modifier_state', PlantModifierState.empty())
                self._scaled_elapsed = 0.0
                self._last_unscaled_elapsed = 0.0
                self._scene_epoch = 0
                self._presentation_revision = 0
            with self._presentation_state_guard():
                resolved_vibe = self._resolved_vibe
                operator_tempo = self.animation_speed_scale
                presentation_revision = self._presentation_revision
                force_refresh = bool(getattr(self, '_presentation_refresh_pending', False))
            context = self._advance_runtime_context(animation, time_elapsed, self.frame_count, resolved_vibe=resolved_vibe, operator_tempo_scale=operator_tempo)
            rendered = animation.generate_frame_with_context(context)
        else:
            rendered = animation.generate_frame(time_elapsed, self.frame_count)
            resolved_vibe = resolve_vibe('neutral')
            presentation_revision = 0
            force_refresh = False
        changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
        dirty_ranges = rendered.dirty_ranges if isinstance(rendered, RenderedFrame) else None
        frame = self._normalize_frame(rendered)
        refresh_pending = getattr(animation, 'framework_plant_modifier_refresh_pending', None)
        apply_framework = getattr(animation, 'apply_framework_plant_modifiers', None)
        framework_active = getattr(animation, 'framework_plant_modifiers_active', None)
        framework_refresh = bool(refresh_pending() if callable(refresh_pending) else False)
        if callable(apply_framework):
            frame = apply_framework(frame, changed=changed)
        if callable(framework_active) and framework_active():
            changed = changed or framework_refresh
            dirty_ranges = None
        if isinstance(animation, AnimationBase):
            source_changed = changed
            if not hasattr(self, '_live_presentation_state'):
                self._live_presentation_state = self._empty_presentation_state()
            frame, changed = self._apply_vibe_presentation(animation, frame, profile=resolved_vibe.profile, changed=changed, state=self._live_presentation_state, force_refresh=force_refresh)
            with self._presentation_state_guard():
                if self._presentation_revision == presentation_revision:
                    self._presentation_refresh_pending = False
            if changed and (not source_changed):
                dirty_ranges = None
        return (frame, changed, dirty_ranges)

    def _animation_loop(self, run_generation=None):
        if run_generation is None:
            run_generation = getattr(self, "_run_generation", 0)
        deadline = None
        scheduled_fps = None
        pending = None
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='led-present') as presenter:
            while self._run_is_active(run_generation):
                started = time.perf_counter()
                try:
                    if getattr(self, '_canonical_host_full_mode', False):
                        elapsed = max(0.0, started-self.start_time)
                        self._scaled_elapsed += max(0.0, elapsed-self._last_unscaled_elapsed) * self.animation_speed_scale
                        self._last_unscaled_elapsed = elapsed
                        presented = self.render_scene_v2_presentation(self._canonical_receiver_runtime,
                            self._canonical_receiver_scene, monotonic_elapsed=self._scaled_elapsed,
                            wall_time=datetime.now().astimezone())
                        frame,changed = presented.pixels,presented.changed
                    elif getattr(self, '_scene_mode', False):
                        frame,changed,_ = self._render_composed_scene_frame(now=started)
                    elif self.current_animation is not None:
                        frame,changed,_ = self._render_compatibility_frame(started-self.start_time)
                    else:
                        break
                    generated = time.perf_counter()-started
                    if not self._run_owns_generation(run_generation):
                        break
                    with self.frame_data_lock:
                        self.current_frame_data = frame
                    sent = shown = 0.0
                    if pending is not None:
                        sent,shown = pending.result()
                        pending = None
                        if not self._run_owns_generation(run_generation):
                            break
                    if changed or self.frames_presented == 0:
                        pending = presenter.submit(self._present_frame,frame.copy(),None,False,getattr(self.controller,'inline_show',False))
                        self.frames_presented += 1
                    else:
                        self.unchanged_frames_skipped += 1
                    self.frame_count += 1
                    self._update_fps_tracking(started)
                except Exception as exc:
                    self._fail_canonical_host_full(exc)
                    break
                work_done = time.perf_counter()
                deadline,remaining = _plan_frame_deadline(deadline,scheduled_fps,frame_started=started,work_finished=work_done,target_fps=self.target_fps)
                scheduled_fps = self.target_fps
                if remaining:
                    _wait_for_frame_deadline(deadline)
                duration = time.perf_counter()-started
                self._record_perf_sample({'generate':generated,'send':sent,'show':shown,
                    'process':work_done-started,'target_period':1/self.target_fps,
                    'deadline_missed':work_done-started>1/self.target_fps,'sleep':duration-(work_done-started),
                    'requested_sleep':remaining,'deadline_lateness':max(0,started+duration-deadline),'frame':duration})
            if pending is not None:
                try:
                    pending.result()
                except Exception as exc:
                    self._fail_canonical_host_full(exc)

    def _fail_canonical_host_full(self, error):
        self._playback_error = str(error)
        with self._run_state_guard():
            self.is_running = False
            self.stop_event.set()
            self._run_generation = getattr(self, "_run_generation", 0) + 1

    def _presentation_io_guard(self):
        """Serialize controller I/O across timed-out stop/start boundaries."""
        lock = getattr(self, '_presentation_io_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._presentation_io_lock = lock
        return lock

    def _present_frame(self, frame, dirty_ranges, use_partial, inline_show):
        with self._presentation_io_guard():
            started = time.perf_counter()
            sender = getattr(self.controller, 'stream_host_full_pixels', self.controller.set_all_pixels)
            if sender(frame) is False:
                raise RuntimeError('one or more receiver RGB transfers failed')
            sent = time.perf_counter() - started
            show_time = 0.0
            if not inline_show:
                started = time.perf_counter()
                self.controller.show()
                show_time = time.perf_counter() - started
            return sent, show_time

    def set_target_fps(self, target_fps: int) -> int:
        """Apply a live, bounded host/physical presentation-rate target."""
        self.target_fps = max(1, min(200, int(target_fps)))
        return self.target_fps

    def _normalize_frame(self, colors):
        """Ensure frame length matches the LED count.

        Accepts either a list of tuples or a numpy uint8 array of shape (N, 3).
        Returns the same type, padded/trimmed to total_pixels.
        """
        total_pixels = self.controller.total_leds
        if isinstance(colors, RenderedFrame):
            colors = colors.pixels
        if colors is None:
            return [(0, 0, 0)] * total_pixels
        if isinstance(colors, np.ndarray):
            if colors.ndim != 2 or colors.shape[1] != 3:
                raise ValueError(f'frame ndarray must have shape (N, 3), got {colors.shape}')
            if colors.shape[0] < total_pixels:
                pad = np.zeros((total_pixels - colors.shape[0], 3), dtype=np.uint8)
                colors = np.concatenate([colors, pad])
            elif colors.shape[0] > total_pixels:
                colors = colors[:total_pixels]
            if colors.dtype != np.uint8:
                colors = np.clip(colors, 0, 255).astype(np.uint8)
            if not colors.flags.c_contiguous:
                colors = np.ascontiguousarray(colors)
            return colors
        frame = list(colors)
        if len(frame) < total_pixels:
            frame.extend([(0, 0, 0)] * (total_pixels - len(frame)))
        elif len(frame) > total_pixels:
            frame = frame[:total_pixels]
        return frame

    def _update_fps_tracking(self, timestamp: Optional[float]=None):
        """Record frame timestamps for FPS calculation"""
        now = timestamp if timestamp is not None else time.perf_counter()
        self.frame_timestamps.append(now)
        while self.frame_timestamps and now - self.frame_timestamps[0] > 5.0:
            self.frame_timestamps.popleft()

    def _calculate_fps(self) -> float:
        """Calculate current FPS"""
        if len(self.frame_timestamps) < 2:
            return 0.0
        duration = self.frame_timestamps[-1] - self.frame_timestamps[0]
        if duration <= 0:
            return 0.0
        return (len(self.frame_timestamps) - 1) / duration

    def _compute_driver_fps(self, driver_stats: Dict[str, Any]) -> float:
        """Estimate hardware-applied FPS from driver frame counters."""
        if not driver_stats or not isinstance(driver_stats, dict):
            return self._driver_fps
        aggregate = driver_stats.get('aggregate')
        if isinstance(aggregate, dict) and aggregate.get('logical_frames_sent') is not None:
            try:
                frames_sent_int = int(aggregate['logical_frames_sent'])
            except (TypeError, ValueError):
                return self._driver_fps
            now = time.perf_counter()
            last_frames = self._driver_fps_last_frames
            last_time = self._driver_fps_last_time
            self._driver_fps_last_frames = frames_sent_int
            self._driver_fps_last_time = now
            if last_frames is not None and last_time is not None and (now > last_time):
                delta_frames = frames_sent_int - last_frames
                if delta_frames >= 0:
                    self._driver_fps = delta_frames / (now - last_time)
            return self._driver_fps
        devices = driver_stats.get('devices')
        now = time.perf_counter()
        if isinstance(devices, list) and devices:
            fps_samples = []
            for idx, device in enumerate(devices):
                frames_sent = device.get('frames_sent')
                try:
                    frames_sent_int = int(frames_sent)
                except (TypeError, ValueError):
                    continue
                last_frames = self._driver_device_last_frames.get(idx)
                last_time = self._driver_device_last_time.get(idx)
                self._driver_device_last_frames[idx] = frames_sent_int
                self._driver_device_last_time[idx] = now
                if last_frames is None or last_time is None:
                    continue
                delta_frames = frames_sent_int - last_frames
                delta_time = now - last_time
                if delta_frames < 0 or delta_time <= 0:
                    continue
                fps_samples.append(delta_frames / delta_time)
            if fps_samples:
                self._driver_fps = min(fps_samples)
            return self._driver_fps
        frames_sent = None
        if 'aggregate' in driver_stats and isinstance(driver_stats.get('aggregate'), dict):
            frames_sent = driver_stats['aggregate'].get('frames_sent')
        else:
            frames_sent = driver_stats.get('frames_sent')
        if frames_sent is None:
            return self._driver_fps
        try:
            frames_sent_int = int(frames_sent)
        except (TypeError, ValueError):
            return self._driver_fps
        last_frames = self._driver_fps_last_frames
        last_time = self._driver_fps_last_time
        self._driver_fps_last_frames = frames_sent_int
        self._driver_fps_last_time = now
        if last_frames is None or last_time is None:
            return self._driver_fps
        delta_frames = frames_sent_int - last_frames
        delta_time = now - last_time
        if delta_frames < 0 or delta_time <= 0:
            return self._driver_fps
        self._driver_fps = delta_frames / delta_time
        return self._driver_fps

    def _record_perf_sample(self, sample: Dict[str, float]):
        """Store per-frame timing samples for debugging"""
        with self.perf_lock:
            self.perf_samples.append(sample)
            self._last_perf_sample = sample

    def _get_perf_summary(self) -> Dict[str, Any]:
        """Summarize recent performance metrics"""
        with self.perf_lock:
            if not self.perf_samples:
                return {}
            count = len(self.perf_samples)
            totals = {key: 0.0 for key in ('generate', 'send', 'show', 'process', 'sleep', 'requested_sleep', 'deadline_lateness', 'frame')}
            for sample in self.perf_samples:
                for key in totals.keys():
                    totals[key] += sample.get(key, 0.0)
            target_frame_ms = 1000.0 / max(1, float(self.target_fps or 1))
            summary = {'samples': count, 'target_frame_ms': target_frame_ms, 'controller_inline_show': bool(getattr(self.controller, 'inline_show', False))}
            for key, total in totals.items():
                summary[f'avg_{key}_ms'] = total / count * 1000.0
                ordered = sorted((sample.get(key, 0.0) for sample in self.perf_samples))
                for label, ratio in (('p50', 0.5), ('p95', 0.95), ('p99', 0.99)):
                    index = min(count - 1, max(0, int(round((count - 1) * ratio))))
                    summary[f'{label}_{key}_ms'] = ordered[index] * 1000.0
                summary[f'max_{key}_ms'] = ordered[-1] * 1000.0
            deadline_misses = sum((bool(sample.get('deadline_missed', False)) for sample in self.perf_samples))
            summary['deadline_misses'] = deadline_misses
            summary['deadline_miss_ratio'] = deadline_misses / count
            summary['frames_presented'] = self.frames_presented
            summary['unchanged_frames_skipped'] = self.unchanged_frames_skipped
            if self._last_perf_sample:
                for key in totals.keys():
                    summary[f'last_{key}_ms'] = self._last_perf_sample.get(key, 0.0) * 1000.0
            return summary

    def reload_animation(self, name: str) -> bool:
        """Reload specific animation plugin"""
        try:
            return self.plugin_loader.reload_plugin(name) is not None
        except Exception as e:
            print(f'✗ Failed to reload animation {name}: {e}')
            return False

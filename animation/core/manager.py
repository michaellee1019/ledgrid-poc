#!/usr/bin/env python3
"""
Animation Manager Service

Coordinates between LED controller, animation plugins, and web interface.
Handles animation switching, parameter updates, and frame generation.
"""

import hashlib
import json
import math
import time
import threading
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List

import numpy as np

from animation.core import AnimationBase, RenderedFrame, StatefulAnimationBase, AnimationPluginLoader
from animation.core.compositing import (
    HostForegroundCompositor,
    HostSceneCompositor,
    PlacedOverlay,
)
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.plant_awareness import PlantModifierState
from animation.core.receiver_presentation import (
    ReceiverPresentationContext,
    quantize_q8_8,
)
from animation.core.receiver_sparse_publisher import ReceiverSparsePublisher
from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_COMPONENT_ID,
    COMPILED_RAINBOW_PLUGIN_ID,
    Q8_8_ONE,
    receiver_static_component_catalog,
    receiver_static_component_descriptor,
    render_compiled_rainbow_preview,
    validate_compiled_rainbow_parameters,
)
from animation.core.presentation_contracts import (
    AGGREGATE_OVERLAY_SLOT_ID,
    AnimationRuntimeContext,
    BaseFrame,
    ClipPolicy,
    ComponentProvider,
    ComponentRef,
    ForegroundStalePolicy,
    OverlayFrame,
    OverlayPlacement,
    OverlayRef,
    ResolvedVibe,
    SceneState,
    StalePolicy,
    VibeState,
    component_preset_fingerprint,
    list_vibe_profiles,
    resolve_vibe,
)
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP
from drivers.frame_codec import encode_frame_data, FRAME_ENCODING_NAME
from ipc.scene_contract import SceneProviderPolicy

# Try to import the real LED controller, fall back to mock for testing
try:
    from drivers.multi_device import MultiDeviceLEDController as LEDController
except ImportError:
    try:
        from drivers.spi_controller import LEDController
    except ImportError:
        # Mock LED controller for testing without SPI hardware
        class LEDController:
            def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP, **kwargs):
                self.strip_count = strips
                self.leds_per_strip = leds_per_strip
                self.total_leds = strips * leds_per_strip
                self.debug = kwargs.get('debug', False)
                print(f"🔧 Mock LED Controller: {strips} strips × {leds_per_strip} LEDs = {self.total_leds} total")

            def set_all_pixels(self, pixel_data):
                """Mock set all pixels"""
                if self.debug and len(pixel_data) > 0:
                    r, g, b = pixel_data[0]
                    print(f"📊 Frame: First pixel = RGB({r}, {g}, {b})")

            def show(self):
                """Mock show"""
                pass

            def clear(self):
                """Mock clear"""
                if self.debug:
                    print("🧹 Cleared LEDs")

            def configure(self):
                """Mock configure"""
                pass


class PreviewLEDController:
    """
    Lightweight controller used for preview generation.
    Mirrors the dimensions of the real controller but performs no I/O so preview
    requests can never block or interfere with the SPI device.
    """
    def __init__(self, strips: int, leds_per_strip: int, debug: bool = False):
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


class AnimationManager:
    """Manages animation playback and plugin system"""

    # The checked-in package manifests are the single source of truth. Keeping
    # this public set preserves callers that inspect the allowlist without
    # duplicating a second registry in manager.py.
    ALLOWED_PLUGINS = set(AnimationPluginLoader.shipped_plugin_ids())
    
    DEFAULT_ANIMATION = "sparkle"

    def __init__(self, controller: LEDController, plugins_dir: Optional[str] = None,
                 animation_speed_scale: float = DEFAULT_ANIMATION_SPEED_SCALE,
                 plant_aware: bool = DEFAULT_PLANT_AWARE,
                 plant_modifiers: Optional[Dict[str, Any]] = None,
                 vibe: Optional[Any] = None,
                 default_animation: Optional[str] = None,
                 default_animation_config: Optional[Dict[str, Any]] = None,
                 default_animation_preset: Optional[Dict[str, Any]] = None,
                 feature_flags: Optional[Any] = None,
                 auto_start: bool = True):
        """
        Initialize animation manager
        
        Args:
            controller: LED controller instance
            plugins_dir: Directory containing animation plugins
            animation_speed_scale: Operator tempo multiplier applied at render time
            plant_aware: Global plant-aware state applied to every animation
            default_animation: Animation to auto-start on init (None = use DEFAULT_ANIMATION)
            default_animation_config: Parameters to apply to the default animation
            auto_start: Whether to start the default animation during construction
        """
        self.controller = controller
        self.plugin_loader = AnimationPluginLoader(
            plugins_dir, allowed_plugins=self.ALLOWED_PLUGINS
        )
        self._default_animation = default_animation or self.DEFAULT_ANIMATION
        self._default_animation_config = default_animation_config or {}
        self._default_animation_preset = default_animation_preset
        self.feature_flags = (
            feature_flags
            if isinstance(feature_flags, AnimationPipelineFeatureFlags)
            else AnimationPipelineFeatureFlags.from_mapping(feature_flags)
        )
        
        # Animation state
        self.current_animation: Optional[AnimationBase] = None
        self.current_animation_name: Optional[str] = None
        self.current_animation_hash: Optional[str] = None
        self.current_preset: Optional[Dict[str, Any]] = None
        self.output_brightness: Optional[int] = getattr(
            controller, 'current_brightness', None
        )
        self._last_active_state: Optional[Dict[str, Any]] = None
        self.is_running = False
        # 200 Hz stays below the physical ceiling of a 138-pixel
        # WS2812 strip while leaving headroom for frame generation and transfer.
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
        self._scene_epoch = time.time_ns() & ((1 << 64) - 1)
        self._presentation_refresh_pending = True
        self._live_presentation_state = self._empty_presentation_state()
        # Phase 2B deliberately keeps scene state process-local. Product API,
        # persistence, and arbitrary layer graphs remain Phase 2C work.
        self._scene_lock = threading.RLock()
        self._scene_mode = False
        self._scene_background: Optional[Dict[str, Any]] = None
        self._scene_overlay: Optional[Dict[str, Any]] = None
        self._scene_compositor: Optional[HostSceneCompositor] = None
        self._active_scene_state: Optional[SceneState] = None
        self._scene_compatibility_mode = False
        self._scene_allows_compatibility_components = False
        self._scene_final_presentation_state = self._empty_presentation_state()
        self._receiver_hybrid_mode = False
        self._receiver_sparse_publisher: Optional[ReceiverSparsePublisher] = None
        self._receiver_foreground_compositor: Optional[HostForegroundCompositor] = None
        self._receiver_context: Optional[ReceiverPresentationContext] = None
        self._receiver_context_revision = 0
        self._receiver_plant_revision = 0
        self._receiver_foreground_presentation_state = self._empty_presentation_state()
        self._receiver_last_status: Optional[Dict[str, Any]] = None
        self._receiver_hybrid_error: Optional[str] = None
        self._receiver_fallback_active = False
        self.plant_modifier_state = (
            PlantModifierState.from_payload(plant_modifiers)
            if plant_modifiers is not None
            else PlantModifierState.from_legacy(bool(plant_aware))
        )
        self._legacy_plant_aware_bridge = plant_modifiers is None
        self.plant_aware = bool(self.plant_modifier_state.active)
        
        # Threading
        self.animation_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        
        # Performance tracking
        self.frame_timestamps = deque(maxlen=1000)  # 5 seconds at up to 200 FPS
        self.perf_samples = deque(maxlen=300)
        self.perf_lock = threading.Lock()
        self._last_perf_sample: Dict[str, float] = {}
        self._driver_fps = 0.0
        self._driver_fps_last_frames: Optional[int] = None
        self._driver_fps_last_time: Optional[float] = None
        self._driver_device_last_frames: Dict[int, int] = {}
        self._driver_device_last_time: Dict[int, float] = {}

        # Current frame data for web interface
        self.current_frame_data = []
        self.frame_data_lock = threading.Lock()
        self.painter_lock = threading.Lock()
        self.painter_active = False
        self.painter_frame_data = [(0, 0, 0)] * self.controller.total_leds
        self.painter_updated_at = 0.0

        # Preview controller avoids hitting the real SPI device during previews
        self.preview_controller = PreviewLEDController(
            self.controller.strip_count,
            self.controller.leds_per_strip,
            getattr(self.controller, 'debug', False)
        )
        self._preview_lock = threading.RLock()
        self._preview_session: Optional[Dict[str, Any]] = None
        self._preview_session_ttl = 300.0

        # Load all plugins on startup and auto-start the default animation
        self.refresh_plugins()
        if auto_start and self._default_animation:
            if self.start_animation(
                self._default_animation,
                self._default_animation_config,
                preset=self._default_animation_preset,
            ):
                print(f"▶️  Auto-started default animation: {self._default_animation}")
            else:
                print(f"⚠️  Could not auto-start default animation: {self._default_animation}")
    
    def refresh_plugins(self) -> Dict[str, Any]:
        """Reload all animation plugins"""
        try:
            plugins = self.plugin_loader.load_all_plugins()
            print(f"✓ Loaded {len(plugins)} animation plugins")
            return {
                name: self.plugin_loader.get_plugin_info(name)
                for name in plugins
                if self._plugin_role(name) != 'overlay'
            }
        except Exception as e:
            print(f"✗ Error loading plugins: {e}")
            traceback.print_exc()
            return {}

    def set_animation_speed_scale(self, speed_scale: float) -> float:
        """Update operator tempo without mutating authored animation state."""
        requested = self._validate_tempo_scale(speed_scale)
        changed = False
        with self._presentation_state_guard():
            if requested != self.animation_speed_scale:
                self.animation_speed_scale = requested
                self._presentation_revision = (
                    getattr(self, "_presentation_revision", 0) + 1
                )
                self._presentation_refresh_pending = True
                changed = True
        if changed:
            self._refresh_active_presentation_context()
        return self.animation_speed_scale

    def _presentation_state_guard(self) -> threading.RLock:
        """Return the manager presentation-state lock, including old test doubles."""
        lock = getattr(self, "_presentation_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._presentation_state_lock = lock
        return lock

    @staticmethod
    def _validate_tempo_scale(value: Any) -> float:
        requested = float(value)
        if not math.isfinite(requested) or requested <= 0:
            raise ValueError("animation speed scale must be a positive finite number")
        return requested

    @staticmethod
    def _wall_time() -> float:
        """Return presentation wall time; benchmarks can replace this clock."""
        return time.time()

    @staticmethod
    def _canonical_vibe(payload: Any, *, revision: Optional[int] = None) -> ResolvedVibe:
        if payload is None:
            return resolve_vibe("neutral", revision=0 if revision is None else revision)
        if isinstance(payload, str):
            return resolve_vibe(payload, revision=0 if revision is None else revision)
        if not isinstance(payload, dict):
            raise TypeError("vibe must be a stable ID or versioned state")
        state = VibeState.from_payload(payload.get("state", payload))
        resolved = resolve_vibe(
            state.vibe_id,
            revision=state.revision if revision is None else revision,
            profile_version=state.profile_version,
        )
        if state.resolved_profile_digest != resolved.state.resolved_profile_digest:
            raise ValueError("persisted vibe profile digest does not match the registry")
        return resolved

    @classmethod
    def _resolve_initial_vibe(cls, payload: Any) -> tuple[ResolvedVibe, Optional[Dict[str, str]]]:
        if payload is None:
            return resolve_vibe("neutral"), None
        try:
            return cls._canonical_vibe(payload), None
        except (KeyError, TypeError, ValueError) as exc:
            revision = 0
            raw = payload.get("state", payload) if isinstance(payload, dict) else {}
            raw_revision = raw.get("revision") if isinstance(raw, dict) else None
            if (
                isinstance(raw_revision, int)
                and not isinstance(raw_revision, bool)
                and 0 <= raw_revision <= 2**64 - 1
            ):
                revision = raw_revision
            return resolve_vibe("neutral", revision=revision), {
                "code": "vibe_profile_fallback",
                "message": f"Saved vibe was incompatible; using neutral: {exc}",
            }

    def get_vibe_state(self) -> Dict[str, Any]:
        with self._presentation_state_guard():
            return self._resolved_vibe.state.to_dict()

    def get_vibe_status(self) -> Dict[str, Any]:
        with self._presentation_state_guard():
            status: Dict[str, Any] = {
                "state": self._resolved_vibe.state.to_dict(),
                "profile": self._resolved_vibe.profile.to_dict(),
            }
            if self._vibe_diagnostic:
                status["diagnostic"] = dict(self._vibe_diagnostic)
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
            same_profile = (
                requested.state.vibe_id == current.state.vibe_id
                and requested.state.profile_version == current.state.profile_version
                and requested.state.resolved_profile_digest
                == current.state.resolved_profile_digest
            )
            if not same_profile:
                requested = resolve_vibe(
                    requested.state.vibe_id,
                    revision=current.state.revision + 1,
                    profile_version=requested.state.profile_version,
                )
                self._resolved_vibe = requested
                self._presentation_revision += 1
                self._presentation_refresh_pending = True
                presentation_changed = True
            elif diagnostic and requested.state.revision != current.state.revision:
                # Preserve the saved revision when an incompatible persisted
                # profile resolves to the already-active neutral fallback.
                # Valid live updates remain manager-owned and idempotent.
                self._resolved_vibe = requested
            self._vibe_diagnostic = diagnostic
        if presentation_changed:
            self._refresh_active_presentation_context()
            self._refresh_receiver_hybrid_context("vibe")
        return self.get_vibe_status()

    @staticmethod
    def _animation_authored_speed(animation: AnimationBase) -> float:
        try:
            value = float(animation.get_authored_parameter("speed", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return value if math.isfinite(value) and value > 0 else 1.0

    @staticmethod
    def _component_tempo(profile, animation: AnimationBase) -> float:
        return profile.tempo_scale if "tempo" in animation.VIBE_CAPABILITIES else 1.0

    def _runtime_context(
        self,
        animation: AnimationBase,
        *,
        unscaled_elapsed: float,
        scaled_elapsed: float,
        frame_index: int,
        resolved_vibe: Optional[ResolvedVibe] = None,
        operator_tempo_scale: Optional[float] = None,
        plant_modifiers: Optional[Dict[str, Any]] = None,
    ) -> AnimationRuntimeContext:
        if resolved_vibe is None or operator_tempo_scale is None:
            with self._presentation_state_guard():
                resolved = resolved_vibe or self._resolved_vibe
                operator_tempo = (
                    self.animation_speed_scale
                    if operator_tempo_scale is None else operator_tempo_scale
                )
        else:
            resolved = resolved_vibe
            operator_tempo = operator_tempo_scale
        authored_speed = self._animation_authored_speed(animation)
        vibe_tempo = self._component_tempo(resolved.profile, animation)
        return AnimationRuntimeContext(
            wall_time=self._wall_time(),
            unscaled_elapsed=max(0.0, float(unscaled_elapsed)),
            scaled_elapsed=max(0.0, float(scaled_elapsed)),
            frame_index=max(0, int(frame_index)),
            scene_epoch=self._scene_epoch,
            global_width=int(self.controller.strip_count),
            height=int(self.controller.leds_per_strip),
            local_strip_offset=0,
            local_width=int(self.controller.strip_count),
            vibe_id=resolved.state.vibe_id,
            vibe_profile_version=resolved.state.profile_version,
            resolved_profile_digest=resolved.state.resolved_profile_digest,
            palette_roles=resolved.profile.palette_roles,
            capability_values=resolved.profile.capability_values,
            tempo_scale=vibe_tempo,
            luminance_scale=(
                resolved.profile.luminance_scale
                if "luminance" in animation.VIBE_CAPABILITIES else 1.0
            ),
            operator_tempo_scale=operator_tempo,
            authored_speed=authored_speed,
            effective_time_scale=(
                authored_speed * vibe_tempo * operator_tempo
            ),
            installation_profile_view={},
            plant_modifiers=(
                self.plant_modifier_state.to_dict()
                if plant_modifiers is None else plant_modifiers
            ),
        )

    def _advance_runtime_context(
        self,
        animation: AnimationBase,
        unscaled_elapsed: float,
        frame_index: int,
        *,
        resolved_vibe: Optional[ResolvedVibe] = None,
        operator_tempo_scale: Optional[float] = None,
    ) -> AnimationRuntimeContext:
        if resolved_vibe is None or operator_tempo_scale is None:
            with self._presentation_state_guard():
                resolved = resolved_vibe or self._resolved_vibe
                operator_tempo = (
                    self.animation_speed_scale
                    if operator_tempo_scale is None else operator_tempo_scale
                )
        else:
            resolved = resolved_vibe
            operator_tempo = operator_tempo_scale
        unscaled = max(0.0, float(unscaled_elapsed))
        delta = max(0.0, unscaled - self._last_unscaled_elapsed)
        authored_speed = self._animation_authored_speed(animation)
        vibe_tempo = self._component_tempo(resolved.profile, animation)
        self._scaled_elapsed += (
            delta * authored_speed * vibe_tempo * operator_tempo
        )
        self._last_unscaled_elapsed = unscaled
        return self._runtime_context(
            animation,
            unscaled_elapsed=unscaled,
            scaled_elapsed=self._scaled_elapsed,
            frame_index=frame_index,
            resolved_vibe=resolved,
            operator_tempo_scale=operator_tempo,
        )

    def _refresh_active_presentation_context(self) -> None:
        with self._presentation_state_guard():
            resolved = getattr(self, '_resolved_vibe', resolve_vibe('neutral'))
            operator_tempo = self.animation_speed_scale
        if getattr(self, '_scene_mode', False):
            with self._scene_state_guard():
                components = tuple(
                    component for component in (
                        self._scene_background, self._scene_overlay
                    ) if component is not None and component.get('animation') is not None
                )
                for component in components:
                    animation = component['animation']
                    context = self._runtime_context(
                        animation,
                        unscaled_elapsed=component['last_unscaled_elapsed'],
                        scaled_elapsed=component['scaled_elapsed'],
                        frame_index=component['frame_index'],
                        resolved_vibe=resolved,
                        operator_tempo_scale=operator_tempo,
                    )
                    animation.set_presentation_context(context)
            return

        animation = self.current_animation
        if isinstance(animation, AnimationBase):
            context = self._runtime_context(
                animation,
                unscaled_elapsed=self._last_unscaled_elapsed,
                scaled_elapsed=self._scaled_elapsed,
                frame_index=self.frame_count,
                resolved_vibe=resolved,
                operator_tempo_scale=operator_tempo,
            )
            animation.set_presentation_context(context)

    @staticmethod
    def _empty_presentation_state() -> Dict[str, Any]:
        return {
            "buffers": [], "index": 0, "geometry": None,
            "cached": None, "identity": None,
        }

    @staticmethod
    def _apply_vibe_presentation(
        animation: AnimationBase,
        pixels: Any,
        *,
        profile,
        changed: bool,
        state: Dict[str, Any],
        force_refresh: bool = False,
        include_grade: bool = True,
        include_luminance: bool = True,
    ) -> tuple[Any, bool]:
        capabilities = animation.VIBE_CAPABILITIES
        policy = animation.VIBE_COLOR_POLICY
        grade = (
            include_grade
            and
            profile.vibe_id != "neutral"
            and policy == "grade"
            and "palette_roles" in capabilities
        )
        luminance = (
            profile.luminance_scale
            if include_luminance and "luminance" in capabilities
            else 1.0
        )
        identity = (
            profile.resolved_profile_digest, policy, tuple(sorted(capabilities)),
            bool(include_grade), bool(include_luminance),
        )
        refresh = force_refresh or state.get("identity") != identity
        state["identity"] = identity

        if not grade and luminance == 1.0:
            state["cached"] = None
            return pixels, changed or refresh
        if not changed and not refresh and state.get("cached") is not None:
            return state["cached"], False

        array = np.asarray(pixels, dtype=np.uint8)
        if array.ndim != 2 or array.shape[1] not in (3, 4):
            raise ValueError("vibe presentation requires an RGB or RGBA frame")
        count = array.shape[0]
        channels = array.shape[1]
        geometry = (count, channels)
        if state.get("geometry") != geometry:
            state["buffers"] = [
                np.empty(geometry, dtype=np.uint8) for _ in range(2)
            ]
            state["index"] = 0
            state["geometry"] = geometry
        output = state["buffers"][state["index"]]
        state["index"] = (state["index"] + 1) % len(state["buffers"])

        working = array[:, :3].astype(np.float32)
        if grade:
            chroma = float(profile.capability_values.get("chroma_scale", 1.0))
            luma = (
                working[:, 0] * 0.299
                + working[:, 1] * 0.587
                + working[:, 2] * 0.114
            )[:, None]
            working = luma + (working - luma) * chroma
            energy = float(profile.capability_values.get("energy", 0.5))
            tint_weight = min(0.12, max(0.0, abs(energy - 0.5) * 0.18))
            if tint_weight:
                tint = np.asarray(profile.palette_roles["primary"], dtype=np.float32)
                if channels == 4:
                    tint = tint[None, :] * (
                        array[:, 3:4].astype(np.float32) / 255.0
                    )
                working = working * (1.0 - tint_weight) + tint * tint_weight
        if luminance != 1.0:
            working *= luminance
        np.clip(working, 0.0, 255.0, out=working)
        if channels == 4:
            # Component grade operates in premultiplied space and must retain
            # the overlay contract. Final luminance leaves alpha unchanged.
            np.minimum(working, array[:, 3:4], out=working)
            np.copyto(output[:, 3], array[:, 3])
        np.copyto(output[:, :3], np.rint(working), casting="unsafe")
        state["cached"] = output
        return output, changed or refresh

    @staticmethod
    def validate_output_brightness(brightness: Any) -> int:
        """Return a valid hardware brightness level without numeric coercion."""
        if isinstance(brightness, bool) or not isinstance(brightness, int):
            raise ValueError("brightness must be an integer between 0 and 255")
        if brightness < 0 or brightness > 255:
            raise ValueError("brightness must be between 0 and 255")
        return brightness

    def set_output_brightness(self, brightness: Any) -> int:
        """Apply the installation-wide receiver brightness at runtime."""
        level = self.validate_output_brightness(brightness)
        setter = getattr(self.controller, 'set_brightness', None)
        if not callable(setter):
            raise RuntimeError("LED controller does not support global brightness")
        setter(level)
        self.output_brightness = level
        return level

    def _remember_active_state(
        self,
        animation_name: str,
        config: Dict[str, Any],
        preset: Optional[Dict[str, Any]],
    ) -> None:
        """Keep the last playable state so a device-level power-on can resume it."""
        self._last_active_state = {
            'animation': animation_name,
            'config': dict(config),
            'preset': dict(preset) if preset else None,
        }

    def _sync_last_active_preset(self) -> None:
        if (
            self._last_active_state is not None
            and self._last_active_state.get('animation') == self.current_animation_name
        ):
            self._last_active_state['preset'] = (
                dict(self.current_preset) if self.current_preset else None
            )

    def apply_device_state(self, state: Dict[str, Any]) -> bool:
        """Atomically validate and apply a device-level control request.

        The IPC transport carries this request as one command. Hardware changes
        are then applied together by the controller process, so a rapid HA
        power/effect/brightness update cannot lose one of its fields.
        """
        if not isinstance(state, dict) or not state:
            raise ValueError("device state must contain at least one field")
        supported = {'power', 'brightness', 'animation', 'config', 'preset'}
        unknown = sorted(set(state) - supported)
        if unknown:
            raise ValueError(f"unsupported device state fields: {', '.join(unknown)}")

        has_power = 'power' in state
        power = state.get('power')
        if has_power and not isinstance(power, bool):
            raise ValueError("power must be boolean")

        has_brightness = 'brightness' in state
        brightness = (
            self.validate_output_brightness(state.get('brightness'))
            if has_brightness else None
        )

        has_animation = 'animation' in state
        animation = state.get('animation')
        if has_animation:
            if not isinstance(animation, str) or not animation:
                raise ValueError("animation must be a non-empty string")
            if self.plugin_loader.get_plugin(animation) is None:
                raise ValueError(f"animation not found: {animation}")
            if self._plugin_role(animation) == 'overlay':
                raise ValueError(
                    f"overlay component {animation} requires a composed scene"
                )

        config = state.get('config', {})
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        if 'config' in state and not has_animation:
            raise ValueError("config requires an animation")

        preset = state.get('preset')
        if 'preset' in state:
            if not has_animation:
                raise ValueError("preset requires an animation")
            if self._normalize_current_preset(preset, animation) is None:
                raise ValueError("preset metadata is invalid")

        if power is False and has_animation:
            raise ValueError("power false cannot be combined with an animation")

        if has_brightness:
            self.set_output_brightness(brightness)

        if power is False:
            self.stop_animation()
            return True

        if has_animation:
            return self.start_animation(animation, config, preset=preset)

        if power is True and not self.is_running:
            restore = self._last_active_state or {
                'animation': self._default_animation,
                'config': self._default_animation_config,
                'preset': None,
            }
            return self.start_animation(
                restore['animation'],
                dict(restore.get('config') or {}),
                preset=restore.get('preset'),
            )

        return True

    def set_plant_aware(self, enabled: bool) -> bool:
        """Compatibility boundary translating the old global boolean."""
        state = PlantModifierState.from_legacy(enabled)
        prior_state = getattr(self, "plant_modifier_state", PlantModifierState.empty())
        changed = state.to_dict() != prior_state.to_dict()
        self.plant_modifier_state = state
        self.plant_aware = bool(state.active)
        self._legacy_plant_aware_bridge = True
        with self._scene_state_guard():
            if self.current_animation:
                self.current_animation.update_parameters({
                    'plant_aware': self.plant_aware,
                    'plant_modifiers': state.to_dict(),
                })
            if getattr(self, '_scene_mode', False) and self._scene_overlay:
                self._scene_overlay['animation'].update_parameters({
                    'plant_aware': self.plant_aware,
                    'plant_modifiers': state.to_dict(),
                })
        self._update_preview_plant_state()
        if changed:
            self._receiver_plant_revision = (
                getattr(self, "_receiver_plant_revision", 0) + 1
            )
            self._refresh_receiver_hybrid_context("plant_modifiers")
        return self.plant_aware

    def set_plant_modifiers(self, state: Any) -> Dict[str, Any]:
        """Validate and apply modifier authority live and to every future start."""
        requested = PlantModifierState.from_payload(state)
        prior_state = getattr(self, "plant_modifier_state", PlantModifierState.empty())
        changed = requested.to_dict() != prior_state.to_dict()
        self.plant_modifier_state = requested
        self._legacy_plant_aware_bridge = False
        self.plant_aware = bool(self.plant_modifier_state.active)
        with self._scene_state_guard():
            if self.current_animation:
                self.current_animation.update_parameters({
                    'plant_aware': False,
                    'plant_modifiers': self.plant_modifier_state.to_dict(),
                })
            if getattr(self, '_scene_mode', False) and self._scene_overlay:
                self._scene_overlay['animation'].update_parameters({
                    'plant_aware': False,
                    'plant_modifiers': self.plant_modifier_state.to_dict(),
                })
        self._update_preview_plant_state()
        if changed:
            self._receiver_plant_revision = (
                getattr(self, "_receiver_plant_revision", 0) + 1
            )
            self._refresh_receiver_hybrid_context("plant_modifiers")
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
            session['animation'].update_parameters({
                'plant_aware': self.plant_aware if self._legacy_plant_aware_bridge else False,
                'plant_modifiers': self.plant_modifier_state.to_dict(),
            })
    
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

    def list_components(
        self, provider: Optional[str] = None, role: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return the descriptor-only component catalog used by scene clients."""
        catalog = self.plugin_loader.component_catalog()
        catalog.extend(receiver_static_component_catalog(self.feature_flags))
        return [
            descriptor for descriptor in catalog
            if (provider is None or descriptor.get("provider") == provider)
            and (role is None or descriptor.get("role") == role)
        ]

    def scene_provider_policy(self) -> SceneProviderPolicy:
        """Return the explicit product policy shared by API and persistence."""
        return SceneProviderPolicy(
            receiver_local_background=self.feature_flags.receiver_local_background,
            receiver_sparse_overlay=self.feature_flags.receiver_sparse_overlay,
        )

    @staticmethod
    def _scene_parameter_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise TypeError("component parameters must be an object")
        return {
            key: value for key, value in payload.items()
            if key not in {"plant_aware", "plant_modifiers", "vibe", "output"}
        }

    @staticmethod
    def _component_snapshot_fingerprint(parameters: Dict[str, Any]) -> str:
        encoded = json.dumps(
            parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _scene_descriptor(
        self,
        ref: ComponentRef,
        expected_role: str,
        *,
        allow_compatibility_component: bool = False,
    ) -> Dict[str, Any]:
        if ref.provider is ComponentProvider.RECEIVER_NATIVE:
            descriptor = receiver_static_component_descriptor(self.feature_flags)
            if descriptor is None or ref.plugin_id != COMPILED_RAINBOW_PLUGIN_ID:
                raise ValueError(
                    "receiver-native scene background is unavailable under the "
                    "active feature policy"
                )
            if expected_role != "background":
                raise ValueError("receiver-native components may only be scene backgrounds")
            if descriptor.get("role") != expected_role:
                raise ValueError(
                    f"scene {expected_role} {ref.plugin_id!r} is declared as "
                    f"{descriptor.get('role')!r}"
                )
            return descriptor
        if ref.provider is not ComponentProvider.PYTHON:
            raise ValueError(f"unsupported live scene provider: {ref.provider.value}")
        descriptor = self.plugin_loader.get_component_descriptor(ref.plugin_id)
        if (
            descriptor is None
            and allow_compatibility_component
            and ref.plugin_id in self.plugin_loader.loaded_plugins
        ):
            role = self._plugin_role(ref.plugin_id)
            descriptor = {
                "plugin_id": ref.plugin_id,
                "provider": "python",
                "role": role,
                "defaults": {},
                "compatibility": {
                    "classification": "legacy_runtime_adapter",
                    "composable": role in {"background", "overlay"},
                },
            }
        if descriptor is None:
            raise ValueError(f"scene component not found: {ref.plugin_id}")
        if descriptor.get("provider") != ComponentProvider.PYTHON.value:
            raise ValueError(
                f"scene component {ref.plugin_id!r} does not use the python provider"
            )
        actual_role = descriptor.get("role")
        if actual_role != expected_role:
            raise ValueError(
                f"scene {expected_role} {ref.plugin_id!r} is declared as {actual_role!r}"
            )
        compatibility = descriptor.get("compatibility") or {}
        if compatibility.get("composable") is not True:
            classification = compatibility.get("classification", "incompatible")
            raise ValueError(
                f"scene component {ref.plugin_id!r} is not composable ({classification})"
            )
        animation_class = self.plugin_loader.get_plugin(ref.plugin_id)
        if animation_class is None:
            raise ValueError(f"scene component implementation not found: {ref.plugin_id}")
        if issubclass(animation_class, StatefulAnimationBase):
            raise TypeError("Stateful animations cannot participate in scenes")
        return descriptor

    def _resolve_component_ref(
        self,
        ref: ComponentRef,
        *,
        expected_role: str,
        allow_compatibility_component: bool = False,
    ) -> tuple[ComponentRef, Dict[str, Any]]:
        descriptor = self._scene_descriptor(
            ref,
            expected_role,
            allow_compatibility_component=allow_compatibility_component,
        )
        defaults = self._scene_parameter_payload(dict(descriptor.get("defaults") or {}))
        snapshot = self._scene_parameter_payload(dict(ref.resolved_parameters))
        overrides = self._scene_parameter_payload(dict(ref.parameter_overrides))
        resolved = snapshot if snapshot else defaults
        resolved.update(overrides)
        if ref.provider is ComponentProvider.RECEIVER_NATIVE:
            resolved = validate_compiled_rainbow_parameters(resolved)
            build = descriptor.get("build") or {}
            if (
                ref.bundle_digest != build.get("bundle_digest")
                or ref.expected_payload_digest
                != build.get("expected_payload_digest")
            ):
                raise ValueError(
                    "receiver-native component identity does not match the compiled contract"
                )
        else:
            try:
                resolved = self.plugin_loader.validate_component_parameters(
                    ref.plugin_id, resolved
                )
            except ValueError:
                if not allow_compatibility_component:
                    raise
        canonical = ComponentRef(
            plugin_id=ref.plugin_id,
            provider=ref.provider,
            preset_id=ref.preset_id,
            preset_fingerprint=ref.preset_fingerprint,
            parameter_overrides=overrides,
            resolved_parameters=resolved,
            bundle_digest=ref.bundle_digest,
            expected_payload_digest=ref.expected_payload_digest,
        )
        return canonical, resolved

    def _resolve_scene_state(
        self, payload: Any, *, allow_compatibility_components: bool = False
    ) -> SceneState:
        scene = payload if isinstance(payload, SceneState) else SceneState.from_payload(payload)
        if len(scene.overlays) > 1:
            raise ValueError("live scene version 1 supports at most one overlay")
        if scene.overlays and scene.overlays[0].slot_id != AGGREGATE_OVERLAY_SLOT_ID:
            raise ValueError(
                "live scene version 1 supports only the fixed "
                f"{AGGREGATE_OVERLAY_SLOT_ID!r} overlay slot"
            )
        background, _ = self._resolve_component_ref(
            scene.background,
            expected_role="background",
            allow_compatibility_component=allow_compatibility_components,
        )
        fallback, _ = self._resolve_component_ref(
            scene.known_python_fallback,
            expected_role="background",
            allow_compatibility_component=allow_compatibility_components,
        )
        overlays = []
        for overlay in scene.overlays:
            component, _ = self._resolve_component_ref(
                overlay.component,
                expected_role="overlay",
                allow_compatibility_component=allow_compatibility_components,
            )
            overlays.append(OverlayRef(
                slot_id=overlay.slot_id,
                component=component,
                enabled=overlay.enabled,
                opacity=overlay.opacity,
                placement=overlay.placement,
                stale_policy=overlay.stale_policy,
            ))
        if background.provider is ComponentProvider.RECEIVER_NATIVE:
            for overlay in overlays:
                if (
                    overlay.stale_policy.policy
                    is not ForegroundStalePolicy.CLEAR_AFTER_LEASE
                ):
                    raise ValueError(
                        "receiver hybrid overlays require clear_after_lease stale policy"
                    )
        return SceneState(
            revision=scene.revision,
            background=background,
            overlays=tuple(overlays),
            known_python_fallback=fallback,
        )

    @staticmethod
    def _component_preset_status(ref: ComponentRef) -> Dict[str, Any]:
        resolved = dict(ref.resolved_parameters)
        dirty = bool(ref.parameter_overrides)
        diagnostic = "live_overrides" if dirty else (
            "preset_snapshot" if ref.preset_id else "direct_parameters"
        )
        return {
            "preset_id": ref.preset_id,
            "preset_fingerprint": ref.preset_fingerprint,
            "resolved_fingerprint": AnimationManager._component_snapshot_fingerprint(
                resolved
            ),
            "is_dirty": dirty,
            "diagnostic": diagnostic,
        }

    def _plugin_role(self, plugin_name: str) -> str:
        """Resolve a role through the Phase 2B Python compatibility adapter."""
        manifest = self.plugin_loader.plugin_manifests.get(plugin_name) or {}
        role = manifest.get('role')
        if role is None and isinstance(manifest.get('component'), dict):
            role = manifest['component'].get('role')
        if role is not None:
            return str(role)
        animation_class = self.plugin_loader.loaded_plugins.get(plugin_name)
        if plugin_name == 'clock' or (
            isinstance(animation_class, type)
            and issubclass(animation_class, StatefulAnimationBase)
        ):
            return 'full_scene'
        return 'background'
    
    def get_animation_info(self, animation_name: str) -> Optional[Dict[str, Any]]:
        """Get legacy RGB-animation details, excluding scene-only overlays."""
        if self._plugin_role(animation_name) == 'overlay':
            return None
        return self.plugin_loader.get_plugin_info(animation_name)

    def _component_config(self, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        effective = dict(config or {})
        effective['plant_aware'] = (
            self.plant_aware if self._legacy_plant_aware_bridge else False
        )
        effective['plant_modifiers'] = self.plant_modifier_state.to_dict()
        return effective

    def _scene_state_guard(self) -> threading.RLock:
        lock = getattr(self, '_scene_lock', None)
        if lock is None:
            lock = threading.RLock()
            self._scene_lock = lock
        return lock

    def _new_scene_component(
        self,
        name: str,
        animation: AnimationBase,
        config: Optional[Dict[str, Any]],
        *,
        started_at: float,
        ref: Optional[ComponentRef] = None,
    ) -> Dict[str, Any]:
        return {
            'name': name,
            'animation': animation,
            'config': dict(config or {}),
            'ref': ref,
            'started_at': started_at,
            'last_unscaled_elapsed': 0.0,
            'scaled_elapsed': 0.0,
            'frame_index': 0,
            'cached_frame': None,
            'grade_state': self._empty_presentation_state(),
            'force_changed': True,
            'calls': 0,
            'changed_calls': 0,
            'render_count': 0,
            'last_revision': None,
        }

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
        self._scene_epoch = time.time_ns() & ((1 << 64) - 1)
        self._presentation_refresh_pending = True
        self._live_presentation_state = self._empty_presentation_state()

    def _launch_animation_loop(self) -> None:
        with self._run_state_guard():
            run_generation = self._run_generation
        self.animation_thread = threading.Thread(
            target=self._animation_loop, args=(run_generation,), daemon=True
        )
        self.animation_thread.start()

    def _run_state_guard(self) -> threading.RLock:
        lock = getattr(self, '_run_state_lock', None)
        if lock is None:
            lock = threading.RLock()
            self._run_state_lock = lock
        return lock

    def _run_is_active(self, run_generation: int) -> bool:
        with self._run_state_guard():
            return bool(
                self.is_running
                and not self.stop_event.is_set()
                and getattr(self, '_run_generation', 0) == run_generation
            )

    def _run_owns_generation(self, run_generation: int) -> bool:
        """Return false only when an external stop/restart revoked this loop."""
        with self._run_state_guard():
            return bool(
                not self.stop_event.is_set()
                and getattr(self, '_run_generation', 0) == run_generation
            )

    def start_composed_scene(
        self,
        background_name: str,
        background_config: Optional[Dict[str, Any]] = None,
        overlay_name: str = 'clock_overlay',
        overlay_config: Optional[Dict[str, Any]] = None,
        overlay_opacity: int = 255,
        strip_offset: int = 0,
        led_offset: int = 0,
    ) -> bool:
        """Compatibility wrapper for the fixed Phase 2B composed-scene API."""
        try:
            background_parameters = self._scene_parameter_payload(background_config)
            overlay_parameters = self._scene_parameter_payload(overlay_config)
            background = ComponentRef(
                plugin_id=background_name,
                provider=ComponentProvider.PYTHON,
                resolved_parameters=background_parameters,
            )
            scene = SceneState(
                revision=0,
                background=background,
                overlays=(OverlayRef(
                    slot_id=AGGREGATE_OVERLAY_SLOT_ID,
                    component=ComponentRef(
                        plugin_id=overlay_name,
                        provider=ComponentProvider.PYTHON,
                        resolved_parameters=overlay_parameters,
                    ),
                    enabled=True,
                    opacity=overlay_opacity,
                    placement=OverlayPlacement(
                        strip_translation=strip_offset,
                        led_translation=led_offset,
                        clip_policy=ClipPolicy.CLIP_TO_WALL,
                    ),
                    stale_policy=StalePolicy(ForegroundStalePolicy.HOLD),
                ),),
                known_python_fallback=background,
            )
        except (TypeError, ValueError) as exc:
            print(f"✗ Invalid composed scene: {exc}")
            return False
        return self.start_scene(scene, _allow_compatibility_components=True)

    def _receiver_hybrid_capability_error(self) -> Optional[str]:
        if not (
            self.feature_flags.receiver_local_background
            and self.feature_flags.receiver_sparse_overlay
        ):
            return "receiver hybrid rollout flags are disabled"
        required = (
            "start_local_background",
            "update_local_background_params",
            "update_presentation_context",
            "publish_sparse_overlay",
            "renew_sparse_overlay",
            "clear_sparse_overlay",
            "set_all_pixels",
        )
        missing = [
            name for name in required
            if not callable(getattr(self.controller, name, None))
        ]
        if missing:
            return "controller lacks receiver hybrid APIs: " + ", ".join(missing)
        return None

    def _next_receiver_context_revision(self, source_revision: int) -> int:
        next_revision = max(self._receiver_context_revision + 1, int(source_revision))
        if next_revision > (1 << 64) - 1:
            raise OverflowError("receiver presentation-context revision is exhausted")
        return next_revision

    def _receiver_scene_time_us(self, now: Optional[float] = None) -> int:
        current = time.perf_counter() if now is None else float(now)
        elapsed = max(0.0, current - float(self.start_time))
        return min((1 << 64) - 1, int(elapsed * 1_000_000.0))

    def _receiver_presentation_context(
        self,
        publisher: ReceiverSparsePublisher,
        *,
        revision: int,
        present_at_scene_time_us: int,
    ) -> ReceiverPresentationContext:
        with self._presentation_state_guard():
            vibe = self._resolved_vibe
        return ReceiverPresentationContext(
            controller_session_id=publisher.controller_session_id,
            scene_revision=revision,
            scene_epoch=self._scene_epoch,
            present_at_scene_time_us=present_at_scene_time_us,
            vibe=vibe,
            plant_modifiers=self.plant_modifier_state,
            plant_revision=self._receiver_plant_revision,
        )

    @staticmethod
    def _apply_receiver_luminance_rgba(
        frame: OverlayFrame,
        *,
        luminance_q8_8: int,
        state: Dict[str, Any],
        force_refresh: bool = False,
    ) -> OverlayFrame:
        identity = int(luminance_q8_8)
        refresh = force_refresh or state.get("identity") != identity
        state["identity"] = identity
        if identity == Q8_8_ONE:
            state["cached"] = None
            return OverlayFrame(
                frame.pixels,
                revision=frame.revision,
                changed=frame.changed or refresh,
                dirty_ranges=None if refresh and not frame.changed else frame.dirty_ranges,
            )
        if not frame.changed and not refresh and state.get("cached") is not None:
            return OverlayFrame(
                state["cached"],
                revision=frame.revision,
                changed=False,
                dirty_ranges=(),
            )
        geometry = frame.pixels.shape
        if state.get("geometry") != geometry:
            state["buffers"] = [
                np.empty(geometry, dtype=np.uint8),
                np.empty(geometry, dtype=np.uint8),
            ]
            state["index"] = 0
            state["geometry"] = geometry
        output = state["buffers"][state["index"]]
        state["index"] = (state["index"] + 1) % len(state["buffers"])
        working = frame.pixels[:, :3].astype(np.uint16)
        working *= identity
        working += 128
        np.floor_divide(working, Q8_8_ONE, out=working)
        np.minimum(working, frame.pixels[:, 3:4], out=working)
        np.copyto(output[:, :3], working, casting="unsafe")
        np.copyto(output[:, 3], frame.pixels[:, 3])
        state["cached"] = output
        return OverlayFrame(
            output,
            revision=frame.revision,
            changed=frame.changed or refresh,
            dirty_ranges=None if refresh else frame.dirty_ranges,
        )

    def _render_receiver_foreground(
        self,
        *,
        now: float,
        force_refresh: bool = False,
    ) -> OverlayFrame:
        with self._scene_state_guard():
            compositor = self._receiver_foreground_compositor
            if not self._receiver_hybrid_mode or compositor is None:
                raise RuntimeError("no receiver hybrid scene is active")
            overlay = self._scene_overlay
            placed = ()
            with self._presentation_state_guard():
                resolved = self._resolved_vibe
                operator_tempo = self.animation_speed_scale
            if overlay is not None and overlay['enabled']:
                source = self._render_scene_component(
                    overlay,
                    now=now,
                    resolved_vibe=resolved,
                    operator_tempo=operator_tempo,
                    overlay=True,
                )
                placed = (PlacedOverlay(
                    source,
                    opacity=overlay['opacity'],
                    strip_offset=overlay['strip_offset'],
                    led_offset=overlay['led_offset'],
                    enabled=True,
                ),)
            aggregate = compositor.compose(placed)
            luminance = quantize_q8_8(
                resolved.profile.luminance_scale,
                name="luminance_scale",
                maximum=Q8_8_ONE,
            )
            return self._apply_receiver_luminance_rgba(
                aggregate,
                luminance_q8_8=luminance,
                state=self._receiver_foreground_presentation_state,
                force_refresh=force_refresh,
            )

    @staticmethod
    def _source_over_receiver_preview(
        base: np.ndarray, foreground: np.ndarray
    ) -> np.ndarray:
        work = base.astype(np.uint16)
        inverse_alpha = 255 - foreground[:, 3].astype(np.uint16)
        work *= inverse_alpha[:, None]
        work += 127
        np.floor_divide(work, 255, out=work)
        work += foreground[:, :3].astype(np.uint16)
        np.minimum(work, 255, out=work)
        return work.astype(np.uint8)

    def _receiver_preview_frame(
        self,
        scene: SceneState,
        foreground: OverlayFrame,
        *,
        scene_time_us: int,
        resolved_vibe: Optional[ResolvedVibe] = None,
    ) -> np.ndarray:
        resolved = self._resolved_vibe if resolved_vibe is None else resolved_vibe
        luminance = quantize_q8_8(
            resolved.profile.luminance_scale,
            name="luminance_scale",
            maximum=Q8_8_ONE,
        )
        base = render_compiled_rainbow_preview(
            scene_time_us,
            scene.background.resolved_parameters,
            strip_count=self.controller.strip_count,
            leds_per_strip=self.controller.leds_per_strip,
            luminance_q8_8=luminance,
        )
        return self._source_over_receiver_preview(base, foreground.pixels)

    def _publish_receiver_foreground(
        self,
        frame: OverlayFrame,
        *,
        scene_time_us: int,
        now: float,
    ) -> bool:
        publisher = self._receiver_sparse_publisher
        if publisher is None or self._receiver_context is None:
            return False
        with self._presentation_io_guard():
            return publisher.publish_frame(
                frame,
                scene_revision=self._receiver_context_revision,
                scene_epoch=self._scene_epoch,
                base_revision=self._receiver_context_revision,
                present_at_scene_time_us=scene_time_us,
                now=now,
            )

    def _refresh_receiver_hybrid_context(self, reason: str) -> bool:
        """Restage receiver context and repair foreground without clock resets."""
        failure: Optional[Exception] = None
        with self._scene_state_guard():
            if (
                not self._receiver_hybrid_mode
                or self._active_scene_state is None
                or self._receiver_sparse_publisher is None
            ):
                return True
            scene = self._active_scene_state
            publisher = self._receiver_sparse_publisher
            try:
                publisher.begin_new_session()
                revision = self._next_receiver_context_revision(scene.revision)
                now = time.perf_counter()
                scene_time_us = self._receiver_scene_time_us(now)
                context = self._receiver_presentation_context(
                    publisher,
                    revision=revision,
                    present_at_scene_time_us=scene_time_us,
                )
                with self._presentation_io_guard():
                    accepted = self.controller.update_presentation_context(context)
                if accepted is False:
                    raise RuntimeError(
                        f"receiver context update for {reason} was not acknowledged"
                    )
                self._receiver_context_revision = revision
                self._receiver_context = context
                if self._scene_overlay is not None:
                    self._scene_overlay['force_changed'] = True
                foreground = self._render_receiver_foreground(
                    now=now, force_refresh=True
                )
                if not self._publish_receiver_foreground(
                    foreground,
                    scene_time_us=scene_time_us,
                    now=now,
                ):
                    raise RuntimeError(
                        f"receiver foreground repair for {reason} was not acknowledged"
                    )
                preview = self._receiver_preview_frame(
                    scene, foreground, scene_time_us=scene_time_us
                )
                with self.frame_data_lock:
                    self.current_frame_data = preview
                self._receiver_hybrid_error = None
                return True
            except Exception as exc:
                failure = exc
                print(f"✗ Receiver hybrid {reason} update fell back: {exc}")
                traceback.print_exc()
        assert failure is not None
        return self._activate_known_python_fallback(scene, failure)

    def _known_python_fallback_scene(self, scene: SceneState) -> SceneState:
        return SceneState(
            revision=min((1 << 64) - 1, scene.revision + 1),
            background=scene.known_python_fallback,
            overlays=(),
            known_python_fallback=scene.known_python_fallback,
        )

    def _fallback_snapshot(self, scene: SceneState) -> np.ndarray:
        preview = self.get_scene_preview(
            self._known_python_fallback_scene(scene), elapsed=0.0
        )
        return np.asarray(preview["frame_data"], dtype=np.uint8)

    def _activate_known_python_fallback(
        self, scene: SceneState, error: Any
    ) -> bool:
        diagnostic = str(error)
        try:
            snapshot = self._fallback_snapshot(scene)
        except Exception:
            traceback.print_exc()
            snapshot = np.zeros((self.controller.total_leds, 3), dtype=np.uint8)

        fallback_status = {
            "healthy": False,
            "fallback_active": True,
            "error": diagnostic,
            "source_scene_revision": scene.revision,
            "context_revision": self._receiver_context_revision,
        }
        self.stop_animation(clear_leds=False)
        self._receiver_last_status = fallback_status
        try:
            with self._presentation_io_guard():
                accepted = self.controller.set_all_pixels(snapshot)
            if accepted is False:
                raise RuntimeError("controller rejected complete fallback takeover")
        except Exception as exc:
            fallback_status["takeover_error"] = str(exc)
            return False
        with self.frame_data_lock:
            self.current_frame_data = snapshot
        started = self.start_scene(self._known_python_fallback_scene(scene))
        if not started:
            fallback_status["fallback_start_error"] = (
                "known Python fallback did not start"
            )
        self._receiver_last_status = fallback_status
        return started

    def _start_receiver_hybrid_scene(self, scene: SceneState) -> bool:
        capability_error = self._receiver_hybrid_capability_error()
        if capability_error is not None:
            print(f"✗ Receiver hybrid scene rejected: {capability_error}")
            return False

        overlay_ref = scene.overlays[0] if scene.overlays else None
        overlay = None
        overlay_component = None
        presentation_taken_over = False
        try:
            if overlay_ref is not None:
                overlay_class = self.plugin_loader.get_plugin(
                    overlay_ref.component.plugin_id
                )
                if overlay_class is None:
                    raise ValueError("receiver hybrid overlay implementation is missing")
                overlay = overlay_class(
                    self.controller,
                    self._component_config(dict(
                        overlay_ref.component.resolved_parameters
                    )),
                )
                if isinstance(overlay, StatefulAnimationBase):
                    raise TypeError("Stateful animations cannot be sparse overlays")
                overlay.start()
                overlay_component = self._new_scene_component(
                    overlay_ref.component.plugin_id,
                    overlay,
                    dict(overlay_ref.component.resolved_parameters),
                    started_at=time.perf_counter(),
                    ref=overlay_ref.component,
                )
                overlay_component.update({
                    'enabled': overlay_ref.enabled,
                    'opacity': overlay_ref.opacity,
                    'strip_offset': overlay_ref.placement.strip_translation,
                    'led_offset': overlay_ref.placement.led_translation,
                    'stale_policy': overlay_ref.stale_policy,
                })

            self.stop_animation(clear_leds=True)
            presentation_taken_over = True
            self._reset_run_counters()
            lease_ms = (
                overlay_ref.stale_policy.lease_ms
                if overlay_ref is not None else 3_000
            )
            publisher = ReceiverSparsePublisher(
                self.controller,
                lease_ms=3_000 if lease_ms is None else lease_ms,
            )
            context_revision = self._next_receiver_context_revision(scene.revision)
            with self._scene_state_guard():
                self._scene_mode = True
                self._scene_compatibility_mode = False
                self._scene_allows_compatibility_components = False
                self._scene_background = {
                    'name': scene.background.plugin_id,
                    'animation': None,
                    'config': dict(scene.background.resolved_parameters),
                    'ref': scene.background,
                    'frame_index': 0,
                    'calls': 0,
                    'changed_calls': 0,
                    'render_count': 0,
                }
                self._scene_overlay = overlay_component
                self._active_scene_state = scene
                self._scene_compositor = None
                self._receiver_hybrid_mode = True
                self._receiver_sparse_publisher = publisher
                self._receiver_foreground_compositor = HostForegroundCompositor(
                    self.controller.strip_count, self.controller.leds_per_strip
                )
                self._receiver_foreground_presentation_state = (
                    self._empty_presentation_state()
                )
                self._receiver_context_revision = context_revision
                self._receiver_hybrid_error = "starting"
                self._receiver_fallback_active = False
                self.current_animation = None
                self.current_animation_name = scene.background.plugin_id
                self.current_animation_hash = scene.background.bundle_digest
                self.current_preset = self._legacy_preset_from_ref(scene.background)

            context = self._receiver_presentation_context(
                publisher, revision=context_revision, present_at_scene_time_us=0
            )
            parameters = validate_compiled_rainbow_parameters(
                scene.background.resolved_parameters
            )
            with self._presentation_io_guard():
                started = self.controller.start_local_background(
                    context,
                    component_id=COMPILED_RAINBOW_COMPONENT_ID,
                    preferred_cadence_hz=parameters["preferred_cadence_hz"],
                    common_seed=parameters["common_seed"],
                )
            if started is False:
                raise RuntimeError("receiver local background start was not acknowledged")
            self._receiver_context = context
            foreground = self._render_receiver_foreground(
                now=self.start_time, force_refresh=True
            )
            if not self._publish_receiver_foreground(
                foreground, scene_time_us=0, now=self.start_time
            ):
                raise RuntimeError("initial sparse foreground snapshot was not acknowledged")
            with self._presentation_state_guard():
                resolved = self._resolved_vibe
            preview = self._receiver_preview_frame(
                scene, foreground, scene_time_us=0, resolved_vibe=resolved
            )
            with self.frame_data_lock:
                self.current_frame_data = preview
            self._receiver_hybrid_error = None
            self._receiver_last_status = None
            self._launch_animation_loop()
            print(f"✓ Started receiver hybrid scene: {scene.background.plugin_id}")
            return True
        except Exception as exc:
            if not presentation_taken_over:
                try:
                    self._cleanup_scene_component(overlay_component)
                except Exception:
                    traceback.print_exc()
                if overlay is not None and overlay_component is None:
                    try:
                        overlay.stop()
                        overlay.cleanup()
                    except Exception:
                        traceback.print_exc()
                print(f"✗ Failed to prepare receiver hybrid scene: {exc}")
                return False
            print(f"✗ Receiver hybrid scene fell back: {exc}")
            traceback.print_exc()
            return self._activate_known_python_fallback(scene, exc)

    def start_scene(
        self,
        scene_payload: Any,
        *,
        _compatibility_animation: bool = False,
        _allow_compatibility_components: bool = False,
    ) -> bool:
        """Validate and start a complete fixed-slot scene atomically."""
        try:
            scene = self._resolve_scene_state(
                scene_payload,
                allow_compatibility_components=_allow_compatibility_components,
            )
        except (TypeError, ValueError) as exc:
            print(f"✗ Invalid scene: {exc}")
            return False

        if scene.background.provider is ComponentProvider.RECEIVER_NATIVE:
            return self._start_receiver_hybrid_scene(scene)

        background_class = self.plugin_loader.get_plugin(scene.background.plugin_id)
        overlay_ref = scene.overlays[0] if scene.overlays else None
        overlay_class = (
            self.plugin_loader.get_plugin(overlay_ref.component.plugin_id)
            if overlay_ref else None
        )
        if background_class is None or (overlay_ref and overlay_class is None):
            return False

        background = None
        overlay = None
        background_component = None
        overlay_component = None
        presentation_taken_over = False
        try:
            background_config = dict(scene.background.resolved_parameters)
            background = background_class(
                self.controller, self._component_config(background_config)
            )
            if isinstance(background, StatefulAnimationBase):
                raise TypeError("Stateful animations cannot participate in composed scenes")
            if overlay_ref is not None:
                overlay_config = dict(overlay_ref.component.resolved_parameters)
                overlay = overlay_class(
                    self.controller, self._component_config(overlay_config)
                )
                if isinstance(overlay, StatefulAnimationBase):
                    raise TypeError(
                        "Stateful animations cannot participate in composed scenes"
                    )

            if hasattr(self.controller, 'configure'):
                try:
                    with self._presentation_io_guard():
                        self.controller.configure()
                except Exception as controller_error:
                    print(f"⚠️ Controller configure failed: {controller_error}")

            background.start()
            started_at = time.perf_counter()
            background_component = self._new_scene_component(
                scene.background.plugin_id,
                background,
                background_config,
                started_at=started_at,
                ref=scene.background,
            )
            if overlay_ref is not None:
                overlay.start()
                overlay_component = self._new_scene_component(
                    overlay_ref.component.plugin_id,
                    overlay,
                    overlay_config,
                    started_at=time.perf_counter(),
                    ref=overlay_ref.component,
                )
                overlay_component.update({
                    'enabled': overlay_ref.enabled,
                    'opacity': overlay_ref.opacity,
                    'strip_offset': overlay_ref.placement.strip_translation,
                    'led_offset': overlay_ref.placement.led_translation,
                    'stale_policy': overlay_ref.stale_policy,
                })

            # Component construction/start is side-effect-free with respect to
            # manager ownership. Keep the prior scene alive until every new
            # component has crossed its lifecycle start boundary successfully.
            self.stop_animation(clear_leds=True)
            presentation_taken_over = True
            self._reset_run_counters()
            with self._scene_lock:
                self._scene_mode = True
                self._scene_compatibility_mode = bool(_compatibility_animation)
                self._scene_allows_compatibility_components = bool(
                    _allow_compatibility_components
                )
                self._scene_background = background_component
                self._scene_overlay = overlay_component
                self._active_scene_state = scene
                self._scene_compositor = HostSceneCompositor(
                    self.controller.strip_count, self.controller.leds_per_strip
                )
                self._scene_final_presentation_state = self._empty_presentation_state()
                self.current_animation = background
                self.current_animation_name = scene.background.plugin_id
                self.current_animation_hash = self._compute_animation_hash(
                    scene.background.plugin_id
                )
                self.current_preset = self._legacy_preset_from_ref(scene.background)
                frame, _changed, _dirty = self._render_composed_scene_frame()
                with self.frame_data_lock:
                    self.current_frame_data = frame

            self._remember_active_state(
                scene.background.plugin_id,
                background_config,
                self.current_preset,
            )
            self._launch_animation_loop()
            label = scene.background.plugin_id
            if overlay_ref:
                label += f" + {overlay_ref.component.plugin_id}"
            print(f"✓ Started scene: {label}")
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
            # Construction can fail before a component state exists.
            for animation, component in (
                (overlay, overlay_component), (background, background_component)
            ):
                if animation is not None and component is None:
                    try:
                        animation.stop()
                        animation.cleanup()
                    except Exception:
                        traceback.print_exc()
            if presentation_taken_over:
                with self._scene_lock:
                    self._clear_scene_state()
            print(f"✗ Failed to start composed scene: {exc}")
            traceback.print_exc()
            return False

    def _clear_scene_state(self) -> None:
        self._scene_mode = False
        self._scene_background = None
        self._scene_overlay = None
        self._scene_compositor = None
        self._active_scene_state = None
        self._scene_compatibility_mode = False
        self._scene_allows_compatibility_components = False
        self._scene_final_presentation_state = self._empty_presentation_state()
        self._receiver_hybrid_mode = False
        self._receiver_sparse_publisher = None
        self._receiver_foreground_compositor = None
        self._receiver_context = None
        self._receiver_foreground_presentation_state = self._empty_presentation_state()
        self._receiver_hybrid_error = None
        self._receiver_fallback_active = False
        self.current_animation = None
        self.current_animation_name = None
        self.current_animation_hash = None
        self.current_preset = None
    
    def start_animation(
        self,
        animation_name: str,
        config: Dict[str, Any] = None,
        preset: Optional[Dict[str, Any]] = None,
    ) -> bool:
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
            if self._plugin_role(animation_name) == 'overlay':
                print(
                    f"✗ Overlay component {animation_name} requires "
                    "start_composed_scene()"
                )
                return False
            animation_class = self.plugin_loader.get_plugin(animation_name)
            if animation_class is None:
                print(f"✗ Animation not found: {animation_name}")
                return False
            if (
                self._plugin_role(animation_name) == 'background'
                and not issubclass(animation_class, StatefulAnimationBase)
            ):
                parameters = self._scene_parameter_payload(config)
                selection = self._normalize_current_preset(preset, animation_name)
                preset_id = selection['preset_id'] if selection else None
                preset_fingerprint = None
                if selection:
                    preset_fingerprint = component_preset_fingerprint(
                        animation_name, selection["preset_id"], parameters
                    )
                ref = ComponentRef(
                    plugin_id=animation_name,
                    provider=ComponentProvider.PYTHON,
                    preset_id=preset_id,
                    preset_fingerprint=preset_fingerprint,
                    resolved_parameters=parameters,
                )
                started = self.start_scene(
                    SceneState(0, ref, (), ref),
                    _compatibility_animation=True,
                    _allow_compatibility_components=True,
                )
                if started:
                    self.current_preset = selection
                    self._sync_last_active_preset()
                return started
            # Stop current animation if running
            self.stop_animation(clear_leds=True)
            
            # Create animation instance
            effective_config = self._component_config(config)
            self.current_animation = animation_class(self.controller, effective_config)
            self.current_animation_name = animation_name
            self.current_animation_hash = self._compute_animation_hash(animation_name)
            self.current_preset = self._normalize_current_preset(preset, animation_name)

            print(f"🔍 Animation instance created: {type(self.current_animation)}")
            print(f"🔍 Is StatefulAnimationBase? {isinstance(self.current_animation, StatefulAnimationBase)}")

            # Ensure controller is configured before frames start flowing
            if hasattr(self.controller, "configure"):
                try:
                    with self._presentation_io_guard():
                        self.controller.configure()
                except Exception as controller_error:
                    print(f"⚠️ Controller configure failed: {controller_error}")

            # Start animation
            self.current_animation.start()
            self._reset_run_counters()
            self._refresh_active_presentation_context()

            # Check if this is a stateful animation
            if isinstance(self.current_animation, StatefulAnimationBase):
                # Stateful animations manage their own threads and timing
                print(f"✓ Started stateful animation: {animation_name}")
            else:
                # Frame-based animations need the animation loop
                self._launch_animation_loop()
                print(f"✓ Started frame-based animation: {animation_name}")

            self._remember_active_state(
                animation_name, restore_config, self.current_preset
            )

            return True
            
        except Exception as e:
            print(f"✗ Failed to start animation {animation_name}: {e}")
            traceback.print_exc()
            return False

    @staticmethod
    def _legacy_preset_from_ref(ref: ComponentRef) -> Optional[Dict[str, Any]]:
        if ref.preset_id is None:
            return None
        return {
            'preset_id': ref.preset_id,
            'name': ref.preset_id.replace('_', ' ').replace('-', ' ').title(),
            'animation': ref.plugin_id,
            'is_dirty': bool(ref.parameter_overrides),
        }
    
    def stop_animation(self, clear_leds: bool = True):
        """Stop current animation or painter mode output."""
        had_output = self.is_running or self.painter_active or self._scene_mode
        receiver_takeover = False

        if self.is_running or self._scene_mode:
            with self._run_state_guard():
                self._run_generation = getattr(self, '_run_generation', 0) + 1
                self.is_running = False
                self.stop_event.set()

            # Stop frame-based animation thread if it exists
            if (
                self.animation_thread
                and self.animation_thread.is_alive()
                and self.animation_thread is not threading.current_thread()
            ):
                self.animation_thread.join(timeout=1.0)
            self.animation_thread = None

            if self._scene_mode:
                with self._scene_lock:
                    overlay = self._scene_overlay
                    background = self._scene_background
                    publisher = self._receiver_sparse_publisher
                    was_receiver_hybrid = self._receiver_hybrid_mode
                    receiver_scene = self._active_scene_state
                    try:
                        self._cleanup_scene_component(overlay)
                    finally:
                        self._cleanup_scene_component(background)
                    if publisher is not None:
                        publisher_status = publisher.get_status()
                        publisher.close(clear=False)
                        self._receiver_last_status = {
                            "healthy": False,
                            "fallback_active": False,
                            "error": None,
                            "operation": "host_takeover",
                            "source_scene_revision": (
                                receiver_scene.revision
                                if receiver_scene is not None else None
                            ),
                            "context_revision": self._receiver_context_revision,
                            "publisher": publisher_status,
                        }
                    if was_receiver_hybrid:
                        with self.frame_data_lock:
                            current = np.asarray(self.current_frame_data, dtype=np.uint8)
                        if (
                            not clear_leds
                            and current.shape == (self.controller.total_leds, 3)
                        ):
                            takeover_frame = current
                        else:
                            takeover_frame = np.zeros(
                                (self.controller.total_leds, 3), dtype=np.uint8
                            )
                        try:
                            with self._presentation_io_guard():
                                accepted = self.controller.set_all_pixels(takeover_frame)
                            receiver_takeover = accepted is not False
                            if not receiver_takeover:
                                raise RuntimeError(
                                    "controller rejected complete host takeover"
                                )
                        except Exception as exc:
                            receiver_takeover = True
                            status = dict(self._receiver_last_status or {})
                            status.update({
                                "healthy": False,
                                "fallback_active": False,
                                "takeover_error": str(exc),
                            })
                            self._receiver_last_status = status
                    self._clear_scene_state()
            else:
                # Stateful compatibility animations handle their own threads.
                if self.current_animation:
                    self.current_animation.stop()
                    self.current_animation.cleanup()
                    self.current_animation = None

                self.current_animation_name = None
                self.current_preset = None
            self.frame_timestamps.clear()
            with self.frame_data_lock:
                self.current_frame_data = []

            print("✓ Animation stopped")

        if self.painter_active:
            self.painter_active = False
            self.current_animation_name = None
            self.current_preset = None
            self.frame_timestamps.clear()
            with self.frame_data_lock:
                self.current_frame_data = []
            print("✓ Painter mode stopped")

        self.current_animation_hash = None

        if clear_leds and had_output and not receiver_takeover:
            with self._presentation_io_guard():
                self.controller.clear()
    
    def update_animation_parameters(self, params: Dict[str, Any]) -> bool:
        """Update current animation parameters in real-time"""
        if self._scene_mode:
            return self.update_scene_component("background", params=params)
        if self.current_animation:
            try:
                requested_params = dict(params)
                effective_params = dict(requested_params)
                effective_params['plant_aware'] = self.plant_aware if self._legacy_plant_aware_bridge else False
                effective_params['plant_modifiers'] = self.plant_modifier_state.to_dict()
                self.current_animation.update_parameters(effective_params)
                if self.current_preset is not None:
                    self.current_preset['is_dirty'] = True
                if (
                    self._last_active_state is not None
                    and self._last_active_state.get('animation') == self.current_animation_name
                ):
                    restore_params = {
                        key: value for key, value in requested_params.items()
                        if key not in {'plant_aware', 'plant_modifiers'}
                    }
                    self._last_active_state['config'].update(restore_params)
                    self._sync_last_active_preset()
                print(f"✓ Updated animation parameters: {effective_params}")
                return True
            except Exception as e:
                print(f"✗ Failed to update parameters: {e}")
                return False
        return False

    def update_overlay(
        self,
        params: Optional[Dict[str, Any]] = None,
        *,
        opacity: Optional[int] = None,
        strip_offset: Optional[int] = None,
        led_offset: Optional[int] = None,
    ) -> bool:
        """Update the fixed foreground without restarting the background."""
        placement = None
        if strip_offset is not None or led_offset is not None:
            current = self._scene_overlay
            if current is None:
                return False
            placement = {
                "strip_translation": current['strip_offset'] if strip_offset is None else strip_offset,
                "led_translation": current['led_offset'] if led_offset is None else led_offset,
                "clip_policy": ClipPolicy.CLIP_TO_WALL.value,
            }
        return self.update_scene_component(
            "overlay", params=params, opacity=opacity, placement=placement
        )

    def update_overlay_parameters(self, params: Dict[str, Any]) -> bool:
        return self.update_overlay(params)

    def set_overlay_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise TypeError("overlay enabled state must be boolean")
        return self.update_scene_component("overlay", enabled=enabled)

    def enable_overlay(self) -> bool:
        return self.set_overlay_enabled(True)

    def disable_overlay(self) -> bool:
        return self.set_overlay_enabled(False)

    def remove_overlay(self) -> bool:
        return self.update_scene_component("overlay", remove=True)

    def get_scene_state(self) -> Optional[Dict[str, Any]]:
        """Return a detached, scene-only serialized snapshot of live state."""
        with self._scene_state_guard():
            return (
                self._active_scene_state.to_dict()
                if self._scene_mode and self._active_scene_state is not None
                else None
            )

    def read_scene(self) -> Optional[Dict[str, Any]]:
        return self.get_scene_state()

    def stop_scene(self, clear_leds: bool = True) -> bool:
        if not self._scene_mode:
            return False
        self.stop_animation(clear_leds=clear_leds)
        return True

    def _update_receiver_hybrid_component(
        self,
        target: str,
        params: Optional[Dict[str, Any]],
        *,
        component: Optional[Any],
        enabled: Optional[bool],
        opacity: Optional[int],
        placement: Optional[Any],
        stale_policy: Optional[Any],
        remove: bool,
    ) -> bool:
        failure: Optional[Exception] = None
        with self._scene_state_guard():
            scene = self._active_scene_state
            if not self._receiver_hybrid_mode or scene is None:
                return False
            requested = self._scene_parameter_payload(params)
            try:
                if target == "background":
                    resolved = dict(scene.background.resolved_parameters)
                    resolved.update(requested)
                    resolved = validate_compiled_rainbow_parameters(resolved)
                    overrides = dict(scene.background.parameter_overrides)
                    overrides.update(requested)
                    background = ComponentRef(
                        plugin_id=scene.background.plugin_id,
                        provider=scene.background.provider,
                        preset_id=scene.background.preset_id,
                        preset_fingerprint=scene.background.preset_fingerprint,
                        parameter_overrides=overrides,
                        resolved_parameters=resolved,
                        bundle_digest=scene.background.bundle_digest,
                        expected_payload_digest=scene.background.expected_payload_digest,
                    )
                    candidate = self._resolve_scene_state(SceneState(
                        scene.revision + 1,
                        background,
                        scene.overlays,
                        scene.known_python_fallback,
                    ))
                    with self._presentation_io_guard():
                        accepted = self.controller.update_local_background_params(
                            preferred_cadence_hz=resolved["preferred_cadence_hz"],
                            common_seed=resolved["common_seed"],
                        )
                    if accepted is False:
                        raise RuntimeError(
                            "receiver local background parameter update was not acknowledged"
                        )
                    assert self._scene_background is not None
                    self._scene_background['config'] = resolved
                    self._scene_background['ref'] = background
                    self._active_scene_state = candidate
                    return self._refresh_receiver_hybrid_context(
                        "background_parameters"
                    )

                old_overlay = scene.overlays[0] if scene.overlays else None
                old_runtime = self._scene_overlay
                if remove:
                    if old_overlay is None:
                        return False
                    candidate = self._resolve_scene_state(SceneState(
                        scene.revision + 1,
                        scene.background,
                        (),
                        scene.known_python_fallback,
                    ))
                    self._scene_overlay = None
                    self._active_scene_state = candidate
                    refreshed = self._refresh_receiver_hybrid_context(
                        "overlay_remove"
                    )
                    self._cleanup_scene_component(old_runtime)
                    return refreshed

                if component is None:
                    if old_overlay is None:
                        return False
                    component_ref = old_overlay.component
                else:
                    component_ref = (
                        component if isinstance(component, ComponentRef)
                        else ComponentRef.from_payload(component)
                    )
                resolved = dict(component_ref.resolved_parameters)
                resolved.update(requested)
                resolved = self.plugin_loader.validate_component_parameters(
                    component_ref.plugin_id, resolved
                )
                overrides = dict(component_ref.parameter_overrides)
                overrides.update(requested)
                component_ref = ComponentRef(
                    plugin_id=component_ref.plugin_id,
                    provider=component_ref.provider,
                    preset_id=component_ref.preset_id,
                    preset_fingerprint=component_ref.preset_fingerprint,
                    parameter_overrides=overrides,
                    resolved_parameters=resolved,
                )
                current_placement = (
                    old_overlay.placement if old_overlay else OverlayPlacement()
                )
                resolved_placement = (
                    placement if isinstance(placement, OverlayPlacement)
                    else OverlayPlacement.from_payload(placement)
                    if placement is not None else current_placement
                )
                current_stale = (
                    old_overlay.stale_policy
                    if old_overlay else StalePolicy(
                        ForegroundStalePolicy.CLEAR_AFTER_LEASE, 3_000
                    )
                )
                resolved_stale = (
                    stale_policy if isinstance(stale_policy, StalePolicy)
                    else StalePolicy.from_payload(stale_policy)
                    if stale_policy is not None else current_stale
                )
                overlay_ref = OverlayRef(
                    slot_id=AGGREGATE_OVERLAY_SLOT_ID,
                    component=component_ref,
                    enabled=(
                        old_overlay.enabled if enabled is None and old_overlay else True
                    ) if enabled is None else enabled,
                    opacity=(
                        old_overlay.opacity if opacity is None and old_overlay else 255
                    ) if opacity is None else opacity,
                    placement=resolved_placement,
                    stale_policy=resolved_stale,
                )
                candidate = self._resolve_scene_state(SceneState(
                    scene.revision + 1,
                    scene.background,
                    (overlay_ref,),
                    scene.known_python_fallback,
                ))
                overlay_ref = candidate.overlays[0]
                replacing = (
                    old_runtime is None
                    or old_runtime['name'] != overlay_ref.component.plugin_id
                )
                runtime = old_runtime
                if replacing:
                    animation_class = self.plugin_loader.get_plugin(
                        overlay_ref.component.plugin_id
                    )
                    if animation_class is None:
                        raise ValueError("overlay implementation not found")
                    animation = animation_class(
                        self.controller,
                        self._component_config(dict(
                            overlay_ref.component.resolved_parameters
                        )),
                    )
                    if isinstance(animation, StatefulAnimationBase):
                        raise TypeError("Stateful animations cannot be sparse overlays")
                    animation.start()
                    runtime = self._new_scene_component(
                        overlay_ref.component.plugin_id,
                        animation,
                        dict(overlay_ref.component.resolved_parameters),
                        started_at=time.perf_counter(),
                        ref=overlay_ref.component,
                    )
                else:
                    assert runtime is not None
                    runtime['animation'].update_parameters(
                        self._component_config(requested)
                    )
                assert runtime is not None
                runtime.update({
                    'config': dict(overlay_ref.component.resolved_parameters),
                    'ref': overlay_ref.component,
                    'enabled': overlay_ref.enabled,
                    'opacity': overlay_ref.opacity,
                    'strip_offset': overlay_ref.placement.strip_translation,
                    'led_offset': overlay_ref.placement.led_translation,
                    'stale_policy': overlay_ref.stale_policy,
                    'force_changed': True,
                })
                self._scene_overlay = runtime
                self._active_scene_state = candidate
                refreshed = self._refresh_receiver_hybrid_context("overlay_update")
                if replacing:
                    self._cleanup_scene_component(old_runtime)
                return refreshed
            except Exception as exc:
                failure = exc
                print(f"✗ Failed to update receiver hybrid {target}: {exc}")
                traceback.print_exc()
        assert failure is not None
        if isinstance(failure, (TypeError, ValueError)):
            return False
        return self._activate_known_python_fallback(scene, failure)

    def update_scene_component(
        self,
        target: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        component: Optional[Any] = None,
        enabled: Optional[bool] = None,
        opacity: Optional[int] = None,
        placement: Optional[Any] = None,
        stale_policy: Optional[Any] = None,
        remove: bool = False,
    ) -> bool:
        """Apply one live component edit without restarting the other component."""
        if isinstance(params, dict) and set(params) & {
            "params", "parameter_overrides", "component", "enabled", "opacity",
            "placement", "stale_policy", "remove",
        }:
            update = dict(params)
            params = update.get("params", update.get("parameter_overrides"))
            component = update.get("component", component)
            enabled = update.get("enabled", enabled)
            opacity = update.get("opacity", opacity)
            placement = update.get("placement", placement)
            stale_policy = update.get("stale_policy", stale_policy)
            remove = update.get("remove", remove)
        if not isinstance(remove, bool):
            raise TypeError("scene overlay remove state must be boolean")
        if target not in {"background", "overlay", AGGREGATE_OVERLAY_SLOT_ID}:
            raise ValueError("scene component target must be 'background' or 'overlay'")
        target = "overlay" if target == AGGREGATE_OVERLAY_SLOT_ID else target
        if remove and target != "overlay":
            raise ValueError("only the overlay component may be removed")
        if component is not None and target == "background":
            raise ValueError("replace a background by applying a complete scene")

        if self._receiver_hybrid_mode:
            return self._update_receiver_hybrid_component(
                target,
                params,
                component=component,
                enabled=enabled,
                opacity=opacity,
                placement=placement,
                stale_policy=stale_policy,
                remove=remove,
            )

        with self._scene_state_guard():
            if not self._scene_mode or self._active_scene_state is None:
                return False
            current_scene = self._active_scene_state
            runtime = (
                self._scene_background if target == "background" else self._scene_overlay
            )
            if remove:
                if runtime is None:
                    return False
                self._scene_overlay = None
                self._active_scene_state = SceneState(
                    current_scene.revision + 1,
                    current_scene.background,
                    (),
                    current_scene.known_python_fallback,
                )
                try:
                    self._cleanup_scene_component(runtime)
                except Exception as exc:
                    print(f"⚠️ Overlay cleanup failed: {exc}")
                return True

            if target == "overlay" and runtime is None and component is None:
                return False
            try:
                if target == "background":
                    assert runtime is not None
                    old_ref = current_scene.background
                    requested = self._scene_parameter_payload(params)
                    if not self._scene_allows_compatibility_components:
                        self.plugin_loader.validate_component_parameters(
                            old_ref.plugin_id,
                            {**dict(old_ref.resolved_parameters), **requested},
                        )
                    resolved = dict(old_ref.resolved_parameters)
                    resolved.update(requested)
                    overrides = dict(old_ref.parameter_overrides)
                    overrides.update(requested)
                    new_ref = ComponentRef(
                        plugin_id=old_ref.plugin_id,
                        provider=old_ref.provider,
                        preset_id=old_ref.preset_id,
                        preset_fingerprint=old_ref.preset_fingerprint,
                        parameter_overrides=overrides,
                        resolved_parameters=resolved,
                    )
                    runtime['animation'].update_parameters(
                        self._component_config(requested)
                    )
                    runtime['config'] = resolved
                    runtime['ref'] = new_ref
                    self._active_scene_state = SceneState(
                        current_scene.revision + 1,
                        new_ref,
                        current_scene.overlays,
                        current_scene.known_python_fallback,
                    )
                    if self.current_preset is not None:
                        self.current_preset['is_dirty'] = bool(overrides)
                    self._remember_active_state(
                        new_ref.plugin_id, resolved, self.current_preset
                    )
                    return True

                old_overlay = current_scene.overlays[0] if current_scene.overlays else None
                if component is None:
                    assert old_overlay is not None and runtime is not None
                    new_component = old_overlay.component
                else:
                    new_component = (
                        component if isinstance(component, ComponentRef)
                        else ComponentRef.from_payload(component)
                    )
                requested = self._scene_parameter_payload(params)
                if not self._scene_allows_compatibility_components:
                    self.plugin_loader.validate_component_parameters(
                        new_component.plugin_id,
                        {**dict(new_component.resolved_parameters), **requested},
                    )
                resolved = dict(new_component.resolved_parameters)
                resolved.update(requested)
                overrides = dict(new_component.parameter_overrides)
                overrides.update(requested)
                new_component = ComponentRef(
                    plugin_id=new_component.plugin_id,
                    provider=new_component.provider,
                    preset_id=new_component.preset_id,
                    preset_fingerprint=new_component.preset_fingerprint,
                    parameter_overrides=overrides,
                    resolved_parameters=resolved,
                )
                old_placement = (
                    old_overlay.placement if old_overlay else OverlayPlacement()
                )
                resolved_placement = (
                    placement if isinstance(placement, OverlayPlacement)
                    else OverlayPlacement.from_payload(placement)
                    if placement is not None else old_placement
                )
                old_stale = (
                    old_overlay.stale_policy
                    if old_overlay else StalePolicy(ForegroundStalePolicy.HOLD)
                )
                resolved_stale = (
                    stale_policy if isinstance(stale_policy, StalePolicy)
                    else StalePolicy.from_payload(stale_policy)
                    if stale_policy is not None else old_stale
                )
                overlay_ref = OverlayRef(
                    slot_id=AGGREGATE_OVERLAY_SLOT_ID,
                    component=new_component,
                    enabled=(old_overlay.enabled if enabled is None and old_overlay else True)
                    if enabled is None else enabled,
                    opacity=(old_overlay.opacity if opacity is None and old_overlay else 255)
                    if opacity is None else opacity,
                    placement=resolved_placement,
                    stale_policy=resolved_stale,
                )
                candidate = self._resolve_scene_state(
                    SceneState(
                        current_scene.revision + 1,
                        current_scene.background,
                        (overlay_ref,),
                        current_scene.known_python_fallback,
                    ),
                    allow_compatibility_components=(
                        self._scene_allows_compatibility_components
                    ),
                )
                overlay_ref = candidate.overlays[0]

                replacing = runtime is None or runtime['name'] != new_component.plugin_id
                if replacing:
                    animation_class = self.plugin_loader.get_plugin(
                        overlay_ref.component.plugin_id
                    )
                    animation = animation_class(
                        self.controller,
                        self._component_config(dict(
                            overlay_ref.component.resolved_parameters
                        )),
                    )
                    new_runtime = None
                    try:
                        animation.start()
                        new_runtime = self._new_scene_component(
                            overlay_ref.component.plugin_id,
                            animation,
                            dict(overlay_ref.component.resolved_parameters),
                            started_at=time.perf_counter(),
                            ref=overlay_ref.component,
                        )
                        new_runtime.update({
                            'enabled': overlay_ref.enabled,
                            'opacity': overlay_ref.opacity,
                            'strip_offset': overlay_ref.placement.strip_translation,
                            'led_offset': overlay_ref.placement.led_translation,
                            'stale_policy': overlay_ref.stale_policy,
                        })
                        with self._presentation_state_guard():
                            self._render_scene_component(
                                new_runtime,
                                now=new_runtime['started_at'],
                                resolved_vibe=self._resolved_vibe,
                                operator_tempo=self.animation_speed_scale,
                                overlay=True,
                            )
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
                    runtime['animation'].update_parameters(
                        self._component_config(requested)
                    )
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
                print(f"✗ Failed to update scene {target}: {exc}")
                return False

    def apply_scene(self, scene_payload: Any) -> bool:
        """Reconcile a scene, retaining matching live component instances."""
        try:
            scene = self._resolve_scene_state(scene_payload)
        except (TypeError, ValueError) as exc:
            print(f"✗ Invalid scene: {exc}")
            return False
        with self._scene_state_guard():
            current = self._active_scene_state
            active = self._scene_mode and current is not None
        if not active or current.background.plugin_id != scene.background.plugin_id:
            return self.start_scene(scene)
        if not self.update_scene_component(
            "background", params=dict(scene.background.resolved_parameters)
        ):
            return False
        old_overlay = current.overlays[0] if current.overlays else None
        new_overlay = scene.overlays[0] if scene.overlays else None
        if new_overlay is None:
            if old_overlay is not None and not self.remove_overlay():
                return False
        else:
            if not self.update_scene_component(
                "overlay",
                params=dict(new_overlay.component.resolved_parameters),
                component=(
                    new_overlay.component
                    if old_overlay is None
                    or old_overlay.component.plugin_id != new_overlay.component.plugin_id
                    else None
                ),
                enabled=new_overlay.enabled,
                opacity=new_overlay.opacity,
                placement=new_overlay.placement,
                stale_policy=new_overlay.stale_policy,
            ):
                return False
        with self._scene_state_guard():
            self._active_scene_state = scene
            if self._scene_background is not None:
                self._scene_background['ref'] = scene.background
            if self._scene_overlay is not None and scene.overlays:
                self._scene_overlay['ref'] = scene.overlays[0].component
        return True

    @staticmethod
    def _normalize_current_preset(
        preset: Optional[Dict[str, Any]], animation_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return the small, safe preset selection shape exposed in status."""
        if not isinstance(preset, dict):
            return None
        preset_id = preset.get('preset_id')
        name = preset.get('name')
        preset_animation = preset.get('animation', animation_name)
        if not all(isinstance(value, str) and value for value in (
            preset_id, name, preset_animation
        )):
            return None
        if preset_animation != animation_name:
            return None
        return {
            'preset_id': preset_id,
            'name': name,
            'animation': preset_animation,
            'is_dirty': bool(preset.get('is_dirty', False)),
        }

    def set_current_preset(self, preset: Dict[str, Any]) -> bool:
        """Mark the running animation as the saved preset without restarting it."""
        if not self.is_running or not self.current_animation_name:
            return False
        selection = self._normalize_current_preset(
            preset, self.current_animation_name
        )
        if selection is None:
            return False
        selection['is_dirty'] = False
        self.current_preset = selection
        self._sync_last_active_preset()
        return True

    @staticmethod
    def _clamp_channel(value: Any) -> int:
        """Clamp an arbitrary value to an RGB channel byte."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(255, parsed))

    def _push_frame_to_controller(self, frame: List[Any]):
        """Send a full frame immediately to the underlying controller."""
        self.controller.set_all_pixels(frame)
        inline_show = getattr(self.controller, "inline_show", False)
        if not inline_show and hasattr(self.controller, "show"):
            try:
                self.controller.show()
            except Exception:
                pass

    def _ensure_painter_frame_length(self):
        """Resize painter buffer to match active controller geometry."""
        total_pixels = self.controller.total_leds
        with self.painter_lock:
            frame = list(self.painter_frame_data)
            if len(frame) < total_pixels:
                frame.extend([(0, 0, 0)] * (total_pixels - len(frame)))
            elif len(frame) > total_pixels:
                frame = frame[:total_pixels]
            self.painter_frame_data = frame

    def _parse_painter_update(self, update: Any) -> Optional[tuple]:
        """Parse supported painter update payload formats."""
        index: Optional[int] = None
        color_values: Optional[List[Any]] = None

        if isinstance(update, dict):
            if 'index' in update:
                try:
                    index = int(update.get('index'))
                except (TypeError, ValueError):
                    index = None
            elif 'strip' in update and 'led' in update:
                try:
                    strip = int(update.get('strip'))
                    led = int(update.get('led'))
                    index = strip * self.controller.leds_per_strip + led
                except (TypeError, ValueError):
                    index = None

            if isinstance(update.get('color'), (list, tuple)) and len(update['color']) >= 3:
                color_values = list(update['color'][:3])
            elif {'r', 'g', 'b'}.issubset(update.keys()):
                color_values = [update.get('r'), update.get('g'), update.get('b')]
        elif isinstance(update, (list, tuple)) and len(update) >= 4:
            try:
                index = int(update[0])
            except (TypeError, ValueError):
                index = None
            color_values = [update[1], update[2], update[3]]

        if index is None or color_values is None:
            return None

        return (
            index,
            (
                self._clamp_channel(color_values[0]),
                self._clamp_channel(color_values[1]),
                self._clamp_channel(color_values[2]),
            )
        )

    def set_painter_frame(self, frame_data: Optional[List[Any]]) -> bool:
        """Replace the full painter frame and display it immediately."""
        if self.is_running:
            self.stop_animation(clear_leds=False)

        frame = self._normalize_frame(frame_data)
        with self.painter_lock:
            self.painter_frame_data = list(frame)
            self.painter_active = True
            self.painter_updated_at = time.time()
        with self.frame_data_lock:
            self.current_frame_data = list(frame)

        self._push_frame_to_controller(frame)
        return True

    def apply_painter_updates(self, updates: List[Any]) -> bool:
        """Apply sparse painter pixel updates and display the result immediately."""
        if not isinstance(updates, list) or not updates:
            return False

        if self.is_running:
            self.stop_animation(clear_leds=False)

        self._ensure_painter_frame_length()
        total_pixels = self.controller.total_leds
        changed = False

        with self.painter_lock:
            frame = list(self.painter_frame_data)
            for update in updates:
                parsed = self._parse_painter_update(update)
                if not parsed:
                    continue
                index, color = parsed
                if index < 0 or index >= total_pixels:
                    continue
                if frame[index] != color:
                    frame[index] = color
                    changed = True

            if not changed:
                return False

            self.painter_frame_data = frame
            self.painter_active = True
            self.painter_updated_at = time.time()

        with self.frame_data_lock:
            self.current_frame_data = list(frame)

        self._push_frame_to_controller(frame)
        return True

    def clear_painter_frame(self) -> bool:
        """Clear all painter pixels to black and display the cleared frame."""
        black_frame = [(0, 0, 0)] * self.controller.total_leds
        return self.set_painter_frame(black_frame)
    
    def get_current_status(self) -> Dict[str, Any]:
        """Get current animation status and performance info"""
        mode = (
            'animation' if self.is_running and self._scene_compatibility_mode
            else 'scene' if self.is_running and self._scene_mode
            else 'animation' if self.is_running
            else 'painter' if self.painter_active
            else 'idle'
        )
        displayed_animation = self.current_animation_name if self.is_running else (
            'frame_painter' if self.painter_active else None
        )

        status = {
            'is_running': self.is_running,
            'mode': mode,
            'painter_active': self.painter_active,
            'painter_updated_at': self.painter_updated_at if self.painter_active else None,
            'current_animation': displayed_animation,
            'current_preset': dict(self.current_preset) if self.current_preset else None,
            'brightness': self.output_brightness,
            'frame_count': self.frame_count,
            'frames_presented': self.frames_presented,
            'unchanged_frames_skipped': self.unchanged_frames_skipped,
            'uptime': (time.perf_counter() - self.start_time) if self.is_running else 0,
            'target_fps': self.target_fps,
            'animation_speed_scale': self.animation_speed_scale,
            'feature_flags': self.feature_flags.to_dict(),
            'vibe': self.get_vibe_status(),
            'plant_aware': self.plant_aware,
            'plant_modifiers': self.plant_modifier_state.to_dict(),
            'actual_fps': self._calculate_fps(),
            'animation_hash': self.current_animation_hash,
            'led_info': {
                'total_leds': self.controller.total_leds,
                'strip_count': self.controller.strip_count,
                'leds_per_strip': self.controller.leds_per_strip
            }
        }
        
        status['animation_info'] = None
        status['animation_stats'] = {}
        if self.current_animation:
            status['animation_info'] = self.current_animation.get_info()
            status['interaction_types'] = status['animation_info'].get(
                'interaction_types', []
            )
            status['plant_modifier_support'] = status['animation_info'].get(
                'plant_modifier_support', []
            )
            status['unsupported_plant_modifiers'] = status['animation_info'].get(
                'unsupported_plant_modifiers', []
            )
            try:
                stats = self.current_animation.get_runtime_stats()
                if isinstance(stats, dict):
                    status['animation_stats'] = stats
            except Exception as exc:
                status['animation_stats'] = {'error': str(exc)}
        else:
            status['interaction_types'] = []
            if self._receiver_hybrid_mode and self._scene_background:
                descriptor = receiver_static_component_descriptor(self.feature_flags)
                if descriptor is not None:
                    status['animation_info'] = {
                        **descriptor,
                        'current_params': dict(self._scene_background['config']),
                    }

        scene_status = self._scene_status_snapshot()
        if scene_status is not None and not self._scene_compatibility_mode:
            status['scene'] = scene_status
        if self._scene_mode:
            status['scene_state'] = self.get_scene_state()
        receiver_status = self._receiver_hybrid_status_snapshot()
        if receiver_status is not None:
            status['receiver_hybrid'] = receiver_status

        performance = self._get_perf_summary()
        if performance:
            status['performance'] = performance

        driver_stats = {}
        if hasattr(self.controller, "get_stats"):
            try:
                driver_stats = self.controller.get_stats()
            except Exception as exc:
                driver_stats = {'error': str(exc)}
        status['driver_stats'] = driver_stats
        status['pipeline_fps'] = self._compute_driver_fps(driver_stats)
        
        return status

    def _receiver_hybrid_status_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self._receiver_hybrid_mode:
            return (
                dict(self._receiver_last_status)
                if self._receiver_last_status is not None else None
            )
        publisher = self._receiver_sparse_publisher
        publisher_status = publisher.get_status() if publisher is not None else {}
        driver_status: Dict[str, Any] = {}
        getter = getattr(self.controller, "get_stats", None)
        if callable(getter):
            try:
                stats = getter()
                aggregate = stats.get("aggregate", {}) if isinstance(stats, dict) else {}
                candidate = aggregate.get("local_background", {})
                if isinstance(candidate, dict):
                    driver_status = dict(candidate)
            except Exception as exc:
                driver_status = {"state": "degraded", "error": str(exc)}
        transport_operational = bool(
            publisher_status.get("healthy")
            and driver_status.get("state") == "active"
            and self._receiver_hybrid_error is None
            and not self._receiver_fallback_active
        )
        operational = bool(
            transport_operational
            and driver_status.get("operational", True)
        )
        telemetry_complete = bool(
            driver_status.get("telemetry_complete", True)
        )
        degraded = bool(
            driver_status.get("degraded", False) or not telemetry_complete
        )
        healthy = bool(operational and telemetry_complete and not degraded)
        release_acceptance = bool(
            healthy and driver_status.get("release_acceptance", True)
        )
        scene = self._active_scene_state
        return {
            "healthy": healthy,
            "operational": operational,
            "degraded": degraded,
            "telemetry_complete": telemetry_complete,
            "release_acceptance": release_acceptance,
            "transport_policy": driver_status.get(
                "transport_policy", "strict_all_readable_v1"
            ),
            "readable_devices": list(
                driver_status.get("readable_devices", ())
            ),
            "unverified_devices": list(
                driver_status.get("unverified_devices", ())
            ),
            "fallback_active": self._receiver_fallback_active,
            "error": self._receiver_hybrid_error,
            "source_scene_revision": scene.revision if scene is not None else None,
            "context_revision": self._receiver_context_revision,
            "context_digest": (
                self._receiver_context.context_digest.hex()
                if self._receiver_context is not None else None
            ),
            "publisher": publisher_status,
            "driver": driver_status,
        }

    def _scene_status_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self._scene_mode or not self._scene_background:
            return None
        with self._scene_lock:
            background = self._scene_background
            overlay = self._scene_overlay
            snapshot: Dict[str, Any] = {
                'provider_mode': (
                    'receiver_hybrid' if self._receiver_hybrid_mode else 'python_host'
                ),
                'background': {
                    'name': background['name'],
                    'frame_count': background['frame_index'],
                    'calls': background['calls'],
                    'changed_calls': background['changed_calls'],
                    'interaction_types': sorted(
                        background['animation'].INTERACTION_TYPES
                    ) if background.get('animation') is not None else [],
                    'component': (
                        background['ref'].to_dict() if background.get('ref') else None
                    ),
                    'preset': (
                        self._component_preset_status(background['ref'])
                        if background.get('ref') else None
                    ),
                },
                'overlay': None if overlay is None else {
                    'name': overlay['name'],
                    'enabled': overlay['enabled'],
                    'opacity': overlay['opacity'],
                    'strip_offset': overlay['strip_offset'],
                    'led_offset': overlay['led_offset'],
                    'frame_count': overlay['frame_index'],
                    'calls': overlay['calls'],
                    'changed_calls': overlay['changed_calls'],
                    'render_count': overlay['render_count'],
                    'interaction_types': sorted(
                        overlay['animation'].INTERACTION_TYPES
                    ),
                    'component': (
                        overlay['ref'].to_dict() if overlay.get('ref') else None
                    ),
                    'preset': (
                        self._component_preset_status(overlay['ref'])
                        if overlay.get('ref') else None
                    ),
                },
            }
            if self._receiver_hybrid_mode:
                snapshot['receiver'] = self._receiver_hybrid_status_snapshot()
            if overlay is not None:
                try:
                    overlay_stats = overlay['animation'].get_runtime_stats()
                except Exception as exc:
                    overlay_stats = {'error': str(exc)}
                snapshot['overlay']['runtime_stats'] = (
                    overlay_stats if isinstance(overlay_stats, dict) else {}
                )
            return snapshot

    def trigger_random_hole(self):
        """Request the current animation to spawn a random puncture if supported."""
        if not self.current_animation:
            return False
        if hasattr(self.current_animation, 'trigger_random_hole'):
            try:
                self.current_animation.trigger_random_hole()
                return True
            except Exception as exc:
                print(f"⚠️ Failed to trigger hole: {exc}")
        return False

    def trigger_hole(self, x: float, y: float, radius: Optional[float] = None):
        """Request a puncture at an exact animation-grid coordinate."""
        if not self.current_animation or not hasattr(self.current_animation, 'trigger_hole'):
            return False
        try:
            return bool(self.current_animation.trigger_hole(x, y, radius))
        except Exception as exc:
            print(f"⚠️ Failed to trigger positioned hole: {exc}")
            return False

    @staticmethod
    def _validated_interaction(
        animation: AnimationBase,
        kind: str,
        x: float,
        y: float,
        strength: float,
    ) -> tuple[str, float, float, float]:
        kind = str(kind or 'primary')
        if kind not in animation.INTERACTION_TYPES:
            raise ValueError(f"interaction {kind!r} is not supported")
        values = (float(x), float(y), float(strength))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("interaction coordinates and strength must be finite")
        x_value, y_value, strength_value = values
        width, height = animation.get_strip_info()
        if not 0.0 <= x_value < width or not 0.0 <= y_value < height:
            raise ValueError("interaction coordinates are outside the animation grid")
        if not 0.0 <= strength_value <= 1.0:
            raise ValueError("interaction strength must be between 0 and 1")
        return kind, x_value, y_value, strength_value

    def dispatch_interaction(
        self,
        kind: str,
        x: float,
        y: float,
        strength: float = 1.0,
        *,
        target: str = 'background',
    ) -> bool:
        """Dispatch to one explicit scene component; legacy defaults to background."""
        if target not in {'background', 'overlay'}:
            raise ValueError("interaction target must be 'background' or 'overlay'")
        if self._scene_mode:
            with self._scene_lock:
                component = (
                    self._scene_background if target == 'background'
                    else self._scene_overlay
                )
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
            print(f"⚠️ Failed to hash animation file {path}: {exc}")
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
        mode = (
            'animation' if self.is_running and self._scene_compatibility_mode
            else 'scene' if self.is_running and self._scene_mode
            else 'animation' if self.is_running
            else 'painter' if self.painter_active
            else 'idle'
        )
        displayed_animation = self.current_animation_name if self.is_running else (
            'frame_painter' if self.painter_active else None
        )

        return {
            'frame_data_encoded': encoded_frame,
            'frame_data_length': len(frame_data),
            'frame_encoding': FRAME_ENCODING_NAME if encoded_frame else None,
            'mode': mode,
            'painter_active': self.painter_active,
            'led_info': {
                'total_leds': self.controller.total_leds,
                'strip_count': self.controller.strip_count,
                'leds_per_strip': self.controller.leds_per_strip
            },
            'is_running': self.is_running,
            'frame_count': self.frame_count,
            'current_animation': displayed_animation,
            'scene': (
                None if self._scene_compatibility_mode
                else self._scene_status_snapshot()
            ),
            'timestamp': time.time()
        }

    def _preview_config(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        effective = dict(params or {})
        effective['plant_aware'] = (
            self.plant_aware if self._legacy_plant_aware_bridge else False
        )
        effective['plant_modifiers'] = self.plant_modifier_state.to_dict()
        return effective

    def _preview_session_for(
        self, animation_name: str, params: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if animation_name not in self.plugin_loader.loaded_plugins:
            raise ValueError(f"Animation '{animation_name}' not found")
        if self._plugin_role(animation_name) == 'overlay':
            raise ValueError(
                f"Overlay component {animation_name!r} requires get_scene_preview()"
            )
        self.preview_controller.strip_count = self.controller.strip_count
        self.preview_controller.leds_per_strip = self.controller.leds_per_strip
        self.preview_controller.total_leds = self.controller.total_leds
        authored = dict(params or {})
        fingerprint = hashlib.sha256(json.dumps(
            authored, sort_keys=True, separators=(',', ':'), default=str
        ).encode()).hexdigest()
        geometry = (
            self.preview_controller.strip_count,
            self.preview_controller.leds_per_strip,
        )
        now = time.monotonic()
        session = self._preview_session
        expired = bool(
            session and now - float(session['last_access']) > self._preview_session_ttl
        )
        if (
            session is None
            or expired
            or session['animation_name'] != animation_name
            or session['fingerprint'] != fingerprint
            or session['geometry'] != geometry
        ):
            animation_class = self.plugin_loader.loaded_plugins[animation_name]
            animation = animation_class(
                self.preview_controller, self._preview_config(authored)
            )
            session = {
                'animation_name': animation_name,
                'fingerprint': fingerprint,
                'geometry': geometry,
                'animation': animation,
                'started_at': now,
                'last_access': now,
                'frame_count': 0,
                'last_unscaled_elapsed': 0.0,
                'scaled_elapsed': 0.0,
                'presentation_state': self._empty_presentation_state(),
            }
            self._preview_session = session
        session['last_access'] = now
        return session

    def _render_preview(
        self,
        animation_name: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        vibe: Optional[Any] = None,
    ) -> Dict[str, Any]:
        with self._preview_lock:
            session = self._preview_session_for(animation_name, params)
            animation = session['animation']
            elapsed = max(0.0, time.monotonic() - session['started_at'])
            with self._presentation_state_guard():
                resolved = (
                    self._resolved_vibe if vibe is None else self._canonical_vibe(vibe)
                )
                operator_tempo = self.animation_speed_scale
            delta = max(0.0, elapsed - float(session['last_unscaled_elapsed']))
            authored_speed = self._animation_authored_speed(animation)
            vibe_tempo = self._component_tempo(resolved.profile, animation)
            session['scaled_elapsed'] += (
                delta * authored_speed * vibe_tempo * operator_tempo
            )
            session['last_unscaled_elapsed'] = elapsed
            context = self._runtime_context(
                animation,
                unscaled_elapsed=elapsed,
                scaled_elapsed=session['scaled_elapsed'],
                frame_index=session['frame_count'],
                resolved_vibe=resolved,
                operator_tempo_scale=operator_tempo,
            )
            rendered = animation.generate_frame_with_context(context)
            changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
            frame_data = self._normalize_frame(rendered)
            frame_data = animation.apply_framework_plant_modifiers(
                frame_data, changed=changed
            )
            frame_data, changed = self._apply_vibe_presentation(
                animation,
                frame_data,
                profile=resolved.profile,
                changed=changed,
                state=session['presentation_state'],
            )
            if isinstance(frame_data, np.ndarray):
                frame_data = frame_data.tolist()
            session['frame_count'] += 1
            return {
                'frame_data': frame_data,
                'led_info': {
                    'total_leds': self.controller.total_leds,
                    'strip_count': self.controller.strip_count,
                    'leds_per_strip': self.controller.leds_per_strip,
                },
                'is_running': False,
                'frame_count': session['frame_count'],
                'current_animation': animation_name,
                'interaction_types': sorted(animation.INTERACTION_TYPES),
                'timestamp': time.time(),
                'preview': True,
                'params': dict(params or {}),
                'changed': changed,
                'vibe': {
                    'state': resolved.state.to_dict(),
                    'profile': resolved.profile.to_dict(),
                },
            }

    def get_animation_preview(
        self, animation_name: str, *, vibe: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Advance and return the process-local dashboard preview session."""
        return self._render_preview(animation_name, vibe=vibe)

    def get_animation_preview_with_params(
        self,
        animation_name: str,
        params: Dict[str, Any],
        *,
        vibe: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Advance a preview, resetting only when authored parameters change."""
        return self._render_preview(animation_name, params, vibe=vibe)

    def _get_receiver_scene_preview(
        self,
        scene: SceneState,
        *,
        vibe: Optional[Any],
        plant_modifiers: Optional[Any],
        elapsed: float,
    ) -> Dict[str, Any]:
        preview_plant_state = (
            self.plant_modifier_state
            if plant_modifiers is None
            else PlantModifierState.from_payload(plant_modifiers)
        )
        with self._presentation_state_guard():
            resolved = (
                self._resolved_vibe if vibe is None else self._canonical_vibe(vibe)
            )
            operator_tempo = self.animation_speed_scale
        overlay_ref = scene.overlays[0] if scene.overlays else None
        overlay_component = None
        try:
            placed = ()
            if overlay_ref is not None:
                overlay_class = self.plugin_loader.get_plugin(
                    overlay_ref.component.plugin_id
                )
                if overlay_class is None:
                    raise ValueError("receiver preview overlay implementation is missing")
                config = dict(overlay_ref.component.resolved_parameters)
                config.update({
                    "plant_aware": False,
                    "plant_modifiers": preview_plant_state.to_dict(),
                })
                overlay = overlay_class(self.preview_controller, config)
                if isinstance(overlay, StatefulAnimationBase):
                    raise TypeError("Stateful animations cannot be scene previews")
                overlay.start()
                overlay_component = self._new_scene_component(
                    overlay_ref.component.plugin_id,
                    overlay,
                    dict(overlay_ref.component.resolved_parameters),
                    started_at=0.0,
                    ref=overlay_ref.component,
                )
                if overlay_ref.enabled:
                    source = self._render_scene_component(
                        overlay_component,
                        now=float(elapsed),
                        resolved_vibe=resolved,
                        operator_tempo=operator_tempo,
                        overlay=True,
                        plant_modifiers=preview_plant_state.to_dict(),
                    )
                    placed = (PlacedOverlay(
                        source,
                        opacity=overlay_ref.opacity,
                        strip_offset=overlay_ref.placement.strip_translation,
                        led_offset=overlay_ref.placement.led_translation,
                        enabled=True,
                    ),)
            compositor = HostForegroundCompositor(
                self.preview_controller.strip_count,
                self.preview_controller.leds_per_strip,
            )
            foreground = compositor.compose(placed)
            luminance = quantize_q8_8(
                resolved.profile.luminance_scale,
                name="luminance_scale",
                maximum=Q8_8_ONE,
            )
            foreground = self._apply_receiver_luminance_rgba(
                foreground,
                luminance_q8_8=luminance,
                state=self._empty_presentation_state(),
                force_refresh=True,
            )
            scene_time_us = int(float(elapsed) * 1_000_000.0)
            frame = self._receiver_preview_frame(
                scene,
                foreground,
                scene_time_us=scene_time_us,
                resolved_vibe=resolved,
            )
            return {
                'frame_data': frame.tolist(),
                'led_info': {
                    'total_leds': self.preview_controller.total_leds,
                    'strip_count': self.preview_controller.strip_count,
                    'leds_per_strip': self.preview_controller.leds_per_strip,
                },
                'is_running': False,
                'mode': 'scene',
                'current_animation': scene.background.plugin_id,
                'background_provider': ComponentProvider.RECEIVER_NATIVE.value,
                'scene': {
                    'background': scene.background.plugin_id,
                    'background_provider': ComponentProvider.RECEIVER_NATIVE.value,
                    'overlay': (
                        overlay_ref.component.plugin_id if overlay_ref else None
                    ),
                    'provider_mode': 'receiver_hybrid',
                },
                'frame_count': 1,
                'changed': True,
                'dirty_ranges': None,
                'preview': True,
                'preview_label': (
                    'Host-rendered preview — receiver framebuffer is not available'
                ),
                'preview_source': 'host_contract_renderer',
                'framebuffer_readback': False,
                'live_state_mutated': False,
                'timestamp': time.time(),
                'vibe': {
                    'state': resolved.state.to_dict(),
                    'profile': resolved.profile.to_dict(),
                },
            }
        finally:
            self._cleanup_scene_component(overlay_component)

    def get_scene_preview(
        self,
        scene_payload: Any,
        background_config: Optional[Dict[str, Any]] = None,
        overlay_name: str = 'clock_overlay',
        overlay_config: Optional[Dict[str, Any]] = None,
        overlay_opacity: int = 255,
        strip_offset: int = 0,
        led_offset: int = 0,
        *,
        vibe: Optional[Any] = None,
        plant_modifiers: Optional[Any] = None,
        elapsed: float = 0.0,
        elapsed_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Render an isolated scene through the same resolver as live starts."""
        if elapsed_seconds is not None:
            elapsed = elapsed_seconds
        if not math.isfinite(float(elapsed)) or float(elapsed) < 0.0:
            raise ValueError("preview elapsed time must be finite and non-negative")
        structured = isinstance(scene_payload, (SceneState, dict))
        scene = None
        if structured:
            scene = self._resolve_scene_state(scene_payload)
            background_name = scene.background.plugin_id
            background_config = dict(scene.background.resolved_parameters)
            overlay_ref = scene.overlays[0] if scene.overlays else None
            overlay_name = (
                overlay_ref.component.plugin_id if overlay_ref is not None else None
            )
            overlay_config = (
                dict(overlay_ref.component.resolved_parameters)
                if overlay_ref is not None else None
            )
            if overlay_ref is not None:
                overlay_opacity = overlay_ref.opacity
                strip_offset = overlay_ref.placement.strip_translation
                led_offset = overlay_ref.placement.led_translation
            if scene.background.provider is ComponentProvider.RECEIVER_NATIVE:
                return self._get_receiver_scene_preview(
                    scene,
                    vibe=vibe,
                    plant_modifiers=plant_modifiers,
                    elapsed=float(elapsed),
                )
        else:
            background_name = scene_payload
            overlay_ref = None
        background_class = self.plugin_loader.get_plugin(background_name)
        overlay_class = (
            self.plugin_loader.get_plugin(overlay_name) if overlay_name else None
        )
        if background_class is None or (
            not structured and self._plugin_role(background_name) != 'background'
        ):
            raise ValueError(f"invalid scene background {background_name!r}")
        if overlay_name and (
            overlay_class is None
            or (not structured and self._plugin_role(overlay_name) != 'overlay')
        ):
            raise ValueError(f"invalid scene overlay {overlay_name!r}")
        if issubclass(background_class, StatefulAnimationBase) or (
            overlay_class is not None
            and issubclass(overlay_class, StatefulAnimationBase)
        ):
            raise TypeError("Stateful animations cannot participate in scene previews")

        preview_plant_state = (
            self.plant_modifier_state
            if plant_modifiers is None
            else PlantModifierState.from_payload(plant_modifiers)
        )

        def preview_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            effective = dict(config or {})
            effective['plant_aware'] = (
                self.plant_aware
                if plant_modifiers is None and self._legacy_plant_aware_bridge
                else False
            )
            effective['plant_modifiers'] = preview_plant_state.to_dict()
            return effective

        background = background_class(
            self.preview_controller, preview_config(background_config)
        )
        overlay_animation = (
            overlay_class(self.preview_controller, preview_config(overlay_config))
            if overlay_class is not None else None
        )
        components: List[Dict[str, Any]] = []
        try:
            background.start()
            background_component = self._new_scene_component(
                background_name,
                background,
                background_config,
                started_at=0.0,
                ref=scene.background if scene else None,
            )
            components.append(background_component)
            overlay_component = None
            if overlay_animation is not None:
                overlay_animation.start()
                overlay_component = self._new_scene_component(
                    overlay_name,
                    overlay_animation,
                    overlay_config,
                    started_at=0.0,
                    ref=overlay_ref.component if overlay_ref else None,
                )
                overlay_component.update({
                    'enabled': overlay_ref.enabled if overlay_ref else True,
                    'opacity': overlay_opacity,
                    'strip_offset': strip_offset,
                    'led_offset': led_offset,
                    'stale_policy': (
                        overlay_ref.stale_policy if overlay_ref
                        else StalePolicy(ForegroundStalePolicy.HOLD)
                    ),
                })
                components.append(overlay_component)
            with self._presentation_state_guard():
                resolved = (
                    self._resolved_vibe if vibe is None else self._canonical_vibe(vibe)
                )
                operator_tempo = self.animation_speed_scale
            frame, changed, dirty_ranges = self._compose_scene_components(
                background_component,
                overlay_component,
                HostSceneCompositor(
                    self.preview_controller.strip_count,
                    self.preview_controller.leds_per_strip,
                ),
                self._empty_presentation_state(),
                now=float(elapsed),
                resolved_vibe=resolved,
                operator_tempo=operator_tempo,
                plant_modifiers=preview_plant_state.to_dict(),
            )
            return {
                'frame_data': frame.tolist(),
                'led_info': {
                    'total_leds': self.preview_controller.total_leds,
                    'strip_count': self.preview_controller.strip_count,
                    'leds_per_strip': self.preview_controller.leds_per_strip,
                },
                'is_running': False,
                'mode': 'scene',
                'current_animation': background_name,
                'scene': {
                    'background': background_name,
                    'overlay': overlay_name,
                    'overlay_opacity': overlay_opacity,
                    'strip_offset': strip_offset,
                    'led_offset': led_offset,
                },
                'frame_count': 1,
                'changed': changed,
                'dirty_ranges': dirty_ranges,
                'preview': True,
                'timestamp': time.time(),
                'vibe': {
                    'state': resolved.state.to_dict(),
                    'profile': resolved.profile.to_dict(),
                },
            }
        finally:
            for component in reversed(components):
                self._cleanup_scene_component(component)

    def dispatch_preview_interaction(
        self,
        animation_name: str,
        kind: str,
        x: float,
        y: float,
        strength: float = 1.0,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Apply an interaction to the isolated dashboard preview session."""
        with self._preview_lock:
            session = self._preview_session_for(animation_name, params)
            animation = session['animation']
            event = self._validated_interaction(animation, kind, x, y, strength)
            return bool(animation.handle_interaction(*event))

    def _render_scene_component(
        self,
        component: Dict[str, Any],
        *,
        now: float,
        resolved_vibe: ResolvedVibe,
        operator_tempo: float,
        overlay: bool,
        plant_modifiers: Optional[Dict[str, Any]] = None,
    ) -> BaseFrame | OverlayFrame:
        animation = component['animation']
        elapsed = max(0.0, float(now) - float(component['started_at']))
        delta = max(0.0, elapsed - component['last_unscaled_elapsed'])
        authored_speed = self._animation_authored_speed(animation)
        vibe_tempo = self._component_tempo(resolved_vibe.profile, animation)
        component['scaled_elapsed'] += (
            delta * authored_speed * vibe_tempo * operator_tempo
        )
        component['last_unscaled_elapsed'] = elapsed
        context = self._runtime_context(
            animation,
            unscaled_elapsed=elapsed,
            scaled_elapsed=component['scaled_elapsed'],
            frame_index=component['frame_index'],
            resolved_vibe=resolved_vibe,
            operator_tempo_scale=operator_tempo,
            plant_modifiers=plant_modifiers,
        )
        rendered = animation.generate_frame_with_context(context)
        component['calls'] += 1
        component['frame_index'] += 1

        force_changed = bool(component.pop('force_changed', False))
        if overlay:
            if not isinstance(rendered, OverlayFrame):
                raise TypeError(
                    f"overlay {component['name']} returned "
                    f"{type(rendered).__name__}; expected OverlayFrame"
                )
            if rendered.pixels.shape[0] != self.controller.total_leds:
                raise ValueError(
                    f"overlay {component['name']} returned {rendered.pixels.shape[0]} "
                    f"pixels; expected {self.controller.total_leds}"
                )
            source_changed = rendered.changed or force_changed
            if rendered.revision != component['last_revision']:
                component['render_count'] += 1
                component['last_revision'] = rendered.revision
            frame: BaseFrame | OverlayFrame = OverlayFrame(
                rendered.pixels,
                revision=rendered.revision,
                changed=source_changed,
                dirty_ranges=None if force_changed else rendered.dirty_ranges,
            )
        else:
            if isinstance(rendered, OverlayFrame):
                raise TypeError(
                    f"background {component['name']} returned OverlayFrame"
                )
            if isinstance(rendered, BaseFrame):
                frame = rendered
            else:
                changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
                dirty_ranges = (
                    rendered.dirty_ranges if isinstance(rendered, RenderedFrame) else None
                )
                pixels = self._normalize_frame(rendered)
                pixels = np.asarray(pixels, dtype=np.uint8)
                if pixels.shape != (self.controller.total_leds, 3):
                    raise ValueError(
                        f"background {component['name']} returned shape {pixels.shape}"
                    )
                if not pixels.flags.c_contiguous:
                    pixels = np.ascontiguousarray(pixels)
                frame = BaseFrame(
                    pixels, changed=changed, dirty_ranges=dirty_ranges
                )
            if frame.pixels.shape[0] != self.controller.total_leds:
                raise ValueError(
                    f"background {component['name']} returned {frame.pixels.shape[0]} "
                    f"pixels; expected {self.controller.total_leds}"
                )
            source_changed = frame.changed or force_changed
            if force_changed:
                frame = BaseFrame(frame.pixels, changed=True, dirty_ranges=None)

        graded, graded_changed = self._apply_vibe_presentation(
            animation,
            frame.pixels,
            profile=resolved_vibe.profile,
            changed=source_changed,
            state=component['grade_state'],
            include_grade=True,
            include_luminance=False,
        )
        dirty_ranges = frame.dirty_ranges
        if graded_changed and not source_changed:
            dirty_ranges = None
        if overlay:
            result: BaseFrame | OverlayFrame = OverlayFrame(
                graded,
                revision=frame.revision,
                changed=graded_changed,
                dirty_ranges=dirty_ranges,
            )
        else:
            result = BaseFrame(
                graded, changed=graded_changed, dirty_ranges=dirty_ranges
            )
        component['cached_frame'] = result
        component['changed_calls'] += int(result.changed)
        component['last_dirty_ranges'] = result.dirty_ranges
        return result

    @staticmethod
    def _cached_overlay_frame(frame: OverlayFrame) -> OverlayFrame:
        return OverlayFrame(
            frame.pixels,
            revision=frame.revision,
            changed=False,
            dirty_ranges=(),
        )

    def _compose_scene_components(
        self,
        background: Dict[str, Any],
        overlay: Optional[Dict[str, Any]],
        compositor: HostSceneCompositor,
        final_presentation_state: Dict[str, Any],
        *,
        now: float,
        resolved_vibe: ResolvedVibe,
        operator_tempo: float,
        force_refresh: bool = False,
        plant_modifiers: Optional[Dict[str, Any]] = None,
    ) -> tuple[np.ndarray, bool, Optional[tuple[tuple[int, int], ...]]]:
        base = self._render_scene_component(
            background,
            now=now,
            resolved_vibe=resolved_vibe,
            operator_tempo=operator_tempo,
            overlay=False,
            plant_modifiers=plant_modifiers,
        )
        placed = ()
        if overlay is not None:
            if overlay['enabled']:
                overlay_frame = self._render_scene_component(
                    overlay,
                    now=now,
                    resolved_vibe=resolved_vibe,
                    operator_tempo=operator_tempo,
                    overlay=True,
                    plant_modifiers=plant_modifiers,
                )
            else:
                cached = overlay.get('cached_frame')
                if not isinstance(cached, OverlayFrame):
                    raise RuntimeError("overlay was disabled before its initial frame")
                overlay_frame = self._cached_overlay_frame(cached)
            placed = (PlacedOverlay(
                overlay_frame,
                opacity=overlay['opacity'],
                strip_offset=overlay['strip_offset'],
                led_offset=overlay['led_offset'],
                enabled=overlay['enabled'],
            ),)

        composed = compositor.compose(base, placed)
        changed = composed.changed
        dirty_ranges = composed.dirty_ranges
        pixels = composed.pixels

        animation = background['animation']
        framework_refresh = animation.framework_plant_modifier_refresh_pending()
        pixels = animation.apply_framework_plant_modifiers(
            pixels, changed=changed
        )
        if animation.framework_plant_modifiers_active():
            changed = changed or framework_refresh
            dirty_ranges = None

        luminance_component = next((
            component['animation']
            for component in (background, overlay)
            if component is not None
            and 'luminance' in component['animation'].VIBE_CAPABILITIES
        ), None)
        if luminance_component is not None:
            source_changed = changed
            pixels, changed = self._apply_vibe_presentation(
                luminance_component,
                pixels,
                profile=resolved_vibe.profile,
                changed=changed,
                state=final_presentation_state,
                force_refresh=force_refresh,
                include_grade=False,
                include_luminance=True,
            )
            if changed and not source_changed:
                dirty_ranges = None
        return np.asarray(pixels, dtype=np.uint8), changed, dirty_ranges

    def _render_composed_scene_frame(
        self, *, now: Optional[float] = None
    ) -> tuple[np.ndarray, bool, Optional[tuple[tuple[int, int], ...]]]:
        with self._scene_lock:
            if not self._scene_mode or not self._scene_background or not self._scene_compositor:
                raise RuntimeError("no composed scene is active")
            with self._presentation_state_guard():
                resolved = self._resolved_vibe
                operator_tempo = self.animation_speed_scale
                presentation_revision = self._presentation_revision
                force_refresh = bool(self._presentation_refresh_pending)
            result = self._compose_scene_components(
                self._scene_background,
                self._scene_overlay,
                self._scene_compositor,
                self._scene_final_presentation_state,
                now=time.perf_counter() if now is None else now,
                resolved_vibe=resolved,
                operator_tempo=operator_tempo,
                force_refresh=force_refresh,
            )
            with self._presentation_state_guard():
                if self._presentation_revision == presentation_revision:
                    self._presentation_refresh_pending = False
            return result

    def render_composed_scene_frame(
        self, *, now: Optional[float] = None
    ) -> BaseFrame:
        """Synchronously render one active scene frame for diagnostics/tests."""
        pixels, changed, dirty_ranges = self._render_composed_scene_frame(now=now)
        return BaseFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)

    def _render_compatibility_frame(
        self, time_elapsed: float
    ) -> tuple[Any, bool, Optional[tuple[tuple[int, int], ...]]]:
        """Render the unchanged single-animation/background-only pipeline."""
        animation = self.current_animation
        if animation is None:
            raise RuntimeError("no animation is active")
        if isinstance(animation, AnimationBase):
            if not hasattr(self, '_resolved_vibe'):
                self._resolved_vibe = resolve_vibe('neutral')
                self._vibe_diagnostic = None
                self.animation_speed_scale = getattr(
                    self, 'animation_speed_scale', 1.0
                )
                self.plant_modifier_state = getattr(
                    self, 'plant_modifier_state', PlantModifierState.empty()
                )
                self._scaled_elapsed = 0.0
                self._last_unscaled_elapsed = 0.0
                self._scene_epoch = 0
                self._presentation_revision = 0
            with self._presentation_state_guard():
                resolved_vibe = self._resolved_vibe
                operator_tempo = self.animation_speed_scale
                presentation_revision = self._presentation_revision
                force_refresh = bool(getattr(
                    self, '_presentation_refresh_pending', False
                ))
            context = self._advance_runtime_context(
                animation,
                time_elapsed,
                self.frame_count,
                resolved_vibe=resolved_vibe,
                operator_tempo_scale=operator_tempo,
            )
            rendered = animation.generate_frame_with_context(context)
        else:
            rendered = animation.generate_frame(time_elapsed, self.frame_count)
            resolved_vibe = resolve_vibe('neutral')
            presentation_revision = 0
            force_refresh = False

        changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
        dirty_ranges = rendered.dirty_ranges if isinstance(rendered, RenderedFrame) else None
        frame = self._normalize_frame(rendered)
        refresh_pending = getattr(
            animation, 'framework_plant_modifier_refresh_pending', None
        )
        apply_framework = getattr(animation, 'apply_framework_plant_modifiers', None)
        framework_active = getattr(animation, 'framework_plant_modifiers_active', None)
        framework_refresh = bool(
            refresh_pending() if callable(refresh_pending) else False
        )
        if callable(apply_framework):
            frame = apply_framework(frame, changed=changed)
        if callable(framework_active) and framework_active():
            changed = changed or framework_refresh
            dirty_ranges = None
        if isinstance(animation, AnimationBase):
            source_changed = changed
            if not hasattr(self, '_live_presentation_state'):
                self._live_presentation_state = self._empty_presentation_state()
            frame, changed = self._apply_vibe_presentation(
                animation,
                frame,
                profile=resolved_vibe.profile,
                changed=changed,
                state=self._live_presentation_state,
                force_refresh=force_refresh,
            )
            with self._presentation_state_guard():
                if self._presentation_revision == presentation_revision:
                    self._presentation_refresh_pending = False
            if changed and not source_changed:
                dirty_ranges = None
        return frame, changed, dirty_ranges

    def _receiver_hybrid_tick(self, now: float) -> tuple[np.ndarray, bool]:
        with self._scene_state_guard():
            scene = self._active_scene_state
            publisher = self._receiver_sparse_publisher
            if (
                not self._receiver_hybrid_mode
                or scene is None
                or publisher is None
            ):
                raise RuntimeError("receiver hybrid scene disappeared")
            before = publisher.get_status()["counts"]
            foreground = self._render_receiver_foreground(now=now)
            scene_time_us = self._receiver_scene_time_us(now)
            if not self._publish_receiver_foreground(
                foreground,
                scene_time_us=scene_time_us,
                now=now,
            ):
                error = publisher.get_status().get("last_error")
                raise RuntimeError(error or "receiver foreground publication failed")
            after = publisher.get_status()["counts"]
            transmitted = any(
                after[name] != before[name]
                for name in ("full_snapshots", "delta_generations", "renewals")
            )
            if foreground.changed:
                preview = self._receiver_preview_frame(
                    scene,
                    foreground,
                    scene_time_us=scene_time_us,
                )
            else:
                # The live status frame is explicitly a host simulation, not
                # receiver readback. Keep the last useful preview between sparse
                # foreground changes so local-base offload does not quietly turn
                # into a 200 Hz host background renderer.
                with self.frame_data_lock:
                    preview = np.asarray(self.current_frame_data, dtype=np.uint8)
            return preview, transmitted

    def _animation_loop(self, run_generation: Optional[int] = None):
        """Main animation loop running in separate thread"""
        if run_generation is None:
            run_generation = getattr(self, '_run_generation', 0)
        inline_show = getattr(self.controller, "inline_show", False)
        pending_present = None

        # One presentation may overlap generation of the next frame. We resolve
        # it before the animation can rotate back to the same one of its two
        # reusable buffers, so ownership remains deterministic without copies.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="led-present") as presenter:
            while self._run_is_active(run_generation):
                loop_start = time.perf_counter()
                generate_duration = 0.0
                send_duration = 0.0
                show_duration = 0.0

                try:
                    receiver_hybrid = bool(getattr(self, "_receiver_hybrid_mode", False))
                    if not self.current_animation and not receiver_hybrid:
                        break

                    gen_start = time.perf_counter()
                    if receiver_hybrid:
                        frame, transmitted = self._receiver_hybrid_tick(loop_start)
                        changed = transmitted
                        dirty_ranges = None
                    elif getattr(self, '_scene_mode', False):
                        frame, changed, dirty_ranges = (
                            self._render_composed_scene_frame(now=loop_start)
                        )
                    else:
                        frame, changed, dirty_ranges = (
                            self._render_compatibility_frame(
                                loop_start - self.start_time
                            )
                        )
                    generate_duration = time.perf_counter() - gen_start

                    if not self._run_owns_generation(run_generation):
                        break
                    with self._run_state_guard():
                        if not self._run_owns_generation(run_generation):
                            break
                        with self.frame_data_lock:
                            self.current_frame_data = frame

                    if receiver_hybrid:
                        if transmitted:
                            self.frames_presented += 1
                        else:
                            self.unchanged_frames_skipped += 1
                        self.frame_count += 1
                        self._update_fps_tracking(loop_start)
                        generate_duration = time.perf_counter() - gen_start
                        pending_present = None
                        # Receiver publication is synchronous under the manager's
                        # presentation-I/O guard; no host RGB future is submitted.
                        should_present = False
                    else:
                        should_present = changed or self.frames_presented == 0

                    if pending_present is not None:
                        completed = pending_present
                        pending_present = None
                        send_duration, show_duration = completed.result()
                        if not self._run_owns_generation(run_generation):
                            break

                    if not receiver_hybrid and should_present:
                        use_partial = bool(
                            dirty_ranges
                            and self.frames_presented > 0
                            and hasattr(self.controller, 'set_frame')
                        )
                        pending_present = presenter.submit(
                            self._present_frame,
                            frame,
                            dirty_ranges,
                            use_partial,
                            inline_show,
                        )
                        self.frames_presented += 1
                    elif not receiver_hybrid:
                        self.unchanged_frames_skipped += 1

                    if not receiver_hybrid:
                        self.frame_count += 1
                        self._update_fps_tracking(loop_start)

                except RuntimeError as e:
                    if str(e).startswith("cannot schedule new futures after"):
                        # A daemon render loop can overlap the last instant of
                        # interpreter shutdown in short-lived tools/tests.
                        # Exit quietly once the futures runtime is unavailable.
                        if self._run_is_active(run_generation):
                            self.is_running = False
                        break
                    if (
                        getattr(self, "_receiver_hybrid_mode", False)
                        and getattr(self, "_active_scene_state", None) is not None
                    ):
                        scene = self._active_scene_state
                        print(f"✗ Receiver hybrid loop failed over: {e}")
                        traceback.print_exc()
                        self._activate_known_python_fallback(scene, e)
                        break
                    print(f"✗ Animation loop error: {e}")
                    traceback.print_exc()
                    time.sleep(0.05)
                except Exception as e:
                    if (
                        getattr(self, "_receiver_hybrid_mode", False)
                        and getattr(self, "_active_scene_state", None) is not None
                    ):
                        scene = self._active_scene_state
                        print(f"✗ Receiver hybrid loop failed over: {e}")
                        traceback.print_exc()
                        self._activate_known_python_fallback(scene, e)
                        break
                    print(f"✗ Animation loop error: {e}")
                    traceback.print_exc()
                    time.sleep(0.05)

                loop_duration = time.perf_counter() - loop_start
                target_frame_time = 1.0 / max(1, int(self.target_fps) or 1)
                sleep_time = max(0.0, target_frame_time - loop_duration)
                if sleep_time > 0:
                    time.sleep(sleep_time)

                self._record_perf_sample({
                    'generate': generate_duration,
                    'send': send_duration,
                    'show': show_duration,
                    'process': loop_duration,
                    'sleep': sleep_time,
                    'frame': loop_duration + sleep_time,
                })

            if pending_present is not None:
                try:
                    pending_present.result()
                except Exception as e:
                    print(f"✗ Final frame presentation failed: {e}")

    def _presentation_io_guard(self):
        """Serialize controller I/O across timed-out stop/start boundaries."""
        lock = getattr(self, '_presentation_io_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._presentation_io_lock = lock
        return lock

    def _present_frame(self, frame, dirty_ranges, use_partial, inline_show):
        """Present one frame on the dedicated I/O worker and return timings."""
        with self._presentation_io_guard():
            send_start = time.perf_counter()
            if use_partial:
                self.controller.set_frame(frame, dirty_ranges=dirty_ranges)
            else:
                self.controller.set_all_pixels(frame)
            send_duration = time.perf_counter() - send_start

            show_duration = 0.0
            if not inline_show and hasattr(self.controller, "show"):
                show_start = time.perf_counter()
                self.controller.show()
                show_duration = time.perf_counter() - show_start
        return send_duration, show_duration

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
                raise ValueError(
                    f"frame ndarray must have shape (N, 3), got {colors.shape}"
                )
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
    
    def _update_fps_tracking(self, timestamp: Optional[float] = None):
        """Record frame timestamps for FPS calculation"""
        now = timestamp if timestamp is not None else time.perf_counter()
        self.frame_timestamps.append(now)

        # Keep only a small window of timestamps to reflect current performance
        while self.frame_timestamps and (now - self.frame_timestamps[0]) > 5.0:
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
            if last_frames is not None and last_time is not None and now > last_time:
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
            totals = {key: 0.0 for key in ('generate', 'send', 'show', 'process', 'sleep', 'frame')}
            for sample in self.perf_samples:
                for key in totals.keys():
                    totals[key] += sample.get(key, 0.0)

            target_frame_ms = 1000.0 / max(1, float(self.target_fps or 1))
            summary = {
                'samples': count,
                'target_frame_ms': target_frame_ms,
                'controller_inline_show': bool(getattr(self.controller, "inline_show", False)),
            }

            for key, total in totals.items():
                summary[f'avg_{key}_ms'] = (total / count) * 1000.0
                ordered = sorted(sample.get(key, 0.0) for sample in self.perf_samples)
                for label, ratio in (('p50', 0.50), ('p95', 0.95), ('p99', 0.99)):
                    index = min(count - 1, max(0, int(round((count - 1) * ratio))))
                    summary[f'{label}_{key}_ms'] = ordered[index] * 1000.0

            deadline_misses = sum(
                sample.get('process', 0.0) > (target_frame_ms / 1000.0)
                for sample in self.perf_samples
            )
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
            print(f"✗ Failed to reload animation {name}: {e}")
            return False

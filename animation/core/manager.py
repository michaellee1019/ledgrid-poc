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
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import (
    AnimationRuntimeContext,
    ResolvedVibe,
    VibeState,
    list_vibe_profiles,
    resolve_vibe,
)
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP
from drivers.frame_codec import encode_frame_data, FRAME_ENCODING_NAME

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
        self._presentation_state_lock = threading.RLock()
        self.animation_speed_scale = self._validate_tempo_scale(animation_speed_scale)
        self._resolved_vibe, self._vibe_diagnostic = self._resolve_initial_vibe(vibe)
        self._presentation_revision = 0
        self._scaled_elapsed = 0.0
        self._last_unscaled_elapsed = 0.0
        self._scene_epoch = time.time_ns() & ((1 << 64) - 1)
        self._presentation_refresh_pending = True
        self._live_presentation_state = self._empty_presentation_state()
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
            return {name: self.plugin_loader.get_plugin_info(name) for name in plugins.keys()}
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
            wall_time=time.time(),
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
            plant_modifiers=self.plant_modifier_state.to_dict(),
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
        animation = self.current_animation
        if not isinstance(animation, AnimationBase):
            return
        with self._presentation_state_guard():
            resolved = self._resolved_vibe
            operator_tempo = self.animation_speed_scale
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
    ) -> tuple[Any, bool]:
        capabilities = animation.VIBE_CAPABILITIES
        policy = animation.VIBE_COLOR_POLICY
        grade = (
            profile.vibe_id != "neutral"
            and policy == "grade"
            and "palette_roles" in capabilities
        )
        luminance = profile.luminance_scale if "luminance" in capabilities else 1.0
        identity = (
            profile.resolved_profile_digest, policy, tuple(sorted(capabilities))
        )
        refresh = force_refresh or state.get("identity") != identity
        state["identity"] = identity

        if not grade and luminance == 1.0:
            state["cached"] = None
            return pixels, changed or refresh
        if not changed and not refresh and state.get("cached") is not None:
            return state["cached"], False

        array = np.asarray(pixels, dtype=np.uint8)
        if array.ndim != 2 or array.shape[1] != 3:
            raise ValueError("vibe presentation requires an RGB frame")
        count = array.shape[0]
        if state.get("geometry") != count:
            state["buffers"] = [
                np.empty((count, 3), dtype=np.uint8) for _ in range(2)
            ]
            state["index"] = 0
            state["geometry"] = count
        output = state["buffers"][state["index"]]
        state["index"] = (state["index"] + 1) % len(state["buffers"])

        working = array.astype(np.float32)
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
                working = working * (1.0 - tint_weight) + tint * tint_weight
        if luminance != 1.0:
            working *= luminance
        np.clip(working, 0.0, 255.0, out=working)
        np.copyto(output, np.rint(working), casting="unsafe")
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
        self.plant_modifier_state = state
        self.plant_aware = bool(state.active)
        self._legacy_plant_aware_bridge = True
        if self.current_animation:
            self.current_animation.update_parameters({
                'plant_aware': self.plant_aware,
                'plant_modifiers': state.to_dict(),
            })
        self._update_preview_plant_state()
        return self.plant_aware

    def set_plant_modifiers(self, state: Any) -> Dict[str, Any]:
        """Validate and apply modifier authority live and to every future start."""
        self.plant_modifier_state = PlantModifierState.from_payload(state)
        self._legacy_plant_aware_bridge = False
        self.plant_aware = bool(self.plant_modifier_state.active)
        if self.current_animation:
            self.current_animation.update_parameters({
                'plant_aware': False,
                'plant_modifiers': self.plant_modifier_state.to_dict(),
            })
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
            session['animation'].update_parameters({
                'plant_aware': self.plant_aware if self._legacy_plant_aware_bridge else False,
                'plant_modifiers': self.plant_modifier_state.to_dict(),
            })
    
    def list_animations(self) -> List[Dict[str, Any]]:
        """Get list of available animations with metadata"""
        animations = []
        for plugin_name in self.plugin_loader.list_plugins():
            info = self.plugin_loader.get_plugin_info(plugin_name)
            if info:
                animations.append(info)
        return animations
    
    def get_animation_info(self, animation_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed info about a specific animation"""
        return self.plugin_loader.get_plugin_info(animation_name)
    
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
            # Stop current animation if running
            self.stop_animation(clear_leds=True)
            
            # Get animation class
            animation_class = self.plugin_loader.get_plugin(animation_name)
            if animation_class is None:
                print(f"✗ Animation not found: {animation_name}")
                return False
            
            # Create animation instance
            effective_config = dict(config or {})
            effective_config['plant_aware'] = self.plant_aware if self._legacy_plant_aware_bridge else False
            effective_config['plant_modifiers'] = self.plant_modifier_state.to_dict()
            self.current_animation = animation_class(self.controller, effective_config)
            self.current_animation_name = animation_name
            self.current_animation_hash = self._compute_animation_hash(animation_name)
            self.current_preset = self._normalize_current_preset(preset, animation_name)

            print(f"🔍 Animation instance created: {type(self.current_animation)}")
            print(f"🔍 Is StatefulAnimationBase? {isinstance(self.current_animation, StatefulAnimationBase)}")

            # Ensure controller is configured before frames start flowing
            if hasattr(self.controller, "configure"):
                try:
                    self.controller.configure()
                except Exception as controller_error:
                    print(f"⚠️ Controller configure failed: {controller_error}")

            # Start animation
            self.current_animation.start()
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
            self._refresh_active_presentation_context()

            # Check if this is a stateful animation
            if isinstance(self.current_animation, StatefulAnimationBase):
                # Stateful animations manage their own threads and timing
                print(f"✓ Started stateful animation: {animation_name}")
            else:
                # Frame-based animations need the animation loop
                self.animation_thread = threading.Thread(target=self._animation_loop, daemon=True)
                self.animation_thread.start()
                print(f"✓ Started frame-based animation: {animation_name}")

            self._remember_active_state(
                animation_name, restore_config, self.current_preset
            )

            return True
            
        except Exception as e:
            print(f"✗ Failed to start animation {animation_name}: {e}")
            traceback.print_exc()
            return False
    
    def stop_animation(self, clear_leds: bool = True):
        """Stop current animation or painter mode output."""
        had_output = self.is_running or self.painter_active

        if self.is_running:
            self.is_running = False
            self.stop_event.set()

            # Stop frame-based animation thread if it exists
            if self.animation_thread and self.animation_thread.is_alive():
                self.animation_thread.join(timeout=1.0)
            self.animation_thread = None

            # Stop the animation (stateful animations handle their own threads)
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

        if clear_leds and had_output:
            self.controller.clear()
    
    def update_animation_parameters(self, params: Dict[str, Any]) -> bool:
        """Update current animation parameters in real-time"""
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
        mode = 'animation' if self.is_running else ('painter' if self.painter_active else 'idle')
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
    ) -> bool:
        """Dispatch a validated logical-grid interaction to the active animation."""
        if not self.current_animation:
            return False
        event = self._validated_interaction(
            self.current_animation, kind, x, y, strength
        )
        return bool(self.current_animation.handle_interaction(*event))

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
        mode = 'animation' if self.is_running else ('painter' if self.painter_active else 'idle')
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

    def _animation_loop(self):
        """Main animation loop running in separate thread"""
        inline_show = getattr(self.controller, "inline_show", False)
        pending_present = None

        # One presentation may overlap generation of the next frame. We resolve
        # it before the animation can rotate back to the same one of its two
        # reusable buffers, so ownership remains deterministic without copies.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="led-present") as presenter:
            while self.is_running and not self.stop_event.is_set():
                loop_start = time.perf_counter()
                generate_duration = 0.0
                send_duration = 0.0
                show_duration = 0.0

                try:
                    if not self.current_animation:
                        break

                    time_elapsed = loop_start - self.start_time
                    gen_start = time.perf_counter()
                    if isinstance(self.current_animation, AnimationBase):
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
                            self.current_animation,
                            time_elapsed,
                            self.frame_count,
                            resolved_vibe=resolved_vibe,
                            operator_tempo_scale=operator_tempo,
                        )
                        rendered = self.current_animation.generate_frame_with_context(context)
                    else:
                        rendered = self.current_animation.generate_frame(
                            time_elapsed, self.frame_count
                        )
                    changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
                    dirty_ranges = rendered.dirty_ranges if isinstance(rendered, RenderedFrame) else None
                    frame = self._normalize_frame(rendered)
                    refresh_pending = getattr(
                        self.current_animation,
                        'framework_plant_modifier_refresh_pending',
                        None,
                    )
                    apply_framework = getattr(
                        self.current_animation, 'apply_framework_plant_modifiers', None
                    )
                    framework_active = getattr(
                        self.current_animation, 'framework_plant_modifiers_active', None
                    )
                    framework_refresh = bool(
                        refresh_pending() if callable(refresh_pending) else False
                    )
                    if callable(apply_framework):
                        frame = apply_framework(frame, changed=changed)
                    if callable(framework_active) and framework_active():
                        changed = changed or framework_refresh
                        # Optical displacement can make a source pixel affect a
                        # neighboring plant-region pixel, so plugin dirty ranges
                        # are no longer a complete presentation description.
                        dirty_ranges = None
                    if isinstance(self.current_animation, AnimationBase):
                        source_changed = changed
                        if not hasattr(self, '_live_presentation_state'):
                            self._live_presentation_state = self._empty_presentation_state()
                        frame, changed = self._apply_vibe_presentation(
                            self.current_animation,
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
                    generate_duration = time.perf_counter() - gen_start

                    with self.frame_data_lock:
                        self.current_frame_data = frame

                    if pending_present is not None:
                        completed = pending_present
                        pending_present = None
                        send_duration, show_duration = completed.result()

                    should_present = changed or self.frames_presented == 0
                    if should_present:
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
                    else:
                        self.unchanged_frames_skipped += 1

                    self.frame_count += 1
                    self._update_fps_tracking(loop_start)

                except RuntimeError as e:
                    if str(e).startswith("cannot schedule new futures after"):
                        # A daemon render loop can overlap the last instant of
                        # interpreter shutdown in short-lived tools/tests.
                        # Exit quietly once the futures runtime is unavailable.
                        self.is_running = False
                        break
                    print(f"✗ Animation loop error: {e}")
                    traceback.print_exc()
                    time.sleep(0.05)
                except Exception as e:
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

    def _present_frame(self, frame, dirty_ranges, use_partial, inline_show):
        """Present one frame on the dedicated I/O worker and return timings."""
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

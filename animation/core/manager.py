#!/usr/bin/env python3
"""
Animation Manager Service

Coordinates between LED controller, animation plugins, and web interface.
Handles animation switching, parameter updates, and frame generation.
"""

import hashlib
import json
import math
import sys
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
                 default_animation: Optional[str] = None,
                 default_animation_config: Optional[Dict[str, Any]] = None,
                 auto_start: bool = True):
        """
        Initialize animation manager
        
        Args:
            controller: LED controller instance
            plugins_dir: Directory containing animation plugins
            animation_speed_scale: Multiplier applied to each animation's speed parameter at start
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
        self.animation_speed_scale = animation_speed_scale
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
            if self.start_animation(self._default_animation, self._default_animation_config):
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

    def _apply_speed_scale(self):
        """Apply global speed scaling to the current animation if supported"""
        if not self.current_animation:
            return
        if not hasattr(self.current_animation, "params"):
            return
        if 'speed' not in self.current_animation.params:
            return
        base_speed = self.current_animation.params['speed']
        scaled_speed = base_speed * self.animation_speed_scale
        # Prevent negative or zero speeds
        if scaled_speed <= 0:
            scaled_speed = base_speed
        self.current_animation.update_parameters({'speed': scaled_speed})

    def set_animation_speed_scale(self, speed_scale: float) -> float:
        """Apply a live global animation speed scalar.

        The active animation already contains the previously scaled value, so
        adjust it by the ratio between the new and old scales. Preset-authored
        speed values therefore remain independent of the dashboard tempo knob.
        """
        requested = float(speed_scale)
        if not math.isfinite(requested) or requested <= 0:
            raise ValueError("animation speed scale must be a positive finite number")

        previous = self.animation_speed_scale
        self.animation_speed_scale = requested
        if (
            self.current_animation
            and hasattr(self.current_animation, "params")
            and 'speed' in self.current_animation.params
            and previous > 0
        ):
            current_speed = self.current_animation.params['speed']
            self.current_animation.update_parameters({
                'speed': current_speed * (requested / previous)
            })
        return self.animation_speed_scale

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

            self._apply_speed_scale()

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
                self.animation_thread.join(timeout=2.0)
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
            }
            self._preview_session = session
        session['last_access'] = now
        return session

    def _render_preview(
        self, animation_name: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        with self._preview_lock:
            session = self._preview_session_for(animation_name, params)
            animation = session['animation']
            elapsed = max(0.0, time.monotonic() - session['started_at'])
            rendered = animation.generate_frame(elapsed, session['frame_count'])
            changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
            frame_data = self._normalize_frame(rendered)
            frame_data = animation.apply_framework_plant_modifiers(
                frame_data, changed=changed
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
            }

    def get_animation_preview(self, animation_name: str) -> Dict[str, Any]:
        """Advance and return the process-local dashboard preview session."""
        return self._render_preview(animation_name)

    def get_animation_preview_with_params(
        self, animation_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Advance a preview, resetting only when authored parameters change."""
        return self._render_preview(animation_name, params)

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
                    rendered = self.current_animation.generate_frame(time_elapsed, self.frame_count)
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
                        try:
                            pending_present = presenter.submit(
                                self._present_frame,
                                frame,
                                dirty_ranges,
                                use_partial,
                                inline_show,
                            )
                        except RuntimeError:
                            # ThreadPoolExecutor shuts down from atexit while a
                            # leaked daemon loop is still running. Exit quietly.
                            break
                        self.frames_presented += 1
                    else:
                        self.unchanged_frames_skipped += 1

                    self.frame_count += 1
                    self._update_fps_tracking(loop_start)

                except Exception as e:
                    if getattr(sys, "is_finalizing", lambda: False)():
                        break
                    try:
                        print(f"✗ Animation loop error: {e}")
                        traceback.print_exc()
                    except Exception:
                        break
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

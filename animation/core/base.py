#!/usr/bin/env python3
"""
Base animation class and plugin system for LED Grid
"""

import time
import colorsys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import List, Tuple, Dict, Any, Optional, Union, Mapping

import numpy as np

from animation.core.presentation_contracts import AnimationRuntimeContext, TimingAdapter
from animation.core.installation_profile_runtime import InstallationProfileRuntimeView
from animation.core.receiver_optics import HUE_STRENGTH_MAX, apply_hue_shift_u8
from animation.core.receiver_presentation import quantize_q8_8

from animation.core.plant_awareness import (
    FRAMEWORK_VISUAL_MODIFIERS, PlantMaskCache, PlantMaskGeometry,
    PlantModifierState, plant_parameter_schema,
)


@dataclass(frozen=True)
class RenderedFrame:
    """Frame pixels plus presentation hints for the animation manager.

    ``changed`` lets event-driven and source-rate animations avoid repeatedly
    transmitting an identical frame. ``dirty_ranges`` is optional advisory
    metadata for controllers that can choose a partial-update protocol.
    """

    pixels: Any
    changed: bool = True
    dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None


FrameOutput = Union[np.ndarray, RenderedFrame]


class AnimationBase(ABC):
    """Base class for all LED animations"""

    PLANT_MODIFIER_SUPPORT = frozenset()
    INTERACTION_TYPES = frozenset()
    TIMING_ADAPTER = TimingAdapter.LEGACY_SPEED_PARAM
    VIBE_CAPABILITIES = frozenset()
    VIBE_COLOR_POLICY = "preserve"
    VIBE_PARAMETER_MAPPINGS: Mapping[str, Any] = MappingProxyType({})
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        """
        Initialize animation
        
        Args:
            controller: LED controller instance
            config: Animation configuration parameters
        """
        self.controller = controller
        self.config = config or {}
        self.start_time = time.time()
        self.frame_count = 0
        self.is_running = False
        self._frame_buffers: List[np.ndarray] = []
        self._frame_buffer_index = 0
        self._frame_buffer_geometry: Optional[Tuple[int, int]] = None
        self._hsv_scratch: Dict[str, np.ndarray] = {}
        self._plant_mask_cache = PlantMaskCache(self)
        self._managed_plant_mask_variants: Dict[
            Tuple[Tuple[Any, ...], int], PlantMaskGeometry
        ] = {}
        self._framework_modifier_buffers: List[np.ndarray] = []
        self._framework_modifier_buffer_index = 0
        self._framework_modifier_geometry: Optional[int] = None
        self._framework_modifier_cached_frame: Optional[np.ndarray] = None
        self._presentation_context: Optional[AnimationRuntimeContext] = None
        self._presentation_lock = threading.RLock()
        
        # Animation metadata
        self.name = getattr(self, 'ANIMATION_NAME', self.__class__.__name__)
        self.description = getattr(self, 'ANIMATION_DESCRIPTION', 'No description')
        self.author = getattr(self, 'ANIMATION_AUTHOR', 'Unknown')
        self.version = getattr(self, 'ANIMATION_VERSION', '1.0')
        
        # Default parameters that can be overridden
        self.default_params = {
            'speed': 1.0,
            'brightness': 1.0,
            'color_saturation': 1.0,
            'color_value': 1.0,
            'plant_aware': False,
            'plant_modifiers': PlantModifierState.empty().to_dict(),
            'plant_clearance': 1,
            'plant_mask_path': 'config/plant_pixel_map_32x138.json',
            'plant_globe_mask_path': 'config/plant_globe_map_32x138.json',
        }
        
        # Merge default params with config
        self._authored_params = {**self.default_params, **self.config}
        self.params = self._authored_params
        self._plant_modifier_state = self._resolve_plant_modifier_state()

    @property
    def presentation_context(self) -> Optional[AnimationRuntimeContext]:
        """Current immutable runtime context, or ``None`` for direct/headless use."""
        return self._presentation_context

    @property
    def authored_params(self) -> Mapping[str, Any]:
        """Read-only view of parameters selected by the preset/operator."""
        with self._presentation_lock:
            self._sync_authored_params()
            return MappingProxyType(self._authored_params)

    def get_authored_parameter(self, name: str, default: Any = None) -> Any:
        """Read one authored value atomically without copying the parameter set."""
        with self._presentation_lock:
            self._sync_authored_params()
            return self._authored_params.get(name, default)

    def authored_params_snapshot(self) -> Dict[str, Any]:
        """Return a stable authored snapshot for status and persistence."""
        with self._presentation_lock:
            self._sync_authored_params()
            return dict(self._authored_params)

    @property
    def effective_params(self) -> Mapping[str, Any]:
        """Read-only parameters visible to the current render invocation."""
        with self._presentation_lock:
            self._sync_authored_params()
            return MappingProxyType(dict(self.params))

    def _sync_authored_params(self) -> None:
        """Adopt subclass post-super initialization before managed rendering."""
        if self._presentation_context is None and self.params is not self._authored_params:
            self._authored_params = dict(self.params)
            self.params = self._authored_params

    def set_presentation_context(self, context: AnimationRuntimeContext) -> None:
        """Install presentation inputs without entering parameter lifecycle.

        Hooks run only when presentation identity changes, never merely because
        frame time or frame index advanced.
        """
        if not isinstance(context, AnimationRuntimeContext):
            raise TypeError("presentation context must be an AnimationRuntimeContext")
        with self._presentation_lock:
            self._sync_authored_params()
            old = self._presentation_context
            self._presentation_context = context
            if old is None or old.presentation_identity != context.presentation_identity:
                self._framework_modifier_cached_frame = None
                if (
                    old is None
                    or old.installation_profile_identity
                    != context.installation_profile_identity
                ):
                    self._plant_mask_cache.invalidate()
                    self._managed_plant_mask_variants.clear()
                self.on_presentation_context_changed(old, context)

    def on_presentation_context_changed(
        self,
        old: Optional[AnimationRuntimeContext],
        new: AnimationRuntimeContext,
    ) -> None:
        """Presentation-cache invalidation hook; semantic state must remain intact."""

    def generate_frame_with_context(
        self, context: AnimationRuntimeContext
    ) -> FrameOutput:
        """Run one frame through the declared compatibility timing adapter."""
        with self._presentation_lock:
            self._sync_authored_params()
            self.set_presentation_context(context)
            adapter = TimingAdapter(self.TIMING_ADAPTER)
            visible_elapsed = context.unscaled_elapsed
            effective = self._authored_params
            if adapter is TimingAdapter.LEGACY_SPEED_PARAM:
                effective = dict(self._authored_params)
                if "speed" in effective:
                    effective["speed"] = context.effective_time_scale
            elif adapter is TimingAdapter.SCALED_CONTEXT:
                visible_elapsed = context.scaled_elapsed
                effective = dict(self._authored_params)
                if "speed" in effective:
                    effective["speed"] = 1.0

            if context.vibe_id != "neutral":
                for parameter, values in self.VIBE_PARAMETER_MAPPINGS.items():
                    mapped = values.get(context.vibe_id)
                    if mapped is not None:
                        if effective is self._authored_params:
                            effective = dict(self._authored_params)
                        effective[parameter] = mapped

            self.params = effective
            try:
                return self.generate_frame(visible_elapsed, context.frame_index)
            finally:
                self.params = self._authored_params
    
    @abstractmethod
    def generate_frame(self, time_elapsed: float, frame_count: int) -> FrameOutput:
        """
        Generate a single frame of animation
        
        Args:
            time_elapsed: Time since animation started (seconds)
            frame_count: Number of frames rendered so far
            
        Returns:
            Canonical uint8 NumPy frame, optionally wrapped in RenderedFrame
        """
        pass
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        """
        Return schema describing configurable parameters
        
        Returns:
            Dict with parameter definitions including type, range, description
        """
        schema = {
            'speed': {
                'type': 'float',
                'min': 0.1,
                'max': 5.0,
                'default': 1.0,
                'description': 'Animation speed multiplier'
            },
            'brightness': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 1.0,
                'description': 'Overall brightness (0.0 - 1.0)'
            },
            'color_saturation': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 1.0,
                'description': 'Color saturation (0.0 - 1.0)'
            },
            'color_value': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 1.0,
                'description': 'Color value/brightness (0.0 - 1.0)'
            }
        }
        schema.update(plant_parameter_schema())
        return schema
    
    def update_parameters(self, new_params: Dict[str, Any]):
        """Update animation parameters in real-time"""
        self._sync_authored_params()
        if 'plant_modifiers' in new_params:
            new_params = dict(new_params)
            new_params['plant_modifiers'] = PlantModifierState.from_payload(
                new_params['plant_modifiers']
            ).to_dict()
        with self._presentation_lock:
            self._authored_params.update(new_params)
            if self.params is not self._authored_params:
                self.params.update(new_params)
        if {'plant_aware', 'plant_modifiers'} & new_params.keys():
            self._plant_modifier_state = self._resolve_plant_modifier_state()
            self._framework_modifier_cached_frame = None
        if {
            'plant_clearance', 'plant_mask_path', 'plant_globe_mask_path'
        } & new_params.keys():
            self._plant_mask_cache.invalidate()
            self._managed_plant_mask_variants.clear()

    def plant_aware_enabled(self) -> bool:
        """Return whether the animation's opt-in semantic mask behavior is active."""
        return bool(self.params.get('plant_aware', False)) or bool(self.plant_modifier_state().active)

    def plant_modifier_state(self) -> PlantModifierState:
        """Return the cached canonical state for hot render and simulation paths."""
        return self._plant_modifier_state

    def _resolve_plant_modifier_state(self) -> PlantModifierState:
        """Validate configured state and translate the legacy boolean bridge."""
        state = PlantModifierState.from_payload(self.params.get('plant_modifiers'))
        if state.active or not self.params.get('plant_aware', False):
            return state
        return PlantModifierState.from_legacy(True)

    def plant_modifier_enabled(self, modifier: str) -> bool:
        return self.plant_modifier_state().enabled(
            modifier, self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        )

    def plant_modifier_strength(self, modifier: str) -> float:
        return self.plant_modifier_state().strength(
            modifier, self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        )

    def framework_plant_modifiers_active(self) -> bool:
        return (
            quantize_q8_8(
                self.plant_modifier_strength("hue_shift"),
                name="hue_shift",
                maximum=HUE_STRENGTH_MAX,
            ) > 0
            or self.plant_modifier_strength("liquid_glass") > 0.0
        )

    def framework_plant_modifier_refresh_pending(self) -> bool:
        return (
            self.framework_plant_modifiers_active()
            and self._framework_modifier_cached_frame is None
        )

    def apply_framework_plant_modifiers(
        self,
        pixels: np.ndarray,
        *,
        changed: bool = True,
    ) -> np.ndarray:
        """Apply universal foliage/globe optics without mutating plugin buffers.

        Plugin-specific modifiers remain at their semantic render layer. These two
        visual-only modifiers are deliberately centralized so every animation,
        including diagnostics and third-party plugins, can use them consistently.
        """
        hue_shift = self.plant_modifier_strength("hue_shift")
        hue_strength_q8_8 = quantize_q8_8(
            hue_shift,
            name="hue_shift",
            maximum=HUE_STRENGTH_MAX,
        )
        liquid_glass = self.plant_modifier_strength("liquid_glass")
        if hue_strength_q8_8 == 0 and liquid_glass <= 0.0:
            return pixels
        if not changed and self._framework_modifier_cached_frame is not None:
            return self._framework_modifier_cached_frame

        array = np.asarray(pixels)
        expected = self.get_pixel_count()
        if array.shape != (expected, 3):
            raise ValueError(
                f"framework plant modifiers require shape ({expected}, 3), got {array.shape}"
            )
        if self._framework_modifier_geometry != expected:
            self._framework_modifier_buffers = [
                np.empty((expected, 3), dtype=np.uint8) for _ in range(2)
            ]
            self._framework_modifier_buffer_index = 0
            self._framework_modifier_geometry = expected

        output = self._framework_modifier_buffers[self._framework_modifier_buffer_index]
        self._framework_modifier_buffer_index = (
            self._framework_modifier_buffer_index + 1
        ) % len(self._framework_modifier_buffers)
        np.copyto(output, array, casting="unsafe")

        masks = self.get_plant_masks()
        target = masks.obstacle_flat
        apply_hue_shift_u8(output, hue_strength_q8_8, target)

        if liquid_glass > 0.0:
            width, height = self.get_strip_info()
            source = output.reshape(width, height, 3).copy()
            distance = masks.distance
            radius = 1.5 + 4.5 * liquid_glass
            glass = distance <= radius
            if np.any(glass):
                x, y = np.indices((width, height))
                displacement = 1 + int(round(2.0 * liquid_glass))
                sample_x = np.clip(
                    x + np.rint(masks.normal_x * displacement).astype(np.int16),
                    0,
                    width - 1,
                )
                sample_y = np.clip(
                    y + np.rint(masks.normal_y * displacement).astype(np.int16),
                    0,
                    height - 1,
                )
                sampled = source[sample_x, sample_y]
                weight = (
                    np.exp(-distance / max(0.5, radius * 0.7))
                    * (0.2 + 0.55 * liquid_glass)
                )[..., None]
                logical = output.reshape(width, height, 3)
                blended = source * (1.0 - weight) + sampled * weight
                logical[glass] = np.clip(blended[glass], 0.0, 255.0).astype(np.uint8)
                highlight = masks.obstacle_edge
                if np.any(highlight):
                    lifted = logical[highlight].astype(np.float32)
                    lifted += (18.0 + 34.0 * liquid_glass)
                    logical[highlight] = np.clip(lifted, 0.0, 255.0).astype(np.uint8)

        self._framework_modifier_cached_frame = output
        return output

    def get_plant_masks(self, clearance: Optional[int] = None) -> PlantMaskGeometry:
        """Use selected global geometry, or the legacy JSON cache by default."""
        context = self._presentation_context
        if context is not None and isinstance(
            context.installation_profile_view, InstallationProfileRuntimeView
        ):
            view = context.installation_profile_view
            masks = view.plant_masks
            if clearance is None:
                return masks
            radius = max(0, int(clearance))
            if radius == view.clearance_radius:
                return masks
            key = (view.presentation_identity, radius)
            cached = self._managed_plant_mask_variants.get(key)
            if cached is not None:
                return cached
            # The portable artifact's global Chebyshev distance field supports
            # the legacy explicit-clearance API without rereading JSON or
            # recomputing derivatives. Cache the single derived boolean layer;
            # every other immutable profile array remains shared by reference.
            resolved_clearance = np.asarray(masks.distance <= radius, dtype=np.bool_)
            resolved_clearance.setflags(write=False)
            cached = replace(
                masks,
                clearance=resolved_clearance,
                clearance_flat=resolved_clearance.reshape(-1),
            )
            self._managed_plant_mask_variants[key] = cached
            return cached
        return self._plant_mask_cache.get(clearance)
    
    def get_info(self) -> Dict[str, Any]:
        """Get animation metadata"""
        state = self.plant_modifier_state()
        supported_set = self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        supported = tuple(sorted(supported_set))
        return {
            'name': self.name,
            'description': self.description,
            'author': self.author,
            'version': self.version,
            'parameters': self.get_parameter_schema(),
            # Status/persistence always expose authored values, even if a render
            # thread is temporarily using an ephemeral effective parameter view.
            'current_params': self.authored_params_snapshot(),
            'plant_modifier_support': list(supported),
            'interaction_types': sorted(self.INTERACTION_TYPES),
            'unsupported_plant_modifiers': [
                modifier for modifier in state.active if modifier not in supported_set
            ],
        }

    def handle_interaction(
        self,
        kind: str,
        x: float,
        y: float,
        strength: float = 1.0,
    ) -> bool:
        """Handle a validated logical-grid interaction when supported."""
        return False
    
    def get_runtime_stats(self) -> Dict[str, Any]:
        """
        Optional hook for animations to expose debugging/telemetry data.
        Default implementation returns an empty dict.
        """
        return {}
    
    def start(self):
        """Called when animation starts"""
        self.start_time = time.time()
        self.frame_count = 0
        self.is_running = True
    
    def stop(self):
        """Called when animation stops"""
        self.is_running = False
    
    def cleanup(self):
        """Called when animation is being destroyed"""
        self.stop()
    
    # Utility methods for common operations
    def hsv_to_rgb(self, h: float, s: float, v: float) -> Tuple[int, int, int]:
        """Convert HSV to RGB (0-255)"""
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return int(r * 255), int(g * 255), int(b * 255)
    
    def apply_brightness(self, color: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Apply brightness parameter to a color"""
        r, g, b = color
        brightness = self.params.get('brightness', 1.0)
        return (
            int(r * brightness),
            int(g * brightness),
            int(b * brightness)
        )
    
    def hsv_to_rgb_array(
        self,
        h: np.ndarray,
        s: np.ndarray,
        v: np.ndarray,
        out: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Vectorized HSV-to-RGB for numpy arrays. Returns uint8 (N,3) array."""
        h = np.asarray(h, dtype=np.float32).ravel()
        s = np.asarray(s, dtype=np.float32).ravel()
        v = np.asarray(v, dtype=np.float32).ravel()
        size = h.size
        if s.size != size or v.size != size:
            raise ValueError("HSV component arrays must have equal lengths")

        if self._hsv_scratch.get('h6', np.empty(0)).size != size:
            self._hsv_scratch = {
                'h6': np.empty(size, dtype=np.float32),
                'f': np.empty(size, dtype=np.float32),
                'p': np.empty(size, dtype=np.float32),
                'q': np.empty(size, dtype=np.float32),
                't': np.empty(size, dtype=np.float32),
                'sector': np.empty(size, dtype=np.int32),
                'rgb': np.empty((size, 3), dtype=np.float32),
            }
        h6 = self._hsv_scratch['h6']
        f = self._hsv_scratch['f']
        p = self._hsv_scratch['p']
        q = self._hsv_scratch['q']
        t = self._hsv_scratch['t']
        sector = self._hsv_scratch['sector']
        rgb = self._hsv_scratch['rgb']

        np.remainder(h, 1.0, out=h6)
        h6 *= 6.0
        np.floor(h6, out=f)
        np.copyto(sector, f, casting='unsafe')
        np.remainder(sector, 6, out=sector)
        np.subtract(h6, f, out=f)

        np.subtract(1.0, s, out=p)
        p *= v
        np.multiply(s, f, out=q)
        np.subtract(1.0, q, out=q)
        q *= v
        np.subtract(1.0, f, out=t)
        t *= s
        np.subtract(1.0, t, out=t)
        t *= v

        components = (
            (v, t, p),
            (q, v, p),
            (p, v, t),
            (p, q, v),
            (t, p, v),
            (v, p, q),
        )
        for index, (red, green, blue) in enumerate(components):
            mask = sector == index
            rgb[mask, 0] = red[mask]
            rgb[mask, 1] = green[mask]
            rgb[mask, 2] = blue[mask]

        if out is None:
            out = np.empty((size, 3), dtype=np.uint8)
        elif out.shape != (size, 3) or out.dtype != np.uint8:
            raise ValueError("HSV output buffer must be uint8 with shape (N, 3)")
        rgb *= 255.0
        np.clip(rgb, 0.0, 255.0, out=rgb)
        np.copyto(out, rgb, casting='unsafe')
        return out

    def apply_brightness_array(
        self,
        colors: np.ndarray,
        out: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Apply brightness parameter to an (N,3) uint8 array. Returns uint8."""
        brightness = self.params.get('brightness', 1.0)
        if brightness >= 1.0:
            if out is not None and out is not colors:
                np.copyto(out, colors, casting='unsafe')
                return out
            return colors
        if out is None:
            scaled = colors.astype(np.float32) * max(0.0, brightness)
            return np.clip(scaled, 0, 255).astype(np.uint8)
        np.multiply(colors, max(0.0, brightness), out=out, casting='unsafe')
        return out

    def next_frame_buffer(self, *, clear: bool = True, count: int = 2) -> np.ndarray:
        """Return the next reusable canonical frame buffer.

        Two buffers are sufficient for the synchronous manager: one may be
        retained for preview/status while the animation prepares the next.
        Buffer ownership is centralized here so plugins do not each implement
        subtly different double-buffering schemes.
        """
        total_pixels = self.get_pixel_count()
        geometry = (total_pixels, max(2, int(count)))
        if geometry != self._frame_buffer_geometry:
            self._frame_buffers = [
                np.zeros((total_pixels, 3), dtype=np.uint8)
                for _ in range(geometry[1])
            ]
            self._frame_buffer_index = 0
            self._frame_buffer_geometry = geometry

        frame = self._frame_buffers[self._frame_buffer_index]
        self._frame_buffer_index = (self._frame_buffer_index + 1) % len(self._frame_buffers)
        if clear:
            frame.fill(0)
        return frame

    @staticmethod
    def rendered_frame(
        pixels: Any,
        *,
        changed: bool = True,
        dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None,
    ) -> RenderedFrame:
        """Attach presentation hints to a generated frame."""
        return RenderedFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)

    def get_pixel_count(self) -> int:
        """Get total number of pixels"""
        return self.controller.total_leds
    
    def get_strip_info(self) -> Tuple[int, int]:
        """Get (strip_count, leds_per_strip)"""
        return self.controller.strip_count, self.controller.leds_per_strip


class StatefulAnimationBase(AnimationBase):
    """
    Base class for animations that control their own timing and state

    Unlike frame-based animations that generate frames at 50 FPS,
    stateful animations run their own loop and only update LEDs when needed.
    This is perfect for animations like strip tests that hold states for seconds.
    """

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.animation_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

    @abstractmethod
    def run_animation(self):
        """
        Main animation logic - runs in its own thread

        This method should contain the main animation loop.
        Check self.stop_event.is_set() periodically to allow clean shutdown.
        Use self.controller.set_all_pixels() to update LEDs only when needed.
        """
        pass

    def generate_frame(self, time_elapsed: float, frame_count: int) -> FrameOutput:
        """
        Stateful animations don't use frame generation - they control their own timing
        This method should not be called for stateful animations.
        """
        # Return black frame - this shouldn't be used
        return np.zeros((self.controller.total_leds, 3), dtype=np.uint8)

    def start(self):
        """Start the stateful animation in its own thread"""
        super().start()
        self.stop_event.clear()
        self.animation_thread = threading.Thread(target=self.run_animation, daemon=True)
        self.animation_thread.start()

    def stop(self):
        """Stop the stateful animation"""
        super().stop()
        self.stop_event.set()
        if self.animation_thread and self.animation_thread.is_alive():
            self.animation_thread.join(timeout=2.0)

    def cleanup(self):
        """Clean up the stateful animation"""
        self.stop()
        self.animation_thread = None

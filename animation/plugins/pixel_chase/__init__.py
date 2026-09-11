"""Scene v2 Pixel Chase: transparent, semantic light moving over the whole wall."""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import OverlayFrame, coverage_dirty_union
from animation.core.presentation_contracts import ResolvedScene


class PixelChaseAnimation(AnimationBase):
    """Evenly spaced heads traverse the complete physical LED path."""

    ANIMATION_NAME = "Pixel Chase"
    ANIMATION_DESCRIPTION = "Configurable light heads and tails chase through every physical LED"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "3.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "pixel_chase", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgba_uint8_premultiplied_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    TAIL_STYLES = ("none", "fade", "solid")
    DEFAULTS = MappingProxyType({"pixels_per_second": 120.0, "pixel_count": 3, "tail_style": "fade", "tail_length": 4, "color_cycle_speed": 0.2})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="premultiplied_rgba", palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS, parameter_normalizer=lambda values: PixelChaseAnimation._normalized_parameters(values))
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": ((255, 226, 154), (255, 128, 46)),
        "mist": ((129, 225, 255), (236, 248, 255)),
        "spectrum": ((255, 67, 235), (46, 239, 227)),
        "ember": ((255, 83, 22), (255, 210, 82)),
    })

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        # Presentation-only geometry: foliage and final optics belong to the host.
        self._path = np.asarray([strip * self.height + led for strip in range(self.width) for led in range(self.height - 1, -1, -1)], dtype=np.int32)
        self._buffers = tuple(np.zeros((self.get_pixel_count(), 4), dtype=np.uint8) for _ in range(2))
        self._last_pixels, self._last_key = self._buffers[0], None
        self._last_head_pixels = np.empty(0, dtype=np.int32)
        self._revision = 0
        self._presentation_context: ResolvedScene | None = None

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor: return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(values); unknown = sorted(set(supplied) - set(cls.DEFAULTS))
        if unknown: raise ValueError(f"Pixel Chase does not accept non-local parameters: {unknown!r}")
        result = dict(cls.DEFAULTS); result.update(supplied)
        for name, low, high in (("pixels_per_second", .5, 1000.), ("color_cycle_speed", 0., 4.)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not low <= float(value) <= high: raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        for name, low, high in (("pixel_count", 1, 32), ("tail_length", 0, 32)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high: raise ValueError(f"{name} must be an integer from {low} to {high}")
        if result["tail_style"] not in cls.TAIL_STYLES: raise ValueError(f"tail_style must be one of {list(cls.TAIL_STYLES)!r}")
        return result

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "pixels_per_second": {"type": "float", "min": .5, "max": 1000., "default": 120., "description": "Physical LEDs travelled per scaled second"},
            "pixel_count": {"type": "int", "min": 1, "max": 32, "default": 3, "description": "Evenly spaced chase heads"},
            "tail_style": {"type": "str", "options": list(self.TAIL_STYLES), "default": "fade", "description": "Trailing light falloff"},
            "tail_length": {"type": "int", "min": 0, "max": 32, "default": 4, "description": "LEDs behind each head"},
            "color_cycle_speed": {"type": "float", "min": 0., "max": 4., "default": .2, "description": "Semantic palette cycling speed"},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        self.params = self._normalized_parameters({**self.params, **dict(new_params)}); self._last_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE): raise ValueError("Pixel Chase received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str): raise ValueError("Pixel Chase requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None: self.on_presentation_context_changed(self._presentation_context, context)
    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame: self.set_presentation_context(context); return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del frame_count
        if self._presentation_context is None: phase_time, palette_id, parameters = max(0., float(time_elapsed)), "neutral", self.params
        else: phase_time, palette_id, parameters = max(0., float(self._presentation_context.phase_time)), str(self._presentation_context.palette["palette_id"]), self._presentation_context.parameters
        self.params = self._normalized_parameters(parameters)
        step = int(math.floor(phase_time * self.params["pixels_per_second"] + 1.e-9)); cycle_tick = int(math.floor(phase_time * self.params["color_cycle_speed"] * 60. + 1.e-9))
        key = (step, cycle_tick, palette_id, tuple(self.params.items()))
        if key == self._last_key: return OverlayFrame(self._last_pixels, revision=self._revision, changed=False, dirty_ranges=())
        output = self._buffers[1] if self._last_pixels is self._buffers[0] else self._buffers[0]
        self._paint(output, step, cycle_tick, palette_id); dirty = coverage_dirty_union(self._last_pixels, output)
        self._last_pixels, self._last_key = output, key; self._revision += 1
        return OverlayFrame(output, revision=self._revision, changed=True, dirty_ranges=dirty)

    def _paint(self, output: np.ndarray, step: int, cycle_tick: int, palette_id: str) -> None:
        output.fill(0); path_size = self._path.size
        heads = np.asarray([(step + index * path_size // self.params["pixel_count"]) % path_size for index in range(self.params["pixel_count"])], dtype=np.int32)
        self._last_head_pixels = self._path[heads]
        if self.params["tail_style"] == "none": offsets = np.asarray((0,), dtype=np.int32); alpha = np.asarray((255,), dtype=np.uint8)
        else:
            offsets = np.arange(self.params["tail_length"] + 1, dtype=np.int32)
            alpha = np.full(offsets.size, 255, dtype=np.uint8) if self.params["tail_style"] == "solid" else np.rint(255 * (1. - offsets / (self.params["tail_length"] + 1))).astype(np.uint8)
        indices = self._path[(heads[:, None] + offsets[None, :]) % path_size]
        colors = np.stack([self._semantic_color(palette_id, cycle_tick, number) for number in range(heads.size)]).astype(np.uint16)
        alpha16 = alpha.astype(np.uint16)
        if np.unique(indices).size == indices.size:
            output[indices, :3] = ((colors[:, None, :] * alpha16[None, :, None] + 127) // 255).astype(np.uint8)
            output[indices, 3] = alpha[None, :]
            return
        for head_number, row in enumerate(indices):
            for offset, index in enumerate(row): self._paint_pixel(output, int(index), colors[head_number], int(alpha[offset]))

    @staticmethod
    def _paint_pixel(output: np.ndarray, index: int, color: np.ndarray, alpha: int) -> None:
        inverse = 255 - alpha; source = (color.astype(np.uint16) * alpha + 127) // 255
        output[index, :3] = np.minimum(255, source + (output[index, :3].astype(np.uint16) * inverse + 127) // 255)
        output[index, 3] = min(255, alpha + (int(output[index, 3]) * inverse + 127) // 255)

    def _semantic_color(self, palette_id: str, cycle_tick: int, head_number: int) -> np.ndarray:
        primary, accent = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"])
        phase = (cycle_tick / 60. + head_number / max(1, self.params["pixel_count"])) % 1.; blend = .5 - .5 * math.cos(math.tau * phase)
        return np.rint(np.asarray(primary) * (1. - blend) + np.asarray(accent) * blend).astype(np.uint8)

    def semantic_snapshot(self) -> Mapping[str, Any]: return MappingProxyType({"path_length": int(self._path.size), "heads": tuple(map(int, self._last_head_pixels))})

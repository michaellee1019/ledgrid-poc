# FOR TESTING DO NOT REMOVE.
"""Host mapping diagnostic: one logical column at a time.

FOR TESTING DO NOT REMOVE.

Lights the current column white for one second, then blacks the wall for one
second, then advances to the next logical column. Use this to verify physical
left-to-right strip order on the installed 33-column wall.
"""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene, TimingAdapter


class ColumnSequenceAnimation(AnimationBase):
    """FOR TESTING DO NOT REMOVE. Sequential one-column mapping flash."""

    ANIMATION_NAME = "Column Sequence"
    ANIMATION_DESCRIPTION = (
        "FOR TESTING DO NOT REMOVE. Flash one column white for 1s, black 1s, then the next."
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "column_sequence", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "preserve"
    TIMING_ADAPTER = TimingAdapter.SCALED_CONTEXT
    VIBE_COLOR_POLICY = "preserve"
    CAPABILITIES = frozenset(("scaled_context",))
    PLANT_MODIFIER_SUPPORT = frozenset()
    DEFAULTS = MappingProxyType({"hold_seconds": 1.0, "pause_seconds": 1.0, "white_level": 1.0})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE,
        timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY,
        plant_capabilities=("none",), fidelity_exceptions=("authored_media_colors",), defaults=DEFAULTS,
        parameter_normalizer=lambda values: ColumnSequenceAnimation._normalized_parameters(values),
    )

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._context: ResolvedScene | None = None
        self._last_key: tuple[Any, ...] | None = None
        self._active_column: int | None = None

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(values)
        unknown = sorted(set(supplied) - set(cls.DEFAULTS))
        if unknown:
            raise ValueError(f"Column Sequence does not accept non-local parameters: {unknown!r}")
        result = dict(cls.DEFAULTS)
        result.update(supplied)
        for name, low, high in (("hold_seconds", 0.1, 10.0), ("pause_seconds", 0.0, 10.0), ("white_level", 0.0, 1.0)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not low <= float(value) <= high:
                raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        return result

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "hold_seconds": {"type": "float", "min": 0.1, "max": 10.0, "default": 1.0, "description": "Seconds the current column stays white"},
            "pause_seconds": {"type": "float", "min": 0.0, "max": 10.0, "default": 1.0, "description": "Seconds of all-black between columns"},
            "white_level": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0, "description": "White level of the active column"},
        }

    def update_parameters(self, values: Mapping[str, Any]) -> None:
        self.params = self._normalized_parameters({**self.params, **dict(values)})
        self._last_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        if new.descriptor.component_id != self.COMPONENT_ID:
            raise ValueError("Column Sequence received another component context")
        self._context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        context = self._context
        params = self._normalized_parameters(context.parameters if context else self.params)
        self.params = params
        elapsed = max(0.0, float(context.phase_time if context else time_elapsed))
        hold = params["hold_seconds"]
        pause = params["pause_seconds"]
        cycle = max(0.001, hold + pause)
        strips = int(self.controller.strip_count)
        leds = int(self.controller.leds_per_strip)
        column = int(elapsed / cycle) % max(1, strips)
        active = column if (elapsed % cycle) < hold else None
        level = max(0, min(255, int(round(255.0 * params["white_level"]))))
        key = (column, active, level, strips, leds)
        if key == self._last_key:
            return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        self._pixels.fill(0)
        if active is not None:
            start = active * leds
            self._pixels[start:start + leds] = level
        self._last_key = key
        self._active_column = active
        return RenderedFrame(self._pixels, changed=True)

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"active_column": self._active_column, "hold_seconds": self.params["hold_seconds"], "pause_seconds": self.params["pause_seconds"]})

    def get_runtime_stats(self) -> dict[str, Any]:
        return dict(self.cadence_snapshot())

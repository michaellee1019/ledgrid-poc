"""Scene v2 ASCII Drop: a compact falling-terminal instrument."""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.point_pulse import AimedPoint, PointPulse


class AsciiDropAnimation(AnimationBase):
    PREFERRED_PARAMETERS = ('fall_speed', 'density')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Release a column of marks"}
    POINT_ACTION_AXES = {"primary": ("x",)}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}

    ANIMATION_NAME = "ASCII Drop"
    ANIMATION_DESCRIPTION = "Falling terminal marks patterned by the selected phrase"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "3.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "ascii_drop", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    DEFAULTS = MappingProxyType({"phrase": "HELLO", "story": "terminal", "fall_speed": 13.0, "density": .45, "seed": 8088})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=1, provider="python", role="animation", timing_policy="scaled_context", alpha_behavior="opaque", palette_policy="semantic", plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    STORIES = ("terminal", "matrix", "love", "datastream", "overflow")
    _COLORS = {"terminal": ((5, 2, 0), (255, 150, 30)), "matrix": ((0, 3, 1), (30, 255, 98)), "love": ((13, 0, 8), (255, 60, 165)), "datastream": ((0, 4, 16), (30, 228, 255)), "overflow": ((8, 1, 20), (230, 235, 255))}

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params, self.params = dict(self.DEFAULTS), self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._context: ResolvedScene | None = None
        self._key: tuple[Any, ...] | None = None
        self._pulse = PointPulse(1.25)

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor: return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown: raise ValueError(f"ASCII Drop received non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS); result.update(values)
        phrase = result["phrase"]
        if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 48: raise ValueError("phrase must be non-empty text up to 48 characters")
        result["phrase"] = phrase.upper()
        if result["story"] not in cls.STORIES: raise ValueError(f"story must be one of {cls.STORIES!r}")
        for name, low, high in (("fall_speed", 0.0, 36.0), ("density", .08, 1.0)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not low <= float(value) <= high: raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        if isinstance(result["seed"], bool) or not isinstance(result["seed"], int) or not 0 <= result["seed"] <= 999999: raise ValueError("seed must be an integer from 0 to 999999")
        return result

    @classmethod
    def get_parameter_schema(cls) -> dict[str, dict[str, Any]]:
        return {"phrase": {"type": "str", "default": "HELLO", "description": "Text that patterns the placement and color of the rain"}, "story": {"type": "str", "options": list(cls.STORIES), "default": "terminal", "description": "Pixel-story color language"}, "fall_speed": {"type": "float", "min": 0, "max": 36, "default": 13, "description": "Pixels per Scene second, zero holds the rain"}, "density": {"type": "float", "min": .08, "max": 1, "default": .45, "description": "How full the terminal rain feels"}, "seed": {"type": "int", "min": 0, "max": 999999, "default": 8088, "description": "Repeatable stream arrangement"}}

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        self.params = self._normalized_parameters({**self.params, **dict(new_params)}); self._key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        if new.descriptor.component_id != self.COMPONENT_ID or new.palette is None: raise ValueError("ASCII Drop requires its semantic Scene v2 context")
        self._context = new
    def set_presentation_context(self, context: ResolvedScene) -> None: self.on_presentation_context_changed(self._context, context)
    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame: self.set_presentation_context(context); return self.generate_frame(context.phase_time, self.frame_count)

    def handle_interaction(self, kind, x, y, strength=1.0):
        point = AimedPoint.from_input(kind, x, y, strength, self.width, self.height)
        return point is not None and self._pulse.trigger(point)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        params = self._normalized_parameters(self._context.parameters if self._context else self.params)
        palette = str(self._context.palette["palette_id"]) if self._context else "neutral"
        phase = max(0., float(self._context.phase_time if self._context else time_elapsed))
        tick = int(phase * 20.0); key = (tick, palette, tuple(params.items()))
        point, progress = self._pulse.sample(tick / 20.0)
        if point is not None:
            key += (point, progress)
        if key == self._key: return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        background, ink = self._COLORS[params["story"]]
        tint = {"neutral": (1., 1., 1.), "mist": (.65, .85, 1.), "spectrum": (1., .55, 1.), "ember": (1., .55, .35), "rainbow": (1., .72, .42)}.get(palette, (1., 1., 1.))
        ink_rgb, tint_rgb = np.asarray(ink, dtype=np.float32), np.asarray(tint)
        ink_levels = tuple(
            np.clip(ink_rgb * (0.35 + 0.65 * (level / 4)) * tint_rgb, 0, 255).astype(np.uint8)
            for level in range(5)
        )
        # Falling glyph rows are top-down; canonical LED indices are bottom-up.
        canvas = self._pixels.reshape(self.width, self.height, 3)[:, ::-1]; canvas[:] = background
        rng = np.random.default_rng(int(params["seed"]) + (tick // 2 if params["fall_speed"] else 0))
        count = max(4, int(self.width * 8 * params["density"])); xs = rng.integers(0, self.width, count); offsets = rng.integers(0, self.height, count)
        speed = float(params["fall_speed"])
        phrase = params["phrase"]
        for i, x in enumerate(xs):
            glyph = ord(phrase[i % len(phrase)])
            x = (int(x) + glyph + i) % self.width
            y = int((offsets[i] + glyph * 3 + phase * speed * (1 + (i % 4) * .18)) % self.height)
            color = ink_levels[glyph % 5]
            for dx, dy in ((0, 0), (0, 1), (1, 1), (-1, 1), (0, 2)):
                if 0 <= x + dx < self.width:
                    canvas[x + dx, (y + dy) % self.height] = color
        if point is not None:
            for offset, char in enumerate(phrase[:8]):
                row = int(progress * self.height) - offset * 4
                if 0 <= row < self.height:
                    color = ink_levels[ord(char) % 5].astype(float) * point.strength * (1.0 - progress)
                    canvas[max(0, point.x - 1):point.x + 2, row] = np.maximum(
                        canvas[max(0, point.x - 1):point.x + 2, row], color,
                    )
        self.params, self._key = params, key
        return RenderedFrame(self._pixels, changed=True)

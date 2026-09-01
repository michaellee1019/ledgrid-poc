"""Scene-v2 foundation for the compact mathematical light sculptures."""

from __future__ import annotations

import math
from abc import ABC
from functools import lru_cache
from typing import Any, Dict, Mapping

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene


SEMANTIC_PALETTES = {
    "neutral": np.array(((2, 10, 18), (24, 148, 132), (150, 255, 218)), np.float32),
    "mist": np.array(((3, 9, 20), (40, 102, 142), (170, 228, 245)), np.float32),
    "spectrum": np.array(((15, 3, 34), (84, 38, 194), (54, 238, 230)), np.float32),
    "ember": np.array(((18, 3, 2), (156, 42, 14), (255, 202, 92)), np.float32),
}
_DIRECT_PALETTES = {"quiet": "mist", "showcase": "spectrum", "night": "neutral", "bright": "mist", "pastel": "spectrum", "synthwave": "spectrum", "candlelight": "ember", "aurora": "neutral"}


class CadencedSculpture(AnimationBase, ABC):
    """Deterministic opaque Scene-v2 animation with source-rate caching.

    Palette, pace, and final brightness are supplied by the resolved scene.
    Legacy preset presentation fields remain readable but cannot reclaim those
    global responsibilities from the Scene-v2 boundary.
    """

    SOURCE_FPS = 24.0
    COMPONENT_ID = ""
    COMPONENT_DEFAULTS: Mapping[str, Any] = {}
    # A one-way importer for checked historical preset rows.  Components do
    # not expose or retain these fields after normalisation.
    LEGACY_PRESET_KEYS = frozenset()

    def __init__(self, controller, config: Dict[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.COMPONENT_DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self._revision = 0; self._render_key = None; self._cached_pixels = None; self._last_sim_tick = -1
        strips, leds = self.get_strip_info(); self._shape = (strips, leds)
        self._x, self._y = np.meshgrid(np.linspace(-1., 1., strips, dtype=np.float32), np.linspace(-1., 1., leds, dtype=np.float32), indexing="ij")
        self.rng = np.random.default_rng(int(self.params["seed"]))
        self._presentation_context: ResolvedScene | None = None

    @classmethod
    @lru_cache(maxsize=None)
    def component_descriptor(cls) -> ComponentDescriptor:
        return ComponentDescriptor(component_id=cls.COMPONENT_ID, version=1, provider="python", role="animation", timing_policy="scaled_context", alpha_behavior="opaque", palette_policy="semantic", plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=cls.COMPONENT_DEFAULTS, parameter_normalizer=cls._normalized_parameters)

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping): raise ValueError(f"{cls.COMPONENT_ID} parameters must be an object")
        legacy = {"brightness", "mood", "background", "plant_aware", "plant_modifiers"}
        unknown = set(values) - set(cls.COMPONENT_DEFAULTS) - legacy - set(cls.LEGACY_PRESET_KEYS)
        if unknown: raise ValueError(f"{cls.COMPONENT_ID} does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.COMPONENT_DEFAULTS)
        for name, value in values.items():
            if name in legacy or name in cls.LEGACY_PRESET_KEYS: continue
            default = result[name]
            if isinstance(default, bool):
                if type(value) is not bool: raise ValueError(f"{name} must be boolean")
                result[name] = value
            elif isinstance(default, int):
                if isinstance(value, bool) or not isinstance(value, int): raise ValueError(f"{name} must be an integer")
                result[name] = int(value)
            elif isinstance(default, float):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)): raise ValueError(f"{name} must be a finite number")
                result[name] = float(value)
            elif isinstance(default, str):
                if not isinstance(value, str): raise ValueError(f"{name} must be a string")
                result[name] = value
        cls._validate_local_parameters(result)
        return result

    @classmethod
    def _validate_local_parameters(cls, values: Mapping[str, Any]) -> None:
        for name, low, high in (("motion", 0., 2.), ("density", .05, 2.), ("background_level", 0., 1.)):
            if not low <= float(values[name]) <= high: raise ValueError(f"{name} is out of range")
        if not 0 <= int(values["seed"]) <= 999999: raise ValueError("seed is out of range")

    def get_parameter_schema(self):
        return {
            "motion": {"type":"float", "min":0., "max":2., "default":self.COMPONENT_DEFAULTS["motion"], "description":"Local structural motion"},
            "density": {"type":"float", "min":.05, "max":2., "default":self.COMPONENT_DEFAULTS["density"], "description":"Local structural density"},
            "background_level": {"type":"float", "min":0., "max":1., "default":self.COMPONENT_DEFAULTS["background_level"], "description":"Local field depth"},
            "seed": {"type":"int", "min":0, "max":999999, "default":self.COMPONENT_DEFAULTS["seed"], "description":"Deterministic arrangement seed"},
        }

    def update_parameters(self, new_params):
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        old_seed = self.params.get("seed"); self.params = candidate; self._revision += 1; self._render_key = None
        if candidate["seed"] != old_seed:
            self.rng = np.random.default_rng(int(candidate["seed"])); self.reset_simulation()

    def _install_resolved_scene(self, new: ResolvedScene) -> None:
        if new.descriptor.component_id != self.COMPONENT_ID: raise ValueError("Sculpture received context for another component")
        candidate = self._normalized_parameters(new.parameters)
        if candidate != self.params: self.update_parameters(candidate)
        self._presentation_context = new

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        self._install_resolved_scene(new)

    def set_presentation_context(self, context: ResolvedScene) -> None:
        old = self._presentation_context
        self._install_resolved_scene(context)
        hook = type(self).on_presentation_context_changed
        if (
            hook is not CadencedSculpture.on_presentation_context_changed
            and (old is None or old.digest != context.digest)
        ):
            hook(self, old, context)
    def render_resolved_scene(self, context: ResolvedScene): self.set_presentation_context(context); return self.generate_frame(context.phase_time, self.frame_count)
    def reset_simulation(self): self._last_sim_tick = -1
    def source_tick(self, time_elapsed: float) -> int: return max(0, int(max(0., float(time_elapsed)) * self.SOURCE_FPS + 1.e-9))

    def begin_frame(self, time_elapsed: float):
        tick = self.source_tick(time_elapsed); key = (tick, self._revision, self._palette_id())
        if key == self._render_key and self._cached_pixels is not None: return tick, self.rendered_frame(self._cached_pixels, changed=False)
        return tick, None

    def finish_frame(self, tick: int, logical_rgb: np.ndarray):
        frame = self.next_frame_buffer(clear=False); level = float(self.params["background_level"])
        if level:
            palette = CadencedSculpture.palette(self); logical_rgb += (palette[1] * .10 + palette[2] * .04) * level
        np.clip(logical_rgb, 0., 255., out=logical_rgb)
        np.copyto(frame, np.ascontiguousarray(logical_rgb).reshape((-1, 3)), casting="unsafe")
        self._cached_pixels = frame; self._render_key = (tick, self._revision, self._palette_id())
        return self.rendered_frame(frame)

    def advance_bounded(self, tick: int, callback, max_steps: int = 12):
        if self._last_sim_tick < 0: self._last_sim_tick = tick - 1
        for step in range(max(self._last_sim_tick + 1, tick - max_steps + 1), tick + 1): callback(step)
        self._last_sim_tick = tick

    def _palette_id(self, mood: str | None = None) -> str:
        if self._presentation_context is not None: return str(self._presentation_context.palette["palette_id"])
        return _DIRECT_PALETTES.get(str(mood if mood is not None else self._authored_config.get("mood", "quiet")), "mist")
    def palette(self, mood: str | None = None): return SEMANTIC_PALETTES.get(self._palette_id(mood), SEMANTIC_PALETTES["neutral"])
    def colorize(self, value: np.ndarray, accent: np.ndarray | None = None):
        p = CadencedSculpture.palette(self); v = np.clip(value, 0., 1.)[..., None]; mid = np.minimum(v * 2., 1.); high = np.maximum(v * 2. - 1., 0.)
        rgb = p[0] + (p[1] - p[0]) * mid + (p[2] - p[1]) * high
        if accent is not None: rgb += accent[..., None] * p[2] * .35
        return rgb.astype(np.float32, copy=False)

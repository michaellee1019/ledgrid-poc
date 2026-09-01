"""Shared bounded renderer for quiet, tall-format procedural scenes."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import ResolvedScene

PALETTES = {
    "moonlit": ((2, 5, 13), (25, 43, 68), (142, 169, 184)),
    "predawn": ((8, 5, 18), (69, 41, 73), (210, 126, 91)),
    "ochre": ((12, 5, 3), (111, 45, 13), (224, 139, 49)),
    "mars": ((10, 3, 4), (91, 24, 22), (191, 74, 44)),
    "natural": ((2, 6, 17), (52, 102, 151), (242, 178, 104)),
    "ember": ((5, 4, 12), (56, 39, 72), (237, 132, 63)),
    "sleeper": ((1, 3, 9), (13, 28, 47), (111, 79, 53)),
    "daylight": ((28, 48, 72), (105, 178, 222), (244, 251, 255)),
    "pastel": ((35, 24, 58), (174, 128, 205), (255, 214, 226)),
    "synthwave": ((18, 5, 45), (193, 35, 151), (45, 224, 255)),
    "candlelight": ((30, 9, 2), (178, 74, 18), (255, 218, 128)),
    "aurora": ((3, 18, 29), (27, 154, 134), (168, 255, 221)),
}

BACKGROUND_LEVELS = {"none": 0.0, "soft": 0.35, "luminous": 0.7, "radiant": 1.0}


class LongformSceneBase(AnimationBase):
    """Allocation-conscious analytic scenes with source-rate throttling."""

    SCENE = "fog"
    DEFAULT_MOOD = "moonlit"
    MOODS: Tuple[str, ...] = ("moonlit", "predawn")
    PLANT_MODIFIER_SUPPORT = frozenset({"illuminate", "shadow", "refract"})
    COMPONENT_ID = ""
    COMPONENT_DEFAULTS: Mapping[str, Any] = MappingProxyType({})

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "speed": 1.0,
            "brightness": 0.42,
            "background": "soft",
            "background_level": 0.18,
            "motion": 0.35,
            "density": 0.5,
            "mood": self.DEFAULT_MOOD,
            "render_fps": 24,
            "seed": 1729,
        })
        self.default_params.update(self.scene_defaults())
        self.params = {**self.default_params, **self.config}
        self._plant_modifier_state = self._resolve_plant_modifier_state()
        width, height = self.get_strip_info()
        self.width, self.height = width, height
        self._x = np.linspace(0.0, 1.0, width, dtype=np.float32)[:, None]
        self._y = np.linspace(0.0, 1.0, height, dtype=np.float32)[None, :]
        self._field = np.empty((width, height), dtype=np.float32)
        self._rgb = np.empty((width, height, 3), dtype=np.float32)
        self._rng = np.random.default_rng(int(self.params.get("seed", 1729)))
        self._phases = self._rng.uniform(0, 2 * np.pi, 16).astype(np.float32)
        self._cached = None
        self._last_tick = None
        self._last_key = None
        self._presentation_context: ResolvedScene | None = None

    @classmethod
    @lru_cache(maxsize=None)
    def component_descriptor(cls) -> ComponentDescriptor:
        return ComponentDescriptor(
            component_id=cls.COMPONENT_ID, version=1, provider="python", role="animation",
            timing_policy="scaled_context", alpha_behavior="opaque", palette_policy="semantic",
            plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=cls.COMPONENT_DEFAULTS,
            parameter_normalizer=cls._normalized_parameters,
        )

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise ValueError("Atmosphere parameters must be an object")
        defaults = dict(cls.COMPONENT_DEFAULTS)
        allowed = set(defaults) | {"plant_aware", "plant_modifiers", "brightness", "speed"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown {cls.COMPONENT_ID} parameter {sorted(unknown)[0]!r}")
        result = dict(defaults)
        for name, value in values.items():
            if name in {"brightness", "speed"}:
                continue
            if name == "plant_aware":
                if type(value) is not bool: raise ValueError("plant_aware must be boolean")
                continue
            elif name == "plant_modifiers":
                PlantModifierState.from_payload(value)
                continue
            elif name == "mood":
                if value not in PALETTES: raise ValueError("mood is not supported")
                result[name] = value
            elif name == "background":
                if value not in BACKGROUND_LEVELS: raise ValueError("background is not supported")
                result[name] = value
            elif name == "seed":
                if type(value) is not int or not 0 <= value <= 2147483647: raise ValueError("seed is out of range")
                result[name] = value
            elif name in {"motion", "density", "background_level"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1: raise ValueError(f"{name} is out of range")
                result[name] = float(value)
            elif name == "render_fps":
                if type(value) is not int or not 5 <= value <= 40: raise ValueError("render_fps is out of range")
                result[name] = value
            elif name in {"hour", "time_offset", "time_scale"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)): raise ValueError(f"{name} must be numeric")
                limits = {"hour": (-1.0, 23.999), "time_offset": (-12.0, 14.0), "time_scale": (0.0, 3600.0)}
                low, high = limits[name]
                if not low <= float(value) <= high: raise ValueError(f"{name} is out of range")
                result[name] = float(value)
        return result

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        if new.descriptor.component_id != self.COMPONENT_ID: raise ValueError("Atmosphere received a context for another component")
        candidate = self._normalized_parameters(new.parameters)
        if candidate != self.params:
            self.update_parameters(candidate)
            self.params = candidate
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene):
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def scene_defaults(self) -> Dict[str, Any]:
        return {}

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema["speed"].update({"min": 0.0, "max": 3.0, "default": 1.0})
        schema["brightness"].update({"default": 0.42, "max": 1.0})
        schema.update({
            "motion": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.35,
                       "description": "Macro motion intensity without changing scene density"},
            "density": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.5,
                        "description": "Amount of scene detail without changing motion"},
            "mood": {"type": "str", "default": self.DEFAULT_MOOD,
                     "options": list(dict.fromkeys((*self.MOODS, "daylight", "pastel", "synthwave", "candlelight", "aurora"))),
                     "description": "Luminance-capped color family"},
            "background": {"type": "str", "default": "soft", "options": list(BACKGROUND_LEVELS),
                           "description": "Ambient light behind the primary scene"},
            "background_level": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.18,
                                 "description": "Strength of the ambient background"},
            "render_fps": {"type": "int", "min": 5, "max": 40, "default": 24,
                           "description": "Source render cadence"},
            "seed": {"type": "int", "min": 0, "max": 2147483647, "default": 1729,
                     "description": "Deterministic scene seed"},
        })
        schema.update(self.scene_schema())
        return schema

    def scene_schema(self) -> Dict[str, Dict[str, Any]]:
        return {}

    def update_parameters(self, new_params: Dict[str, Any]):
        old_seed = int(self.params.get("seed", 1729))
        super().update_parameters(new_params)
        if int(self.params.get("seed", 1729)) != old_seed:
            self._rng = np.random.default_rng(int(self.params["seed"]))
            self._phases = self._rng.uniform(0, 2 * np.pi, 16).astype(np.float32)
        self._last_key = None

    def generate_frame(self, time_elapsed: float, frame_count: int):
        fps = max(5, min(40, int(self.params.get("render_fps", 24))))
        tick = int(max(0.0, float(time_elapsed)) * fps)
        key = self._visual_key()
        if self._cached is not None and tick == self._last_tick and key == self._last_key:
            return self.rendered_frame(self._cached, changed=False)

        t = tick / fps * max(0.0, float(self.params.get("speed", 1.0)))
        motion = min(1.0, max(0.0, float(self.params.get("motion", 0.35))))
        density = min(1.0, max(0.0, float(self.params.get("density", 0.5))))
        self.render_scene(t, motion, density)
        self._apply_background()
        self._apply_plant_presentation(t)
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 0.42))))
        self._rgb *= brightness
        np.clip(self._rgb, 0.0, 255.0, out=self._rgb)
        frame = self.next_frame_buffer(clear=False)
        np.copyto(frame, self._rgb.reshape(-1, 3), casting="unsafe")
        self._cached = frame
        self._last_tick = tick
        self._last_key = key
        return self.rendered_frame(frame)

    def _visual_key(self):
        state = self.plant_modifier_state()
        return (
            float(self.params.get("brightness", 0.42)),
            float(self.params.get("motion", 0.35)),
            float(self.params.get("density", 0.5)),
            str(self.params.get("mood", self.DEFAULT_MOOD)),
            str(self.params.get("background", "soft")),
            float(self.params.get("background_level", .18)),
            int(self.params.get("render_fps", 24)),
            state,
        ) + self.scene_key()

    def scene_key(self) -> tuple:
        return ()

    def palette(self):
        mood = str(self.params.get("mood", self.DEFAULT_MOOD))
        return tuple(np.asarray(c, dtype=np.float32) for c in PALETTES.get(mood, PALETTES[self.DEFAULT_MOOD]))

    def colorize(self, value: np.ndarray, accent: Optional[np.ndarray] = None):
        low, mid, high = self.palette()
        v = np.clip(value, 0.0, 1.0)
        first = np.minimum(v * 2.0, 1.0)[..., None]
        second = np.maximum(v * 2.0 - 1.0, 0.0)[..., None]
        self._rgb[:] = low + (mid - low) * first
        self._rgb += (high - mid) * second
        if accent is not None:
            self._rgb += np.clip(accent, 0.0, 1.0)[..., None] * high * 0.35

    def _apply_background(self):
        style = str(self.params.get("background", "soft"))
        level = float(np.clip(self.params.get("background_level", .18), 0.0, 1.0))
        strength = BACKGROUND_LEVELS.get(style, BACKGROUND_LEVELS["soft"]) * level
        if strength <= 0.0:
            return
        low, mid, high = self.palette()
        color = mid * (1.0 - .38 * strength) + high * (.18 + .38 * strength)
        vertical = (.62 + .38 * (1.0 - self._y))[..., None]
        self._rgb += color * vertical * (.16 * strength)

    def render_scene(self, t: float, motion: float, density: float):
        if self.SCENE == "fog":
            self._render_fog(t, motion, density)
        elif self.SCENE == "desert":
            self._render_desert(t, motion, density)
        elif self.SCENE == "circadian":
            self._render_circadian(t, motion, density)
        else:
            self._render_train(t, motion, density)

    def _render_fog(self, t: float, motion: float, density: float):
        drift = t * (0.008 + 0.045 * motion)
        fog = (0.42 + 0.18 * np.sin(2 * np.pi * (self._y * 2.2 - drift) + self._phases[0])
               + self._x * 0.0)
        fog += 0.12 * np.sin(2 * np.pi * (self._x * 1.4 + self._y * 0.7 + drift * 0.6) + self._phases[1])
        fog += 0.07 * np.sin(2 * np.pi * (self._x * 3.1 - self._y * 1.6 - drift * 1.4) + self._phases[2])
        fog = np.clip((fog - (0.48 - density * 0.22)) * 1.35, 0.0, 1.0)
        moon_x = 0.68 + 0.08 * np.sin(t * 0.006)
        moon_y = 0.18 + 0.04 * np.cos(t * 0.004)
        halo = np.exp(-((self._x - moon_x) ** 2 / 0.035 + (self._y - moon_y) ** 2 / 0.018))
        ridge = np.clip((self._y - (0.73 + 0.08 * np.sin(self._x * 9 + self._phases[3]))) * 25, 0, 1)
        value = 0.04 + fog * (0.18 + 0.34 * halo) + halo * 0.38
        value *= 1.0 - ridge * 0.88
        self.colorize(value, halo * (1.0 - fog))

    def _render_desert(self, t: float, motion: float, density: float):
        drift = t * (0.004 + motion * 0.025)
        dune1 = 0.68 + 0.08 * np.sin(self._x * 8.0 + drift + self._phases[0])
        dune2 = 0.82 + 0.05 * np.sin(self._x * 13.0 - drift * 0.7 + self._phases[1])
        body1 = np.clip((self._y - dune1) * 18.0, 0.0, 1.0)
        body2 = np.clip((self._y - dune2) * 28.0, 0.0, 1.0)
        crest = np.exp(-((self._y - dune1) / 0.018) ** 2)
        haze = (1.0 - self._y) * (0.08 + 0.08 * np.sin(self._x * 3 + drift))
        grains = np.sin((self._x * 73 - self._y * 19 + drift * 35 + self._phases[2]) * 2 * np.pi)
        grains = (grains > (0.995 - density * 0.025)).astype(np.float32) * crest
        value = 0.025 + haze + body1 * 0.28 + body2 * 0.18 + crest * 0.34 + grains * 0.42
        self.colorize(value, crest * 0.4 + grains)

    def _current_hour(self, t: float) -> float:
        fixed = float(self.params.get("hour", -1.0))
        if fixed >= 0:
            return (fixed + t * float(self.params.get("time_scale", 1.0)) / 3600.0) % 24.0
        now = datetime.now().astimezone()
        return (now.hour + now.minute / 60 + now.second / 3600 + float(self.params.get("time_offset", 0.0))) % 24

    def _circadian_palette(self):
        """Return the authored night, daylight, and twilight colors."""
        return (
            np.asarray((2, 5, 18), dtype=np.float32),
            np.asarray((90, 151, 207), dtype=np.float32),
            np.asarray((240, 104, 53), dtype=np.float32),
        )

    def _render_circadian(self, t: float, motion: float, density: float):
        hour = self._current_hour(t)
        sun = max(0.0, np.sin((hour - 6.0) / 12.0 * np.pi))
        twilight = max(0.0, np.sin((hour - 4.5) / 15.0 * np.pi))
        horizon = np.exp(-((self._y - 0.73) / 0.25) ** 2)
        sky = (0.035 + sun * (0.24 + 0.46 * (1.0 - self._y))
               + twilight * horizon * 0.24 + self._x * 0.0)
        cloud = np.sin(self._x * 11 + self._y * 5 - t * (0.006 + motion * 0.025) + self._phases[1])
        cloud += 0.5 * np.sin(self._x * 23 - self._y * 7 - t * 0.012 + self._phases[2])
        sky *= 1.0 - np.clip(cloud - (1.1 - density * 0.7), 0, 1) * 0.28
        stars = (np.sin(self._x * 173 + self._y * 271 + self._phases[4]) > 0.992 - density * 0.01)
        sky += stars * (1.0 - sun) * 0.32
        low, day, warm = self._circadian_palette()
        self._rgb[:] = low + day * sky[..., None]
        self._rgb += warm * (horizon * twilight * (1.0 - sun * 0.6))[..., None] * 0.32

    def _render_train(self, t: float, motion: float, density: float):
        travel = t * (0.012 + 0.07 * motion)
        sky = 0.03 + (1.0 - self._y) * 0.09
        far = 0.72 + 0.06 * np.sin(self._x * 8 + travel + self._phases[0])
        near = 0.83 + 0.07 * np.sin(self._x * 17 + travel * 2.2 + self._phases[1])
        value = sky + np.clip((self._y - far) * 5, 0, 1) * 0.05
        value += np.clip((self._y - near) * 9, 0, 1) * 0.08
        pole_phase = np.mod(self._x * (5 + density * 8) + travel * 1.8, 1.0)
        poles = pole_phase < 0.035
        town_band = (self._y > near - 0.11) & (self._y < near - 0.02)
        windows = town_band & (np.sin(self._x * 113 + self._y * 239 + self._phases[3]) > 0.94 - density * 0.08)
        value = np.where(poles & (self._y > 0.22), value * 0.15, value)
        self.colorize(value, windows.astype(np.float32) * 1.5)

    def _apply_plant_presentation(self, t: float):
        illuminate = self.plant_modifier_strength("illuminate")
        shadow = self.plant_modifier_strength("shadow")
        refract = self.plant_modifier_strength("refract")
        if illuminate <= 0 and shadow <= 0 and refract <= 0:
            return
        masks = self.get_plant_masks()
        logical = self._rgb.reshape(-1, 3)
        if refract > 0:
            normals = np.column_stack((masks.normal_x.ravel(), masks.normal_y.ravel()))
            shifted = np.roll(logical, 1, axis=0)
            weight = np.clip(np.linalg.norm(normals, axis=1) * refract * 0.35, 0, 0.35)
            logical[:] = logical * (1.0 - weight[:, None]) + shifted * weight[:, None]
        if shadow > 0:
            logical[masks.foliage_flat] *= 1.0 - 0.62 * shadow
            logical[masks.globes_flat] *= 1.0 - 0.86 * shadow
        if illuminate > 0:
            edge = masks.foliage_edge.ravel() | masks.globe_edge.ravel()
            logical[edge] += np.asarray((32, 48, 58), dtype=np.float32) * illuminate

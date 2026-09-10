"""Shared allocation-conscious renderer for tall procedural atmospheres."""

from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import OverlayFrame, ResolvedScene


MOOD_PALETTES: Mapping[str, Mapping[str, tuple[float, float, float]]] = {
    "moonlit": {"low": (1, 5, 14), "mid": (8, 38, 64), "high": (76, 178, 205)},
    "boreal": {"low": (1, 8, 13), "mid": (7, 61, 52), "high": (60, 214, 151)},
    "violet": {"low": (6, 2, 18), "mid": (48, 17, 80), "high": (191, 94, 219)},
    "ember": {"low": (12, 3, 1), "mid": (91, 28, 8), "high": (238, 126, 42)},
    "garden": {"low": (2, 8, 9), "mid": (16, 45, 31), "high": (129, 176, 116)},
    "daylight": {"low": (28, 48, 72), "mid": (105, 178, 222), "high": (244, 251, 255)},
    "pastel": {"low": (35, 24, 58), "mid": (174, 128, 205), "high": (255, 214, 226)},
    "synthwave": {"low": (18, 5, 45), "mid": (193, 35, 151), "high": (45, 224, 255)},
    "candlelight": {"low": (30, 9, 2), "mid": (178, 74, 18), "high": (255, 218, 128)},
    "aurora": {"low": (3, 18, 29), "mid": (27, 154, 134), "high": (168, 255, 221)},
}

# Scene v2 owns the palette family for the migrated atmosphere instruments.
# Their local ``mood`` control remains useful as a tonal/contrast choice, but
# it deliberately cannot replace the Scene's semantic hue roles.
SEMANTIC_PALETTES: Mapping[str, Mapping[str, tuple[float, float, float]]] = {
    "neutral": {"low": (2, 10, 18), "mid": (24, 148, 132), "high": (150, 255, 218)},
    "mist": {"low": (3, 9, 20), "mid": (40, 102, 142), "high": (170, 228, 245)},
    "spectrum": {"low": (15, 3, 34), "mid": (84, 38, 194), "high": (54, 238, 230)},
    "ember": {"low": (18, 3, 2), "mid": (156, 42, 14), "high": (255, 202, 92)},
}

# Per-band gains and analytic tone curves preserve each instrument's authored
# local-mood range without introducing a second palette authority.  The curves
# reshape luminance/contrast only; every RGB value still interpolates Scene
# semantic roles.
MOOD_TONAL_GAINS: Mapping[str, tuple[float, float, float]] = {
    "moonlit": (.72, .88, 1.00), "boreal": (.70, 1.00, .92),
    "violet": (.78, .84, 1.08), "ember": (.82, .98, 1.10),
    "garden": (.72, .93, .88), "daylight": (1.12, 1.08, 1.04),
    "pastel": (1.03, .92, 1.12), "synthwave": (.76, 1.06, 1.16),
    "candlelight": (.92, .95, 1.08), "aurora": (.68, 1.08, 1.02),
}

# field_bias, field_gain, background_gain, accent_gain
MOOD_TONAL_CURVES: Mapping[str, tuple[float, float, float, float]] = {
    "moonlit": (-.020, .70, .70, .80), "boreal": (.025, .92, .90, 1.00),
    "violet": (.160, 1.25, 1.15, 1.30), "ember": (.055, 1.20, .95, 1.20),
    "garden": (.015, .84, .88, .95), "daylight": (.145, 1.05, 1.30, 1.05),
    "pastel": (.190, .90, 1.35, .92), "synthwave": (.110, 1.22, 1.08, 1.35),
    "candlelight": (.090, 1.02, 1.18, 1.10), "aurora": (.070, 1.12, 1.05, 1.20),
}

BACKGROUND_LEVELS = {"none": 0.0, "soft": 0.35, "luminous": 0.7, "radiant": 1.0}


class ProceduralAtmosphereBase(AnimationBase):
    """Source-rate cached analytic simulation composed for the installed wall."""

    SCENE = ""
    DEFAULT_MOOD = "moonlit"
    DEFAULT_SEED = 1701
    PLANT_MODIFIER_SUPPORT = frozenset()
    COMPONENT_ID = ""
    COMPONENT_DEFAULTS: Mapping[str, Any] = MappingProxyType({})
    # Only the three accepted adapters opt in.  Other legacy atmospheres keep
    # their exact component-local palette behavior until separately reviewed.
    SCENE_SEMANTIC_PALETTE = False
    # Rain and Waterfall opt into the Scene v2 foreground plane below.  Keep
    # the remaining atmosphere family on its original opaque RGB path until a
    # component-specific composition review accepts a change.
    PREMULTIPLIED_RGBA = False
    FRAME_FORMAT = "rgb_uint8_strip_major"

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "motion": 0.42,
            "density": 0.46,
            "mood": self.DEFAULT_MOOD,
            "brightness": 0.46,
            "background": "soft",
            "background_level": 0.18,
            "source_fps": 30.0,
            "seed": self.DEFAULT_SEED,
        })
        self.params = {**self.default_params, **self.config}
        self._x = np.linspace(0.0, 1.0, self.width, dtype=np.float32)[:, None]
        self._y = np.linspace(0.0, 1.0, self.height, dtype=np.float32)[None, :]
        self._rgb = np.empty((self.width, self.height, 3), dtype=np.float32)
        self._field = np.empty((self.width, self.height), dtype=np.float32)
        self._coverage = np.zeros((self.width, self.height), dtype=np.float32)
        self._cached_frame = None
        self._rgba_buffers = (
            np.zeros((self.get_pixel_count(), 4), dtype=np.uint8),
            np.zeros((self.get_pixel_count(), 4), dtype=np.uint8),
        ) if self.PREMULTIPLIED_RGBA else ()
        self._rgba_buffer_index = 0
        self._rgba_revision = 0
        self._last_source_tick = None
        self._last_presentation_key = None
        self._last_elapsed = None
        self._simulation_time = 0.0
        self._reset_seeded_state()
        self._presentation_context: ResolvedScene | None = None

    @classmethod
    @lru_cache(maxsize=None)
    def component_descriptor(cls) -> ComponentDescriptor:
        """Describe this renderer's qualified Scene v2 frame contract."""
        return ComponentDescriptor(
            component_id=cls.COMPONENT_ID, version=1, provider="python", role="animation",
            timing_policy="scaled_context",
            alpha_behavior=("premultiplied_rgba" if cls.PREMULTIPLIED_RGBA else "opaque"),
            palette_policy="semantic",
            plant_capabilities=("effect_intent",), fidelity_exceptions=(),
            defaults=cls.COMPONENT_DEFAULTS, parameter_normalizer=cls._normalized_parameters,
        )

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        """Keep the Composer payload small, local, and independent of global look output."""
        if not isinstance(values, Mapping):
            raise ValueError("Atmosphere parameters must be an object")
        defaults = dict(cls.COMPONENT_DEFAULTS)
        allowed = set(defaults) | {"plant_aware", "plant_modifiers", "brightness", "speed"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown {cls.COMPONENT_ID} parameter {sorted(unknown)[0]!r}")
        result = dict(defaults)
        for name, value in values.items():
            # Legacy brightness/speed remain readable in authored source files,
            # but Scene v2 owns final brightness and pace at the scene boundary.
            if name in {"brightness", "speed"}:
                continue
            if name == "plant_aware":
                if type(value) is not bool:
                    raise ValueError("plant_aware must be boolean")
                # Source-preset compatibility only; v2 carries explicit effects.
                continue
            elif name == "plant_modifiers":
                PlantModifierState.from_payload(value)
                # Scene v2 applies installation effects once, after composition.
                continue
            elif name == "mood":
                if value not in MOOD_PALETTES:
                    raise ValueError("mood is not supported")
                result[name] = value
            elif name == "background":
                if value not in BACKGROUND_LEVELS:
                    raise ValueError("background is not supported")
                result[name] = value
            elif name in {"seed"}:
                if type(value) is not int or not 0 <= value <= 999999:
                    raise ValueError(f"{name} is out of range")
                result[name] = value
            elif name in {"motion", "density", "background_level"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= (2 if name == "motion" else 1):
                    raise ValueError(f"{name} is out of range")
                result[name] = float(value)
            elif name == "source_fps":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 20 <= float(value) <= 40:
                    raise ValueError("source_fps is out of range")
                result[name] = float(value)
        return result

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        if new.descriptor.component_id != self.COMPONENT_ID:
            raise ValueError("Atmosphere received a context for another component")
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

    def _reset_seeded_state(self) -> None:
        rng = np.random.default_rng(int(self.params.get("seed", self.DEFAULT_SEED)))
        self._phase = rng.uniform(0.0, 2.0 * np.pi, 16).astype(np.float32)
        self._offset = rng.uniform(0.0, 1.0, 16).astype(np.float32)
        self._frequency = rng.uniform(0.65, 2.4, 16).astype(np.float32)
        self._last_source_tick = None
        self._last_elapsed = None
        self._simulation_time = 0.0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.pop("color_saturation", None)
        schema.pop("color_value", None)
        schema["brightness"].update({"default": 0.46, "max": 1.0,
                                      "description": "Conservative installation luminance"})
        schema.update({
            "motion": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.42,
                       "description": "Macro advection and fine-motion rate"},
            "density": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.46,
                        "description": "Scene coverage or event population"},
            "mood": {"type": "str", "options": list(MOOD_PALETTES), "default": self.DEFAULT_MOOD,
                     "description": "Color and light atmosphere"},
            "background": {"type": "str", "options": list(BACKGROUND_LEVELS), "default": "soft",
                           "description": "Ambient light behind the primary scene"},
            "background_level": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.18,
                                 "description": "Strength of the ambient background"},
            "source_fps": {"type": "float", "min": 20.0, "max": 40.0, "default": 30.0,
                           "description": "Bounded source redraw cadence"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": self.DEFAULT_SEED,
                     "description": "Deterministic long-form scene seed"},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        old_seed = int(self.params.get("seed", self.DEFAULT_SEED))
        super().update_parameters(new_params)
        if "seed" in new_params and int(new_params["seed"]) != old_seed:
            self._reset_seeded_state()
        self._last_source_tick = None
        self._last_presentation_key = None

    def generate_frame(self, time_elapsed: float, frame_count: int):
        fps = float(np.clip(self.params.get("source_fps", 30.0), 20.0, 40.0))
        tick = int(max(0.0, float(time_elapsed)) * fps + 1.0e-7)
        presentation_key = self._presentation_key(tick)
        same_source_tick = self._last_source_tick == tick and self._cached_frame is not None
        if same_source_tick and self._last_presentation_key == presentation_key:
            if self.PREMULTIPLIED_RGBA:
                return OverlayFrame(self._cached_frame, revision=self._rgba_revision, changed=False, dirty_ranges=())
            return self.rendered_frame(self._cached_frame, changed=False)

        if not same_source_tick:
            elapsed = max(0.0, float(time_elapsed))
            if self._last_elapsed is not None:
                # A stalled manager cannot create an unbounded simulation leap.
                dt = min(0.1, max(0.0, elapsed - self._last_elapsed))
                self._simulation_time += dt * float(np.clip(self.params.get("motion", .42), 0.0, 2.0))
            self._last_elapsed = elapsed
        self._last_source_tick = tick
        self._last_presentation_key = presentation_key
        self._render_scene(self._simulation_time)
        if not self.PREMULTIPLIED_RGBA:
            self._apply_background()
        self._apply_plant_modifiers()
        self._rgb *= float(np.clip(self.params.get("brightness", .46), 0.0, 1.0))
        np.clip(self._rgb, 0.0, 255.0, out=self._rgb)
        if self.PREMULTIPLIED_RGBA:
            # Start every source frame transparent.  ``_coverage`` is a
            # component-local wet-material mask, not an RGB substrate, so the
            # receiver-native background remains visible between droplets.
            alpha = np.rint(np.clip(self._coverage, 0.0, 1.0) * 255.0).astype(np.uint8)
            self._rgba_buffer_index = 1 - self._rgba_buffer_index
            frame = self._rgba_buffers[self._rgba_buffer_index]
            frame.fill(0)
            frame[:, 3] = alpha.reshape(-1)
            rgb = np.rint(self._rgb * (alpha[..., None] / 255.0)).astype(np.uint8)
            frame[:, :3] = np.minimum(rgb.reshape((-1, 3)), frame[:, 3:4])
            self._cached_frame = frame
            self._rgba_revision += 1
            return OverlayFrame(frame, revision=self._rgba_revision, changed=True)
        frame = self.next_frame_buffer(clear=False)
        np.copyto(frame, self._rgb.reshape((-1, 3)), casting="unsafe")
        self._cached_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _presentation_key(self, tick: int) -> tuple[Any, ...]:
        """Name every presentation input that can refresh a cached source tick."""
        return tick, self._semantic_palette_id(), str(self.params.get("mood", self.DEFAULT_MOOD))

    def _semantic_palette_id(self) -> str | None:
        if not self.SCENE_SEMANTIC_PALETTE or self._presentation_context is None:
            return None
        palette = self._presentation_context.palette
        if palette is None or not isinstance(palette.get("palette_id"), str):
            raise ValueError(f"{self.COMPONENT_ID} requires a semantic Scene v2 palette")
        return str(palette["palette_id"])

    def _palette(self):
        palette_id = self._semantic_palette_id()
        if palette_id is not None:
            palette = SEMANTIC_PALETTES.get(palette_id, SEMANTIC_PALETTES["neutral"])
            gains = MOOD_TONAL_GAINS.get(
                str(self.params.get("mood", self.DEFAULT_MOOD)), MOOD_TONAL_GAINS[self.DEFAULT_MOOD]
            )
            return tuple(
                np.asarray(palette[key], dtype=np.float32) * gain
                for key, gain in zip(("low", "mid", "high"), gains)
            )
        palette = MOOD_PALETTES.get(str(self.params.get("mood", self.DEFAULT_MOOD)),
                                    MOOD_PALETTES[self.DEFAULT_MOOD])
        return tuple(np.asarray(palette[key], dtype=np.float32) for key in ("low", "mid", "high"))

    def _semantic_mood_curve(self) -> tuple[float, float, float, float]:
        return MOOD_TONAL_CURVES.get(
            str(self.params.get("mood", self.DEFAULT_MOOD)),
            MOOD_TONAL_CURVES[self.DEFAULT_MOOD],
        )

    def semantic_snapshot(self) -> Mapping[str, Any]:
        """Stable state proof for presentation-only Scene palette switches."""
        return MappingProxyType({
            "phase": tuple(float(value) for value in self._phase),
            "offset": tuple(float(value) for value in self._offset),
            "frequency": tuple(float(value) for value in self._frequency),
            "simulation_time": self._simulation_time,
            "source_tick": self._last_source_tick,
        })

    def _paint(self, field: np.ndarray, accent: Optional[np.ndarray] = None) -> None:
        low, mid, high = self._palette()
        f = np.clip(field, 0.0, 1.0)
        accent_gain = 1.0
        if self._semantic_palette_id() is not None:
            field_bias, field_gain, _background_gain, accent_gain = self._semantic_mood_curve()
            f = np.clip(f * field_gain + field_bias, 0.0, 1.0)
        lower = np.minimum(f * 2.0, 1.0)[..., None]
        upper = np.maximum(f * 2.0 - 1.0, 0.0)[..., None]
        self._rgb[:] = low + (mid - low) * lower + (high - mid) * upper
        if accent is not None:
            self._rgb += np.maximum(accent, 0.0)[..., None] * high * (.55 * accent_gain)

    def _apply_background(self) -> None:
        style = str(self.params.get("background", "soft"))
        level = float(np.clip(self.params.get("background_level", .18), 0.0, 1.0))
        strength = BACKGROUND_LEVELS.get(style, BACKGROUND_LEVELS["soft"]) * level
        if self._semantic_palette_id() is not None:
            strength *= self._semantic_mood_curve()[2]
        if strength <= 0.0:
            return
        low, mid, high = self._palette()
        color = mid * (1.0 - .38 * strength) + high * (.18 + .38 * strength)
        vertical = (.62 + .38 * (1.0 - self._y))[..., None]
        self._rgb += color * vertical * (.16 * strength)

    def _render_scene(self, t: float) -> None:
        density = float(np.clip(self.params.get("density", .46), 0.0, 1.0))
        if self.SCENE == "rain":
            self._rain(t, density)
        elif self.SCENE == "aurora":
            self._aurora(t, density)
        elif self.SCENE == "cloud":
            self._cloud(t, density)
        elif self.SCENE == "waterfall":
            self._waterfall(t, density)
        elif self.SCENE == "tidal":
            self._tidal(t, density)
        else:
            raise ValueError(f"unknown atmosphere scene {self.SCENE!r}")

    def _rain(self, t: float, density: float) -> None:
        city = .08 + .08 * np.sin(17.0 * self._x + self._phase[0]) * (self._y > .52)
        field = city + .05 * (1.0 - self._y)
        count = 3 + int(11 * density)
        trails = np.zeros_like(self._field)
        for i in range(count):
            x0 = self._offset[i] + .055 * np.sin(t * (.4 + self._frequency[i]) + self._phase[i])
            head = np.mod(self._offset[(i + 5) % 16] + t * (.13 + .09 * self._frequency[i]), 1.18) - .08
            dx = np.minimum(np.abs(self._x - x0), 1.0 - np.abs(self._x - x0))
            track = np.exp(-((dx / (.018 + .012 * density)) ** 2))
            tail = np.exp(-np.maximum(0.0, head - self._y) * (20.0 - 8.0 * density))
            trails += track * tail * (self._y <= head + .018)
        if self.PREMULTIPLIED_RGBA:
            # Tracks read as refractive highlights while sparse city glints
            # retain enough coverage to describe wet glass without veiling the
            # native background behind the foreground plane.
            glints = np.maximum(city - .125, 0.0) * 1.8
            self._coverage[:] = np.clip(np.sqrt(trails) * (.42 + .38 * density) + glints, 0.0, .84)
        self._paint(np.clip(field + trails * (.14 + .16 * density), 0, 1), trails * .12)
        if self.PREMULTIPLIED_RGBA:
            # The glass highlights need enough chroma after premultiplication
            # to remain legible over both native background luminance ranges.
            accent_gain = self._semantic_mood_curve()[3] if self._semantic_palette_id() is not None else 1.0
            self._rgb += trails[..., None] * self._palette()[2] * (.76 * accent_gain)

    def _aurora(self, t: float, density: float) -> None:
        field = np.full_like(self._field, .025)
        curtains = 2 + int(4 * density)
        for i in range(curtains):
            center = self._offset[i] + .16 * np.sin(self._y * (5.0 + self._frequency[i]) + t * .33 + self._phase[i])
            width = .025 + .025 * density
            sheet = np.exp(-((self._x - center) / width) ** 2)
            reach = np.clip((1.1 - self._y) * (1.5 + density), 0.0, 1.0)
            fold = .55 + .45 * np.sin(self._y * 15.0 + t * .7 + self._phase[(i + 7) % 16]) ** 2
            field += sheet * reach * fold * (.16 + .09 * density)
        stars = (np.sin(self._x * 173.0 + self._y * 311.0 + self._phase[12]) > .997).astype(np.float32)
        self._paint(np.clip(field + stars * (.08 + .12 * density), 0, 1), field * .12)

    def _cloud(self, t: float, density: float) -> None:
        n1 = np.sin(self._x * 7.0 + self._y * 9.0 - t * .18 + self._phase[0])
        n2 = np.sin(self._x * 15.0 - self._y * 5.0 + t * .11 + self._phase[1])
        n3 = np.sin(self._x * 27.0 + self._y * 21.0 - t * .07 + self._phase[2])
        fog = np.clip(.5 + .25 * n1 + .16 * n2 + .09 * n3 - (.72 - density), 0, 1)
        canyon = np.exp(-((self._x - (.5 + .12 * np.sin(self._y * 4.0 + t * .08))) / .13) ** 2)
        light = canyon * (1.0 - fog) * (.25 + .3 * (1.0 - self._y))
        edge = np.maximum(0.0, fog - np.roll(fog, 1, axis=1))
        self._paint(.035 + fog * (.12 + .24 * density) + light, edge * .5)

    def _waterfall(self, t: float, density: float) -> None:
        field = .025 + .035 * (1.0 - self._y)
        streams = np.zeros_like(self._field)
        count = 3 + int(10 * density)
        for i in range(count):
            bend = self._offset[i] + .045 * np.sin(self._y * (7 + i % 4) + self._phase[i])
            dx = np.abs(self._x - bend)
            pulse = .55 + .45 * np.sin(self._y * 31.0 + t * (1.2 + self._frequency[i])) ** 2
            streams += np.exp(-((dx / (.012 + .01 * density)) ** 2)) * pulse
        ledges = (np.sin(self._x * 19.0 + self._y * 48.0 + self._phase[10]) > .94).astype(np.float32)
        mist = np.exp(-((self._y - (.72 + .08 * np.sin(self._x * 11.0))) / .06) ** 2) * density
        if self.PREMULTIPLIED_RGBA:
            # Streams carry the strongest coverage, with ledges and mist
            # contributing a deliberately bounded, translucent breakup.
            self._coverage[:] = np.clip(streams * (.25 + .34 * density) + ledges * .16 + mist * .26, 0.0, .86)
        self._paint(np.clip(field + streams * .23 + ledges * .05 + mist * .1, 0, 1), mist * .22)
        if self.PREMULTIPLIED_RGBA:
            high = self._palette()[2]
            self._rgb += (streams[..., None] * .42 + mist[..., None] * .30) * high

    def _tidal(self, t: float, density: float) -> None:
        surface = .47 + .035 * np.sin(self._x * 8.0 - t * .42) + .018 * np.sin(self._x * 19.0 + t * .25)
        depth = np.clip((self._y - surface) * 2.0, 0.0, 1.0)
        ocean = (self._y >= surface) * (.035 + .09 * (1.0 - depth))
        wakes = np.zeros_like(self._field)
        count = 3 + int(9 * density)
        for i in range(count):
            cx = np.mod(self._offset[i] + t * (.018 + .015 * self._frequency[i]), 1.0)
            cy = .52 + self._offset[(i + 6) % 16] * .43
            radius = np.sqrt((self._x - cx) ** 2 + ((self._y - cy) * .52) ** 2)
            wakes += np.exp(-radius * (30.0 - 10.0 * density)) * (.5 + .5 * np.sin(radius * 95.0 - t * 2.0))
        plankton = (np.sin(self._x * 157.0 + self._y * 263.0 + self._phase[9]) > (.994 - .02 * density)).astype(np.float32)
        glow = (wakes * .28 + plankton * .18) * (self._y >= surface)
        self._paint(np.clip(ocean + glow, 0, 1), glow * .65)

    def _apply_plant_modifiers(self) -> None:
        strengths = {name: self.plant_modifier_strength(name) for name in self.PLANT_MODIFIER_SUPPORT}
        if not any(value > 0.0 for value in strengths.values()):
            return
        masks = self.get_plant_masks()
        obstacle = masks.obstacle_flat.reshape(self.width, self.height)
        edge = masks.obstacle_edge.reshape(self.width, self.height)
        if strengths.get("shadow", 0.0) > 0.0:
            self._rgb[obstacle] *= 1.0 - .82 * strengths["shadow"]
        if strengths.get("refract", 0.0) > 0.0:
            amount = strengths["refract"]
            halo = np.exp(-masks.distance.reshape(self.width, self.height) / (1.0 + 4.0 * amount))
            self._rgb += halo[..., None] * np.roll(self._rgb, 1, axis=0) * (.08 * amount)
        if strengths.get("illuminate", 0.0) > 0.0:
            self._rgb[edge] += 38.0 * strengths["illuminate"]
        if strengths.get("emitter", 0.0) > 0.0:
            pulse = .5 + .5 * np.sin(self._simulation_time * 2.0 + self._x * 9.0 + self._y * 13.0)
            self._rgb[edge] += pulse[edge, None] * (42.0 * strengths["emitter"])

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {"scene": self.SCENE, "source_tick": self._last_source_tick,
                "simulation_time": self._simulation_time,
                "source_fps": float(self.params.get("source_fps", 30.0))}

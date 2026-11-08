"""Scene v2 Ocean Waves: stacked swells, foam, and sparkle on the tall wall."""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.scene_palettes import HOLIDAY_TRIPLES, hsv_to_rgb_field


class OceanWavesAnimation(AnimationBase):
    ANIMATION_NAME = "Ocean Waves"
    ANIMATION_DESCRIPTION = "Rolling ocean swells with foam crests traveling down the wall"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "ocean_waves", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    SIM_HZ = 24.0
    DEFAULTS = MappingProxyType({
        "swell": 0.68, "chop": 0.38, "foam": 0.55, "travel": 0.9, "seed": 3301,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE,
        timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS,
    )
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": MappingProxyType({
            "deep": (2.0, 18.0, 48.0), "shallow": (18.0, 118.0, 168.0), "foam": (214.0, 244.0, 255.0),
        }),
        "mist": MappingProxyType({
            "deep": (6.0, 22.0, 42.0), "shallow": (72.0, 148.0, 176.0), "foam": (232.0, 246.0, 255.0),
        }),
        "spectrum": MappingProxyType({
            "deep": (12.0, 6.0, 48.0), "shallow": (28.0, 92.0, 196.0), "foam": (140.0, 255.0, 230.0),
        }),
        "ember": MappingProxyType({
            "deep": (18.0, 8.0, 6.0), "shallow": (48.0, 92.0, 118.0), "foam": (255.0, 214.0, 168.0),
        }),
        "rainbow": MappingProxyType({
            "deep": (4.0, 10.0, 32.0), "shallow": (16.0, 64.0, 96.0), "foam": (255.0, 255.0, 255.0),
        }),
        "xmas": MappingProxyType({
            "deep": HOLIDAY_TRIPLES["xmas"][0], "shallow": (18.0, 96.0, 42.0), "foam": HOLIDAY_TRIPLES["xmas"][2],
        }),
        "fall": MappingProxyType({
            "deep": HOLIDAY_TRIPLES["fall"][0], "shallow": HOLIDAY_TRIPLES["fall"][1], "foam": HOLIDAY_TRIPLES["fall"][2],
        }),
        "easter": MappingProxyType({
            "deep": HOLIDAY_TRIPLES["easter"][0], "shallow": HOLIDAY_TRIPLES["easter"][1], "foam": HOLIDAY_TRIPLES["easter"][2],
        }),
        "july4": MappingProxyType({
            "deep": HOLIDAY_TRIPLES["july4"][0], "shallow": (28.0, 56.0, 168.0), "foam": HOLIDAY_TRIPLES["july4"][1],
        }),
    })

    def __init__(self, controller: Any, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._x = np.linspace(0.0, 1.0, self.width, dtype=np.float32)[:, None]
        self._y = np.linspace(0.0, 1.0, self.height, dtype=np.float32)[None, :]
        self._presentation_context: ResolvedScene | None = None
        self._last_render_key: tuple[Any, ...] | None = None
        self._last_tick: int | None = None
        self._seed_phases()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def palette_roles(cls, palette_id: str) -> Mapping[str, tuple[float, float, float]]:
        return cls.SEMANTIC_PALETTES.get(str(palette_id), cls.SEMANTIC_PALETTES["neutral"])

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "swell": {"type": "float", "min": 0.15, "max": 1.0, "default": 0.68,
                      "description": "Height of the rolling ocean swells."},
            "chop": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.38,
                     "description": "Wind-chop roughness on the water."},
            "foam": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.55,
                     "description": "Whitecap foam on the wave faces."},
            "travel": {"type": "float", "min": 0.2, "max": 2.0, "default": 0.9,
                       "description": "How quickly the swells roll down the wall."},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 3301,
                     "description": "Repeatable swell layout seed."},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        reset = candidate["seed"] != self.params["seed"]
        self.params = candidate
        if reset:
            self._seed_phases()
        self._last_render_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (
            self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE
        ):
            raise ValueError("Ocean Waves received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Ocean Waves requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        if self._presentation_context is None:
            phase_time, palette_id, parameters = max(0.0, float(time_elapsed)), "neutral", self.params
        else:
            phase_time = max(0.0, float(self._presentation_context.phase_time))
            palette_id = str(self._presentation_context.palette["palette_id"])
            parameters = self._presentation_context.parameters
        candidate = self._normalized_parameters(parameters)
        if candidate["seed"] != self.params["seed"]:
            self.params = candidate
            self._seed_phases()
        else:
            self.params = candidate
        tick = int(math.floor(phase_time * self.SIM_HZ + 1.0e-9))
        self._last_tick = tick
        key = (tick, palette_id, tuple(self.params.items()), tuple(self._phase.tolist()))
        if key == self._last_render_key:
            return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        self._paint(palette_id, tick / self.SIM_HZ)
        self._last_render_key = key
        return RenderedFrame(self._pixels, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "seed": self.params["seed"],
            "tick": self._last_tick,
            "phase": self._phase.tobytes(),
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.SIM_HZ, "tick": self._last_tick})

    def _seed_phases(self) -> None:
        rng = np.random.default_rng(int(self.params["seed"]))
        self._phase = rng.uniform(0.0, 1.0, 4).astype(np.float32)
        self._last_tick = None
        self._last_render_key = None

    def _paint(self, palette_id: str, phase_time: float) -> None:
        palette = self.palette_roles(palette_id)
        deep = np.asarray(palette["deep"], dtype=np.float32)
        shallow = np.asarray(palette["shallow"], dtype=np.float32)
        foam_color = np.asarray(palette["foam"], dtype=np.float32)
        swell = float(self.params["swell"])
        chop = float(self.params["chop"])
        foam_amt = float(self.params["foam"])
        travel = float(self.params["travel"])
        x, y = self._x, self._y
        p0, p1, p2, p3 = (float(value) for value in self._phase)
        time_term = phase_time * travel
        # Crests travel toward +y (down the wall) so the column reads as incoming surf.
        arg1 = (y * 3.05 + x * 0.62 - time_term * 0.58 + p0) * math.tau
        arg2 = (y * 5.35 - x * 0.44 - time_term * 0.92 + p1) * math.tau
        arg3 = (y * 9.40 + x * 1.18 - time_term * 1.48 + p2) * math.tau
        wave = swell * (0.58 * np.sin(arg1) + 0.30 * np.sin(arg2))
        chop_field = chop * 0.28 * np.sin(arg3)
        height = wave + chop_field
        face = np.clip(
            swell * (0.58 * np.cos(arg1) + 0.30 * np.cos(arg2)) + chop * 0.18 * np.cos(arg3),
            0.0, 1.0,
        )
        crest = np.clip(height, 0.0, 1.0)
        foam = np.clip(crest * face * (0.28 + 0.72 * foam_amt), 0.0, 1.0)
        foam = foam * foam
        sparkle = np.maximum(
            0.0,
            np.sin((x * 19.0 + y * 11.0 - time_term * 2.2 + p3) * math.tau),
        )
        sparkle = np.power(sparkle, 14.0) * foam_amt * 0.42
        water = np.clip(0.18 + 0.46 * y + 0.36 * (0.5 + 0.5 * height), 0.0, 1.0)
        sky = np.clip(1.0 - y / 0.11, 0.0, 1.0)
        if palette_id == "rainbow":
            hue = np.mod(water * 0.58 + x * 0.28 + y * 0.08 + time_term * 0.03, 1.0)
            rgb = hsv_to_rgb_field(hue, np.clip(0.72 - 0.45 * foam, 0.25, 0.92), np.clip(0.22 + 0.78 * water, 0.0, 1.0))
            rgb = rgb + foam_color * (foam + sparkle)[..., None]
        else:
            rgb = deep + (shallow - deep) * water[..., None]
            rgb = rgb + (foam_color - rgb) * (foam + sparkle)[..., None]
        horizon = shallow * 0.35 + foam_color * 0.65
        rgb = rgb * (1.0 - sky)[..., None] + horizon * sky[..., None]
        np.clip(rgb, 0.0, 255.0, out=rgb)
        logical = self._pixels.reshape(self.width, self.height, 3)[:, ::-1]
        np.copyto(logical, rgb, casting="unsafe")

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Ocean Waves does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        seed = result["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        for name, low, high in (
            ("swell", 0.15, 1.0), ("chop", 0.0, 1.0), ("foam", 0.0, 1.0), ("travel", 0.2, 2.0),
        ):
            value = result[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not low <= float(value) <= high
            ):
                raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        return result

"""Scene v2 Campfire: a ground fire, rising sparks, and smoke into a themed sky."""

from __future__ import annotations

import colorsys
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.scene_palettes import HOLIDAY_STOPS, HOLIDAY_TRIPLES, hsv_to_rgb_field


_HUE_GOLDEN = np.float32(0.6180339887)


class CampfireAnimation(AnimationBase):
    ANIMATION_NAME = "Campfire"
    ANIMATION_DESCRIPTION = "A ground fire throws sparks and smoke into a themed night sky"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "campfire", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    SIM_HZ = 24.0
    MAX_CATCH_UP_STEPS = 8
    MAX_SPARKS = 96
    MAX_SMOKE = 28
    DEFAULTS = MappingProxyType({
        "heat": 0.78, "sparks": 0.72, "smoke": 0.55, "flicker": 0.45, "seed": 1903,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE,
        timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS,
    )
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": MappingProxyType({
            "sky": (4.0, 10.0, 28.0), "horizon": (48.0, 22.0, 8.0),
            "flame": (255.0, 118.0, 18.0), "core": (255.0, 236.0, 140.0),
            "spark": (255.0, 196.0, 72.0), "smoke": (78.0, 82.0, 88.0), "log": (42.0, 24.0, 12.0),
        }),
        "mist": MappingProxyType({
            "sky": (8.0, 18.0, 32.0), "horizon": (36.0, 48.0, 62.0),
            "flame": (180.0, 210.0, 230.0), "core": (236.0, 246.0, 255.0),
            "spark": (210.0, 232.0, 255.0), "smoke": (92.0, 104.0, 118.0), "log": (36.0, 40.0, 48.0),
        }),
        "spectrum": MappingProxyType({
            "sky": (14.0, 4.0, 36.0), "horizon": (64.0, 16.0, 72.0),
            "flame": (186.0, 48.0, 255.0), "core": (120.0, 255.0, 230.0),
            "spark": (255.0, 80.0, 210.0), "smoke": (72.0, 48.0, 96.0), "log": (32.0, 12.0, 42.0),
        }),
        "ember": MappingProxyType({
            "sky": (12.0, 4.0, 2.0), "horizon": (72.0, 22.0, 6.0),
            "flame": (255.0, 92.0, 8.0), "core": (255.0, 214.0, 96.0),
            "spark": (255.0, 168.0, 36.0), "smoke": (64.0, 36.0, 22.0), "log": (48.0, 22.0, 8.0),
        }),
        "rainbow": MappingProxyType({
            "sky": (6.0, 4.0, 22.0), "horizon": (28.0, 8.0, 36.0),
            "flame": (255.0, 80.0, 40.0), "core": (255.0, 255.0, 210.0),
            "spark": (255.0, 210.0, 80.0), "smoke": (70.0, 64.0, 86.0), "log": (36.0, 20.0, 14.0),
        }),
        "xmas": MappingProxyType({
            "sky": HOLIDAY_TRIPLES["xmas"][0], "horizon": (48.0, 8.0, 10.0),
            "flame": HOLIDAY_TRIPLES["xmas"][1], "core": HOLIDAY_TRIPLES["xmas"][2],
            "spark": HOLIDAY_TRIPLES["xmas"][2], "smoke": (48.0, 56.0, 42.0), "log": (28.0, 16.0, 8.0),
        }),
        "fall": MappingProxyType({
            "sky": HOLIDAY_TRIPLES["fall"][0], "horizon": HOLIDAY_TRIPLES["fall"][1],
            "flame": HOLIDAY_TRIPLES["fall"][1], "core": HOLIDAY_TRIPLES["fall"][2],
            "spark": HOLIDAY_TRIPLES["fall"][2], "smoke": (72.0, 42.0, 22.0), "log": (42.0, 20.0, 8.0),
        }),
        "easter": MappingProxyType({
            "sky": HOLIDAY_TRIPLES["easter"][0], "horizon": HOLIDAY_TRIPLES["easter"][1],
            "flame": HOLIDAY_TRIPLES["easter"][1], "core": HOLIDAY_TRIPLES["easter"][2],
            "spark": HOLIDAY_TRIPLES["easter"][2], "smoke": (86.0, 72.0, 96.0), "log": (42.0, 24.0, 36.0),
        }),
        "july4": MappingProxyType({
            "sky": HOLIDAY_TRIPLES["july4"][0], "horizon": (48.0, 12.0, 18.0),
            "flame": HOLIDAY_TRIPLES["july4"][1], "core": HOLIDAY_TRIPLES["july4"][2],
            "spark": HOLIDAY_TRIPLES["july4"][1], "smoke": (48.0, 52.0, 72.0), "log": (28.0, 16.0, 18.0),
        }),
    })

    def __init__(self, controller: Any, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._canvas = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._cols, self._rows = np.indices((self.width, self.height), dtype=np.float32)
        self._presentation_context: ResolvedScene | None = None
        self._last_tick: int | None = None
        self._last_render_key: tuple[Any, ...] | None = None
        self._initialize_simulation()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def palette_roles(cls, palette_id: str) -> Mapping[str, tuple[float, float, float]]:
        return cls.SEMANTIC_PALETTES.get(str(palette_id), cls.SEMANTIC_PALETTES["neutral"])

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "heat": {"type": "float", "min": 0.2, "max": 1.0, "default": 0.78,
                     "description": "How tall and bright the ground fire burns."},
            "sparks": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.72,
                       "description": "How many sparks lift off the fire toward the sky."},
            "smoke": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.55,
                      "description": "How much smoke drifts into the sky."},
            "flicker": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.45,
                        "description": "How much the flame bed wavers."},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1903,
                     "description": "Repeatable campfire layout seed."},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        reset = candidate["seed"] != self.params["seed"]
        self.params = candidate
        if reset:
            self._initialize_simulation()
        self._last_render_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (
            self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE
        ):
            raise ValueError("Campfire received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Campfire requires a semantic Scene v2 palette")
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
            self._initialize_simulation()
        else:
            self.params = candidate
        tick = int(math.floor(phase_time * self.SIM_HZ + 1.0e-9))
        if self._last_tick is None:
            self._last_tick = tick
        else:
            target = min(tick, self._last_tick + self.MAX_CATCH_UP_STEPS)
            while self._last_tick < target:
                self._simulate_step(1.0 / self.SIM_HZ)
                self._last_tick += 1
        key = (self._last_tick, palette_id, tuple(self.params.items()))
        if key == self._last_render_key:
            return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        self._paint(palette_id, self._last_tick / self.SIM_HZ)
        self._last_render_key = key
        return RenderedFrame(self._pixels, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "seed": self.params["seed"],
            "tick": self._last_tick,
            "spark_x": self.spark_x.tobytes(),
            "spark_y": self.spark_y.tobytes(),
            "spark_life": self.spark_life.tobytes(),
            "smoke_x": self.smoke_x.tobytes(),
            "smoke_y": self.smoke_y.tobytes(),
            "smoke_life": self.smoke_life.tobytes(),
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.SIM_HZ, "tick": self._last_tick})

    def spark_ignition_points(self) -> np.ndarray:
        """Spark positions in host LED space: strip x, LED 0 at the physical bottom."""

        count = max(0, int(round(float(self.params["sparks"]) * self.MAX_SPARKS)))
        if count <= 0:
            return np.zeros((0, 2), dtype=np.float32)
        led = (self.height - 1.0) - self.spark_y[:count]
        return np.column_stack((self.spark_x[:count], led)).astype(np.float32, copy=False)

    def _fire_center(self) -> tuple[float, float]:
        return (self.width - 1) * 0.5, float(self.height - 4)

    def _initialize_simulation(self) -> None:
        rng = np.random.default_rng(int(self.params["seed"]))
        cx, fire_y = self._fire_center()
        self.spark_x = (cx + rng.normal(0.0, 2.4, self.MAX_SPARKS)).astype(np.float32)
        self.spark_y = (fire_y - rng.uniform(0.0, self.height * 0.88, self.MAX_SPARKS)).astype(np.float32)
        self.spark_vx = rng.normal(0.0, 3.2, self.MAX_SPARKS).astype(np.float32)
        self.spark_vy = rng.uniform(-78.0, -22.0, self.MAX_SPARKS).astype(np.float32)
        self.spark_life = rng.uniform(0.18, 2.4, self.MAX_SPARKS).astype(np.float32)
        self.spark_max = rng.uniform(0.8, 2.6, self.MAX_SPARKS).astype(np.float32)
        self._spark_hue = np.mod(
            np.arange(self.MAX_SPARKS, dtype=np.float32) * _HUE_GOLDEN, np.float32(1.0)
        )
        self.smoke_x = (cx + rng.normal(0.0, 3.6, self.MAX_SMOKE)).astype(np.float32)
        self.smoke_y = (fire_y - rng.uniform(6.0, self.height * 0.85, self.MAX_SMOKE)).astype(np.float32)
        self.smoke_vx = rng.normal(0.0, 1.6, self.MAX_SMOKE).astype(np.float32)
        self.smoke_vy = rng.uniform(-16.0, -6.0, self.MAX_SMOKE).astype(np.float32)
        self.smoke_life = rng.uniform(0.4, 3.4, self.MAX_SMOKE).astype(np.float32)
        self.smoke_max = rng.uniform(1.8, 3.8, self.MAX_SMOKE).astype(np.float32)
        self._phase = rng.uniform(0.0, math.tau, 4).astype(np.float32)
        self._rng = rng
        self._last_tick = None
        self._last_render_key = None

    def _respawn_sparks(self, mask: np.ndarray) -> None:
        if not np.any(mask):
            return
        count = int(np.count_nonzero(mask))
        cx, fire_y = self._fire_center()
        self.spark_x[mask] = cx + self._rng.normal(0.0, 2.1, count)
        self.spark_y[mask] = fire_y - self._rng.uniform(0.0, 3.5, count)
        self.spark_vx[mask] = self._rng.normal(0.0, 3.4, count)
        self.spark_vy[mask] = self._rng.uniform(-82.0, -24.0, count)
        self.spark_max[mask] = self._rng.uniform(0.8, 2.6, count)
        self.spark_life[mask] = self.spark_max[mask]

    def _respawn_smoke(self, mask: np.ndarray) -> None:
        if not np.any(mask):
            return
        count = int(np.count_nonzero(mask))
        cx, fire_y = self._fire_center()
        self.smoke_x[mask] = cx + self._rng.normal(0.0, 2.8, count)
        self.smoke_y[mask] = fire_y - self._rng.uniform(2.0, 10.0, count)
        self.smoke_vx[mask] = self._rng.normal(0.0, 1.8, count)
        self.smoke_vy[mask] = self._rng.uniform(-18.0, -7.0, count)
        self.smoke_max[mask] = self._rng.uniform(1.8, 3.8, count)
        self.smoke_life[mask] = self.smoke_max[mask]

    def _simulate_step(self, dt: float) -> None:
        heat = float(self.params["heat"])
        self.spark_vx += self._rng.normal(0.0, 6.0, self.MAX_SPARKS).astype(np.float32) * dt
        self.spark_vy += (3.2 - 1.6 * heat) * dt
        self.spark_x += self.spark_vx * dt
        self.spark_y += self.spark_vy * dt
        self.spark_life -= dt
        dead = (
            (self.spark_life <= 0.0)
            | (self.spark_y < -1.0)
            | (self.spark_x < -1.0)
            | (self.spark_x > self.width)
        )
        self._respawn_sparks(dead)
        wind = 1.4 * math.sin(float(self._phase[0]) + (self._last_tick or 0) * 0.05)
        self.smoke_vx += (wind + self._rng.normal(0.0, 1.2, self.MAX_SMOKE).astype(np.float32)) * dt
        self.smoke_x += self.smoke_vx * dt
        self.smoke_y += self.smoke_vy * dt
        self.smoke_life -= dt
        spent = (
            (self.smoke_life <= 0.0)
            | (self.smoke_y < -4.0)
            | (self.smoke_x < -4.0)
            | (self.smoke_x > self.width + 3.0)
        )
        self._respawn_smoke(spent)

    def _spark_color(self, palette_id: str, index: int, spark: np.ndarray, core: np.ndarray) -> np.ndarray:
        if palette_id == "rainbow":
            red, green, blue = colorsys.hsv_to_rgb(float(self._spark_hue[index]), 0.88, 1.0)
            return np.asarray((red * 255.0, green * 255.0, blue * 255.0), dtype=np.float32)
        stops = HOLIDAY_STOPS.get(str(palette_id))
        if stops is not None:
            return np.asarray(stops[index % len(stops)], dtype=np.float32)
        mix = 0.35 + 0.65 * float(self.spark_life[index] / max(0.2, float(self.spark_max[index])))
        return spark * (1.0 - mix) + core * mix

    def _stamp(self, x: float, y: float, radius: float, color: np.ndarray, gain: float) -> None:
        rad = max(1, int(math.ceil(radius * 2.4)))
        x0 = max(0, int(math.floor(x)) - rad)
        x1 = min(self.width, int(math.floor(x)) + rad + 1)
        y0 = max(0, int(math.floor(y)) - rad)
        y1 = min(self.height, int(math.floor(y)) + rad + 1)
        if x0 >= x1 or y0 >= y1:
            return
        dx = np.arange(x0, x1, dtype=np.float32) - np.float32(x)
        dy = np.arange(y0, y1, dtype=np.float32) - np.float32(y)
        falloff = np.exp(-(dx[:, None] ** 2 + dy[None, :] ** 2) / max(0.18, radius * radius))
        self._canvas[x0:x1, y0:y1] += color * (falloff * gain)[..., None]

    def _paint(self, palette_id: str, phase_time: float) -> None:
        palette = self.palette_roles(palette_id)
        sky, horizon, flame, core, spark, smoke, log = (
            np.asarray(palette[name], dtype=np.float32)
            for name in ("sky", "horizon", "flame", "core", "spark", "smoke", "log")
        )
        if palette_id == "rainbow":
            hue = np.mod(self._rows / max(1.0, self.height - 1.0) * 0.42 + 0.58, 1.0)
            sky_rgb = hsv_to_rgb_field(hue, 0.55, 0.16)
        else:
            sky_rgb = np.broadcast_to(sky, self._canvas.shape)
        heat = float(self.params["heat"])
        flicker = float(self.params["flicker"])
        spark_amt = float(self.params["sparks"])
        smoke_amt = float(self.params["smoke"])
        y_norm = self._rows / max(1.0, self.height - 1.0)
        dusk = np.clip(1.0 - y_norm / 0.72, 0.0, 1.0)
        glow = np.clip((y_norm - 0.58) / 0.32, 0.0, 1.0) ** 1.6
        self._canvas[:] = sky_rgb * dusk[..., None] + horizon * ((1.0 - dusk) * (0.35 + 0.65 * glow))[..., None]
        cx, fire_y = self._fire_center()
        dx = self._cols - cx
        from_bottom = (self.height - 1.0) - self._rows
        flame_h = 8.0 + 26.0 * heat
        flame_w = 3.0 + 5.6 * heat
        width = flame_w * (0.55 + 0.85 * np.clip(1.0 - from_bottom / flame_h, 0.12, 1.2))
        plume = np.exp(-(dx * dx) / (2.0 * width * width + 0.4)) * np.exp(-np.maximum(0.0, from_bottom) / flame_h)
        wobble = 0.78 + 0.22 * flicker * np.sin(
            phase_time * (9.0 + 7.0 * flicker) + dx * 0.42 + float(self._phase[1])
        )
        field = np.clip(heat * plume * wobble, 0.0, 1.4)
        inner = np.clip((field - 0.38) / 0.62, 0.0, 1.0)
        inner *= inner * (3.0 - 2.0 * inner)
        self._canvas += flame * (field * (1.0 - inner) * 0.85)[..., None]
        self._canvas += core * (inner * field)[..., None]
        visible_smoke = max(0, int(round(smoke_amt * self.MAX_SMOKE)))
        for index in range(visible_smoke):
            age = 1.0 - float(self.smoke_life[index] / max(0.4, float(self.smoke_max[index])))
            radius = 1.6 + 3.8 * age
            gain = (0.22 + 0.38 * smoke_amt) * (1.0 - age)
            self._stamp(float(self.smoke_x[index]), float(self.smoke_y[index]), radius, smoke, gain)
        for offset, stretch in ((-2.4, 3.4), (0.0, 4.1), (2.3, 3.2)):
            log_dx = self._cols - (cx + offset)
            log_dy = self._rows - (fire_y + 1.4)
            log_mask = np.exp(-(log_dx * log_dx) / (2.2 * stretch) - (log_dy * log_dy) / 1.15)
            self._canvas += log * (0.72 * log_mask)[..., None]
        visible_sparks = max(0, int(round(spark_amt * self.MAX_SPARKS)))
        for index in range(visible_sparks):
            life = float(self.spark_life[index] / max(0.2, float(self.spark_max[index])))
            color = self._spark_color(palette_id, index, spark, core)
            self._stamp(float(self.spark_x[index]), float(self.spark_y[index]), 0.55 + 0.35 * life, color, 0.55 + 0.7 * life)
        np.clip(self._canvas, 0.0, 255.0, out=self._canvas)
        logical = self._pixels.reshape(self.width, self.height, 3)[:, ::-1]
        np.copyto(logical, self._canvas, casting="unsafe")

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Campfire does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        seed = result["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        for name, low, high in (
            ("heat", 0.2, 1.0), ("sparks", 0.0, 1.0), ("smoke", 0.0, 1.0), ("flicker", 0.0, 1.0),
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

"""Scene v2 Fireworks: a small, immediate, semantic-palette instrument."""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import OverlayFrame, ResolvedScene
from animation.libraries.scene_palettes import HOLIDAY_TRIPLES, nearest_stop_color


@dataclass
class _Rocket:
    x: float
    y: float
    vx: float
    vy: float
    target: float
    hue: float


@dataclass
class _Spark:
    x: float
    y: float
    vx: float
    vy: float
    age: float
    lifetime: float
    hue: float
    size: float = 1.0
    split: bool = False


class FireworksAnimation(AnimationBase):
    """A bounded fireworks show whose controls act on the next simulation tick."""

    ANIMATION_NAME = "Fireworks"
    ANIMATION_DESCRIPTION = "An immediate, playful aerial-shell instrument"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "fireworks", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "premultiplied_rgba_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset({"semantic_palette_roles", "scaled_context", "effect_intent"})
    PLANT_MODIFIER_SUPPORT = frozenset()
    SIM_HZ, MAX_CATCH_UP_STEPS, MAX_SPARKS = 24.0, 16, 720
    STYLES = ("mixed", "ring", "willow", "palm", "burst")
    DEFAULTS = MappingProxyType({"launch_cadence": 1.15, "shell_population": 54, "burst_size": .29, "burst_style": "mixed", "gravity": .38, "trails": .72, "crackle": .24, "twinkle": .35, "seed": 1776})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="premultiplied_rgba", palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": ((4., 9., 18.), (255., 126., 42.), (255., 228., 146.)),
        "mist": ((3., 9., 20.), (72., 185., 255.), (244., 237., 255.)),
        "spectrum": ((14., 3., 35.), (245., 42., 238.), (45., 237., 220.)),
        "ember": ((18., 3., 2.), (255., 76., 18.), (255., 206., 74.)),
        "rainbow": ((8., 0., 18.), (255., 40., 90.), (40., 255., 140.)),
        **{name: tuple(triple) for name, triple in HOLIDAY_TRIPLES.items()},
    })

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        # Trails are semantic light energy, never a pre-rendered local sky.  This
        # makes a palette swap a presentation-only operation and keeps the
        # returned plane transparent wherever the instrument has no light.
        self._pixels = np.zeros((self.get_pixel_count(), 4), dtype=np.uint8)
        # Chromatic trail energy, colorized at paint so palette swaps stay presentation-only.
        self._trail = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._presentation_context: ResolvedScene | None = None
        self._presentation_only_refresh = False
        self._last_tick: int | None = None; self._last_render_key: tuple[Any, ...] | None = None
        self._rng = np.random.default_rng(self.params["seed"])
        self._rockets: list[_Rocket] = []; self._sparks: list[_Spark] = []
        self._launch_phase, self._burst_count = 1.0, 0

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "launch_cadence": {"type": "float", "min": .1, "max": 4., "default": 1.15, "description": "How often a new shell launches"},
            "shell_population": {"type": "int", "min": 12, "max": 120, "default": 54, "description": "Sparks in each aerial shell"},
            "burst_size": {"type": "float", "min": .08, "max": .65, "default": .29, "description": "Shell radius and launch reach"},
            "burst_style": {"type": "str", "options": list(self.STYLES), "default": "mixed", "description": "Shape of the next shell"},
            "gravity": {"type": "float", "min": 0., "max": 1.5, "default": .38, "description": "How quickly embers fall"},
            "trails": {"type": "float", "min": .1, "max": 1., "default": .72, "description": "How long glowing trails linger"},
            "crackle": {"type": "float", "min": 0., "max": 1., "default": .24, "description": "Chance of a shell breaking into crackles"},
            "twinkle": {"type": "float", "min": 0., "max": 1., "default": .35, "description": "Sparkle shimmer"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1776, "description": "Repeatable show sequence"},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        if candidate["seed"] != self.params["seed"]:
            self.params = candidate; self._reset_show()
        else:
            self.params = candidate
        self._last_render_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Fireworks received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Fireworks requires a semantic Scene v2 palette")
        self._presentation_only_refresh = bool(
            old is not None
            and old.phase_time == new.phase_time
            and old.parameters == new.parameters
            and old.palette != new.palette
        )
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del frame_count
        if self._presentation_context is None:
            phase_time, palette_id, parameters = max(0., float(time_elapsed)), "neutral", self.params
        else:
            phase_time, palette_id, parameters = max(0., float(self._presentation_context.phase_time)), str(self._presentation_context.palette["palette_id"]), self._presentation_context.parameters
        candidate = self._normalized_parameters(parameters)
        if candidate["seed"] != self.params["seed"]:
            self.params = candidate; self._reset_show()
        else:
            self.params = candidate
        tick = int(math.floor(phase_time * self.SIM_HZ + 1.e-9))
        if self._last_tick is None: self._last_tick = tick
        elif self._presentation_only_refresh:
            # A Scene palette refresh at one source time repaints this exact
            # semantic snapshot.  It must not consume deferred catch-up work
            # (or RNG) merely because the visual context changed.
            self._presentation_only_refresh = False
        else:
            target = min(tick, self._last_tick + self.MAX_CATCH_UP_STEPS)
            while self._last_tick < target:
                self._step(1. / self.SIM_HZ); self._last_tick += 1
        key = (self._last_tick, palette_id, tuple(self.params.items()))
        if key == self._last_render_key:
            return OverlayFrame(self._pixels, revision=self._last_tick or 0, changed=False, dirty_ranges=())
        self._paint(palette_id, phase_time); self._last_render_key = key
        return OverlayFrame(self._pixels, revision=self._last_tick or 0, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"seed": self.params["seed"], "tick": self._last_tick, "rockets": tuple((round(r.x, 4), round(r.y, 4), round(r.target, 4)) for r in self._rockets), "sparks": len(self._sparks), "bursts": self._burst_count})

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.SIM_HZ, "tick": self._last_tick, "bursts": self._burst_count})

    def _reset_show(self) -> None:
        self._rng = np.random.default_rng(self.params["seed"]); self._rockets.clear(); self._sparks.clear(); self._trail.fill(0.)
        self._launch_phase, self._burst_count, self._last_tick = 1., 0, None

    def _step(self, dt: float) -> None:
        persist = (0.80 + 0.16 * float(self.params["trails"])) ** (60.0 * dt)
        self._trail *= persist
        self._launch_phase += dt * float(self.params["launch_cadence"])
        while self._launch_phase >= 1. and len(self._rockets) < 5:
            self._launch_phase -= 1.
            launch_speed = 0.72 + float(self.params["burst_size"]) * 0.38
            self._rockets.append(_Rocket(
                float(self._rng.uniform(.08, .92)), 1.04,
                float(self._rng.uniform(-.025, .025)), -launch_speed,
                float(self._rng.uniform(.18, .54)), float(self._rng.random()),
            ))
        gravity = float(self.params["gravity"])
        rising: list[_Rocket] = []
        for rocket in self._rockets:
            rocket.x += rocket.vx * dt
            rocket.y += rocket.vy * dt
            rocket.vy += gravity * 0.32 * dt
            color = self._hue_rgb(rocket.hue)
            self._deposit(rocket.x, rocket.y, color, 1.35 + 0.55 * float(self.params["trails"]))
            self._deposit(rocket.x, rocket.y + 0.012, tuple(channel * 0.35 for channel in color), 0.7)
            if rocket.y <= rocket.target or rocket.vy >= -0.04:
                self._burst(rocket)
            elif -0.1 <= rocket.x <= 1.1 and rocket.y > -0.1:
                rising.append(rocket)
        self._rockets = rising
        drag = 0.965 ** (dt * 60.0)
        twinkle = float(self.params["twinkle"])
        trail_gain = 0.55 + 0.85 * float(self.params["trails"])
        elapsed = (self._last_tick or 0) / self.SIM_HZ
        survivors, spawned = [], []
        for spark in self._sparks:
            spark.age += dt
            spark.x += spark.vx * dt
            spark.y += spark.vy * dt
            spark.vx *= drag
            spark.vy = spark.vy * drag + gravity * dt
            life = spark.age / spark.lifetime
            if life >= 1. or not (-.12 <= spark.x <= 1.12 and spark.y < 1.08):
                continue
            if not spark.split and life > .55 and self._rng.random() < float(self.params["crackle"]) * .18:
                spark.split = True
                for direction in (-1., 1.):
                    spawned.append(_Spark(
                        spark.x, spark.y,
                        spark.vx + direction * float(self._rng.uniform(.025, .07)),
                        spark.vy + float(self._rng.uniform(-.04, .04)),
                        0., spark.lifetime * .32, (spark.hue + direction * .07) % 1., 0.65,
                    ))
            fade = (1. - life) ** 1.35
            shimmer = 1. - twinkle + twinkle * (.35 + .65 * abs(math.sin(elapsed * 25. + spark.x * 31.)))
            self._deposit(spark.x, spark.y, self._hue_rgb(spark.hue), fade * shimmer * spark.size * trail_gain)
            survivors.append(spark)
        self._sparks = (survivors + spawned)[-self.MAX_SPARKS:]

    def _burst(self, rocket: _Rocket) -> None:
        style = self.params["burst_style"]
        if style == "mixed": style = self.STYLES[1 + int(self._rng.integers(len(self.STYLES) - 1))]
        population = int(self.params["shell_population"])
        if style == "palm":
            population = max(12, population // 2)
        size = float(self.params["burst_size"])
        for index in range(population):
            if style == "ring":
                angle = math.tau * index / population + float(self._rng.uniform(-.025, .025))
                velocity = size * float(self._rng.uniform(.90, 1.08))
            elif style == "willow":
                angle = float(self._rng.uniform(0., math.tau))
                velocity = size * 0.78 * float(self._rng.random() ** 0.35)
            elif style == "palm":
                angle = float(self._rng.uniform(math.pi * 1.10, math.pi * 1.90))
                velocity = size * float(self._rng.uniform(.75, 1.35))
            else:
                angle = float(self._rng.uniform(0., math.tau))
                velocity = size * float(self._rng.random() ** 0.35)
            lifetime = float(self._rng.uniform(.72, 1.18)) * (1.55 if style == "willow" else 1.8)
            hue = 0.105 if style == "willow" else rocket.hue
            self._sparks.append(_Spark(
                rocket.x, rocket.y, math.cos(angle) * velocity, math.sin(angle) * velocity,
                0., lifetime, hue, float(self._rng.uniform(.75, 1.25)),
            ))
        self._deposit(rocket.x, rocket.y, (255., 255., 240.), 2.2)
        self._burst_count += 1

    def _paint(self, palette_id: str, phase_time: float) -> None:
        del phase_time
        _background, primary, accent = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"])
        canvas = self._pixels.reshape(self.width, self.height, 4)[:, ::-1]
        canvas.fill(0)
        energy = np.clip(self._trail, 0., 255.)
        luma = np.max(energy, axis=2, keepdims=True)
        festive = nearest_stop_color(energy, palette_id)
        if palette_id == "rainbow":
            mixed = energy
        elif festive is not None:
            mixed = festive
        else:
            semantic = np.asarray(primary, dtype=np.float32) * .62 + np.asarray(accent, dtype=np.float32) * .38
            mixed = energy * .55 + semantic.reshape(1, 1, 3) * (luma / 255.)
        rgb8 = np.clip(np.rint(mixed), 0., 255.).astype(np.uint8)
        alpha = np.max(rgb8, axis=2)
        keep = alpha >= 2
        rgb8 *= keep[..., None]
        canvas[:, :, :3] = rgb8
        canvas[:, :, 3] = np.where(keep, alpha, np.uint8(0))

    @staticmethod
    def _hue_rgb(hue: float) -> tuple[float, float, float]:
        red, green, blue = colorsys.hsv_to_rgb(float(hue) % 1., .92, 1.)
        return red * 255., green * 255., blue * 255.

    def _deposit(self, x: float, y: float, color: tuple[float, float, float], intensity: float) -> None:
        """Add a sub-pixel light sample with bilinear filtering."""
        if intensity <= 0. or not (0. <= x <= 1. and 0. <= y <= 1.):
            return
        px = x * max(0, self.width - 1)
        py = y * max(0, self.height - 1)
        x0, y0 = int(px), int(py)
        fx, fy = px - x0, py - y0
        red, green, blue = (channel * intensity for channel in color)
        for yy, wy in ((y0, 1.0 - fy), (y0 + 1, fy)):
            if not 0 <= yy < self.height:
                continue
            for xx, wx in ((x0, 1.0 - fx), (x0 + 1, fx)):
                weight = wx * wy
                if 0 <= xx < self.width and weight > 0.0:
                    pixel = self._trail[xx, yy]
                    pixel[0] += red * weight
                    pixel[1] += green * weight
                    pixel[2] += blue * weight

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown: raise ValueError(f"Fireworks does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS); result.update(values); seed, population = result["seed"], result["shell_population"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999: raise ValueError("seed must be an integer from 0 to 999999")
        if isinstance(population, bool) or not isinstance(population, int) or not 12 <= population <= 120: raise ValueError("shell_population must be an integer from 12 to 120")
        if result["burst_style"] not in cls.STYLES: raise ValueError(f"burst_style must be one of {list(cls.STYLES)!r}")
        for name, minimum, maximum in (("launch_cadence", .1, 4.), ("burst_size", .08, .65), ("gravity", 0., 1.5), ("trails", .1, 1.), ("crackle", 0., 1.), ("twinkle", 0., 1.)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not minimum <= float(value) <= maximum: raise ValueError(f"{name} must be a finite number from {minimum} to {maximum}")
            result[name] = float(value)
        return result

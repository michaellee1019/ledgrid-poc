"""Scene v2 Firefly Meadow, a deterministic opaque coupled-oscillator field."""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.point_pulse import AimedPoint, PointPulse
from animation.libraries.scene_palettes import HOLIDAY_STOPS, HOLIDAY_TRIPLES, hsv_to_rgb_field


_HUE_GOLDEN = np.float32(0.6180339887)


class FireflySynchronyAnimation(AnimationBase):
    """A calm, semantic-palette meadow of locally synchronising fireflies."""

    PREFERRED_PARAMETERS = ('population', 'synchrony', 'wandering', 'pulse_softness', 'coupling_radius')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Nudge nearby fireflies"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}

    ANIMATION_NAME = "Firefly Meadow"
    ANIMATION_DESCRIPTION = "A wandering meadow of gently synchronising fireflies"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID = "firefly_synchrony"
    COMPONENT_VERSION = 1
    PROVIDER = "python"
    ROLE = "animation"
    FRAME_FORMAT = "rgb_uint8_strip_major"
    TIMING_POLICY = "scaled_context"
    PALETTE_POLICY = "semantic"
    PALETTE_ROLES = ("night", "meadow", "firefly")
    CAPABILITIES = frozenset({"semantic_palette_roles", "scaled_context", "effect_intent"})
    PLANT_MODIFIER_SUPPORT = frozenset()
    SIM_HZ = 24.0
    MAX_CATCH_UP_STEPS = 8
    MIN_FIREFLIES = 20
    MAX_FIREFLIES = 220
    MAX_PEAK_FRACTION = 0.18
    DEFAULTS = MappingProxyType({"seed": 7319, "population": 100, "coupling_radius": 8.0,
        "synchrony": 0.85, "wandering": 0.55, "pulse_softness": 0.50, "meadow_glow": 0.12})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE,
        timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": MappingProxyType({"night": (2.0, 10.0, 18.0), "meadow": (24.0, 148.0, 132.0), "firefly": (150.0, 255.0, 218.0)}),
        "mist": MappingProxyType({"night": (3.0, 9.0, 20.0), "meadow": (40.0, 102.0, 142.0), "firefly": (170.0, 228.0, 245.0)}),
        "spectrum": MappingProxyType({"night": (15.0, 3.0, 34.0), "meadow": (84.0, 38.0, 194.0), "firefly": (54.0, 238.0, 230.0)}),
        "ember": MappingProxyType({"night": (18.0, 3.0, 2.0), "meadow": (156.0, 42.0, 14.0), "firefly": (255.0, 202.0, 92.0)}),
        "rainbow": MappingProxyType({"night": (8.0, 0.0, 18.0), "meadow": (48.0, 12.0, 40.0), "firefly": (40.0, 255.0, 140.0)}),
        "xmas": MappingProxyType({"night": HOLIDAY_TRIPLES["xmas"][0], "meadow": (18.0, 96.0, 36.0), "firefly": HOLIDAY_TRIPLES["xmas"][2]}),
        "fall": MappingProxyType({"night": HOLIDAY_TRIPLES["fall"][0], "meadow": HOLIDAY_TRIPLES["fall"][1], "firefly": HOLIDAY_TRIPLES["fall"][2]}),
        "easter": MappingProxyType({"night": HOLIDAY_TRIPLES["easter"][0], "meadow": HOLIDAY_TRIPLES["easter"][1], "firefly": HOLIDAY_TRIPLES["easter"][2]}),
        "july4": MappingProxyType({"night": HOLIDAY_TRIPLES["july4"][0], "meadow": (28.0, 48.0, 140.0), "firefly": HOLIDAY_TRIPLES["july4"][2]}),
    })

    def __init__(self, controller: Any, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._field = np.zeros((self.width, self.height), dtype=np.float32)
        self._color_field = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._canvas_float = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._canvas_u8 = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        self._presentation_context: ResolvedScene | None = None
        self._last_tick: int | None = None
        self._last_render_key: tuple[Any, ...] | None = None
        self._revision = 0
        self._last_peak_count = 0
        self._pulse = PointPulse(.55)
        self._applied_pulse = None
        self._initialize_simulation()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def palette_roles(cls, palette_id: str) -> Mapping[str, tuple[float, float, float]]:
        return cls.SEMANTIC_PALETTES.get(str(palette_id), cls.SEMANTIC_PALETTES["neutral"])

    @classmethod
    def get_parameter_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "population": {"type": "int", "min": cls.MIN_FIREFLIES, "max": cls.MAX_FIREFLIES, "default": 100, "description": "Number of fireflies in the meadow"},
            "synchrony": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.85, "description": "How strongly nearby fireflies pulse together"},
            "wandering": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.55, "description": "How freely the fireflies drift through the meadow"},
            "pulse_softness": {"type": "float", "min": 0.1, "max": 1.0, "default": 0.50, "description": "How softly each firefly fades in and out"},
            "meadow_glow": {"type": "float", "min": 0.0, "max": 0.5, "default": 0.12, "description": "Subtle glow across the meadow floor"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 7319, "description": "Deterministic meadow layout seed"},
            "coupling_radius": {"type": "float", "min": 2.0, "max": 18.0, "default": 8.0, "description": "Local neighborhood used for synchrony"},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        reset = any(candidate[name] != self.params[name] for name in ("seed", "population"))
        self.params = candidate
        if reset:
            self._initialize_simulation()
        self._last_render_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Firefly Meadow received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Firefly Meadow requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def handle_interaction(self, kind, x, y, strength=1.0):
        point = AimedPoint.from_input(kind, x, y, strength, self.width, self.height)
        return point is not None and self._pulse.trigger(point)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        if self._presentation_context is None:
            phase_time, palette_id, parameters = max(0.0, float(time_elapsed)), "neutral", self.params
        else:
            phase_time = max(0.0, float(self._presentation_context.phase_time))
            palette_id = str(self._presentation_context.palette["palette_id"])
            parameters = self._presentation_context.parameters
        candidate = self._normalized_parameters(parameters)
        if candidate["seed"] != self.params["seed"] or candidate["population"] != self.params["population"]:
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
        point, progress = self._pulse.sample(tick / self.SIM_HZ)
        if point is not None:
            key += (point, progress)
            if point is not self._applied_pulse:
                radius = float(self.params["coupling_radius"])
                depth = self.height - 1 - point.y
                influence = np.clip(1.0 - np.hypot(self.x - point.x, self.y - depth) / radius, 0, 1)
                self.phase = np.mod(self.phase + .9 * point.strength * influence, math.tau)
                self._applied_pulse = point
        if key == self._last_render_key:
            return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        self._paint(palette_id)
        if point is not None:
            canvas = self._pixels.reshape(self.width, self.height, 3)
            pulse = np.asarray(self.palette_roles(palette_id)["firefly"]) * point.strength * (1.0 - progress) * .65
            canvas[point.x, point.y] = np.maximum(canvas[point.x, point.y], pulse)
        self._last_render_key = key
        self._revision += 1
        return RenderedFrame(self._pixels, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "seed": self.params["seed"], "tick": self._last_tick,
            "x": self.x.tobytes(), "y": self.y.tobytes(), "z": self.z.tobytes(),
            "phase": self.phase.tobytes(), "energy": self.energy.tobytes(),
            "heading": self.heading.tobytes(),
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.SIM_HZ, "tick": self._last_tick})

    def _initialize_simulation(self) -> None:
        rng = np.random.default_rng(int(self.params["seed"]))
        count = int(self.params["population"])
        self.x = rng.uniform(0.0, self.width, count).astype(np.float32)
        self.y = rng.uniform(0.0, self.height, count).astype(np.float32)
        self.vx = rng.normal(0.0, 0.22, count).astype(np.float32)
        self.vy = rng.normal(0.0, 0.22, count).astype(np.float32)
        self.phase = rng.uniform(0.0, math.tau, count).astype(np.float32)
        self.frequency = rng.normal(math.tau * 0.72, 0.14, count).astype(np.float32)
        self.energy = np.ones(count, dtype=np.float32)
        self.z = rng.uniform(0.12, 0.96, count).astype(np.float32)
        self.vz = rng.normal(0.0, 0.05, count).astype(np.float32)
        self.heading = rng.uniform(0.0, math.tau, count).astype(np.float32)
        self.turn = rng.normal(0.0, 0.35, count).astype(np.float32)
        self.dart = np.zeros(count, dtype=np.float32)
        self._hue = np.mod(np.arange(count, dtype=np.float32) * _HUE_GOLDEN, np.float32(1.0))
        self._rng = rng
        self._last_tick = None
        self._last_render_key = None
        self._last_peak_count = 0

    def _simulate_step(self, dt: float) -> None:
        dx = self.x[:, None] - self.x[None, :]
        dy = self.y[:, None] - self.y[None, :]
        radius = float(self.params["coupling_radius"])
        near = dx * dx + dy * dy < radius * radius
        np.fill_diagonal(near, False)
        differences = self.phase[None, :] - self.phase[:, None]
        coupling = (np.sin(differences) * near).sum(axis=1) / np.maximum(1, near.sum(axis=1))
        self.phase = np.mod(self.phase + (self.frequency + float(self.params["synchrony"]) * coupling) * dt, math.tau)
        distance = np.minimum(self.phase, math.tau - self.phase)
        self.energy += dt * 0.035 - (distance < 0.28).astype(np.float32) * dt * 0.18
        np.clip(self.energy, 0.0, 1.0, out=self.energy)

        wandering = float(self.params["wandering"])
        flash = np.exp(-np.square(distance / 0.42)) * self.energy
        n = self.x.size
        self.turn += self._rng.normal(0.0, 2.6, n).astype(np.float32) * dt
        self.turn *= math.exp(-1.8 * dt)
        self.turn += (self.x < 3.2).astype(np.float32) * 2.4 * dt
        self.turn -= (self.x > self.width - 4.2).astype(np.float32) * 2.4 * dt
        self.heading = np.mod(
            self.heading + (self.turn * (0.55 + 0.9 * wandering) + 0.22 * wandering * np.sin(self.phase * 0.27)) * dt,
            math.tau,
        )
        self.dart *= math.exp(-2.8 * dt)
        release = self._rng.random(n) < (0.12 + 0.38 * wandering) * dt
        self.dart[release] = 1.0
        cruise = (2.4 + 6.2 * wandering) * (0.38 + 0.62 * self.z)
        speed = cruise * (0.42 + 0.58 * self.dart)
        self.vx = np.cos(self.heading) * speed
        self.vy = np.sin(self.heading) * speed - flash * (3.4 + 5.0 * wandering) * self.z
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vz += self._rng.normal(0.0, 0.22, n).astype(np.float32) * dt
        self.vz += (0.52 - self.z) * 0.28 * dt
        self.vz *= math.exp(-0.85 * dt)
        self.z += self.vz * dt
        hit_left = self.x < 0.45
        hit_right = self.x > self.width - 1.45
        hit_top = self.y < 0.45
        hit_bottom = self.y > self.height - 1.45
        self.heading[hit_left | hit_right] = (math.pi - self.heading[hit_left | hit_right]) % math.tau
        self.heading[hit_top | hit_bottom] = (-self.heading[hit_top | hit_bottom]) % math.tau
        np.clip(self.x, 0.45, self.width - 1.45, out=self.x)
        np.clip(self.y, 0.45, self.height - 1.45, out=self.y)
        np.clip(self.z, 0.08, 1.0, out=self.z)

    def _fly_colors(self, palette_id: str, firefly: np.ndarray) -> np.ndarray:
        if palette_id == "rainbow":
            return hsv_to_rgb_field(self._hue, 0.92, 1.0)
        stops = HOLIDAY_STOPS.get(str(palette_id))
        if stops is not None:
            samples = np.asarray(stops, dtype=np.float32)
            return samples[np.arange(self.x.size) % len(samples)]
        return np.broadcast_to(np.asarray(firefly, dtype=np.float32), (self.x.size, 3)).copy()

    def _stamp_glow(self, index: int, pulse: float, rgb: np.ndarray) -> None:
        depth = float(self.z[index])
        sigma = 0.72 + 3.35 * (depth ** 1.35) * (0.40 + 0.60 * pulse)
        radius = max(2, min(12, int(math.ceil(sigma * 3.1))))
        fx, fy = float(self.x[index]), float(self.y[index])
        x0 = max(0, int(math.floor(fx)) - radius)
        x1 = min(self.width, int(math.floor(fx)) + radius + 1)
        y0 = max(0, int(math.floor(fy)) - radius)
        y1 = min(self.height, int(math.floor(fy)) + radius + 1)
        if x0 >= x1 or y0 >= y1:
            return
        dx = np.arange(x0, x1, dtype=np.float32) - fx
        dy = (np.arange(y0, y1, dtype=np.float32) - fy) * np.float32(0.82)
        r2 = dx[:, None] * dx[:, None] + dy[None, :] * dy[None, :]
        sigma2 = np.float32(sigma * sigma)
        core = np.exp(-r2 / (np.float32(2.0) * sigma2))
        halo = sigma2 / (r2 + sigma2)
        gain = pulse * (0.32 + 0.68 * depth)
        light = gain * (np.float32(0.82) * core + np.float32(0.58) * halo)
        self._field[x0:x1, y0:y1] += light
        self._color_field[x0:x1, y0:y1] += light[..., None] * rgb

    def _paint(self, palette_id: str) -> None:
        palette = self.palette_roles(palette_id)
        night, meadow, firefly = (np.asarray(palette[role], dtype=np.float32) for role in self.PALETTE_ROLES)
        softness = float(self.params["pulse_softness"])
        phase_distance = np.minimum(self.phase, math.tau - self.phase)
        pulse = np.exp(-np.square(phase_distance / (0.16 + 0.65 * softness))) * self.energy
        candidates = np.flatnonzero(pulse > 0.72)
        cap = max(1, int(self.x.size * self.MAX_PEAK_FRACTION))
        if candidates.size > cap:
            keep = candidates[np.argsort(pulse[candidates])[-cap:]]
            muted = np.ones(self.x.size, dtype=bool)
            muted[keep] = False
            pulse[muted & (pulse > 0.72)] = 0.72
        self._last_peak_count = int(np.count_nonzero(pulse > 0.72))
        colors = self._fly_colors(palette_id, firefly)
        self._field.fill(0.0)
        self._color_field.fill(0.0)
        for index in np.flatnonzero(pulse > 0.02):
            self._stamp_glow(int(index), float(pulse[index]), colors[int(index)])
        gradient = np.linspace(0.0, 1.0, self.height, dtype=np.float32)[None, :] ** 3
        self._canvas_float[:] = night
        self._canvas_float += meadow * float(self.params["meadow_glow"]) * gradient[..., None]
        mixed = np.divide(
            self._color_field, self._field[..., None],
            out=np.zeros_like(self._color_field), where=self._field[..., None] > 1e-6,
        )
        core = np.clip((self._field - 0.18) / 0.82, 0.0, 1.0)
        core *= core * (3.0 - 2.0 * core)
        halo = np.clip((self._field - 0.04) / 0.48, 0.0, 1.0)
        clear = 1.0 - core
        self._canvas_float += mixed * (halo * clear * 0.46)[..., None]
        self._canvas_float *= clear[..., None]
        self._canvas_float += mixed * core[..., None]
        np.clip(self._canvas_float, 0.0, 255.0, out=self._canvas_float)
        np.copyto(self._canvas_u8, self._canvas_float, casting="unsafe")
        logical = self._pixels.reshape(self.width, self.height, 3)
        logical[:, ::-1] = self._canvas_u8

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Firefly Meadow does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        seed = result["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        population = result["population"]
        if isinstance(population, bool) or not isinstance(population, int) or not cls.MIN_FIREFLIES <= population <= cls.MAX_FIREFLIES:
            raise ValueError(f"population must be an integer from {cls.MIN_FIREFLIES} to {cls.MAX_FIREFLIES}")
        for name, minimum, maximum in (("coupling_radius", 2.0, 18.0), ("synchrony", 0.0, 2.0), ("wandering", 0.0, 2.0), ("pulse_softness", 0.1, 1.0), ("meadow_glow", 0.0, 0.5)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not minimum <= float(value) <= maximum:
                raise ValueError(f"{name} must be a finite number from {minimum} to {maximum}")
            result[name] = float(value)
        return result

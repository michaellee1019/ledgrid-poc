"""Scene v2 Fish Tank: independent fish, weeds, and bubbles in a tall aquarium."""

from __future__ import annotations

import colorsys
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.scene_palettes import holiday_stop


_HUE_GOLDEN = np.float32(0.6180339887)


class FishTankAnimation(AnimationBase):
    PREFERRED_PARAMETERS = ('fish_count', 'swim_speed', 'schooling', 'bubbles', 'weeds')

    ANIMATION_NAME = "Fish Tank"
    ANIMATION_DESCRIPTION = "A tall aquarium of swimming fish, swaying weeds, and rising bubbles"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "fish_tank", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    SIM_HZ = 24.0
    MAX_CATCH_UP_STEPS = 8
    MIN_FISH, MAX_FISH = 3, 16
    MAX_BUBBLES, MAX_WEEDS = 36, 12
    GRAVEL_ROWS = 5
    DEFAULTS = MappingProxyType({
        "fish_count": 8, "swim_speed": 0.8, "schooling": 0.4,
        "bubbles": 0.45, "weeds": 0.55, "seed": 4240,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE,
        timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS,
    )
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": MappingProxyType({
            "deep": (2.0, 18.0, 42.0), "shallow": (8.0, 92.0, 128.0),
            "gravel": (62.0, 42.0, 28.0), "weed": (18.0, 118.0, 52.0),
            "fish": (255.0, 168.0, 48.0), "highlight": (255.0, 236.0, 196.0),
        }),
        "mist": MappingProxyType({
            "deep": (4.0, 16.0, 38.0), "shallow": (48.0, 118.0, 148.0),
            "gravel": (58.0, 62.0, 72.0), "weed": (36.0, 110.0, 96.0),
            "fish": (186.0, 220.0, 236.0), "highlight": (232.0, 248.0, 255.0),
        }),
        "spectrum": MappingProxyType({
            "deep": (12.0, 4.0, 36.0), "shallow": (64.0, 28.0, 132.0),
            "gravel": (48.0, 22.0, 64.0), "weed": (28.0, 168.0, 122.0),
            "fish": (255.0, 72.0, 186.0), "highlight": (96.0, 255.0, 230.0),
        }),
        "ember": MappingProxyType({
            "deep": (14.0, 6.0, 4.0), "shallow": (48.0, 28.0, 16.0),
            "gravel": (72.0, 36.0, 16.0), "weed": (48.0, 92.0, 22.0),
            "fish": (255.0, 132.0, 28.0), "highlight": (255.0, 214.0, 96.0),
        }),
        "rainbow": MappingProxyType({
            "deep": (4.0, 8.0, 28.0), "shallow": (12.0, 48.0, 78.0),
            "gravel": (42.0, 32.0, 28.0), "weed": (22.0, 128.0, 64.0),
            "fish": (255.0, 40.0, 90.0), "highlight": (40.0, 255.0, 140.0),
        }),
        "xmas": MappingProxyType({
            "deep": (4.0, 18.0, 10.0), "shallow": (18.0, 72.0, 28.0),
            "gravel": (62.0, 36.0, 18.0), "weed": (12.0, 128.0, 42.0),
            "fish": (220.0, 16.0, 28.0), "highlight": (255.0, 196.0, 58.0),
        }),
        "fall": MappingProxyType({
            "deep": (22.0, 8.0, 4.0), "shallow": (72.0, 32.0, 10.0),
            "gravel": (78.0, 42.0, 16.0), "weed": (48.0, 92.0, 18.0),
            "fish": (232.0, 92.0, 18.0), "highlight": (255.0, 186.0, 64.0),
        }),
        "easter": MappingProxyType({
            "deep": (28.0, 10.0, 36.0), "shallow": (72.0, 36.0, 88.0),
            "gravel": (72.0, 52.0, 36.0), "weed": (48.0, 168.0, 92.0),
            "fish": (255.0, 140.0, 186.0), "highlight": (186.0, 255.0, 140.0),
        }),
        "july4": MappingProxyType({
            "deep": (4.0, 8.0, 36.0), "shallow": (16.0, 36.0, 92.0),
            "gravel": (48.0, 36.0, 28.0), "weed": (18.0, 72.0, 36.0),
            "fish": (220.0, 28.0, 36.0), "highlight": (240.0, 244.0, 255.0),
        }),
    })

    def __init__(self, controller: Any, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._canvas_float = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._depth = np.linspace(0.0, 1.0, self.height, dtype=np.float32)[None, :]
        self._xs = np.arange(self.width, dtype=np.float32)[:, None]
        self._ys = np.arange(self.height, dtype=np.float32)[None, :]
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

    @classmethod
    def get_parameter_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "fish_count": {"type": "int", "min": cls.MIN_FISH, "max": cls.MAX_FISH, "default": 8,
                           "description": "How many fish swim the tank."},
            "swim_speed": {"type": "float", "min": 0.25, "max": 1.8, "default": 0.8,
                           "description": "How quickly the fish cruise."},
            "schooling": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.4,
                          "description": "How strongly nearby fish match headings."},
            "bubbles": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.45,
                        "description": "Rising bubble density."},
            "weeds": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.55,
                      "description": "Height and presence of bottom weeds."},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 4240,
                     "description": "Repeatable tank layout seed."},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        reset = any(candidate[name] != self.params[name] for name in ("seed", "fish_count"))
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
            raise ValueError("Fish Tank received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Fish Tank requires a semantic Scene v2 palette")
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
        if candidate["seed"] != self.params["seed"] or candidate["fish_count"] != self.params["fish_count"]:
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
        self._paint(palette_id, phase_time)
        self._last_render_key = key
        return RenderedFrame(self._pixels, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "seed": self.params["seed"],
            "tick": self._last_tick,
            "x": self.x.tobytes(),
            "y": self.y.tobytes(),
            "angle": self.angle.tobytes(),
            "bubble_y": self.bubble_y.tobytes(),
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.SIM_HZ, "tick": self._last_tick})

    def _water_top(self) -> float:
        return 3.0

    def _water_bottom(self) -> float:
        return float(self.height - 1 - self.GRAVEL_ROWS)

    def _initialize_simulation(self) -> None:
        rng = np.random.default_rng(int(self.params["seed"]))
        count = int(self.params["fish_count"])
        top, bottom = self._water_top(), self._water_bottom()
        self.x = rng.uniform(1.0, max(2.0, self.width - 2.0), count).astype(np.float32)
        self.y = rng.uniform(top + 2.0, max(top + 3.0, bottom - 2.0), count).astype(np.float32)
        self.angle = rng.uniform(0.0, math.tau, count).astype(np.float32)
        self.speed = rng.uniform(9.0, 16.0, count).astype(np.float32)
        self.length = rng.uniform(3.6, 6.2, count).astype(np.float32)
        self.phase = rng.uniform(0.0, math.tau, count).astype(np.float32)
        self.hue = np.mod(np.arange(count, dtype=np.float32) * _HUE_GOLDEN, np.float32(1.0))
        bubble_count = self.MAX_BUBBLES
        self.bubble_x = rng.uniform(0.0, self.width, bubble_count).astype(np.float32)
        self.bubble_y = rng.uniform(top, float(self.height - 1), bubble_count).astype(np.float32)
        self.bubble_speed = rng.uniform(10.0, 22.0, bubble_count).astype(np.float32)
        self.bubble_phase = rng.uniform(0.0, math.tau, bubble_count).astype(np.float32)
        weed_count = self.MAX_WEEDS
        self.weed_x = rng.uniform(0.6, self.width - 1.6, weed_count).astype(np.float32)
        self.weed_height = rng.uniform(18.0, 52.0, weed_count).astype(np.float32)
        self.weed_phase = rng.uniform(0.0, math.tau, weed_count).astype(np.float32)
        self._gravel = rng.random((self.width, self.GRAVEL_ROWS)).astype(np.float32)
        self._rng = rng
        self._last_tick = None
        self._last_render_key = None

    def _simulate_step(self, dt: float) -> None:
        count = int(self.x.size)
        schooling = float(self.params["schooling"])
        swim = float(self.params["swim_speed"])
        top, bottom = self._water_top(), self._water_bottom()
        if count > 1 and schooling > 0.0:
            dx = self.x[:, None] - self.x[None, :]
            dy = self.y[:, None] - self.y[None, :]
            near = (dx * dx + dy * dy) < 64.0
            np.fill_diagonal(near, False)
            neighbor_count = near.sum(axis=1)
            has_neighbors = neighbor_count > 0
            safe_count = np.maximum(1, neighbor_count)
            mean_sin = (np.sin(self.angle)[None, :] * near).sum(axis=1) / safe_count
            mean_cos = (np.cos(self.angle)[None, :] * near).sum(axis=1) / safe_count
            school_angle = np.arctan2(mean_sin, mean_cos)
            blend = np.float32(0.12 * schooling)
            blended = np.arctan2(
                (1.0 - blend) * np.sin(self.angle) + blend * np.sin(school_angle),
                (1.0 - blend) * np.cos(self.angle) + blend * np.cos(school_angle),
            )
            self.angle = np.where(has_neighbors, blended, self.angle)
        wander = self._rng.normal(0.0, 0.18 * (1.15 - schooling), count).astype(np.float32)
        self.phase = np.mod(self.phase + dt * (8.0 + self.speed * 0.35), math.tau)
        self.angle = self.angle + wander * dt * 6.0 + 0.35 * np.sin(self.phase) * dt
        vx = np.cos(self.angle) * self.speed * swim
        vy = np.sin(self.angle) * self.speed * swim * 0.42
        self.x = self.x + vx * dt
        self.y = self.y + vy * dt
        hit_left = self.x < 0.6
        hit_right = self.x > self.width - 1.6
        self.angle = np.where(hit_left | hit_right, math.pi - self.angle, self.angle)
        self.x = np.clip(self.x, 0.6, self.width - 1.6)
        hit_top = self.y < top + 1.0
        hit_bottom = self.y > bottom - 1.0
        self.angle = np.where(hit_top | hit_bottom, -self.angle, self.angle)
        self.y = np.clip(self.y, top + 1.0, bottom - 1.0)
        self.bubble_phase = np.mod(self.bubble_phase + dt * 3.4, math.tau)
        self.bubble_x = np.mod(
            self.bubble_x + 0.35 * np.sin(self.bubble_phase) * dt * 8.0, self.width
        )
        self.bubble_y = self.bubble_y - self.bubble_speed * dt
        reset = self.bubble_y < top
        if np.any(reset):
            self.bubble_y[reset] = float(self.height - 1) - self._rng.uniform(0.0, 4.0, int(np.count_nonzero(reset)))
            self.bubble_x[reset] = self._rng.uniform(0.0, self.width, int(np.count_nonzero(reset)))

    def _paint(self, palette_id: str, phase_time: float) -> None:
        palette = self.palette_roles(palette_id)
        deep, shallow, gravel, weed, fish, highlight = (
            np.asarray(palette[name], dtype=np.float32)
            for name in ("deep", "shallow", "gravel", "weed", "fish", "highlight")
        )
        canvas = self._canvas_float
        canvas[:] = 0.0
        depth = self._depth
        water = deep * depth[..., None] + shallow * (1.0 - depth)[..., None]
        caustic = (
            0.5 + 0.5 * np.sin(self._xs * 0.72 + phase_time * 1.55)
        ) * (
            0.5 + 0.5 * np.sin(self._ys * 0.13 + phase_time * 0.62)
        )
        surface = np.clip(1.0 - self._ys / 4.0, 0.0, 1.0)
        water = water + highlight * ((0.14 * caustic * (1.0 - depth) + 0.22 * surface)[..., None])
        canvas[:] = water
        gravel_rows = self.GRAVEL_ROWS
        gravel_view = canvas[:, self.height - gravel_rows:]
        pebble = 0.45 + 0.55 * self._gravel
        gravel_view[:] = gravel * pebble[..., None]
        weed_strength = float(self.params["weeds"])
        visible_weeds = max(0, int(round(3 + weed_strength * 8)))
        for index in range(visible_weeds):
            height = float(self.weed_height[index]) * (0.45 + 0.55 * weed_strength)
            sway = math.sin(phase_time * 1.35 + float(self.weed_phase[index])) * (0.6 + 0.9 * weed_strength)
            for step in range(int(height)):
                t = step / max(1.0, height)
                col = int(round(float(self.weed_x[index]) + sway * t * t))
                row = int(self.height - 1 - gravel_rows - step)
                if 0 <= col < self.width and 0 <= row < self.height:
                    canvas[col, row] = weed * (0.55 + 0.45 * (1.0 - t))
        bubble_strength = float(self.params["bubbles"])
        visible_bubbles = max(0, int(round(bubble_strength * self.MAX_BUBBLES)))
        for index in range(visible_bubbles):
            col = int(self.bubble_x[index]) % self.width
            row = int(np.clip(self.bubble_y[index], 0, self.height - 1))
            sparkle = highlight * (0.35 + 0.65 * bubble_strength)
            canvas[col, row] = np.maximum(canvas[col, row], sparkle * 0.85)
            neighbor = col - 1 if col > 0 else col + 1
            canvas[neighbor, row] = np.maximum(canvas[neighbor, row], sparkle * 0.28)
        for index in range(int(self.x.size)):
            color = self._fish_color(palette_id, index, fish, highlight)
            heading = float(self.angle[index])
            cosine, sine = math.cos(heading), math.sin(heading)
            wag = 0.55 * math.sin(float(self.phase[index]) * 2.2)
            length = float(self.length[index])
            px, py = float(self.x[index]), float(self.y[index])
            for step in range(int(round(length)) + 1):
                along = step / max(1.0, length)
                fx = px - cosine * step
                fy = py - sine * step
                self._put(canvas, fx, fy, color * (0.72 + 0.28 * (1.0 - along)))
            tail_x = px - cosine * (length + 0.8) + (-sine) * wag
            tail_y = py - sine * (length + 0.8) + cosine * wag
            self._put(canvas, tail_x, tail_y, color * 0.55)
            eye_x = px + cosine * 0.4
            eye_y = py + sine * 0.4
            self._put(canvas, eye_x, eye_y, highlight)
        np.clip(canvas, 0.0, 255.0, out=canvas)
        logical = self._pixels.reshape(self.width, self.height, 3)[:, ::-1]
        np.copyto(logical, canvas, casting="unsafe")

    def _fish_color(
        self,
        palette_id: str,
        index: int,
        fish: np.ndarray,
        highlight: np.ndarray,
    ) -> np.ndarray:
        if palette_id == "rainbow":
            red, green, blue = colorsys.hsv_to_rgb(float(self.hue[index]) % 1.0, 0.86, 1.0)
            return np.asarray((red * 255.0, green * 255.0, blue * 255.0), dtype=np.float32)
        festive = holiday_stop(palette_id, float(self.hue[index]))
        if festive is not None:
            return np.asarray(festive, dtype=np.float32)
        mix = float(self.hue[index])
        return fish * (1.0 - 0.45 * mix) + highlight * (0.45 * mix)

    def _put(self, canvas: np.ndarray, x: float, y: float, color: np.ndarray) -> None:
        col = int(round(x))
        row = int(round(y))
        if 0 <= col < self.width and 0 <= row < self.height:
            canvas[col, row] = np.maximum(canvas[col, row], color)

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Fish Tank does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        seed, count = result["seed"], result["fish_count"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        if isinstance(count, bool) or not isinstance(count, int) or not cls.MIN_FISH <= count <= cls.MAX_FISH:
            raise ValueError(f"fish_count must be an integer from {cls.MIN_FISH} to {cls.MAX_FISH}")
        for name, low, high in (
            ("swim_speed", 0.25, 1.8), ("schooling", 0.0, 1.0),
            ("bubbles", 0.0, 1.0), ("weeds", 0.0, 1.0),
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

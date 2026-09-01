#!/usr/bin/env python3
"""Autonomous portrait-platformer tournament for the living plant wall.

The cast and artwork are original, tiny tribute archetypes.  The simulation is
fixed-step and deterministic; rendering is deliberately a separate concern so
themes and HUD choices never influence race outcomes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene


Color = Tuple[int, int, int]


@dataclass
class Platform:
    x: float
    y: float
    width: float
    kind: str = "stone"
    route_index: int = -1
    amplitude: float = 0.0
    phase: float = 0.0
    rate: float = 0.0
    broken_for: float = 0.0
    lifetime: float = -1.0
    owner: int = -1

    def current_x(self, simulation_time: float) -> float:
        if self.amplitude <= 0.0:
            return self.x
        return self.x + math.sin(self.phase + simulation_time * self.rate) * self.amplitude

    @property
    def active(self) -> bool:
        return self.broken_for <= 0.0 and self.lifetime != 0.0


@dataclass
class Racer:
    index: int
    key: str
    name: str
    x: float
    y: float
    colors: Tuple[Color, Color, Color, Color]
    risk: float
    vx: float = 0.0
    vy: float = 0.0
    grounded: bool = True
    grounded_platform: int = 0
    route_index: int = 0
    target_index: int = 1
    checkpoint_x: float = 0.0
    checkpoint_y: float = 0.0
    best_y: float = 0.0
    jump_delay: float = 0.0
    ability_cooldown: float = 0.0
    ability_time: float = 0.0
    charge_time: float = 0.0
    power: int = 2
    stun: float = 0.0
    invulnerable: float = 0.0
    stuck_time: float = 0.0
    last_progress_y: float = 0.0
    finished: bool = False
    finish_time: Optional[float] = None
    web_anchor: Optional[Tuple[float, float]] = None
    web_length: float = 0.0
    ground_pound: bool = False
    glide: bool = False
    form_bonus: float = 0.0
    comeback: float = 0.0
    trail: List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class Enemy:
    kind: str
    x: float
    y: float
    vx: float
    vy: float = 0.0
    age: float = 0.0
    cooldown: float = 0.0


@dataclass
class Barrel:
    owner: int
    x: float
    y: float
    vx: float
    vy: float = 0.0
    age: float = 0.0


@dataclass
class PowerOrb:
    x: float
    y: float
    kind: int
    active: bool = True
    respawn: float = 0.0


class CanopyCupAnimation(AnimationBase):
    """A deterministic, opaque Scene v2 race through a magical plant tower."""

    ANIMATION_NAME = "Canopy Cup: Impossible Ascent"
    ANIMATION_DESCRIPTION = (
        "Four tiny tribute racers web-swing, barrel-roll, glide, and bend "
        "stairways through a seven-heat living-wall tournament"
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "canopy_cup", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    # Scene v2 owns plant geometry, optics, output brightness, and calibration.
    PLANT_MODIFIER_SUPPORT = frozenset()

    PHYSICS_DT = 1.0 / 120.0
    MAX_CATCHUP = 0.10
    MAX_STEPS = 12
    MIN_HEAT_TIME = 44.0
    MAX_HEAT_TIME = 48.0
    RESULTS_TIME = 4.0
    INTRO_TIME = 3.0
    PODIUM_TIME = 8.0
    MAX_ENEMIES = 16
    SOURCE_FPS = 30.0
    THEMES = (
        "tournament", "emerald_gully", "web_city", "barrel_ruins",
        "ivory_valley", "crystal_sunset", "neon_night",
    )
    HEAT_THEMES = THEMES[1:]
    DEFAULTS = MappingProxyType({
        "seed": 4242, "world_theme": "tournament", "qualifying_heats": 7,
        "course_difficulty": 1.0, "enemy_density": .55, "rivalry": .55,
        "powerup_rate": .60, "show_hud": True,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER,
        role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="opaque",
        palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",),
        fidelity_exceptions=(), defaults=DEFAULTS,
    )
    SEMANTIC_TINTS = MappingProxyType({
        "neutral": (1.00, 1.00, 1.00), "mist": (.74, .91, 1.00),
        "spectrum": (1.00, .72, 1.00), "ember": (1.00, .76, .48),
    })

    RACER_SPECS = (
        ("web", "Web-Wisp", ((246, 35, 64), (246, 35, 64), (28, 105, 255), (28, 105, 255)), .86),
        ("barrel", "Barrelback", ((126, 66, 24), (255, 184, 52), (91, 42, 17), (126, 66, 24)), .42),
        ("fern", "Glimmer Fern", ((255, 234, 77), (43, 245, 116), (18, 177, 93), (43, 245, 116)), .72),
        ("ivory", "Ivory Wayfarer", ((248, 247, 222), (248, 247, 222), (235, 55, 72), (248, 247, 222)), .58),
    )

    PALETTES: Dict[str, Dict[str, Color]] = {
        "emerald_gully": {
            "top": (8, 72, 66), "bottom": (2, 16, 27), "mist": (40, 205, 151),
            "stone": (78, 116, 83), "branch": (137, 76, 37), "accent": (255, 222, 65),
        },
        "web_city": {
            "top": (38, 19, 89), "bottom": (3, 9, 31), "mist": (51, 180, 255),
            "stone": (88, 102, 142), "branch": (186, 46, 88), "accent": (255, 78, 111),
        },
        "barrel_ruins": {
            "top": (152, 74, 26), "bottom": (35, 13, 16), "mist": (255, 167, 60),
            "stone": (158, 117, 70), "branch": (92, 47, 24), "accent": (255, 216, 67),
        },
        "ivory_valley": {
            "top": (82, 164, 185), "bottom": (24, 43, 76), "mist": (242, 205, 191),
            "stone": (239, 218, 190), "branch": (210, 92, 82), "accent": (255, 255, 226),
        },
        "crystal_sunset": {
            "top": (168, 36, 112), "bottom": (24, 13, 68), "mist": (255, 133, 168),
            "stone": (91, 214, 231), "branch": (188, 77, 145), "accent": (255, 231, 116),
        },
        "neon_night": {
            "top": (25, 12, 84), "bottom": (1, 3, 18), "mist": (45, 221, 255),
            "stone": (102, 63, 226), "branch": (244, 36, 178), "accent": (107, 255, 96),
        },
    }

    ENEMY_COLORS = {
        "spore": ((255, 98, 202), (255, 230, 116)),
        "beetle": ((64, 255, 132), (18, 88, 50)),
        "jaw": ((255, 74, 75), (120, 20, 44)),
    }

    FONT = {
        "0": ("111", "101", "101", "101", "111"),
        "1": ("010", "110", "010", "010", "111"),
        "2": ("110", "001", "111", "100", "111"),
        "3": ("110", "001", "111", "001", "110"),
        "4": ("101", "101", "111", "001", "001"),
        "5": ("111", "100", "110", "001", "110"),
        "6": ("011", "100", "111", "101", "111"),
        "7": ("111", "001", "010", "010", "010"),
        "8": ("111", "101", "111", "101", "111"),
        "9": ("111", "101", "111", "001", "110"),
    }

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.width, self.height = self.get_strip_info()
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)

        self._canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._gradient = np.zeros_like(self._canvas)
        self._row_ratio = np.linspace(0.0, 1.0, self.height, dtype=np.float32)[:, None]
        self._column_phase = np.linspace(0.0, math.tau, self.width, endpoint=False, dtype=np.float32)[None, :]
        self._plant_obstacle_canvas = np.zeros((self.height, self.width), dtype=bool)
        self._plant_clearance_canvas = np.zeros_like(self._plant_obstacle_canvas)
        self._plant_edge_canvas = np.zeros_like(self._plant_obstacle_canvas)
        self._plant_emitter_sites: List[Tuple[float, float]] = []
        self._plant_geometry_key: Optional[Tuple[Any, ...]] = None

        self.seed = int(self.params["seed"])
        self.game_rng = random.Random(self.seed)
        self.simulation_time = 0.0
        self.accumulator = 0.0
        self.last_elapsed: Optional[float] = None
        self.last_render_elapsed: Optional[float] = None
        self._last_render_tick: Optional[int] = None
        self.last_rendered_frame: Optional[np.ndarray] = None
        self._presentation_context: ResolvedScene | None = None
        self.fixed_steps = 0
        self.dropped_catchup_seconds = 0.0
        self.tournament_index = 0
        self.heat_index = -1
        self.phase = "intro"
        self.phase_time = 0.0
        self.heat_time = 0.0
        self.current_theme = self._resolved_theme(0)
        self.points = [0, 0, 0, 0]
        self.heat_results: List[int] = []
        self.total_finishes = [0, 0, 0, 0]
        self.ability_uses = [0, 0, 0, 0]
        self.enemy_spawns = 0
        self.plant_enemy_spawns = 0
        self.racer_collisions = 0
        self.enemy_collisions = 0
        self.checkpoint_rescues = 0
        self.platforms: List[Platform] = []
        self.route_platforms: List[Platform] = []
        self.enemies: List[Enemy] = []
        self.barrels: List[Barrel] = []
        self.power_orbs: List[PowerOrb] = []
        self.racers: List[Racer] = []
        self._procedural_emitters: List[Tuple[float, float]] = []
        self.next_enemy_time = 2.5
        self._refresh_plant_geometry(force=True)
        self._begin_tournament(reset_clock=False)

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 4242,
                     "description": "Repeatable world seed"},
            "world_theme": {"type": "str", "default": "tournament", "options": list(self.THEMES),
                            "description": "Course world; tournament rotates through every realm"},
            "qualifying_heats": {"type": "int", "min": 2, "max": 7, "default": 7,
                                 "description": "Race length before the championship"},
            "course_difficulty": {"type": "float", "min": 0.6, "max": 1.4, "default": 1.0,
                                  "description": "Course gaps and moving-platform challenge"},
            "enemy_density": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.55,
                              "description": "Obstacle population and spawn cadence"},
            "rivalry": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.55,
                        "description": "Playful racer bumps and barrel knockback"},
            "powerup_rate": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.60,
                        "description": "Signature power recharge rate"},
            "show_hud": {"type": "bool", "default": True,
                         "description": "Show compact standings and heat markers"},
        }

    @classmethod
    def _normalized_parameters(cls, parameters: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(parameters)
        unknown = sorted(set(supplied) - set(cls.DEFAULTS))
        if unknown:
            raise ValueError(f"Canopy Cup received non-local parameters: {', '.join(unknown)}")
        result = dict(cls.DEFAULTS)
        result.update(supplied)
        if isinstance(result["seed"], bool) or not isinstance(result["seed"], int) or not 0 <= result["seed"] <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        if result["world_theme"] not in cls.THEMES:
            raise ValueError("world_theme must be a supported world")
        if isinstance(result["qualifying_heats"], bool) or not isinstance(result["qualifying_heats"], int) or not 2 <= result["qualifying_heats"] <= 7:
            raise ValueError("qualifying_heats must be an integer from 2 to 7")
        for name, low, high in (("course_difficulty", .6, 1.4), ("enemy_density", 0., 1.), ("rivalry", 0., 1.), ("powerup_rate", 0., 1.)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= float(value) <= high:
                raise ValueError(f"{name} must be a number from {low} to {high}")
            result[name] = float(value)
        if not isinstance(result["show_hud"], bool):
            raise ValueError("show_hud must be a boolean")
        return result

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        seed_changed = candidate["seed"] != self.params["seed"]
        self.params = candidate
        if seed_changed:
            self.seed = int(self.params["seed"])
            self.tournament_index = 0
            self.simulation_time = 0.0
            self.fixed_steps = 0
            self.dropped_catchup_seconds = 0.0
            self._begin_tournament(reset_clock=True)
        self.current_theme = self._resolved_theme(max(0, self.heat_index))
        self.last_render_elapsed = None
        self._last_render_tick = None
        self.last_rendered_frame = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Canopy Cup received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Canopy Cup requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def _begin_tournament(self, *, reset_clock: bool) -> None:
        self.game_rng = random.Random(self.seed + self.tournament_index * 1_000_003)
        self.points = [0, 0, 0, 0]
        self.heat_results = []
        self.heat_index = -1
        self.phase = "intro"
        self.phase_time = 0.0
        self.heat_time = 0.0
        self.current_theme = self._resolved_theme(0)
        self.platforms.clear()
        self.route_platforms.clear()
        self.enemies.clear()
        self.barrels.clear()
        self.power_orbs.clear()
        self.racers.clear()
        if reset_clock:
            self.accumulator = 0.0
            self.last_elapsed = None
            self.last_render_elapsed = None
            self._last_render_tick = None

    def _start_heat(self, index: int) -> None:
        self.heat_index = index
        self.phase = "race"
        self.phase_time = 0.0
        self.heat_time = 0.0
        self.current_theme = self._resolved_theme(index)
        self.enemies.clear()
        self.barrels.clear()
        self.next_enemy_time = 2.0
        self._build_course()
        self._reset_racers()

    def _resolved_theme(self, heat_index: int) -> str:
        selected = str(self.params.get("world_theme", "tournament"))
        if selected != "tournament":
            return selected
        qualifiers = int(self.params.get("qualifying_heats", 7))
        if heat_index >= qualifiers:
            return "neon_night"
        return self.HEAT_THEMES[heat_index % len(self.HEAT_THEMES)]

    def _build_course(self) -> None:
        difficulty = float(self.params.get("course_difficulty", 1.0))
        local_seed = self.seed + self.tournament_index * 100_003 + self.heat_index * 7_919
        rng = random.Random(local_seed)
        self.platforms = []
        self.route_platforms = []
        self.power_orbs = []
        self._procedural_emitters = []

        start_y = max(8.0, float(self.height - 3))
        start = Platform(1.0, start_y, max(4.0, float(self.width - 2)), "start", 0)
        self.platforms.append(start)
        self.route_platforms.append(start)
        previous_x = max(1.0, min(self.width - 7.0, self.width * .5 - 4.0))
        previous_y = start_y
        gap = max(4, min(8, int(round(6.5 + difficulty))))
        route_index = 1
        kinds = ("branch", "stone", "vine", "moving", "brittle", "stair")
        while previous_y - gap > 5:
            y = previous_y - gap
            width = float(max(5, min(10, rng.randint(6, 10))))
            max_x = max(1, int(self.width - width - 1))
            preferred = previous_x + rng.randint(-7, 7)
            preferred = max(1.0, min(float(max_x), preferred))
            x = self._least_blocked_x(preferred, y, int(width))
            kind = kinds[(route_index + self.heat_index) % len(kinds)]
            amplitude = 0.0
            phase = 0.0
            rate = 0.0
            if kind == "moving" and self.width >= 12:
                amplitude = min(2.8, max(1.0, (self.width - width) * .10 * difficulty))
                phase = rng.random() * math.tau
                rate = .8 + rng.random() * .65
                x = max(1.0 + amplitude, min(self.width - width - 1.0 - amplitude, x))
            platform = Platform(x, float(y), width, kind, route_index, amplitude, phase, rate)
            self.platforms.append(platform)
            self.route_platforms.append(platform)
            if kind == "vine":
                self._procedural_emitters.append((x + width * .5, y - 1.0))
            if route_index % 3 == 0:
                self.power_orbs.append(PowerOrb(x + width * .5, y - 2.5, route_index % 4))
            if route_index % 4 == 2 and self.width >= 14:
                shortcut_width = 4.0
                side_x = 1.0 if x > self.width * .5 else max(1.0, self.width - shortcut_width - 1.0)
                shortcut_y = y - max(3.0, gap * .55)
                self.platforms.append(Platform(side_x, shortcut_y, shortcut_width, "crystal", -1))
            previous_x, previous_y = x, y
            route_index += 1

        finish_x = max(1.0, min(self.width - 9.0, previous_x - 1.0))
        finish = Platform(finish_x, 4.0, min(10.0, self.width - 2.0), "finish", route_index)
        self.platforms.append(finish)
        self.route_platforms.append(finish)
        self.finish_y = finish.y
        self._revalidate_current_course()

    def _least_blocked_x(self, preferred: float, y: float, width: int) -> float:
        if not np.any(self._plant_clearance_canvas) or self.width <= width + 2:
            return float(max(1, min(self.width - width - 1, int(round(preferred)))))
        y0 = max(0, int(round(y)) - 2)
        y1 = min(self.height, int(round(y)) + 2)
        candidates = []
        for x in range(1, max(2, self.width - width)):
            overlap = int(np.count_nonzero(self._plant_clearance_canvas[y0:y1, x:x + width]))
            candidates.append((overlap, abs(x - preferred), x))
        return float(min(candidates)[2]) if candidates else 1.0

    def _revalidate_current_course(self) -> None:
        if not self.route_platforms:
            return
        # Reposition generated route ledges deterministically against clearance.
        # Semantic state and RNG remain untouched during live modifier changes.
        for platform in self.route_platforms[1:-1]:
            platform.x = self._least_blocked_x(platform.x, platform.y, int(platform.width))
        for racer in self.racers:
            racer.target_index = min(racer.target_index, max(1, len(self.route_platforms) - 1))

    def course_is_reachable(self) -> bool:
        if len(self.route_platforms) < 2:
            return False
        for lower, upper in zip(self.route_platforms, self.route_platforms[1:]):
            vertical = lower.y - upper.y
            lower_center = lower.x + lower.width * .5
            upper_center = upper.x + upper.width * .5
            if vertical <= 0.0 or vertical > 10.0:
                return False
            if abs(lower_center - upper_center) > 12.5:
                return False
        return True

    def _reset_racers(self) -> None:
        bottom = self.route_platforms[0]
        self.racers = []
        favored = (self.seed + self.tournament_index + self.heat_index * 3) % 4
        available = max(1.0, bottom.width - 3.0)
        for index, (key, name, colors, risk) in enumerate(self.RACER_SPECS):
            x = bottom.x + .5 + available * (index + .5) / 4.0
            x = self._safe_racer_x(x, bottom.y - 2.0)
            racer = Racer(
                index, key, name, x, bottom.y - 2.0, colors, risk,
                checkpoint_x=x, checkpoint_y=bottom.y - 2.0,
                best_y=bottom.y - 2.0, last_progress_y=bottom.y - 2.0,
                jump_delay=.12 * index, ability_cooldown=1.8 + .7 * index,
                # Each realm quietly favors one archetype.  The rotating boost
                # is physical (run speed and jump impulse), preserves the same
                # rules for everyone, and prevents one signature kit from
                # owning every long-running installation tournament.
                form_bonus=.35 if index == favored else 0.0,
            )
            self.racers.append(racer)

    def _safe_racer_x(self, preferred: float, y: float) -> float:
        maximum = max(0, self.width - 2)
        preferred_i = max(0, min(maximum, int(round(preferred))))
        if not self._plant_obstacle_active():
            return float(preferred_i)
        return float(min(
            range(maximum + 1),
            key=lambda x: (self._plant_overlap(x, y), abs(x - preferred_i), x),
        ))

    def _plant_overlap(self, x: float, y: float) -> int:
        if not self._plant_obstacle_active():
            return 0
        x0, y0 = int(math.floor(x)), int(math.floor(y))
        x1, y1 = min(self.width, x0 + 2), min(self.height, y0 + 2)
        if x0 < 0 or y0 < 0 or x0 >= x1 or y0 >= y1:
            return 4
        return int(np.count_nonzero(self._plant_obstacle_canvas[y0:y1, x0:x1]))

    def _refresh_plant_geometry(self, *, force: bool = False) -> None:
        obstacle_strength = self.plant_modifier_strength("obstacle")
        emitter_strength = self.plant_modifier_strength("emitter")
        illuminate_strength = self.plant_modifier_strength("illuminate")
        key = (
            obstacle_strength, emitter_strength, illuminate_strength,
            self.width, self.height, self.params.get("plant_clearance"),
            self.params.get("plant_mask_path"), self.params.get("plant_globe_mask_path"),
        )
        if not force and key == self._plant_geometry_key:
            return
        self._plant_geometry_key = key
        self._plant_obstacle_canvas.fill(False)
        self._plant_clearance_canvas.fill(False)
        self._plant_edge_canvas.fill(False)
        self._plant_emitter_sites = []
        if max(obstacle_strength, emitter_strength, illuminate_strength) <= 0.0:
            return
        masks = self.get_plant_masks()
        if obstacle_strength > 0.0:
            self._plant_obstacle_canvas[:] = masks.obstacle.T[::-1]
            self._plant_clearance_canvas[:] = masks.clearance.T[::-1]
        if illuminate_strength > 0.0 or emitter_strength > 0.0:
            self._plant_edge_canvas[:] = masks.obstacle_edge.T[::-1]
        if emitter_strength > 0.0 and np.any(self._plant_edge_canvas):
            ys, xs = np.nonzero(self._plant_edge_canvas)
            stride = max(1, len(xs) // 48)
            self._plant_emitter_sites = [
                (float(x), float(y)) for y, x in zip(ys[::stride], xs[::stride])
            ][:48]

    def _plant_obstacle_active(self) -> bool:
        return self.plant_modifier_strength("obstacle") > 0.0 and np.any(self._plant_obstacle_canvas)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        if self._presentation_context is None:
            phase_time, palette_id, parameters = max(0.0, float(time_elapsed)), "neutral", self.params
        else:
            phase_time = max(0.0, float(self._presentation_context.phase_time))
            palette_id = str(self._presentation_context.palette["palette_id"])
            parameters = self._presentation_context.parameters
        candidate = self._normalized_parameters(parameters)
        if candidate != self.params:
            self.update_parameters(candidate)
        render_tick = int(math.floor(phase_time * self.SOURCE_FPS + 1e-9))
        if (
            self.last_rendered_frame is not None and self._last_render_tick is not None
            and render_tick == self._last_render_tick
        ):
            return self.rendered_frame(self.last_rendered_frame, changed=False)

        if self.last_elapsed is None or phase_time < self.last_elapsed:
            raw_delta = 0.0
        else:
            raw_delta = max(0.0, phase_time - self.last_elapsed)
        self.last_elapsed = phase_time
        self.last_render_elapsed = phase_time
        self._last_render_tick = render_tick
        accepted = min(self.MAX_CATCHUP, raw_delta)
        self.dropped_catchup_seconds += max(0.0, raw_delta - accepted)
        self.accumulator += accepted
        steps = 0
        while self.accumulator + 1e-12 >= self.PHYSICS_DT and steps < self.MAX_STEPS:
            self._fixed_step(self.PHYSICS_DT)
            self.accumulator -= self.PHYSICS_DT
            steps += 1
            self.fixed_steps += 1
        if steps >= self.MAX_STEPS and self.accumulator >= self.PHYSICS_DT:
            self.dropped_catchup_seconds += self.accumulator
            self.accumulator = 0.0

        self._render(palette_id)
        frame = self.next_frame_buffer(clear=False)
        frame.reshape(self.width, self.height, 3)[:] = self._canvas[::-1].transpose(1, 0, 2)
        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _fixed_step(self, dt: float) -> None:
        self.simulation_time += dt
        self.phase_time += dt
        if self.phase == "intro":
            if self.phase_time >= self.INTRO_TIME:
                self._start_heat(0)
            return
        if self.phase == "results":
            if self.phase_time >= self.RESULTS_TIME:
                qualifiers = int(self.params.get("qualifying_heats", 7))
                if self.heat_index < qualifiers:
                    self._start_heat(self.heat_index + 1)
                else:
                    self.phase = "podium"
                    self.phase_time = 0.0
            return
        if self.phase == "podium":
            if self.phase_time >= self.PODIUM_TIME:
                self.tournament_index += 1
                self._begin_tournament(reset_clock=False)
            return
        if self.phase != "race":
            return

        self.heat_time += dt
        self._update_platforms(dt)
        for racer in self.racers:
            self._update_racer(racer, dt)
        self._resolve_racer_collisions()
        self._update_barrels(dt)
        self._update_enemies(dt)
        self._update_power_orbs(dt)
        self._spawn_enemies_if_due()
        all_finished = bool(self.racers) and all(racer.finished for racer in self.racers)
        if (all_finished and self.heat_time >= self.MIN_HEAT_TIME) or self.heat_time >= self.MAX_HEAT_TIME:
            self._finish_heat()

    def _update_platforms(self, dt: float) -> None:
        retained = []
        for platform in self.platforms:
            if platform.broken_for > 0.0:
                platform.broken_for = max(0.0, platform.broken_for - dt)
            if platform.lifetime > 0.0:
                platform.lifetime = max(0.0, platform.lifetime - dt)
            if platform.lifetime != 0.0:
                retained.append(platform)
        self.platforms = retained

    def _update_racer(self, racer: Racer, dt: float) -> None:
        if racer.finished:
            return
        racer.ability_cooldown = max(0.0, racer.ability_cooldown - dt)
        racer.ability_time = max(0.0, racer.ability_time - dt)
        racer.jump_delay = max(0.0, racer.jump_delay - dt)
        racer.stun = max(0.0, racer.stun - dt)
        racer.invulnerable = max(0.0, racer.invulnerable - dt)
        racer.comeback = max(0.0, racer.comeback - dt)
        racer.charge_time += dt * (.05 + .16 * float(self.params.get("powerup_rate", .6)))
        if racer.charge_time >= 1.0 and racer.power < 3:
            racer.charge_time -= 1.0
            racer.power += 1
        racer.glide = False
        if racer.stun > 0.0:
            self._integrate_racer(racer, dt, 0.0)
            return

        planned_index = min(racer.target_index, len(self.route_platforms) - 1)
        if (
            racer.key == "web" and racer.power > 0 and racer.ability_cooldown <= 0.0
            and planned_index + 1 < len(self.route_platforms)
        ):
            # The web kit earns a real shortcut rather than merely decorating
            # the baseline route: a charged swing targets the next-next ledge.
            planned_index += 1
        target = self.route_platforms[planned_index]
        target_x = target.current_x(self.simulation_time) + target.width * .5 - 1.0
        delta_x = target_x - racer.x
        comeback_bonus = 3.5 if racer.comeback > 0.0 else 0.0
        late_surge = max(0.0, min(1.0, (self.heat_time - 30.0) / 8.0))
        max_speed = (
            8.5 + racer.risk * 1.8 + racer.form_bonus * 10.0
            + comeback_bonus + late_surge * 5.0
        )
        acceleration = 42.0 + racer.risk * 14.0
        desired = max(-max_speed, min(max_speed, delta_x * 3.0))
        racer.vx += max(-acceleration * dt, min(acceleration * dt, desired - racer.vx))

        final_pull = self.heat_time > 40.0
        if final_pull:
            finish = self.route_platforms[-1]
            finish_x = finish.current_x(self.simulation_time) + finish.width * .5 - 1.0
            pull_desired = max(-14.0, min(14.0, (finish_x - racer.x) * 4.0))
            racer.vx += max(-70.0 * dt, min(70.0 * dt, pull_desired - racer.vx))
            racer.vy = min(racer.vy, -34.0)
            racer.comeback = max(racer.comeback, .25)

        if self.heat_time > 34.0 and racer.stuck_time > 1.0 and racer.comeback <= 0.0:
            # A visible owner-only leaf staircase is the bounded anti-stall
            # fallback.  It preserves continuous motion and collisions while
            # making the final minute of every heat resolve decisively.
            start_x, start_y = racer.x, racer.y + 2.0
            end_x, end_y = target_x, target.y
            for step in range(1, 4):
                fraction = step / 4.0
                px = start_x + (end_x - start_x) * fraction - 1.0
                py = start_y + (end_y - start_y) * fraction
                self.platforms.append(Platform(
                    max(0.0, min(self.width - 4.0, px)), py, 4.0,
                    "assist", -1, lifetime=5.0, owner=racer.index,
                ))
            racer.comeback = 4.0
            racer.stuck_time = 0.0

        self._maybe_use_ability(racer, target, delta_x)
        if racer.grounded and racer.jump_delay <= 0.0:
            racer.vy = -(30.5 + racer.risk * 1.7 + racer.form_bonus * 8.0
                         + (4.5 if racer.comeback > 0.0 else 0.0)
                         + late_surge * 9.0)
            racer.grounded = False
            racer.grounded_platform = -1
            racer.jump_delay = .13 + (1.0 - racer.risk) * .08
        gravity = 52.0
        if final_pull:
            gravity = 8.0
        if late_surge > 0.0:
            gravity = max(4.0, gravity - late_surge * 8.0)
            racer.invulnerable = max(racer.invulnerable, self.PHYSICS_DT * 2.0)
        if racer.key == "fern" and racer.vy > 3.0 and (racer.ability_time > 0.0 or racer.power > 0):
            if racer.ability_time <= 0.0 and racer.power > 0 and racer.ability_cooldown <= 0.0:
                self._activate_ability(racer, "glide")
            if racer.ability_time > 0.0:
                gravity = 14.0
                racer.glide = True
                racer.vy = min(racer.vy, 8.5)
        self._integrate_racer(racer, dt, gravity)

        if racer.y < racer.best_y - .2:
            racer.best_y = racer.y
            racer.last_progress_y = racer.y
            racer.stuck_time = 0.0
        else:
            racer.stuck_time += dt
        if racer.stuck_time > 3.4 or racer.y > self.height + 4:
            self._rescue_racer(racer)
        if racer.y <= self.finish_y - .2 or racer.route_index >= len(self.route_platforms) - 1:
            racer.finished = True
            racer.finish_time = self.heat_time
            racer.y = max(0.0, self.finish_y - 2.0)
            racer.vx = racer.vy = 0.0
            self.total_finishes[racer.index] += 1

        racer.trail.append((racer.x, racer.y))
        if len(racer.trail) > 7:
            del racer.trail[0]

    def _maybe_use_ability(self, racer: Racer, target: Platform, delta_x: float) -> None:
        if racer.power <= 0 or racer.ability_cooldown > 0.0:
            return
        airborne = not racer.grounded
        if racer.key == "web" and airborne and (abs(delta_x) > 5.0 or racer.vy > 5.0):
            anchor_y = max(1.0, min(racer.y - 4.0, target.y))
            anchor_x = max(0.0, min(self.width - 1.0, target.current_x(self.simulation_time) + target.width * .5))
            racer.web_anchor = (anchor_x, anchor_y)
            racer.web_length = max(3.0, math.hypot(racer.x - anchor_x, racer.y - anchor_y) * .60)
            # Reeling stores elastic energy and turns a defensive catch into a
            # genuine shortcut; the rope constraint still owns the trajectory.
            racer.vy = min(racer.vy, -22.0)
            racer.vx += math.copysign(3.0, anchor_x - racer.x or 1.0)
            self._activate_ability(racer, "web")
        elif racer.key == "barrel" and racer.grounded:
            direction = 1.0 if delta_x >= 0.0 else -1.0
            self.barrels.append(Barrel(racer.index, racer.x + direction * 1.5, racer.y + 1.0, direction * 10.5))
            self._activate_ability(racer, "barrel")
        elif racer.key == "barrel" and airborne and racer.vy > 3.0:
            racer.ground_pound = True
            racer.vy = max(racer.vy, 31.0)
            self._activate_ability(racer, "ground_pound")
        elif racer.key == "fern" and racer.grounded:
            leaf_y = racer.y + 2.0
            self.platforms.append(Platform(max(0.0, racer.x - 1.0), leaf_y, 4.0, "leaf", -1,
                                           lifetime=4.0, owner=racer.index))
            self._activate_ability(racer, "leaf")
        elif racer.key == "ivory" and (abs(delta_x) > 5.0 or racer.stuck_time > 1.0):
            direction = 1.0 if delta_x >= 0.0 else -1.0
            for step in range(1, 4):
                px = max(0.0, min(self.width - 4.0, racer.x + direction * step * 2.2))
                py = racer.y - step * 2.2 + 2.0
                self.platforms.append(Platform(px, py, 4.0, "ivory", -1,
                                               lifetime=4.2, owner=racer.index))
            if racer.stuck_time > 2.0:
                racer.x = self._safe_racer_x(target.current_x(self.simulation_time) + target.width * .5 - 1.0,
                                              target.y - 3.0)
                racer.y = target.y - 3.0
                racer.vy = 3.0
            self._activate_ability(racer, "stair")

    def _activate_ability(self, racer: Racer, _kind: str) -> None:
        racer.power -= 1
        racer.ability_cooldown = 4.4 + (1.0 - racer.risk) * 1.8
        racer.ability_time = 1.1 if racer.key in ("web", "fern") else .45
        self.ability_uses[racer.index] += 1

    def _integrate_racer(self, racer: Racer, dt: float, gravity: float) -> None:
        old_x, old_y = racer.x, racer.y
        if racer.grounded_platform >= 0 and racer.grounded_platform < len(self.platforms):
            platform = self.platforms[racer.grounded_platform]
            if platform.active and platform.amplitude > 0.0:
                now = platform.current_x(self.simulation_time)
                before = platform.current_x(self.simulation_time - dt)
                racer.x += now - before
        racer.vy = min(38.0, racer.vy + gravity * dt)
        racer.vx *= max(0.0, 1.0 - dt * (1.4 if racer.grounded else .20))
        racer.x += racer.vx * dt
        if racer.x < 0.0:
            racer.x = 0.0
            racer.vx = abs(racer.vx) * .35
        elif racer.x > self.width - 2.0:
            racer.x = max(0.0, self.width - 2.0)
            racer.vx = -abs(racer.vx) * .35
        if self._plant_overlap(racer.x, racer.y):
            racer.x = old_x
            racer.vx *= -.28

        racer.y += racer.vy * dt
        racer.grounded = False
        racer.grounded_platform = -1
        landed = self._landing_platform(racer, old_y, racer.y)
        if landed is not None:
            index, platform, platform_x = landed
            racer.y = platform.y - 2.0
            spring = platform.kind == "leaf"
            racer.vy = -37.0 if spring else 0.0
            racer.grounded = not spring
            racer.grounded_platform = index if not spring else -1
            if platform.route_index >= 0 and platform.route_index >= racer.route_index:
                racer.route_index = platform.route_index
                racer.target_index = min(platform.route_index + 1, len(self.route_platforms) - 1)
                if platform.route_index and platform.route_index % 4 == 0:
                    racer.checkpoint_x = max(platform_x, min(platform_x + platform.width - 2.0, racer.x))
                    racer.checkpoint_y = racer.y
            if platform.kind == "brittle" and racer.ground_pound:
                platform.broken_for = 3.5
                racer.vy = -18.0
                racer.grounded = False
            racer.ground_pound = False
        elif self._plant_obstacle_active() and self._plant_overlap(racer.x, racer.y):
            if racer.vy >= 0.0:
                racer.y = old_y
                racer.vy = -max(4.0, racer.vy * .28)
                racer.grounded = True
            else:
                racer.y = old_y
                racer.vy = abs(racer.vy) * .18

        if racer.web_anchor is not None:
            if racer.ability_time <= 0.0:
                racer.web_anchor = None
            else:
                ax, ay = racer.web_anchor
                cx, cy = racer.x + 1.0, racer.y + 1.0
                dx, dy = cx - ax, cy - ay
                distance = math.hypot(dx, dy)
                if distance > racer.web_length and distance > 1e-6:
                    nx, ny = dx / distance, dy / distance
                    correction = distance - racer.web_length
                    racer.x -= nx * correction
                    racer.y -= ny * correction
                    outward = racer.vx * nx + racer.vy * ny
                    if outward > 0.0:
                        racer.vx -= outward * nx
                        racer.vy -= outward * ny
                    racer.vx += (-ny) * (4.0 if racer.risk > .5 else 2.5) * dt

    def _landing_platform(
        self, racer: Racer, old_y: float, new_y: float
    ) -> Optional[Tuple[int, Platform, float]]:
        if racer.vy < 0.0:
            return None
        old_bottom, new_bottom = old_y + 2.0, new_y + 2.0
        candidates = []
        for index, platform in enumerate(self.platforms):
            if not platform.active or platform.owner not in (-1, racer.index):
                continue
            if old_bottom > platform.y + .08 or new_bottom < platform.y - .08:
                continue
            px = platform.current_x(self.simulation_time)
            if racer.x + 1.75 <= px or racer.x + .25 >= px + platform.width:
                continue
            candidates.append((platform.y, index, platform, px))
        if not candidates:
            return None
        _, index, platform, px = min(candidates, key=lambda item: item[0])
        return index, platform, px

    def _resolve_racer_collisions(self) -> None:
        rivalry = float(self.params.get("rivalry", .55))
        if rivalry <= 0.0:
            return
        for left_index in range(len(self.racers)):
            left = self.racers[left_index]
            if left.finished or left.invulnerable > 0.0:
                continue
            for right in self.racers[left_index + 1:]:
                if right.finished or right.invulnerable > 0.0:
                    continue
                if abs((left.x + 1.0) - (right.x + 1.0)) >= 1.8:
                    continue
                if abs((left.y + 1.0) - (right.y + 1.0)) >= 1.8:
                    continue
                direction = -1.0 if left.x <= right.x else 1.0
                impulse = 2.0 + rivalry * 3.5
                left.vx += direction * impulse
                right.vx -= direction * impulse
                left.x += direction * .08
                right.x -= direction * .08
                self.racer_collisions += 1

    def _update_barrels(self, dt: float) -> None:
        rivalry = float(self.params.get("rivalry", .55))
        retained = []
        for barrel in self.barrels:
            barrel.age += dt
            barrel.vy = min(34.0, barrel.vy + 48.0 * dt)
            old_y = barrel.y
            barrel.x += barrel.vx * dt
            barrel.y += barrel.vy * dt
            if barrel.x < 0.0 or barrel.x > self.width - 1.0:
                barrel.x = max(0.0, min(self.width - 1.0, barrel.x))
                barrel.vx *= -.7
            for platform in self.platforms:
                px = platform.current_x(self.simulation_time)
                if platform.active and old_y + 1.0 <= platform.y <= barrel.y + 1.0 and px <= barrel.x <= px + platform.width:
                    barrel.y = platform.y - 1.0
                    barrel.vy = -5.0
                    barrel.vx *= .97
                    break
            for racer in self.racers:
                if racer.index == barrel.owner or racer.invulnerable > 0.0 or racer.finished:
                    continue
                if abs((racer.x + 1.0) - barrel.x) < 1.6 and abs((racer.y + 1.0) - barrel.y) < 1.6:
                    racer.vx += math.copysign(5.0 + 6.0 * rivalry, barrel.vx or 1.0)
                    racer.vy = min(racer.vy, -5.0)
                    racer.stun = .20 + .25 * rivalry
                    racer.invulnerable = .65
                    barrel.vx *= -.55
            if barrel.age < 4.5 and barrel.y < self.height + 3:
                retained.append(barrel)
        self.barrels = retained[:8]

    def _spawn_enemies_if_due(self) -> None:
        density = float(self.params.get("enemy_density", .55))
        emitter = self.plant_modifier_strength("emitter")
        if density <= 0.0 and emitter <= 0.0:
            return
        if self.heat_time + 1e-9 < self.next_enemy_time:
            return
        interval = max(.85, 4.4 - density * 2.7 - emitter * 1.0)
        self.next_enemy_time += interval
        cap = min(self.MAX_ENEMIES, 3 + int(round(density * 9 + emitter * 4)))
        if len(self.enemies) >= cap:
            return
        procedural = list(self._procedural_emitters)
        plant = list(self._plant_emitter_sites) if emitter > 0.0 else []
        sources = procedural + plant
        if not sources:
            sources = [(self.width * .5, self.height * .55)]
        source_index = self.game_rng.randrange(len(sources))
        x, y = sources[source_index]
        kind = ("spore", "beetle", "jaw")[self.enemy_spawns % 3]
        direction = -1.0 if self.game_rng.random() < .5 else 1.0
        self.enemies.append(Enemy(kind, x, y, direction * (3.0 if kind != "jaw" else 0.0),
                                  -8.0 if kind == "spore" else 0.0))
        self.enemy_spawns += 1
        if source_index >= len(procedural):
            self.plant_enemy_spawns += 1

    def _update_enemies(self, dt: float) -> None:
        retained = []
        for enemy in self.enemies:
            enemy.age += dt
            enemy.cooldown = max(0.0, enemy.cooldown - dt)
            if enemy.kind == "spore":
                enemy.vy += 24.0 * dt
                enemy.x += enemy.vx * dt
                enemy.y += enemy.vy * dt
                if enemy.vy > 0.0:
                    landing = self._point_landing(enemy.x, enemy.y - enemy.vy * dt, enemy.y)
                    if landing is not None:
                        enemy.y = landing - 1.0
                        enemy.vy = -10.0
            elif enemy.kind == "beetle":
                enemy.x += enemy.vx * dt
                if enemy.x <= 0.0 or enemy.x >= self.width - 1.0:
                    enemy.vx *= -1.0
                    enemy.x = max(0.0, min(self.width - 1.0, enemy.x))
            for racer in self.racers:
                if racer.finished or racer.invulnerable > 0.0 or enemy.cooldown > 0.0:
                    continue
                active_jaw = enemy.kind != "jaw" or int(enemy.age * 3.0) % 2 == 0
                if active_jaw and abs((racer.x + 1.0) - enemy.x) < 1.7 and abs((racer.y + 1.0) - enemy.y) < 1.7:
                    racer.vx += -6.0 if racer.x < enemy.x else 6.0
                    racer.vy = -7.0
                    racer.stun = .32
                    racer.invulnerable = .8
                    enemy.cooldown = .8
                    self.enemy_collisions += 1
            if enemy.age < 12.0 and -4.0 < enemy.y < self.height + 4.0:
                retained.append(enemy)
        self.enemies = retained[:self.MAX_ENEMIES]

    def _point_landing(self, x: float, old_y: float, new_y: float) -> Optional[float]:
        candidates = []
        for platform in self.platforms:
            px = platform.current_x(self.simulation_time)
            if platform.active and old_y + 1.0 <= platform.y <= new_y + 1.0 and px <= x <= px + platform.width:
                candidates.append(platform.y)
        return min(candidates) if candidates else None

    def _update_power_orbs(self, dt: float) -> None:
        rate = float(self.params.get("powerup_rate", .6))
        for orb in self.power_orbs:
            if not orb.active:
                orb.respawn -= dt * (.4 + rate)
                if orb.respawn <= 0.0:
                    orb.active = True
                continue
            for racer in self.racers:
                if racer.finished:
                    continue
                if abs((racer.x + 1.0) - orb.x) < 1.6 and abs((racer.y + 1.0) - orb.y) < 2.0:
                    racer.power = min(3, racer.power + 1)
                    racer.ability_cooldown = max(0.0, racer.ability_cooldown - 1.0)
                    orb.active = False
                    orb.respawn = 10.0 - rate * 5.0
                    break

    def _rescue_racer(self, racer: Racer) -> None:
        racer.x = self._safe_racer_x(racer.checkpoint_x, racer.checkpoint_y)
        racer.y = racer.checkpoint_y
        target = self.route_platforms[min(racer.target_index, len(self.route_platforms) - 1)]
        target_x = target.current_x(self.simulation_time) + target.width * .5 - 1.0
        racer.vx = math.copysign(7.0, target_x - racer.x or 1.0)
        racer.vy = -35.0
        racer.stun = 0.0
        racer.invulnerable = 1.0
        racer.comeback = 4.0
        racer.grounded = False
        racer.grounded_platform = -1
        racer.ability_cooldown = 0.0
        racer.power = max(2, racer.power)
        racer.stuck_time = 0.0
        racer.web_anchor = None
        racer.ground_pound = False
        self.checkpoint_rescues += 1

    def _rank_racers(self) -> List[int]:
        return sorted(
            range(len(self.racers)),
            key=lambda index: (
                self.racers[index].finish_time is None,
                self.racers[index].finish_time if self.racers[index].finish_time is not None else math.inf,
                self.racers[index].best_y,
                index,
            ),
        )

    def _finish_heat(self) -> None:
        ranking = self._rank_racers()
        awards = (5, 3, 2, 1)
        multiplier = 2 if self.heat_index >= int(self.params.get("qualifying_heats", 7)) else 1
        for place, racer_index in enumerate(ranking):
            self.points[racer_index] += awards[place] * multiplier
        self.heat_results = ranking
        self.phase = "results"
        self.phase_time = 0.0

    def logical_state(self) -> Tuple[Any, ...]:
        return (
            self.phase, self.heat_index, round(self.phase_time, 5), round(self.heat_time, 5),
            tuple(self.points), tuple((
                racer.index, round(racer.x, 5), round(racer.y, 5),
                round(racer.vx, 5), round(racer.vy, 5), racer.route_index,
                racer.power, racer.finished,
            ) for racer in self.racers),
            tuple((enemy.kind, round(enemy.x, 4), round(enemy.y, 4)) for enemy in self.enemies),
            self.enemy_spawns, tuple(self.ability_uses), self.checkpoint_rescues,
        )

    # ------------------------------ rendering ------------------------------

    def _render(self, palette_id: str = "neutral") -> None:
        self._render_background()
        self._render_geometry()
        self._render_entities()
        if bool(self.params.get("show_hud", True)):
            self._render_hud()
        # Worlds establish course identity; the selected Scene v2 palette is
        # the only palette authority for the final presentation plane.
        tint = np.asarray(self.SEMANTIC_TINTS.get(palette_id, self.SEMANTIC_TINTS["neutral"]), dtype=np.float32)
        np.multiply(self._canvas, tint, out=self._canvas, casting="unsafe")

    def _render_background(self) -> None:
        palette = self.PALETTES[self.current_theme]
        top = np.asarray(palette["top"], dtype=np.float32)
        bottom = np.asarray(palette["bottom"], dtype=np.float32)
        gradient_rows = top[None, :] * (1.0 - self._row_ratio) + bottom[None, :] * self._row_ratio
        self._gradient[:] = np.clip(gradient_rows[:, None, :], 0, 255).astype(np.uint8)
        self._canvas[:] = self._gradient

        mist = np.sin(
            self._row_ratio * (math.tau * 4.0)
            + self._column_phase * 1.7
            + self.simulation_time * .55
        )
        mist_mask = mist > .76
        mist_color = np.asarray(palette["mist"], dtype=np.uint16)
        selected = self._canvas[mist_mask].astype(np.uint16)
        self._canvas[mist_mask] = np.minimum(255, (selected * 3 + mist_color) // 4).astype(np.uint8)

        # Monument-like diagonal shadows and a distant central tower.
        for y in range(self.height):
            diagonal = int((y * .17 + self.simulation_time * .7) % max(1, self.width))
            self._dim_pixel(diagonal, y, .55)
            self._dim_pixel((diagonal + 1) % self.width, y, .72)
        center = self.width // 2
        tower_half = max(1, self.width // 10)
        self._canvas[:, max(0, center - tower_half):min(self.width, center + tower_half + 1)] //= 2

        # Analytic fireflies never consume gameplay RNG.
        accent = palette["accent"]
        for index in range(min(14, max(4, self.width // 2))):
            x = int((index * 11 + self.seed * 3 + math.sin(self.simulation_time * .7 + index) * 3) % self.width)
            y = int((index * 23 + self.seed + math.sin(self.simulation_time * .5 + index * 2) * 5) % self.height)
            self._pixel(x, y, accent, additive=True)

    def _render_geometry(self) -> None:
        palette = self.PALETTES[self.current_theme]
        for platform in self.platforms:
            if not platform.active:
                continue
            x = int(round(platform.current_x(self.simulation_time)))
            y = int(round(platform.y))
            color = palette["stone"]
            if platform.kind in ("branch", "vine", "start"):
                color = palette["branch"]
            elif platform.kind == "moving":
                color = palette["mist"]
            elif platform.kind == "brittle":
                color = (230, 126, 55)
            elif platform.kind in ("stair", "ivory"):
                color = (244, 231, 207)
            elif platform.kind == "crystal":
                color = palette["accent"]
            elif platform.kind == "leaf":
                color = (54, 255, 120)
            elif platform.kind == "assist":
                color = self.RACER_SPECS[platform.owner][2][0]
            elif platform.kind == "finish":
                color = (255, 232, 72)
            self._rect(x, y, max(1, int(round(platform.width))), 1, color)
            if platform.kind in ("vine", "branch") and y + 1 < self.height:
                self._pixel(x + max(0, int(platform.width) // 2), y + 1, (34, 132, 62))

        illuminate = self.plant_modifier_strength("illuminate")
        obstacle = self.plant_modifier_strength("obstacle")
        if obstacle > 0.0 and np.any(self._plant_obstacle_canvas):
            base = np.asarray((18, int(80 + obstacle * 70), 54), dtype=np.uint8)
            self._canvas[self._plant_obstacle_canvas] = base
        if illuminate > 0.0 and np.any(self._plant_edge_canvas):
            glow = np.asarray((int(80 + 175 * illuminate), 255, int(80 + 90 * illuminate)), dtype=np.uint8)
            self._canvas[self._plant_edge_canvas] = glow

        for orb in self.power_orbs:
            if not orb.active:
                continue
            pulse = .65 + .35 * math.sin(self.simulation_time * 5.0 + orb.kind)
            color = self.RACER_SPECS[orb.kind][2][0]
            shaded = tuple(int(channel * pulse) for channel in color)
            self._pixel(int(round(orb.x)), int(round(orb.y)), shaded, additive=True)

    def _render_entities(self) -> None:
        for enemy in self.enemies:
            colors = self.ENEMY_COLORS[enemy.kind]
            x, y = int(round(enemy.x)), int(round(enemy.y))
            if enemy.kind == "jaw":
                self._pixel(x, y, colors[int(enemy.age * 3.0) & 1])
                self._pixel(x + (1 if int(enemy.age * 3.0) & 1 else -1), y, colors[0])
            else:
                self._pixel(x, y, colors[0])
                self._pixel(x, y + 1, colors[1])

        for barrel in self.barrels:
            x, y = int(round(barrel.x)), int(round(barrel.y))
            self._pixel(x, y, (153, 76, 27))
            self._pixel(x + (int(barrel.age * 8) & 1), y, (255, 178, 47))

        for racer in self.racers:
            for trail_index, (tx, ty) in enumerate(racer.trail[:-1]):
                fade = (trail_index + 1) / max(1, len(racer.trail)) * .28
                color = tuple(int(channel * fade) for channel in racer.colors[0])
                self._pixel(int(round(tx + 1)), int(round(ty + 1)), color, additive=True)
            if racer.web_anchor is not None:
                self._line(
                    int(round(racer.x + 1)), int(round(racer.y)),
                    int(round(racer.web_anchor[0])), int(round(racer.web_anchor[1])),
                    (210, 235, 255),
                )
            x, y = int(round(racer.x)), int(round(racer.y))
            colors = racer.colors
            if racer.invulnerable > 0.0 and int(racer.invulnerable * 12) & 1:
                colors = ((255, 255, 255),) * 4
            self._pixel(x, y, colors[0])
            self._pixel(x + 1, y, colors[1])
            self._pixel(x, y + 1, colors[2])
            self._pixel(x + 1, y + 1, colors[3])
            if racer.glide:
                self._pixel(x - 1, y, (102, 255, 186), additive=True)
                self._pixel(x + 2, y, (102, 255, 186), additive=True)
            if racer.finished:
                phase = self.simulation_time * 7.0 + racer.index * 1.3
                for offset in range(4):
                    angle = phase + offset * math.tau / 4.0
                    self._pixel(x + 1 + int(round(math.cos(angle) * 3)),
                                y + 1 + int(round(math.sin(angle) * 2)),
                                self.PALETTES[self.current_theme]["accent"], additive=True)
            elif self.heat_time > 40.0:
                # The tournament's final living-vine pull is visible rather
                # than a hidden timeout correction.
                self._pixel(x, y + 2, (80, 255, 118), additive=True)
                self._pixel(x, y + 3, (38, 160, 82), additive=True)

    def _render_hud(self) -> None:
        ranking = sorted(range(4), key=lambda index: (-self.points[index], index))
        for place, racer_index in enumerate(ranking):
            color = self.RACER_SPECS[racer_index][2][0]
            y = min(self.height - 1, 1 + place * 3)
            self._pixel(0, y, color)
            for pip in range(min(4, self.points[racer_index] // 5)):
                self._pixel(1, y + pip % 2, color)
        if self.phase == "race":
            total = int(self.params.get("qualifying_heats", 7)) + 1
            self._draw_number(max(1, self.heat_index + 1), max(1, self.width - 4), 1,
                              self.PALETTES[self.current_theme]["accent"])
            for index in range(total):
                color = (255, 225, 80) if index <= self.heat_index else (44, 49, 67)
                self._pixel(self.width - 1, min(self.height - 1, 8 + index * 2), color)
        elif self.phase == "intro":
            self._draw_cup(self.width // 2, max(8, self.height // 2))
        elif self.phase in ("results", "podium"):
            ranking = self.heat_results if self.phase == "results" and self.heat_results else ranking
            base_y = max(8, self.height // 2 - 8)
            for place, racer_index in enumerate(ranking[:3]):
                x = self.width // 2 - 6 + place * 6
                height = 5 - place
                self._rect(x, base_y + 7 - height, 4, height, (82, 83, 108))
                self._rect(x + 1, base_y + 5 - height, 2, 2, self.RACER_SPECS[racer_index][2][0])
                self._draw_number(place + 1, x, base_y + 9, (255, 230, 90))

    def _draw_cup(self, cx: int, cy: int) -> None:
        color = (255, 223, 70)
        self._rect(cx - 2, cy - 3, 5, 1, color)
        self._rect(cx - 1, cy - 2, 3, 3, color)
        self._pixel(cx - 3, cy - 2, color)
        self._pixel(cx + 3, cy - 2, color)
        self._rect(cx, cy + 1, 1, 2, color)
        self._rect(cx - 2, cy + 3, 5, 1, color)

    def _draw_number(self, value: int, x: int, y: int, color: Color) -> None:
        glyph = self.FONT[str(abs(int(value)) % 10)]
        for row, line in enumerate(glyph):
            for column, bit in enumerate(line):
                if bit == "1":
                    self._pixel(x + column, y + row, color)

    def _pixel(self, x: int, y: int, color: Color, additive: bool = False) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            if additive:
                self._canvas[y, x] = np.maximum(self._canvas[y, x], color)
            else:
                self._canvas[y, x] = color

    def _dim_pixel(self, x: int, y: int, factor: float) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self._canvas[y, x] = (self._canvas[y, x].astype(np.float32) * factor).astype(np.uint8)

    def _rect(self, x: int, y: int, width: int, height: int, color: Color) -> None:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + width), min(self.height, y + height)
        if x0 < x1 and y0 < y1:
            self._canvas[y0:y1, x0:x1] = color

    def _line(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx + dy
        while True:
            self._pixel(x0, y0, color, additive=True)
            if x0 == x1 and y0 == y1:
                break
            twice = error * 2
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def get_runtime_stats(self) -> Dict[str, Any]:
        ranking = self._rank_racers() if self.racers else list(range(4))
        return {
            "phase": self.phase,
            "tournament": self.tournament_index + 1,
            "heat": self.heat_index + 1 if self.heat_index >= 0 else 0,
            "theme": self.current_theme,
            "leader": self.RACER_SPECS[ranking[0]][1],
            "standings": {
                self.RACER_SPECS[index][1]: self.points[index] for index in range(4)
            },
            "progress": {
                racer.name: round(max(0.0, min(1.0, 1.0 - racer.best_y / max(1.0, self.height - 3))), 3)
                for racer in self.racers
            },
            "finishes": {
                self.RACER_SPECS[index][1]: self.total_finishes[index] for index in range(4)
            },
            "ability_uses": {
                self.RACER_SPECS[index][1]: self.ability_uses[index] for index in range(4)
            },
            "enemy_spawns": self.enemy_spawns,
            "plant_enemy_spawns": self.plant_enemy_spawns,
            "active_enemies": len(self.enemies),
            "racer_collisions": self.racer_collisions,
            "enemy_collisions": self.enemy_collisions,
            "checkpoint_rescues": self.checkpoint_rescues,
            "fixed_steps": self.fixed_steps,
            "dropped_catchup_seconds": round(self.dropped_catchup_seconds, 6),
            "course_reachable": self.course_is_reachable() if self.route_platforms else True,
        }

#!/usr/bin/env python3
"""Self-playing behind-the-truck desert rally for the portrait LED wall.

The trucks, courses, and art are original. A fixed-step race model owns
standings, damage, nitro, and pit upgrades. Drawing is a chase camera up the
wall: the near road is at the bottom, the horizon is a thin band at the top,
and nothing in the renderer feeds back into the result.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from animation import AnimationBase


Color = Tuple[int, int, int]


@dataclass
class Hazard:
    id: int
    distance: float
    lane: float
    kind: str


@dataclass
class Course:
    length: float
    biome: str
    seed: int
    samples: np.ndarray
    line: np.ndarray
    elevation: np.ndarray
    grip: np.ndarray
    jumps: np.ndarray
    hazards: Tuple[Hazard, ...]


@dataclass
class Truck:
    index: int
    name: str
    personality: str
    skill: float
    aggression: float
    body: Color
    cab: Color
    progress: float = 0.0
    lane: float = 0.0
    speed: float = 0.0
    damage: float = 0.0
    nitro: float = 0.35
    boosting: float = 0.0
    airborne: float = 0.0
    air_total: float = 0.0
    was_on_jump: bool = False
    contact_lock: float = 0.0
    finished: bool = False
    finish_time: Optional[float] = None
    dnf: bool = False
    cash: int = 0
    engine: int = 1
    tires: int = 1
    shocks: int = 1
    nitro_tank: int = 1
    hazard_hits: int = 0
    money_bags: int = 0
    nitro_uses: int = 0
    landings: int = 0
    last_place: int = 0
    last_upgrade: str = ""
    career_distance: float = 0.0


@dataclass
class RoadSlice:
    rows: np.ndarray
    distance: np.ndarray
    nearness: np.ndarray
    center: np.ndarray
    half: np.ndarray
    elevation: np.ndarray
    y_index: np.ndarray


class BajaRallyAnimation(AnimationBase):
    """CPU-only rally. The camera truck chases four rivals up the wall."""

    ANIMATION_NAME = "Baja Rally"
    ANIMATION_DESCRIPTION = (
        "A self-playing behind-the-truck desert rally with hills, rivals, "
        "nitro, damage, and pit upgrades"
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("obstacle", "hazard", "illuminate"))

    SIM_DT = 1.0 / 30.0
    MAX_CATCHUP = 0.20
    MAX_STEPS = 8
    LOOKAHEAD = 90.0
    DEPTH_GAMMA = 1.35
    CAMERA_BACK = 6.0
    START_HOLD = 0.95
    PIT_TIME = 2.6
    PODIUM_TIME = 4.2
    POINT_AWARDS = (8, 5, 3, 2, 1)
    CASH_AWARDS = (420, 260, 160, 90, 40)
    UPGRADE_COST = (150, 320, 600)
    CUPS = (("frontier", 3), ("coast", 4), ("spine", 5))
    BIOMES = ("desert", "gravel", "canyon", "coast")
    TIMES_OF_DAY = ("day", "dusk", "night")
    START_GAPS = (0.0, 5.0, 12.0, 20.0, 30.0)
    START_LANES = (0.0, -0.42, 0.48, -0.18, 0.30)
    TRUCK_SPECS = (
        ("Dustline", "hero", 0.94, 0.58, (236, 186, 42), (248, 246, 236)),
        ("Redline", "charger", 0.76, 0.96, (214, 42, 34), (120, 16, 18)),
        ("Coyote", "wild", 0.56, 0.86, (222, 104, 28), (92, 42, 16)),
        ("Gate", "blocker", 0.82, 0.72, (42, 96, 214), (16, 36, 110)),
        ("Mesa", "cautious", 0.90, 0.24, (36, 158, 82), (214, 224, 206)),
    )
    PACE = {"hero": 1.09, "charger": 1.03, "wild": 1.0, "blocker": 0.99, "cautious": 1.0}
    PALETTES: Dict[str, Dict[str, Color]] = {
        "desert": {
            "sky_top": (20, 52, 122),
            "sky_horizon": (232, 154, 78),
            "shoulder": (58, 32, 16),
            "dirt": (168, 104, 48),
            "dirt_dark": (96, 58, 26),
            "edge": (228, 186, 104),
            "dash": (244, 228, 186),
            "crest": (255, 214, 140),
        },
        "gravel": {
            "sky_top": (58, 78, 108),
            "sky_horizon": (176, 174, 168),
            "shoulder": (42, 40, 38),
            "dirt": (124, 116, 108),
            "dirt_dark": (72, 68, 64),
            "edge": (206, 202, 190),
            "dash": (232, 230, 220),
            "crest": (236, 236, 228),
        },
        "canyon": {
            "sky_top": (42, 18, 72),
            "sky_horizon": (196, 86, 58),
            "shoulder": (46, 18, 22),
            "dirt": (132, 62, 52),
            "dirt_dark": (72, 30, 32),
            "edge": (186, 112, 78),
            "dash": (228, 176, 132),
            "crest": (255, 160, 96),
        },
        "coast": {
            "sky_top": (18, 92, 148),
            "sky_horizon": (132, 196, 204),
            "shoulder": (48, 58, 42),
            "dirt": (186, 158, 96),
            "dirt_dark": (102, 86, 48),
            "edge": (236, 214, 164),
            "dash": (248, 240, 214),
            "crest": (255, 244, 210),
        },
    }
    HAZARD_COLORS = {
        "rock": (78, 70, 72),
        "animal": (236, 228, 210),
        "atv": (196, 36, 36),
        "money": (255, 208, 36),
    }
    FONT = {
        "1": ("010", "110", "010", "010", "111"),
        "2": ("110", "001", "111", "100", "111"),
        "3": ("110", "001", "111", "001", "110"),
        "4": ("101", "101", "111", "001", "001"),
        "5": ("111", "100", "110", "001", "110"),
    }

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "speed": 1.0,
            "brightness": 0.8,
            "render_fps": 40.0,
            "seed": 1993,
            "cup": "series",
            "biome": "rotation",
            "time_of_day": "day",
            "difficulty": 1.0,
            "traffic": 0.65,
            "show_hud": True,
        })
        self.params = {**self.default_params, **self.config}
        self._validate_local_params()
        self.horizon = 7 if self.height >= 24 else max(3, self.height // 5)
        self._near_half = max(2.4, self.width * 0.44)
        self._far_half = 0.55
        self._canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._core_canvas = np.zeros((self.height, self.width), dtype=bool)
        self._clearance_canvas = np.zeros((self.height, self.width), dtype=bool)
        self._hazard_canvas = np.zeros((self.height, self.width), dtype=bool)
        self._foliage_canvas = np.zeros((self.height, self.width), dtype=bool)
        self._globe_canvas = np.zeros((self.height, self.width), dtype=bool)
        self._globe_edge_canvas = np.zeros((self.height, self.width), dtype=bool)
        self._plant_key: Optional[Tuple[Any, ...]] = None
        self.leg_length_override: Optional[float] = None
        self.seed = int(self.params["seed"])
        self.loop_index = 0
        self.cup_index = 0
        self.leg_index = 0
        self.points = [0, 0, 0, 0, 0]
        self.cup_wins = [0, 0, 0, 0, 0]
        self.overtakes = 0
        self.bumps = 0
        self.sim_time = 0.0
        self.leg_time = 0.0
        self.phase_time = 0.0
        self.hold = self.START_HOLD
        self.phase = "race"
        self.pit_applied = False
        self.podium_winner = -1
        self.accumulator = 0.0
        self.fixed_steps = 0
        self.dropped_catchup_seconds = 0.0
        self.last_elapsed: Optional[float] = None
        self._last_render_tick: Optional[int] = None
        self.last_rendered_frame: Optional[np.ndarray] = None
        self.consumed: set = set()
        self.trucks: List[Truck] = []
        self.course: Course
        self._refresh_plant_geometry(force=True)
        self._start_leg(0)

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.35, "max": 2.5, "default": 1.0,
                      "description": "Race clock multiplier"},
            "render_fps": {"type": "float", "min": 24.0, "max": 60.0, "default": 40.0,
                           "description": "Maximum visual refresh rate"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1993,
                     "description": "Repeatable series seed"},
            "cup": {"type": "str", "default": "series",
                    "options": ["series", "frontier", "coast", "spine"],
                    "description": "Championship rotation or one repeating cup"},
            "biome": {"type": "str", "default": "rotation",
                      "options": ["rotation", *self.BIOMES],
                      "description": "Surface family. Rotation follows the leg"},
            "time_of_day": {"type": "str", "default": "day", "options": list(self.TIMES_OF_DAY),
                            "description": "Sky, headlight, and terrain light"},
            "difficulty": {"type": "float", "min": 0.5, "max": 1.5, "default": 1.0,
                           "description": "Rival pace and hazard density"},
            "traffic": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.65,
                        "description": "Rocks, animals, ATVs, and money bags"},
            "show_hud": {"type": "bool", "default": True,
                         "description": "Show place, damage, and nitro"},
        })
        return schema

    def _validate_local_params(self) -> None:
        if str(self.params.get("cup", "series")) not in ("series", "frontier", "coast", "spine"):
            self.params["cup"] = "series"
        if str(self.params.get("biome", "rotation")) not in ("rotation", *self.BIOMES):
            self.params["biome"] = "rotation"
        if str(self.params.get("time_of_day", "day")) not in self.TIMES_OF_DAY:
            self.params["time_of_day"] = "day"
        self.params["difficulty"] = max(0.5, min(1.5, float(self.params.get("difficulty", 1.0))))
        self.params["traffic"] = max(0.0, min(1.0, float(self.params.get("traffic", 0.65))))
        self.params["seed"] = max(0, min(999999, int(self.params.get("seed", 1993))))

    def update_parameters(self, new_params: Dict[str, Any]):
        seed_changed = "seed" in new_params and int(new_params["seed"]) != int(self.params.get("seed", 1993))
        super().update_parameters(new_params)
        self._validate_local_params()
        self._refresh_plant_geometry(force=True)
        if seed_changed:
            self.seed = int(self.params["seed"])
            self._reset_series()
        self._last_render_tick = None
        self.last_rendered_frame = None

    def _reset_series(self) -> None:
        self.loop_index = 0
        self.cup_index = 0
        self.points = [0, 0, 0, 0, 0]
        self.cup_wins = [0, 0, 0, 0, 0]
        self.overtakes = 0
        self.bumps = 0
        self.sim_time = 0.0
        self.accumulator = 0.0
        self.fixed_steps = 0
        self.dropped_catchup_seconds = 0.0
        self.last_elapsed = None
        self.trucks = []
        self._start_leg(0)

    @property
    def hero(self) -> Truck:
        return self.trucks[0]

    def _cup_name(self) -> str:
        selected = str(self.params.get("cup", "series"))
        if selected == "series":
            return self.CUPS[self.cup_index % len(self.CUPS)][0]
        return selected

    def _leg_count(self) -> int:
        return dict(self.CUPS)[self._cup_name()]

    def _leg_length(self) -> float:
        if self.leg_length_override is not None:
            return max(60.0, float(self.leg_length_override))
        base = {"frontier": 280.0, "coast": 340.0, "spine": 420.0}[self._cup_name()]
        return base + self.leg_index * 24.0

    def _start_leg(self, index: int) -> None:
        self.leg_index = index
        self.phase = "race"
        self.phase_time = 0.0
        self.leg_time = 0.0
        self.hold = self.START_HOLD
        self.pit_applied = False
        self.podium_winner = -1
        self.consumed = set()
        self.course = self._build_course()
        if not self.trucks:
            self.trucks = [self._make_truck(i) for i in range(len(self.TRUCK_SPECS))]
        for truck, gap, lane in zip(self.trucks, self.START_GAPS, self.START_LANES):
            truck.progress = gap
            truck.lane = lane
            truck.speed = 0.0
            truck.damage = 0.0
            truck.dnf = False
            truck.finished = False
            truck.finish_time = None
            truck.airborne = 0.0
            truck.air_total = 0.0
            truck.was_on_jump = False
            truck.boosting = 0.0
            truck.nitro = 0.35
            truck.contact_lock = 0.0
            truck.hazard_hits = 0
            truck.landings = 0

    def _make_truck(self, index: int) -> Truck:
        name, personality, skill, aggression, body, cab = self.TRUCK_SPECS[index]
        return Truck(
            index=index, name=name, personality=personality, skill=skill,
            aggression=aggression, body=body, cab=cab,
            progress=self.START_GAPS[index], lane=self.START_LANES[index],
        )

    def _build_course(self) -> Course:
        length = self._leg_length()
        locked = str(self.params.get("biome", "rotation"))
        biome = locked if locked in self.BIOMES else self.BIOMES[(self.cup_index * 2 + self.leg_index) % 4]
        leg_seed = (
            self.seed + self.loop_index * 104729 + self.cup_index * 10007
            + self.leg_index * 97 + self.BIOMES.index(biome) * 13
        )
        rng = random.Random(leg_seed)
        count = max(12, int(length / 2.0))
        samples = np.linspace(0.0, length, count, dtype=np.float64)
        phase = rng.random() * math.tau
        line = (
            np.sin(samples / 17.0 + phase) * 0.50
            + np.sin(samples / 7.5 + phase * 1.7) * 0.20
        )
        np.clip(line, -0.72, 0.72, out=line)
        elevation = (
            np.sin(samples / 34.0 + phase) * 1.05
            + np.sin(samples / 13.0 + 1.4) * 0.42
        )
        slope = np.empty_like(elevation)
        slope[:-1] = elevation[1:] - elevation[:-1]
        slope[-1] = slope[-2]
        steep = slope < -0.055
        jumps = steep.copy()
        jumps[1:] |= steep[:-1]
        jumps[:-1] |= steep[1:]
        grip = 0.78 + 0.22 * (0.5 + 0.5 * np.sin(samples / 22.0 + phase))
        if biome == "gravel":
            grip = grip * 0.84
        elif biome == "canyon":
            grip = grip * 0.90
        elif biome == "coast":
            grip = grip * 0.94
        traffic = float(self.params.get("traffic", 0.65))
        difficulty = float(self.params.get("difficulty", 1.0))
        hazard_count = int(round(traffic * (8.0 + 8.0 * difficulty)))
        hazards: List[Hazard] = []
        kinds = ("rock", "animal", "atv", "money")
        weights = (0.34, 0.28, 0.20, 0.18)
        for index in range(hazard_count):
            distance = 28.0 + (index + 0.5) * (length - 56.0) / max(1, hazard_count)
            distance = min(length - 16.0, max(22.0, distance + rng.uniform(-5.0, 5.0)))
            sample = int(np.searchsorted(samples, distance))
            if 0 <= sample < len(jumps) and jumps[sample]:
                distance = min(length - 16.0, distance + 8.0)
            lane = rng.choice((-0.62, -0.28, 0.0, 0.26, 0.58))
            kind = rng.choices(kinds, weights=weights, k=1)[0]
            hazards.append(Hazard(len(hazards), float(distance), float(lane), kind))
        return Course(
            length=float(length), biome=biome, seed=leg_seed, samples=samples,
            line=line, elevation=elevation, grip=grip, jumps=jumps,
            hazards=tuple(hazards),
        )

    def _sample(self, values: np.ndarray, distance: float) -> float:
        return float(np.interp(distance, self.course.samples, values))

    def _line_delta(self, progress: float) -> float:
        return self._sample(self.course.line, progress + 2.0) - self._sample(self.course.line, progress)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        self._integrate_elapsed(time_elapsed)
        fps = max(24.0, min(60.0, float(self.params.get("render_fps", 40.0))))
        render_tick = int(math.floor(max(0.0, time_elapsed) * fps + 1e-9))
        if self.last_rendered_frame is not None and render_tick == self._last_render_tick:
            return self.rendered_frame(self.last_rendered_frame, changed=False)
        self._last_render_tick = render_tick
        self._render()
        frame = self.next_frame_buffer(clear=False)
        frame.reshape(self.width, self.height, 3)[:] = self._canvas[::-1].transpose(1, 0, 2)
        self.apply_brightness_array(frame, out=frame)
        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _integrate_elapsed(self, time_elapsed: float) -> None:
        if self.last_elapsed is None or time_elapsed < self.last_elapsed:
            raw_delta = 0.0
        else:
            raw_delta = max(0.0, time_elapsed - self.last_elapsed)
        self.last_elapsed = time_elapsed
        scaled = raw_delta * max(0.05, float(self.params.get("speed", 1.0)))
        accepted = min(self.MAX_CATCHUP, scaled)
        self.dropped_catchup_seconds += max(0.0, scaled - accepted)
        self.accumulator += accepted
        steps = 0
        while self.accumulator + 1e-12 >= self.SIM_DT and steps < self.MAX_STEPS:
            self._fixed_step(self.SIM_DT)
            self.accumulator -= self.SIM_DT
            steps += 1
            self.fixed_steps += 1
        if steps >= self.MAX_STEPS and self.accumulator >= self.SIM_DT:
            self.dropped_catchup_seconds += self.accumulator
            self.accumulator = 0.0

    def _fixed_step(self, dt: float) -> None:
        self.sim_time += dt
        self.phase_time += dt
        self._refresh_plant_geometry()
        if self.phase == "race":
            self._advance_race(dt)
        elif self.phase == "pit":
            self._advance_pit(dt)
        elif self.phase == "podium":
            if self.phase_time >= self.PODIUM_TIME:
                self._begin_next_cup()

    def _advance_race(self, dt: float) -> None:
        self.leg_time += dt
        if self.hold > 0.0:
            self.hold -= dt
            return
        need_road = self._obstacle_strength() > 0.0 or self._hazard_strength() > 0.0
        road = self._road_slice() if need_road else None
        before = [truck.progress for truck in self.trucks]
        for truck in sorted(self.trucks, key=lambda item: -item.progress):
            self._drive(truck, dt, road)
        self._resolve_bumps()
        self._count_overtakes(before)
        if self._field_done():
            self._close_leg()

    def _field_done(self) -> bool:
        hero_done = self.hero.finished or self.hero.dnf
        everyone = all(truck.finished or truck.dnf for truck in self.trucks)
        timed_out = self.leg_time > self.course.length / 9.0 + 8.0
        return hero_done or everyone or timed_out

    def _drive(self, truck: Truck, dt: float, road: Optional[RoadSlice]) -> None:
        truck.contact_lock = max(0.0, truck.contact_lock - dt)
        if truck.airborne > 0.0:
            truck.airborne = max(0.0, truck.airborne - dt)
            if truck.airborne == 0.0:
                truck.landings += 1
                if truck.speed > 26.0 and truck.shocks <= 2:
                    self._hurt(truck, 0.045 + max(0.0, truck.speed - 26.0) * 0.006)
        if truck.finished:
            truck.speed = 0.0
            return
        delta = self._line_delta(truck.progress)
        grip = self._sample(self.course.grip, truck.progress)
        if truck.boosting > 0.0:
            truck.boosting = max(0.0, truck.boosting - dt)
            truck.nitro = max(0.0, truck.nitro - 0.08 * dt)
        else:
            truck.nitro = min(1.0, truck.nitro + (0.055 + 0.028 * truck.nitro_tank) * dt)
            if self._wants_nitro(truck, delta):
                truck.boosting = 1.05 + 0.12 * truck.nitro_tank
                truck.nitro = max(0.0, truck.nitro - 0.34)
                truck.nitro_uses += 1
        target = self._target_speed(truck, delta, grip)
        if self._drafting(truck):
            target *= 1.06
        accel = 9.0 + truck.engine * 1.4
        if truck.speed < target:
            truck.speed = min(target, truck.speed + accel * dt)
        else:
            truck.speed = max(target, truck.speed - 16.0 * dt)
        desired = self._desired_lane(truck, delta)
        if road is not None:
            desired = self._plant_lane(truck, desired, road)
        rate = (1.45 + truck.tires * 0.32) * (0.65 + 0.35 * truck.skill)
        if truck.airborne > 0.0:
            rate *= 0.18
        truck.lane = max(-0.9, min(0.9, truck.lane + max(-rate * dt, min(rate * dt, desired - truck.lane))))
        on_jump = self._sample(self.course.jumps.astype(np.float64), truck.progress) > 0.55
        if on_jump and not truck.was_on_jump and truck.airborne <= 0.0 and truck.speed > 18.0:
            truck.air_total = 0.46 + 0.06 * truck.shocks
            truck.airborne = truck.air_total
        truck.was_on_jump = on_jump
        truck.progress += truck.speed * dt
        truck.career_distance += truck.speed * dt
        if truck.progress >= self.course.length:
            truck.progress = self.course.length
            truck.finished = True
            truck.finish_time = self.leg_time
            truck.speed = 0.0
            return
        self._collect_hazards(truck)
        if road is not None:
            self._plant_contact(truck, road)

    def _target_speed(self, truck: Truck, line_delta: float, grip: float) -> float:
        if truck.dnf:
            return 11.0
        corner = min(1.0, abs(line_delta) * 22.0)
        if truck.personality == "cautious":
            corner = min(1.0, corner * 1.08)
        base = (19.0 + truck.engine * 2.55) * self.PACE[truck.personality]
        if truck.index:
            difficulty = float(self.params.get("difficulty", 1.0))
            base *= 0.82 + 0.18 * difficulty
        target = base * (1.0 - 0.36 * corner) * (0.60 + 0.40 * grip)
        if truck.boosting > 0.0:
            target *= 1.30
        if truck.damage > 0.72:
            target *= 0.80
        return target

    def _wants_nitro(self, truck: Truck, line_delta: float) -> bool:
        if truck.nitro < 0.5 or truck.dnf or truck.airborne > 0.0:
            return False
        straight = abs(line_delta) < 0.035
        if not straight:
            return False
        rivals_ahead = any(
            0.0 < other.progress - truck.progress < 24.0
            for other in self.trucks if other.index != truck.index
        )
        if truck.personality == "cautious":
            return rivals_ahead and truck.damage < 0.35 and truck.nitro > 0.85
        return rivals_ahead or truck.aggression > 0.9

    def _drafting(self, truck: Truck) -> bool:
        if truck.airborne > 0.0:
            return False
        return any(
            1.2 < other.progress - truck.progress < 6.5 and abs(other.lane - truck.lane) < 0.22
            for other in self.trucks if other.index != truck.index
        )

    def _desired_lane(self, truck: Truck, line_delta: float) -> float:
        lookahead = 8.0 + truck.skill * 14.0
        desired = self._sample(self.course.line, truck.progress + lookahead * 0.5)
        best_money: Optional[Tuple[float, float]] = None
        for hazard in self.course.hazards:
            if hazard.id in self.consumed:
                continue
            gap = hazard.distance - truck.progress
            if gap < 1.5 or gap > lookahead:
                continue
            if hazard.kind == "money":
                if truck.skill > 0.6 and (best_money is None or gap < best_money[0]):
                    best_money = (gap, hazard.lane)
                continue
            if abs(hazard.lane - desired) < 0.40:
                desired += 0.64 if desired >= hazard.lane else -0.64
        if best_money is not None:
            desired = desired * (1.0 - 0.28 * truck.skill) + best_money[1] * (0.28 * truck.skill)
        if truck.personality == "blocker":
            gap = truck.progress - self.hero.progress
            if 0.0 < gap < 18.0:
                desired = desired * 0.35 + self.hero.lane * 0.65
        noise = {"wild": 0.34, "charger": 0.12, "blocker": 0.06, "cautious": 0.04, "hero": 0.03}
        desired += math.sin(truck.progress * 0.23 + truck.index * 1.7) * noise[truck.personality]
        if abs(line_delta) > 0.08 and truck.personality == "cautious":
            desired *= 0.85
        return max(-0.88, min(0.88, desired))

    def _collect_hazards(self, truck: Truck) -> None:
        if truck.airborne > 0.05:
            return
        for hazard in self.course.hazards:
            if hazard.id in self.consumed:
                continue
            if abs(hazard.distance - truck.progress) > 1.7:
                continue
            if abs(hazard.lane - truck.lane) > 0.30:
                continue
            self.consumed.add(hazard.id)
            if hazard.kind == "money":
                truck.cash += 80
                truck.money_bags += 1
                continue
            shock = 1.0 - 0.16 * min(3, truck.shocks)
            if truck.boosting > 0.0:
                shock += 0.12
            self._hurt(truck, 0.14 * shock)
            truck.speed *= 0.84
            truck.hazard_hits += 1

    def _hurt(self, truck: Truck, amount: float) -> None:
        if truck.dnf or amount <= 0.0:
            return
        truck.damage = min(1.0, truck.damage + amount)
        if truck.damage >= 1.0:
            truck.dnf = True
            truck.boosting = 0.0
            truck.speed = min(truck.speed, 11.0)

    def _resolve_bumps(self) -> None:
        trucks = self.trucks
        for index, left in enumerate(trucks):
            if left.contact_lock > 0.0:
                continue
            for right in trucks[index + 1:]:
                if right.contact_lock > 0.0:
                    continue
                if abs(left.progress - right.progress) >= 2.1 or abs(left.lane - right.lane) >= 0.26:
                    continue
                if abs(left.lane - right.lane) < 1e-6:
                    left.lane -= 0.16
                    right.lane += 0.16
                else:
                    left.lane += math.copysign(0.16, left.lane - right.lane)
                    right.lane += math.copysign(0.16, right.lane - left.lane)
                left.lane = max(-0.9, min(0.9, left.lane))
                right.lane = max(-0.9, min(0.9, right.lane))
                left.speed *= 0.985
                right.speed *= 0.985
                left.contact_lock = 0.4
                right.contact_lock = 0.4
                self.bumps += 1

    def _count_overtakes(self, before: Sequence[float]) -> None:
        for index, left in enumerate(self.trucks):
            for right in self.trucks[index + 1:]:
                ahead_before = before[left.index] >= before[right.index]
                ahead_now = left.progress >= right.progress
                if ahead_before != ahead_now:
                    self.overtakes += 1

    def _close_leg(self) -> None:
        if self.phase != "race":
            return
        unfinished = sorted(
            (truck for truck in self.trucks if not truck.finished),
            key=lambda truck: (-truck.progress, truck.index),
        )
        for offset, truck in enumerate(unfinished):
            truck.finished = True
            truck.finish_time = self.leg_time + 1.0 + offset * 0.05
            truck.speed = 0.0
        for place, truck in enumerate(self._ranking()):
            self.points[truck.index] += self.POINT_AWARDS[place]
            truck.cash += self.CASH_AWARDS[place]
            truck.last_place = place
        self.phase = "pit"
        self.phase_time = 0.0
        self.pit_applied = False

    def _ranking(self) -> List[Truck]:
        return sorted(
            self.trucks,
            key=lambda truck: (
                truck.finish_time is None,
                truck.finish_time if truck.finish_time is not None else math.inf,
                -truck.progress,
                truck.index,
            ),
        )

    def _advance_pit(self, dt: float) -> None:
        del dt
        if not self.pit_applied:
            for truck in self.trucks:
                self._upgrade(truck)
                truck.damage = 0.0
                truck.dnf = False
            self.pit_applied = True
        if self.phase_time >= self.PIT_TIME:
            if self.leg_index + 1 < self._leg_count():
                self._start_leg(self.leg_index + 1)
            else:
                self.podium_winner = max(range(len(self.trucks)), key=lambda index: (self.points[index], -index))
                self.cup_wins[self.podium_winner] += 1
                self.phase = "podium"
                self.phase_time = 0.0

    def _upgrade(self, truck: Truck) -> None:
        options = []
        if truck.shocks < 3:
            options.append((truck.hazard_hits * 3 + truck.landings * 2, "shocks", truck.shocks))
        if truck.engine < 3:
            options.append((4 + truck.last_place * 2, "engine", truck.engine))
        if truck.tires < 3:
            options.append((2 + truck.hazard_hits, "tires", truck.tires))
        if truck.nitro_tank < 3:
            options.append((truck.nitro_uses * 2, "nitro_tank", truck.nitro_tank))
        options.sort(key=lambda item: (-item[0], item[1]))
        for _need, name, level in options:
            cost = self.UPGRADE_COST[level - 1]
            if truck.cash >= cost:
                setattr(truck, name, level + 1)
                truck.cash -= cost
                truck.last_upgrade = name
                return

    def _begin_next_cup(self) -> None:
        self.loop_index += 1
        if str(self.params.get("cup", "series")) == "series":
            self.cup_index += 1
        self.points = [0, 0, 0, 0, 0]
        self._start_leg(0)

    def _camera(self) -> float:
        return self.hero.progress - self.CAMERA_BACK

    def _screen_t(self, rel: float) -> float:
        rel = min(self.LOOKAHEAD, max(0.0, rel))
        return 1.0 - (rel / self.LOOKAHEAD) ** (1.0 / self.DEPTH_GAMMA)

    def _y_from_t(self, screen_t: float) -> int:
        span = max(1, self.height - 1 - self.horizon)
        return max(self.horizon, min(self.height - 1, int(round(self.horizon + screen_t * span))))

    def _road_slice(self) -> RoadSlice:
        rows = np.arange(self.horizon, self.height, dtype=np.int32)
        span = max(1, self.height - 1 - self.horizon)
        nearness = (rows - self.horizon) / span
        rel = self.LOOKAHEAD * (1.0 - nearness) ** self.DEPTH_GAMMA
        distance = self._camera() + rel
        line = np.interp(distance, self.course.samples, self.course.line)
        elevation = np.interp(distance, self.course.samples, self.course.elevation)
        half = self._far_half + nearness * (self._near_half - self._far_half)
        center = (self.width - 1) * 0.5 + line * (self.width * 0.16) * (0.25 + 0.75 * nearness)
        center, half = self._fit_centers(rows, center, half)
        y_index = np.full(self.height, -1, dtype=np.int32)
        y_index[rows] = np.arange(len(rows), dtype=np.int32)
        return RoadSlice(rows, distance, nearness, center, half, elevation, y_index)

    def _fit_centers(self, rows: np.ndarray, center: np.ndarray, half: np.ndarray):
        if self._obstacle_strength() <= 0.0 or not np.any(self._clearance_canvas):
            return center, half
        fitted_center = center.copy()
        fitted_half = half.copy()
        blocked = self._clearance_canvas
        for index, y in enumerate(rows):
            row = blocked[int(y)]
            if not row.any():
                continue
            placed = self._best_run(row, float(center[index]), float(half[index]))
            if placed is not None:
                fitted_center[index], fitted_half[index] = placed
        return fitted_center, fitted_half

    def _best_run(self, row: np.ndarray, ideal: float, want: float) -> Optional[Tuple[float, float]]:
        best_score: Optional[Tuple[float, float]] = None
        best: Optional[Tuple[float, float]] = None
        start: Optional[int] = None
        width = self.width

        def consider(left: int, right: int) -> None:
            nonlocal best_score, best
            run = right - left + 1
            if run < 3:
                return
            fitted_half = min(want, run * 0.5 - 0.2)
            if fitted_half < 1.0:
                return
            low = left + fitted_half
            high = right - fitted_half
            if low > high:
                fitted_half = max(1.0, run * 0.5 - 0.2)
                low = high = (left + right) * 0.5
            placed = min(high, max(low, ideal))
            score = (abs(placed - ideal), -fitted_half)
            if best_score is None or score < best_score:
                best_score = score
                best = (placed, fitted_half)

        for x in range(width):
            if not row[x]:
                if start is None:
                    start = x
            elif start is not None:
                consider(start, x - 1)
                start = None
        if start is not None:
            consider(start, width - 1)
        return best

    def _truck_box(self, progress: float, lane: float, road: RoadSlice) -> Optional[Tuple[int, int, int, int]]:
        rel = progress - self._camera()
        if rel < 0.35 or rel > self.LOOKAHEAD:
            return None
        y = self._y_from_t(self._screen_t(rel))
        index = int(road.y_index[y])
        if index < 0:
            return None
        nearness = float(road.nearness[index])
        width = max(2, min(self.width - 1, int(round(2 + 7 * nearness))))
        height = max(2, min(14, int(round(2 + 6 * nearness))))
        x = int(round(float(road.center[index]) + lane * float(road.half[index]) * 0.92))
        return x, y, width, height

    def _plant_lane(self, truck: Truck, desired: float, road: RoadSlice) -> float:
        mask = self._clearance_canvas if self._obstacle_strength() > 0.0 else self._hazard_canvas
        if not np.any(mask):
            return desired
        best_lane = desired
        best_cost = None
        for lane in np.linspace(-0.85, 0.85, 9):
            box = self._truck_box(truck.progress, float(lane), road)
            overlap = 0 if box is None else self._overlap_count(mask, *box)
            cost = (overlap * 12.0 + abs(float(lane) - desired), abs(float(lane) - truck.lane))
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_lane = float(lane)
        return best_lane

    def _plant_contact(self, truck: Truck, road: RoadSlice) -> None:
        if truck.finished:
            return
        box = self._truck_box(truck.progress, truck.lane, road)
        if box is None:
            return
        if self._obstacle_strength() > 0.0 and self._overlap_count(self._core_canvas, *box):
            self._hurt(truck, 0.28 * self._obstacle_strength() * self.SIM_DT * 4.0)
            truck.speed *= 0.96
            truck.lane += -math.copysign(0.08, truck.lane if abs(truck.lane) > 0.05 else 1.0)
            truck.lane = max(-0.9, min(0.9, truck.lane))
        elif self._hazard_strength() > 0.0 and self._overlap_count(self._hazard_canvas, *box):
            strength = self._hazard_strength()
            self._hurt(truck, 0.16 * strength * self.SIM_DT * 3.0)
            truck.speed = max(10.0, truck.speed - 7.0 * strength * self.SIM_DT)

    @staticmethod
    def _overlap_count(mask: np.ndarray, x: int, y: int, width: int, height: int) -> int:
        x0 = max(0, x - width // 2)
        x1 = min(mask.shape[1], x0 + width)
        y0 = max(0, y - height)
        y1 = min(mask.shape[0], y + 1)
        if x0 >= x1 or y0 >= y1:
            return 0
        return int(np.count_nonzero(mask[y0:y1, x0:x1]))

    def _obstacle_strength(self) -> float:
        return self.plant_modifier_strength("obstacle")

    def _hazard_strength(self) -> float:
        return self.plant_modifier_strength("hazard")

    def _refresh_plant_geometry(self, force: bool = False) -> None:
        obstacle = self._obstacle_strength()
        hazard = self._hazard_strength()
        illuminate = self.plant_modifier_strength("illuminate")
        key = (
            round(obstacle, 4), round(hazard, 4), round(illuminate, 4),
            self.width, self.height, self.params.get("plant_clearance"),
            self.params.get("plant_mask_path"), self.params.get("plant_globe_mask_path"),
        )
        if not force and key == self._plant_key:
            return
        self._plant_key = key
        self._core_canvas.fill(False)
        self._clearance_canvas.fill(False)
        self._hazard_canvas.fill(False)
        self._foliage_canvas.fill(False)
        self._globe_canvas.fill(False)
        self._globe_edge_canvas.fill(False)
        if max(obstacle, hazard, illuminate) <= 0.0:
            return
        radius = int(round(int(self.params.get("plant_clearance", 1)) * max(obstacle, 0.0)))
        masks = self.get_plant_masks(radius if obstacle > 0.0 else 0)
        if obstacle > 0.0:
            self._core_canvas[:] = masks.obstacle.T[::-1]
            self._clearance_canvas[:] = masks.clearance.T[::-1]
        if hazard > 0.0:
            self._hazard_canvas[:] = masks.obstacle.T[::-1]
        if illuminate > 0.0 or obstacle > 0.0:
            self._foliage_canvas[:] = masks.foliage.T[::-1]
            self._globe_canvas[:] = masks.globes.T[::-1]
            self._globe_edge_canvas[:] = masks.globe_edge.T[::-1]

    def logical_state(self) -> Tuple[Any, ...]:
        def truck_state(truck: Truck) -> Tuple[Any, ...]:
            return (
                truck.index, round(truck.progress, 4), round(truck.lane, 4),
                round(truck.speed, 4), round(truck.damage, 4), round(truck.nitro, 4),
                round(truck.boosting, 4), round(truck.airborne, 4), truck.finished,
                None if truck.finish_time is None else round(truck.finish_time, 4),
                truck.dnf, truck.cash, truck.engine, truck.tires, truck.shocks,
                truck.nitro_tank, truck.hazard_hits, truck.money_bags, truck.nitro_uses,
                truck.landings, truck.last_place, truck.last_upgrade,
                round(truck.career_distance, 3),
            )

        return (
            self.phase, self._cup_name(), self.leg_index, self.loop_index, self.cup_index,
            round(self.hold, 4), round(self.leg_time, 4), round(self.phase_time, 4),
            round(self.sim_time, 4), tuple(self.points), tuple(self.cup_wins),
            self.course.biome, round(self.course.length, 3), self.course.seed,
            self.pit_applied, self.podium_winner, self.overtakes, self.bumps,
            tuple(sorted(self.consumed)), tuple(truck_state(truck) for truck in self.trucks),
        )

    def get_runtime_stats(self) -> Dict[str, Any]:
        ranking = self._display_rank()
        hero_place = ranking.index(0) + 1
        return {
            "phase": self.phase,
            "cup": self._cup_name(),
            "leg": self.leg_index + 1,
            "legs": self._leg_count(),
            "biome": self.course.biome,
            "place": hero_place,
            "speed_mps": round(self.hero.speed, 2),
            "damage": round(self.hero.damage, 3),
            "nitro": round(self.hero.nitro, 3),
            "cash": self.hero.cash,
            "points": list(self.points),
            "cup_wins": list(self.cup_wins),
            "overtakes": self.overtakes,
            "bumps": self.bumps,
            "upgrades": {
                "engine": self.hero.engine,
                "tires": self.hero.tires,
                "shocks": self.hero.shocks,
                "nitro_tank": self.hero.nitro_tank,
            },
            "plant_obstacle": round(self._obstacle_strength(), 3),
            "plant_hazard": round(self._hazard_strength(), 3),
        }

    def _display_rank(self) -> List[int]:
        if self.phase == "race":
            return sorted(range(len(self.trucks)), key=lambda index: (-self.trucks[index].progress, index))
        return sorted(range(len(self.trucks)), key=lambda index: (-self.points[index], index))

    def _render(self) -> None:
        self._refresh_plant_geometry()
        road = self._road_slice()
        colors = self._toned_palette()
        self._paint_sky(colors)
        self._canvas[self.horizon:] = colors["shoulder"]
        self._paint_road(road, colors)
        self._paint_checker(road)
        self._paint_hazards(road)
        self._paint_trucks(road)
        self._paint_plants()
        if bool(self.params.get("show_hud", True)):
            self._paint_hud()
        if self.phase == "podium":
            self._paint_podium()

    def _toned_palette(self) -> Dict[str, Color]:
        mode = str(self.params.get("time_of_day", "day"))
        toned = {}
        for name, color in self.PALETTES[self.course.biome].items():
            if mode == "night":
                if name.startswith("sky") or name == "shoulder":
                    scale = 0.36
                elif name in ("edge", "dash", "crest"):
                    scale = 0.92
                else:
                    scale = 0.72
                toned[name] = tuple(max(0, int(channel * scale)) for channel in color)
            elif mode == "dusk":
                toned[name] = (
                    min(255, int(color[0] * 0.92 + 16)),
                    int(color[1] * 0.70),
                    int(color[2] * 0.76),
                )
            else:
                toned[name] = color
        return toned

    def _paint_sky(self, colors: Dict[str, Color]) -> None:
        if self.horizon <= 0:
            return
        top = np.asarray(colors["sky_top"], dtype=np.float32)
        horizon = np.asarray(colors["sky_horizon"], dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, self.horizon, dtype=np.float32)[:, None, None]
        self._canvas[:self.horizon] = np.clip(top * (1.0 - ramp) + horizon * ramp, 0, 255).astype(np.uint8)
        mode = str(self.params.get("time_of_day", "day"))
        mark_x = max(0, self.width - 2)
        mark_y = max(0, self.horizon - 1)
        if mode == "night":
            self._pixel(mark_x, mark_y, (210, 216, 230))
            self._pixel(mark_x + 1, mark_y, (150, 160, 186))
        elif mode == "dusk":
            self._pixel(mark_x, mark_y, (255, 140, 64))
        else:
            self._pixel(mark_x, mark_y, (255, 214, 96))

    def _paint_road(self, road: RoadSlice, colors: Dict[str, Color]) -> None:
        mode = str(self.params.get("time_of_day", "day"))
        horizon = np.asarray(colors["sky_horizon"], dtype=np.float32)
        dirt = np.asarray(colors["dirt"], dtype=np.float32)
        dark = np.asarray(colors["dirt_dark"], dtype=np.float32)
        for index, y in enumerate(road.rows):
            half = float(road.half[index])
            mid = float(road.center[index])
            left = max(0, int(math.floor(mid - half)))
            right = min(self.width - 1, int(math.ceil(mid + half)))
            if right < left:
                continue
            nearness = float(road.nearness[index])
            fog = (1.0 - nearness) ** 1.35
            base = dark * (1.0 - nearness) + dirt * nearness
            base = base * (1.0 - fog * 0.82) + horizon * (fog * 0.82)
            slope = 0.0
            if 0 < index < len(road.elevation) - 1:
                slope = float(road.elevation[index + 1] - road.elevation[index - 1])
            shade = 1.0 + 0.16 * float(road.elevation[index]) + 0.55 * max(-0.45, min(0.45, slope))
            if mode != "day" and nearness > 0.48:
                shade += (nearness - 0.48) * (0.85 if mode == "night" else 0.4)
            color = tuple(int(max(0, min(255, channel * shade))) for channel in base)
            self._canvas[int(y), left:right + 1] = color
            edge = colors["edge"] if int(road.distance[index]) % 4 < 2 else colors["crest"]
            self._pixel(left, int(y), edge)
            self._pixel(right, int(y), edge)
            if int(road.distance[index] * 0.5) % 5 < 2:
                self._pixel(int(round(mid)), int(y), colors["dash"])
            if slope > 0.08:
                self._pixel(int(round(mid)), int(y), colors["crest"])
            if int(road.distance[index]) % 12 == 0 and left > 0:
                self._pixel(left - 1, int(y), (24, 110, 48))
                if int(y) > self.horizon:
                    self._pixel(left - 1, int(y) - 1, (36, 140, 58))

    def _paint_checker(self, road: RoadSlice) -> None:
        rel = self.course.length - self._camera()
        if not 0.4 < rel < self.LOOKAHEAD:
            return
        y = self._y_from_t(self._screen_t(rel))
        index = int(road.y_index[y])
        if index < 0:
            return
        left = max(0, int(math.floor(road.center[index] - road.half[index])))
        right = min(self.width, int(math.ceil(road.center[index] + road.half[index])) + 1)
        for x in range(left, right):
            light = (x // 1 + int(self.course.length)) % 2 == 0
            self._pixel(x, y, (236, 236, 230) if light else (18, 18, 22))

    def _paint_hazards(self, road: RoadSlice) -> None:
        for hazard in self.course.hazards:
            if hazard.id in self.consumed:
                continue
            box = self._truck_box(hazard.distance, hazard.lane, road)
            if box is None:
                continue
            x, y, _width, _height = box
            index = int(road.y_index[y])
            if index < 0:
                continue
            color = self.HAZARD_COLORS[hazard.kind]
            nearness = float(road.nearness[index])
            self._pixel(x, y, color)
            if hazard.kind == "money":
                self._pixel(x, y - 1, (255, 244, 180))
            elif hazard.kind == "animal" and nearness > 0.35:
                self._pixel(x + 1, y, (120, 72, 48))
                self._pixel(x, y - 1, color)
            elif hazard.kind == "rock" and nearness > 0.4:
                self._pixel(x - 1, y, (48, 44, 46))
            elif hazard.kind == "atv" and nearness > 0.3:
                self._pixel(x, y - 1, (30, 30, 34))
                self._pixel(x + 1, y, color)

    def _paint_trucks(self, road: RoadSlice) -> None:
        order = sorted(self.trucks, key=lambda truck: truck.progress, reverse=True)
        for truck in order:
            box = self._truck_box(truck.progress, truck.lane, road)
            if box is None:
                continue
            x, y, width, height = box
            index = int(road.y_index[y])
            nearness = 0.3 if index < 0 else float(road.nearness[index])
            lift = 0
            if truck.airborne > 0.0 and truck.air_total > 0.0:
                lift = int(round((truck.airborne / truck.air_total) * (2.0 + 7.0 * nearness)))
                self._pixel(x - 1, y, (24, 16, 12))
                self._pixel(x + 1, y, (24, 16, 12))
            foot = y - lift
            if nearness < 0.24:
                self._pixel(x, foot, truck.body)
                self._pixel(x, foot - 1, truck.cab)
                continue
            left = x - width // 2
            self._rect(left, foot - height + 1, width, max(1, height - 1), truck.body)
            cab_w = max(1, width - 2)
            cab_h = max(1, height // 3)
            self._rect(x - cab_w // 2, foot - height - cab_h + 1, cab_w, cab_h, truck.cab)
            self._pixel(left, foot, (24, 18, 16))
            self._pixel(left + width - 1, foot, (24, 18, 16))
            if width >= 5:
                lamp = (255, 246, 220) if truck.index == 0 else (255, 42, 36)
                self._pixel(left + 1, foot - 1, lamp)
                self._pixel(left + width - 2, foot - 1, lamp)
            if truck.boosting > 0.0 and nearness > 0.3:
                flame = (255, 228, 120) if int(self.sim_time * 18) % 2 == 0 else (255, 132, 28)
                self._pixel(x, foot + 1, flame)
            if truck.index == 0 and truck.speed > 12.0 and truck.airborne <= 0.0:
                dust = (176, 122, 64)
                phase = int(self.sim_time * 16)
                self._pixel(x - 1 + phase % 3 - 1, foot + 1, dust)

    def _paint_plants(self) -> None:
        illuminate = self.plant_modifier_strength("illuminate")
        obstacle = self._obstacle_strength()
        hazard = self._hazard_strength()
        if illuminate <= 0.0 and obstacle <= 0.0 and hazard <= 0.0:
            return
        if (illuminate > 0.0 or obstacle > 0.0) and np.any(self._foliage_canvas):
            amount = 0.28 + 0.42 * max(illuminate, 0.35 * obstacle)
            current = self._canvas[self._foliage_canvas].astype(np.float32)
            leaf = np.asarray((12, 96, 40), dtype=np.float32)
            self._canvas[self._foliage_canvas] = np.clip(
                current * (1.0 - amount) + leaf * amount, 0, 255
            ).astype(np.uint8)
        if (illuminate > 0.0 or obstacle > 0.0) and np.any(self._globe_canvas):
            self._canvas[self._globe_canvas] = (108, 84, 90)
            if np.any(self._globe_edge_canvas):
                self._canvas[self._globe_edge_canvas] = (168, 142, 148)
        if hazard > 0.0 and np.any(self._hazard_canvas):
            amount = 0.22 * hazard
            current = self._canvas[self._hazard_canvas].astype(np.float32)
            scrim = np.asarray((120, 28, 22), dtype=np.float32)
            self._canvas[self._hazard_canvas] = np.clip(
                current * (1.0 - amount) + scrim * amount, 0, 255
            ).astype(np.uint8)

    def _paint_hud(self) -> None:
        hero = self.hero
        track = (8, 8, 14)
        bar_width = max(4, self.width - 3)
        self._rect(1, 2, bar_width, 1, track)
        damage_width = max(1, int(round(bar_width * (1.0 - hero.damage))))
        damage_color = (
            int(255 * hero.damage + 40 * (1.0 - hero.damage)),
            int(210 * (1.0 - hero.damage) + 24),
            42,
        )
        self._rect(1, 2, damage_width, 1, damage_color)
        self._rect(1, 4, bar_width, 1, track)
        nitro_width = max(1, int(round(bar_width * hero.nitro))) if hero.nitro > 0.04 else 0
        nitro_color = (255, 196, 48) if hero.boosting > 0.0 else (48, 214, 255)
        if nitro_width:
            self._rect(1, 4, nitro_width, 1, nitro_color)
        for place, index in enumerate(self._display_rank()):
            color = self.trucks[index].body
            x = 1 + place * 2
            self._pixel(x, 0, color)
            if index == 0:
                self._pixel(x, 1, (255, 255, 255))
        if self.width >= 16:
            place = self._display_rank().index(0) + 1
            self._digit(place, self.width - 4, 0, (255, 236, 210))
        legs = self._leg_count()
        for index in range(legs):
            color = (255, 210, 80) if index <= self.leg_index else (36, 36, 48)
            self._pixel(self.width - 1, min(self.height - 1, self.horizon + 2 + index * 2), color)
        if self.phase == "race" and self.hold > 0.0:
            light = (255, 64, 48) if self.hold > 0.6 else (255, 196, 48) if self.hold > 0.3 else (64, 220, 80)
            self._pixel(self.width // 2, 0, light)

    def _paint_podium(self) -> None:
        winner = self.podium_winner
        if not 0 <= winner < len(self.trucks):
            return
        color = self.trucks[winner].body
        x = max(1, self.width // 2 - 1)
        y = min(self.height - 12, self.horizon + 4)
        self._rect(x, y, 3, 8, color)
        self._pixel(x + 1, y - 1, (255, 220, 90))

    def _digit(self, digit: int, x: int, y: int, color: Color) -> None:
        glyph = self.FONT.get(str(digit))
        if glyph is None:
            return
        for dy, row in enumerate(glyph):
            for dx, pixel in enumerate(row):
                if pixel == "1":
                    self._pixel(x + dx, y + dy, color)

    def _pixel(self, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self._canvas[y, x] = color

    def _rect(self, x: int, y: int, width: int, height: int, color: Color) -> None:
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.width, int(x) + max(0, int(width)))
        y1 = min(self.height, int(y) + max(0, int(height)))
        if x0 < x1 and y0 < y1:
            self._canvas[y0:y1, x0:x1] = color

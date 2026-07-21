#!/usr/bin/env python3
"""A persistent, genetically evolving top-down pixel ecosystem.

The expensive ecology runs at a fixed low frequency while drawing is capped
separately.  Populations are deliberately small: this keeps the O(n^2) flock
and hunt decisions cheap enough for the Raspberry Pi that drives the wall.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from animation import AnimationBase


@dataclass
class Creature:
    species: int                 # 0 grazer, 1 hunter
    pack: int
    x: float
    y: float
    vx: float
    vy: float
    energy: float
    age: float
    genes: np.ndarray            # speed, body size, vision, efficiency, hue
    generation: int = 0
    cooldown: float = 0.0


@dataclass
class Tree:
    x: float
    y: float
    age: float
    lifespan: float
    size_gene: float
    crown_gene: float
    generation: int = 0


class LivingEcosystemAnimation(AnimationBase):
    """Long-form food web with flocking, predation, inheritance, and mutation."""

    ANIMATION_NAME = "Living Ecosystem"
    ANIMATION_DESCRIPTION = (
        "A 15-minute top-down habitat where grass and trees grow, herds form, "
        "hunters cooperate, and inherited traits visibly evolve"
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    LOOP_SECONDS = 15.0 * 60.0
    SIM_HZ = 8.0
    MAX_GRAZERS = 30
    MAX_HUNTERS = 10

    PALETTES = {
        "natural": {
            "grass_low": (10, 43, 22), "grass_high": (28, 139, 43),
            "water": (18, 78, 155), "shore": (55, 79, 41),
            "tree": (31, 137, 46), "grazer": (222, 166, 60),
            "hunter": (151, 83, 130), "accent": (170, 188, 58),
        },
        "golden_hour": {
            "grass_low": (45, 20, 8), "grass_high": (194, 92, 18),
            "water": (36, 75, 139), "shore": (134, 65, 18),
            "tree": (178, 104, 22), "grazer": (255, 205, 74),
            "hunter": (222, 64, 44), "accent": (255, 167, 38),
        },
        "autumn": {
            "grass_low": (31, 23, 10), "grass_high": (134, 91, 24),
            "water": (25, 67, 91), "shore": (101, 67, 24),
            "tree": (207, 68, 18), "grazer": (247, 180, 63),
            "hunter": (126, 40, 55), "accent": (255, 116, 18),
        },
        "moonlit": {
            "grass_low": (4, 12, 26), "grass_high": (17, 57, 75),
            "water": (22, 72, 175), "shore": (22, 54, 74),
            "tree": (18, 84, 82), "grazer": (104, 199, 214),
            "hunter": (154, 102, 227), "accent": (166, 223, 255),
        },
        "boreal": {
            "grass_low": (7, 27, 25), "grass_high": (29, 91, 65),
            "water": (31, 92, 128), "shore": (47, 76, 65),
            "tree": (21, 101, 67), "grazer": (190, 182, 139),
            "hunter": (104, 117, 128), "accent": (194, 224, 211),
        },
        "bioluminescent": {
            "grass_low": (2, 5, 28), "grass_high": (8, 73, 104),
            "water": (19, 17, 116), "shore": (4, 74, 91),
            "tree": (0, 220, 146), "grazer": (40, 247, 213),
            "hunter": (255, 45, 178), "accent": (113, 255, 86),
        },
        "ultraviolet": {
            "grass_low": (12, 0, 31), "grass_high": (79, 13, 125),
            "water": (18, 13, 107), "shore": (98, 20, 126),
            "tree": (176, 22, 213), "grazer": (27, 241, 255),
            "hunter": (255, 30, 105), "accent": (196, 255, 24),
        },
        "ember": {
            "grass_low": (17, 2, 0), "grass_high": (112, 19, 2),
            "water": (28, 8, 18), "shore": (92, 25, 5),
            "tree": (212, 47, 4), "grazer": (255, 189, 35),
            "hunter": (255, 43, 8), "accent": (255, 229, 102),
        },
    }

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "speed": 1.0,
            "brightness": 1.0,
            "render_fps": 40.0,
            "simulation_hz": 8.0,
            "seed": 7319,
            "lifecycle_minutes": 15.0,
            "day_length_seconds": 90.0,
            "mutation_rate": 0.07,
            "creature_density": 1.0,
            "predator_ratio": 0.25,
            "tree_density": 1.0,
            "creature_size": 1.0,
            "grass_regrowth": 1.0,
            "predation_pressure": 1.0,
            "pack_cohesion": 1.0,
            "palette": "natural",
            "night_brightness": 0.22,
            "glow_strength": 0.08,
            "firefly_density": 1.0,
            "water_shimmer": 1.0,
            "output_gamma": 1.0,
            "show_water": True,
        })
        self.params = {**self.default_params, **self.config}
        self._canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._last_frame: Optional[np.ndarray] = None
        self._last_render_elapsed: Optional[float] = None
        self._sim_time = 0.0
        self._cycle = -1
        self._kills = 0
        self._births = 0
        self._reset_world(0)

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.25, "max": 8.0, "default": 1.0,
                      "description": "Playback multiplier applied after lifecycle duration"},
            "lifecycle_minutes": {"type": "float", "min": 2.0, "max": 60.0, "default": 15.0,
                                  "description": "Wall-clock duration of one complete evolutionary lifecycle"},
            "day_length_seconds": {"type": "float", "min": 15.0, "max": 300.0, "default": 90.0,
                                   "description": "Wall-clock duration of one day/night cycle"},
            "render_fps": {"type": "float", "min": 12.0, "max": 60.0, "default": 40.0,
                           "description": "Maximum visual redraw rate"},
            "simulation_hz": {"type": "float", "min": 2.0, "max": 12.0, "default": 8.0,
                              "description": "Ecology updates per canonical second; lower values save CPU"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 7319,
                     "description": "Deterministic habitat and ancestry seed"},
            "mutation_rate": {"type": "float", "min": 0.0, "max": 0.25, "default": 0.07,
                              "description": "Inherited trait mutation strength"},
            "creature_density": {"type": "float", "min": 0.25, "max": 2.0, "default": 1.0,
                                 "description": "Population capacity; 2× supports roughly twice as many creatures"},
            "predator_ratio": {"type": "float", "min": 0.08, "max": 0.45, "default": 0.25,
                               "description": "Hunter capacity relative to grazers"},
            "tree_density": {"type": "float", "min": 0.2, "max": 2.0, "default": 1.0,
                             "description": "Woodland coverage"},
            "creature_size": {"type": "float", "min": 0.0, "max": 2.0, "default": 1.0,
                              "description": "Visual sprite scale; 0 draws every creature as one pixel"},
            "grass_regrowth": {"type": "float", "min": 0.2, "max": 3.0, "default": 1.0,
                               "description": "Plant recovery rate"},
            "predation_pressure": {"type": "float", "min": 0.25, "max": 2.0, "default": 1.0,
                                   "description": "Hunter pursuit and capture effectiveness"},
            "pack_cohesion": {"type": "float", "min": 0.0, "max": 2.0, "default": 1.0,
                              "description": "Strength of herd and pack grouping"},
            "palette": {"type": "str", "default": "natural",
                        "description": "natural, golden_hour, autumn, moonlit, boreal, bioluminescent, ultraviolet, or ember"},
            "night_brightness": {"type": "float", "min": 0.03, "max": 0.7, "default": 0.22,
                                 "description": "Minimum habitat brightness at night"},
            "glow_strength": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.08,
                              "description": "Colored halo around creatures for installation viewing"},
            "firefly_density": {"type": "float", "min": 0.0, "max": 4.0, "default": 1.0,
                                "description": "Nighttime accent-light density"},
            "water_shimmer": {"type": "float", "min": 0.0, "max": 2.0, "default": 1.0,
                              "description": "Moving highlight intensity on water"},
            "output_gamma": {"type": "float", "min": 0.6, "max": 2.4, "default": 1.0,
                             "description": "NeoPixel output curve; above 1 lifts dim installation detail"},
            "show_water": {"type": "bool", "default": True,
                           "description": "Render the river and use it as a movement barrier"},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        structural = {"seed", "creature_density", "predator_ratio", "tree_density", "show_water"}
        should_reset = bool(structural.intersection(new_params))
        super().update_parameters(new_params)
        if should_reset:
            self._reset_world(self._cycle)
        self._last_render_elapsed = None

    def _population_limits(self) -> Tuple[int, int]:
        density = float(np.clip(self.params.get("creature_density", 1.0), .25, 2.0))
        grazers = max(8, min(60, round(min(30, self.width * .95) * density)))
        ratio = float(np.clip(self.params.get("predator_ratio", .25), .08, .45))
        hunters = max(3, min(20, round(grazers * ratio * 1.35)))
        return grazers, hunters

    def _reset_world(self, cycle: int):
        seed = int(self.params.get("seed", 7319))
        self.rng = np.random.default_rng(seed)
        self._cycle = cycle
        self._sim_time = 0.0
        self._kills = 0
        self._births = 0

        # A low-resolution resource field is enough for feeding decisions and
        # makes plant growth effectively free compared with per-pixel agents.
        self.grass_w = max(4, self.width // 2)
        self.grass_h = max(8, self.height // 4)
        self.grass = self.rng.uniform(.5, 1.0, (self.grass_h, self.grass_w)).astype(np.float32)
        self._grass_view = np.zeros((self.height, self.width), dtype=np.float32)

        yy = np.arange(self.height, dtype=np.float32)[:, None]
        xx = np.arange(self.width, dtype=np.float32)[None, :]
        river_center = self.width * .52 + np.sin(yy * .075) * max(1.5, self.width * .13)
        river_width = max(1.0, self.width * .055)
        self.water = np.abs(xx - river_center) <= river_width
        self.shore = (np.abs(xx - river_center) > river_width) & (np.abs(xx - river_center) <= river_width + 1.2)

        tree_density = float(np.clip(self.params.get("tree_density", 1.0), .2, 2.0))
        tree_count = max(4, min(80, round(self.width * self.height / 105 * tree_density)))
        self.trees: List[Tree] = []
        for _ in range(tree_count):
            x, y = self._land_position(edge_bias=True)
            self.trees.append(Tree(x, y, self.rng.uniform(8, 150), self.rng.uniform(150, 260),
                                   self.rng.uniform(.7, 1.35), self.rng.uniform(0, 1)))

        grazer_limit, hunter_limit = self._population_limits()
        grazers = max(6, round(grazer_limit * .72))
        hunters = max(2, round(hunter_limit * .70))
        self.creatures: List[Creature] = []
        herd_centers = [self._land_position() for _ in range(3)]
        for index in range(grazers):
            cx, cy = herd_centers[index % len(herd_centers)]
            self.creatures.append(self._founder(0, index % 3, cx, cy))
        pack_centers = [self._land_position() for _ in range(2)]
        for index in range(hunters):
            cx, cy = pack_centers[index % len(pack_centers)]
            self.creatures.append(self._founder(1, index % 2, cx, cy))

    def _land_position(self, edge_bias: bool = False) -> Tuple[float, float]:
        for _ in range(30):
            x = self.rng.uniform(1, max(1.1, self.width - 2))
            y = self.rng.uniform(1, max(1.1, self.height - 2))
            if (not bool(self.params.get("show_water", True))
                    or not self.water[min(self.height - 1, int(y)), min(self.width - 1, int(x))]):
                if not edge_bias or x < self.width * .35 or x > self.width * .68:
                    return x, y
        return 1.0, 1.0

    def _founder(self, species: int, pack: int, cx: float, cy: float) -> Creature:
        base = np.array([1.0, 1.0, 1.0, 1.0, .0 if species == 0 else .55], dtype=np.float32)
        base[:4] += self.rng.normal(0, .10, 4)
        base[4] += self.rng.normal(0, .04)
        x = float(np.clip(cx + self.rng.normal(0, 3), 1, self.width - 2))
        y = float(np.clip(cy + self.rng.normal(0, 7), 1, self.height - 2))
        angle = self.rng.uniform(0, math.tau)
        return Creature(species, pack, x, y, math.cos(angle), math.sin(angle),
                        65.0 if species == 0 else 90.0, self.rng.uniform(0, 20), base)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        fps = float(np.clip(self.params.get("render_fps", 40.0), 12.0, 60.0))
        if (self._last_frame is not None and self._last_render_elapsed is not None
                and 0 <= time_elapsed - self._last_render_elapsed < 1.0 / fps):
            return self.rendered_frame(self._last_frame, changed=False)

        speed = max(.05, float(self.params.get("speed", 1.0)))
        lifecycle = float(np.clip(self.params.get("lifecycle_minutes", 15.0), 2.0, 60.0))
        # Every loop remains 900 canonical seconds internally. Changing the
        # lifecycle accelerates all growth, aging, and evolution together.
        absolute_sim_time = max(0.0, time_elapsed) * speed * (15.0 / lifecycle)
        cycle = int(absolute_sim_time // self.LOOP_SECONDS)
        target = absolute_sim_time % self.LOOP_SECONDS
        if cycle != self._cycle or target < self._sim_time:
            self._reset_world(cycle)

        # Bound catch-up after a suspended process.  The ecosystem remains
        # stable instead of monopolizing a core to replay minutes of history.
        simulation_hz = float(np.clip(self.params.get("simulation_hz", self.SIM_HZ), 2.0, 12.0))
        step = 1.0 / simulation_hz
        due = int((target - self._sim_time) / step)
        for _ in range(min(due, 32)):
            self._simulate(step)
        if due > 32:
            self._sim_time = target

        self._last_render_elapsed = time_elapsed
        self._render(target)
        frame = self.next_frame_buffer(clear=False)
        frame.reshape(self.width, self.height, 3)[:] = self._canvas[::-1].transpose(1, 0, 2)
        self.apply_brightness_array(frame, out=frame)
        self._last_frame = frame
        return self.rendered_frame(frame)

    def _simulate(self, dt: float):
        self._sim_time += dt
        daylight = self._daylight(self._sim_time)
        regrowth = float(np.clip(self.params.get("grass_regrowth", 1.0), .2, 3.0))
        self.grass += dt * regrowth * (.0025 + .006 * daylight) * (1.0 - self.grass)
        np.clip(self.grass, 0.0, 1.0, out=self.grass)

        grazers = [c for c in self.creatures if c.species == 0]
        hunters = [c for c in self.creatures if c.species == 1]
        for creature in list(self.creatures):
            creature.age += dt
            creature.cooldown = max(0.0, creature.cooldown - dt)
            if creature.species == 0:
                self._move_grazer(creature, grazers, hunters, dt)
            else:
                self._move_hunter(creature, hunters, grazers, dt)
            efficiency = float(np.clip(creature.genes[3], .65, 1.35))
            size = float(np.clip(creature.genes[1], .65, 1.5))
            creature.energy -= dt * (.16 if creature.species == 0 else .22) * size / efficiency

        self._reproduce()
        survivors: List[Creature] = []
        for creature in self.creatures:
            lifespan = (125 if creature.species == 0 else 205) * float(np.clip(creature.genes[3], .7, 1.3))
            if creature.energy > 0 and creature.age < lifespan:
                survivors.append(creature)
            else:
                gy, gx = self._grass_cell(creature.x, creature.y)
                self.grass[gy, gx] = min(1.0, self.grass[gy, gx] + .25)
        self.creatures = survivors
        self._tree_step(dt)
        self._immigration()

    def _move_grazer(self, c: Creature, herd: List[Creature], hunters: List[Creature], dt: float):
        ax = self.rng.normal(0, .16)
        ay = self.rng.normal(0, .16)
        mates = [o for o in herd if o is not c and o.pack == c.pack]
        if mates:
            cx = sum(o.x for o in mates) / len(mates)
            cy = sum(o.y for o in mates) / len(mates)
            cohesion = float(np.clip(self.params.get("pack_cohesion", 1.0), 0, 2))
            ax += (cx - c.x) * .004 * cohesion
            ay += (cy - c.y) * .004 * cohesion
        vision = 9.0 * float(np.clip(c.genes[2], .65, 1.4))
        threats = [(c.x - p.x, c.y - p.y) for p in hunters
                   if (c.x - p.x) ** 2 + (c.y - p.y) ** 2 < vision ** 2]
        if threats:
            ax += sum(dx for dx, _ in threats) * .05
            ay += sum(dy for _, dy in threats) * .05
        gy, gx = self._grass_cell(c.x, c.y)
        food = float(self.grass[gy, gx])
        if food > .12:
            bite = min(food, dt * .055)
            self.grass[gy, gx] -= bite
            c.energy = min(120.0, c.energy + bite * 62)
        self._integrate(c, ax, ay, dt, base_speed=2.7)

    def _move_hunter(self, c: Creature, pack: List[Creature], prey: List[Creature], dt: float):
        ax = self.rng.normal(0, .11)
        ay = self.rng.normal(0, .11)
        target = None
        if prey:
            pressure = float(np.clip(self.params.get("predation_pressure", 1.0), .25, 2.0))
            vision = 17.0 * float(np.clip(c.genes[2], .65, 1.4)) * math.sqrt(pressure)
            target = min(prey, key=lambda o: (o.x - c.x) ** 2 + (o.y - c.y) ** 2)
            dist2 = (target.x - c.x) ** 2 + (target.y - c.y) ** 2
            if dist2 < vision * vision:
                ax += (target.x - c.x) * .016 * pressure
                ay += (target.y - c.y) * .016 * pressure
                if dist2 < ((1.0 + c.genes[1] * .5) * math.sqrt(pressure)) ** 2 and target in self.creatures:
                    self.creatures.remove(target)
                    if target in prey:
                        prey.remove(target)
                    c.energy = min(150.0, c.energy + 43.0)
                    self._kills += 1
        allies = [o for o in pack if o is not c and o.pack == c.pack]
        if allies:
            cohesion = float(np.clip(self.params.get("pack_cohesion", 1.0), 0, 2))
            ax += (sum(o.x for o in allies) / len(allies) - c.x) * .003 * cohesion
            ay += (sum(o.y for o in allies) / len(allies) - c.y) * .003 * cohesion
        self._integrate(c, ax, ay, dt, base_speed=3.05)

    def _integrate(self, c: Creature, ax: float, ay: float, dt: float, base_speed: float):
        c.vx = c.vx * .91 + ax
        c.vy = c.vy * .91 + ay
        length = max(.001, math.hypot(c.vx, c.vy))
        speed = base_speed * float(np.clip(c.genes[0], .65, 1.4))
        c.vx, c.vy = c.vx / length * speed, c.vy / length * speed
        nx = float(np.clip(c.x + c.vx * dt, .5, self.width - 1.5))
        ny = float(np.clip(c.y + c.vy * dt, .5, self.height - 1.5))
        if bool(self.params.get("show_water", True)) and self.water[int(ny), int(nx)]:
            c.vx *= -.65
            c.vy += self.rng.choice((-1, 1)) * speed * .25
        else:
            c.x, c.y = nx, ny

    def _reproduce(self):
        mutation = float(np.clip(self.params.get("mutation_rate", .07), 0, .25))
        newborns = []
        grazer_limit, hunter_limit = self._population_limits()
        for species, limit in ((0, grazer_limit), (1, hunter_limit)):
            adults = [c for c in self.creatures if c.species == species and c.age > (24 if species == 0 else 32)
                      and c.energy > (84 if species == 0 else 102) and c.cooldown <= 0]
            room = limit - sum(c.species == species for c in self.creatures)
            self.rng.shuffle(adults)
            for first in adults:
                candidates = [c for c in adults if c is not first and c.pack == first.pack
                              and c.cooldown <= 0 and (c.x-first.x)**2 + (c.y-first.y)**2 < 45]
                if not candidates or room <= 0:
                    continue
                second = min(candidates, key=lambda c: (c.x-first.x)**2 + (c.y-first.y)**2)
                mask = self.rng.random(5) < .5
                genes = np.where(mask, first.genes, second.genes).astype(np.float32)
                genes += self.rng.normal(0, mutation, 5).astype(np.float32)
                genes[:4] = np.clip(genes[:4], .58, 1.5)
                genes[4] %= 1.0
                child = Creature(species, first.pack, (first.x+second.x)/2, (first.y+second.y)/2,
                                 first.vx*.3, first.vy*.3, 48.0, 0.0, genes,
                                 max(first.generation, second.generation)+1, 18.0)
                cost = 21 if species == 0 else 25
                first.energy -= cost
                second.energy -= cost
                first.cooldown = second.cooldown = 22 if species == 0 else 35
                newborns.append(child)
                room -= 1
                self._births += 1
        self.creatures.extend(newborns)

    def _immigration(self):
        # Rare migrants prevent a single unlucky hunt from ending the story.
        grazer_limit, hunter_limit = self._population_limits()
        for species, floor in ((0, max(3, round(grazer_limit * .23))),
                               (1, max(2, round(hunter_limit * .30)))):
            count = sum(c.species == species for c in self.creatures)
            if count < floor:
                x, y = self._land_position()
                if species == 1 and count:
                    packs = [c.pack for c in self.creatures if c.species == 1]
                    pack = max(set(packs), key=packs.count)
                else:
                    pack = int(self.rng.integers(0, 3 if species == 0 else 2))
                self.creatures.append(self._founder(species, pack, x, y))

    def _tree_step(self, dt: float):
        for index, tree in enumerate(self.trees):
            tree.age += dt
            if tree.age >= tree.lifespan:
                parent = self.trees[int(self.rng.integers(0, len(self.trees)))]
                x = float(np.clip(parent.x + self.rng.normal(0, 4), 1, self.width - 2))
                y = float(np.clip(parent.y + self.rng.normal(0, 9), 1, self.height - 2))
                if bool(self.params.get("show_water", True)) and self.water[int(y), int(x)]:
                    x, y = self._land_position(edge_bias=True)
                self.trees[index] = Tree(x, y, 0.0, self.rng.uniform(165, 280),
                                         float(np.clip(parent.size_gene+self.rng.normal(0,.08),.55,1.5)),
                                         float((parent.crown_gene+self.rng.normal(0,.07))%1),
                                         parent.generation+1)

    def _grass_cell(self, x: float, y: float) -> Tuple[int, int]:
        return (min(self.grass_h-1, int(y/self.height*self.grass_h)),
                min(self.grass_w-1, int(x/self.width*self.grass_w)))

    def _daylight(self, t: float) -> float:
        lifecycle = float(np.clip(self.params.get("lifecycle_minutes", 15.0), 2.0, 60.0))
        wall_day = float(np.clip(self.params.get("day_length_seconds", 90.0), 15.0, 300.0))
        canonical_day = max(5.0, wall_day * 15.0 / lifecycle)
        return float(np.clip(.5 + .5 * math.sin(t * math.tau / canonical_day - math.pi/2), 0, 1))

    def _palette(self) -> Dict[str, Tuple[int, int, int]]:
        return self.PALETTES.get(str(self.params.get("palette", "natural")).lower(),
                                 self.PALETTES["natural"])

    def _render(self, t: float):
        daylight = self._daylight(t)
        palette = self._palette()
        # Upsample the resource field without interpolation; individual cells
        # look like natural pixel-map patches and this allocates very little.
        y_idx = np.minimum((np.arange(self.height) * self.grass_h // self.height), self.grass_h-1)
        x_idx = np.minimum((np.arange(self.width) * self.grass_w // self.width), self.grass_w-1)
        self._grass_view[:] = self.grass[y_idx[:, None], x_idx[None, :]]
        night = float(np.clip(self.params.get("night_brightness", .22), .03, .7))
        light = night + (1.0 - night) * daylight
        low, high = palette["grass_low"], palette["grass_high"]
        for channel in range(3):
            self._canvas[..., channel] = np.clip(
                (low[channel] + self._grass_view * (high[channel] - low[channel])) * light,
                0, 255,
            )
        show_water = bool(self.params.get("show_water", True))
        if show_water:
            self._canvas[self.water] = np.clip(np.asarray(palette["water"]) * light, 0, 255).astype(np.uint8)
            self._canvas[self.shore] = np.clip(np.asarray(palette["shore"]) * light, 0, 255).astype(np.uint8)

        # Moonlight ripples and fireflies retain motion/readability at night.
        if daylight < .28:
            shimmer = float(np.clip(self.params.get("water_shimmer", 1.0), 0, 2))
            ripple_y = int((t * 2.2) % self.height)
            if show_water and shimmer > 0:
                ripple = tuple(min(255, int(value * (.7 + .65 * shimmer))) for value in palette["water"])
                self._canvas[ripple_y, self.water[ripple_y]] = ripple
            fireflies = float(np.clip(self.params.get("firefly_density", 1.0), 0, 4))
            for index in range(round(max(2, self.width // 8) * fireflies)):
                x = int((index * 17 + t * (1 + index % 2)) % self.width)
                y = int((index * 37 + math.sin(t + index) * 5) % self.height)
                pulse = .55 + .45 * math.sin(t * 3.1 + index * 2.4) ** 2
                self._pixel(x, y, tuple(int(value * pulse) for value in palette["accent"]))

        for tree in sorted(self.trees, key=lambda item: item.y):
            self._draw_tree(tree, light, palette)
        for creature in sorted(self.creatures, key=lambda item: item.y):
            self._draw_creature(creature, daylight, palette)
        self._finish_color()

    def _draw_tree(self, tree: Tree, light: float, palette: Dict[str, Tuple[int, int, int]]):
        maturity = min(1.0, tree.age / 34.0)
        decline = min(1.0, max(0.0, (tree.lifespan-tree.age) / 22.0))
        radius = max(0, int(round(2.0 * tree.size_gene * maturity * decline)))
        x, y = int(round(tree.x)), int(round(tree.y))
        self._pixel(x, y+1, tuple(int(v*light) for v in (82, 47, 25)))
        hue_shift = tree.crown_gene
        base = palette["tree"]
        crown = (int(base[0] * (1+.18*hue_shift) * light),
                 int(base[1] * (1-.12*hue_shift) * light),
                 int(base[2] * (1+.22*hue_shift) * light))
        crown = tuple(int(np.clip(value, 0, 255)) for value in crown)
        if radius == 0:
            self._pixel(x, y, (35, int(145*light), 42))
        else:
            for dy in range(-radius, radius+1):
                span = radius - abs(dy)//2
                self._rect(x-span, y+dy-radius, span*2+1, 1, crown)
            self._pixel(x-radius, y-radius, tuple(min(255, c+18) for c in crown))

    def _draw_creature(self, c: Creature, daylight: float,
                       palette: Dict[str, Tuple[int, int, int]]):
        x, y = int(round(c.x)), int(round(c.y))
        size = float(np.clip(c.genes[1], .58, 1.5))
        visual_scale = float(np.clip(self.params.get("creature_size", 1.0), 0, 2))
        body = max(0, int(round((1 if size < .88 or c.age < 12 else 2) * visual_scale)))
        # Genotype hue affects the red/blue balance; generation changes anatomy.
        hue = float(c.genes[4])
        if c.species == 0:
            base = palette["grazer"]
        else:
            base = palette["hunter"]
        variation = (hue - (.0 if c.species == 0 else .55)) * .35
        color = (int(np.clip(base[0] * (1+variation), 0, 255)),
                 int(np.clip(base[1] * (1-abs(variation)*.5), 0, 255)),
                 int(np.clip(base[2] * (1-variation), 0, 255)))
        dark = tuple(int(value * .35) for value in color)
        night_scale = .62 + .38 * daylight
        color = tuple(int(v*night_scale) for v in color)
        dx = 1 if c.vx >= 0 else -1
        if body == 0:
            self._pixel(x, y, color)
            return
        glow = float(np.clip(self.params.get("glow_strength", .08), 0, 1))
        if glow > 0:
            halo = tuple(int(value * glow * (.45 + .55*(1-daylight))) for value in color)
            for ox, oy in ((-1,0),(1,0),(0,-1),(0,1)):
                self._max_pixel(x+ox, y+oy, halo)
        self._rect(x-body, y-body//2, body*2+1, max(1, body), color)
        self._pixel(x+dx*(body+1), y, color)
        self._pixel(x-dx*(body+1), y, dark)  # tail / silhouette direction
        if c.species == 0 and c.generation >= 2:
            self._pixel(x+dx*(body+1), y-1, (205, 185, 105))  # evolved horns
        if c.species == 1:
            self._pixel(x+dx*(body+1), y, (235, 85, 55))
            if c.generation >= 2 and size > 1.05:
                self._pixel(x-dx, y+1, color)  # broader evolved haunches

    def _finish_color(self):
        saturation = float(np.clip(self.params.get("color_saturation", 1.0), 0, 1))
        value = float(np.clip(self.params.get("color_value", 1.0), 0, 1))
        if saturation < .999:
            gray = np.max(self._canvas, axis=2, keepdims=True)
            self._canvas[:] = np.clip(gray + (self._canvas.astype(np.float32)-gray) * saturation,
                                      0, 255).astype(np.uint8)
        if value < .999:
            np.multiply(self._canvas, value, out=self._canvas, casting="unsafe")
        gamma = float(np.clip(self.params.get("output_gamma", 1.0), .6, 2.4))
        if abs(gamma - 1.0) > .01:
            lut = np.clip((np.arange(256) / 255.0) ** (1.0 / gamma) * 255.0, 0, 255).astype(np.uint8)
            self._canvas[:] = lut[self._canvas]

    def _pixel(self, x: int, y: int, color: Tuple[int, int, int]):
        if 0 <= x < self.width and 0 <= y < self.height:
            self._canvas[y, x] = color

    def _max_pixel(self, x: int, y: int, color: Tuple[int, int, int]):
        if 0 <= x < self.width and 0 <= y < self.height:
            self._canvas[y, x] = np.maximum(self._canvas[y, x], color)

    def _rect(self, x: int, y: int, w: int, h: int, color: Tuple[int, int, int]):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x+w), min(self.height, y+h)
        if x0 < x1 and y0 < y1:
            self._canvas[y0:y1, x0:x1] = color

    def get_runtime_stats(self) -> Dict[str, Any]:
        grazers = [c for c in self.creatures if c.species == 0]
        hunters = [c for c in self.creatures if c.species == 1]
        return {
            "ecosystem_seconds": round(self._sim_time, 1),
            "lifecycle_minutes": float(self.params.get("lifecycle_minutes", 15.0)),
            "lifecycle_progress": round(self._sim_time / self.LOOP_SECONDS, 3),
            "day": int(self._sim_time / max(1.0, float(self.params.get("day_length_seconds", 90.0))
                                            * 15.0 / float(self.params.get("lifecycle_minutes", 15.0)))) + 1,
            "daylight": round(self._daylight(self._sim_time), 3),
            "grazers": len(grazers),
            "hunters": len(hunters),
            "births": self._births,
            "kills": self._kills,
            "max_generation": max((c.generation for c in self.creatures), default=0),
            "mean_grass": round(float(self.grass.mean()), 3),
            "tree_generations": max((tree.generation for tree in self.trees), default=0),
            "palette": str(self.params.get("palette", "natural")),
            "creature_size": float(self.params.get("creature_size", 1.0)),
            "simulation_hz": float(self.params.get("simulation_hz", self.SIM_HZ)),
        }

#!/usr/bin/env python3
"""Conway's Game of Life animation plugin for the LED grid."""

import math
import random
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import numpy as np

from animation import AnimationBase
from animation.libraries.mask_effects import dilate_8
from animation.libraries.palette_field import AnimatedPaletteField
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


Color = Tuple[int, int, int]


class ConwayLifeAnimation(AnimationBase):
    """Conway's Game of Life with smooth transitions and color blending."""

    ANIMATION_NAME = "Conway's Game of Life"
    ANIMATION_DESCRIPTION = "Classic B3/S23 Life with evolving color, atmospheric worlds, and pattern seeds"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.3"
    PLANT_MODIFIER_SUPPORT = frozenset(("obstacle", "habitat", "hazard", "emitter"))

    PALETTES = (
        "natural",
        "classic",
        "aurora",
        "bioluminescent",
        "ember",
        "ice",
        "neon",
        "synthwave",
        "monochrome",
    )
    PALETTE_ENDPOINTS = {
        "classic": ((0, 255, 0), (0, 255, 0)),
        "aurora": ((40, 255, 155), (145, 80, 255)),
        "bioluminescent": ((0, 255, 220), (35, 80, 255)),
        "ember": ((255, 215, 70), (205, 20, 8)),
        "ice": ((235, 255, 255), (35, 115, 255)),
        "neon": ((65, 255, 80), (255, 25, 225)),
        "synthwave": ((20, 225, 255), (255, 25, 150)),
        "monochrome": ((225, 240, 255), (75, 105, 140)),
    }
    BACKGROUNDS = (
        "void", "twilight", "deep_ocean", "aurora", "earth", "starfield", "ember", "arcade"
    )
    SEED_PATTERNS = (
        "random",
        "glider_fleet",
        "r_pentomino",
        "acorn",
        "pulsar",
        "gosper_glider_gun",
        "oscillator_garden",
    )

    PATTERNS = {
        "r_pentomino": [(1, 0), (2, 0), (0, 1), (1, 1), (1, 2)],
        "acorn": [(1, 0), (3, 1), (0, 2), (1, 2), (4, 2), (5, 2), (6, 2)],
        "pulsar": [
            (x, y)
            for x, y in (
                [(x, y) for x in (2, 3, 4, 8, 9, 10) for y in (0, 5, 7, 12)]
                + [(x, y) for x in (0, 5, 7, 12) for y in (2, 3, 4, 8, 9, 10)]
            )
        ],
        "gosper_glider_gun": [
            (0, 4), (0, 5), (1, 4), (1, 5), (10, 4), (10, 5), (10, 6), (11, 3),
            (11, 7), (12, 2), (12, 8), (13, 2), (13, 8), (14, 5), (15, 3),
            (15, 7), (16, 4), (16, 5), (16, 6), (17, 5), (20, 2), (20, 3),
            (20, 4), (21, 2), (21, 3), (21, 4), (22, 1), (22, 5), (24, 0),
            (24, 1), (24, 5), (24, 6), (34, 2), (34, 3), (35, 2), (35, 3),
        ],
    }

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.num_strips = getattr(controller, "strip_count", DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, "leds_per_strip", DEFAULT_LEDS_PER_STRIP)
        self.width = max(1, self.num_strips)
        self.height = max(1, self.leds_per_strip)

        self.default_params.update(
            {
                "speed": 1.0,
                "wrap_edges": True,
                "random_density": 0.14,
                "phase_frames": 10,
                "generations_per_second": 5.0,
                "glider_interval": 10.0,
                "glider_count": 3,
                "stagnation_generations": 120,
                "destruct_on_loop": True,
                "destruct_on_loop_action": "glider_storm",
                "destruct_on_loop_history": 2048,
                "destruct_on_loop_gliders": 10,
                "seed_pattern": "random",
                "tile_installation": False,
                "tile_columns": 2,
                "tile_rows": 4,
                "tile_gutter": 1,
                "palette": "natural",
                "background": "void",
                "background_brightness": 0.18,
                "background_animation": True,
                "background_speed": 1.0,
                "background_fps": 12.0,
                "evolution_color_strength": 0.85,
                "random_seed": 0,
                "plant_nursery": True,
            }
        )
        self.params = {**self.default_params, **self.config}

        random_seed = int(self.params.get("random_seed", 0) or 0)
        self.random = random.Random(random_seed if random_seed else None)
        self.grid: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.next_grid: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.natural_grid: List[List[Optional[Color]]] = [
            [None for _ in range(self.width)] for _ in range(self.height)
        ]
        self.next_natural_grid: List[List[Optional[Color]]] = [
            [None for _ in range(self.width)] for _ in range(self.height)
        ]
        self.neighbor_counts: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]

        self.generation = 0
        self.alive_cells = 0
        self.births_last_generation = 0
        self.deaths_last_generation = 0
        self.last_step_elapsed: Optional[float] = None

        self.phase = "color"
        self.phase_frame = 0
        self.frame_progress = 0.0
        self.last_glider_time = 0.0
        self.stagnation_counter = 0
        self.previous_population = -1
        self._render_cells: List[Tuple[int, int]] = []
        self._last_frame = None
        self._background_cache_key = None
        self._background_cache = None
        background_hues = np.linspace(0.0, 1.0, 256, endpoint=False, dtype=np.float32)
        background_saturation = np.full(256, 0.92, dtype=np.float32)
        background_values = (
            0.35
            + 0.55
            * (np.sin(background_hues * math.tau * 3.0) * 0.5 + 0.5)
        ).astype(np.float32)
        background_palette = self.hsv_to_rgb_array(
            background_hues, background_saturation, background_values
        )
        self._arcade_background = AnimatedPaletteField(
            self.width, self.height, background_palette
        )
        background_y, background_x = np.indices(
            (self.height, self.width), dtype=np.float32
        )
        self._background_nx = (background_x + 0.5) / self.width
        self._background_ny = (background_y + 0.5) / self.height
        self._background_x = background_x
        integer_x = background_x.astype(np.int64)
        integer_y = background_y.astype(np.int64)
        self._star_hash = (
            (integer_x * 73856093) ^ (integer_y * 19349663) ^ 0x45D9F3B
        )
        self._star_mask = self._star_hash % 43 == 0
        self._background_layer = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._background_scaled = np.zeros_like(self._background_layer)
        self._tile_active_mask: Optional[np.ndarray] = None
        self._loop_history: Dict[Tuple[int, int], int] = {}
        self._loop_order: Deque[Tuple[Tuple[int, int], int]] = deque()
        self._next_state_fingerprint: Tuple[int, int] = (0, 0)
        self._tile_ids: Optional[List[List[int]]] = None
        self._tile_regions: List[Tuple[int, int, int, int]] = []
        self._plant_blocked = np.zeros((self.height, self.width), dtype=bool)
        self._plant_hazard = np.zeros((self.height, self.width), dtype=bool)
        self._plant_warning = np.zeros((self.height, self.width), dtype=bool)
        self._plant_fertile = np.zeros((self.height, self.width), dtype=bool)
        self._plant_fertile_flat = np.zeros(self.width * self.height, dtype=bool)
        self._plant_foliage_flat = np.zeros(self.width * self.height, dtype=bool)
        self._plant_globes_flat = np.zeros(self.width * self.height, dtype=bool)
        self._plant_foliage_count = 0
        self._plant_globe_count = 0
        self._plant_mask_error = ""
        self._plant_emitter_candidates: List[Tuple[int, int]] = []
        self.plant_hazard_deaths = 0
        self._next_plant_hazard_deaths = 0
        self.plant_emitted_cells = 0
        self.plant_emitter_events = 0
        self.destruct_on_loop_recoveries = 0
        self.last_detected_loop_period = 0
        self.last_detected_loop_generation = -1
        self.last_destruct_on_loop_action = ""

        self._refresh_plant_habitat()
        self._initialize_grid(self.params.get("seed_cells"))

    def get_parameter_schema(self) -> Dict[str, Any]:
        schema = super().get_parameter_schema()
        schema.update(
            {
                "wrap_edges": {
                    "type": "bool",
                    "default": True,
                    "description": "Wrap edges so patterns flow around panel borders",
                },
                "random_density": {
                    "type": "float",
                    "min": 0.0,
                    "max": 0.4,
                    "default": 0.14,
                    "description": "Spawn density when no seed cells are provided",
                },
                "phase_frames": {
                    "type": "int",
                    "min": 1,
                    "max": 30,
                    "default": 10,
                    "description": "Frames per color/transition phase",
                },
                "generations_per_second": {
                    "type": "float",
                    "min": 0.5,
                    "max": 20.0,
                    "default": 5.0,
                    "description": "Base simulation generations per second before speed scaling",
                },
                "glider_interval": {
                    "type": "float",
                    "min": 0.0,
                    "max": 60.0,
                    "default": 10.0,
                    "description": "Seconds between automatic glider injections (0 disables)",
                },
                "glider_count": {
                    "type": "int",
                    "min": 1,
                    "max": 12,
                    "default": 3,
                    "description": "Gliders introduced at each automatic injection",
                },
                "stagnation_generations": {
                    "type": "int",
                    "min": 0,
                    "max": 1000,
                    "default": 120,
                    "description": "Re-seed after this many stagnant generations (0 disables)",
                },
                "destruct_on_loop": {
                    "type": "bool",
                    "default": True,
                    "description": "Monitor repeated board states and destruct detected loops",
                },
                "destruct_on_loop_action": {
                    "type": "str",
                    "default": "glider_storm",
                    "options": ["glider_storm", "reseed", "restart"],
                    "description": "How to break a detected loop",
                },
                "destruct_on_loop_history": {
                    "type": "int",
                    "min": 16,
                    "max": 10000,
                    "default": 2048,
                    "description": "Generations of compact fingerprints retained for loop detection",
                },
                "destruct_on_loop_gliders": {
                    "type": "int",
                    "min": 1,
                    "max": 32,
                    "default": 10,
                    "description": "Gliders injected when the destruct action is glider storm",
                },
                "seed_pattern": {
                    "type": "str",
                    "default": "random",
                    "options": list(self.SEED_PATTERNS),
                    "description": "Initial Life pattern; reseed to apply a new selection",
                },
                "tile_installation": {
                    "type": "bool",
                    "default": False,
                    "description": "Repeat deterministic seeds in isolated finite regions",
                },
                "tile_columns": {
                    "type": "int",
                    "min": 1,
                    "max": 8,
                    "default": 2,
                    "description": "Number of isolated installation columns",
                },
                "tile_rows": {
                    "type": "int",
                    "min": 1,
                    "max": 16,
                    "default": 4,
                    "description": "Number of isolated installation rows",
                },
                "tile_gutter": {
                    "type": "int",
                    "min": 0,
                    "max": 3,
                    "default": 1,
                    "description": "Dead-cell border around every isolated tile",
                },
                "palette": {
                    "type": "str",
                    "default": "natural",
                    "options": list(self.PALETTES),
                    "description": "Living-cell palette; age shifts color without changing Life rules",
                },
                "background": {
                    "type": "str",
                    "default": "void",
                    "options": list(self.BACKGROUNDS),
                    "description": "Atmospheric backdrop rendered behind the simulation",
                },
                "background_brightness": {
                    "type": "float",
                    "min": 0.0,
                    "max": 0.6,
                    "default": 0.18,
                    "description": "Backdrop intensity relative to the living cells",
                },
                "background_animation": {
                    "type": "bool",
                    "default": True,
                    "description": "Animate the backdrop independently of Life generations",
                },
                "background_speed": {
                    "type": "float",
                    "min": 0.0,
                    "max": 3.0,
                    "default": 1.0,
                    "presets": {
                        "frozen": 0.0,
                        "gentle": 0.5,
                        "normal": 1.0,
                        "lively": 2.0,
                        "turbo": 3.0,
                    },
                    "description": "Backdrop motion speed (0 freezes the current atmosphere)",
                },
                "background_fps": {
                    "type": "float",
                    "min": 1.0,
                    "max": 30.0,
                    "default": 12.0,
                    "description": "Maximum backdrop refresh rate; Life simulation rate is unchanged",
                },
                "evolution_color_strength": {
                    "type": "float",
                    "min": 0.0,
                    "max": 1.0,
                    "default": 0.85,
                    "description": "How strongly cell age changes its color",
                },
                "random_seed": {
                    "type": "int",
                    "min": 0,
                    "max": 9999,
                    "default": 0,
                    "description": "Repeatable visual and seed variation (0 chooses a fresh run)",
                },
                "plant_nursery": {
                    "type": "bool",
                    "default": True,
                    "description": "Let globe-edge habitat grow Life with two neighbors instead of three",
                },
            }
        )
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        params = dict(new_params)
        spawn_glider = params.pop("spawn_glider", None)
        reseed = bool(params.pop("reseed", False))

        super().update_parameters(params)
        self._last_frame = None

        plant_geometry_keys = {
            "plant_aware", "plant_modifiers", "plant_clearance", "plant_mask_path",
            "plant_globe_mask_path", "plant_nursery",
        }
        if plant_geometry_keys & params.keys():
            self._refresh_plant_habitat()
            # A modifier-only change is not a semantic tick: retain the world,
            # RNG stream, phase, and generation while refreshing the planned
            # next generation under the newly selected rules.
            self._compute_next_state()
            return

        if "seed_cells" in params:
            self._initialize_grid(params.get("seed_cells"))
            return

        if "random_seed" in params:
            random_seed = int(self.params.get("random_seed", 0) or 0)
            self.random.seed(random_seed if random_seed else None)

        if any(key in params for key in ("tile_installation", "tile_columns", "tile_rows", "tile_gutter")):
            self._initialize_grid(self.params.get("seed_cells"))
            return

        if any(
            key in params
            for key in ("wrap_edges", "random_density", "phase_frames", "generations_per_second")
        ):
            self._compute_next_state()

        if any(key.startswith("destruct_on_loop") for key in params) or "wrap_edges" in params:
            self._reset_loop_monitor(record_current=True)

        if reseed or "seed_pattern" in params or "random_seed" in params:
            self._initialize_grid(None)

        if spawn_glider:
            self._spawn_glider(count=int(self.params.get("glider_count", 3) or 3))

    def get_runtime_stats(self) -> Dict[str, Any]:
        stats = {
            "generation": self.generation,
            "alive_cells": self.alive_cells,
            "population_ratio": self.alive_cells / max(1, self.width * self.height),
            "births_last_generation": self.births_last_generation,
            "deaths_last_generation": self.deaths_last_generation,
            "phase": self.phase,
            "phase_frame": self.phase_frame,
            "stagnation_counter": self.stagnation_counter,
            "destruct_on_loop": bool(self.params.get("destruct_on_loop", True)),
            "destruct_on_loop_history_entries": len(self._loop_history),
            "destruct_on_loop_recoveries": self.destruct_on_loop_recoveries,
            "last_detected_loop_period": self.last_detected_loop_period,
            "last_detected_loop_generation": self.last_detected_loop_generation,
            "last_destruct_on_loop_action": self.last_destruct_on_loop_action,
            "tile_installation": self._tile_ids is not None,
            "tile_regions": len(self._tile_regions),
            "plant_hazard_deaths": self.plant_hazard_deaths,
            "plant_emitted_cells": self.plant_emitted_cells,
            "plant_emitter_events": self.plant_emitter_events,
        }
        if self._plant_effects_enabled():
            stats.update(
                {
                    "plant_aware": True,
                    "plant_active_modifiers": list(self.plant_modifier_state().active),
                    "plant_foliage_pixels": self._plant_foliage_count,
                    "plant_globe_pixels": self._plant_globe_count,
                    "plant_nursery_pixels": int(np.count_nonzero(self._plant_fertile)),
                    "plant_mask_error": self._plant_mask_error,
                }
            )
        return stats

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        visual_changed = self._last_frame is None

        background = str(self.params.get("background", "void"))
        background_level = float(self.params.get("background_brightness", 0.18) or 0.0)
        background_enabled = background != "void" and background_level > 0.0
        background_fps = max(
            1.0, min(30.0, float(self.params.get("background_fps", 12.0) or 12.0))
        )
        background_speed = max(
            0.0, min(3.0, float(self.params.get("background_speed", 1.0) or 0.0))
        )
        background_animated = (
            background_enabled
            and bool(self.params.get("background_animation", True))
            and background_speed > 0.0
        )
        background_tick = (
            int(max(0.0, time_elapsed) * background_fps + 1e-9)
            if background_animated
            else 0
        )
        background_key = (
            background,
            background_level,
            float(self.params.get("brightness", 1.0) or 0.0),
            bool(self.params.get("background_animation", True)),
            background_speed,
            background_fps,
            background_tick,
            self.width,
            self.height,
        )
        if background_enabled and background_key != self._background_cache_key:
            visual_changed = True

        glider_interval = max(0.0, float(self.params.get("glider_interval", 10.0) or 0.0))
        if glider_interval > 0 and (time_elapsed - self.last_glider_time) >= glider_interval:
            self._spawn_glider(count=int(self.params.get("glider_count", 3) or 3))
            self.last_glider_time = time_elapsed
            visual_changed = True

        if self.last_step_elapsed is None:
            self.last_step_elapsed = time_elapsed
        else:
            delta = max(0.0, time_elapsed - self.last_step_elapsed)
            self.last_step_elapsed = time_elapsed
            step_interval = self._step_interval()
            self.frame_progress += delta / step_interval

            # Bound catch-up work if time jumps (pauses/debugger breakpoints).
            steps = 0
            while self.frame_progress >= 1.0 and steps < 40:
                self._advance_phase()
                self.frame_progress -= 1.0
                steps += 1
            visual_changed = visual_changed or steps > 0

        if not visual_changed and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        frame = self.next_frame_buffer(clear=True)
        if background_enabled:
            if self._background_cache_key == background_key and self._background_cache is not None:
                frame[:] = self._background_cache
            else:
                background_time = (background_tick / background_fps) * background_speed
                self._render_background(
                    frame, background, background_time, background_level
                )
                self._background_cache_key = background_key
                self._background_cache = frame.copy()
        if self._plant_effects_enabled():
            self._render_plant_habitat(frame)
        for x, y in self._render_cells:
            color = self._cell_color(x, y)
            if color:
                self._set_pixel(frame, x, y, color)

        self._last_frame = frame
        return self.rendered_frame(frame)

    def _initialize_grid(
        self,
        seed_cells: Optional[Iterable[Any]],
        seed_pattern_override: Optional[str] = None,
    ):
        self._configure_tile_installation()
        self.grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.next_grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.natural_grid = [[None for _ in range(self.width)] for _ in range(self.height)]
        self.next_natural_grid = [[None for _ in range(self.width)] for _ in range(self.height)]

        has_explicit_seed = seed_cells is not None
        parsed = self._parse_seed_cells(seed_cells)

        if has_explicit_seed:
            initial_color = self._random_natural_color()
            for x, y in parsed:
                if self._cell_is_active(x, y):
                    self.grid[y][x] = 1
                    self.natural_grid[y][x] = initial_color
        else:
            density = float(self.params.get("random_density", 0.14) or 0.0)
            seed_pattern = seed_pattern_override or str(self.params.get("seed_pattern", "random"))
            if seed_pattern == "random":
                if seed_pattern_override == "random" and density <= 0:
                    density = float(self.default_params["random_density"])
                density = max(0.0, min(0.4, density))
            else:
                density = 0.0
                self._seed_named_pattern(seed_pattern)
            if density > 0:
                for y in range(self.height):
                    for x in range(self.width):
                        if self._cell_is_active(x, y) and self.random.random() < density:
                            self.grid[y][x] = 1
                            self.natural_grid[y][x] = self._random_natural_color()

        self.generation = 0
        self.alive_cells = self._count_alive(self.grid)
        self.births_last_generation = 0
        self.deaths_last_generation = 0
        self.previous_population = self.alive_cells
        self.stagnation_counter = 0
        self.last_step_elapsed = None

        self.phase = "color"
        self.phase_frame = 0
        self.frame_progress = 0.0
        self._reset_loop_monitor(record_current=True)
        self._compute_next_state()

    def _parse_seed_cells(self, seed_cells: Optional[Iterable[Any]]) -> List[Tuple[int, int]]:
        if not seed_cells:
            return []

        parsed: List[Tuple[int, int]] = []
        for item in seed_cells:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    parsed.append((int(item[0]), int(item[1])))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, dict):
                try:
                    parsed.append((int(item.get("x")), int(item.get("y"))))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, str) and "," in item:
                parts = item.split(",")
                if len(parts) >= 2:
                    try:
                        parsed.append((int(parts[0].strip()), int(parts[1].strip())))
                    except (TypeError, ValueError):
                        continue
        return parsed

    def _step_interval(self) -> float:
        speed = max(0.1, float(self.params.get("speed", 1.0) or 1.0))
        base_gps = max(0.5, float(self.params.get("generations_per_second", 5.0) or 5.0))
        phase_frames = max(1, int(self.params.get("phase_frames", 10) or 10))
        steps_per_generation = max(1.0, phase_frames * 2.0)
        return 1.0 / max(1.0, base_gps * speed * steps_per_generation)

    def _advance_phase(self):
        phase_frames = max(1, int(self.params.get("phase_frames", 10) or 10))

        self.phase_frame += 1
        if self.phase_frame < phase_frames:
            return

        if self.phase == "color":
            self.phase = "transition"
            self.phase_frame = 0
            return

        self.grid = [row[:] for row in self.next_grid]
        self.natural_grid = [row[:] for row in self.next_natural_grid]
        self.generation += 1
        self.plant_hazard_deaths += self._next_plant_hazard_deaths
        self._emit_plant_cells()
        self.alive_cells = self._count_alive(self.grid)

        if self.alive_cells == self.previous_population:
            self.stagnation_counter += 1
        else:
            self.stagnation_counter = 0
        self.previous_population = self.alive_cells

        stagnation_limit = int(self.params.get("stagnation_generations", 120) or 0)
        if self.alive_cells == 0 or (stagnation_limit > 0 and self.stagnation_counter >= stagnation_limit):
            self._initialize_grid(None)
            return

        if bool(self.params.get("destruct_on_loop", True)):
            loop_period = self._remember_loop_fingerprint(
                self._next_state_fingerprint, self.generation
            )
            if loop_period is not None:
                self._destruct_detected_loop(loop_period)
                return

        self.phase = "color"
        self.phase_frame = 0
        self._compute_next_state()

    def _compute_next_state(self):
        wrap = bool(self.params.get("wrap_edges", True)) and self._tile_ids is None
        self.next_grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.next_natural_grid = [[None for _ in range(self.width)] for _ in range(self.height)]
        self.neighbor_counts = [[0 for _ in range(self.width)] for _ in range(self.height)]

        births = 0
        deaths = 0
        next_population = 0
        hazard_deaths = 0
        next_fingerprint = 14695981039346656037
        render_cells: List[Tuple[int, int]] = []
        alive_cells = {
            (x, y)
            for y, row in enumerate(self.grid)
            for x, value in enumerate(row)
            if value > 0
        }
        counts: Dict[Tuple[int, int], int] = {}
        for x, y in alive_cells:
            source_tile = self._tile_ids[y][x] if self._tile_ids is not None else 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx
                    ny = y + dy
                    if wrap:
                        nx %= self.width
                        ny %= self.height
                    elif not (0 <= nx < self.width and 0 <= ny < self.height):
                        continue
                    if self._tile_ids is not None and self._tile_ids[ny][nx] != source_tile:
                        continue
                    if self._obstacle_enabled() and self._plant_blocked[ny, nx]:
                        continue
                    key = (nx, ny)
                    counts[key] = counts.get(key, 0) + 1

        candidates = sorted(alive_cells | counts.keys(), key=lambda cell: (cell[1], cell[0]))
        for x, y in candidates:
            neighbors = counts.get((x, y), 0)
            self.neighbor_counts[y][x] = neighbors
            alive = (x, y) in alive_cells

            if alive and neighbors in (2, 3):
                self.next_grid[y][x] = min(self.grid[y][x] + 1, 20)
                self.next_natural_grid[y][x] = self.natural_grid[y][x]
            elif not alive and (
                neighbors == 3
                or (
                    self._habitat_enabled()
                    and bool(self.params.get("plant_nursery", True))
                    and self._plant_fertile[y, x]
                    and neighbors == 2
                )
            ):
                color_sum = [0, 0, 0]
                color_count = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx = x + dx
                        ny = y + dy
                        if wrap:
                            nx %= self.width
                            ny %= self.height
                        elif not (0 <= nx < self.width and 0 <= ny < self.height):
                            continue
                        if self._tile_ids is not None and self._tile_ids[ny][nx] != self._tile_ids[y][x]:
                            continue
                        neighbor_color = self.natural_grid[ny][nx]
                        if self.grid[ny][nx] > 0 and neighbor_color is not None:
                            color_sum[0] += neighbor_color[0]
                            color_sum[1] += neighbor_color[1]
                            color_sum[2] += neighbor_color[2]
                            color_count += 1
                self.next_grid[y][x] = 1
                if self._habitat_enabled() and self._plant_fertile[y, x] and neighbors == 2:
                    self.next_natural_grid[y][x] = (255, 176, 48)
                else:
                    self.next_natural_grid[y][x] = self._blend_neighbor_colors(color_sum, color_count)
                births += 1
            elif alive:
                deaths += 1

            if self._hazard_applies(x, y) and self.next_grid[y][x] > 0:
                self.next_grid[y][x] = 0
                self.next_natural_grid[y][x] = None
                hazard_deaths += 1
                deaths += 1

            if self.next_grid[y][x] > 0:
                next_population += 1
                next_fingerprint ^= y * self.width + x + 1
                next_fingerprint = (next_fingerprint * 1099511628211) & 0xFFFFFFFFFFFFFFFF

            if alive or self.next_grid[y][x] > 0:
                render_cells.append((x, y))

        self.births_last_generation = births
        self.deaths_last_generation = deaths
        self._render_cells = render_cells
        self._next_plant_hazard_deaths = hazard_deaths
        self._next_state_fingerprint = (next_fingerprint, next_population)

    def _fingerprint_grid(self) -> Tuple[int, int]:
        """Return a compact fingerprint of logical occupancy, excluding visual age/color."""
        fingerprint = 14695981039346656037
        population = 0
        for y, row in enumerate(self.grid):
            for x, value in enumerate(row):
                if value <= 0:
                    continue
                population += 1
                fingerprint ^= y * self.width + x + 1
                fingerprint = (fingerprint * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        return fingerprint, population

    def _reset_loop_monitor(self, record_current: bool = False):
        self._loop_history.clear()
        self._loop_order.clear()
        if record_current and bool(self.params.get("destruct_on_loop", True)):
            fingerprint = self._fingerprint_grid()
            self._loop_history[fingerprint] = self.generation
            self._loop_order.append((fingerprint, self.generation))

    def _remember_loop_fingerprint(
        self, fingerprint: Tuple[int, int], generation: int
    ) -> Optional[int]:
        previous_generation = self._loop_history.get(fingerprint)
        if previous_generation is not None:
            return max(1, generation - previous_generation)

        self._loop_history[fingerprint] = generation
        self._loop_order.append((fingerprint, generation))
        history_limit = max(
            16, min(10000, int(self.params.get("destruct_on_loop_history", 2048) or 2048))
        )
        while len(self._loop_order) > history_limit:
            old_fingerprint, old_generation = self._loop_order.popleft()
            if self._loop_history.get(old_fingerprint) == old_generation:
                del self._loop_history[old_fingerprint]
        return None

    def _destruct_detected_loop(self, period: int):
        action = str(self.params.get("destruct_on_loop_action", "glider_storm"))
        if action not in ("glider_storm", "reseed", "restart"):
            action = "glider_storm"

        self.destruct_on_loop_recoveries += 1
        self.last_detected_loop_period = period
        self.last_detected_loop_generation = self.generation
        self.last_destruct_on_loop_action = action

        if action == "reseed":
            self._initialize_grid(None, seed_pattern_override="random")
            return
        if action == "restart":
            self._initialize_grid(self.params.get("seed_cells"))
            return

        storm_count = max(
            1, min(32, int(self.params.get("destruct_on_loop_gliders", 10) or 10))
        )
        self._spawn_glider(count=storm_count)

    def _cell_color(self, x: int, y: int) -> Optional[Color]:
        alive_now = self.grid[y][x] > 0
        alive_next = self.next_grid[y][x] > 0
        if not alive_now and not alive_next:
            return None

        neighbors = self.neighbor_counts[y][x]
        neighbor_intensity = 0.65 + (neighbors / 8.0) * 0.55
        neighbor_intensity = max(0.4, min(1.3, neighbor_intensity))

        base_spawn = (0, 255, 0)
        base_die = (255, 40, 20)

        phase_frames = max(1, int(self.params.get("phase_frames", 10) or 10))
        phase_ratio = 0.0 if phase_frames <= 1 else self.phase_frame / (phase_frames - 1)

        if self.phase == "color":
            if not alive_now:
                return None

            natural_now = self._palette_color(x, y, self.grid[y][x], self.natural_grid[y][x])
            if alive_now and not alive_next:
                color = self._blend_colors(natural_now, base_die, phase_ratio)
                return self._clamp_color(
                    (
                        int(color[0] * neighbor_intensity),
                        int(color[1] * neighbor_intensity),
                        int(color[2] * neighbor_intensity),
                    )
                )

            shimmer_phase = (self.generation * phase_frames + self.phase_frame) / max(1.0, phase_frames)
            shimmer = 0.92 + 0.08 * math.sin(shimmer_phase * math.tau)
            return self._clamp_color(
                (
                    int(natural_now[0] * neighbor_intensity * shimmer),
                    int(natural_now[1] * neighbor_intensity * shimmer),
                    int(natural_now[2] * neighbor_intensity * shimmer),
                )
            )

        if alive_now and alive_next:
            natural_now = self._palette_color(x, y, self.grid[y][x], self.natural_grid[y][x])
            return self._clamp_color(
                (
                    int(natural_now[0] * neighbor_intensity),
                    int(natural_now[1] * neighbor_intensity),
                    int(natural_now[2] * neighbor_intensity),
                )
            )

        if alive_now and not alive_next:
            fade = 1.0 - phase_ratio
            return self._clamp_color(
                (
                    int(base_die[0] * neighbor_intensity * fade),
                    int(base_die[1] * neighbor_intensity * fade),
                    int(base_die[2] * neighbor_intensity * fade),
                )
            )

        if (not alive_now) and alive_next:
            fade = phase_ratio
            natural_next = self._palette_color(
                x, y, self.next_grid[y][x], self.next_natural_grid[y][x]
            )
            color = self._blend_colors(base_spawn, natural_next, phase_ratio)
            return self._clamp_color(
                (
                    int(color[0] * neighbor_intensity * fade),
                    int(color[1] * neighbor_intensity * fade),
                    int(color[2] * neighbor_intensity * fade),
                )
            )

        return None

    def _blend_neighbor_colors(self, color_sum: List[int], color_count: int) -> Color:
        if color_count <= 0:
            return self._random_natural_color()

        if color_count == 1:
            return self._clamp_color((color_sum[0], color_sum[1], color_sum[2]))

        return self._clamp_color(
            (
                int(color_sum[0] / color_count),
                int(color_sum[1] / color_count),
                int(color_sum[2] / color_count),
            )
        )

    def _blend_colors(self, first: Color, second: Color, ratio: float) -> Color:
        ratio = max(0.0, min(1.0, ratio))
        inv = 1.0 - ratio
        return (
            int(first[0] * inv + second[0] * ratio),
            int(first[1] * inv + second[1] * ratio),
            int(first[2] * inv + second[2] * ratio),
        )

    def _random_natural_color(self) -> Color:
        return (
            self.random.randint(40, 255),
            self.random.randint(40, 255),
            self.random.randint(40, 255),
        )

    def _palette_color(self, x: int, y: int, age: int, natural: Optional[Color]) -> Color:
        """Color a live cell by age while leaving its binary Life state untouched."""
        palette = str(self.params.get("palette", "natural"))
        strength = max(0.0, min(1.0, float(self.params.get("evolution_color_strength", 0.85) or 0.0)))
        progress = min(1.0, max(0, age - 1) / 19.0) * strength
        hue_offset = ((x * 0.037 + y * 0.021 + self.generation * 0.008) % 1.0) * (1.0 - strength)

        if palette == "natural":
            base = natural or self._random_natural_color()
            mature = (255, 232, 138)
            return self._blend_colors(base, mature, progress)

        young, mature = self.PALETTE_ENDPOINTS.get(palette, self.PALETTE_ENDPOINTS["aurora"])
        color = self._blend_colors(young, mature, min(1.0, progress + hue_offset * 0.25))
        return color

    def _render_background(
        self,
        frame: np.ndarray,
        style: str,
        time_elapsed: float,
        level: float,
    ):
        """Render a logical background and map it to physical strip order in bulk."""
        nx = self._background_nx
        ny = self._background_ny
        layer = self._background_layer

        if style == "arcade":
            source = self._arcade_background.render(
                time_elapsed, ticks_per_second=42.0
            )
        elif style == "twilight":
            horizon = np.clip(
                ny + 0.08 * np.sin(nx * 5.0 + time_elapsed * 0.32), 0.0, 1.0
            )
            pulse = 0.88 + 0.12 * math.sin(time_elapsed * 0.55)
            layer[:, :, 0] = (12.0 + 108.0 * horizon) * pulse
            layer[:, :, 1] = (7.0 + 17.0 * horizon) * pulse
            layer[:, :, 2] = (38.0 + 24.0 * horizon) * pulse
            source = layer
        elif style == "deep_ocean":
            wave = 0.5 + 0.5 * np.sin(
                nx * 12.0 + ny * 5.0 + time_elapsed * 0.25
            )
            layer[:, :, 0] = 0
            layer[:, :, 1] = 24.0 + wave * 28.0
            layer[:, :, 2] = 62.0 + wave * 48.0
            source = layer
        elif style == "aurora":
            ribbon = 0.5 + 0.5 * np.sin(
                nx * 10.0 + ny * 4.0 + time_elapsed * 0.18
            )
            layer[:, :, 0] = 18.0 + 25.0 * ribbon
            layer[:, :, 1] = 38.0 + 90.0 * ribbon
            layer[:, :, 2] = 75.0 + 80.0 * (1.0 - ribbon)
            source = layer
        elif style == "earth":
            rotating_x = np.remainder(nx + time_elapsed * 0.018, 1.0)
            land = np.zeros((self.height, self.width), dtype=bool)
            for cx, cy, rx, ry in (
                (0.20, 0.31, 0.15, 0.20), (0.27, 0.58, 0.07, 0.23),
                (0.51, 0.31, 0.11, 0.13), (0.58, 0.45, 0.09, 0.22),
                (0.72, 0.32, 0.19, 0.15), (0.82, 0.59, 0.10, 0.10),
            ):
                land |= ((rotating_x - cx) / rx) ** 2 + ((ny - cy) / ry) ** 2 <= 1.0
            blend = land.astype(np.float32) * 0.78
            daylight = 0.72 + 0.28 * np.sin((rotating_x - 0.12) * math.tau)
            ocean_red = 4.0 - 2.0 * ny
            ocean_green = 58.0 - 40.0 * ny
            ocean_blue = 92.0 - 47.0 * ny
            layer[:, :, 0] = ocean_red * (1.0 - blend) + 38.0 * daylight * blend
            layer[:, :, 1] = ocean_green * (1.0 - blend) + 92.0 * daylight * blend
            layer[:, :, 2] = ocean_blue * (1.0 - blend) + 52.0 * daylight * blend
            source = layer
        elif style == "starfield":
            layer[:] = (3, 5, 18)
            twinkle = 0.65 + 0.35 * np.sin(
                time_elapsed * 0.7 + self._star_hash % 17
            )
            layer[:, :, 0][self._star_mask] = (180.0 * twinkle)[self._star_mask]
            layer[:, :, 1][self._star_mask] = (205.0 * twinkle)[self._star_mask]
            layer[:, :, 2][self._star_mask] = (255.0 * twinkle)[self._star_mask]
            source = layer
        elif style == "ember":
            glow = (1.0 - ny) * (
                0.7 + 0.3 * np.sin(self._background_x * 1.7 + time_elapsed * 0.3)
            )
            layer[:, :, 0] = 90.0 * glow
            layer[:, :, 1] = 22.0 * glow
            layer[:, :, 2] = 5.0 * glow
            source = layer
        else:
            layer.fill(0)
            source = layer

        factor = max(0.0, min(0.6, level)) * max(
            0.0, min(1.0, float(self.params.get("brightness", 1.0) or 0.0))
        )
        np.multiply(source, factor, out=self._background_scaled, casting="unsafe")
        if self._tile_active_mask is not None:
            self._background_scaled[~self._tile_active_mask] = 0
        frame.reshape(self.width, self.height, 3)[:, ::-1, :] = (
            self._background_scaled.transpose(1, 0, 2)
        )

    def _background_color(self, x: int, y: int, time_elapsed: float) -> Color:
        style = str(self.params.get("background", "void"))
        level = max(0.0, min(0.6, float(self.params.get("background_brightness", 0.18) or 0.0)))
        nx = (x + 0.5) / self.width
        ny = (y + 0.5) / self.height

        if style == "twilight":
            horizon = max(
                0.0,
                min(1.0, ny + 0.08 * math.sin(nx * 5.0 + time_elapsed * 0.32)),
            )
            pulse = 0.88 + 0.12 * math.sin(time_elapsed * 0.55)
            color = tuple(
                int(channel * pulse)
                for channel in self._blend_colors((12, 7, 38), (120, 24, 62), horizon)
            )
        elif style == "deep_ocean":
            wave = 0.5 + 0.5 * math.sin(nx * 12.0 + ny * 5.0 + time_elapsed * 0.25)
            color = (0, int(24 + wave * 28), int(62 + wave * 48))
        elif style == "aurora":
            ribbon = 0.5 + 0.5 * math.sin(nx * 10.0 + ny * 4.0 + time_elapsed * 0.18)
            color = (int(18 + 25 * ribbon), int(38 + 90 * ribbon), int(75 + 80 * (1.0 - ribbon)))
        elif style == "earth":
            ocean = self._blend_colors((2, 18, 45), (4, 58, 92), 1.0 - ny)
            rotating_x = (nx + time_elapsed * 0.018) % 1.0
            land = self._earth_land_mask(rotating_x, ny)
            daylight = 0.72 + 0.28 * math.sin((rotating_x - 0.12) * math.tau)
            land_color = (
                int(38 * daylight), int(92 * daylight), int(52 * daylight)
            )
            color = self._blend_colors(ocean, land_color, 0.78 if land else 0.0)
        elif style == "starfield":
            star_hash = (x * 73856093) ^ (y * 19349663) ^ 0x45D9F3B
            star = star_hash % 43 == 0
            twinkle = 0.65 + 0.35 * math.sin(time_elapsed * 0.7 + star_hash % 17)
            color = (int(180 * twinkle), int(205 * twinkle), int(255 * twinkle)) if star else (3, 5, 18)
        elif style == "ember":
            glow = max(0.0, 1.0 - ny) * (0.7 + 0.3 * math.sin(x * 1.7 + time_elapsed * 0.3))
            color = (int(90 * glow), int(22 * glow), int(5 * glow))
        else:
            color = (0, 0, 0)
        return self._clamp_color(tuple(int(channel * level) for channel in color))

    @staticmethod
    def _earth_land_mask(nx: float, ny: float) -> bool:
        """A tiny equirectangular silhouette suitable for low-resolution LED panels."""
        continents = (
            (0.20, 0.31, 0.15, 0.20), (0.27, 0.58, 0.07, 0.23),
            (0.51, 0.31, 0.11, 0.13), (0.58, 0.45, 0.09, 0.22),
            (0.72, 0.32, 0.19, 0.15), (0.82, 0.59, 0.10, 0.10),
        )
        for cx, cy, rx, ry in continents:
            if ((nx - cx) / rx) ** 2 + ((ny - cy) / ry) ** 2 <= 1.0:
                return True
        return False

    def _seed_named_pattern(self, name: str):
        if self._tile_regions:
            for tile_index, region in enumerate(self._tile_regions):
                self._seed_pattern_in_region(name, region, tile_index)
            return
        if name == "glider_fleet":
            self._place_pattern([(1, 0), (2, 1), (0, 2), (1, 2), (2, 2)], self.width // 4, self.height // 4)
            self._place_pattern([(0, 0), (0, 1), (0, 2), (1, 0), (2, 1)], 3 * self.width // 4, 3 * self.height // 4)
            return
        if name == "oscillator_garden":
            blinker = [(0, 0), (1, 0), (2, 0)]
            for cy in range(3, self.height, 7):
                for cx in range(2, self.width, 7):
                    self._place_pattern(blinker, cx, cy, centered=True)
            return
        pattern = self.PATTERNS.get(name)
        if pattern:
            pattern_width = max(x for x, _ in pattern) + 1
            pattern_height = max(y for _, y in pattern) + 1
            if pattern_width > self.width and pattern_width <= self.height and pattern_height <= self.width:
                pattern = [(y, pattern_width - 1 - x) for x, y in pattern]
            self._place_pattern(pattern, self.width // 2, self.height // 2, centered=True)

    def _configure_tile_installation(self):
        self._background_cache_key = None
        self._background_cache = None
        if not bool(self.params.get("tile_installation", False)):
            self._tile_ids = None
            self._tile_regions = []
            self._tile_active_mask = None
            return

        columns = max(1, min(self.width, int(self.params.get("tile_columns", 2) or 2)))
        rows = max(1, min(self.height, int(self.params.get("tile_rows", 4) or 4)))
        gutter = max(0, min(3, int(self.params.get("tile_gutter", 1) or 0)))
        tile_ids = [[-1 for _ in range(self.width)] for _ in range(self.height)]
        regions: List[Tuple[int, int, int, int]] = []

        for tile_row in range(rows):
            raw_y0 = tile_row * self.height // rows
            raw_y1 = (tile_row + 1) * self.height // rows
            for tile_column in range(columns):
                raw_x0 = tile_column * self.width // columns
                raw_x1 = (tile_column + 1) * self.width // columns
                x0, x1 = raw_x0 + gutter, raw_x1 - gutter
                y0, y1 = raw_y0 + gutter, raw_y1 - gutter
                if x1 <= x0 or y1 <= y0:
                    continue
                tile_id = len(regions)
                regions.append((x0, y0, x1, y1))
                for y in range(y0, y1):
                    for x in range(x0, x1):
                        tile_ids[y][x] = tile_id

        self._tile_ids = tile_ids
        self._tile_regions = regions
        self._tile_active_mask = np.asarray(tile_ids, dtype=np.int16) >= 0

    def _cell_is_active(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        if self._tile_ids is not None and self._tile_ids[y][x] < 0:
            return False
        return not (self._obstacle_enabled() and self._plant_blocked[y, x])

    def _legacy_plant_mode(self) -> bool:
        """The old boolean combined blocking, globe nurseries, and landmarks."""
        raw_state = self.params.get("plant_modifiers") or {}
        return bool(self.params.get("plant_aware", False)) and not raw_state.get("active")

    def _plant_effects_enabled(self) -> bool:
        return self._legacy_plant_mode() or any(
            self.plant_modifier_enabled(modifier) for modifier in self.PLANT_MODIFIER_SUPPORT
        )

    def _obstacle_enabled(self) -> bool:
        return self._legacy_plant_mode() or self.plant_modifier_enabled("obstacle")

    def _habitat_enabled(self) -> bool:
        return self._legacy_plant_mode() or self.plant_modifier_enabled("habitat")

    def _hazard_enabled(self) -> bool:
        return self.plant_modifier_enabled("hazard")

    def _hazard_applies(self, x: int, y: int) -> bool:
        if not self._hazard_enabled() or not self._plant_hazard[y, x]:
            return False
        strength = self.plant_modifier_strength("hazard")
        # Strength is deterministic burn frequency, not fresh randomness.
        sample = (
            ((y * self.width + x + 1) * 2654435761)
            ^ ((self.generation + 1) * 2246822519)
        ) & 0xFFFF
        return sample / 65535.0 < strength

    def _emitter_enabled(self) -> bool:
        return self.plant_modifier_enabled("emitter")

    def _refresh_plant_habitat(self):
        """Map calibrated physical masks into Conway's top-down Life canvas."""
        self._plant_blocked.fill(False)
        self._plant_hazard.fill(False)
        self._plant_warning.fill(False)
        self._plant_fertile.fill(False)
        self._plant_fertile_flat.fill(False)
        self._plant_foliage_flat.fill(False)
        self._plant_globes_flat.fill(False)
        self._plant_foliage_count = 0
        self._plant_globe_count = 0
        self._plant_mask_error = ""
        self._plant_emitter_candidates = []
        if not self._plant_effects_enabled():
            return

        masks = self.get_plant_masks()
        if self._legacy_plant_mode():
            blocked_physical = masks.clearance
        else:
            blocked_physical = masks.obstacle.copy()
            obstacle_steps = int(round(
                max(0, int(self.params.get("plant_clearance", 1)))
                * self.plant_modifier_strength("obstacle")
            ))
            for _ in range(obstacle_steps):
                blocked_physical = dilate_8(blocked_physical)
        self._plant_blocked[:] = blocked_physical.T[::-1]
        self._plant_hazard[:] = masks.obstacle.T[::-1]
        warning_physical = dilate_8(masks.obstacle) & ~masks.obstacle
        self._plant_warning[:] = warning_physical.T[::-1]
        self._plant_foliage_flat[:] = masks.foliage_flat
        self._plant_globes_flat[:] = masks.globes_flat
        self._plant_foliage_count = masks.foliage_count
        self._plant_globe_count = masks.globe_count
        self._plant_mask_error = masks.error

        # Habitat belongs to foliage. Legacy boolean mode retains the prior
        # globe nursery exactly for compatibility with saved installations.
        habitat_core = masks.globes.copy() if self._legacy_plant_mode() else masks.foliage.copy()
        globe_reach = habitat_core
        habitat_steps = (
            max(0, int(self.params.get("plant_clearance", 1))) + 1
            if self._legacy_plant_mode()
            else 1 + int(round(
                (max(0, int(self.params.get("plant_clearance", 1))) + 1)
                * self.plant_modifier_strength("habitat")
            ))
        )
        for _ in range(habitat_steps):
            globe_reach = dilate_8(globe_reach)
        fertile_physical = globe_reach & ~masks.clearance
        self._plant_fertile[:] = fertile_physical.T[::-1]
        self._plant_fertile_flat[:] = fertile_physical.ravel()

        emitter_physical = dilate_8(masks.obstacle) & ~masks.obstacle
        emitter_canvas = emitter_physical.T[::-1]
        self._plant_emitter_candidates = [
            (x, y)
            for y, x in np.argwhere(emitter_canvas)
            if self._tile_ids is None or self._tile_ids[y][x] >= 0
        ]

    def _emit_plant_cells(self) -> None:
        """Inject bounded deterministic Life seeds at generation boundaries."""
        if not self._emitter_enabled() or not self._plant_emitter_candidates:
            return
        strength = self.plant_modifier_strength("emitter")
        requested = max(1, int(math.ceil(strength * 8.0)))
        start = (self.generation * 17) % len(self._plant_emitter_candidates)
        emitted = 0
        for offset in range(len(self._plant_emitter_candidates)):
            if emitted >= requested:
                break
            x, y = self._plant_emitter_candidates[(start + offset) % len(self._plant_emitter_candidates)]
            if self.grid[y][x] == 0 and self._cell_is_active(x, y):
                self.grid[y][x] = 1
                self.natural_grid[y][x] = (72, 236, 176)
                emitted += 1
        if emitted:
            self.plant_emitter_events += 1
            self.plant_emitted_cells += emitted

    def _render_plant_habitat(self, frame: np.ndarray):
        """Render modifier-specific semantic landmarks without changing Life."""
        if self._habitat_enabled() and np.any(self._plant_fertile_flat):
            frame[self._plant_fertile_flat] = np.maximum(
                frame[self._plant_fertile_flat], self.apply_brightness((34, 12, 2))
            )
        if (self._obstacle_enabled() or self._legacy_plant_mode()) and np.any(self._plant_foliage_flat):
            frame[self._plant_foliage_flat] = np.maximum(
                frame[self._plant_foliage_flat], self.apply_brightness((3, 38, 10))
            )
        if (self._obstacle_enabled() or self._legacy_plant_mode()) and np.any(self._plant_globes_flat):
            frame[self._plant_globes_flat] = np.maximum(
                frame[self._plant_globes_flat], self.apply_brightness((92, 18, 112))
            )
        if self._hazard_enabled():
            hazard_flat = self._plant_hazard[::-1].T.ravel()
            warning_flat = self._plant_warning[::-1].T.ravel()
            frame[warning_flat] = np.maximum(
                frame[warning_flat], self.apply_brightness((72, 18, 0))
            )
            hazard_level = self.plant_modifier_strength("hazard")
            frame[hazard_flat] = np.maximum(
                frame[hazard_flat], self.apply_brightness((int(55 + 95 * hazard_level), 5, 0))
            )
        if self._emitter_enabled() and self._plant_emitter_candidates:
            emitter_flat = np.zeros(self.width * self.height, dtype=bool)
            for x, y in self._plant_emitter_candidates:
                physical = x * self.height + (self.height - 1 - y)
                emitter_flat[physical] = True
            frame[emitter_flat] = np.maximum(
                frame[emitter_flat], self.apply_brightness((0, 30, 46))
            )

    def _seed_pattern_in_region(
        self,
        name: str,
        region: Tuple[int, int, int, int],
        tile_index: int,
    ):
        x0, y0, x1, y1 = region
        if name == "oscillator_garden":
            pattern = [(0, 0), (1, 0), (2, 0)]
            if tile_index % 2:
                pattern = [(0, 0), (0, 1), (0, 2)]
        elif name == "glider_fleet":
            gliders = (
                [(1, 0), (2, 1), (0, 2), (1, 2), (2, 2)],
                [(0, 0), (0, 1), (0, 2), (1, 0), (2, 1)],
                [(0, 0), (1, 0), (2, 0), (0, 1), (1, 2)],
                [(0, 1), (1, 2), (2, 0), (2, 1), (2, 2)],
            )
            pattern = gliders[tile_index % len(gliders)]
        else:
            pattern = list(self.PATTERNS.get(name, []))
        if not pattern:
            return

        region_width, region_height = x1 - x0, y1 - y0
        pattern_width = max(x for x, _ in pattern) + 1
        pattern_height = max(y for _, y in pattern) + 1
        if pattern_width > region_width and pattern_width <= region_height and pattern_height <= region_width:
            pattern = [(y, pattern_width - 1 - x) for x, y in pattern]
            pattern_width, pattern_height = pattern_height, pattern_width
        if pattern_width > region_width or pattern_height > region_height:
            return

        if tile_index % 2:
            pattern = [(pattern_width - 1 - x, y) for x, y in pattern]
        if (tile_index // 2) % 2:
            pattern = [(x, pattern_height - 1 - y) for x, y in pattern]
        self._place_pattern(pattern, (x0 + x1) // 2, (y0 + y1) // 2, centered=True)

    def _place_pattern(self, pattern, origin_x: int, origin_y: int, centered: bool = False):
        if not pattern:
            return
        if centered:
            origin_x -= (max(x for x, _ in pattern) + 1) // 2
            origin_y -= (max(y for _, y in pattern) + 1) // 2
        color = self._random_natural_color()
        for dx, dy in pattern:
            x, y = origin_x + dx, origin_y + dy
            if 0 <= x < self.width and 0 <= y < self.height and (
                not self._obstacle_enabled() or self._cell_is_active(x, y)
            ):
                self.grid[y][x] = 1
                self.natural_grid[y][x] = color

    def _clamp_color(self, color: Color) -> Color:
        return (
            max(0, min(255, int(color[0]))),
            max(0, min(255, int(color[1]))),
            max(0, min(255, int(color[2]))),
        )

    def _spawn_glider(self, count: int = 1):
        if self.width < 3 or self.height < 3:
            return

        gliders = (
            [(1, 0), (2, 1), (0, 2), (1, 2), (2, 2)],
            [(0, 0), (0, 1), (0, 2), (1, 0), (2, 1)],
            [(0, 0), (1, 0), (2, 0), (0, 1), (1, 2)],
            [(0, 1), (1, 2), (2, 0), (2, 1), (2, 2)],
        )
        spawn_regions = [
            region for region in self._tile_regions if region[2] - region[0] >= 3 and region[3] - region[1] >= 3
        ]
        if not spawn_regions:
            spawn_regions = [(0, 0, self.width, self.height)]
        spawn_count = max(1, int(count))

        used_origins = set()
        attempts = 0
        while len(used_origins) < spawn_count and attempts < spawn_count * 8:
            x0, y0, x1, y1 = self.random.choice(spawn_regions)
            origin_x = self.random.randint(x0, x1 - 3)
            origin_y = self.random.randint(y0, y1 - 3)
            attempts += 1

            if (origin_x, origin_y) in used_origins:
                continue

            glider = self.random.choice(gliders)
            if self._obstacle_enabled() and any(
                not self._cell_is_active(origin_x + dx, origin_y + dy)
                for dx, dy in glider
            ):
                continue

            used_origins.add((origin_x, origin_y))
            glider_color = self._random_natural_color()
            for dx, dy in glider:
                x = origin_x + dx
                y = origin_y + dy
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = 1
                    self.natural_grid[y][x] = glider_color

        self.phase = "color"
        self.phase_frame = 0
        self.frame_progress = 0.0
        self.alive_cells = self._count_alive(self.grid)
        self.previous_population = self.alive_cells
        self.stagnation_counter = 0
        self._reset_loop_monitor(record_current=True)
        self._compute_next_state()

    def _count_alive(self, target_grid: List[List[int]]) -> int:
        alive = 0
        for row in target_grid:
            for value in row:
                if value > 0:
                    alive += 1
        return alive

    def _set_pixel(self, frame: List[Color], strip: int, led: int, color: Color):
        if strip < 0 or strip >= self.num_strips:
            return

        if led < 0 or led >= self.leds_per_strip:
            return

        # LED columns are physically reversed on each strip in this layout.
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            frame[idx] = self.apply_brightness(color)

"""Scene v2 Mini Metro: octilinear lines, geometric stations, and shuttling trains."""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.scene_palettes import HOLIDAY_STOPS


_HUE_GOLDEN = np.float32(0.6180339887)

# Mini Metro's opening station glyphs, plus two later specials, as 3x3 stamps.
_SHAPES = (
    ((0, 1, 0), (1, 1, 1), (0, 1, 0)),  # circle
    ((0, 1, 0), (1, 1, 1), (1, 0, 1)),  # triangle
    ((1, 1, 1), (1, 0, 1), (1, 1, 1)),  # square
    ((0, 1, 0), (1, 0, 1), (0, 1, 0)),  # diamond
    ((1, 0, 1), (0, 1, 0), (1, 0, 1)),  # star
)
_CLASSIC_LINES = (
    (226.0, 62.0, 62.0),
    (61.0, 126.0, 219.0),
    (240.0, 192.0, 0.0),
    (60.0, 180.0, 74.0),
    (245.0, 130.0, 49.0),
)


@dataclass
class _Train:
    line: int
    pos: float
    direction: float
    dwell: float = 0.0
    last_stop: int = -1
    cargo: list[int] = field(default_factory=list)


class MiniMetroAnimation(AnimationBase):
    ANIMATION_NAME = "Mini Metro"
    ANIMATION_DESCRIPTION = "Octilinear subway lines, geometric stations, and shuttling trains"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "mini_metro", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    SIM_HZ = 24.0
    MAX_CATCH_UP_STEPS = 8
    MIN_STATIONS, MAX_STATIONS = 4, 14
    MIN_LINES, MAX_LINES = 2, 5
    MIN_TRAINS, MAX_TRAINS = 1, 8
    MAX_WAIT, MAX_CARGO = 6, 4
    DWELL_SECONDS = 0.42
    DEFAULTS = MappingProxyType({
        "stations": 9, "lines": 3, "trains": 4, "traffic": 0.55, "train_speed": 0.85, "seed": 8080,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE,
        timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS,
    )
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": MappingProxyType({
            "paper": (16.0, 18.0, 22.0), "river": (22.0, 48.0, 78.0), "station": (236.0, 238.0, 242.0),
        }),
        "mist": MappingProxyType({
            "paper": (12.0, 18.0, 28.0), "river": (28.0, 64.0, 96.0), "station": (214.0, 228.0, 240.0),
        }),
        "spectrum": MappingProxyType({
            "paper": (10.0, 6.0, 22.0), "river": (48.0, 18.0, 92.0), "station": (255.0, 240.0, 255.0),
        }),
        "ember": MappingProxyType({
            "paper": (22.0, 12.0, 8.0), "river": (48.0, 28.0, 16.0), "station": (255.0, 228.0, 196.0),
        }),
        "rainbow": MappingProxyType({
            "paper": (8.0, 8.0, 16.0), "river": (16.0, 28.0, 64.0), "station": (248.0, 248.0, 255.0),
        }),
        "xmas": MappingProxyType({
            "paper": (8.0, 18.0, 10.0), "river": (12.0, 48.0, 28.0), "station": (255.0, 232.0, 196.0),
        }),
        "fall": MappingProxyType({
            "paper": (22.0, 10.0, 4.0), "river": (64.0, 32.0, 12.0), "station": (255.0, 214.0, 160.0),
        }),
        "easter": MappingProxyType({
            "paper": (28.0, 12.0, 36.0), "river": (72.0, 36.0, 88.0), "station": (255.0, 236.0, 248.0),
        }),
        "july4": MappingProxyType({
            "paper": (4.0, 8.0, 28.0), "river": (16.0, 28.0, 92.0), "station": (240.0, 244.0, 255.0),
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
            "stations": {"type": "int", "min": self.MIN_STATIONS, "max": self.MAX_STATIONS, "default": 9,
                         "description": "How many geometric stations sit on the map."},
            "lines": {"type": "int", "min": self.MIN_LINES, "max": self.MAX_LINES, "default": 3,
                      "description": "How many colored octilinear routes run."},
            "trains": {"type": "int", "min": self.MIN_TRAINS, "max": self.MAX_TRAINS, "default": 4,
                       "description": "How many trains shuttle the network."},
            "traffic": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.55,
                        "description": "How quickly passengers appear at stations."},
            "train_speed": {"type": "float", "min": 0.25, "max": 1.8, "default": 0.85,
                            "description": "How quickly trains travel the lines."},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 8080,
                     "description": "Repeatable city layout seed."},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        reset = any(candidate[name] != self.params[name] for name in ("seed", "stations", "lines", "trains"))
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
            raise ValueError("Mini Metro received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Mini Metro requires a semantic Scene v2 palette")
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
        if any(candidate[name] != self.params[name] for name in ("seed", "stations", "lines", "trains")):
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
        self._paint(palette_id)
        self._last_render_key = key
        return RenderedFrame(self._pixels, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "seed": self.params["seed"],
            "tick": self._last_tick,
            "stations": self.station_xy.tobytes(),
            "shapes": self.station_shape.tobytes(),
            "waiting": tuple(tuple(queue) for queue in self.waiting),
            "trains": tuple((train.line, round(train.pos, 3), train.direction, tuple(train.cargo)) for train in self.trains),
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.SIM_HZ, "tick": self._last_tick})

    def _initialize_simulation(self) -> None:
        rng = np.random.default_rng(int(self.params["seed"]))
        count = int(self.params["stations"])
        self.station_xy = self._place_stations(rng, count)
        self.station_shape = (np.arange(count, dtype=np.int32) % len(_SHAPES))
        rng.shuffle(self.station_shape)
        self.line_stations = self._build_lines(int(self.params["lines"]))
        self.line_points = []
        self.line_stops: list[list[tuple[int, int]]] = []
        self.line_shapes: list[set[int]] = []
        for members in self.line_stations:
            points, stops = self._route_line(members)
            self.line_points.append(points)
            self.line_stops.append(stops)
            self.line_shapes.append({int(self.station_shape[index]) for index in members})
        self.line_hue = np.mod(np.arange(len(self.line_stations), dtype=np.float32) * _HUE_GOLDEN, np.float32(1.0))
        self.waiting = [[] for _ in range(count)]
        self.trains = self._place_trains(rng, int(self.params["trains"]))
        self._river = self._raster_river()
        self._rng = rng
        self._delivered = 0
        self._last_tick = None
        self._last_render_key = None

    def _place_stations(self, rng: np.random.Generator, count: int) -> np.ndarray:
        margin_x = 2 if self.width > 6 else 0
        margin_y = 3 if self.height > 12 else 0
        min_dist = 11.0 if self.height >= 80 else 6.0
        placed: list[tuple[float, float]] = []
        attempts = 0
        while len(placed) < count and attempts < 800:
            attempts += 1
            x = float(rng.uniform(margin_x + 1.0, max(margin_x + 1.5, self.width - margin_x - 2.0)))
            y = float(rng.uniform(margin_y + 1.0, max(margin_y + 1.5, self.height - margin_y - 2.0)))
            river_x = self._river_x(y)
            if abs(x - river_x) < 2.4 and self.width > 8:
                x = min(self.width - 3.0, river_x + 3.5) if x >= river_x else max(2.0, river_x - 3.5)
            if all((x - px) ** 2 + (y - py) ** 2 >= min_dist * min_dist for px, py in placed):
                placed.append((x, y))
        while len(placed) < count:
            placed.append((
                1.0 + (len(placed) % max(1, self.width - 2)),
                2.0 + (len(placed) * 9) % max(1, self.height - 4),
            ))
        return np.asarray(placed[:count], dtype=np.float32)

    def _build_lines(self, n_lines: int) -> list[list[int]]:
        count = int(self.station_xy.shape[0])
        order = np.argsort(self.station_xy[:, 1])
        n_lines = max(1, min(n_lines, count - 1))
        lines: list[list[int]] = []
        for index in range(n_lines):
            members = [int(station) for station in order[index::n_lines]]
            if len(members) < 3:
                extra = [int(station) for station in order if int(station) not in members]
                members.extend(extra[: max(0, 3 - len(members))])
            if n_lines > 1 and count > 3:
                interchange = int(order[(index + 1) % n_lines])
                if interchange not in members:
                    members.append(interchange)
            members = sorted(set(members), key=lambda station: float(self.station_xy[station, 1]))
            if len(members) >= 2:
                lines.append(members)
        if not lines:
            lines = [list(range(max(2, count)))]
        return lines

    def _route_line(self, members: list[int]) -> tuple[np.ndarray, list[tuple[int, int]]]:
        points: list[tuple[int, int]] = []
        stops: list[tuple[int, int]] = []
        for start, end in zip(members, members[1:]):
            x0, y0 = self.station_xy[start]
            x1, y1 = self.station_xy[end]
            segment = self._octilinear(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
            if points and segment and segment[0] == points[-1]:
                segment = segment[1:]
            if not points:
                stops.append((start, 0))
            points.extend(segment)
            stops.append((end, max(0, len(points) - 1)))
        if not points:
            x0, y0 = self.station_xy[members[0]]
            points = [(int(round(x0)), int(round(y0)))]
            stops = [(members[0], 0)]
        return np.asarray(points, dtype=np.int32), stops

    @staticmethod
    def _octilinear(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = []
        x, y = x0, y0
        for _ in range(512):
            points.append((x, y))
            if x == x1 and y == y1:
                break
            dx = 0 if x == x1 else (1 if x1 > x else -1)
            dy = 0 if y == y1 else (1 if y1 > y else -1)
            x += dx
            y += dy
        return points

    def _place_trains(self, rng: np.random.Generator, count: int) -> list[_Train]:
        trains: list[_Train] = []
        n_lines = max(1, len(self.line_points))
        for index in range(count):
            line = index % n_lines
            length = max(1, int(self.line_points[line].shape[0]) - 1)
            pos = float(rng.uniform(0.0, float(length))) if length else 0.0
            direction = 1.0 if rng.random() < 0.5 else -1.0
            trains.append(_Train(line=line, pos=pos, direction=direction))
        return trains

    def _river_x(self, y: float) -> float:
        if self.width <= 4:
            return self.width * 0.5
        return (self.width * 0.46) + 3.2 * math.sin(float(y) * math.tau / max(18.0, self.height / 3.5))

    def _raster_river(self) -> np.ndarray:
        coords: list[tuple[int, int]] = []
        for row in range(self.height):
            col = int(round(self._river_x(float(row))))
            for offset in (-1, 0, 1):
                x = col + offset
                if 0 <= x < self.width:
                    coords.append((x, row))
        if not coords:
            return np.zeros((0, 2), dtype=np.int32)
        return np.asarray(coords, dtype=np.int32)

    def _simulate_step(self, dt: float) -> None:
        traffic = float(self.params["traffic"])
        speed = 22.0 * float(self.params["train_speed"])
        for station, queue in enumerate(self.waiting):
            if len(queue) >= self.MAX_WAIT or traffic <= 0.0:
                continue
            if float(self._rng.random()) < traffic * 0.045:
                dest = self._destination_for(station)
                if dest is not None:
                    queue.append(dest)
        for train in self.trains:
            points = self.line_points[train.line]
            last = max(0.0, float(points.shape[0] - 1))
            if last <= 0.0:
                continue
            if train.dwell > 0.0:
                train.dwell = max(0.0, train.dwell - dt)
                continue
            old = train.pos
            train.pos += train.direction * speed * dt
            crossed = self._crossed_stop(train, old)
            if crossed is not None:
                station, index = crossed
                train.pos = float(index)
                self._exchange(train, station)
                train.dwell = self.DWELL_SECONDS
                train.last_stop = station
                continue
            if train.pos >= last:
                train.pos = last
                train.direction = -1.0
            elif train.pos <= 0.0:
                train.pos = 0.0
                train.direction = 1.0

    def _crossed_stop(self, train: _Train, old_pos: float) -> tuple[int, int] | None:
        low, high = (old_pos, train.pos) if train.pos >= old_pos else (train.pos, old_pos)
        closest: tuple[int, int] | None = None
        best = 1.0e9
        for station, index in self.line_stops[train.line]:
            if station == train.last_stop:
                continue
            marker = float(index)
            if low - 1.0e-6 <= marker <= high + 1.0e-6:
                distance = abs(marker - old_pos)
                if distance < best:
                    best = distance
                    closest = (station, index)
        return closest

    def _destination_for(self, station: int) -> int | None:
        own = int(self.station_shape[station])
        choices: list[int] = []
        for line_index, members in enumerate(self.line_stations):
            if station not in members:
                continue
            choices.extend(shape for shape in self.line_shapes[line_index] if shape != own)
        if not choices:
            return None
        return int(choices[int(self._rng.integers(0, len(choices)))])

    def _exchange(self, train: _Train, station: int) -> None:
        shape = int(self.station_shape[station])
        kept = [cargo for cargo in train.cargo if cargo != shape]
        self._delivered += len(train.cargo) - len(kept)
        train.cargo = kept
        served = self.line_shapes[train.line]
        queue = self.waiting[station]
        remaining: list[int] = []
        for passenger in queue:
            if passenger in served and len(train.cargo) < self.MAX_CARGO:
                train.cargo.append(passenger)
            else:
                remaining.append(passenger)
        self.waiting[station] = remaining

    def _paint(self, palette_id: str) -> None:
        palette = self.palette_roles(palette_id)
        paper = np.asarray(palette["paper"], dtype=np.float32)
        river = np.asarray(palette["river"], dtype=np.float32)
        station_color = np.asarray(palette["station"], dtype=np.float32)
        canvas = self._canvas_float
        canvas[:] = paper
        if self._river.size:
            canvas[self._river[:, 0], self._river[:, 1]] = river
        for line_index, points in enumerate(self.line_points):
            color = self._line_color(palette_id, line_index)
            dim = color * 0.78
            for col, row in points:
                if 0 <= col < self.width and 0 <= row < self.height:
                    canvas[col, row] = dim
        for index in range(int(self.station_xy.shape[0])):
            self._stamp_station(canvas, index, station_color)
            self._stamp_passengers(canvas, index, station_color)
        for train in self.trains:
            self._stamp_train(canvas, train, palette_id, station_color)
        np.clip(canvas, 0.0, 255.0, out=canvas)
        logical = self._pixels.reshape(self.width, self.height, 3)[:, ::-1]
        np.copyto(logical, canvas, casting="unsafe")

    def _line_color(self, palette_id: str, line_index: int) -> np.ndarray:
        hue = float(self.line_hue[line_index % self.line_hue.size])
        if palette_id == "rainbow":
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.86, 1.0)
            return np.asarray((red * 255.0, green * 255.0, blue * 255.0), dtype=np.float32)
        stops = HOLIDAY_STOPS.get(str(palette_id))
        if stops:
            return np.asarray(stops[line_index % len(stops)], dtype=np.float32)
        return np.asarray(_CLASSIC_LINES[line_index % len(_CLASSIC_LINES)], dtype=np.float32)

    def _stamp_station(self, canvas: np.ndarray, index: int, color: np.ndarray) -> None:
        cx, cy = int(round(float(self.station_xy[index, 0]))), int(round(float(self.station_xy[index, 1])))
        glyph = _SHAPES[int(self.station_shape[index]) % len(_SHAPES)]
        for dy, row in enumerate(glyph):
            for dx, on in enumerate(row):
                if not on:
                    continue
                col, r = cx + dx - 1, cy + dy - 1
                if 0 <= col < self.width and 0 <= r < self.height:
                    canvas[col, r] = color

    def _stamp_passengers(self, canvas: np.ndarray, index: int, color: np.ndarray) -> None:
        cx, cy = int(round(float(self.station_xy[index, 0]))), int(round(float(self.station_xy[index, 1])))
        col = cx + 2 if cx + 2 < self.width else cx - 2
        for offset, _passenger in enumerate(self.waiting[index][: self.MAX_WAIT]):
            row = cy - 1 + offset
            if 0 <= col < self.width and 0 <= row < self.height:
                canvas[col, row] = color * (0.55 + 0.08 * offset)

    def _stamp_train(self, canvas: np.ndarray, train: _Train, palette_id: str, lamp: np.ndarray) -> None:
        points = self.line_points[train.line]
        if points.shape[0] == 0:
            return
        color = self._line_color(palette_id, train.line)
        last = points.shape[0] - 1
        for along, gain in ((0, 1.0), (1, 0.82), (2, 0.62)):
            index = int(round(train.pos - train.direction * along))
            index = int(np.clip(index, 0, last))
            col, row = int(points[index, 0]), int(points[index, 1])
            if 0 <= col < self.width and 0 <= row < self.height:
                canvas[col, row] = lamp if along == 0 else color * gain
        for extra, _cargo in enumerate(train.cargo[:2]):
            index = int(round(train.pos - train.direction * (1 + extra)))
            index = int(np.clip(index, 0, last))
            col, row = int(points[index, 0]), int(points[index, 1])
            neighbor = col + 1 if col + 1 < self.width else col - 1
            if 0 <= neighbor < self.width and 0 <= row < self.height:
                canvas[neighbor, row] = lamp * 0.7

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Mini Metro does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        seed = result["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        for name, low, high in (
            ("stations", cls.MIN_STATIONS, cls.MAX_STATIONS),
            ("lines", cls.MIN_LINES, cls.MAX_LINES),
            ("trains", cls.MIN_TRAINS, cls.MAX_TRAINS),
        ):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        for name, low, high in (("traffic", 0.0, 1.0), ("train_speed", 0.25, 1.8)):
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

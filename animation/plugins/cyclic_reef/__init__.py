"""Scene v2 Cyclic Reef: a bounded coral-colony instrument."""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene


class CyclicReefAnimation(AnimationBase):
    """Competing coral species, open grazer channels, and luminous boundaries.

    The simulation owns only species and channel state. Scene v2 owns palette,
    final brightness, plant optics, and the global pace multiplier.
    """

    ANIMATION_NAME = "Cyclic Reef"
    ANIMATION_DESCRIPTION = "A living coral instrument of competing species and grazing channels"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "cyclic_reef", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    BASE_SIMULATION_HZ, MAX_CATCH_UP_STEPS, MAX_GRAZERS = 12.0, 12, 48
    TOPOLOGIES = ("wrap", "closed")
    DEFAULTS = MappingProxyType({
        "species_count": 5, "takeover_threshold": 2, "mutation": 0.002,
        "grazers": 8, "boundary_glow": 0.55, "topology": "wrap",
        "pace": 1.0, "seed": 13100,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER,
        role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="opaque",
        palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",),
        fidelity_exceptions=(), defaults=DEFAULTS,
    )
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": ((3.0, 12.0, 18.0), (28.0, 151.0, 132.0), (163.0, 255.0, 211.0)),
        "mist": ((3.0, 10.0, 21.0), (46.0, 118.0, 174.0), (177.0, 234.0, 246.0)),
        "spectrum": ((15.0, 3.0, 34.0), (106.0, 45.0, 201.0), (61.0, 239.0, 225.0)),
        "ember": ((20.0, 3.0, 2.0), (180.0, 48.0, 18.0), (255.0, 205.0, 82.0)),
    })
    CAVITY = np.uint8(255)

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._canvas = self._pixels.reshape(self.width, self.height, 3)
        self._neighbors = np.zeros((self.height, self.width), dtype=np.uint8)
        self._boundary = np.zeros((self.height, self.width), dtype=bool)
        self._last_tick: int | None = None
        self._last_render_key: tuple[Any, ...] | None = None
        self._presentation_context: ResolvedScene | None = None
        self._rng = np.random.default_rng(self.params["seed"])
        self._reset_colony()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "species_count": {"type": "int", "min": 3, "max": 8, "default": 5, "description": "Number of competing coral species."},
            "takeover_threshold": {"type": "int", "min": 1, "max": 5, "default": 2, "description": "Neighbor pressure needed for the next species to take over."},
            "mutation": {"type": "float", "min": 0.0, "max": .02, "default": .002, "description": "Rare mutation that prevents a settled colony."},
            "grazers": {"type": "int", "min": 0, "max": self.MAX_GRAZERS, "default": 8, "description": "Mobile grazers that cut temporary dark channels."},
            "boundary_glow": {"type": "float", "min": 0.0, "max": 1.0, "default": .55, "description": "Luminous emphasis at species boundaries."},
            "topology": {"type": "str", "options": list(self.TOPOLOGIES), "default": "wrap", "description": "Whether coral flows through the wall edges or meets a closed reef edge."},
            "pace": {"type": "float", "min": .25, "max": 2.0, "default": 1.0, "description": "Local colony change cadence."},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 13100, "description": "Repeatable colony starting arrangement."},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        reset = any(candidate[name] != self.params[name] for name in ("seed", "species_count", "grazers"))
        self.params = candidate
        if reset:
            self._rng = np.random.default_rng(self.params["seed"])
            self._reset_colony()
        self._last_render_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        identity = descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value
        if identity != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Cyclic Reef received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Cyclic Reef requires a semantic Scene v2 palette")
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
            palette_id, parameters = str(self._presentation_context.palette["palette_id"]), self._presentation_context.parameters
        candidate = self._normalized_parameters(parameters)
        if candidate != self.params:
            self.update_parameters(candidate)
        tick = int(math.floor(phase_time * self.BASE_SIMULATION_HZ * self.params["pace"] + 1.e-9))
        if self._last_tick is None:
            self._last_tick = tick
        else:
            target = min(tick, self._last_tick + self.MAX_CATCH_UP_STEPS)
            while self._last_tick < target:
                self._step()
                self._last_tick += 1
        key = (self._last_tick, palette_id, self.params["boundary_glow"], self.params["topology"])
        if key == self._last_render_key:
            return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        self._paint(palette_id)
        self._last_render_key = key
        return RenderedFrame(self._pixels, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "tick": self._last_tick, "species_count": self.params["species_count"],
            "population": tuple(int(np.count_nonzero(self.state == species)) for species in range(self.params["species_count"])),
            "cavities": int(np.count_nonzero(self.state == self.CAVITY)),
            "grazers": tuple(zip(self._grazer_x.tolist(), self._grazer_y.tolist())),
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.BASE_SIMULATION_HZ * self.params["pace"], "tick": self._last_tick, "max_catch_up_steps": self.MAX_CATCH_UP_STEPS})

    def logical_state(self) -> tuple[bytes, bytes, bytes]:
        return self.state.tobytes(), self._grazer_x.tobytes(), self._grazer_y.tobytes()

    def _reset_colony(self) -> None:
        species = self.params["species_count"]
        self.state = self._rng.integers(0, species, (self.height, self.width), dtype=np.uint8)
        self.state[self._rng.random(self.state.shape) < .025] = self.CAVITY
        count = self.params["grazers"]
        self._grazer_x = self._rng.integers(0, self.width, count, dtype=np.int16)
        self._grazer_y = self._rng.integers(0, self.height, count, dtype=np.int16)
        self._grazer_dx = self._rng.integers(-1, 2, count, dtype=np.int8)
        self._grazer_dy = self._rng.integers(-1, 2, count, dtype=np.int8)
        self._last_tick = None

    def _shift(self, source: np.ndarray, dy: int, dx: int) -> np.ndarray:
        shifted = np.roll(np.roll(source, dy, axis=0), dx, axis=1)
        if self.params["topology"] == "closed":
            if dy > 0: shifted[:dy, :] = self.CAVITY
            elif dy < 0: shifted[dy:, :] = self.CAVITY
            if dx > 0: shifted[:, :dx] = self.CAVITY
            elif dx < 0: shifted[:, dx:] = self.CAVITY
        return shifted

    def _step(self) -> None:
        species = self.params["species_count"]
        successor = (self.state.astype(np.int16) + 1) % species
        self._neighbors.fill(0)
        for dy, dx in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            self._neighbors += self._shift(self.state, dy, dx) == successor
        eligible = self.state != self.CAVITY
        advance = eligible & (self._neighbors >= self.params["takeover_threshold"])
        self.state[advance] = successor[advance].astype(np.uint8)
        mutation = self.params["mutation"]
        if mutation:
            changed = (self._rng.random(self.state.shape) < mutation) & (self.state != self.CAVITY)
            self.state[changed] = (self.state[changed] + 1) % species
        if self._grazer_x.size:
            turn = self._rng.random(self._grazer_x.size) < .18
            self._grazer_dx[turn] = self._rng.integers(-1, 2, int(np.count_nonzero(turn)), dtype=np.int8)
            self._grazer_dy[turn] = self._rng.integers(-1, 2, int(np.count_nonzero(turn)), dtype=np.int8)
            stationary = (self._grazer_dx == 0) & (self._grazer_dy == 0)
            self._grazer_dx[stationary] = 1
            if self.params["topology"] == "wrap":
                self._grazer_x = np.mod(self._grazer_x + self._grazer_dx, self.width).astype(np.int16)
                self._grazer_y = np.mod(self._grazer_y + self._grazer_dy, self.height).astype(np.int16)
            else:
                self._grazer_x = np.clip(self._grazer_x + self._grazer_dx, 0, self.width - 1).astype(np.int16)
                self._grazer_y = np.clip(self._grazer_y + self._grazer_dy, 0, self.height - 1).astype(np.int16)
            self.state[self._grazer_y, self._grazer_x] = self.CAVITY
        cavities = np.flatnonzero(self.state == self.CAVITY)
        if cavities.size:
            restore_count = min(cavities.size, max(1, self.params["grazers"] // 3 + 1))
            restore = self._rng.choice(cavities, size=restore_count, replace=False)
            self.state.ravel()[restore] = self._rng.integers(0, species, restore_count, dtype=np.uint8)

    def _paint(self, palette_id: str) -> None:
        background, primary, accent = (np.asarray(value, dtype=np.float32) for value in self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"]))
        self._canvas[:] = background.astype(np.uint8)
        species = self.params["species_count"]
        phases = np.linspace(0.05, .95, species, dtype=np.float32)
        palette = np.empty((species, 3), dtype=np.float32)
        for index, phase in enumerate(phases):
            wave = .5 + .5 * np.sin((phase + np.asarray((0., .33, .67), dtype=np.float32)) * math.tau)
            semantic = primary * (.45 + .28 * phase) + accent * (.18 + .42 * (1. - phase))
            palette[index] = np.clip(semantic * (.70 + .30 * wave), 0., 255.)
        valid = self.state != self.CAVITY
        image = self._canvas.transpose(1, 0, 2)
        image[valid] = palette[self.state[valid]].astype(np.uint8)
        self._boundary.fill(False)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self._boundary |= self._shift(self.state, dy, dx) != self.state
        glow = self.params["boundary_glow"]
        if glow:
            canvas = image.astype(np.float32)
            highlighted = self._boundary & valid
            canvas[highlighted] += (accent - canvas[highlighted]) * (.26 * glow)
            image[:] = np.clip(canvas, 0., 255.).astype(np.uint8)

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        values = {key: value for key, value in values.items() if key not in {"plant_aware", "plant_modifiers"}}
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Cyclic Reef does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        for name, low, high in (("species_count", 3, 8), ("takeover_threshold", 1, 5), ("grazers", 0, cls.MAX_GRAZERS), ("seed", 0, 999999)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        for name, low, high in (("mutation", 0., .02), ("boundary_glow", 0., 1.), ("pace", .25, 2.)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not low <= float(value) <= high:
                raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        if result["topology"] not in cls.TOPOLOGIES:
            raise ValueError(f"topology must be one of {list(cls.TOPOLOGIES)!r}")
        return result

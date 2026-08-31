"""Scene-v1 Conway Life as a transparent, semantic-palette overlay."""

from __future__ import annotations

import copy
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation.core.base import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import OverlayFrame
from animation.core.presentation_contracts import ResolvedScene


_RULE = re.compile(r"^B([0-8]*)/S([0-8]*)$")
_SEMANTIC_PALETTES = MappingProxyType({
    "neutral": MappingProxyType({"alive": (40.0, 220.0, 150.0), "mature": (180.0, 255.0, 220.0)}),
    "mist": MappingProxyType({"alive": (70.0, 165.0, 220.0), "mature": (190.0, 245.0, 255.0)}),
    "spectrum": MappingProxyType({"alive": (130.0, 60.0, 255.0), "mature": (65.0, 245.0, 230.0)}),
    "ember": MappingProxyType({"alive": (255.0, 100.0, 28.0), "mature": (255.0, 225.0, 110.0)}),
})


def _dirty_ranges(previous: np.ndarray, current: np.ndarray) -> tuple[tuple[int, int], ...]:
    changed = np.flatnonzero(np.any(previous != current, axis=1))
    if not changed.size:
        return ()
    starts = changed[np.r_[True, np.diff(changed) != 1]]
    ends = changed[np.r_[np.diff(changed) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


class ConwayLifeAnimation(AnimationBase):
    """Bounded-cadence B/S cellular life in canonical premultiplied RGBA8."""

    ANIMATION_NAME = "Conway Life Overlay"
    ANIMATION_DESCRIPTION = "A seeded Life field composed over a semantic Scene-v1 background"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "3.0"

    COMPONENT_ID = "conway_life"
    COMPONENT_VERSION = 1
    PROVIDER = "python"
    ROLE = "overlay"
    FRAME_FORMAT = "rgba_uint8_premultiplied_strip_major"
    TIMING_POLICY = "scaled_context"
    CADENCE_POLICY = "local_generation_cadence_cached"
    PALETTE_POLICY = "semantic"
    PALETTE_ROLES = ("alive", "mature")
    CAPABILITIES = frozenset({"semantic_palette_roles", "scaled_context", "local_generation_cadence"})
    PLANT_MODIFIER_SUPPORT = frozenset()
    MAX_CATCH_UP_GENERATIONS = 8

    DEFAULTS = MappingProxyType({
        "seed": 1971,
        "rule": "B3/S23",
        "initial_density": 0.14,
        "generations_per_second": 5.0,
        "seed_cells": [],
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        provider=PROVIDER,
        role=ROLE,
        palette_policy=PALETTE_POLICY,
        timing_policy=TIMING_POLICY,
        optional_simulation_inputs=("foliage_density", "globe_proximity", "occlusion"),
        defaults=DEFAULTS,
    )

    def __init__(self, controller: Any, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._grid = np.zeros((self.width, self.height), dtype=bool)
        self._age = np.zeros((self.width, self.height), dtype=np.uint8)
        self._buffers = tuple(np.zeros((self.get_pixel_count(), 4), dtype=np.uint8) for _ in range(2))
        self._last_pixels = self._buffers[0]
        self._last_render_key: tuple[Any, ...] | None = None
        self._revision = 0
        self._generation = 0
        self._last_generation_tick: int | None = None
        self._presentation_context: ResolvedScene | None = None
        self._active_seed = int(self.params["seed"])
        self._active_rule = str(self.params["rule"])
        self._rng = np.random.default_rng(self._active_seed)
        self._reset_world()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def palette_roles(cls, palette_id: str) -> Mapping[str, tuple[float, float, float]]:
        return _SEMANTIC_PALETTES.get(str(palette_id), _SEMANTIC_PALETTES["neutral"])

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1971, "description": "Deterministic initial-world seed"},
            "rule": {"type": "string", "options": ["B3/S23", "B36/S23"], "default": "B3/S23", "description": "Life birth/survival rule"},
            "initial_density": {"type": "float", "min": 0.0, "max": 0.4, "default": 0.14, "description": "Seeded live-cell density"},
            "generations_per_second": {"type": "float", "min": 0.5, "max": 20.0, "default": 5.0, "description": "Local scaled-time generation cadence"},
            "seed_cells": {"type": "cells", "default": [], "description": "Optional deterministic [strip, led] live cells"},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        unknown = set(new_params) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(f"Conway Life does not accept non-local parameters: {sorted(unknown)!r}")
        self._apply_parameters(self._normalized_parameters({**self.params, **dict(new_params)}))

    def on_presentation_context_changed(self, old: Optional[ResolvedScene], new: ResolvedScene) -> None:
        del old
        self._validate_context(new)
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del frame_count
        if self._presentation_context is None:
            phase_time, palette_id, parameters = max(0.0, float(time_elapsed)), "neutral", self.params
        else:
            context = self._presentation_context
            phase_time, palette_id, parameters = context.phase_time, self._palette_id(context), context.parameters
        self._apply_parameters(self._normalized_parameters(parameters))
        tick = int(max(0.0, float(phase_time)) * float(self.params["generations_per_second"]) + 1.0e-7)
        if self._last_generation_tick is None:
            self._last_generation_tick = tick
        else:
            target = min(tick, self._last_generation_tick + self.MAX_CATCH_UP_GENERATIONS)
            while self._last_generation_tick < target:
                self._step()
                self._last_generation_tick += 1
        key = (self._generation, palette_id, self._active_seed, self._active_rule)
        if key == self._last_render_key:
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False)

        output = self._buffers[1] if self._last_pixels is self._buffers[0] else self._buffers[0]
        self._paint(output, self.palette_roles(palette_id))
        ranges = _dirty_ranges(self._last_pixels, output)
        self._last_pixels = output
        self._last_render_key = key
        self._revision += 1
        return OverlayFrame(output, revision=self._revision, changed=True, dirty_ranges=ranges)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "seed": self._active_seed,
            "rule": self._active_rule,
            "generation": self._generation,
            "generation_tick": self._last_generation_tick,
            "grid": self._grid.tobytes(),
            "age": self._age.tobytes(),
            "rng_state": copy.deepcopy(self._rng.bit_generator.state),
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"generations_per_second": self.params["generations_per_second"], "generation_tick": self._last_generation_tick})

    def get_runtime_stats(self) -> dict[str, Any]:
        return {"provider": self.PROVIDER, "role": self.ROLE, "generation": self._generation, "generation_tick": self._last_generation_tick, "alive_cells": int(np.count_nonzero(self._grid))}

    def _apply_parameters(self, parameters: Mapping[str, Any]) -> None:
        semantic = self._semantic_key(parameters)
        if semantic != self._semantic_key(self.params):
            self.params = dict(parameters)
            self._active_seed, self._active_rule = int(parameters["seed"]), str(parameters["rule"])
            self._reset_world()
        elif self.params != parameters:
            self.params = dict(parameters)
            self._last_render_key = None

    def _reset_world(self) -> None:
        self._rng = np.random.default_rng(self._active_seed)
        self._grid.fill(False)
        self._age.fill(0)
        if self.params["seed_cells"]:
            for strip, led in self.params["seed_cells"]:
                if strip >= self.width or led >= self.height:
                    raise ValueError("seed_cells coordinates must fit the controller geometry")
                self._grid[strip, led] = True
                self._age[strip, led] = 1
        else:
            self._grid[:] = self._rng.random(self._grid.shape) < float(self.params["initial_density"])
            self._age[self._grid] = 1
        self._generation = 0
        self._last_generation_tick = None
        self._last_render_key = None

    def _step(self) -> None:
        grid = self._grid
        neighbors = sum(np.roll(np.roll(grid, sx, axis=0), sy, axis=1) for sx, sy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)))
        births, survivors = self._rule_sets(self._active_rule)
        born = ~grid & np.isin(neighbors, tuple(births))
        survived = grid & np.isin(neighbors, tuple(survivors))
        self._grid[:] = born | survived
        self._age[~self._grid] = 0
        self._age[survived] = np.minimum(self._age[survived].astype(np.uint16) + 1, 255).astype(np.uint8)
        self._age[born] = 1
        self._generation += 1

    def _paint(self, output: np.ndarray, roles: Mapping[str, tuple[float, float, float]]) -> None:
        visual = output.reshape(self.width, self.height, 4)
        visual.fill(0)
        live = self._grid
        if not np.any(live):
            return
        age = np.minimum(self._age.astype(np.float32) / 12.0, 1.0)[..., None]
        alive = np.asarray(roles["alive"], dtype=np.float32)
        mature = np.asarray(roles["mature"], dtype=np.float32)
        alpha = 220
        colors = alive + (mature - alive) * age
        visual[..., :3] = np.rint(colors * alpha / 255.0).astype(np.uint8)
        visual[..., 3] = np.where(live, alpha, 0).astype(np.uint8)
        visual[~live, :3] = 0

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Conway Life does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        seed = result["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        rule = result["rule"]
        if not isinstance(rule, str) or _RULE.fullmatch(rule) is None:
            raise ValueError("rule must use B.../S... digits from 0 to 8")
        result["rule"] = rule
        for name, minimum, maximum in (("initial_density", 0.0, 0.4), ("generations_per_second", 0.5, 20.0)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
                raise ValueError(f"{name} must be a number from {minimum} to {maximum}")
            result[name] = float(value)
        cells = result["seed_cells"]
        if not isinstance(cells, (list, tuple)):
            raise ValueError("seed_cells must be a list of [strip, led] pairs")
        normalized_cells: list[tuple[int, int]] = []
        for cell in cells:
            if not isinstance(cell, (list, tuple)) or len(cell) != 2 or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in cell):
                raise ValueError("seed_cells must be a list of non-negative [strip, led] pairs")
            normalized_cells.append((int(cell[0]), int(cell[1])))
        result["seed_cells"] = tuple(normalized_cells)
        return result

    @staticmethod
    def _semantic_key(parameters: Mapping[str, Any]) -> tuple[Any, ...]:
        return (parameters["seed"], parameters["rule"], parameters["initial_density"], tuple(parameters["seed_cells"]))

    @staticmethod
    def _rule_sets(rule: str) -> tuple[set[int], set[int]]:
        match = _RULE.fullmatch(rule)
        assert match is not None
        return ({int(value) for value in match.group(1)}, {int(value) for value in match.group(2)})

    def _validate_context(self, context: ResolvedScene) -> None:
        descriptor = context.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Conway Life received a context for another component")
        if context.palette is None:
            raise ValueError("Conway Life requires a semantic palette context")

    @staticmethod
    def _palette_id(context: ResolvedScene) -> str:
        assert context.palette is not None
        return str(context.palette["palette_id"])


__all__ = ["ConwayLifeAnimation"]

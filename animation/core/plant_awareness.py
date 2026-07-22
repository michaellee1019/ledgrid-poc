"""Shared calibrated-mask support for opt-in plant-aware animations.

The physical wall uses ``index = strip * leds_per_strip + led``.  This module
keeps the two semantic layers separate: foliage is soft/occluding while the
seven rooting globes are solid landmarks.  Plugins decide what those layers
mean for their own simulation; this module only owns loading and geometry.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import numpy as np

from animation.libraries.mask_effects import dilate_8, indices_from_payload, logical_mask


DEFAULT_FOLIAGE_MASK = "config/plant_pixel_map_32x138.json"
DEFAULT_GLOBE_MASK = "config/plant_globe_map_32x138.json"

PLANT_MODIFIER_IDS = (
    "illuminate", "shadow", "refract", "attractor", "repulsor", "slow_zone",
    "obstacle", "portal", "bumper", "hazard", "habitat", "emitter",
)
FIELD_MODIFIERS = frozenset(("attractor", "repulsor", "slow_zone"))
SURFACE_MODIFIERS = frozenset(("obstacle", "portal", "bumper", "hazard", "habitat"))
GLOBE_REGION_ORDER = (
    "top_left", "top_right", "upper_middle", "middle_left", "middle_right",
    "lower_left", "lower_right",
)


@dataclass(frozen=True)
class PlantModifierState:
    """Validated, deterministic manager-global plant semantics."""

    version: int = 1
    active: Tuple[str, ...] = ()
    strengths: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "PlantModifierState":
        return cls()

    @classmethod
    def from_legacy(cls, enabled: bool) -> "PlantModifierState":
        if not isinstance(enabled, bool):
            raise ValueError("plant_aware must be boolean")
        return cls.from_payload({
            "active": ["illuminate", "obstacle"] if enabled else [],
            "strengths": {"illuminate": 0.5, "obstacle": 1.0} if enabled else {},
        })

    @classmethod
    def from_payload(cls, payload: Any) -> "PlantModifierState":
        if isinstance(payload, cls):
            return payload
        if payload is None:
            return cls.empty()
        if not isinstance(payload, Mapping):
            raise ValueError("plant_modifiers must be an object")
        if payload.get("version", 1) != 1:
            raise ValueError("plant_modifiers.version must be 1")
        raw_active = payload.get("active", ())
        if not isinstance(raw_active, (list, tuple)) or any(not isinstance(x, str) for x in raw_active):
            raise ValueError("plant_modifiers.active must be an array of IDs")
        if len(set(raw_active)) != len(raw_active):
            raise ValueError("plant_modifiers.active contains duplicate IDs")
        unknown = set(raw_active) - set(PLANT_MODIFIER_IDS)
        if unknown:
            raise ValueError(f"unknown plant modifier: {sorted(unknown)[0]}")
        if len(set(raw_active) & FIELD_MODIFIERS) > 1:
            raise ValueError("at most one plant field modifier may be active")
        if len(set(raw_active) & SURFACE_MODIFIERS) > 1:
            raise ValueError("at most one plant surface modifier may be active")
        raw_strengths = payload.get("strengths", {})
        if not isinstance(raw_strengths, Mapping):
            raise ValueError("plant_modifiers.strengths must be an object")
        unknown_strengths = set(raw_strengths) - set(PLANT_MODIFIER_IDS)
        if unknown_strengths:
            raise ValueError(f"unknown plant modifier strength: {sorted(unknown_strengths)[0]}")
        strengths: Dict[str, float] = {}
        for modifier in raw_active:
            value = raw_strengths.get(modifier, 1.0 if modifier == "obstacle" else 0.5)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"strength for {modifier} must be numeric")
            value = float(value)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"strength for {modifier} must be finite and between 0 and 1")
            strengths[modifier] = value
        active = tuple(modifier for modifier in PLANT_MODIFIER_IDS if modifier in raw_active)
        return cls(active=active, strengths=strengths)

    def enabled(self, modifier: str, supported: Iterable[str] = PLANT_MODIFIER_IDS) -> bool:
        return modifier in self.active and modifier in supported

    def strength(self, modifier: str, supported: Iterable[str] = PLANT_MODIFIER_IDS) -> float:
        return self.strengths.get(modifier, 0.0) if self.enabled(modifier, supported) else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"version": 1, "active": list(self.active),
                "strengths": {key: self.strengths[key] for key in self.active}}


@dataclass(frozen=True)
class PlantMaskGeometry:
    """Logical and flat views of the calibrated semantic mask layers."""

    foliage: np.ndarray
    globes: np.ndarray
    obstacle: np.ndarray
    clearance: np.ndarray
    foliage_flat: np.ndarray
    globes_flat: np.ndarray
    obstacle_flat: np.ndarray
    clearance_flat: np.ndarray
    foliage_count: int
    globe_count: int
    globe_regions: int
    foliage_edge: np.ndarray
    globe_edge: np.ndarray
    obstacle_edge: np.ndarray
    distance: np.ndarray
    normal_x: np.ndarray
    normal_y: np.ndarray
    globe_region_masks: Mapping[str, np.ndarray]
    error: str = ""

    @property
    def safe(self) -> np.ndarray:
        return ~self.clearance

    @property
    def safe_flat(self) -> np.ndarray:
        return ~self.clearance_flat


def plant_parameter_schema() -> Mapping[str, Mapping[str, Any]]:
    """Common opt-in controls exposed by every animation plugin."""

    return {
        "plant_aware": {
            "type": "bool",
            "default": False,
            "description": "Use calibrated foliage and globe geometry in this animation",
        },
        "plant_modifiers": {
            "type": "object",
            "default": PlantModifierState.empty().to_dict(),
            "description": "Manager-global composable plant modifier state",
        },
        "plant_clearance": {
            "type": "int",
            "min": 0,
            "max": 4,
            "default": 1,
            "description": "Logical pixels kept clear around calibrated plant masks",
        },
        "plant_mask_path": {
            "type": "str",
            "default": DEFAULT_FOLIAGE_MASK,
            "description": "Path to the calibrated foliage mask JSON",
        },
        "plant_globe_mask_path": {
            "type": "str",
            "default": DEFAULT_GLOBE_MASK,
            "description": "Path to the calibrated globe mask JSON",
        },
    }


def _inner_edge(mask: np.ndarray) -> np.ndarray:
    """Return a non-wrapping four-neighbor inner edge."""
    padded = np.pad(mask, 1, constant_values=False)
    interior = (
        padded[:-2, 1:-1] & padded[2:, 1:-1]
        & padded[1:-1, :-2] & padded[1:-1, 2:]
    )
    return mask & ~interior


def _distance_and_normals(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    distance = np.zeros(mask.shape, dtype=np.float32)
    if not np.any(mask):
        distance.fill(float(max(mask.shape)))
    else:
        reached = mask.copy()
        frontier = mask.copy()
        for step in range(1, max(mask.shape) + 1):
            grown = dilate_8(frontier)
            ring = grown & ~reached
            if not np.any(ring):
                break
            distance[ring] = float(step)
            reached |= grown
            frontier = grown
    normal_x = np.zeros_like(distance)
    normal_y = np.zeros_like(distance)
    if distance.shape[0] > 1:
        normal_x[:] = np.gradient(distance, axis=0)
    if distance.shape[1] > 1:
        normal_y[:] = np.gradient(distance, axis=1)
    magnitude = np.hypot(normal_x, normal_y)
    np.divide(normal_x, magnitude, out=normal_x, where=magnitude > 0)
    np.divide(normal_y, magnitude, out=normal_y, where=magnitude > 0)
    return distance, normal_x, normal_y


class PlantMaskCache:
    """Lazy per-animation cache of mask files and dilated clearance geometry."""

    def __init__(self, owner: Any):
        self.owner = owner
        self._key: Optional[Tuple[Any, ...]] = None
        self._geometry: Optional[PlantMaskGeometry] = None

    @staticmethod
    def _resolve_path(configured_path: str) -> Path:
        path = Path(configured_path)
        if path.is_absolute():
            return path
        return (Path(__file__).resolve().parents[2] / path).resolve()

    @staticmethod
    def _read(path: Path) -> Tuple[Mapping[str, Any], str]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("root must be an object")
            return payload, ""
        except Exception as exc:
            return {}, f"Failed to read {path}: {exc}"

    def invalidate(self) -> None:
        self._key = None
        self._geometry = None

    def get(self, clearance: Optional[int] = None) -> PlantMaskGeometry:
        strip_count, leds_per_strip = self.owner.get_strip_info()
        total_leds = self.owner.get_pixel_count()
        radius = max(
            0,
            int(
                self.owner.params.get("plant_clearance", 1)
                if clearance is None
                else clearance
            ),
        )
        foliage_path = self._resolve_path(
            str(self.owner.params.get("plant_mask_path", DEFAULT_FOLIAGE_MASK))
        )
        globe_path = self._resolve_path(
            str(self.owner.params.get("plant_globe_mask_path", DEFAULT_GLOBE_MASK))
        )
        key = (strip_count, leds_per_strip, radius, foliage_path, globe_path)
        if key == self._key and self._geometry is not None:
            return self._geometry

        foliage_payload, foliage_error = self._read(foliage_path)
        globe_payload, globe_error = self._read(globe_path)
        error = "; ".join(item for item in (foliage_error, globe_error) if item)
        if error:
            # Never half-apply one semantic layer when its companion calibration
            # is unavailable: every modifier sees the same deterministic empty wall.
            foliage_payload = {}
            globe_payload = {}
        foliage_indices = indices_from_payload(
            foliage_payload, total_leds, ("covered_indices",)
        )
        globe_indices = indices_from_payload(
            globe_payload, total_leds, ("globe_indices", "covered_indices")
        )
        # Globes are the higher-priority semantic layer if malformed inputs overlap.
        foliage_indices -= globe_indices
        foliage = logical_mask(foliage_indices, strip_count, leds_per_strip)
        globes = logical_mask(globe_indices, strip_count, leds_per_strip)
        obstacle = foliage | globes
        expanded = obstacle.copy()
        for _ in range(radius):
            expanded = dilate_8(expanded)

        distance, normal_x, normal_y = _distance_and_normals(obstacle)
        region_masks: Dict[str, np.ndarray] = {}
        pixels = globe_payload.get("pixels", ())
        if isinstance(pixels, list):
            region_indices: Dict[str, set] = {name: set() for name in GLOBE_REGION_ORDER}
            for pixel in pixels:
                if isinstance(pixel, Mapping) and pixel.get("region") in region_indices:
                    try:
                        index = int(pixel["index"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if 0 <= index < total_leds:
                        region_indices[str(pixel["region"])].add(index)
            for name in GLOBE_REGION_ORDER:
                if region_indices[name]:
                    region_masks[name] = logical_mask(
                        region_indices[name], strip_count, leds_per_strip
                    ) & globes

        self._geometry = PlantMaskGeometry(
            foliage=foliage,
            globes=globes,
            obstacle=obstacle,
            clearance=expanded,
            foliage_flat=foliage.ravel(),
            globes_flat=globes.ravel(),
            obstacle_flat=obstacle.ravel(),
            clearance_flat=expanded.ravel(),
            foliage_count=int(np.count_nonzero(foliage)),
            globe_count=int(np.count_nonzero(globes)),
            globe_regions=int(globe_payload.get("region_count", 0) or 0),
            foliage_edge=_inner_edge(foliage),
            globe_edge=_inner_edge(globes),
            obstacle_edge=_inner_edge(obstacle),
            distance=distance,
            normal_x=normal_x,
            normal_y=normal_y,
            globe_region_masks=region_masks,
            error=error,
        )
        self._key = key
        return self._geometry

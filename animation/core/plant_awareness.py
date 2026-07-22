"""Shared calibrated-mask support for opt-in plant-aware animations.

The physical wall uses ``index = strip * leds_per_strip + led``.  This module
keeps the two semantic layers separate: foliage is soft/occluding while the
seven rooting globes are solid landmarks.  Plugins decide what those layers
mean for their own simulation; this module only owns loading and geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

import numpy as np

from animation.libraries.mask_effects import dilate_8, indices_from_payload, logical_mask


DEFAULT_FOLIAGE_MASK = "config/plant_pixel_map_32x138.json"
DEFAULT_GLOBE_MASK = "config/plant_globe_map_32x138.json"


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
            error="; ".join(error for error in (foliage_error, globe_error) if error),
        )
        self._key = key
        return self._geometry

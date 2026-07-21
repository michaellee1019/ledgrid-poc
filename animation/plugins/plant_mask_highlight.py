#!/usr/bin/env python3
"""
Plant Mask Highlight Animation Plugin

Loads the live-verified foliage and globe masks and illuminates each layer.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
import numpy as np

from animation import AnimationBase
from animation.core.mask_effects import logical_mask, mask_boundary


class PlantMaskHighlightAnimation(AnimationBase):
    """Highlight plant-covered pixels from the calibration mask."""

    ANIMATION_NAME = "Plant Mask Highlight"
    ANIMATION_DESCRIPTION = "Illuminates pixels currently mapped as covered by plants"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update(
            {
                "background_red": 0,
                "background_green": 6,
                "background_blue": 18,
                "plant_red": 255,
                "plant_green": 64,
                "plant_blue": 24,
                "globe_red": 255,
                "globe_green": 0,
                "globe_blue": 220,
                "pulse_speed": 1.0,
                "pulse_depth": 0.20,
                "show_foliage": True,
                "globe_region": "all",
                "globe_outline_only": False,
                "globe_center_marker": False,
                "mask_path": "config/plant_pixel_map_32x138.json",
                "globe_mask_path": "config/plant_globe_map_32x138.json",
            }
        )
        self.params = {**self.default_params, **self.config}

        self.mask_indices: Set[int] = set()
        self.globe_indices: Set[int] = set()
        self.globe_region_indices: Dict[str, Set[int]] = {}
        self.globe_region_centers: Dict[str, Set[int]] = {}
        self.globe_region_count = 0
        self.mask_load_error = ""
        self.globe_mask_load_error = ""
        self._load_mask()
        self._load_globe_mask()

    def _load_mask(self):
        self.mask_indices = set()
        self.mask_load_error = ""

        total_leds = self.get_pixel_count()
        mask_path = self.params.get("mask_path", "config/plant_pixel_map_32x138.json")
        resolved = self._resolve_mask_path(str(mask_path))

        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            self.mask_load_error = f"Failed to read mask file {resolved}: {exc}"
            print(f"Warning: {self.mask_load_error}")
            return

        indices = payload.get("covered_indices")
        if isinstance(indices, list):
            for idx in indices:
                try:
                    i = int(idx)
                except Exception:
                    continue
                if 0 <= i < total_leds:
                    self.mask_indices.add(i)
            print(f"Plant mask loaded: {len(self.mask_indices)} covered pixels from {resolved}")
            return

        pixels = payload.get("covered_pixels")
        if isinstance(pixels, list):
            leds_per_strip = self.controller.leds_per_strip
            strip_count = self.controller.strip_count
            for px in pixels:
                if not isinstance(px, dict):
                    continue
                try:
                    strip = int(px.get("strip", -1))
                    led = int(px.get("led", -1))
                except Exception:
                    continue
                if 0 <= strip < strip_count and 0 <= led < leds_per_strip:
                    self.mask_indices.add(strip * leds_per_strip + led)
            print(f"Plant mask loaded: {len(self.mask_indices)} covered pixels from {resolved}")
            return

        self.mask_load_error = f"No covered_indices or covered_pixels in {resolved}"
        print(f"Warning: {self.mask_load_error}")

    def _load_globe_mask(self):
        self.globe_indices = set()
        self.globe_region_indices = {}
        self.globe_region_centers = {}
        self.globe_region_count = 0
        self.globe_mask_load_error = ""

        total_leds = self.get_pixel_count()
        configured = self.params.get(
            "globe_mask_path", "config/plant_globe_map_32x138.json"
        )
        resolved = self._resolve_mask_path(str(configured))
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            self.globe_mask_load_error = f"Failed to read globe mask file {resolved}: {exc}"
            print(f"Warning: {self.globe_mask_load_error}")
            return

        self.globe_region_count = int(payload.get("region_count", 0))
        indices = payload.get("globe_indices", payload.get("covered_indices"))
        if not isinstance(indices, list):
            self.globe_mask_load_error = f"No globe_indices in {resolved}"
            print(f"Warning: {self.globe_mask_load_error}")
            return
        for idx in indices:
            try:
                index = int(idx)
            except Exception:
                continue
            if 0 <= index < total_leds:
                self.globe_indices.add(index)
        for pixel in payload.get("pixels", []):
            try:
                index = int(pixel["index"])
                region = str(pixel["region"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < total_leds:
                self.globe_region_indices.setdefault(region, set()).add(index)
        strips = self.controller.strip_count
        leds = self.controller.leds_per_strip
        for region in payload.get("regions", []):
            try:
                region_id = str(region["id"])
                strip_start = int(region["strip_start"])
                led_start = int(region["led_start"])
            except (KeyError, TypeError, ValueError):
                continue
            centers = {
                strip * leds + led
                for strip in (strip_start + 3, strip_start + 4)
                for led in (led_start + 3, led_start + 4)
                if 0 <= strip < strips and 0 <= led < leds
            }
            self.globe_region_centers[region_id] = centers
        print(
            f"Globe mask loaded: {len(self.globe_indices)} pixels across "
            f"{self.globe_region_count} regions from {resolved}"
        )

    def _resolve_mask_path(self, configured_path: str) -> Path:
        p = Path(configured_path)
        if p.is_absolute():
            return p
        repo_root = Path(__file__).resolve().parents[2]
        return (repo_root / p).resolve()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update(
            {
                "background_red": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 0,
                    "description": "Background red channel",
                },
                "background_green": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 6,
                    "description": "Background green channel",
                },
                "background_blue": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 18,
                    "description": "Background blue channel",
                },
                "plant_red": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 255,
                    "description": "Plant mask red channel",
                },
                "plant_green": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 64,
                    "description": "Plant mask green channel",
                },
                "plant_blue": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 24,
                    "description": "Plant mask blue channel",
                },
                "globe_red": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 255,
                    "description": "Globe layer red channel",
                },
                "globe_green": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 0,
                    "description": "Globe layer green channel",
                },
                "globe_blue": {
                    "type": "int",
                    "min": 0,
                    "max": 255,
                    "default": 220,
                    "description": "Globe layer blue channel",
                },
                "pulse_speed": {
                    "type": "float",
                    "min": 0.0,
                    "max": 5.0,
                    "default": 1.0,
                    "description": "Mask pulse speed",
                },
                "pulse_depth": {
                    "type": "float",
                    "min": 0.0,
                    "max": 0.95,
                    "default": 0.20,
                    "description": "Mask pulse depth",
                },
                "show_foliage": {
                    "type": "bool",
                    "default": True,
                    "description": "Show the foliage layer",
                },
                "globe_region": {
                    "type": "str",
                    "default": "all",
                    "options": ["all", *sorted(self.globe_region_indices)],
                    "description": "Show all globes or one stable calibration region",
                },
                "globe_outline_only": {
                    "type": "bool",
                    "default": False,
                    "description": "Show only the logical boundary of each selected globe",
                },
                "globe_center_marker": {
                    "type": "bool",
                    "default": False,
                    "description": "Add the selected globe's central 2x2 marker",
                },
                "mask_path": {
                    "type": "str",
                    "default": "config/plant_pixel_map_32x138.json",
                    "description": "Path to plant mask JSON",
                },
                "globe_mask_path": {
                    "type": "str",
                    "default": "config/plant_globe_map_32x138.json",
                    "description": "Path to seven-vessel globe mask JSON",
                },
            }
        )
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        old_path = str(self.params.get("mask_path", "config/plant_pixel_map_32x138.json"))
        old_globe_path = str(
            self.params.get("globe_mask_path", "config/plant_globe_map_32x138.json")
        )
        super().update_parameters(new_params)
        new_path = str(self.params.get("mask_path", "config/plant_pixel_map_32x138.json"))
        new_globe_path = str(
            self.params.get("globe_mask_path", "config/plant_globe_map_32x138.json")
        )
        if new_path != old_path:
            self._load_mask()
        if new_globe_path != old_globe_path:
            self._load_globe_mask()

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        total_leds = self.get_pixel_count()

        bg = (
            int(self.params.get("background_red", 0)),
            int(self.params.get("background_green", 6)),
            int(self.params.get("background_blue", 18)),
        )
        plant = (
            int(self.params.get("plant_red", 255)),
            int(self.params.get("plant_green", 64)),
            int(self.params.get("plant_blue", 24)),
        )
        globe = (
            int(self.params.get("globe_red", 255)),
            int(self.params.get("globe_green", 0)),
            int(self.params.get("globe_blue", 220)),
        )

        pulse_speed = float(self.params.get("pulse_speed", 1.0))
        pulse_depth = float(self.params.get("pulse_depth", 0.20))
        pulse_depth = max(0.0, min(0.95, pulse_depth))

        pulse = 1.0
        if pulse_speed > 0.0:
            pulse = 1.0 - pulse_depth + pulse_depth * ((math.sin(time_elapsed * pulse_speed * 2.0 * math.pi) + 1.0) / 2.0)

        frame = self.next_frame_buffer(clear=False)
        frame[:] = bg
        if self.mask_indices and bool(self.params.get("show_foliage", True)):
            indices = np.fromiter(
                (idx for idx in self.mask_indices if 0 <= idx < total_leds),
                dtype=np.intp,
            )
            frame[indices] = (
                int(plant[0] * pulse),
                int(plant[1] * pulse),
                int(plant[2] * pulse),
            )
        selected_region = str(self.params.get("globe_region", "all"))
        selected_globes = (
            self.globe_indices
            if selected_region == "all"
            else self.globe_region_indices.get(selected_region, set())
        )
        if bool(self.params.get("globe_outline_only", False)) and selected_globes:
            mask = logical_mask(
                selected_globes,
                self.controller.strip_count,
                self.controller.leds_per_strip,
            )
            selected_globes = set(np.flatnonzero(mask_boundary(mask).ravel()).tolist())
        if bool(self.params.get("globe_center_marker", False)):
            if selected_region == "all":
                centers = set().union(*self.globe_region_centers.values()) if self.globe_region_centers else set()
            else:
                centers = self.globe_region_centers.get(selected_region, set())
            selected_globes = selected_globes | centers
        if selected_globes:
            globe_indices = np.fromiter(selected_globes, dtype=np.intp)
            frame[globe_indices] = globe

        return self.apply_brightness_array(frame, out=frame)

    def get_runtime_stats(self) -> Dict[str, Any]:
        stats = {
            "covered_pixels": len(self.mask_indices),
            "foliage_pixels": len(self.mask_indices - self.globe_indices),
            "globe_pixels": len(self.globe_indices),
            "globe_regions": self.globe_region_count,
            "selected_globe_region": str(self.params.get("globe_region", "all")),
            "mask_path": str(
                self.params.get("mask_path", "config/plant_pixel_map_32x138.json")
            ),
            "globe_mask_path": str(
                self.params.get(
                    "globe_mask_path", "config/plant_globe_map_32x138.json"
                )
            ),
        }
        if self.mask_load_error:
            stats["mask_load_error"] = self.mask_load_error
        if self.globe_mask_load_error:
            stats["globe_mask_load_error"] = self.globe_mask_load_error
        return stats

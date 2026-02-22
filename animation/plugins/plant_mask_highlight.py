#!/usr/bin/env python3
"""
Plant Mask Highlight Animation Plugin

Loads plant-covered pixels from config/plant_pixel_map.json and illuminates them.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from animation import AnimationBase


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
                "pulse_speed": 1.0,
                "pulse_depth": 0.20,
                "mask_path": "config/plant_pixel_map.json",
            }
        )
        self.params = {**self.default_params, **self.config}

        self.mask_indices: Set[int] = set()
        self.mask_load_error = ""
        self._load_mask()

    def _load_mask(self):
        self.mask_indices = set()
        self.mask_load_error = ""

        total_leds = self.get_pixel_count()
        mask_path = self.params.get("mask_path", "config/plant_pixel_map.json")
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
                "mask_path": {
                    "type": "str",
                    "default": "config/plant_pixel_map.json",
                    "description": "Path to plant mask JSON",
                },
            }
        )
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        old_path = str(self.params.get("mask_path", "config/plant_pixel_map.json"))
        super().update_parameters(new_params)
        new_path = str(self.params.get("mask_path", "config/plant_pixel_map.json"))
        if new_path != old_path:
            self._load_mask()

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

        pulse_speed = float(self.params.get("pulse_speed", 1.0))
        pulse_depth = float(self.params.get("pulse_depth", 0.20))
        pulse_depth = max(0.0, min(0.95, pulse_depth))

        pulse = 1.0
        if pulse_speed > 0.0:
            pulse = 1.0 - pulse_depth + pulse_depth * ((math.sin(time_elapsed * pulse_speed * 2.0 * math.pi) + 1.0) / 2.0)

        frame: List[Tuple[int, int, int]] = [bg] * total_leds
        if self.mask_indices:
            for idx in self.mask_indices:
                if 0 <= idx < total_leds:
                    frame[idx] = (
                        int(plant[0] * pulse),
                        int(plant[1] * pulse),
                        int(plant[2] * pulse),
                    )

        return [self.apply_brightness(c) for c in frame]

    def get_runtime_stats(self) -> Dict[str, Any]:
        stats = {
            "covered_pixels": len(self.mask_indices),
            "mask_path": str(self.params.get("mask_path", "config/plant_pixel_map.json")),
        }
        if self.mask_load_error:
            stats["mask_load_error"] = self.mask_load_error
        return stats

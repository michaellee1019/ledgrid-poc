#!/usr/bin/env python3
"""Ethereal, mask-driven glow for the living plant wall."""

import json
import math
from pathlib import Path
from typing import Any, Dict, Set

import numpy as np

from animation import AnimationBase
from animation.core.mask_effects import build_halo_weights, indices_from_payload


class PlantGlowAnimation(AnimationBase):
    """Render distinct foliage and globe cores with soft logical-pixel halos."""

    ANIMATION_NAME = "Plant Glow"
    ANIMATION_DESCRIPTION = "Breathing foliage and globe masks with an ethereal edge glow"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.default_params.update(
            {
                "brightness": 0.24,
                "background_red": 0,
                "background_green": 0,
                "background_blue": 3,
                "foliage_red": 54,
                "foliage_green": 255,
                "foliage_blue": 132,
                "foliage_halo_red": 18,
                "foliage_halo_green": 110,
                "foliage_halo_blue": 255,
                "globe_red": 255,
                "globe_green": 72,
                "globe_blue": 224,
                "globe_halo_red": 108,
                "globe_halo_green": 34,
                "globe_halo_blue": 255,
                "glow_radius": 2,
                "glow_strength": 0.72,
                "glow_falloff": 1.4,
                "breath_speed": 0.18,
                "breath_depth": 0.24,
                "shimmer": 0.10,
                "mask_path": "config/plant_pixel_map_32x138.json",
                "globe_mask_path": "config/plant_globe_map_32x138.json",
            }
        )
        self.params = {**self.default_params, **self.config}
        self.foliage_indices: Set[int] = set()
        self.globe_indices: Set[int] = set()
        self.globe_region_count = 0
        self.mask_load_error = ""
        self.globe_mask_load_error = ""
        self._linear_frame = np.zeros((self.get_pixel_count(), 3), dtype=np.float32)
        self._phase = (
            np.arange(self.get_pixel_count(), dtype=np.float32) * np.float32(2.3999632)
        ) % np.float32(2.0 * math.pi)
        self._foliage_core = np.zeros(self.get_pixel_count(), dtype=bool)
        self._globe_core = np.zeros(self.get_pixel_count(), dtype=bool)
        self._foliage_halo = np.zeros(self.get_pixel_count(), dtype=np.float32)
        self._globe_halo = np.zeros(self.get_pixel_count(), dtype=np.float32)
        self._load_masks()

    @staticmethod
    def _resolve_mask_path(configured_path: str) -> Path:
        path = Path(configured_path)
        if path.is_absolute():
            return path
        return (Path(__file__).resolve().parents[2] / path).resolve()

    def _read_mask(self, parameter: str, keys, error_attribute: str):
        configured = str(self.params[parameter])
        path = self._resolve_mask_path(configured)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            setattr(self, error_attribute, f"Failed to read {path}: {exc}")
            return {}, set()
        setattr(self, error_attribute, "")
        return payload, indices_from_payload(payload, self.get_pixel_count(), keys)

    def _load_masks(self):
        _, self.foliage_indices = self._read_mask(
            "mask_path", ("covered_indices",), "mask_load_error"
        )
        globe_payload, self.globe_indices = self._read_mask(
            "globe_mask_path", ("globe_indices", "covered_indices"), "globe_mask_load_error"
        )
        self.globe_region_count = int(globe_payload.get("region_count", 0))
        # Globes are a higher-priority semantic layer if a malformed calibration overlaps.
        self.foliage_indices -= self.globe_indices
        self._rebuild_geometry()

    def _rebuild_geometry(self):
        strip_count, leds_per_strip = self.get_strip_info()
        radius = int(self.params.get("glow_radius", 2))
        falloff = float(self.params.get("glow_falloff", 1.4))
        self._foliage_core, self._foliage_halo = build_halo_weights(
            self.foliage_indices, strip_count, leds_per_strip, radius, falloff
        )
        self._globe_core, self._globe_halo = build_halo_weights(
            self.globe_indices, strip_count, leds_per_strip, radius, falloff
        )

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        colors = {
            "background_red": (0, "Background red"),
            "background_green": (0, "Background green"),
            "background_blue": (3, "Background blue"),
            "foliage_red": (54, "Foliage core red"),
            "foliage_green": (255, "Foliage core green"),
            "foliage_blue": (132, "Foliage core blue"),
            "foliage_halo_red": (18, "Foliage halo red"),
            "foliage_halo_green": (110, "Foliage halo green"),
            "foliage_halo_blue": (255, "Foliage halo blue"),
            "globe_red": (255, "Globe core red"),
            "globe_green": (72, "Globe core green"),
            "globe_blue": (224, "Globe core blue"),
            "globe_halo_red": (108, "Globe halo red"),
            "globe_halo_green": (34, "Globe halo green"),
            "globe_halo_blue": (255, "Globe halo blue"),
        }
        for name, (default, description) in colors.items():
            schema[name] = {
                "type": "int", "min": 0, "max": 255,
                "default": default, "description": description,
            }
        schema.update(
            {
                "glow_radius": {
                    "type": "int", "min": 0, "max": 5, "default": 2,
                    "description": "Exterior halo radius in logical pixels",
                },
                "glow_strength": {
                    "type": "float", "min": 0.0, "max": 2.0, "default": 0.72,
                    "description": "Halo intensity relative to the mask core",
                },
                "glow_falloff": {
                    "type": "float", "min": 0.1, "max": 4.0, "default": 1.4,
                    "description": "Halo decay exponent",
                },
                "breath_speed": {
                    "type": "float", "min": 0.0, "max": 2.0, "default": 0.18,
                    "description": "Slow breathing cycles per second",
                },
                "breath_depth": {
                    "type": "float", "min": 0.0, "max": 0.8, "default": 0.24,
                    "description": "Breathing modulation depth",
                },
                "shimmer": {
                    "type": "float", "min": 0.0, "max": 0.5, "default": 0.10,
                    "description": "Spatial edge shimmer depth",
                },
                "mask_path": {
                    "type": "str", "default": "config/plant_pixel_map_32x138.json",
                    "description": "Path to the foliage mask JSON",
                },
                "globe_mask_path": {
                    "type": "str", "default": "config/plant_globe_map_32x138.json",
                    "description": "Path to the globe mask JSON",
                },
            }
        )
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        geometry_keys = {"mask_path", "globe_mask_path", "glow_radius", "glow_falloff"}
        needs_reload = bool({"mask_path", "globe_mask_path"} & new_params.keys())
        needs_geometry = bool(geometry_keys & new_params.keys())
        super().update_parameters(new_params)
        if needs_reload:
            self._load_masks()
        elif needs_geometry:
            self._rebuild_geometry()

    def _color(self, prefix: str) -> np.ndarray:
        return np.asarray(
            [self.params[f"{prefix}_red"], self.params[f"{prefix}_green"], self.params[f"{prefix}_blue"]],
            dtype=np.float32,
        )

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        speed = float(self.params.get("speed", 1.0))
        breath_speed = float(self.params.get("breath_speed", 0.18))
        breath_depth = float(np.clip(self.params.get("breath_depth", 0.24), 0.0, 0.8))
        shimmer_depth = float(np.clip(self.params.get("shimmer", 0.10), 0.0, 0.5))
        glow_strength = max(0.0, float(self.params.get("glow_strength", 0.72)))
        phase = time_elapsed * speed * breath_speed * 2.0 * math.pi
        foliage_breath = 1.0 - breath_depth + breath_depth * (0.5 + 0.5 * math.sin(phase))
        globe_breath = 1.0 - breath_depth + breath_depth * (0.5 + 0.5 * math.sin(phase + 1.7))

        linear = self._linear_frame
        linear[:] = self._color("background")
        shimmer = 1.0 + shimmer_depth * np.sin(self._phase + phase * 1.9)
        foliage_level = self._foliage_halo * shimmer * glow_strength * foliage_breath
        globe_level = self._globe_halo * shimmer * glow_strength * globe_breath
        linear += foliage_level[:, None] * self._color("foliage_halo")
        linear += globe_level[:, None] * self._color("globe_halo")
        linear[self._foliage_core] = self._color("foliage") * foliage_breath
        linear[self._globe_core] = self._color("globe") * globe_breath

        frame = self.next_frame_buffer(clear=False)
        np.clip(linear, 0.0, 255.0, out=linear)
        np.copyto(frame, linear, casting="unsafe")
        return self.apply_brightness_array(frame, out=frame)

    def get_runtime_stats(self) -> Dict[str, Any]:
        stats = {
            "foliage_pixels": len(self.foliage_indices),
            "globe_pixels": len(self.globe_indices),
            "globe_regions": self.globe_region_count,
            "foliage_halo_pixels": int(np.count_nonzero(self._foliage_halo)),
            "globe_halo_pixels": int(np.count_nonzero(self._globe_halo)),
            "glow_radius": int(self.params.get("glow_radius", 2)),
            "mask_path": str(self.params.get("mask_path")),
            "globe_mask_path": str(self.params.get("globe_mask_path")),
        }
        if self.mask_load_error:
            stats["mask_load_error"] = self.mask_load_error
        if self.globe_mask_load_error:
            stats["globe_mask_load_error"] = self.globe_mask_load_error
        return stats

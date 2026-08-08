"""Vectorized sine-wave color animation."""

from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase
from animation.libraries.mask_effects import build_halo_weights
from animation.libraries.spatial import normalized_axis_positions


class WaveAnimation(AnimationBase):
    ANIMATION_NAME = "Color Wave"
    ANIMATION_DESCRIPTION = "Traveling sine wave across the horizontal, vertical, or diagonal axis"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "wave_red": 0, "wave_green": 255, "wave_blue": 255,
            "background_red": 0, "background_green": 0, "background_blue": 12,
            "axis": "vertical", "frequency": 2.0, "speed": 0.25,
            "amplitude": 1.0, "direction": 1,
        })
        self.params = {**self.default_params, **self.config}
        self.params.pop("color_saturation", None)
        self.params.pop("color_value", None)
        total = self.get_pixel_count()
        self._phase = np.empty(total, dtype=np.float32)
        self._blend = np.empty(total, dtype=np.float32)
        self._scratch = np.empty((total, 3), dtype=np.float32)
        self._plant_field_key = None
        self._plant_phase_offsets = None
        self._plant_foliage = None
        self._plant_globes = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.pop("color_saturation", None)
        schema.pop("color_value", None)
        schema["speed"].update({
            "min": 0.0, "max": 4.0, "default": 0.25,
            "description": "Wave travel speed in cycles per second",
        })
        schema.update({
            "wave_red": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Wave red"},
            "wave_green": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Wave green"},
            "wave_blue": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Wave blue"},
            "background_red": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Background red"},
            "background_green": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Background green"},
            "background_blue": {"type": "int", "min": 0, "max": 255, "default": 12, "description": "Background blue"},
            "axis": {"type": "str", "default": "vertical", "description": "horizontal, vertical, or diagonal"},
            "frequency": {"type": "float", "min": 0.1, "max": 12.0, "default": 2.0, "description": "Spatial cycles across the wall"},
            "amplitude": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0, "description": "Color contrast around the midpoint"},
            "direction": {"type": "int", "min": -1, "max": 1, "default": 1, "description": "Travel direction: 1 or -1"},
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        axis = str(self.params.get("axis", "vertical")).lower()
        if axis not in {"horizontal", "vertical", "diagonal"}:
            axis = "vertical"
        position = self._positions(axis)
        frequency = max(0.1, float(self.params.get("frequency", 2.0)))
        speed = max(0.0, float(self.params.get("speed", 0.25)))
        direction = -1.0 if int(self.params.get("direction", 1)) < 0 else 1.0
        amplitude = min(1.0, max(0.0, float(self.params.get("amplitude", 1.0))))

        np.multiply(position, frequency, out=self._phase)
        plant_aware = self.plant_aware_enabled()
        if plant_aware:
            masks = self.get_plant_masks()
            self._ensure_plant_field(masks)
            self._phase += self._plant_phase_offsets
        self._phase += float(time_elapsed) * speed * direction
        self._phase *= 2.0 * np.pi
        np.sin(self._phase, out=self._blend)
        self._blend *= 0.5 * amplitude
        self._blend += 0.5

        background, wave = self._color("background"), self._color("wave")
        np.multiply(wave - background, self._blend[:, None], out=self._scratch)
        self._scratch += background
        self._scratch *= min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        np.clip(self._scratch, 0, 255, out=self._scratch)
        frame = self.next_frame_buffer(clear=False)
        frame[:] = self._scratch
        if plant_aware:
            self._accent_plant_cores(frame, brightness=float(self.params.get("brightness", 1.0)))
        return frame

    def _ensure_plant_field(self, masks) -> None:
        """Cache refraction offsets that bend wave fronts around both mask layers."""
        radius = max(2, int(self.params.get("plant_clearance", 1)) + 2)
        key = (id(masks), radius)
        if key == self._plant_field_key:
            return

        width, height = self.get_strip_info()
        _, foliage_halo = build_halo_weights(
            np.flatnonzero(masks.foliage_flat), width, height, radius, 1.2
        )
        _, globe_halo = build_halo_weights(
            np.flatnonzero(masks.globes_flat), width, height, radius, 1.05
        )

        # Leaf clusters advance the local wave front while glass globes retard
        # it.  The opposing refraction makes the semantic layers recognizable
        # and parts useful high-contrast crests around their occluded cores.
        self._plant_phase_offsets = (
            foliage_halo * np.float32(0.16)
            - globe_halo * np.float32(0.22)
        )
        self._plant_foliage = masks.foliage_flat
        self._plant_globes = masks.globes_flat
        self._plant_field_key = key

    def _accent_plant_cores(self, frame: np.ndarray, brightness: float) -> None:
        """Replace clipped crests with subdued, distinct plant landmarks."""
        intensity = min(1.0, max(0.0, float(brightness)))
        foliage = np.rint(np.asarray((20, 126, 48)) * intensity).astype(np.uint8)
        globe = np.rint(np.asarray((180, 42, 158)) * intensity).astype(np.uint8)
        frame[self._plant_foliage] = foliage
        frame[self._plant_globes] = globe

    def _positions(self, axis: str) -> np.ndarray:
        width, height = self.get_strip_info()
        return normalized_axis_positions(width, height, axis)

    def _color(self, prefix: str) -> np.ndarray:
        defaults = (0, 255, 255) if prefix == "wave" else (0, 0, 12)
        return np.asarray([
            max(0, min(255, int(self.params.get(f"{prefix}_{channel}", default))))
            for channel, default in zip(("red", "green", "blue"), defaults)
        ], dtype=np.float32)

    def get_runtime_stats(self) -> Dict[str, Any]:
        if not self.plant_aware_enabled():
            return {"plant_aware": False}
        masks = self.get_plant_masks()
        return {
            "plant_aware": True,
            "plant_foliage_pixels": masks.foliage_count,
            "plant_globe_pixels": masks.globe_count,
            "plant_globe_regions": masks.globe_regions,
            "plant_mask_error": masks.error,
        }

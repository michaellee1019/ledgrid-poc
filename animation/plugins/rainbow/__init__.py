#!/usr/bin/env python3
"""
Rainbow Animation Plugin

Classic rainbow cycle animation that flows across all LED strips.
Based on the original rainbow_animation from led_controller_spi.py
"""

import numpy as np
from typing import Dict, Any
from animation import AnimationBase
from animation.libraries.mask_effects import build_halo_weights


class RainbowAnimation(AnimationBase):
    """Rainbow cycle animation flowing across all strips"""

    _COLOR_LUT_SIZE = 1536
    
    ANIMATION_NAME = "Rainbow Cycle"
    ANIMATION_DESCRIPTION = "Classic rainbow animation that cycles through all colors across the LED strips"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.hue_offset = 0.0
        
        self.default_params.update({
            'speed': 0.3,
            'span_ratio': 1.0,
            'direction': 1,
            'brightness': 1.0,
            'color_saturation': 1.0,
            'color_value': 1.0
        })
        
        self.params = {**self.default_params, **self.config}
        self._cached_geometry = None
        self._base_indices = None
        self._indices = None
        self._color_lut = None
        self._color_lut_key = None
        self._plant_field_key = None
        self._plant_phase_offsets = None
        self._plant_foliage = None
        self._plant_globes = None

    def _ensure_arrays(self, strip_count: int, leds_per_strip: int, span_pixels: int):
        key = (strip_count, leds_per_strip, span_pixels)
        if key == self._cached_geometry:
            return
        strip_hues = np.arange(leds_per_strip, dtype=np.float32) / span_pixels
        strip_indices = np.floor(
            np.remainder(strip_hues, 1.0) * self._COLOR_LUT_SIZE
        ).astype(np.int32)
        self._base_indices = np.tile(strip_indices, strip_count)
        self._indices = np.empty_like(self._base_indices)
        self._cached_geometry = key

    def _ensure_color_lut(self, saturation: float, value: float, brightness: float):
        key = (float(saturation), float(value), float(brightness))
        if key == self._color_lut_key:
            return

        hues = np.arange(self._COLOR_LUT_SIZE, dtype=np.float32)
        hues /= self._COLOR_LUT_SIZE
        sats = np.full_like(hues, saturation)
        vals = np.full_like(hues, value)
        self._color_lut = np.empty((self._COLOR_LUT_SIZE, 3), dtype=np.uint8)
        self.hsv_to_rgb_array(hues, sats, vals, out=self._color_lut)
        self.apply_brightness_array(self._color_lut, out=self._color_lut)
        self._color_lut_key = key

    def _ensure_plant_field(self, masks):
        """Cache a phase field whose contours part around calibrated plants."""
        radius = max(2, int(self.params.get('plant_clearance', 1)) + 2)
        key = (id(masks), radius)
        if key == self._plant_field_key:
            return

        strip_count, leds_per_strip = self.get_strip_info()
        foliage_indices = np.flatnonzero(masks.foliage_flat)
        globe_indices = np.flatnonzero(masks.globes_flat)
        _, foliage_halo = build_halo_weights(
            foliage_indices, strip_count, leds_per_strip, radius, 1.25
        )
        _, globe_halo = build_halo_weights(
            globe_indices, strip_count, leds_per_strip, radius, 1.1
        )

        # Opposing phase bends keep the two semantic layers readable: rainbow
        # bands bow forward around foliage and backward around glass globes.
        phase = foliage_halo * np.float32(0.16 * self._COLOR_LUT_SIZE)
        phase -= globe_halo * np.float32(0.22 * self._COLOR_LUT_SIZE)
        self._plant_phase_offsets = np.rint(phase).astype(np.int32)
        self._plant_foliage = masks.foliage_flat
        self._plant_globes = masks.globes_flat
        self._plant_field_key = key

    def _accent_plant_cores(self, frame, value: float, brightness: float):
        """Turn occluded cores into subdued leaf and glass landmarks."""
        intensity = np.clip(float(value) * float(brightness), 0.0, 1.0)
        foliage = np.rint(np.asarray((22, 150, 65)) * intensity).astype(np.uint8)
        globe = np.rint(np.asarray((220, 55, 190)) * intensity).astype(np.uint8)
        frame[self._plant_foliage] = foliage
        frame[self._plant_globes] = globe
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'span_ratio': {
                'type': 'float',
                'min': 0.1,
                'max': 3.0,
                'default': 1.0,
                'description': 'Rainbow span ratio (1.0 = one rainbow per strip)'
            },
            'direction': {
                'type': 'int',
                'min': -1,
                'max': 1,
                'default': 1,
                'description': 'Animation direction (1=forward, -1=reverse)'
            }
        })
        return schema
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        """Generate rainbow frame. Returns (N,3) uint8 ndarray."""
        strip_count, leds_per_strip = self.get_strip_info()
        speed = self.params.get('speed', 0.3)
        span_ratio = self.params.get('span_ratio', 1.0)
        direction = self.params.get('direction', 1)
        saturation = self.params.get('color_saturation', 1.0)
        value = self.params.get('color_value', 1.0)
        brightness = self.params.get('brightness', 1.0)
        
        span_pixels = max(int(leds_per_strip * span_ratio), 1)
        
        hue_step = 0.01 * speed * direction
        self.hue_offset += hue_step
        if self.hue_offset >= 1.0:
            self.hue_offset -= 1.0
        elif self.hue_offset < 0.0:
            self.hue_offset += 1.0
        
        self._ensure_arrays(strip_count, leds_per_strip, span_pixels)
        self._ensure_color_lut(saturation, value, brightness)
        offset = int(self.hue_offset * self._COLOR_LUT_SIZE)
        np.add(self._base_indices, offset, out=self._indices)
        plant_aware = self.plant_aware_enabled()
        if plant_aware:
            masks = self.get_plant_masks()
            self._ensure_plant_field(masks)
            np.add(self._indices, self._plant_phase_offsets, out=self._indices)
        np.remainder(self._indices, self._COLOR_LUT_SIZE, out=self._indices)
        result = self.next_frame_buffer(clear=False)
        np.take(self._color_lut, self._indices, axis=0, out=result)
        if plant_aware:
            self._accent_plant_cores(result, value, brightness)
        return result

    def get_runtime_stats(self) -> Dict[str, Any]:
        if not self.plant_aware_enabled():
            return {'plant_aware': False}
        masks = self.get_plant_masks()
        return {
            'plant_aware': True,
            'plant_foliage_pixels': masks.foliage_count,
            'plant_globe_pixels': masks.globe_count,
            'plant_globe_regions': masks.globe_regions,
            'plant_mask_error': masks.error,
        }

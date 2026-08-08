#!/usr/bin/env python3
"""
Solid Color Animation Plugin

Simple solid color animation with optional breathing effect.
"""

import math
from typing import Dict, Any

import numpy as np

from animation import AnimationBase


class SolidColorAnimation(AnimationBase):
    """Solid color animation with optional breathing effect"""
    
    ANIMATION_NAME = "Solid Color"
    ANIMATION_DESCRIPTION = "Display a solid color across all LEDs with optional breathing effect"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.default_params.update({
            'red': 255,
            'green': 0,
            'blue': 0,
            'breathing': False,
            'breathing_speed': 1.0,
            'min_brightness': 0.1,
            'max_brightness': 1.0,
            'plant_foliage_strength': 0.62,
            'plant_globe_strength': 0.72,
            'plant_negative_space': 0.58,
            'plant_breath_speed': 0.16,
            'plant_breath_depth': 0.24,
            'plant_render_fps': 20.0,
        })
        
        self.params = {**self.default_params, **self.config}
        self._last_frame_key = None
        self._last_frame = None
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'red': {
                'type': 'int',
                'min': 0,
                'max': 255,
                'default': 255,
                'description': 'Red component (0-255)'
            },
            'green': {
                'type': 'int',
                'min': 0,
                'max': 255,
                'default': 0,
                'description': 'Green component (0-255)'
            },
            'blue': {
                'type': 'int',
                'min': 0,
                'max': 255,
                'default': 0,
                'description': 'Blue component (0-255)'
            },
            'breathing': {
                'type': 'bool',
                'default': False,
                'description': 'Enable breathing effect'
            },
            'breathing_speed': {
                'type': 'float',
                'min': 0.1,
                'max': 5.0,
                'default': 1.0,
                'description': 'Breathing effect speed'
            },
            'min_brightness': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.1,
                'description': 'Minimum brightness for breathing'
            },
            'max_brightness': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 1.0,
                'description': 'Maximum brightness for breathing'
            },
            'plant_foliage_strength': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.62,
                'description': 'Leaf-green highlight strength in plant-aware mode'
            },
            'plant_globe_strength': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.72,
                'description': 'Glass-magenta highlight strength in plant-aware mode'
            },
            'plant_negative_space': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.58,
                'description': 'Brightness retained in the clearance ring around plants'
            },
            'plant_breath_speed': {
                'type': 'float',
                'min': 0.0,
                'max': 2.0,
                'default': 0.16,
                'description': 'Plant landmark breathing cycles per second'
            },
            'plant_breath_depth': {
                'type': 'float',
                'min': 0.0,
                'max': 0.8,
                'default': 0.24,
                'description': 'Depth of the alternating foliage and globe breath'
            },
            'plant_render_fps': {
                'type': 'float',
                'min': 1.0,
                'max': 60.0,
                'default': 20.0,
                'description': 'Maximum source rate for plant-aware breathing'
            }
        })
        return schema

    @staticmethod
    def _bounded(value, minimum, maximum):
        return min(maximum, max(minimum, float(value)))

    def _apply_plant_ambient(self, frame, color, masks, phase):
        """Give a solid field leaf, glass, and negative-space semantics."""
        foliage_strength = self._bounded(
            self.params.get('plant_foliage_strength', 0.62), 0.0, 1.0
        )
        globe_strength = self._bounded(
            self.params.get('plant_globe_strength', 0.72), 0.0, 1.0
        )
        negative_space = self._bounded(
            self.params.get('plant_negative_space', 0.58), 0.0, 1.0
        )
        breath_depth = self._bounded(
            self.params.get('plant_breath_depth', 0.24), 0.0, 0.8
        )

        # A subdued clearance ring leaves visual breathing room around occluded
        # pixels instead of spending the solid field's full intensity behind
        # plant edges. The calibrated cores remain useful semantic landmarks.
        ring = masks.clearance_flat & ~masks.obstacle_flat
        if np.any(ring):
            frame[ring] = np.rint(
                np.asarray(color, dtype=np.float32) * negative_space
            ).astype(np.uint8)

        foliage_breath = 1.0 - breath_depth * (0.5 - 0.5 * math.sin(phase))
        globe_breath = 1.0 - breath_depth * (0.5 - 0.5 * math.sin(phase + math.pi))
        brightness = self._bounded(self.params.get('brightness', 1.0), 0.0, 1.0)
        base = np.asarray(color, dtype=np.float32)
        foliage_target = np.asarray((28, 190, 78), dtype=np.float32) * brightness
        globe_target = np.asarray((220, 62, 196), dtype=np.float32) * brightness
        foliage_alpha = foliage_strength * foliage_breath
        globe_alpha = globe_strength * globe_breath

        foliage_color = (
            base * (1.0 - foliage_alpha) + foliage_target * foliage_alpha
        )
        globe_color = (
            base * (1.0 - globe_alpha) + globe_target * globe_alpha
        )
        frame[masks.foliage_flat] = np.clip(foliage_color, 0.0, 255.0)
        frame[masks.globes_flat] = np.clip(globe_color, 0.0, 255.0)
    
    def generate_frame(self, time_elapsed: float, frame_count: int):
        """Generate solid color frame"""
        total_pixels = self.get_pixel_count()
        
        # Get base color
        r = self.params.get('red', 255)
        g = self.params.get('green', 0)
        b = self.params.get('blue', 0)
        
        # Apply breathing effect if enabled
        if self.params.get('breathing', False):
            breathing_speed = self.params.get('breathing_speed', 1.0)
            min_brightness = self.params.get('min_brightness', 0.1)
            max_brightness = self.params.get('max_brightness', 1.0)
            
            # Calculate breathing brightness using sine wave
            breathing_phase = time_elapsed * breathing_speed * 2 * math.pi
            breathing_factor = (math.sin(breathing_phase) + 1) / 2  # 0-1
            brightness = min_brightness + (max_brightness - min_brightness) * breathing_factor
            
            r = int(r * brightness)
            g = int(g * brightness)
            b = int(b * brightness)
        
        # Apply global brightness
        color = self.apply_brightness((r, g, b))
        
        plant_aware = self.plant_aware_enabled()
        static_key = None
        if not self.params.get('breathing', False) and not plant_aware:
            static_key = (color, total_pixels)
            if static_key == self._last_frame_key and self._last_frame is not None:
                return self.rendered_frame(self._last_frame, changed=False)

        masks = None
        plant_phase = 0.0
        if plant_aware:
            masks = self.get_plant_masks()
            plant_fps = self._bounded(
                self.params.get('plant_render_fps', 20.0), 1.0, 60.0
            )
            plant_tick = int(max(0.0, time_elapsed) * plant_fps)
            if (
                self._bounded(self.params.get('plant_breath_speed', 0.16), 0.0, 2.0) == 0.0
                or self._bounded(self.params.get('plant_breath_depth', 0.24), 0.0, 0.8) == 0.0
            ):
                plant_tick = 0
            plant_time = plant_tick / plant_fps
            plant_phase = (
                plant_time
                * self._bounded(self.params.get('plant_breath_speed', 0.16), 0.0, 2.0)
                * 2.0
                * math.pi
            )
            static_key = (
                'plant', color, total_pixels, plant_tick, id(masks),
                self.params.get('plant_foliage_strength', 0.62),
                self.params.get('plant_globe_strength', 0.72),
                self.params.get('plant_negative_space', 0.58),
                self.params.get('plant_breath_speed', 0.16),
                self.params.get('plant_breath_depth', 0.24),
                plant_fps,
            )
            if static_key == self._last_frame_key and self._last_frame is not None:
                return self.rendered_frame(self._last_frame, changed=False)

        frame = self.next_frame_buffer(clear=False)
        frame[:] = color
        if masks is not None:
            self._apply_plant_ambient(frame, color, masks, plant_phase)
        self._last_frame_key = static_key
        self._last_frame = frame
        return self.rendered_frame(frame)

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

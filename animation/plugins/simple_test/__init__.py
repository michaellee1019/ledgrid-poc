#!/usr/bin/env python3
"""
Simple Test Animation Plugin

Very basic animation to test LED strip connectivity.
Lights up all LEDs in solid colors to verify which strips work.
"""

from typing import List, Tuple, Dict, Any
import numpy as np
from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class SimpleTestAnimation(AnimationBase):
    """Simple test animation - solid colors"""
    
    ANIMATION_NAME = "Simple Test"
    ANIMATION_DESCRIPTION = "Solid color test for all LEDs"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        # Animation state
        self.color_index = 0
        self.last_change = 0
        self.change_interval = 2.0  # Change color every 2 seconds
        
        # Test colors
        self.colors = [
            (255, 0, 0),    # Red
            (0, 255, 0),    # Green  
            (0, 0, 255),    # Blue
            (255, 255, 0),  # Yellow
            (255, 0, 255),  # Magenta
            (0, 255, 255),  # Cyan
            (255, 255, 255) # White
        ]
        
        # Get controller dimensions
        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.total_leds = self.num_strips * self.leds_per_strip
        self._color_frames = [
            np.full((self.total_leds, 3), color, dtype=np.uint8)
            for color in self.colors
        ]
        self._plant_color_frames = None
        self._plant_frame_key = None
        self._last_output_index = None
        self._last_output_key = None
        
        print("🔍 Simple Test Animation initialized:")
        print(f"   Strips: {self.num_strips}")
        print(f"   LEDs per strip: {self.leds_per_strip}")
        print(f"   Total LEDs: {self.total_leds}")
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate test frame"""
        color_index = int(time_elapsed / max(0.5, self.change_interval)) % len(self.colors)
        plant_aware = self.plant_aware_enabled()
        output_key = (plant_aware, color_index, self._plant_frame_key if plant_aware else None)
        color_changed = color_index != self._last_output_index
        if color_changed:
            self.color_index = color_index
            current_color = self.colors[self.color_index]
            color_name = ["Red", "Green", "Blue", "Yellow", "Magenta", "Cyan", "White"][self.color_index]
            print(f"🎨 Switching to {color_name}: RGB{current_color}")
            self._last_output_index = color_index
        if plant_aware:
            frames = self._plant_aware_frames()
            # Loading the masks establishes the cache key used for change detection.
            output_key = (True, color_index, self._plant_frame_key)
            pixels = frames[color_index]
        else:
            pixels = self._color_frames[color_index]
        changed = output_key != self._last_output_key
        self._last_output_key = output_key
        return self.rendered_frame(pixels, changed=changed)

    def _plant_aware_frames(self):
        """Build connectivity frames that distinguish occlusion from LED failure."""
        masks = self.get_plant_masks()
        level = min(1.0, max(0.05, float(
            self.params.get('plant_occlusion_brightness', 0.35)
        )))
        key = (id(masks), level)
        if self._plant_color_frames is not None and self._plant_frame_key == key:
            return self._plant_color_frames

        # Keep the ordinary test color on clear pixels. The clearance ring is dimmed
        # to make the calibrated occlusion boundary obvious, while the two mask cores
        # use stable semantic colors at a deliberately restrained output level.
        foliage_color = np.rint(
            np.asarray((48, 255, 80), dtype=np.float32) * level
        ).astype(np.uint8)
        globe_color = np.rint(
            np.asarray((255, 80, 220), dtype=np.float32) * level
        ).astype(np.uint8)
        frames = []
        for ordinary in self._color_frames:
            diagnostic = ordinary.copy()
            diagnostic[masks.clearance_flat] = np.rint(
                ordinary[masks.clearance_flat].astype(np.float32) * level
            ).astype(np.uint8)
            diagnostic[masks.foliage_flat] = foliage_color
            diagnostic[masks.globes_flat] = globe_color
            frames.append(diagnostic)
        self._plant_color_frames = frames
        self._plant_frame_key = key
        return frames
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """Return configurable parameters"""
        schema = super().get_parameter_schema()
        schema.update({
            'change_interval': {
                'type': 'float',
                'min': 0.5,
                'max': 10.0,
                'default': 2.0,
                'description': 'Color change interval (seconds)'
            },
            'plant_occlusion_brightness': {
                'type': 'float',
                'min': 0.05,
                'max': 1.0,
                'default': 0.35,
                'description': 'Brightness used for plant landmarks and their clearance ring'
            }
        })
        return schema
    
    def update_parameters(self, params: Dict[str, Any]):
        """Update animation parameters"""
        super().update_parameters(params)
        if 'change_interval' in params:
            self.change_interval = max(0.5, float(params['change_interval']))
        if {
            'plant_clearance', 'plant_mask_path', 'plant_globe_mask_path',
            'plant_occlusion_brightness',
        } & params.keys():
            self._plant_color_frames = None
            self._plant_frame_key = None

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

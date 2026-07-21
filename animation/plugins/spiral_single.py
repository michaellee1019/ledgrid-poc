#!/usr/bin/env python3
"""
Single pixel spiral animation.
"""

from typing import List, Tuple, Dict, Any

import numpy as np

from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class SpiralSingleAnimation(AnimationBase):
    """Illuminates one pixel at a time following a spiral path."""

    ANIMATION_NAME = "Spiral Single"
    ANIMATION_DESCRIPTION = "Single pixel spirals across the grid at maximum framerate"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'red': 255,
            'green': 255,
            'blue': 255,
            'pixels_per_second': 200.0,
        })
        self.params = {**self.default_params, **self.config}

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.total_pixels = self.num_strips * self.leds_per_strip

        self.spiral_indices = self._build_spiral_indices(self.num_strips, self.leds_per_strip)
        self._plant_route_masks = None
        self._plant_spiral_indices: List[int] = []
        self._plant_spiral_semantics: List[int] = []
        self.step_index = 0
        self._buffer_pixel = [None, None]
        self._last_output_pixel = None
        self._last_step_number = None
        self._last_route_token = None
        self._last_frame = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Red component (0-255)'},
            'green': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Green component (0-255)'},
            'blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Blue component (0-255)'},
            'pixels_per_second': {
                'type': 'float', 'min': 1.0, 'max': 1000.0, 'default': 200.0,
                'description': 'Travel speed independent of render FPS',
            },
        })
        schema.pop('speed', None)
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        if self.total_pixels <= 0:
            return np.empty((0, 3), dtype=np.uint8)

        pixels_per_second = max(1.0, float(self.params.get('pixels_per_second', 200.0)))
        step_number = int(time_elapsed * pixels_per_second)
        route, semantics, route_token = self._active_route()
        if (
            step_number == self._last_step_number
            and route_token == self._last_route_token
            and self._last_frame is not None
        ):
            return self.rendered_frame(self._last_frame, changed=False)

        buffer_index = self._frame_buffer_index
        frame = self.next_frame_buffer(clear=False)
        previous_pixel = self._buffer_pixel[buffer_index]
        if previous_pixel is not None:
            frame[previous_pixel] = 0

        self.step_index = step_number % len(route)
        idx = route[self.step_index]
        semantic = semantics[self.step_index] if semantics else 0
        if semantic == 2:
            color = (255, 80, 220)  # Rooting globe boundary.
        elif semantic == 1:
            color = (48, 255, 80)  # Foliage boundary.
        else:
            color = (
                int(self.params.get('red', 255)),
                int(self.params.get('green', 255)),
                int(self.params.get('blue', 255)),
            )
        frame[idx] = self.apply_brightness(color)
        self._buffer_pixel[buffer_index] = idx
        self._last_step_number = step_number
        self._last_route_token = route_token
        self._last_frame = frame
        dirty_pixels = {idx}
        if self._last_output_pixel is not None:
            dirty_pixels.add(self._last_output_pixel)
        self._last_output_pixel = idx
        dirty_ranges = tuple((pixel, pixel + 1) for pixel in sorted(dirty_pixels))
        return self.rendered_frame(frame, dirty_ranges=dirty_ranges)

    def _active_route(self):
        """Return the legacy route or a safe route with plant encounter marks.

        Plant-aware traversal removes complete clearance runs instead of shining
        through them.  The safe pixel immediately before each removed run is
        colored green for foliage or magenta for a rooting globe, making the
        calibrated geometry act as a visible turn/phase boundary.
        """
        if not self.plant_aware_enabled():
            return self.spiral_indices, (), (False,)

        masks = self.get_plant_masks()
        if self._plant_route_masks is not masks:
            route: List[int] = []
            semantics: List[int] = []
            leading_semantic = 0
            pending_semantic = 0

            for idx in self.spiral_indices:
                if masks.clearance_flat[idx]:
                    semantic = 2 if masks.globes_flat[idx] else (
                        1 if masks.foliage_flat[idx] else 0
                    )
                    if route:
                        pending_semantic = max(pending_semantic, semantic)
                    else:
                        leading_semantic = max(leading_semantic, semantic)
                    continue

                if pending_semantic:
                    semantics[-1] = max(semantics[-1], pending_semantic)
                    pending_semantic = 0
                route.append(idx)
                semantics.append(0)

            if route:
                # The spiral is cyclic, so an obstacle spanning the end/start
                # seam is announced by the final safe pixel in the route.
                semantics[-1] = max(
                    semantics[-1], pending_semantic, leading_semantic
                )
            else:
                # Degenerate masks covering the entire display leave no route
                # around the plants. Keep the animation alive with one marked
                # landmark rather than failing modulo zero.
                route = [self.spiral_indices[0]]
                semantic = 2 if np.any(masks.globes_flat) else 1
                semantics = [semantic]

            self._plant_spiral_indices = route
            self._plant_spiral_semantics = semantics
            self._plant_route_masks = masks

        return (
            self._plant_spiral_indices,
            self._plant_spiral_semantics,
            (True, id(masks)),
        )

    def get_runtime_stats(self) -> Dict[str, Any]:
        if not self.plant_aware_enabled():
            return {'plant_aware': False}
        masks = self.get_plant_masks()
        route, _, _ = self._active_route()
        return {
            'plant_aware': True,
            'plant_route_pixels': len(route),
            'plant_skipped_pixels': max(0, len(self.spiral_indices) - len(route)),
            'plant_foliage_pixels': masks.foliage_count,
            'plant_globe_pixels': masks.globe_count,
            'plant_globe_regions': masks.globe_regions,
            'plant_mask_error': masks.error,
        }

    def _build_spiral_indices(self, width: int, height: int) -> List[int]:
        coords: List[Tuple[int, int]] = []
        left = 0
        right = width - 1
        top = 0
        bottom = height - 1

        while left <= right and top <= bottom:
            for x in range(left, right + 1):
                coords.append((x, top))
            for y in range(top + 1, bottom + 1):
                coords.append((right, y))
            if top < bottom:
                for x in range(right - 1, left - 1, -1):
                    coords.append((x, bottom))
            if left < right:
                for y in range(bottom - 1, top, -1):
                    coords.append((left, y))

            left += 1
            right -= 1
            top += 1
            bottom -= 1

        indices = []
        for x, y in coords:
            phys_led = (height - 1) - y
            idx = x * height + phys_led
            if 0 <= idx < self.total_pixels:
                indices.append(idx)

        if not indices:
            indices = [0]
        return indices

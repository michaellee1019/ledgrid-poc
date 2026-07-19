#!/usr/bin/env python3
"""
Strip Order Test Animation

Lights each vertical strip one at a time (50% white) so physical vs logical
ordering can be verified. Hold → pause → next strip, looping forever.
"""

from typing import Dict, Any
import numpy as np

from animation import AnimationBase, RenderedFrame


class StripOrderAnimation(AnimationBase):
    """Illuminate one strip at a time to verify strip ordering."""

    ANIMATION_NAME = "Strip Order Test"
    ANIMATION_DESCRIPTION = (
        "Lights each vertical strip one at a time (50% white, 1s on / 1s off) "
        "to confirm strip ordering"
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.default_params.update({
            'hold_seconds': 1.0,
            'pause_seconds': 1.0,
            'brightness': 0.5,
        })
        self.params = {**self.default_params, **(config or {})}
        self._frame = np.zeros((controller.total_leds, 3), dtype=np.uint8)
        self._active_strip = object()
        self._level = None

        if controller and getattr(controller, 'debug', False):
            print("Strip Order Test initialized:")
            print(f"   Strips: {controller.strip_count}")
            print(f"   LEDs per strip: {controller.leds_per_strip}")

    def _white(self):
        brightness = float(self.params.get('brightness', 0.5))
        level = max(0, min(255, int(255 * brightness)))
        return level

    def generate_frame(self, time_elapsed: float, frame_count: int):
        """Advance only when the hold/pause state changes."""
        strip_count = self.controller.strip_count
        hold = max(0.0, float(self.params.get('hold_seconds', 1.0)))
        pause = max(0.0, float(self.params.get('pause_seconds', 1.0)))
        cycle = max(0.001, hold + pause)
        strip = int(time_elapsed / cycle) % strip_count
        active_strip = strip if (time_elapsed % cycle) < hold else None
        level = self._white()

        if active_strip == self._active_strip and level == self._level:
            return RenderedFrame(self._frame, changed=False)

        dirty_ranges = []
        leds_per_strip = self.controller.leds_per_strip
        if isinstance(self._active_strip, int):
            start = self._active_strip * leds_per_strip
            self._frame[start:start + leds_per_strip] = 0
            dirty_ranges.append((start, start + leds_per_strip))
        if active_strip is not None:
            start = active_strip * leds_per_strip
            self._frame[start:start + leds_per_strip] = level
            dirty_ranges.append((start, start + leds_per_strip))

        self._active_strip = active_strip
        self._level = level
        return RenderedFrame(
            self._frame,
            changed=True,
            dirty_ranges=tuple(sorted(dirty_ranges)) or None,
        )

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            'hold_seconds': {
                'type': 'float',
                'min': 0.1,
                'max': 10.0,
                'default': 1.0,
                'description': 'Seconds each strip stays illuminated',
            },
            'pause_seconds': {
                'type': 'float',
                'min': 0.0,
                'max': 10.0,
                'default': 1.0,
                'description': 'Seconds of all-off between strips',
            },
            'brightness': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.5,
                'description': 'White level (0.5 = 50%)',
            },
        }

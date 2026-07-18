#!/usr/bin/env python3
"""
Strip Order Test Animation

Lights each vertical strip one at a time (50% white) so physical vs logical
ordering can be verified. Hold → pause → next strip, looping forever.
"""

from typing import Dict, Any
from animation import StatefulAnimationBase


class StripOrderAnimation(StatefulAnimationBase):
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

        if controller and getattr(controller, 'debug', False):
            print("Strip Order Test initialized:")
            print(f"   Strips: {controller.strip_count}")
            print(f"   LEDs per strip: {controller.leds_per_strip}")

    def _white(self):
        brightness = float(self.params.get('brightness', 0.5))
        level = max(0, min(255, int(255 * brightness)))
        return (level, level, level)

    def _frame_for_strip(self, strip: int):
        """Build a full frame with only the given strip illuminated."""
        leds_per_strip = self.controller.leds_per_strip
        frame = [(0, 0, 0)] * self.controller.total_leds
        color = self._white()
        start = strip * leds_per_strip
        for i in range(start, start + leds_per_strip):
            frame[i] = color
        return frame

    def generate_frame(self, time_elapsed: float, frame_count: int):
        """Static preview: first strip on (stateful loop drives live playback)."""
        return self._frame_for_strip(0)

    def run_animation(self):
        """Loop: light strip N for hold_seconds, then all-off for pause_seconds."""
        strip_count = self.controller.strip_count
        black = [(0, 0, 0)] * self.controller.total_leds

        print(f"Strip Order Test: cycling {strip_count} strips...")

        while not self.stop_event.is_set():
            for strip in range(strip_count):
                if self.stop_event.is_set():
                    break

                hold = float(self.params.get('hold_seconds', 1.0))
                pause = float(self.params.get('pause_seconds', 1.0))

                print(f"Strip Order Test: strip {strip}/{strip_count - 1}")
                self.controller.set_all_pixels(self._frame_for_strip(strip))

                if self.stop_event.wait(hold):
                    break

                self.controller.set_all_pixels(black)

                if self.stop_event.wait(pause):
                    break

        # Ensure clean blackout on stop
        try:
            self.controller.set_all_pixels(black)
        except Exception:
            pass

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

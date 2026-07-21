#!/usr/bin/env python3
"""Strip-order diagnostic with an optional plant-mask inspection pass.

The default remains the original white hold → dark pause sequence.  When
plant awareness is enabled, the active strip stays fully visible while three
sub-passes distinguish foliage, rooting globes, and their clearance halo.
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
            'plant_diagnostic_style': 'focus_passes',
            'plant_context_brightness': 0.18,
            'plant_foliage_red': 42,
            'plant_foliage_green': 255,
            'plant_foliage_blue': 96,
            'plant_globe_red': 255,
            'plant_globe_green': 48,
            'plant_globe_blue': 220,
            'plant_clearance_red': 255,
            'plant_clearance_green': 144,
            'plant_clearance_blue': 24,
        })
        self.params = {**self.default_params, **(config or {})}
        self._frame = np.zeros((controller.total_leds, 3), dtype=np.uint8)
        self._active_strip = object()
        self._level = None
        self._plant_render_key = None
        self._plant_phase_name = 'disabled'

        if controller and getattr(controller, 'debug', False):
            print("Strip Order Test initialized:")
            print(f"   Strips: {controller.strip_count}")
            print(f"   LEDs per strip: {controller.leds_per_strip}")

    def _white(self):
        brightness = float(self.params.get('brightness', 0.5))
        level = max(0, min(255, int(255 * brightness)))
        return level

    def _plant_color(self, prefix: str) -> np.ndarray:
        """Return a configured diagnostic color scaled by overall brightness."""
        brightness = max(0.0, min(1.0, float(self.params.get('brightness', 0.5))))
        color = np.asarray(
            [
                self.params.get(f'{prefix}_red', 0),
                self.params.get(f'{prefix}_green', 0),
                self.params.get(f'{prefix}_blue', 0),
            ],
            dtype=np.float32,
        )
        return np.clip(color * brightness, 0, 255).astype(np.uint8)

    @staticmethod
    def _focus_phase(position: float, hold: float) -> int:
        """Divide a strip hold into overview, foliage, and globe passes."""
        if hold <= 0.0:
            return 0
        return min(2, int((position / hold) * 3.0))

    def _render_plant_strip(self, start: int, end: int, level: int, phase: int):
        """Render one complete strip with semantic mask crossings emphasized."""
        masks = self.get_plant_masks()
        strip = self._frame[start:end]
        strip[:] = level

        foliage = masks.foliage_flat[start:end]
        globes = masks.globes_flat[start:end]
        clearance = masks.clearance_flat[start:end] & ~(foliage | globes)
        foliage_color = self._plant_color('plant_foliage')
        globe_color = self._plant_color('plant_globe')
        clearance_color = self._plant_color('plant_clearance')

        style = str(self.params.get('plant_diagnostic_style', 'focus_passes'))
        if style == 'focus_passes' and phase:
            context = max(
                0.0,
                min(1.0, float(self.params.get('plant_context_brightness', 0.18))),
            )
            if phase == 1:
                globe_color = (globe_color.astype(np.float32) * context).astype(np.uint8)
                clearance_color = (
                    clearance_color.astype(np.float32) * context
                ).astype(np.uint8)
            else:
                foliage_color = (
                    foliage_color.astype(np.float32) * context
                ).astype(np.uint8)
                clearance_color = (
                    clearance_color.astype(np.float32) * context
                ).astype(np.uint8)

        strip[clearance] = clearance_color
        strip[foliage] = foliage_color
        strip[globes] = globe_color

    def generate_frame(self, time_elapsed: float, frame_count: int):
        """Advance only when the hold/pause state changes."""
        strip_count = self.controller.strip_count
        hold = max(0.0, float(self.params.get('hold_seconds', 1.0)))
        pause = max(0.0, float(self.params.get('pause_seconds', 1.0)))
        cycle = max(0.001, hold + pause)
        strip = int(time_elapsed / cycle) % strip_count
        active_strip = strip if (time_elapsed % cycle) < hold else None
        level = self._white()

        # Preserve the original state machine byte-for-byte unless explicitly
        # enabled.  Mask files are not loaded on this path.
        if not self.plant_aware_enabled():
            leaving_plant_mode = self._plant_render_key is not None
            self._plant_phase_name = 'disabled'
            if (
                not leaving_plant_mode
                and active_strip == self._active_strip
                and level == self._level
            ):
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
            self._plant_render_key = None
            return RenderedFrame(
                self._frame,
                changed=True,
                dirty_ranges=tuple(sorted(dirty_ranges)) or None,
            )

        position = time_elapsed % cycle
        style = str(self.params.get('plant_diagnostic_style', 'focus_passes'))
        phase = self._focus_phase(position, hold) if style == 'focus_passes' else 0
        colors = tuple(
            self.params.get(name)
            for prefix in ('plant_foliage', 'plant_globe', 'plant_clearance')
            for name in (f'{prefix}_red', f'{prefix}_green', f'{prefix}_blue')
        )
        plant_key = (
            active_strip,
            level,
            phase,
            style,
            float(self.params.get('plant_context_brightness', 0.18)),
            colors,
            int(self.params.get('plant_clearance', 1)),
            str(self.params.get('plant_mask_path', '')),
            str(self.params.get('plant_globe_mask_path', '')),
        )
        if plant_key == self._plant_render_key:
            return RenderedFrame(self._frame, changed=False)

        dirty_ranges = []
        leds_per_strip = self.controller.leds_per_strip
        if isinstance(self._active_strip, int):
            start = self._active_strip * leds_per_strip
            self._frame[start:start + leds_per_strip] = 0
            dirty_ranges.append((start, start + leds_per_strip))
        if active_strip is not None:
            start = active_strip * leds_per_strip
            self._render_plant_strip(start, start + leds_per_strip, level, phase)
            dirty_ranges.append((start, start + leds_per_strip))

        self._active_strip = active_strip
        self._level = level
        self._plant_render_key = plant_key
        self._plant_phase_name = ('overview', 'foliage', 'globes')[phase]
        return RenderedFrame(
            self._frame,
            changed=True,
            dirty_ranges=tuple(sorted(set(dirty_ranges))) or None,
        )

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
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
            'plant_diagnostic_style': {
                'type': 'str',
                'default': 'focus_passes',
                'options': ['focus_passes', 'semantic'],
                'description': 'Cycle mask focus passes or show all semantic colors together',
            },
            'plant_context_brightness': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.18,
                'description': 'Relative brightness of non-focused mask layers',
            },
        })
        for prefix, color, description in (
            ('plant_foliage', (42, 255, 96), 'Foliage crossing'),
            ('plant_globe', (255, 48, 220), 'Rooting-globe crossing'),
            ('plant_clearance', (255, 144, 24), 'Plant clearance halo'),
        ):
            for channel, default in zip(('red', 'green', 'blue'), color):
                schema[f'{prefix}_{channel}'] = {
                    'type': 'int',
                    'min': 0,
                    'max': 255,
                    'default': default,
                    'description': f'{description} {channel}',
                }
        return schema

    def get_runtime_stats(self) -> Dict[str, Any]:
        if not self.plant_aware_enabled():
            return {'plant_aware': False, 'plant_diagnostic_phase': 'disabled'}
        masks = self.get_plant_masks()
        return {
            'plant_aware': True,
            'plant_diagnostic_phase': self._plant_phase_name,
            'plant_foliage_pixels': masks.foliage_count,
            'plant_globe_pixels': masks.globe_count,
            'plant_globe_regions': masks.globe_regions,
            'plant_mask_error': masks.error,
        }

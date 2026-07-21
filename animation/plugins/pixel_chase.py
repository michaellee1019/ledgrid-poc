"""Individual-pixel diagnostic chase for validating the complete wall."""

from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase


class PixelChaseAnimation(AnimationBase):
    ANIMATION_NAME = "Pixel Chase"
    ANIMATION_DESCRIPTION = "Walks every physical LED to identify dead or misordered pixels"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    _CLEAR = 0
    _CLEARANCE = 1
    _FOLIAGE = 2
    _GLOBE = 3

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "pixels_per_second": 120.0,
            "red": 255, "green": 255, "blue": 255,
            "plant_foliage_red": 24,
            "plant_foliage_green": 255,
            "plant_foliage_blue": 72,
            "plant_globe_red": 80,
            "plant_globe_green": 180,
            "plant_globe_blue": 255,
        })
        self.params = {**self.default_params, **self.config}
        for unused in ("speed", "color_saturation", "color_value"):
            self.params.pop(unused, None)
        width, height = self.get_strip_info()
        self._physical_path = np.asarray([
            strip * height + physical_led
            for strip in range(width)
            for physical_led in range(height - 1, -1, -1)
        ], dtype=np.int32)
        self._path = self._physical_path
        self._path_kind = np.full(self._path.size, self._CLEAR, dtype=np.uint8)
        self._rebuild_path()
        self._buffer_pixel = [None, None]
        self._last_output_pixel = None
        self._last_step = None
        self._last_frame = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        for name in ("speed", "color_saturation", "color_value"):
            schema.pop(name, None)
        schema.update({
            "pixels_per_second": {
                "type": "float", "min": 0.5, "max": 1000.0, "default": 120.0,
                "description": "Number of physical LEDs visited per second",
            },
            "red": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Pixel red"},
            "green": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Pixel green"},
            "blue": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Pixel blue"},
        })
        for layer, defaults, description in (
            ("plant_foliage", (24, 255, 72), "Foliage diagnostic"),
            ("plant_globe", (80, 180, 255), "Globe diagnostic"),
        ):
            for channel, default in zip(("red", "green", "blue"), defaults):
                schema[f"{layer}_{channel}"] = {
                    "type": "int", "min": 0, "max": 255, "default": default,
                    "description": f"{description} {channel}",
                }
        return schema

    def _rebuild_path(self) -> None:
        """Keep wiring order within each increasingly occluded diagnostic pass."""
        if not self.plant_aware_enabled() or self._physical_path.size == 0:
            self._path = self._physical_path
            self._path_kind = np.full(self._path.size, self._CLEAR, dtype=np.uint8)
            return

        masks = self.get_plant_masks()
        physical = self._physical_path
        foliage = masks.foliage_flat[physical]
        globes = masks.globes_flat[physical]
        clearance = masks.clearance_flat[physical] & ~foliage & ~globes
        clear = ~masks.clearance_flat[physical]
        parts = []
        kinds = []
        for selector, kind in (
            (clear, self._CLEAR),
            (clearance, self._CLEARANCE),
            (foliage, self._FOLIAGE),
            (globes, self._GLOBE),
        ):
            selected = physical[selector]
            if selected.size:
                parts.append(selected)
                kinds.append(np.full(selected.size, kind, dtype=np.uint8))
        self._path = np.concatenate(parts) if parts else physical
        self._path_kind = np.concatenate(kinds) if kinds else np.empty(0, dtype=np.uint8)

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        if {
            "plant_aware", "plant_clearance", "plant_mask_path",
            "plant_globe_mask_path",
        } & new_params.keys():
            self._rebuild_path()
            for frame in self._frame_buffers:
                frame.fill(0)
            self._buffer_pixel = [None, None]
            self._last_output_pixel = None
            self._last_step = None
            self._last_frame = None

    def _pixel_color(self, path_index: int):
        prefix = ""
        if self.plant_aware_enabled():
            kind = int(self._path_kind[path_index])
            if kind == self._FOLIAGE:
                prefix = "plant_foliage_"
            elif kind == self._GLOBE:
                prefix = "plant_globe_"
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        return tuple(
            int(max(0, min(255, int(self.params.get(f"{prefix}{channel}", 255)))) * brightness)
            for channel in ("red", "green", "blue")
        )

    def generate_frame(self, time_elapsed: float, frame_count: int):
        if self._path.size == 0:
            return np.empty((0, 3), dtype=np.uint8)
        rate = max(0.5, float(self.params.get("pixels_per_second", 120.0)))
        step = int(max(0.0, float(time_elapsed)) * rate)
        if step == self._last_step and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        buffer_index = self._frame_buffer_index
        frame = self.next_frame_buffer(clear=False)
        previous = self._buffer_pixel[buffer_index]
        if previous is not None:
            frame[previous] = 0
        path_index = step % self._path.size
        pixel = int(self._path[path_index])
        frame[pixel] = self._pixel_color(path_index)
        self._buffer_pixel[buffer_index] = pixel
        self._last_step, self._last_frame = step, frame

        dirty = {pixel}
        if self._last_output_pixel is not None:
            dirty.add(self._last_output_pixel)
        self._last_output_pixel = pixel
        return self.rendered_frame(
            frame,
            dirty_ranges=tuple((index, index + 1) for index in sorted(dirty)),
        )

    def get_runtime_stats(self) -> Dict[str, Any]:
        if self._last_output_pixel is None:
            return {"pixel_index": None, "plant_aware": self.plant_aware_enabled()}
        _, height = self.get_strip_info()
        physical_led = self._last_output_pixel % height
        stats = {
            "pixel_index": self._last_output_pixel,
            "strip": self._last_output_pixel // height,
            "led": physical_led,
            "display_row": height - 1 - physical_led,
            "plant_aware": self.plant_aware_enabled(),
        }
        if self.plant_aware_enabled():
            masks = self.get_plant_masks()
            if masks.globes_flat[self._last_output_pixel]:
                layer = "globe"
            elif masks.foliage_flat[self._last_output_pixel]:
                layer = "foliage"
            elif masks.clearance_flat[self._last_output_pixel]:
                layer = "clearance"
            else:
                layer = "clear"
            stats.update({
                "plant_layer": layer,
                "plant_foliage_pixels": masks.foliage_count,
                "plant_globe_pixels": masks.globe_count,
                "plant_globe_regions": masks.globe_regions,
                "plant_mask_error": masks.error,
            })
        return stats

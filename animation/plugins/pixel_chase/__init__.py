"""Sparse multi-pixel chase for validating and decorating the complete wall."""

import colorsys
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np

from animation import AnimationBase


class PixelChaseAnimation(AnimationBase):
    ANIMATION_NAME = "Pixel Chase"
    ANIMATION_DESCRIPTION = "Chases configurable pixels and tails through every physical LED"
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
            "pixel_count": 3,
            "color_mode": "fixed",
            "color_cycle_speed": 0.2,
            "tail_style": "fade",
            "tail_length": 4,
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
        self._buffer_pixels = [np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)]
        self._last_output_pixels = np.empty(0, dtype=np.int32)
        self._last_head_pixels = np.empty(0, dtype=np.int32)
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
            "pixel_count": {
                "type": "int", "min": 1, "max": 32, "default": 3,
                "description": "Evenly spaced chase heads active at once",
            },
            "color_mode": {
                "type": "str", "options": ["fixed", "rainbow"], "default": "fixed",
                "description": "Use the configured RGB color or cycle each chase head through hues",
            },
            "color_cycle_speed": {
                "type": "float", "min": 0.0, "max": 4.0, "default": 0.2,
                "description": "Rainbow color cycles per second",
            },
            "tail_style": {
                "type": "str", "options": ["none", "solid", "fade"], "default": "fade",
                "description": "Disable tails or render solid/fading trails behind each head",
            },
            "tail_length": {
                "type": "int", "min": 0, "max": 32, "default": 4,
                "description": "Maximum trail pixels behind each chase head",
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
            "plant_aware", "plant_modifiers", "plant_clearance", "plant_mask_path",
            "plant_globe_mask_path",
        } & new_params.keys():
            self._rebuild_path()
            for frame in self._frame_buffers:
                frame.fill(0)
            self._buffer_pixels = [
                np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)
            ]
            self._last_output_pixels = np.empty(0, dtype=np.int32)
            self._last_head_pixels = np.empty(0, dtype=np.int32)
            self._last_output_pixel = None
        # Every exposed parameter affects presentation. Invalidate the source-
        # rate key so a live update is visible without waiting for another step.
        self._last_step = None
        self._last_frame = None

    def _pixel_color(self, path_index: int, head_index: int, step: int, rate: float):
        prefix = ""
        if self.plant_aware_enabled():
            kind = int(self._path_kind[path_index])
            if kind == self._FOLIAGE:
                prefix = "plant_foliage_"
            elif kind == self._GLOBE:
                prefix = "plant_globe_"
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        if str(self.params.get("color_mode", "fixed")) == "rainbow" and not prefix:
            count = self._resolved_pixel_count()
            cycle_speed = min(
                4.0, max(0.0, float(self.params.get("color_cycle_speed", 0.2)))
            )
            hue = ((step / rate) * cycle_speed + head_index / count) % 1.0
            rgb = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            return tuple(int(channel * 255.0 * brightness) for channel in rgb)
        return tuple(
            int(max(0, min(255, int(self.params.get(f"{prefix}{channel}", 255)))) * brightness)
            for channel in ("red", "green", "blue")
        )

    def _resolved_pixel_count(self) -> int:
        return min(self._path.size, max(1, min(32, int(self.params.get("pixel_count", 3)))))

    def _resolved_tail(self) -> Tuple[str, int]:
        style = str(self.params.get("tail_style", "fade"))
        if style not in {"none", "solid", "fade"}:
            style = "fade"
        length = min(32, max(0, int(self.params.get("tail_length", 4))))
        return style, 0 if style == "none" else length

    @staticmethod
    def _dirty_ranges(indices: Iterable[int]) -> Tuple[Tuple[int, int], ...]:
        """Compress sparse physical indices into controller-friendly ranges."""
        ordered = np.unique(np.fromiter(indices, dtype=np.int32))
        if ordered.size == 0:
            return ()
        starts = np.r_[ordered[0], ordered[1:][np.diff(ordered) > 1]]
        ends = np.r_[ordered[:-1][np.diff(ordered) > 1] + 1, ordered[-1] + 1]
        return tuple((int(start), int(end)) for start, end in zip(starts, ends))

    def generate_frame(self, time_elapsed: float, frame_count: int):
        if self._path.size == 0:
            return np.empty((0, 3), dtype=np.uint8)
        rate = max(0.5, float(self.params.get("pixels_per_second", 120.0)))
        step = int(max(0.0, float(time_elapsed)) * rate)
        if step == self._last_step and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        buffer_index = self._frame_buffer_index
        frame = self.next_frame_buffer(clear=False)
        previous = self._buffer_pixels[buffer_index]
        if previous.size:
            frame[previous] = 0

        count = self._resolved_pixel_count()
        style, tail_length = self._resolved_tail()
        offsets = (np.arange(count, dtype=np.int64) * self._path.size) // count
        painted = []
        heads = []
        # Paint oldest tail pixels first so every head remains full intensity.
        for depth in range(tail_length, -1, -1):
            intensity = (
                (tail_length - depth + 1) / (tail_length + 1)
                if style == "fade" and depth > 0
                else 1.0
            )
            for head_index, offset in enumerate(offsets):
                path_index = int((step + int(offset) - depth) % self._path.size)
                pixel = int(self._path[path_index])
                color = self._pixel_color(path_index, head_index, step, rate)
                if intensity < 1.0:
                    color = tuple(int(channel * intensity) for channel in color)
                frame[pixel] = color
                painted.append(pixel)
                if depth == 0:
                    heads.append(pixel)

        output_pixels = np.unique(np.asarray(painted, dtype=np.int32))
        self._buffer_pixels[buffer_index] = output_pixels
        self._last_head_pixels = np.asarray(heads, dtype=np.int32)
        self._last_output_pixel = int(heads[0]) if heads else None
        self._last_step, self._last_frame = step, frame

        dirty = self._dirty_ranges(
            np.concatenate((self._last_output_pixels, output_pixels)).tolist()
        )
        self._last_output_pixels = output_pixels
        return self.rendered_frame(
            frame,
            dirty_ranges=dirty,
        )

    def get_runtime_stats(self) -> Dict[str, Any]:
        if self._last_output_pixel is None:
            return {"pixel_index": None, "plant_aware": self.plant_aware_enabled()}
        _, height = self.get_strip_info()
        physical_led = self._last_output_pixel % height
        stats = {
            "pixel_index": self._last_output_pixel,
            "pixel_indices": self._last_head_pixels.tolist(),
            "pixel_count": len(self._last_head_pixels),
            "lit_pixels": int(self._last_output_pixels.size),
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

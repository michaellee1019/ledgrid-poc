"""A camera-mapped parade of recognizable flags from around the world."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import OverlayFrame, coverage_dirty_union
from animation.core.presentation_contracts import ResolvedScene


Color = Tuple[int, int, int]


class WorldFlagsAnimation(AnimationBase):
    ANIMATION_NAME = "World Flags"
    ANIMATION_DESCRIPTION = "A scrolling parade of world flags adapted to the photographed LED wall"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION = "world_flags", 1
    PROVIDER, ROLE = "python", "animation"
    FRAME_FORMAT = "rgba_uint8_premultiplied_strip_major"
    TIMING_POLICY, PALETTE_POLICY = "scaled_context", "preserve"
    CAPABILITIES = frozenset(("flag_identity_colors", "scaled_context"))
    PLANT_MODIFIER_SUPPORT = frozenset()

    CATALOG = (
        ("USA", "United States", "us", ()),
        ("CAN", "Canada", "canada", ()),
        ("MEX", "Mexico", "vertical", ((0, 104, 71), (255, 255, 255), (206, 17, 38))),
        ("BRA", "Brazil", "brazil", ()),
        ("ARG", "Argentina", "horizontal", ((108, 172, 228), (255, 255, 255), (108, 172, 228))),
        ("COL", "Colombia", "horizontal", ((252, 209, 22), (252, 209, 22), (0, 56, 147), (206, 17, 38))),
        ("GBR", "United Kingdom", "uk", ()),
        ("FRA", "France", "vertical", ((0, 35, 149), (255, 255, 255), (237, 41, 57))),
        ("DEU", "Germany", "horizontal", ((0, 0, 0), (221, 0, 0), (255, 206, 0))),
        ("ITA", "Italy", "vertical", ((0, 146, 70), (255, 255, 255), (206, 43, 55))),
        ("ESP", "Spain", "horizontal", ((170, 21, 27), (241, 191, 0), (241, 191, 0), (170, 21, 27))),
        ("IRL", "Ireland", "vertical", ((22, 155, 98), (255, 255, 255), (255, 136, 62))),
        ("NLD", "Netherlands", "horizontal", ((174, 28, 40), (255, 255, 255), (33, 70, 139))),
        ("BEL", "Belgium", "vertical", ((0, 0, 0), (253, 218, 36), (239, 51, 64))),
        ("POL", "Poland", "horizontal", ((255, 255, 255), (220, 20, 60))),
        ("UKR", "Ukraine", "horizontal", ((0, 87, 184), (255, 215, 0))),
        ("SWE", "Sweden", "nordic", ((0, 106, 167), (254, 204, 0))),
        ("NOR", "Norway", "nordic_double", ((186, 12, 47), (255, 255, 255), (0, 32, 91))),
        ("DNK", "Denmark", "nordic", ((198, 12, 48), (255, 255, 255))),
        ("FIN", "Finland", "nordic", ((255, 255, 255), (0, 53, 128))),
        ("GRC", "Greece", "greece", ()),
        ("CHE", "Switzerland", "swiss", ()),
        ("JPN", "Japan", "disc", ((255, 255, 255), (188, 0, 45))),
        ("CHN", "China", "china", ()),
        ("IND", "India", "india", ()),
        ("BGD", "Bangladesh", "disc", ((0, 106, 78), (244, 42, 65))),
        ("IDN", "Indonesia", "horizontal", ((255, 0, 0), (255, 255, 255))),
        ("PHL", "Philippines", "philippines", ()),
        ("THA", "Thailand", "horizontal", ((165, 25, 49), (255, 255, 255), (45, 42, 74), (45, 42, 74), (255, 255, 255), (165, 25, 49))),
        ("VNM", "Vietnam", "china", ((218, 37, 29), (255, 255, 0))),
        ("AUS", "Australia", "australia", ()),
        ("NZL", "New Zealand", "australia", ((0, 36, 125), (204, 20, 43))),
        ("NGA", "Nigeria", "vertical", ((0, 135, 81), (255, 255, 255), (0, 135, 81))),
        ("ZAF", "South Africa", "south_africa", ()),
        ("KEN", "Kenya", "horizontal", ((0, 0, 0), (255, 255, 255), (187, 18, 38), (187, 18, 38), (255, 255, 255), (0, 98, 51))),
        ("EGY", "Egypt", "horizontal", ((206, 17, 38), (255, 255, 255), (0, 0, 0))),
        ("TUR", "Turkey", "disc", ((227, 10, 23), (255, 255, 255))),
        ("ISR", "Israel", "israel", ()),
    )

    DISPLAY_MODES = ("parade", "single")
    COUNTRY_CODES = tuple(item[0] for item in CATALOG)
    DEFAULTS = MappingProxyType({
        "display_mode": "parade",
        "country": "USA",
        "scroll_pixels_per_second": 7.0,
        "flag_height": 21,
        "gap": 3,
        "flip_horizontal": False,
        "flip_vertical": True,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        provider=PROVIDER,
        role=ROLE,
        timing_policy=TIMING_POLICY,
        alpha_behavior="premultiplied_rgba",
        palette_policy=PALETTE_POLICY,
        plant_capabilities=("none",),
        fidelity_exceptions=("flag_identity_colors",),
        defaults=DEFAULTS,
        parameter_normalizer=lambda values: WorldFlagsAnimation._normalized_parameters(values),
    )
    _LEGACY_KEYS = frozenset({
        "speed", "brightness", "color_saturation", "color_value", "map_path",
        "map_mode", "visibility_boost", "plant_aware", "plant_modifiers",
        "plant_clearance", "plant_mask_path", "plant_globe_mask_path",
    })

    def __init__(self, controller, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.strip_count, self.leds_per_strip = self.get_strip_info()
        self._flags = self._render_catalog()
        self._buffers = tuple(
            np.zeros((self.get_pixel_count(), 4), dtype=np.uint8) for _ in range(2)
        )
        self._last_pixels = self._buffers[0]
        self._last_key: Optional[Tuple[Any, ...]] = None
        self._revision = 0
        self._presentation_context: ResolvedScene | None = None

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(values)
        unknown = sorted(set(supplied) - set(cls.DEFAULTS) - cls._LEGACY_KEYS)
        if unknown:
            raise ValueError(f"World Flags does not accept non-local parameters: {unknown!r}")
        result = dict(cls.DEFAULTS)
        result.update({key: supplied[key] for key in cls.DEFAULTS if key in supplied})
        if "scroll_pixels_per_second" not in supplied and "speed" in supplied:
            result["scroll_pixels_per_second"] = supplied["speed"]
        if result["display_mode"] not in cls.DISPLAY_MODES:
            raise ValueError(f"display_mode must be one of {list(cls.DISPLAY_MODES)!r}")
        if result["country"] not in cls.COUNTRY_CODES:
            raise ValueError("country must be a supported ISO code")
        rate = result["scroll_pixels_per_second"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(float(rate)) or not -40.0 <= float(rate) <= 40.0:
            raise ValueError("scroll_pixels_per_second must be a finite number from -40.0 to 40.0")
        result["scroll_pixels_per_second"] = float(rate)
        for name, low, high in (("flag_height", 12, 40), ("gap", 0, 12)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        for name in ("flip_horizontal", "flip_vertical"):
            if not isinstance(result[name], bool):
                raise ValueError(f"{name} must be a bool")
        return result

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "display_mode": {"type": "str", "options": list(self.DISPLAY_MODES), "default": "parade", "description": "Show one flag or a scrolling parade"},
            "country": {"type": "str", "options": list(self.COUNTRY_CODES), "default": "USA", "description": "ISO code used in single mode"},
            "scroll_pixels_per_second": {"type": "float", "min": -40.0, "max": 40.0, "default": 7.0, "description": "Parade travel per scaled second"},
            "flag_height": {"type": "int", "min": 12, "max": 40, "default": 21, "description": "Height of each parade flag"},
            "gap": {"type": "int", "min": 0, "max": 12, "default": 3, "description": "Transparent rows between parade flags"},
            "flip_horizontal": {"type": "bool", "default": False, "description": "Mirror the wall left-to-right"},
            "flip_vertical": {"type": "bool", "default": True, "description": "Mirror the wall top-to-bottom"},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        previous_height = self.params["flag_height"]
        self.params = self._normalized_parameters({**self.params, **dict(new_params)})
        if self.params["flag_height"] != previous_height:
            self._flags = self._render_catalog()
        self._last_key = None

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        del old_context
        descriptor = new_context.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("World Flags received a context for another component")
        if new_context.palette is not None:
            raise ValueError("World Flags preserves flag identity colors")
        self._presentation_context = new_context

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame:
        if dict(context.parameters) != self.params:
            self.update_parameters(context.parameters)
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int):
        del frame_count
        if self._presentation_context is not None:
            self.params = self._normalized_parameters(self._presentation_context.parameters)
            elapsed = self._presentation_context.phase_time
        else:
            elapsed = max(0.0, float(time_elapsed))
        single = self.params["display_mode"] == "single"
        offset = 0
        if not single:
            block = self._flags[0].shape[1] + self.params["gap"]
            offset = int(math.floor(elapsed * self.params["scroll_pixels_per_second"])) % (block * len(self._flags))
        key = (single, self.params["country"], offset, tuple(self.params.items()))
        if key == self._last_key:
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False, dirty_ranges=())
        output = self._buffers[1] if self._last_pixels is self._buffers[0] else self._buffers[0]
        output.fill(0)
        canvas = output.reshape(self.strip_count, self.leds_per_strip, 4)
        if single:
            self._paint_single_plane(canvas)
        else:
            self._paint_parade_plane(canvas, offset)
        if bool(self.params.get("flip_horizontal", False)):
            canvas[:] = canvas[::-1, :, :]
        if bool(self.params.get("flip_vertical", True)):
            canvas[:] = canvas[:, ::-1, :]
        dirty = coverage_dirty_union(self._last_pixels, output)
        self._last_pixels, self._last_key = output, key
        self._revision += 1
        return OverlayFrame(output, revision=self._revision, changed=True, dirty_ranges=dirty)

    def _paint_single_plane(self, canvas: np.ndarray) -> None:
        index = self.COUNTRY_CODES.index(self.params["country"])
        flag = self._flags[index]
        y0 = max(0, (self.leds_per_strip - flag.shape[1]) // 2)
        height = min(flag.shape[1], self.leds_per_strip - y0)
        canvas[:, y0:y0 + height, :3] = flag[:, :height]
        canvas[:, y0:y0 + height, 3] = 255

    def _paint_parade_plane(self, canvas: np.ndarray, offset: int) -> None:
        gap = self.params["gap"]
        block = self._flags[0].shape[1] + gap
        virtual_height = block * len(self._flags)
        for led in range(self.leds_per_strip):
            virtual_y = (led + offset) % virtual_height
            flag_index, row = divmod(virtual_y, block)
            if row < self._flags[flag_index].shape[1]:
                canvas[:, led, :3] = self._flags[flag_index][:, row]
                canvas[:, led, 3] = 255

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            "flag_count": len(self.CATALOG),
            "display_mode": self.params["display_mode"],
            "country": self.params["country"],
        }

    def _single_canvas(self) -> np.ndarray:
        code = str(self.params.get("country", "USA")).upper()
        index = next((i for i, item in enumerate(self.CATALOG) if item[0] == code), 0)
        flag = self._flags[index]
        canvas = np.zeros((self.strip_count, self.leds_per_strip, 3), dtype=np.uint8)
        y0 = max(0, (self.leds_per_strip - flag.shape[1]) // 2)
        canvas[:, y0:y0 + flag.shape[1]] = flag[:, : self.leds_per_strip - y0]
        return canvas

    def _plant_layout_masks(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return masks in the pre-flip coordinate space used to compose flags."""
        masks = self.get_plant_masks()
        obstacle = masks.clearance
        globes = masks.globes
        if bool(self.params.get("flip_horizontal", False)):
            obstacle = obstacle[::-1, :]
            globes = globes[::-1, :]
        if bool(self.params.get("flip_vertical", True)):
            obstacle = obstacle[:, ::-1]
            globes = globes[:, ::-1]
        return obstacle, globes

    @staticmethod
    def _resize_flag(flag: np.ndarray, width: int) -> np.ndarray:
        """Nearest-neighbor resize keeps small flag emblems and hard bands crisp."""
        height = max(1, round(flag.shape[1] * width / max(1, flag.shape[0])))
        xs = np.minimum(
            flag.shape[0] - 1,
            np.floor(np.arange(width) * flag.shape[0] / width).astype(np.intp),
        )
        ys = np.minimum(
            flag.shape[1] - 1,
            np.floor(np.arange(height) * flag.shape[1] / height).astype(np.intp),
        )
        return flag[xs[:, None], ys[None, :]]

    def _plant_flag_candidates(self, flag: np.ndarray) -> Iterable[np.ndarray]:
        minimum_width = min(flag.shape[0], max(16, round(flag.shape[0] * 0.62)))
        widths = []
        for scale in (1.0, 0.875, 0.75, 0.625):
            width = max(minimum_width, round(flag.shape[0] * scale))
            if width not in widths:
                widths.append(width)
        return tuple(flag if width == flag.shape[0] else self._resize_flag(flag, width) for width in widths)

    def _place_plant_banner(
        self,
        canvas: np.ndarray,
        flag: np.ndarray,
        slot_top: int,
        slot_height: int,
        obstacle: np.ndarray,
        globes: np.ndarray,
    ) -> None:
        """Place one recognizable banner where its visible pixels meet least foliage."""
        best = None
        for candidate in self._plant_flag_candidates(flag):
            width, height = candidate.shape[:2]
            spare_y = max(0, slot_height - height)
            y_positions = range(slot_top, slot_top + spare_y + 1)
            for x0 in range(0, self.strip_count - width + 1):
                for y0 in y_positions:
                    x1, y1 = x0 + width, y0 + height
                    visible_y0, visible_y1 = max(0, y0), min(self.leds_per_strip, y1)
                    if visible_y1 <= visible_y0:
                        continue
                    local = obstacle[x0:x1, visible_y0:visible_y1]
                    globe_local = globes[x0:x1, visible_y0:visible_y1]
                    visible_pixels = width * (visible_y1 - visible_y0)
                    overlap = int(np.count_nonzero(local))
                    globe_overlap = int(np.count_nonzero(globe_local))
                    scale_loss = 1.0 - width / flag.shape[0]
                    clipped = 1.0 - visible_pixels / (width * height)
                    score = (
                        (overlap + globe_overlap) / visible_pixels
                        + 0.035 * scale_loss
                        + 0.20 * clipped,
                        overlap,
                        -visible_pixels,
                        abs((x0 + width / 2) - self.strip_count / 2),
                        abs((y0 + height / 2) - (slot_top + slot_height / 2)),
                        x0,
                        y0,
                    )
                    if best is None or score < best[0]:
                        best = (score, candidate, x0, y0, overlap, visible_pixels)

        if best is None:
            return
        _, candidate, x0, y0, overlap, visible_pixels = best
        source_y0 = max(0, -y0)
        source_y1 = min(candidate.shape[1], self.leds_per_strip - y0)
        canvas[x0:x0 + candidate.shape[0], max(0, y0):min(self.leds_per_strip, y0 + candidate.shape[1])] = candidate[:, source_y0:source_y1]
        self._plant_banner_overlap += overlap
        self._plant_banner_pixels += visible_pixels

    def _plant_single_canvas(self) -> np.ndarray:
        code = str(self.params.get("country", "USA")).upper()
        index = next((i for i, item in enumerate(self.CATALOG) if item[0] == code), 0)
        flag = self._flags[index]
        key = (
            "single", code, flag.shape,
            bool(self.params.get("flip_horizontal", False)),
            bool(self.params.get("flip_vertical", True)),
            int(self.params.get("plant_clearance", 1)),
            str(self.params.get("plant_mask_path", "")),
            str(self.params.get("plant_globe_mask_path", "")),
        )
        if key == self._plant_canvas_key and self._plant_canvas is not None:
            return self._plant_canvas
        canvas = np.zeros((self.strip_count, self.leds_per_strip, 3), dtype=np.uint8)
        obstacle, globes = self._plant_layout_masks()
        self._plant_banner_overlap = 0
        self._plant_banner_pixels = 0
        # A single flag may use the whole wall as its placement slot. This is the
        # informational mode, so keeping the emblem visible wins over centering.
        self._place_plant_banner(canvas, flag, 0, self.leds_per_strip, obstacle, globes)
        self._plant_canvas_key = key
        self._plant_canvas = canvas
        return canvas

    def _plant_parade_canvas(self, time_elapsed: float) -> np.ndarray:
        gap = max(0, int(self.params.get("gap", 3)))
        flag_height = self._flags[0].shape[1]
        block = flag_height + gap
        virtual_height = block * len(self._flags)
        speed = float(self.params.get("speed", 7.0))
        offset = int(math.floor(time_elapsed * speed)) % virtual_height
        key = (
            "parade", offset, flag_height, gap,
            bool(self.params.get("flip_horizontal", False)),
            bool(self.params.get("flip_vertical", True)),
            int(self.params.get("plant_clearance", 1)),
            str(self.params.get("plant_mask_path", "")),
            str(self.params.get("plant_globe_mask_path", "")),
        )
        if key == self._plant_canvas_key and self._plant_canvas is not None:
            return self._plant_canvas
        canvas = np.zeros((self.strip_count, self.leds_per_strip, 3), dtype=np.uint8)
        obstacle, globes = self._plant_layout_masks()
        self._plant_banner_overlap = 0
        self._plant_banner_pixels = 0
        for flag_index, flag in enumerate(self._flags):
            virtual_top = flag_index * block
            # At most two copies can intersect a physical viewport, but this
            # bounded range also handles unusually short test geometries.
            first_copy = math.floor((offset - virtual_top - block) / virtual_height)
            last_copy = math.ceil((offset + self.leds_per_strip - virtual_top) / virtual_height)
            for copy in range(first_copy, last_copy + 1):
                slot_top = virtual_top - offset + copy * virtual_height
                if slot_top < self.leds_per_strip and slot_top + block > 0:
                    self._place_plant_banner(
                        canvas, flag, slot_top, block, obstacle, globes
                    )
        self._plant_canvas_key = key
        self._plant_canvas = canvas
        return canvas

    def _parade_canvas(self, time_elapsed: float) -> np.ndarray:
        gap = max(0, int(self.params.get("gap", 3)))
        block = self._flags[0].shape[1] + gap
        virtual_height = block * len(self._flags)
        speed = float(self.params.get("speed", 7.0))
        offset = int(math.floor(time_elapsed * speed)) % virtual_height
        canvas = np.zeros((self.strip_count, self.leds_per_strip, 3), dtype=np.uint8)
        for y in range(self.leds_per_strip):
            virtual_y = (y + offset) % virtual_height
            flag_index, row = divmod(virtual_y, block)
            if row < self._flags[flag_index].shape[1]:
                canvas[:, y] = self._flags[flag_index][:, row]
        return canvas

    def _render_catalog(self) -> Tuple[np.ndarray, ...]:
        height = max(12, min(40, int(self.params.get("flag_height", 21))))
        return tuple(self._render_flag(kind, colors, self.strip_count, height) for _, _, kind, colors in self.CATALOG)

    @staticmethod
    def _bands(canvas: np.ndarray, colors: Sequence[Color], vertical: bool) -> None:
        size = canvas.shape[0] if vertical else canvas.shape[1]
        for index, color in enumerate(colors):
            start, end = round(index * size / len(colors)), round((index + 1) * size / len(colors))
            if vertical:
                canvas[start:end, :] = color
            else:
                canvas[:, start:end] = color

    @staticmethod
    def _disc(canvas: np.ndarray, color: Color, center=(0.5, 0.5), radius=0.22) -> None:
        width, height = canvas.shape[:2]
        xx, yy = np.ogrid[:width, :height]
        mask = ((xx / width - center[0]) ** 2 + (yy / height - center[1]) ** 2) <= radius ** 2
        canvas[mask] = color

    @staticmethod
    def _star(canvas: np.ndarray, color: Color, center=(0.5, 0.5), radius=0.17) -> None:
        width, height = canvas.shape[:2]
        cx, cy = center[0] * width, center[1] * height
        for x in range(width):
            for y in range(height):
                angle = math.atan2(y - cy, x - cx)
                distance = math.hypot((x - cx) / max(1, width), (y - cy) / max(1, height))
                edge = radius * (0.42 if int((angle + math.pi) / (math.pi / 5)) % 2 else 1.0)
                if distance <= edge:
                    canvas[x, y] = color

    @classmethod
    def _render_flag(cls, kind: str, colors: Sequence[Color], width: int, height: int) -> np.ndarray:
        c = np.zeros((width, height, 3), dtype=np.uint8)
        if kind == "vertical":
            cls._bands(c, colors, True)
        elif kind == "horizontal":
            cls._bands(c, colors, False)
        elif kind == "disc":
            c[:] = colors[0]
            cls._disc(c, colors[1], center=(0.5 if colors[0] == (255, 255, 255) else 0.44, 0.5), radius=0.23)
        elif kind in {"nordic", "nordic_double"}:
            c[:] = colors[0]
            x, y, thickness = round(width * 0.38), height // 2, max(1, height // 7)
            c[max(0, x - thickness):x + thickness + 1] = colors[1]
            c[:, max(0, y - thickness):y + thickness + 1] = colors[1]
            if kind == "nordic_double":
                inner = max(1, thickness // 2)
                c[x - inner:x + inner + 1] = colors[2]
                c[:, y - inner:y + inner + 1] = colors[2]
        elif kind == "us":
            cls._bands(c, ((178, 34, 52), (255, 255, 255)) * 7, False)
            c[: round(width * 0.42), : round(height * 0.54)] = (60, 59, 110)
            c[1:round(width * 0.42):3, 1:round(height * 0.54):3] = (255, 255, 255)
        elif kind == "uk":
            c[:] = (1, 33, 105)
            for x in range(width):
                y = round(x * height / width)
                c[max(0, x - 1):x + 2, max(0, y - 1):y + 2] = (255, 255, 255)
                y2 = height - 1 - y
                c[max(0, x - 1):x + 2, max(0, y2 - 1):y2 + 2] = (255, 255, 255)
            c[width // 2 - 2:width // 2 + 2] = (200, 16, 46)
            c[:, height // 2 - 2:height // 2 + 2] = (200, 16, 46)
        elif kind == "brazil":
            c[:] = (0, 156, 59)
            for x in range(width):
                half = int((1.0 - abs(x / max(1, width - 1) - 0.5) * 2.0) * height * 0.42)
                c[x, height // 2 - half:height // 2 + half + 1] = (255, 223, 0)
            cls._disc(c, (0, 39, 118), radius=0.19)
        elif kind == "canada":
            c[:] = (255, 255, 255)
            c[: width // 4] = (255, 0, 0)
            c[width - width // 4:] = (255, 0, 0)
            cls._star(c, (255, 0, 0), radius=0.16)
        elif kind == "swiss":
            c[:] = (218, 41, 28)
            c[width // 2 - 2:width // 2 + 2, height // 4:3 * height // 4] = (255, 255, 255)
            c[width // 3:2 * width // 3, height // 2 - 2:height // 2 + 2] = (255, 255, 255)
        elif kind == "china":
            background, star = colors if colors else ((222, 41, 16), (255, 222, 0))
            c[:] = background
            cls._star(c, star, center=(0.22, 0.30), radius=0.14)
        elif kind == "india":
            cls._bands(c, ((255, 153, 51), (255, 255, 255), (19, 136, 8)), False)
            cls._disc(c, (0, 0, 128), radius=0.10)
            cls._disc(c, (255, 255, 255), radius=0.055)
        elif kind == "greece":
            cls._bands(c, ((13, 94, 175), (255, 255, 255)) * 5, False)
            c[: width // 2, : height // 2] = (13, 94, 175)
            c[width // 4 - 1:width // 4 + 2, : height // 2] = (255, 255, 255)
            c[: width // 2, height // 4 - 1:height // 4 + 2] = (255, 255, 255)
        elif kind == "philippines":
            cls._bands(c, ((0, 56, 168), (206, 17, 38)), False)
            for x in range(width // 2):
                half = round((1 - x / max(1, width // 2)) * height / 2)
                c[x, height // 2 - half:height // 2 + half + 1] = (255, 255, 255)
            cls._disc(c, (252, 209, 22), center=(0.13, 0.5), radius=0.07)
        elif kind == "australia":
            background, star = colors if colors else ((0, 36, 125), (255, 255, 255))
            c[:] = background
            cls._star(c, star, center=(0.70, 0.58), radius=0.09)
            cls._star(c, star, center=(0.84, 0.26), radius=0.06)
            c[: width // 2, : height // 2] = cls._render_flag("uk", (), max(1, width // 2), max(1, height // 2))
        elif kind == "israel":
            c[:] = (255, 255, 255)
            c[:, 2:4] = (0, 56, 184)
            c[:, height - 4:height - 2] = (0, 56, 184)
            cls._star(c, (0, 56, 184), radius=0.13)
        elif kind == "south_africa":
            c[:] = (0, 122, 77)
            c[:, : height // 3] = (222, 56, 49)
            c[:, 2 * height // 3:] = (0, 35, 149)
            for x in range(width // 2):
                half = round((1 - x / max(1, width // 2)) * height / 2)
                c[x, height // 2 - half:height // 2 + half + 1] = (0, 0, 0)
        else:
            c[:] = (255, 0, 255)
        return c

    def _load_map(self) -> None:
        self._visibility.fill(1.0)
        self._occluded.fill(False)
        self._map_error = ""
        path = Path(str(self.params.get("map_path", "config/webcam_pixel_map.json")))
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("geometry", {}).get("total_leds") != self.get_pixel_count():
                raise ValueError("pixel-map geometry does not match the controller")
            for pixel in payload.get("pixels", []):
                index = int(pixel["index"])
                if 0 <= index < self.get_pixel_count():
                    self._visibility[index] = max(0.0, min(1.0, float(pixel.get("visibility", 1.0))))
                    self._occluded[index] = bool(pixel.get("occluded", False))
        except Exception as exc:
            self._map_error = str(exc)

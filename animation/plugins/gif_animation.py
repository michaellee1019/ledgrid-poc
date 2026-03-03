#!/usr/bin/env python3
"""
GIF Animation Plugin

Loads animated GIFs from disk and plays them on the LED wall.
"""

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from animation import AnimationBase

try:
    from PIL import Image, ImageSequence
except ImportError:  # pragma: no cover - handled gracefully at runtime
    Image = None
    ImageSequence = None


Color = Tuple[int, int, int]


class GifAnimation(AnimationBase):
    """Play pre-rendered animated GIF files."""

    ANIMATION_NAME = "GIF Animation"
    ANIMATION_DESCRIPTION = "Plays a GIF from a filesystem directory with frame timing"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "gif_directory": "assets/gifs",
            "gif_name": "penguin_top_center.gif",
            "gif_index": 0,
            "playback_speed": 1.0,
            "brightness": 1.0,
            "brightness_mode": "rgb",  # "rgb" or "luma"
            "brightness_floor": 0.0,
            "gamma": 1.0,
            "flip_y": True,
            "fit_mode": "stretch",  # "stretch", "contain", "cover"
            "contain_background": 0,
        })
        self.params = {**self.default_params, **self.config}

        self._frames: List[List[Color]] = []
        self._durations_sec: List[float] = []
        self._current_frame_index = 0
        self._next_frame_time = 0.0
        self._loaded_gif_path: Optional[Path] = None
        self._load_error: Optional[str] = None

        self._load_selected_gif()

    def start(self):
        super().start()
        self._current_frame_index = 0
        self._next_frame_time = 0.0

    def update_parameters(self, new_params: Dict[str, Any]):
        old_directory = str(self.params.get("gif_directory", ""))
        old_name = str(self.params.get("gif_name", ""))
        old_index = int(self.params.get("gif_index", 0) or 0)
        old_fit_mode = str(self.params.get("fit_mode", "stretch"))

        super().update_parameters(new_params)

        directory_changed = old_directory != str(self.params.get("gif_directory", ""))
        name_changed = old_name != str(self.params.get("gif_name", ""))
        index_changed = old_index != int(self.params.get("gif_index", 0) or 0)
        fit_mode_changed = old_fit_mode != str(self.params.get("fit_mode", "stretch"))
        if directory_changed or name_changed or index_changed or fit_mode_changed:
            self._load_selected_gif()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "gif_directory": {
                "type": "str",
                "default": "assets/gifs",
                "description": "Directory containing GIF files",
            },
            "gif_name": {
                "type": "str",
                "default": "",
                "description": "GIF filename (or stem). Empty selects by index.",
            },
            "gif_index": {
                "type": "int",
                "min": 0,
                "max": 999,
                "default": 0,
                "description": "Index into sorted GIF list when gif_name is empty.",
            },
            "playback_speed": {
                "type": "float",
                "min": 0.1,
                "max": 4.0,
                "default": 1.0,
                "description": "GIF playback speed multiplier.",
            },
            "brightness_mode": {
                "type": "str",
                "default": "rgb",
                "description": "rgb = direct RGB scaling, luma = separate hue/chroma and brightness.",
            },
            "brightness_floor": {
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.0,
                "description": "Minimum per-pixel brightness in luma mode.",
            },
            "gamma": {
                "type": "float",
                "min": 0.2,
                "max": 3.0,
                "default": 1.0,
                "description": "Gamma correction exponent.",
            },
            "flip_y": {
                "type": "bool",
                "default": True,
                "description": "Flip vertical axis so GIF top maps to wall top.",
            },
            "fit_mode": {
                "type": "str",
                "default": "stretch",
                "description": "stretch, contain, or cover frame fit.",
            },
            "contain_background": {
                "type": "int",
                "min": 0,
                "max": 255,
                "default": 0,
                "description": "Background level used by contain fit mode.",
            },
        })
        return schema

    def get_runtime_stats(self) -> Dict[str, Any]:
        available = self._list_gif_files()
        return {
            "loaded_gif": str(self._loaded_gif_path) if self._loaded_gif_path else None,
            "available_gifs": [p.name for p in available],
            "frame_count": len(self._frames),
            "current_frame_index": self._current_frame_index,
            "load_error": self._load_error,
        }

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        total = self.get_pixel_count()
        if not self._frames:
            return [(0, 0, 0)] * total

        now = time_elapsed
        speed = max(0.1, float(self.params.get("playback_speed", 1.0) or 1.0))

        if self._next_frame_time <= 0.0:
            duration = self._durations_sec[self._current_frame_index] / speed
            self._next_frame_time = now + duration
        else:
            while now >= self._next_frame_time and self._frames:
                self._current_frame_index = (self._current_frame_index + 1) % len(self._frames)
                duration = self._durations_sec[self._current_frame_index] / speed
                self._next_frame_time += duration

        source = self._frames[self._current_frame_index]
        return self._apply_output_adjustments(source)

    def _apply_output_adjustments(self, source: Sequence[Color]) -> List[Color]:
        brightness = max(0.0, min(1.0, float(self.params.get("brightness", 1.0) or 0.0)))
        gamma = max(0.2, float(self.params.get("gamma", 1.0) or 1.0))
        brightness_mode = str(self.params.get("brightness_mode", "rgb")).strip().lower()
        brightness_floor = max(0.0, min(1.0, float(self.params.get("brightness_floor", 0.0) or 0.0)))

        if brightness <= 0.0:
            return [(0, 0, 0)] * len(source)

        out: List[Color] = []
        for r, g, b in source:
            if brightness_mode == "luma":
                max_channel = max(r, g, b)
                if max_channel <= 0:
                    out.append((0, 0, 0))
                    continue
                value = max_channel / 255.0
                value = brightness_floor + (1.0 - brightness_floor) * value
                scale = value * brightness
                rr = (r / max_channel) * scale
                gg = (g / max_channel) * scale
                bb = (b / max_channel) * scale
                out.append((
                    self._gamma_to_byte(rr, gamma),
                    self._gamma_to_byte(gg, gamma),
                    self._gamma_to_byte(bb, gamma),
                ))
            else:
                out.append((
                    self._gamma_to_byte((r / 255.0) * brightness, gamma),
                    self._gamma_to_byte((g / 255.0) * brightness, gamma),
                    self._gamma_to_byte((b / 255.0) * brightness, gamma),
                ))

        return out

    @staticmethod
    def _gamma_to_byte(value_0_to_1: float, gamma: float) -> int:
        value = max(0.0, min(1.0, value_0_to_1))
        corrected = math.pow(value, gamma)
        return max(0, min(255, int(round(corrected * 255.0))))

    def _load_selected_gif(self):
        self._load_error = None
        self._frames = []
        self._durations_sec = []
        self._current_frame_index = 0
        self._next_frame_time = 0.0
        self._loaded_gif_path = None

        if Image is None or ImageSequence is None:
            self._load_error = "Pillow is not installed; GIF animation plugin unavailable."
            return

        directory = Path(str(self.params.get("gif_directory", "assets/gifs"))).expanduser()
        if not directory.is_absolute():
            directory = Path.cwd() / directory
        if not directory.exists():
            self._load_error = f"GIF directory does not exist: {directory}"
            return

        gif_files = self._list_gif_files(directory)
        if not gif_files:
            self._load_error = f"No .gif files found in: {directory}"
            return

        selected = self._select_gif(gif_files)
        if selected is None:
            self._load_error = f"GIF selection failed in: {directory}"
            return

        try:
            frames, durations = self._decode_gif(selected)
        except Exception as exc:  # pragma: no cover - runtime data dependent
            self._load_error = f"Failed to decode GIF {selected.name}: {exc}"
            return

        if not frames:
            self._load_error = f"Decoded GIF has no frames: {selected.name}"
            return

        self._frames = frames
        self._durations_sec = durations
        self._loaded_gif_path = selected

    def _list_gif_files(self, directory: Optional[Path] = None) -> List[Path]:
        target = directory
        if target is None:
            target = Path(str(self.params.get("gif_directory", "assets/gifs"))).expanduser()
            if not target.is_absolute():
                target = Path.cwd() / target
        if not target.exists():
            return []
        return sorted([p for p in target.iterdir() if p.is_file() and p.suffix.lower() == ".gif"])

    def _select_gif(self, gif_files: Sequence[Path]) -> Optional[Path]:
        requested_name = str(self.params.get("gif_name", "")).strip()
        if requested_name:
            exact = [p for p in gif_files if p.name == requested_name or p.stem == requested_name]
            if exact:
                return exact[0]
            lowered = requested_name.lower()
            fuzzy = [p for p in gif_files if lowered in p.name.lower() or lowered in p.stem.lower()]
            if fuzzy:
                return fuzzy[0]
            return None

        idx = int(self.params.get("gif_index", 0) or 0)
        idx = max(0, min(len(gif_files) - 1, idx))
        return gif_files[idx]

    def _decode_gif(self, gif_path: Path) -> Tuple[List[List[Color]], List[float]]:
        strip_count, leds_per_strip = self.get_strip_info()
        fit_mode = str(self.params.get("fit_mode", "stretch")).strip().lower()
        if fit_mode not in {"stretch", "contain", "cover"}:
            fit_mode = "stretch"
        contain_background = int(self.params.get("contain_background", 0) or 0)
        contain_background = max(0, min(255, contain_background))
        flip_y = bool(self.params.get("flip_y", True))

        frames: List[List[Color]] = []
        durations: List[float] = []

        with Image.open(gif_path) as img:
            for frame in ImageSequence.Iterator(img):
                rgba = frame.convert("RGBA")
                fitted = self._fit_frame(
                    rgba,
                    width=strip_count,
                    height=leds_per_strip,
                    fit_mode=fit_mode,
                    contain_background=contain_background,
                )
                rgb = fitted.convert("RGB")
                frames.append(self._flatten_frame(rgb, flip_y=flip_y))

                duration_ms = frame.info.get("duration", img.info.get("duration", 100))
                duration = max(0.01, float(duration_ms or 100) / 1000.0)
                durations.append(duration)

        return frames, durations

    @staticmethod
    def _fit_frame(
        image: "Image.Image",
        width: int,
        height: int,
        fit_mode: str,
        contain_background: int,
    ) -> "Image.Image":
        if fit_mode == "stretch":
            return image.resize((width, height), Image.Resampling.BILINEAR)

        src_w, src_h = image.size
        if src_w <= 0 or src_h <= 0:
            return Image.new("RGBA", (width, height), (0, 0, 0, 255))

        if fit_mode == "cover":
            scale = max(width / src_w, height / src_h)
        else:
            scale = min(width / src_w, height / src_h)

        new_w = max(1, int(round(src_w * scale)))
        new_h = max(1, int(round(src_h * scale)))
        resized = image.resize((new_w, new_h), Image.Resampling.BILINEAR)

        bg = contain_background
        canvas = Image.new("RGBA", (width, height), (bg, bg, bg, 255))
        offset_x = (width - new_w) // 2
        offset_y = (height - new_h) // 2
        canvas.alpha_composite(resized, (offset_x, offset_y))
        return canvas

    @staticmethod
    def _flatten_frame(image_rgb: "Image.Image", flip_y: bool) -> List[Color]:
        width, height = image_rgb.size
        px = image_rgb.load()
        out: List[Color] = [(0, 0, 0)] * (width * height)
        for strip in range(width):
            for led in range(height):
                y = (height - 1 - led) if flip_y else led
                r, g, b = px[strip, y]
                out[strip * height + led] = (int(r), int(g), int(b))
        return out

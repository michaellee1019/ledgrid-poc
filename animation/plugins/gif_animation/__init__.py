#!/usr/bin/env python3
"""
GIF Animation Plugin

Loads animated GIFs from disk and plays them on the LED wall.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np

from animation import AnimationBase

try:
    from PIL import Image, ImageSequence
except ImportError:  # pragma: no cover - handled gracefully at runtime
    Image = None
    ImageSequence = None


Color = Tuple[int, int, int]
PACKAGED_GIF_DIRECTORY = Path(__file__).resolve().parent / "assets"
DEFAULT_GIF_DIRECTORY = "animation/plugins/gif_animation/assets"
COMPAT_GIF_DIRECTORY = "assets/gifs"


class GifAnimation(AnimationBase):
    """Play pre-rendered animated GIF files."""

    ANIMATION_NAME = "GIF Animation"
    ANIMATION_DESCRIPTION = "Plays a GIF from a filesystem directory with frame timing"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "gif_directory": DEFAULT_GIF_DIRECTORY,
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
            # Plant-aware composition is opt-in and does not alter default output.
            "plant_gif_offset_radius": 4,
            "plant_foliage_dim": 0.32,
            "plant_globe_dim": 0.18,
            "plant_accent_strength": 0.16,
        })
        self.params = {**self.default_params, **self.config}

        self._frames: List[List[Color]] = []
        self._durations_sec: List[float] = []
        self._current_frame_index = 0
        self._next_frame_time = 0.0
        self._loaded_gif_path: Optional[Path] = None
        self._load_error: Optional[str] = None
        self._adjusted_frame_cache: Dict[Tuple[int, Tuple[Any, ...]], np.ndarray] = {}
        self._last_output_key: Optional[Tuple[int, Tuple[Any, ...]]] = None
        self._plant_offset_key: Optional[Tuple[Any, ...]] = None
        self._plant_offset = (0, 0)
        self._empty_frame = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)

        self._load_selected_gif()

    def start(self):
        super().start()
        self._current_frame_index = 0
        self._next_frame_time = 0.0
        self._last_output_key = None

    def update_parameters(self, new_params: Dict[str, Any]):
        old_directory = str(self.params.get("gif_directory", ""))
        old_name = str(self.params.get("gif_name", ""))
        old_index = int(self.params.get("gif_index", 0) or 0)
        old_fit_mode = str(self.params.get("fit_mode", "stretch"))

        super().update_parameters(new_params)
        adjustment_keys = {
            "brightness", "gamma", "brightness_mode", "brightness_floor",
            "plant_aware", "plant_clearance", "plant_mask_path",
            "plant_globe_mask_path", "plant_gif_offset_radius",
            "plant_foliage_dim", "plant_globe_dim", "plant_accent_strength",
        }
        if adjustment_keys & new_params.keys():
            self._adjusted_frame_cache.clear()
            self._last_output_key = None
            self._plant_offset_key = None

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
                "default": DEFAULT_GIF_DIRECTORY,
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
            "plant_gif_offset_radius": {
                "type": "int", "min": 0, "max": 8, "default": 4,
                "description": "Maximum translation used to keep salient GIF content off plants.",
            },
            "plant_foliage_dim": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.32,
                "description": "Occlusion dimming applied where GIF content still crosses foliage.",
            },
            "plant_globe_dim": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.18,
                "description": "Occlusion dimming applied where GIF content still crosses globes.",
            },
            "plant_accent_strength": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.16,
                "description": "Green/magenta landmark tint mixed into foliage/globe pixels.",
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
            "plant_aware": self.plant_aware_enabled(),
            "plant_content_offset": self._plant_offset if self.plant_aware_enabled() else (0, 0),
        }

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        if not self._frames:
            return self.rendered_frame(self._empty_frame, changed=frame_count == 0)

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

        adjustment_key = self._adjustment_key()
        output_key = (self._current_frame_index, adjustment_key)
        frame = self._adjusted_frame_cache.get(output_key)
        if frame is None:
            source = self._frames[self._current_frame_index]
            frame = self._apply_output_adjustments(source)
            self._adjusted_frame_cache[output_key] = frame

        changed = output_key != self._last_output_key
        self._last_output_key = output_key
        return self.rendered_frame(frame, changed=changed)

    def _adjustment_key(self) -> Tuple[Any, ...]:
        key = (
            max(0.0, min(1.0, float(self.params.get("brightness", 1.0) or 0.0))),
            max(0.2, float(self.params.get("gamma", 1.0) or 1.0)),
            str(self.params.get("brightness_mode", "rgb")).strip().lower(),
            max(0.0, min(1.0, float(self.params.get("brightness_floor", 0.0) or 0.0))),
        )
        if not self.plant_aware_enabled():
            return key
        return key + (
            True,
            max(0, min(8, int(self.params.get("plant_gif_offset_radius", 4) or 0))),
            max(0.0, min(1.0, float(self.params.get("plant_foliage_dim", 0.32) or 0.0))),
            max(0.0, min(1.0, float(self.params.get("plant_globe_dim", 0.18) or 0.0))),
            max(0.0, min(1.0, float(self.params.get("plant_accent_strength", 0.16) or 0.0))),
            int(self.params.get("plant_clearance", 1) or 0),
            str(self.params.get("plant_mask_path", "")),
            str(self.params.get("plant_globe_mask_path", "")),
        )

    def _apply_output_adjustments(self, source: Sequence[Color]) -> np.ndarray:
        brightness = max(0.0, min(1.0, float(self.params.get("brightness", 1.0) or 0.0)))
        gamma = max(0.2, float(self.params.get("gamma", 1.0) or 1.0))
        brightness_mode = str(self.params.get("brightness_mode", "rgb")).strip().lower()
        brightness_floor = max(0.0, min(1.0, float(self.params.get("brightness_floor", 0.0) or 0.0)))

        if brightness <= 0.0:
            return np.zeros((len(source), 3), dtype=np.uint8)

        source_array = np.asarray(source, dtype=np.uint8)
        if self.plant_aware_enabled():
            source_array = self._apply_plant_composition(source_array)
        if brightness_mode != "luma" and brightness >= 1.0 and gamma == 1.0:
            return source_array

        normalized = source_array.astype(np.float32)
        if brightness_mode == "luma":
            max_channel = np.max(normalized, axis=1)
            chroma = np.zeros_like(normalized)
            np.divide(
                normalized,
                max_channel[:, None],
                out=chroma,
                where=max_channel[:, None] > 0,
            )
            value = brightness_floor + (1.0 - brightness_floor) * (max_channel / 255.0)
            normalized = chroma * (value * brightness)[:, None]
        else:
            normalized *= brightness / 255.0

        np.clip(normalized, 0.0, 1.0, out=normalized)
        np.power(normalized, gamma, out=normalized)
        normalized *= 255.0
        return np.rint(normalized).astype(np.uint8)

    def _plant_content_offset(self) -> Tuple[int, int]:
        """Choose one stable translation for the whole loop, avoiding frame jitter."""
        radius = max(0, min(8, int(self.params.get("plant_gif_offset_radius", 4) or 0)))
        masks = self.get_plant_masks()
        key = (radius, id(masks), id(self._frames))
        if key == self._plant_offset_key:
            return self._plant_offset

        width, height = self.get_strip_info()
        if radius == 0 or not self._frames or not np.any(masks.clearance):
            self._plant_offset_key, self._plant_offset = key, (0, 0)
            return self._plant_offset

        # Bright, saturated, or locally contrasted pixels are more likely to be
        # the useful subject than a flat backdrop. Average the loop so motion
        # cannot make the selected placement wobble from frame to frame.
        energy = np.zeros((width, height), dtype=np.float32)
        sample_step = max(1, len(self._frames) // 12)
        sampled = self._frames[::sample_step]
        for frame in sampled:
            rgb = np.asarray(frame, dtype=np.float32).reshape(width, height, 3)
            high = np.max(rgb, axis=2)
            low = np.min(rgb, axis=2)
            luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
            detail = np.zeros_like(luma)
            detail[1:] += np.abs(luma[1:] - luma[:-1])
            detail[:, 1:] += np.abs(luma[:, 1:] - luma[:, :-1])
            energy += luma * (0.2 + 0.8 * (high - low) / 255.0) + detail
        energy /= float(len(sampled))

        total = float(np.sum(energy))
        best = (float(np.sum(energy[masks.clearance])), 0, 0, 0)
        best_offset = (0, 0)
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                shifted = self._translate_grid(energy, dx, dy)
                visible = float(np.sum(shifted))
                occluded = float(np.sum(shifted[masks.clearance]))
                # Cropping is allowed only when its benefit outweighs lost subject.
                score = occluded + max(0.0, total - visible) * 0.65
                rank = (score, abs(dx) + abs(dy), abs(dy), abs(dx))
                if rank < best:
                    best, best_offset = rank, (dx, dy)
        self._plant_offset_key, self._plant_offset = key, best_offset
        return best_offset

    @staticmethod
    def _translate_grid(grid: np.ndarray, dx: int, dy: int) -> np.ndarray:
        """Translate strip/LED data without wrapping content around an edge."""
        shifted = np.zeros_like(grid)
        width, height = grid.shape[:2]
        src_x0, src_x1 = max(0, -dx), min(width, width - dx)
        src_y0, src_y1 = max(0, -dy), min(height, height - dy)
        if src_x1 > src_x0 and src_y1 > src_y0:
            shifted[src_x0 + dx:src_x1 + dx, src_y0 + dy:src_y1 + dy] = \
                grid[src_x0:src_x1, src_y0:src_y1]
        return shifted

    def _apply_plant_composition(self, source: np.ndarray) -> np.ndarray:
        width, height = self.get_strip_info()
        masks = self.get_plant_masks()
        dx, dy = self._plant_content_offset()
        composed = self._translate_grid(source.reshape(width, height, 3), dx, dy)
        working = composed.astype(np.float32)
        foliage_dim = max(0.0, min(1.0, float(self.params.get("plant_foliage_dim", 0.32))))
        globe_dim = max(0.0, min(1.0, float(self.params.get("plant_globe_dim", 0.18))))
        accent = max(0.0, min(1.0, float(self.params.get("plant_accent_strength", 0.16))))

        working[masks.foliage] *= 1.0 - foliage_dim
        working[masks.globes] *= 1.0 - globe_dim
        if accent > 0.0:
            # Low-level semantic landmarks remain visible even over dark media.
            foliage_color = np.asarray((18.0, 74.0, 34.0), dtype=np.float32)
            globe_color = np.asarray((82.0, 20.0, 76.0), dtype=np.float32)
            working[masks.foliage] = (
                working[masks.foliage] * (1.0 - accent) + foliage_color * accent
            )
            working[masks.globes] = (
                working[masks.globes] * (1.0 - accent) + globe_color * accent
            )
        return np.rint(np.clip(working, 0.0, 255.0)).astype(np.uint8).reshape(-1, 3)

    def _load_selected_gif(self):
        self._adjusted_frame_cache.clear()
        self._last_output_key = None
        self._load_error = None
        self._frames = []
        self._durations_sec = []
        self._current_frame_index = 0
        self._next_frame_time = 0.0
        self._loaded_gif_path = None
        self._plant_offset_key = None

        if Image is None or ImageSequence is None:
            self._load_error = "Pillow is not installed; GIF animation plugin unavailable."
            return

        directory = self._resolve_gif_directory()
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

        self._frames = [np.asarray(frame, dtype=np.uint8) for frame in frames]
        self._durations_sec = durations
        self._loaded_gif_path = selected

    def _list_gif_files(self, directory: Optional[Path] = None) -> List[Path]:
        target = directory
        if target is None:
            target = self._resolve_gif_directory()
        if not target.exists():
            return []
        return sorted([p for p in target.iterdir() if p.is_file() and p.suffix.lower() == ".gif"])

    def _resolve_gif_directory(self) -> Path:
        """Resolve configured media paths, including the supported alias."""
        configured = str(self.params.get("gif_directory", DEFAULT_GIF_DIRECTORY))
        if configured in {COMPAT_GIF_DIRECTORY, DEFAULT_GIF_DIRECTORY}:
            return PACKAGED_GIF_DIRECTORY
        directory = Path(configured).expanduser()
        if not directory.is_absolute():
            directory = Path.cwd() / directory
        compatibility_path = (Path.cwd() / COMPAT_GIF_DIRECTORY).resolve()
        if directory.resolve() == compatibility_path:
            return PACKAGED_GIF_DIRECTORY
        return directory

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

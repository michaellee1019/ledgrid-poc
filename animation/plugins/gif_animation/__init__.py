#!/usr/bin/env python3
"""
GIF Animation Plugin

Loads animated GIFs from disk and plays them on the LED wall.
"""

from pathlib import Path
import math
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import OverlayFrame, coverage_dirty_union
from animation.core.presentation_contracts import ResolvedScene

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
    ANIMATION_DESCRIPTION = "Plays packaged GIF artwork with its authored frame timing"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION = "gif_animation", 1
    PROVIDER, ROLE = "python", "animation"
    FRAME_FORMAT = "rgba_uint8_premultiplied_strip_major"
    TIMING_POLICY, PALETTE_POLICY = "scaled_context", "preserve"
    CAPABILITIES = frozenset(("authored_media_colors", "scaled_context"))
    PLANT_MODIFIER_SUPPORT = frozenset()
    FIT_MODES = ("stretch", "contain", "cover")
    DEFAULTS = MappingProxyType({
        "gif_name": "penguin_top_center.gif",
        "playback_speed": 1.0,
        "flip_y": True,
        "fit_mode": "stretch",
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
        fidelity_exceptions=("authored_media_colors",),
        defaults=DEFAULTS,
        parameter_normalizer=lambda values: GifAnimation._normalized_parameters(values),
    )

    _LEGACY_KEYS = frozenset({
        "gif_directory", "gif_index", "brightness", "brightness_mode",
        "brightness_floor", "gamma", "contain_background", "speed",
        "color_saturation", "color_value", "plant_aware", "plant_modifiers",
        "plant_clearance", "plant_mask_path", "plant_globe_mask_path",
        "plant_gif_offset_radius", "plant_foliage_dim", "plant_globe_dim",
        "plant_accent_strength",
    })

    def __init__(self, controller, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)

        self._frames: List[np.ndarray] = []
        self._durations_sec: List[float] = []
        self._current_frame_index = 0
        self._loaded_gif_path: Optional[Path] = None
        self._load_error: Optional[str] = None
        self._last_output_key: Optional[Tuple[Any, ...]] = None
        self._last_pixels = np.zeros((self.get_pixel_count(), 4), dtype=np.uint8)
        self._revision = 0
        self._presentation_context: ResolvedScene | None = None

        self._load_selected_gif()

    def start(self):
        super().start()
        self._current_frame_index = 0
        self._last_output_key = None

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(values)
        unknown = sorted(set(supplied) - set(cls.DEFAULTS) - cls._LEGACY_KEYS)
        if unknown:
            raise ValueError(f"GIF Animation does not accept non-local parameters: {unknown!r}")
        result = dict(cls.DEFAULTS)
        result.update({key: supplied[key] for key in cls.DEFAULTS if key in supplied})
        name = result["gif_name"]
        if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(".gif"):
            raise ValueError("gif_name must be a packaged .gif basename")
        if not (PACKAGED_GIF_DIRECTORY / name).is_file():
            raise ValueError("gif_name must select an existing packaged GIF")
        speed = result["playback_speed"]
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(float(speed)) or not 0.1 <= float(speed) <= 4.0:
            raise ValueError("playback_speed must be a finite number from 0.1 to 4.0")
        result["playback_speed"] = float(speed)
        if not isinstance(result["flip_y"], bool):
            raise ValueError("flip_y must be a bool")
        if result["fit_mode"] not in cls.FIT_MODES:
            raise ValueError(f"fit_mode must be one of {list(cls.FIT_MODES)!r}")
        return result

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        previous = self.params
        normalized = self._normalized_parameters({**previous, **dict(new_params)})
        reload_keys = ("gif_name", "flip_y", "fit_mode")
        reload_required = any(previous[key] != normalized[key] for key in reload_keys)
        self.params = normalized
        self._last_output_key = None
        if reload_required:
            self._load_selected_gif()

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        del old_context
        descriptor = new_context.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("GIF Animation received a context for another component")
        if new_context.palette is not None:
            raise ValueError("GIF Animation preserves authored media colors")
        self._presentation_context = new_context

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame:
        if dict(context.parameters) != self.params:
            self.update_parameters(context.parameters)
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "gif_name": {
                "type": "str",
                "options": [path.name for path in self._list_gif_files()],
                "default": self.DEFAULTS["gif_name"],
                "description": "Packaged GIF artwork",
            },
            "playback_speed": {
                "type": "float",
                "min": 0.1,
                "max": 4.0,
                "default": 1.0,
                "description": "GIF playback speed multiplier.",
            },
            "flip_y": {
                "type": "bool",
                "default": True,
                "description": "Flip vertical axis so GIF top maps to wall top.",
            },
            "fit_mode": {
                "type": "str",
                "options": list(self.FIT_MODES),
                "default": "stretch",
                "description": "stretch, contain, or cover frame fit.",
            },
        }

    def get_runtime_stats(self) -> Dict[str, Any]:
        available = self._list_gif_files()
        return {
            "loaded_gif": str(self._loaded_gif_path) if self._loaded_gif_path else None,
            "available_gifs": [p.name for p in available],
            "frame_count": len(self._frames),
            "current_frame_index": self._current_frame_index,
            "load_error": self._load_error,
        }

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del frame_count
        if not self._frames:
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False, dirty_ranges=())

        if self._presentation_context is not None:
            self.params = self._normalized_parameters(self._presentation_context.parameters)
            elapsed = self._presentation_context.phase_time
        else:
            elapsed = max(0.0, float(time_elapsed))
        loop_time = sum(self._durations_sec)
        position = (elapsed * self.params["playback_speed"]) % loop_time
        cumulative = 0.0
        index = len(self._frames) - 1
        for candidate, duration in enumerate(self._durations_sec):
            cumulative += duration
            if position < cumulative:
                index = candidate
                break
        self._current_frame_index = index
        output_key = (self._loaded_gif_path, index)
        if output_key == self._last_output_key:
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False, dirty_ranges=())
        frame = self._frames[index]
        dirty = coverage_dirty_union(self._last_pixels, frame)
        self._last_pixels = frame
        self._last_output_key = output_key
        self._revision += 1
        return OverlayFrame(frame, revision=self._revision, changed=True, dirty_ranges=dirty)

    def _load_selected_gif(self):
        self._last_output_key = None
        self._load_error = None
        self._frames = []
        self._durations_sec = []
        self._current_frame_index = 0
        self._loaded_gif_path = None

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
        """Keep current Scene media selection inside the packaged library."""
        return PACKAGED_GIF_DIRECTORY

    def _select_gif(self, gif_files: Sequence[Path]) -> Optional[Path]:
        requested_name = self.params["gif_name"]
        return next((path for path in gif_files if path.name == requested_name), None)

    def _decode_gif(self, gif_path: Path) -> Tuple[List[np.ndarray], List[float]]:
        strip_count, leds_per_strip = self.get_strip_info()
        fit_mode = str(self.params.get("fit_mode", "stretch")).strip().lower()
        if fit_mode not in {"stretch", "contain", "cover"}:
            fit_mode = "stretch"
        flip_y = self.params["flip_y"]

        frames: List[np.ndarray] = []
        durations: List[float] = []

        with Image.open(gif_path) as img:
            for frame in ImageSequence.Iterator(img):
                rgba = frame.convert("RGBA")
                fitted = self._fit_frame(
                    rgba,
                    width=strip_count,
                    height=leds_per_strip,
                    fit_mode=fit_mode,
                    contain_background=0,
                )
                frames.append(self._flatten_frame(fitted, flip_y=flip_y))

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

        # Contain padding reveals the qualified native Background.
        del contain_background
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        offset_x = (width - new_w) // 2
        offset_y = (height - new_h) // 2
        canvas.alpha_composite(resized, (offset_x, offset_y))
        return canvas

    @staticmethod
    def _flatten_frame(image_rgba: "Image.Image", flip_y: bool) -> np.ndarray:
        rgba = np.asarray(image_rgba.convert("RGBA"), dtype=np.uint8)
        if flip_y:
            rgba = rgba[::-1]
        # Pillow is row-major (LED, strip); transpose into canonical strip-major.
        straight = np.ascontiguousarray(rgba.transpose(1, 0, 2).reshape(-1, 4))
        alpha = straight[:, 3:4].astype(np.uint16)
        premultiplied = straight.copy()
        premultiplied[:, :3] = (
            (straight[:, :3].astype(np.uint16) * alpha + 127) // 255
        ).astype(np.uint8)
        return premultiplied

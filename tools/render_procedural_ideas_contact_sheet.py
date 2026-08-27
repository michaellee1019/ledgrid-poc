#!/usr/bin/env python3
"""Render the 19 procedural-ideas plugins and their curated presets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.base import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


PLUGIN_IDS = (
    "rain_on_glass", "aurora_curtains", "cloud_canyon", "waterfall_veil",
    "tidal_bioluminescence", "wind_in_the_reeds", "moonlit_fog_banks",
    "desert_wind", "reaction_diffusion_garden", "physarum_network",
    "firefly_synchrony", "cyclic_reef", "frostwork", "flow_field_silk",
    "living_stained_glass", "quasicrystal_bloom", "cellular_tapestry",
    "circadian_window", "night_train_windows",
)


def frame_to_visual(pixels: object) -> np.ndarray:
    """Convert installed strip-major pixels into a top-down image canvas."""

    logical = np.asarray(pixels, dtype=np.uint8).reshape(
        DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP, 3
    )
    return logical[:, ::-1, :].transpose(1, 0, 2)


def wall_image_size(scale: int) -> tuple[int, int]:
    """Return the installed wall's contact-sheet image size at ``scale``."""

    return DEFAULT_STRIP_COUNT * scale, DEFAULT_LEDS_PER_STRIP * scale


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time", type=float, default=12.0)
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--warmup-fps", type=int, default=10,
                        help="Sequential semantic warmup cadence before capture")
    args = parser.parse_args()

    loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
    plugins = loader.load_all_plugins()
    missing = sorted(set(PLUGIN_IDS) - set(plugins))
    if missing:
        raise SystemExit(f"missing procedural plugins: {', '.join(missing)}")

    controller = PreviewLEDController(
        strips=DEFAULT_STRIP_COUNT,
        leds_per_strip=DEFAULT_LEDS_PER_STRIP,
    )
    tiles = []
    for plugin_id in PLUGIN_IDS:
        paths = list(loader.iter_curated_preset_files(plugin_id))
        if not paths:
            paths = [None]
        for path in paths:
            payload = json.loads(path.read_text()) if path else {"params": {}, "name": "Default"}
            params = dict(payload.get("params", {}))
            if plugin_id == "circadian_window" and float(params.get("hour", -1)) < 0:
                params["hour"] = 12.0
            animation = plugins[plugin_id](controller, params)
            rendered = animation.generate_frame(0.0, 0)
            steps = max(1, int(max(0.0, args.time) * max(1, args.warmup_fps)))
            for step in range(1, steps + 1):
                elapsed = args.time * step / steps
                rendered = animation.generate_frame(elapsed, step)
            frame = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
            visual = frame_to_visual(frame)
            image = Image.fromarray(visual, "RGB").resize(
                wall_image_size(args.scale), Image.Resampling.NEAREST
            )
            tiles.append((plugin_id, str(payload.get("name", "Default")), image))

    label_height = 30
    tile_width, image_height = wall_image_size(args.scale)
    rows = (len(tiles) + args.columns - 1) // args.columns
    sheet = Image.new("RGB", (tile_width * args.columns, (image_height + label_height) * rows), (14, 14, 18))
    draw = ImageDraw.Draw(sheet)
    for index, (plugin_id, name, tile) in enumerate(tiles):
        x = (index % args.columns) * tile_width
        y = (index // args.columns) * (image_height + label_height)
        sheet.paste(tile, (x, y))
        draw.text((x + 2, y + image_height + 2), plugin_id[:15], fill=(225, 225, 230))
        draw.text((x + 2, y + image_height + 15), name[:15], fill=(150, 155, 165))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output)
    print(f"rendered {len(tiles)} tiles to {args.output}")


if __name__ == "__main__":
    main()

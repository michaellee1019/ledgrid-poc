#!/usr/bin/env python3
"""Render all curated Canopy Cup presets at the installed wall aspect ratio."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.base import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.plugins.canopy_cup import CanopyCupAnimation


def render_preset(path: Path, seconds: float) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    controller = PreviewLEDController(32, 138)
    animation = CanopyCupAnimation(controller, payload["params"])
    rendered = None
    final_frame = max(1, int(round(seconds * 30.0)))
    for frame_count in range(final_frame + 1):
        rendered = animation.generate_frame(frame_count / 30.0, frame_count)
    pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
    return np.asarray(pixels, dtype=np.uint8).reshape(32, 138, 3).transpose(1, 0, 2)[::-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--check-distinct", action="store_true")
    args = parser.parse_args()

    preset_dir = Path(__file__).resolve().parent / "presets"
    tiles = []
    fingerprints = {}
    failures = []
    for path in sorted(preset_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        frame = render_preset(path, args.seconds)
        fingerprint = sha256(frame.tobytes()).hexdigest()
        if fingerprint in fingerprints:
            failures.append(f"{path.stem} duplicates {fingerprints[fingerprint]}")
        fingerprints[fingerprint] = path.stem
        tiles.append((payload["name"], frame))

    label_height = 24
    tile_width = 32 * args.scale
    tile_height = 138 * args.scale + label_height
    rows = (len(tiles) + args.columns - 1) // args.columns
    sheet = Image.new("RGB", (tile_width * args.columns, tile_height * rows), (8, 9, 15))
    draw = ImageDraw.Draw(sheet)
    for index, (name, frame) in enumerate(tiles):
        x = index % args.columns * tile_width
        y = index // args.columns * tile_height
        image = Image.fromarray(frame, mode="RGB").resize(
            (tile_width, 138 * args.scale), Image.Resampling.NEAREST
        )
        sheet.paste(image, (x, y + label_height))
        draw.text((x + 3, y + 5), name, fill=(240, 240, 230))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output)
    print(f"rendered {len(tiles)} distinct Canopy Cup tiles to {args.output}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        if args.check_distinct:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render the six sprint showcases at the installed wall's true aspect ratio."""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.base import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.fluid_tank import FluidTankAnimation
from animation.plugins.gradient import GradientAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.snake import SnakeAnimation
from animation.plugins.sparkle import SparkleAnimation


SHOWCASES = (
    ("gradient", GradientAnimation), ("sparkle", SparkleAnimation),
    ("snake", SnakeAnimation), ("pinball", PinballAnimation),
    ("conway_life", ConwayLifeAnimation), ("fluid_tank", FluidTankAnimation),
)
COMBINATIONS = {
    "gradient": ("illuminate", "shadow", "refract"),
    "sparkle": ("illuminate", "attractor", "habitat", "emitter"),
    "conway_life": ("obstacle", "emitter"),
    "fluid_tank": ("refract", "slow_zone", "obstacle"),
}


def render(animation_class, controller, active):
    state = {"active": list(active), "strengths": {name: 1.0 for name in active}}
    animation = animation_class(controller, {
        "seed": 317, "random_seed": 317, "animated": True,
        "plant_modifiers": state,
    })
    rendered = None
    for frame in range(48):
        rendered = animation.generate_frame(frame / 30.0, frame)
    pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
    return np.asarray(pixels, dtype=np.uint8).reshape(
        controller.strip_count, controller.leds_per_strip, 3
    ).transpose(1, 0, 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strips", type=int, default=32)
    parser.add_argument("--leds-per-strip", type=int, default=138)
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-distinct", action="store_true")
    args = parser.parse_args()

    controller = PreviewLEDController(args.strips, args.leds_per_strip)
    tiles = []
    failures = []
    for plugin, animation_class in SHOWCASES:
        baseline = render(animation_class, controller, ())
        baseline_hash = sha256(baseline.tobytes()).hexdigest()
        variants = [("off", ())]
        variants.extend((modifier, (modifier,)) for modifier in sorted(animation_class.PLANT_MODIFIER_SUPPORT))
        if plugin in COMBINATIONS:
            variants.append(("stack", COMBINATIONS[plugin]))
        fingerprints = set()
        for label, active in variants:
            frame = baseline if not active else render(animation_class, controller, active)
            fingerprint = sha256(frame.tobytes()).hexdigest()
            if active and fingerprint == baseline_hash:
                failures.append(f"{plugin}/{label} matches off")
            if fingerprint in fingerprints:
                failures.append(f"{plugin}/{label} duplicates another tile")
            fingerprints.add(fingerprint)
            tiles.append((f"{plugin}: {label}", frame))

    label_height = 24
    tile_width = args.strips * args.scale
    tile_height = args.leds_per_strip * args.scale + label_height
    rows = (len(tiles) + args.columns - 1) // args.columns
    sheet = Image.new("RGB", (tile_width * args.columns, tile_height * rows), (12, 12, 16))
    draw = ImageDraw.Draw(sheet)
    for index, (label, frame) in enumerate(tiles):
        x = index % args.columns * tile_width
        y = index // args.columns * tile_height
        image = Image.fromarray(frame).resize(
            (tile_width, args.leds_per_strip * args.scale), Image.Resampling.NEAREST
        )
        sheet.paste(image, (x, y + label_height))
        draw.text((x + 3, y + 5), label, fill=(235, 235, 240))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output)
    print(f"rendered {len(tiles)} tiles to {args.output}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        if args.check_distinct:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

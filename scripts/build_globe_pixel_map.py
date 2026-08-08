#!/usr/bin/env python3
"""Build a seven-vessel LED layer from fixed grid-space calibration regions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


def contains(region: dict, strip: int, led: int) -> bool:
    strip_start = int(region["strip_start"])
    led_start = int(region["led_start"])
    width = int(region["width"])
    height = int(region["height"])
    local_strip = strip - strip_start
    local_led = led - led_start
    if not (0 <= local_strip < width and 0 <= local_led < height):
        return False
    radius_x = width / 2.0
    radius_y = height / 2.0
    dx = (local_strip + 0.5 - radius_x) / radius_x
    dy = (local_led + 0.5 - radius_y) / radius_y
    return dx * dx + dy * dy <= 1.0


def build(pixel_map: dict, calibration: dict) -> dict:
    geometry = pixel_map["geometry"]
    if geometry["total_leds"] != calibration["geometry"]["total_leds"]:
        raise ValueError("pixel map and globe calibration geometries do not match")

    selected: dict[int, dict] = {}
    counts: dict[str, int] = {}
    for region in calibration["regions"]:
        region_pixels = [
            pixel
            for pixel in pixel_map["pixels"]
            if contains(region, int(pixel["strip"]), int(pixel["led"]))
        ]
        counts[str(region["id"])] = len(region_pixels)
        for pixel in region_pixels:
            selected[int(pixel["index"])] = {
                "index": int(pixel["index"]),
                "strip": int(pixel["strip"]),
                "led": int(pixel["led"]),
                "camera_x": float(pixel["camera_x"]),
                "camera_y": float(pixel["camera_y"]),
                "region": str(region["id"]),
            }

    indices = sorted(selected)
    return {
        "version": 1,
        "source_pixel_map": pixel_map.get("source_image", ""),
        "source_image": calibration.get("source_image", ""),
        "geometry": geometry,
        "region_count": len(calibration["regions"]),
        "regions": calibration["regions"],
        "region_pixel_counts": counts,
        "globe_count": len(indices),
        "globe_indices": indices,
        "covered_count": len(indices),
        "covered_indices": indices,
        "pixels": [selected[index] for index in indices],
    }


def draw_overlay(payload: dict, image_path: Path, output_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for pixel in payload["pixels"]:
        x, y = float(pixel["camera_x"]), float(pixel["camera_y"])
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 0, 255))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=95)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pixel-map", default="config/webcam_pixel_map_32x138_candidate.json")
    parser.add_argument("--regions", default="config/plant_globe_regions_32x138.json")
    parser.add_argument("--output", default="config/plant_globe_map_32x138.json")
    parser.add_argument("--overlay")
    args = parser.parse_args()

    pixel_map = json.loads(Path(args.pixel_map).read_text(encoding="utf-8"))
    calibration = json.loads(Path(args.regions).read_text(encoding="utf-8"))
    payload = build(pixel_map, calibration)
    output = Path(args.output)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.overlay:
        draw_overlay(payload, Path(calibration["source_image"]), Path(args.overlay))
    print(
        f"Mapped {payload['globe_count']} pixels across "
        f"{payload['region_count']} globe regions"
    )


if __name__ == "__main__":
    main()

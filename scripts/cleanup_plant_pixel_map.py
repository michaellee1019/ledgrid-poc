#!/usr/bin/env python3
"""Remove known hardware regions and isolated non-foliage mask components."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def components(pixel_by_cell: dict[tuple[int, int], dict]) -> list[list[dict]]:
    seen = set()
    result = []
    for cell in pixel_by_cell:
        if cell in seen:
            continue
        stack = [cell]
        seen.add(cell)
        component = []
        while stack:
            current = stack.pop()
            component.append(pixel_by_cell[current])
            strip, led = current
            for ds in (-1, 0, 1):
                for dl in (-1, 0, 1):
                    neighbor = (strip + ds, led + dl)
                    if neighbor in pixel_by_cell and neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
        result.append(component)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("map")
    parser.add_argument("--exclude-below-camera-y", type=float, default=880.0)
    parser.add_argument("--anchor-size", type=int, default=4)
    parser.add_argument("--small-component-size", type=int, default=2)
    parser.add_argument("--satellite-distance", type=float, default=25.0)
    args = parser.parse_args()

    path = Path(args.map)
    payload = json.loads(path.read_text(encoding="utf-8"))
    active = [pixel for pixel in payload["pixels"] if pixel.get("occluded", False)]
    eligible = {
        (int(pixel["strip"]), int(pixel["led"])): pixel
        for pixel in active
        if float(pixel["camera_y"]) <= args.exclude_below_camera_y
    }
    groups = components(eligible)
    anchors = [pixel for group in groups if len(group) >= args.anchor_size for pixel in group]
    removed = {
        int(pixel["index"])
        for pixel in active
        if float(pixel["camera_y"]) > args.exclude_below_camera_y
    }
    for group in groups:
        if len(group) > args.small_component_size or not anchors:
            continue
        distance = min(
            math.hypot(
                float(pixel["camera_x"]) - float(anchor["camera_x"]),
                float(pixel["camera_y"]) - float(anchor["camera_y"]),
            )
            for pixel in group
            for anchor in anchors
        )
        if distance > args.satellite_distance:
            removed.update(int(pixel["index"]) for pixel in group)

    retained = []
    for pixel in payload["pixels"]:
        index = int(pixel["index"])
        if index in removed:
            pixel["occluded"] = False
        if pixel.get("occluded", False):
            retained.append(index)
    payload["occluded_indices"] = retained
    payload["covered_indices"] = retained
    payload["occluded_count"] = len(retained)
    payload["covered_count"] = len(retained)
    payload["semantic_cleanup"] = {
        "exclude_below_camera_y": args.exclude_below_camera_y,
        "anchor_size": args.anchor_size,
        "small_component_size": args.small_component_size,
        "satellite_distance": args.satellite_distance,
        "removed_count": len(removed),
        "retained_count": len(retained),
        "removed_indices": sorted(removed),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Removed {len(removed)} non-foliage pixels; retained {len(retained)} foliage pixels")


if __name__ == "__main__":
    main()

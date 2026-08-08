#!/usr/bin/env python3
"""Prune camera-visible pixels from a live Plant Mask Highlight photograph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def live_response(
    difference: np.ndarray,
    x: float,
    y: float,
    radius: int,
    percentile: float,
) -> float:
    xi, yi = int(round(x)), int(round(y))
    sample = difference[
        max(0, yi - radius):min(difference.shape[0], yi + radius + 1),
        max(0, xi - radius):min(difference.shape[1], xi + radius + 1),
    ]
    return float(np.percentile(sample, percentile)) if sample.size else 255.0


def refine(
    payload: dict,
    mask: Image.Image,
    off: Image.Image,
    threshold: float,
    radius: int,
    percentile: float = 100.0,
) -> dict:
    mask_rgb = np.asarray(mask.convert("RGB"), dtype=np.uint8)
    off_rgb = np.asarray(off.convert("RGB"), dtype=np.uint8)
    if mask_rgb.shape != off_rgb.shape:
        raise ValueError("mask-on and wall-off images must have matching dimensions")
    difference = np.max(
        np.clip(mask_rgb.astype(np.int16) - off_rgb.astype(np.int16), 0, 255),
        axis=2,
    )

    removed = []
    retained = []
    for pixel in payload["pixels"]:
        if not pixel.get("occluded", False):
            continue
        response = live_response(
            difference,
            float(pixel["camera_x"]),
            float(pixel["camera_y"]),
            radius,
            percentile,
        )
        pixel["live_mask_response"] = round(response, 2)
        if response >= threshold:
            pixel["occluded"] = False
            removed.append(int(pixel["index"]))
        else:
            retained.append(int(pixel["index"]))

    payload["occluded_indices"] = retained
    payload["covered_indices"] = retained
    payload["occluded_count"] = len(retained)
    payload["covered_count"] = len(retained)
    payload.setdefault("live_verification", []).append(
        {
            "mask_image": str(mask.filename or ""),
            "off_image": str(off.filename or ""),
            "visible_response_threshold": threshold,
            "sample_radius": radius,
            "sample_percentile": percentile,
            "removed_count": len(removed),
            "retained_count": len(retained),
            "removed_indices": removed,
        }
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("map")
    parser.add_argument("--mask-image", required=True)
    parser.add_argument("--off-image", required=True)
    parser.add_argument("--threshold", type=float, default=100.0)
    parser.add_argument("--radius", type=int, default=3)
    parser.add_argument("--percentile", type=float, default=100.0)
    parser.add_argument("--output")
    args = parser.parse_args()

    map_path = Path(args.map)
    output_path = Path(args.output) if args.output else map_path
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    mask = Image.open(args.mask_image)
    off = Image.open(args.off_image)
    payload = refine(
        payload,
        mask,
        off,
        args.threshold,
        max(0, args.radius),
        min(100.0, max(0.0, args.percentile)),
    )
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest = payload["live_verification"][-1]
    print(
        f"Removed {latest['removed_count']} camera-visible pixels; "
        f"retained {latest['retained_count']} occluded pixels"
    )


if __name__ == "__main__":
    main()

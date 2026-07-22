#!/usr/bin/env python3
"""Refine a foliage LED map from an ambient-lit wall-off photograph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def median_features(image: np.ndarray, hsv: np.ndarray, x: float, y: float, radius: int):
    xi, yi = int(round(x)), int(round(y))
    y0, y1 = max(0, yi - radius), min(image.shape[0], yi + radius + 1)
    x0, x1 = max(0, xi - radius), min(image.shape[1], xi + radius + 1)
    b, g, r = np.median(image[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    hue, saturation, value = np.median(hsv[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    green_excess = float(g - (r + b) / 2.0)
    return float(hue), float(saturation), float(value), green_excess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pixel-map", default="config/webcam_pixel_map_32x138_candidate.json")
    parser.add_argument("--existing-map", default="config/plant_pixel_map_32x138.json")
    parser.add_argument("--globe-map", default="config/plant_globe_map_32x138.json")
    parser.add_argument("--ambient-image", required=True)
    parser.add_argument("--output", default="config/plant_pixel_map_32x138.json")
    parser.add_argument("--overlay")
    parser.add_argument("--sample-radius", type=int, default=2)
    parser.add_argument("--min-hue", type=float, default=25.0)
    parser.add_argument("--max-hue", type=float, default=50.0)
    parser.add_argument("--min-saturation", type=float, default=90.0)
    parser.add_argument("--max-value", type=float, default=140.0)
    parser.add_argument("--min-green-excess", type=float, default=10.0)
    parser.add_argument(
        "--fresh-baseline",
        action="store_true",
        help=(
            "Seed from the newly regenerated full-white pixel map instead of the "
            "existing foliage output; prevents stale masks accumulating after moves"
        ),
    )
    parser.add_argument(
        "--retain-existing-below-camera-y",
        type=float,
        default=250.0,
        help="Retain baseline foliage at or below this image row even if color is brown",
    )
    args = parser.parse_args()

    pixel_map = json.loads(Path(args.pixel_map).read_text(encoding="utf-8"))
    existing = json.loads(Path(args.existing_map).read_text(encoding="utf-8"))
    globe = json.loads(Path(args.globe_map).read_text(encoding="utf-8"))
    baseline = pixel_map if args.fresh_baseline else existing
    existing_indices = {int(index) for index in baseline["covered_indices"]}
    globe_indices = {int(index) for index in globe["globe_indices"]}

    image = cv2.imread(args.ambient_image)
    if image is None:
        raise ValueError(f"Unable to read ambient image: {args.ambient_image}")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    detected = set()
    features = {}
    for pixel in pixel_map["pixels"]:
        index = int(pixel["index"])
        hue, saturation, value, green_excess = median_features(
            image,
            hsv,
            float(pixel["camera_x"]),
            float(pixel["camera_y"]),
            max(0, args.sample_radius),
        )
        features[index] = (hue, saturation, value, green_excess)
        if (
            index not in globe_indices
            and args.min_hue <= hue <= args.max_hue
            and saturation >= args.min_saturation
            and value <= args.max_value
            and green_excess >= args.min_green_excess
        ):
            detected.add(index)

    retained_existing = {
        int(pixel["index"])
        for pixel in pixel_map["pixels"]
        if int(pixel["index"]) in existing_indices - globe_indices
        and (
            args.fresh_baseline
            or float(pixel["camera_y"]) >= args.retain_existing_below_camera_y
            or int(pixel["index"]) in detected
        )
    }
    foliage_indices = sorted(retained_existing | detected)
    foliage_set = set(foliage_indices)
    for pixel in pixel_map["pixels"]:
        index = int(pixel["index"])
        hue, saturation, value, green_excess = features[index]
        pixel["occluded"] = index in foliage_set
        pixel["ambient_foliage_features"] = {
            "hue": round(hue, 2),
            "saturation": round(saturation, 2),
            "value": round(value, 2),
            "green_excess": round(green_excess, 2),
        }

    pixel_map["source_image"] = args.ambient_image
    pixel_map["covered_indices"] = foliage_indices
    pixel_map["occluded_indices"] = foliage_indices
    pixel_map["covered_count"] = len(foliage_indices)
    pixel_map["occluded_count"] = len(foliage_indices)
    pixel_map["ambient_foliage_refinement"] = {
        "ambient_image": args.ambient_image,
        "globe_map": args.globe_map,
        "sample_radius": args.sample_radius,
        "min_hue": args.min_hue,
        "max_hue": args.max_hue,
        "min_saturation": args.min_saturation,
        "max_value": args.max_value,
        "min_green_excess": args.min_green_excess,
        "retain_existing_below_camera_y": args.retain_existing_below_camera_y,
        "baseline_source": args.pixel_map if args.fresh_baseline else args.existing_map,
        "fresh_baseline": args.fresh_baseline,
        "baseline_count": len(existing_indices),
        "retained_existing_count": len(retained_existing),
        "detected_count": len(detected),
        "added_count": len(detected - retained_existing),
        "globe_excluded_count": len(existing_indices & globe_indices),
    }
    Path(args.output).write_text(json.dumps(pixel_map, indent=2) + "\n", encoding="utf-8")

    if args.overlay:
        overlay = image.copy()
        for pixel in pixel_map["pixels"]:
            if int(pixel["index"]) not in foliage_set:
                continue
            center = (int(round(pixel["camera_x"])), int(round(pixel["camera_y"])))
            color = (255, 255, 0) if int(pixel["index"]) in retained_existing else (0, 255, 0)
            cv2.circle(overlay, center, 2, color, -1, lineType=cv2.LINE_AA)
        cv2.imwrite(args.overlay, overlay)

    print(
        f"Retained {len(retained_existing)} existing foliage pixels, added "
        f"{len(detected - retained_existing)}, and excluded "
        f"{len(existing_indices & globe_indices)} globe-overlap pixels; "
        f"final foliage count {len(foliage_indices)}"
    )


if __name__ == "__main__":
    main()

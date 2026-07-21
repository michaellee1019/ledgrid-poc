#!/usr/bin/env python3
"""Rectify and color-normalize fixed-webcam LED wall calibration captures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Sequence

import cv2
import numpy as np


CORNER_NAMES = ("tl", "tr", "br", "bl")


def load_config(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return image


def corner_array(config: Dict[str, object]) -> np.ndarray:
    raw = config["panel_corners"]
    if not isinstance(raw, dict):
        raise ValueError("panel_corners must be an object")
    points = [raw[name] for name in CORNER_NAMES]
    corners = np.asarray(points, dtype=np.float32)
    if corners.shape != (4, 2):
        raise ValueError("panel_corners must contain four [x, y] points")
    return corners


def homography(corners: np.ndarray, width: int, height: int) -> np.ndarray:
    destination = np.asarray(
        ((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)),
        dtype=np.float32,
    )
    return cv2.getPerspectiveTransform(corners, destination)


def warp(image: np.ndarray, matrix: np.ndarray, width: int, height: int) -> np.ndarray:
    return cv2.warpPerspective(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )


def corrected_illumination(on: np.ndarray, off: np.ndarray) -> tuple[np.ndarray, list[float]]:
    positive = np.clip(on.astype(np.float32) - off.astype(np.float32), 0.0, 255.0)
    intensity = positive.max(axis=2)
    cutoff = max(12.0, float(np.percentile(intensity, 55)))
    usable = intensity >= cutoff
    references = np.asarray(
        [np.percentile(positive[:, :, channel][usable], 85) for channel in range(3)],
        dtype=np.float32,
    )
    target = float(np.median(references))
    gains = np.clip(target / np.maximum(references, 1.0), 0.5, 2.0)
    balanced = positive * gains.reshape(1, 1, 3)
    white = max(1.0, float(np.percentile(balanced[usable], 99)))
    balanced *= 245.0 / white
    return np.clip(balanced, 0, 255).astype(np.uint8), [round(float(v), 4) for v in gains]


def draw_corner_overlay(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    overlay = image.copy()
    polygon = np.round(corners).astype(np.int32).reshape((-1, 1, 2))
    cv2.polylines(overlay, [polygon], True, (0, 215, 255), 3, cv2.LINE_AA)
    for name, point in zip(CORNER_NAMES, corners):
        x, y = (int(round(v)) for v in point)
        cv2.circle(overlay, (x, y), 7, (0, 64, 255), -1, cv2.LINE_AA)
        cv2.putText(overlay, name, (x + 9, y - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 64, 255), 2)
    return overlay


def ensure_matching(images: Sequence[np.ndarray]) -> tuple[int, int]:
    shapes = {image.shape for image in images}
    if len(shapes) != 1:
        raise ValueError(f"Capture dimensions differ: {sorted(shapes)}")
    height, width = images[0].shape[:2]
    return width, height


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/webcam_wall_calibration.json")
    parser.add_argument("--off", required=True, help="Wall-off reference image")
    parser.add_argument("--orientation", required=True, help="Orientation-marker image")
    parser.add_argument("--white", required=True, help="Low-exposure full-white image")
    parser.add_argument("--dimension-probe", help="Optional edge/dimension probe image")
    parser.add_argument("--output-dir", default="calibration_photos")
    parser.add_argument("--prefix", default="webcam-wall")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)
    off_path, orientation_path, white_path = map(Path, (args.off, args.orientation, args.white))
    off, orientation, white = map(load_image, (off_path, orientation_path, white_path))
    dimension_path = Path(args.dimension_probe) if args.dimension_probe else None
    dimension = load_image(dimension_path) if dimension_path else None
    captures = (off, orientation, white) + ((dimension,) if dimension is not None else ())
    source_width, source_height = ensure_matching(captures)

    output_config = config["rectified_output"]
    if not isinstance(output_config, dict):
        raise ValueError("rectified_output must be an object")
    width = int(output_config["width"])
    height = int(output_config["height"])
    corners = corner_array(config)
    matrix = homography(corners, width, height)

    rectified_off = warp(off, matrix, width, height)
    rectified_white = warp(white, matrix, width, height)
    illumination, gains = corrected_illumination(rectified_white, rectified_off)
    overlay = draw_corner_overlay(orientation, corners)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "corner_overlay": output_dir / f"{args.prefix}-corners.jpg",
        "rectified_white": output_dir / f"{args.prefix}-rectified.jpg",
        "corrected_illumination": output_dir / f"{args.prefix}-illumination.png",
        "report": output_dir / f"{args.prefix}-report.json",
    }
    if dimension is not None:
        outputs["rectified_dimension_probe"] = output_dir / f"{args.prefix}-dimension-probe.png"
    cv2.imwrite(str(outputs["corner_overlay"]), overlay, [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(outputs["rectified_white"]), rectified_white, [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(outputs["corrected_illumination"]), illumination)
    if dimension is not None:
        cv2.imwrite(str(outputs["rectified_dimension_probe"]), warp(dimension, matrix, width, height))

    polygon_mask = np.zeros((source_height, source_width), dtype=np.uint8)
    cv2.fillConvexPoly(polygon_mask, np.round(corners).astype(np.int32), 255)
    source_wall = white[polygon_mask > 0]
    saturation_fraction = float(np.mean(np.max(source_wall, axis=1) >= 250))
    report = {
        "version": 1,
        "config": str(config_path),
        "sources": {
            "off": str(off_path),
            "orientation": str(orientation_path),
            "white": str(white_path),
            **({"dimension_probe": str(dimension_path)} if dimension_path else {}),
        },
        "source_size": {"width": source_width, "height": source_height},
        "corners": {name: [round(float(v), 3) for v in point] for name, point in zip(CORNER_NAMES, corners)},
        "homography_source_to_rectified": [[round(float(v), 9) for v in row] for row in matrix],
        "rectified_size": {"width": width, "height": height},
        "white_balance_gains_bgr": gains,
        "source_white_saturation_fraction": round(saturation_fraction, 6),
        "controller_claim": config.get("controller_claim", {}),
        "measured_layout": config.get("measured_layout", {}),
        "outputs": {key: str(path) for key, path in outputs.items() if key != "report"},
    }
    outputs["report"].write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

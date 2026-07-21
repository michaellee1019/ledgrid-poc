#!/usr/bin/env python3
"""Build a logical LED-to-camera map from a full-white wall photograph."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw


Point = Tuple[float, float]


def parse_corners(raw: str) -> Tuple[Point, Point, Point, Point]:
    values = [float(value.strip()) for value in raw.split(",")]
    if len(values) != 8:
        raise argparse.ArgumentTypeError("corners must contain tlx,tly,trx,try,brx,bry,blx,bly")
    return tuple((values[i], values[i + 1]) for i in range(0, 8, 2))  # type: ignore[return-value]


def parse_bottom_corners(raw: str) -> Tuple[Point, Point]:
    values = [float(value.strip()) for value in raw.split(",")]
    if len(values) != 4:
        raise argparse.ArgumentTypeError("bottom corners must contain blx,bly,brx,bry")
    return (values[0], values[1]), (values[2], values[3])


def _robust_line_fit(y_values: np.ndarray, x_values: np.ndarray) -> Tuple[float, float]:
    keep = np.ones(y_values.size, dtype=bool)
    for _ in range(6):
        slope, intercept = np.polyfit(y_values[keep], x_values[keep], 1)
        residual = x_values - (slope * y_values + intercept)
        median_error = float(np.median(np.abs(residual[keep])))
        keep = np.abs(residual) <= max(4.0, median_error * 2.5)
    return float(slope), float(intercept)


def extrapolate_full_panel(
    image: Image.Image,
    bottom_corners: Tuple[Point, Point],
    strips: int,
    leds_per_strip: int,
) -> Tuple[Tuple[Point, Point, Point, Point], dict]:
    """Fit visible side edges and extrapolate the off-camera top of the wall.

    The calibrated panel cells are approximately square. In a perspective
    image, their vertical pitch is proportional to the panel width at the same
    row. Integrating ``32 / width(y)`` along the fitted side edges gives the
    number of logical LED rows visible over an image interval. We solve that
    integral for exactly 140 rows instead of incorrectly treating image y=0 as
    the top of the panel.
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width = rgb.shape[:2]
    bl, br = bottom_corners
    bottom_y = (bl[1] + br[1]) * 0.5
    bottom_x_min, bottom_x_max = min(bl[0], br[0]), max(bl[0], br[0])
    margin = max(60.0, (bottom_x_max - bottom_x_min) * 0.35)
    roi_min = max(0, int(bottom_x_min - margin))
    roi_max = min(width, int(bottom_x_max + margin))

    fit_y = []
    fit_left = []
    fit_right = []
    last_fit_y = max(20, int(min(bl[1], br[1]) - 35))
    for y in range(12, last_fit_y, 5):
        band = rgb[max(0, y - 5):min(height, y + 6)]
        red = band[:, :, 0].mean(axis=0)
        green = band[:, :, 1].mean(axis=0)
        blue = band[:, :, 2].mean(axis=0)
        candidates = np.flatnonzero(
            (blue > 175.0) & (blue > red * 1.035) & (green > 135.0)
        )
        candidates = candidates[(candidates >= roi_min) & (candidates <= roi_max)]
        if candidates.size < 70:
            continue
        fit_y.append(float(y))
        fit_left.append(float(np.percentile(candidates, 2)))
        fit_right.append(float(np.percentile(candidates, 98)))

    if len(fit_y) < 20:
        raise ValueError("could not find enough illuminated panel edge samples")
    y_values = np.asarray(fit_y)
    left_slope, _ = _robust_line_fit(y_values, np.asarray(fit_left))
    right_slope, _ = _robust_line_fit(y_values, np.asarray(fit_right))

    # Anchor the CV-derived slopes to the visually reliable bottom corners.
    def left_x(y: float) -> float:
        return bl[0] + left_slope * (y - bl[1])

    def right_x(y: float) -> float:
        return br[0] + right_slope * (y - br[1])

    bottom_width = right_x(bottom_y) - left_x(bottom_y)
    width_slope = right_slope - left_slope
    if bottom_width <= 0 or abs(width_slope) < 1e-5:
        # Near-orthographic fallback: square-cell height follows width directly.
        full_height = bottom_width * leds_per_strip / strips
        top_y_center = bottom_y - full_height
        top_width = bottom_width
    else:
        # rows = integral(strips / width(y), y=top..bottom)
        top_width = bottom_width / math.exp(leds_per_strip * width_slope / strips)
        top_y_center = bottom_y + (top_width - bottom_width) / width_slope

    perspective_scale = max(0.5, min(2.0, top_width / bottom_width))
    half_bottom_tilt = (br[1] - bl[1]) * 0.5
    top_left_y = top_y_center - half_bottom_tilt * perspective_scale
    top_right_y = top_y_center + half_bottom_tilt * perspective_scale
    corners = (
        (left_x(top_left_y), top_left_y),
        (right_x(top_right_y), top_right_y),
        br,
        bl,
    )
    diagnostics = {
        "method": "cv_side_fit_plus_square_cell_projective_extrapolation",
        "edge_samples": len(fit_y),
        "left_edge_dx_per_dy": round(left_slope, 6),
        "right_edge_dx_per_dy": round(right_slope, 6),
        "estimated_top_center_y": round(top_y_center, 2),
        "estimated_full_height_pixels": round(bottom_y - top_y_center, 2),
        "estimated_visible_row_fraction": round(
            max(0.0, min(1.0, (bottom_y - max(0.0, top_y_center)) / max(1.0, bottom_y - top_y_center))),
            4,
        ),
    }
    return corners, diagnostics


def _homography(corners: Sequence[Point], strips: int, leds_per_strip: int) -> np.ndarray:
    logical = ((0.0, 0.0), (float(strips), 0.0), (float(strips), float(leds_per_strip)), (0.0, float(leds_per_strip)))
    matrix = []
    target = []
    for (x, y), (u, v) in zip(logical, corners):
        matrix.append((x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y))
        matrix.append((0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y))
        target.extend((u, v))
    values = np.linalg.solve(np.asarray(matrix, dtype=np.float64), np.asarray(target, dtype=np.float64))
    return np.append(values, 1.0).reshape((3, 3))


def project(homography: np.ndarray, x: float, y: float) -> Point:
    result = homography @ np.asarray((x, y, 1.0), dtype=np.float64)
    return float(result[0] / result[2]), float(result[1] / result[2])


def sample_luminance(gray: np.ndarray, x: float, y: float, radius: int = 2) -> float:
    xi, yi = int(round(x)), int(round(y))
    y0, y1 = max(0, yi - radius), min(gray.shape[0], yi + radius + 1)
    x0, x1 = max(0, xi - radius), min(gray.shape[1], xi + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return 0.0
    # A high percentile finds the illuminated emitter inside each diffuser cell
    # while remaining robust to the photographed grid lines.
    return float(np.percentile(gray[y0:y1, x0:x1], 80))


def build_map(
    image: Image.Image,
    corners: Sequence[Point],
    strips: int,
    leds_per_strip: int,
    visibility_threshold: float,
) -> dict:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    gray = np.max(rgb, axis=2) / 255.0
    homography = _homography(corners, strips, leds_per_strip)

    coordinates = np.empty((strips, leds_per_strip, 2), dtype=np.float32)
    samples = np.empty((strips, leds_per_strip), dtype=np.float32)
    observed = np.zeros((strips, leds_per_strip), dtype=bool)
    for strip in range(strips):
        for led in range(leds_per_strip):
            x, y = project(homography, strip + 0.5, led + 0.5)
            coordinates[strip, led] = (x, y)
            observed[strip, led] = 0 <= x < image.width and 0 <= y < image.height
            samples[strip, led] = sample_luminance(gray, x, y) if observed[strip, led] else np.nan

    # Normalize against nearby rows. This compensates for camera vignetting and
    # perspective while preserving the sharp dark regions caused by foliage.
    visibility = np.empty_like(samples)
    for led in range(leds_per_strip):
        lo, hi = max(0, led - 4), min(leds_per_strip, led + 5)
        nearby = samples[:, lo:hi]
        finite = nearby[np.isfinite(nearby)]
        baseline = max(0.08, float(np.percentile(finite, 85))) if finite.size else 1.0
        visibility[:, led] = np.where(
            observed[:, led],
            np.clip(samples[:, led] / baseline, 0.0, 1.0),
            1.0,
        )

    pixels = []
    occluded_indices = []
    for strip in range(strips):
        for led in range(leds_per_strip):
            index = strip * leds_per_strip + led
            score = float(visibility[strip, led])
            is_observed = bool(observed[strip, led])
            occluded = is_observed and score < visibility_threshold
            if occluded:
                occluded_indices.append(index)
            pixels.append(
                {
                    "index": index,
                    "strip": strip,
                    "led": led,
                    "camera_x": round(float(coordinates[strip, led, 0]), 2),
                    "camera_y": round(float(coordinates[strip, led, 1]), 2),
                    "visibility": round(score, 4),
                    "observed": is_observed,
                    "occluded": occluded,
                }
            )

    return {
        "version": 1,
        "source_image": "calibration_photos/webcam-full-white.jpg",
        "geometry": {
            "strip_count": strips,
            "leds_per_strip": leds_per_strip,
            "total_leds": strips * leds_per_strip,
            "index_formula": "strip * leds_per_strip + led",
        },
        "camera": {
            "image_width": image.width,
            "image_height": image.height,
            "corners": {
                name: {"x": round(point[0], 2), "y": round(point[1], 2)}
                for name, point in zip(("tl", "tr", "br", "bl"), corners)
            },
        },
        "visibility_threshold": visibility_threshold,
        "observed_count": int(np.count_nonzero(observed)),
        "occluded_count": len(occluded_indices),
        "occluded_indices": occluded_indices,
        "pixels": pixels,
    }


def draw_overlay(image: Image.Image, payload: dict, output_path: Path) -> None:
    overlay = image.convert("RGB")
    draw = ImageDraw.Draw(overlay)
    for pixel in payload["pixels"]:
        x, y = pixel["camera_x"], pixel["camera_y"]
        if not pixel.get("observed", True):
            continue
        color = (255, 48, 32) if pixel["occluded"] else (40, 255, 96)
        draw.ellipse((x - 1.4, y - 1.4, x + 1.4, y + 1.4), fill=color)
    corner_values = payload["camera"]["corners"]
    polygon = [(corner_values[name]["x"], corner_values[name]["y"]) for name in ("tl", "tr", "br", "bl")]
    draw.line(polygon + [polygon[0]], fill=(255, 215, 0), width=3)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(output_path, quality=92)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Full-white webcam photograph")
    parser.add_argument(
        "--corners",
        type=parse_corners,
        default=None,
        help="tlx,tly,trx,try,brx,bry,blx,bly in image pixels",
    )
    parser.add_argument(
        "--bottom-corners",
        type=parse_bottom_corners,
        default=None,
        help="blx,bly,brx,bry; visible side edges are CV-fit and the missing top is extrapolated",
    )
    parser.add_argument("--strips", type=int, default=32)
    parser.add_argument("--leds-per-strip", type=int, default=140)
    parser.add_argument("--visibility-threshold", type=float, default=0.52)
    parser.add_argument("--output", default="config/webcam_pixel_map.json")
    parser.add_argument("--overlay", default="calibration_photos/webcam-pixel-map-overlay.jpg")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_path = Path(args.image)
    image = Image.open(image_path)
    if args.corners is not None:
        corners = args.corners
        extrapolation = {"method": "manual_full_corners"}
    elif args.bottom_corners is not None:
        corners, extrapolation = extrapolate_full_panel(
            image, args.bottom_corners, args.strips, args.leds_per_strip
        )
    else:
        raise SystemExit("provide --bottom-corners for CV extrapolation or --corners for a fully visible panel")
    payload = build_map(
        image,
        corners,
        args.strips,
        args.leds_per_strip,
        args.visibility_threshold,
    )
    payload["source_image"] = str(image_path)
    payload["camera"]["extrapolation"] = extrapolation
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    draw_overlay(image, payload, Path(args.overlay))
    print(
        f"Mapped {payload['geometry']['total_leds']} LEDs; "
        f"{payload['observed_count']} observed in-frame; "
        f"{payload['occluded_count']} marked occluded"
    )
    print(f"Map: {output_path}")
    print(f"Overlay: {args.overlay}")


if __name__ == "__main__":
    main()

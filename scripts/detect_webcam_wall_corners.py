#!/usr/bin/env python3
"""Detect LED wall corners from paired full-white and wall-off webcam images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Tuple

import cv2
import numpy as np


CORNER_NAMES = ("tl", "tr", "br", "bl")


def _ordered_quad(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    total = points.sum(axis=1)
    difference = points[:, 0] - points[:, 1]
    return np.asarray(
        (
            points[np.argmin(total)],
            points[np.argmax(difference)],
            points[np.argmax(total)],
            points[np.argmin(difference)],
        ),
        dtype=np.float32,
    )


def detect_panel_corners(on: np.ndarray, off: np.ndarray) -> Tuple[np.ndarray, Dict[str, float]]:
    """Find the dominant tall illuminated quadrilateral and return TL/TR/BR/BL."""
    if on.shape != off.shape or on.ndim != 3:
        raise ValueError("full-white and wall-off images must be matching color images")
    positive = np.clip(on.astype(np.float32) - off.astype(np.float32), 0.0, 255.0)
    response = np.max(positive, axis=2).astype(np.uint8)
    response = cv2.GaussianBlur(response, (5, 5), 0)
    nonzero = response[response > 3]
    if nonzero.size < 1000:
        raise ValueError("not enough positive LED response to locate the wall")
    otsu_threshold, _ = cv2.threshold(response, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    percentile_threshold = float(np.percentile(nonzero, 58))
    threshold = max(8.0, min(float(otsu_threshold), percentile_threshold))
    binary = np.where(response >= threshold, 255, 0).astype(np.uint8)
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (19, 19))
    )
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    )

    image_height, image_width = response.shape
    candidates = []
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        area = float(cv2.contourArea(contour))
        x, y, width, height = cv2.boundingRect(contour)
        if area < image_width * image_height * 0.008 or height < image_height * 0.30:
            continue
        aspect = height / max(1.0, float(width))
        if not 1.8 <= aspect <= 8.0:
            continue
        rectangularity = area / max(1.0, float(width * height))
        score = area * min(aspect, 5.0) * (0.5 + rectangularity)
        candidates.append((score, contour, rectangularity, aspect))
    if not candidates:
        raise ValueError("could not find a tall illuminated wall candidate")
    _, contour, rectangularity, aspect = max(candidates, key=lambda item: item[0])

    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    quad = None
    for epsilon in np.linspace(0.008, 0.08, 40):
        approximation = cv2.approxPolyDP(hull, float(epsilon * perimeter), True)
        if len(approximation) == 4:
            quad = approximation.reshape(4, 2).astype(np.float32)
            break
    if quad is None:
        quad = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
    corners = _ordered_quad(quad)

    polygon = np.zeros(response.shape, dtype=np.uint8)
    cv2.fillConvexPoly(polygon, np.round(corners).astype(np.int32), 255)
    inside = polygon > 0
    response_coverage = float(np.mean(binary[inside] > 0)) if np.any(inside) else 0.0
    height_fraction = float((corners[2:, 1].mean() - corners[:2, 1].mean()) / image_height)
    edge_margin = 5.0
    frame_edge_clipped = bool(
        np.any(corners[:, 0] <= edge_margin)
        or np.any(corners[:, 1] <= edge_margin)
        or np.any(corners[:, 0] >= image_width - 1 - edge_margin)
        or np.any(corners[:, 1] >= image_height - 1 - edge_margin)
    )
    confidence = float(
        np.clip(0.40 * response_coverage + 0.35 * rectangularity + 0.25 * height_fraction, 0.0, 1.0)
    )
    # A clipped quadrilateral can look extremely rectangular while leaving its
    # missing corner mathematically unknowable. Never auto-commit that result.
    if frame_edge_clipped:
        confidence = min(confidence, 0.55)
    diagnostics = {
        "threshold": round(threshold, 3),
        "otsu_threshold": round(float(otsu_threshold), 3),
        "candidate_aspect": round(float(aspect), 4),
        "rectangularity": round(float(rectangularity), 4),
        "response_coverage": round(response_coverage, 4),
        "height_fraction": round(height_fraction, 4),
        "frame_edge_clipped": frame_edge_clipped,
        "confidence": round(confidence, 4),
    }
    return corners, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--on", required=True, help="Low-brightness full-white capture")
    parser.add_argument("--off", required=True, help="Wall-off capture with unchanged camera/exposure")
    parser.add_argument("--config", default="config/webcam_wall_calibration.json")
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="Replace panel_corners only when automatic confidence is at least 0.60",
    )
    args = parser.parse_args()

    on = cv2.imread(args.on, cv2.IMREAD_COLOR)
    off = cv2.imread(args.off, cv2.IMREAD_COLOR)
    if on is None or off is None:
        raise FileNotFoundError("could not read one or both capture images")
    corners, diagnostics = detect_panel_corners(on, off)
    overlay = on.copy()
    polygon = np.round(corners).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(overlay, [polygon], True, (0, 215, 255), 3, cv2.LINE_AA)
    for name, point in zip(CORNER_NAMES, corners):
        center = tuple(np.round(point).astype(int))
        cv2.circle(overlay, center, 7, (0, 64, 255), -1, cv2.LINE_AA)
        cv2.putText(overlay, name, (center[0] + 9, center[1] - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 64, 255), 2)
    overlay_path = Path(args.overlay)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    old_corners = np.asarray([config["panel_corners"][name] for name in CORNER_NAMES])
    corner_shift = np.linalg.norm(corners - old_corners, axis=1)
    report = {
        "version": 1,
        "sources": {"on": args.on, "off": args.off},
        "corners": {name: [round(float(v), 2) for v in point] for name, point in zip(CORNER_NAMES, corners)},
        "previous_corner_shift_pixels": {
            name: round(float(value), 2) for name, value in zip(CORNER_NAMES, corner_shift)
        },
        "diagnostics": diagnostics,
        "config_updated": False,
    }
    if args.write_config:
        if diagnostics["confidence"] < 0.60:
            raise RuntimeError(f"corner confidence {diagnostics['confidence']:.3f} is below 0.60")
        config["panel_corners"] = report["corners"]
        config["corner_detection"] = {
            "method": "full_white_minus_wall_off",
            "report": args.report,
            **diagnostics,
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        report["config_updated"] = True
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

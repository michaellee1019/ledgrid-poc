#!/usr/bin/env python3
"""
Calibrate plant-covered pixels from captured LED wall photos.

This script is the canonical calibration workflow for this repository.

Assumptions:
- The source photos were captured from the `plant_calibration` animation plugin.
- Pattern file order is fixed:
  1) orientation_markers, 2) major_grid_lines, 3) checkerboard,
  4) coordinate_gradient, 5) full_white
- Camera framing is stable across the 5 photos (same viewpoint).
- Input photos may contain EXIF rotation metadata; this script always applies
  EXIF transpose before analysis.
- Logical layout defaults to 32 strips x 140 LEDs per strip.

Coordinate conventions:
- Logical `(strip, led)` uses row-major flattening:
  `index = strip * leds_per_strip + led`
- `strip` traverses the short panel axis (left/right), `led` traverses the
  long panel axis (top/bottom) before optional auto flip.

Iterative workflow:
1) Run with auto corners.
2) Inspect generated overlay and corner polygon.
3) Re-run with `--corners ...` copied from the previous run output to lock
   perspective and iterate thresholds.
4) Repeat until mask quality is acceptable.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from PIL import Image, ImageDraw, ImageOps

# Ensure repo root is on sys.path when running as a standalone script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animation.plugins.plant_calibration import PlantCalibrationAnimation


Point = Tuple[float, float]
Color = Tuple[int, int, int]

PATTERN_SEQUENCE_LABELS = [
    "orientation_markers",
    "major_grid_lines",
    "checkerboard",
    "coordinate_gradient",
    "full_white",
]
DEFAULT_PATTERN_FILES = "IMG_4124.jpeg,IMG_4120.jpeg,IMG_4121.jpeg,IMG_4122.jpeg,IMG_4123.jpeg"


@dataclass
class PanelCorners:
    tl: Point
    tr: Point
    br: Point
    bl: Point

    def as_dict(self) -> Dict[str, Dict[str, float]]:
        return {
            "tl": {"x": float(self.tl[0]), "y": float(self.tl[1])},
            "tr": {"x": float(self.tr[0]), "y": float(self.tr[1])},
            "br": {"x": float(self.br[0]), "y": float(self.br[1])},
            "bl": {"x": float(self.bl[0]), "y": float(self.bl[1])},
        }


@dataclass
class ClassificationStats:
    gains: List[float]
    selected_flip_x: bool
    selected_flip_y: bool
    covered_before_cleanup: int
    covered_after_cleanup: int


class _DummyController:
    def __init__(self, strips: int, leds_per_strip: int):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LED wall plant occlusion calibrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=build_cli_epilog(),
    )
    parser.add_argument(
        "--image-dir",
        default="calibration_photos",
        help="Directory containing calibration images",
    )
    parser.add_argument(
        "--pattern-files",
        default=DEFAULT_PATTERN_FILES,
        help=(
            "Comma-separated files in pattern order: "
            "orientation,grid,checker,gradient,white"
        ),
    )
    parser.add_argument(
        "--full-on-file",
        default="",
        help="Optional supplemental image file showing full-on panel (used for diff fusion)",
    )
    parser.add_argument(
        "--full-off-file",
        default="",
        help="Optional supplemental image file showing full-off panel (used for diff fusion)",
    )
    parser.add_argument(
        "--mask-pattern-file",
        default="",
        help="Optional supplemental image file showing current mask pattern",
    )
    parser.add_argument(
        "--mask-map-path",
        default="config/plant_pixel_map.json",
        help="Mask map JSON used to interpret --mask-pattern-file expected lit pixels",
    )
    parser.add_argument("--strips", type=int, default=32, help="Strip count")
    parser.add_argument("--leds-per-strip", type=int, default=140, help="LEDs per strip")
    parser.add_argument(
        "--corners",
        default="",
        help=(
            "Manual panel corners in upright image pixels: "
            "tlx,tly,trx,try,brx,bry,blx,bly"
        ),
    )
    parser.add_argument(
        "--auto-downsample",
        type=int,
        default=4,
        help="Downsample factor used by auto corner detection",
    )
    parser.add_argument(
        "--flip-x",
        choices=["auto", "true", "false"],
        default="auto",
        help="Flip strip axis left/right",
    )
    parser.add_argument(
        "--flip-y",
        choices=["auto", "true", "false"],
        default="auto",
        help="Flip LED axis top/bottom",
    )
    parser.add_argument(
        "--med-threshold",
        type=float,
        default=0.62,
        help="Median normalized ratio threshold for covered classification",
    )
    parser.add_argument(
        "--max-threshold",
        type=float,
        default=0.88,
        help="Max normalized ratio threshold for covered classification",
    )
    parser.add_argument(
        "--hard-med-threshold",
        type=float,
        default=0.50,
        help="Hard lower median threshold for covered classification",
    )
    parser.add_argument(
        "--drop-led-below",
        type=int,
        default=4,
        help="Drop covered pixels with led index < this value (cleanup for edge glare)",
    )
    parser.add_argument(
        "--use-on-off-diff",
        action="store_true",
        help="Fuse optional full-on/full-off ratio into covered classification",
    )
    parser.add_argument(
        "--on-off-threshold",
        type=float,
        default=0.72,
        help="Threshold for normalized (full_on-full_off) ratio when --use-on-off-diff is enabled",
    )
    parser.add_argument(
        "--use-mask-pattern",
        action="store_true",
        help="Fuse optional current-mask pattern ratio into covered classification",
    )
    parser.add_argument(
        "--mask-pattern-threshold",
        type=float,
        default=0.75,
        help="Threshold for normalized current-mask pattern ratio when --use-mask-pattern is enabled",
    )
    parser.add_argument(
        "--output-map",
        default="config/plant_pixel_map.json",
        help="Output JSON mask path",
    )
    parser.add_argument(
        "--overlay-image",
        default="calibration_photos/plant_pixel_overlay.jpg",
        help="Output overlay image path",
    )
    parser.add_argument(
        "--heatmap-image",
        default="calibration_photos/plant_pixel_heatmap.png",
        help="Output logical heatmap image path",
    )
    return parser.parse_args()


def build_cli_epilog() -> str:
    return (
        "Pattern order contract:\n"
        "  position 1 -> orientation_markers\n"
        "  position 2 -> major_grid_lines\n"
        "  position 3 -> checkerboard\n"
        "  position 4 -> coordinate_gradient\n"
        "  position 5 -> full_white\n"
        "\n"
        "Recommended process:\n"
        "  1) Auto run first.\n"
        "  2) Review overlay for corner alignment and false positives.\n"
        "  3) Copy printed corners into --corners for locked perspective.\n"
        "  4) Adjust thresholds only if needed.\n"
        "\n"
        "Optional supplemental fusion:\n"
        "  - Capture full-on and full-off stills; pass --full-on-file and\n"
        "    --full-off-file, then enable --use-on-off-diff.\n"
        "  - Capture current mask highlight still; pass --mask-pattern-file and\n"
        "    optionally --mask-map-path, then enable --use-mask-pattern.\n"
        "\n"
        "Example:\n"
        "  python scripts/calibrate.py --image-dir calibration_photos\n"
    )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def luminance(rgb: Color) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int((len(vals) - 1) * q)
    return vals[idx]


def smooth_1d(values: Sequence[float], radius: int) -> List[float]:
    out = [0.0] * len(values)
    for i in range(len(values)):
        lo = max(0, i - radius)
        hi = min(len(values), i + radius + 1)
        span = hi - lo
        out[i] = sum(values[lo:hi]) / span
    return out


def runs_above(values: Sequence[float], threshold: float) -> List[Tuple[int, int]]:
    runs: List[Tuple[int, int]] = []
    in_run = False
    start = 0
    for i, v in enumerate(values):
        if v > threshold and not in_run:
            in_run = True
            start = i
        if in_run and (v <= threshold or i == len(values) - 1):
            end = i if v <= threshold else i
            runs.append((start, end))
            in_run = False
    return runs


def longest_run(runs: Sequence[Tuple[int, int]]) -> Tuple[int, int]:
    if not runs:
        return 0, 0
    return max(runs, key=lambda r: r[1] - r[0])


def robust_fit_y_of_x(points: Sequence[Point], tol: float = 6.0, iters: int = 4) -> Tuple[float, float]:
    """Fit y = a*x + b with simple robust trimming."""
    pts = list(points)
    if not pts:
        return 0.0, 0.0
    for _ in range(iters):
        a, b = _fit_y_of_x(pts)
        residuals = [(abs(y - (a * x + b)), x, y) for x, y in pts]
        residuals.sort(key=lambda t: t[0])
        med = residuals[len(residuals) // 2][0]
        threshold = max(tol, med * 2.5)
        pts = [(x, y) for r, x, y in residuals if r <= threshold]
        if len(pts) < 2:
            break
    return _fit_y_of_x(pts)


def robust_fit_x_of_y(points: Sequence[Point], tol: float = 6.0, iters: int = 4) -> Tuple[float, float]:
    """Fit x = a*y + b with simple robust trimming."""
    pts = list(points)
    if not pts:
        return 0.0, 0.0
    for _ in range(iters):
        a, b = _fit_x_of_y(pts)
        residuals = [(abs(x - (a * y + b)), x, y) for x, y in pts]
        residuals.sort(key=lambda t: t[0])
        med = residuals[len(residuals) // 2][0]
        threshold = max(tol, med * 2.5)
        pts = [(x, y) for r, x, y in residuals if r <= threshold]
        if len(pts) < 2:
            break
    return _fit_x_of_y(pts)


def _fit_y_of_x(points: Sequence[Point]) -> Tuple[float, float]:
    if not points:
        return 0.0, 0.0
    n = float(len(points))
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return 0.0, sy / n
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return a, b


def _fit_x_of_y(points: Sequence[Point]) -> Tuple[float, float]:
    if not points:
        return 0.0, 0.0
    n = float(len(points))
    sy = sum(p[1] for p in points)
    sx = sum(p[0] for p in points)
    syy = sum(p[1] * p[1] for p in points)
    syx = sum(p[1] * p[0] for p in points)
    den = n * syy - sy * sy
    if abs(den) < 1e-9:
        return 0.0, sx / n
    a = (n * syx - sy * sx) / den
    b = (sx - a * sy) / n
    return a, b


def intersect_yx_and_xy(
    y_line: Tuple[float, float], x_line: Tuple[float, float]
) -> Point:
    """
    Intersect lines:
      y = a*x + b
      x = c*y + d
    """
    a, b = y_line
    c, d = x_line
    den = 1.0 - c * a
    if abs(den) < 1e-9:
        return d, a * d + b
    x = (c * b + d) / den
    y = a * x + b
    return x, y


def load_images(image_dir: Path, pattern_files: Sequence[str]) -> List[Image.Image]:
    images: List[Image.Image] = []
    for name in pattern_files:
        path = image_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Missing image: {path}")
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        images.append(img)
    return images


def load_optional_image(image_dir: Path, file_arg: str) -> Optional[Image.Image]:
    if not file_arg:
        return None
    p = Path(file_arg)
    if not p.is_absolute():
        p = image_dir / p
    if not p.exists():
        raise FileNotFoundError(f"Missing supplemental image: {p}")
    return ImageOps.exif_transpose(Image.open(p)).convert("RGB")


def load_mask_indices(mask_map_path: str, strips: int, leds: int) -> Set[int]:
    p = Path(mask_map_path)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[1] / p).resolve()
    payload = json.loads(p.read_text(encoding="utf-8"))
    total = strips * leds
    indices: Set[int] = set()
    raw_indices = payload.get("covered_indices")
    if isinstance(raw_indices, list):
        for v in raw_indices:
            try:
                idx = int(v)
            except Exception:
                continue
            if 0 <= idx < total:
                indices.add(idx)
        return indices
    raw_pixels = payload.get("covered_pixels")
    if isinstance(raw_pixels, list):
        for px in raw_pixels:
            if not isinstance(px, dict):
                continue
            try:
                s = int(px.get("strip", -1))
                l = int(px.get("led", -1))
            except Exception:
                continue
            if 0 <= s < strips and 0 <= l < leds:
                indices.add(s * leds + l)
    return indices


def detect_panel_corners_auto(images: Sequence[Image.Image], downsample: int) -> PanelCorners:
    """
    Auto corner detection:
    - Use checkerboard frame (index 2) for texture
    - Find dominant texture runs for axis-aligned ROI
    - Refine each edge with gradient-based line fits
    """
    checker = images[2]
    ds = max(1, int(downsample))
    small = checker.resize((checker.width // ds, checker.height // ds), Image.Resampling.BILINEAR)
    px = small.load()
    w, h = small.size

    # Luminance map
    lum = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            lum[y][x] = luminance(px[x, y])

    # Texture signals (horizontal texture for rows, vertical texture for cols)
    row_texture = [0.0] * h
    for y in range(h):
        s = 0.0
        for x in range(w - 1):
            s += abs(lum[y][x + 1] - lum[y][x])
        row_texture[y] = s / max(1, (w - 1))

    col_texture = [0.0] * w
    for x in range(w):
        s = 0.0
        for y in range(h - 1):
            s += abs(lum[y + 1][x] - lum[y][x])
        col_texture[x] = s / max(1, (h - 1))

    row_s = smooth_1d(row_texture, radius=8)
    col_s = smooth_1d(col_texture, radius=8)

    r_thr = min(row_s) + (max(row_s) - min(row_s)) * 0.45
    c_thr = min(col_s) + (max(col_s) - min(col_s)) * 0.45
    row_run = longest_run(runs_above(row_s, r_thr))
    col_run = longest_run(runs_above(col_s, c_thr))
    y1, y2 = row_run
    x1, x2 = col_run

    # Refine top/bottom using vertical gradients inside x-range
    top_points: List[Point] = []
    bottom_points: List[Point] = []
    top_lo = max(0, y1 - int(0.08 * h))
    top_hi = min(h - 2, y1 + int(0.08 * h))
    bot_lo = max(0, y2 - int(0.08 * h))
    bot_hi = min(h - 2, y2 + int(0.08 * h))
    for x in range(max(1, x1), min(w - 1, x2), 2):
        best_top_y = top_lo
        best_top_g = -1e9
        for y in range(top_lo, top_hi + 1):
            g = lum[y + 1][x] - lum[y][x]
            if g > best_top_g:
                best_top_g = g
                best_top_y = y
        if best_top_g > 5.0:
            top_points.append((float(x), float(best_top_y)))

        best_bot_y = bot_lo
        best_bot_g = 1e9
        for y in range(bot_lo, bot_hi + 1):
            g = lum[y + 1][x] - lum[y][x]
            if g < best_bot_g:
                best_bot_g = g
                best_bot_y = y
        if best_bot_g < -5.0:
            bottom_points.append((float(x), float(best_bot_y)))

    # Refine left/right using horizontal gradients inside y-range
    left_points: List[Point] = []
    right_points: List[Point] = []
    left_lo = max(0, x1 - int(0.08 * w))
    left_hi = min(w - 2, x1 + int(0.08 * w))
    right_lo = max(0, x2 - int(0.08 * w))
    right_hi = min(w - 2, x2 + int(0.08 * w))
    for y in range(max(1, y1), min(h - 1, y2), 2):
        best_left_x = left_lo
        best_left_g = -1e9
        for x in range(left_lo, left_hi + 1):
            g = lum[y][x + 1] - lum[y][x]
            if g > best_left_g:
                best_left_g = g
                best_left_x = x
        if best_left_g > 5.0:
            left_points.append((float(best_left_x), float(y)))

        best_right_x = right_lo
        best_right_g = 1e9
        for x in range(right_lo, right_hi + 1):
            g = lum[y][x + 1] - lum[y][x]
            if g < best_right_g:
                best_right_g = g
                best_right_x = x
        if best_right_g < -5.0:
            right_points.append((float(best_right_x), float(y)))

    top_line = robust_fit_y_of_x(top_points, tol=6.0)
    bottom_line = robust_fit_y_of_x(bottom_points, tol=6.0)
    left_line = robust_fit_x_of_y(left_points, tol=6.0)
    right_line = robust_fit_x_of_y(right_points, tol=6.0)

    tl = intersect_yx_and_xy(top_line, left_line)
    tr = intersect_yx_and_xy(top_line, right_line)
    br = intersect_yx_and_xy(bottom_line, right_line)
    bl = intersect_yx_and_xy(bottom_line, left_line)

    return PanelCorners(
        tl=(tl[0] * ds, tl[1] * ds),
        tr=(tr[0] * ds, tr[1] * ds),
        br=(br[0] * ds, br[1] * ds),
        bl=(bl[0] * ds, bl[1] * ds),
    )


def parse_manual_corners(corners_str: str) -> PanelCorners:
    vals = [float(v.strip()) for v in corners_str.split(",") if v.strip()]
    if len(vals) != 8:
        raise ValueError("Expected 8 comma-separated numbers for --corners")
    return PanelCorners(
        tl=(vals[0], vals[1]),
        tr=(vals[2], vals[3]),
        br=(vals[4], vals[5]),
        bl=(vals[6], vals[7]),
    )


def corners_to_arg(corners: PanelCorners) -> str:
    vals = [
        corners.tl[0],
        corners.tl[1],
        corners.tr[0],
        corners.tr[1],
        corners.br[0],
        corners.br[1],
        corners.bl[0],
        corners.bl[1],
    ]
    return ",".join(f"{v:.3f}" for v in vals)


def assumptions_block(strips: int, leds: int) -> Dict[str, object]:
    return {
        "pattern_sequence": PATTERN_SEQUENCE_LABELS,
        "exif_transpose_applied": True,
        "logical_layout": {
            "strip_count": strips,
            "leds_per_strip": leds,
            "index_formula": "index = strip * leds_per_strip + led",
        },
        "capture_guidance": [
            "Use fixed camera position across all calibration photos.",
            "Keep full panel visible.",
            "Do not pre-rotate/pre-crop before calibration.",
            "Optional: capture supplemental full-on/full-off and current-mask photos.",
        ],
        "iteration_guidance": [
            "Inspect overlay and corner polygon after each run.",
            "Lock perspective with --corners using prior run output.",
            "Tune thresholds after corner alignment is stable.",
            "Optionally enable supplemental diff fusion once base alignment is stable.",
        ],
    }


def build_rerun_command(args: argparse.Namespace, corners: PanelCorners) -> str:
    corners_arg = corners_to_arg(corners)
    parts = [
        "python scripts/calibrate.py",
        f"--image-dir {args.image_dir}",
        f"--pattern-files {args.pattern_files}",
        f"--strips {args.strips}",
        f"--leds-per-strip {args.leds_per_strip}",
        f"--corners \"{corners_arg}\"",
        f"--flip-x {args.flip_x}",
        f"--flip-y {args.flip_y}",
        f"--med-threshold {args.med_threshold}",
        f"--max-threshold {args.max_threshold}",
        f"--hard-med-threshold {args.hard_med_threshold}",
        f"--drop-led-below {args.drop_led_below}",
        f"--output-map {args.output_map}",
        f"--overlay-image {args.overlay_image}",
        f"--heatmap-image {args.heatmap_image}",
    ]
    if args.full_on_file:
        parts.append(f"--full-on-file {args.full_on_file}")
    if args.full_off_file:
        parts.append(f"--full-off-file {args.full_off_file}")
    if args.mask_pattern_file:
        parts.append(f"--mask-pattern-file {args.mask_pattern_file}")
    if args.mask_map_path:
        parts.append(f"--mask-map-path {args.mask_map_path}")
    if args.use_on_off_diff:
        parts.append("--use-on-off-diff")
    parts.append(f"--on-off-threshold {args.on_off_threshold}")
    if args.use_mask_pattern:
        parts.append("--use-mask-pattern")
    parts.append(f"--mask-pattern-threshold {args.mask_pattern_threshold}")
    return " ".join(parts)


def solve_linear_system(a: List[List[float]], b: List[float]) -> List[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("Singular matrix while solving homography")
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]

        pv = m[col][col]
        for j in range(col, n + 1):
            m[col][j] /= pv

        for r in range(n):
            if r == col:
                continue
            factor = m[r][col]
            if factor == 0.0:
                continue
            for j in range(col, n + 1):
                m[r][j] -= factor * m[col][j]

    return [m[i][n] for i in range(n)]


def solve_homography_unit_to_panel(corners: PanelCorners) -> List[float]:
    """
    Solve homography from (u,v) in unit square to image (x,y):
      [x, y, 1]^T ~ H * [u, v, 1]^T
    with H33 fixed to 1.
    """
    src = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    dst = [corners.tl, corners.tr, corners.br, corners.bl]

    a: List[List[float]] = []
    b: List[float] = []
    for (u, v), (x, y) in zip(src, dst):
        a.append([u, v, 1.0, 0.0, 0.0, 0.0, -x * u, -x * v])
        b.append(x)
        a.append([0.0, 0.0, 0.0, u, v, 1.0, -y * u, -y * v])
        b.append(y)

    h = solve_linear_system(a, b)
    return h  # [h11,h12,h13,h21,h22,h23,h31,h32], h33=1


def map_uv_to_xy(h: Sequence[float], u: float, v: float) -> Point:
    h11, h12, h13, h21, h22, h23, h31, h32 = h
    den = h31 * u + h32 * v + 1.0
    if abs(den) < 1e-9:
        den = 1e-9
    x = (h11 * u + h12 * v + h13) / den
    y = (h21 * u + h22 * v + h23) / den
    return x, y


def logical_uv(
    strip: int,
    led: int,
    strips: int,
    leds: int,
    flip_x: bool,
    flip_y: bool,
) -> Tuple[float, float]:
    sx = (strips - 1 - strip) if flip_x else strip
    ly = (leds - 1 - led) if flip_y else led
    u = sx / max(1, strips - 1)
    v = ly / max(1, leds - 1)
    return u, v


def sample_luma_bilinear(img: Image.Image, x: float, y: float) -> float:
    w, h = img.size
    x = max(0.0, min(float(w - 1), x))
    y = max(0.0, min(float(h - 1), y))
    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
    x1 = min(w - 1, x0 + 1)
    y1 = min(h - 1, y0 + 1)
    fx = x - x0
    fy = y - y0
    px = img.load()
    c00 = luminance(px[x0, y0])
    c10 = luminance(px[x1, y0])
    c01 = luminance(px[x0, y1])
    c11 = luminance(px[x1, y1])
    return (
        c00 * (1.0 - fx) * (1.0 - fy)
        + c10 * fx * (1.0 - fy)
        + c01 * (1.0 - fx) * fy
        + c11 * fx * fy
    )


def expected_luma_patterns(strips: int, leds: int) -> List[List[List[float]]]:
    dummy = _DummyController(strips=strips, leds_per_strip=leds)
    anim = PlantCalibrationAnimation(dummy, {})
    expected: List[List[List[float]]] = []
    for frame in anim._pattern_frames:
        arr = [[0.0] * leds for _ in range(strips)]
        for s in range(strips):
            for l in range(leds):
                arr[s][l] = luminance(frame[s * leds + l])
        expected.append(arr)
    return expected


def corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n == 0:
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    va = sum((x - ma) * (x - ma) for x in a)
    vb = sum((y - mb) * (y - mb) for y in b)
    if va < 1e-9 or vb < 1e-9:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def detect_flips(
    images: Sequence[Image.Image],
    expected: Sequence[Sequence[Sequence[float]]],
    h: Sequence[float],
    strips: int,
    leds: int,
    flip_x_mode: str,
    flip_y_mode: str,
) -> Tuple[bool, bool]:
    if flip_x_mode != "auto" and flip_y_mode != "auto":
        return flip_x_mode == "true", flip_y_mode == "true"

    x_candidates = [False, True] if flip_x_mode == "auto" else [flip_x_mode == "true"]
    y_candidates = [False, True] if flip_y_mode == "auto" else [flip_y_mode == "true"]

    best_score = -1e9
    best = (False, False)
    pattern_ids = [1, 2, 3]  # grid, checker, gradient

    for fx in x_candidates:
        for fy in y_candidates:
            total = 0.0
            for pid in pattern_ids:
                observed_vals: List[float] = []
                expected_vals: List[float] = []
                for s in range(strips):
                    sx = (strips - 1 - s) if fx else s
                    u = sx / max(1, strips - 1)
                    for l in range(leds):
                        ly = (leds - 1 - l) if fy else l
                        v = ly / max(1, leds - 1)
                        x, y = map_uv_to_xy(h, u, v)
                        observed_vals.append(sample_luma_bilinear(images[pid], x, y))
                        expected_vals.append(expected[pid][s][l])
                total += corr(observed_vals, expected_vals)
            if total > best_score:
                best_score = total
                best = (fx, fy)

    return best


def build_on_off_ratio_map(
    full_on: Image.Image,
    full_off: Image.Image,
    h: Sequence[float],
    strips: int,
    leds: int,
    flip_x: bool,
    flip_y: bool,
) -> Dict[Tuple[int, int], float]:
    deltas = [[0.0] * leds for _ in range(strips)]
    for s in range(strips):
        for l in range(leds):
            u, v = logical_uv(s, l, strips, leds, flip_x, flip_y)
            x, y = map_uv_to_xy(h, u, v)
            on_l = sample_luma_bilinear(full_on, x, y)
            off_l = sample_luma_bilinear(full_off, x, y)
            deltas[s][l] = max(0.0, on_l - off_l)

    per_led_baseline = [1.0] * leds
    for l in range(leds):
        vals = [deltas[s][l] for s in range(strips)]
        per_led_baseline[l] = max(1e-6, percentile(vals, 0.80))

    ratios: Dict[Tuple[int, int], float] = {}
    for s in range(strips):
        for l in range(leds):
            ratios[(s, l)] = deltas[s][l] / per_led_baseline[l]
    return ratios


def build_mask_pattern_ratio_map(
    mask_pattern: Image.Image,
    full_off: Image.Image,
    expected_mask_indices: Set[int],
    h: Sequence[float],
    strips: int,
    leds: int,
    flip_x: bool,
    flip_y: bool,
) -> Dict[Tuple[int, int], float]:
    deltas = [[0.0] * leds for _ in range(strips)]
    for s in range(strips):
        for l in range(leds):
            idx = s * leds + l
            if idx not in expected_mask_indices:
                continue
            u, v = logical_uv(s, l, strips, leds, flip_x, flip_y)
            x, y = map_uv_to_xy(h, u, v)
            mask_l = sample_luma_bilinear(mask_pattern, x, y)
            off_l = sample_luma_bilinear(full_off, x, y)
            deltas[s][l] = max(0.0, mask_l - off_l)

    per_led_baseline = [1.0] * leds
    for l in range(leds):
        vals = [deltas[s][l] for s in range(strips) if deltas[s][l] > 0.0]
        if vals:
            per_led_baseline[l] = max(1e-6, percentile(vals, 0.80))
        else:
            per_led_baseline[l] = 1.0

    ratios: Dict[Tuple[int, int], float] = {}
    for s in range(strips):
        for l in range(leds):
            idx = s * leds + l
            if idx not in expected_mask_indices:
                continue
            ratios[(s, l)] = deltas[s][l] / per_led_baseline[l]
    return ratios


def classify_covered_pixels(
    images: Sequence[Image.Image],
    expected: Sequence[Sequence[Sequence[float]]],
    h: Sequence[float],
    strips: int,
    leds: int,
    flip_x: bool,
    flip_y: bool,
    med_threshold: float,
    max_threshold: float,
    hard_med_threshold: float,
    drop_led_below: int,
    on_off_ratio: Optional[Dict[Tuple[int, int], float]] = None,
    mask_pattern_ratio: Optional[Dict[Tuple[int, int], float]] = None,
    use_on_off_diff: bool = False,
    on_off_threshold: float = 0.72,
    use_mask_pattern: bool = False,
    mask_pattern_threshold: float = 0.75,
) -> Tuple[Dict[Tuple[int, int], Tuple[float, float]], ClassificationStats]:
    # Observed luminance [pattern][strip][led]
    observed = [[[0.0] * leds for _ in range(strips)] for _ in range(len(images))]
    for p in range(len(images)):
        for s in range(strips):
            sx = (strips - 1 - s) if flip_x else s
            u = sx / max(1, strips - 1)
            for l in range(leds):
                ly = (leds - 1 - l) if flip_y else l
                v = ly / max(1, leds - 1)
                x, y = map_uv_to_xy(h, u, v)
                observed[p][s][l] = sample_luma_bilinear(images[p], x, y)

    gains: List[float] = [1.0] * len(images)
    for p in range(len(images)):
        ratios: List[float] = []
        for s in range(strips):
            for l in range(leds):
                e = expected[p][s][l]
                if e >= 70.0:
                    ratios.append(observed[p][s][l] / e)
        gains[p] = percentile(ratios, 0.5) if ratios else 1.0

    # Per-led baseline to remove global vertical shading/exposure falloff
    baseline = [[1.0] * leds for _ in range(len(images))]
    for p in [1, 2, 3, 4]:
        for l in range(leds):
            ratios = []
            for s in range(strips):
                e = expected[p][s][l]
                if e >= 55.0:
                    raw = observed[p][s][l] / max(1e-6, gains[p] * e)
                    ratios.append(raw)
            baseline[p][l] = max(0.25, percentile(ratios, 0.80)) if ratios else 1.0

    covered: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for s in range(strips):
        for l in range(leds):
            ratios = []
            for p in [1, 2, 3, 4]:
                e = expected[p][s][l]
                if e < 55.0:
                    continue
                raw = observed[p][s][l] / max(1e-6, gains[p] * e)
                norm = raw / max(1e-6, baseline[p][l])
                ratios.append(norm)
            if not ratios:
                continue
            ratios.sort()
            med = ratios[len(ratios) // 2]
            mx = ratios[-1]
            is_cov = (med < med_threshold and mx < max_threshold) or (med < hard_med_threshold)
            if use_on_off_diff and on_off_ratio is not None:
                oor = on_off_ratio.get((s, l))
                if oor is not None and oor < on_off_threshold and med < 0.95:
                    is_cov = True
            if use_mask_pattern and mask_pattern_ratio is not None:
                mpr = mask_pattern_ratio.get((s, l))
                if mpr is not None and mpr < mask_pattern_threshold and med < 0.95:
                    is_cov = True
            if is_cov:
                covered[(s, l)] = (med, mx)

    before_cleanup = len(covered)

    # Neighbor cleanup + optional early-led drop
    cleaned: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for (s, l), (med, mx) in covered.items():
        neighbors = 0
        for ds in (-1, 0, 1):
            for dl in (-1, 0, 1):
                if ds == 0 and dl == 0:
                    continue
                if (s + ds, l + dl) in covered:
                    neighbors += 1
        if neighbors < 1:
            continue
        if l < drop_led_below:
            continue
        cleaned[(s, l)] = (med, mx)

    stats = ClassificationStats(
        gains=gains,
        selected_flip_x=flip_x,
        selected_flip_y=flip_y,
        covered_before_cleanup=before_cleanup,
        covered_after_cleanup=len(cleaned),
    )
    return cleaned, stats


def write_mask_json(
    output_map: Path,
    strips: int,
    leds: int,
    covered: Dict[Tuple[int, int], Tuple[float, float]],
    image_names: Sequence[str],
    corners: PanelCorners,
    stats: ClassificationStats,
    args: argparse.Namespace,
    rerun_command: str,
    on_off_ratio: Optional[Dict[Tuple[int, int], float]] = None,
    mask_pattern_ratio: Optional[Dict[Tuple[int, int], float]] = None,
) -> None:
    ensure_parent(output_map)

    coords = []
    for (s, l), (med, mx) in covered.items():
        idx = s * leds + l
        coords.append(
            {
                "strip": s,
                "led": l,
                "index": idx,
                "median_ratio": round(float(med), 6),
                "max_ratio": round(float(mx), 6),
            }
        )
        if on_off_ratio is not None and (s, l) in on_off_ratio:
            coords[-1]["on_off_ratio"] = round(float(on_off_ratio[(s, l)]), 6)
        if mask_pattern_ratio is not None and (s, l) in mask_pattern_ratio:
            coords[-1]["mask_pattern_ratio"] = round(float(mask_pattern_ratio[(s, l)]), 6)
    coords.sort(key=lambda d: d["index"])
    indices = [c["index"] for c in coords]

    payload = {
        "strip_count": strips,
        "leds_per_strip": leds,
        "covered_count": len(coords),
        "covered_indices": indices,
        "covered_pixels": coords,
        "assumptions": assumptions_block(strips=strips, leds=leds),
        "calibration": {
            "pattern_files": list(image_names),
            "exif_transpose": True,
            "panel_corners_upright_raw": corners.as_dict(),
            "flip_x": stats.selected_flip_x,
            "flip_y": stats.selected_flip_y,
            "gains": [round(float(g), 6) for g in stats.gains],
            "covered_before_cleanup": stats.covered_before_cleanup,
            "covered_after_cleanup": stats.covered_after_cleanup,
            "med_threshold": args.med_threshold,
            "max_threshold": args.max_threshold,
            "hard_med_threshold": args.hard_med_threshold,
            "drop_led_below": args.drop_led_below,
            "corners_source": "manual" if args.corners else "auto",
            "supplemental_files": {
                "full_on_file": args.full_on_file or None,
                "full_off_file": args.full_off_file or None,
                "mask_pattern_file": args.mask_pattern_file or None,
                "mask_map_path": args.mask_map_path or None,
            },
            "use_on_off_diff": bool(args.use_on_off_diff),
            "on_off_threshold": args.on_off_threshold,
            "use_mask_pattern": bool(args.use_mask_pattern),
            "mask_pattern_threshold": args.mask_pattern_threshold,
            "rerun_command_template": rerun_command,
        },
    }

    output_map.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_overlay(
    overlay_path: Path,
    base_image: Image.Image,
    corners: PanelCorners,
    covered: Dict[Tuple[int, int], Tuple[float, float]],
    strips: int,
    leds: int,
    h: Sequence[float],
    flip_x: bool,
    flip_y: bool,
) -> None:
    ensure_parent(overlay_path)
    out = base_image.copy()
    draw = ImageDraw.Draw(out)

    draw.polygon([corners.tl, corners.tr, corners.br, corners.bl], outline=(255, 64, 64), width=6)

    for (s, l) in covered.keys():
        sx = (strips - 1 - s) if flip_x else s
        ly = (leds - 1 - l) if flip_y else l
        u = sx / max(1, strips - 1)
        v = ly / max(1, leds - 1)
        x, y = map_uv_to_xy(h, u, v)
        r = 6
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 32, 32), outline=(255, 180, 180))

    out.save(overlay_path, quality=92)


def write_heatmap(
    heatmap_path: Path,
    strips: int,
    leds: int,
    covered: Dict[Tuple[int, int], Tuple[float, float]],
) -> None:
    ensure_parent(heatmap_path)
    img = Image.new("RGB", (strips, leds), color=(20, 220, 20))
    for s in range(strips):
        for l in range(leds):
            if (s, l) in covered:
                img.putpixel((s, l), (220, 30, 30))
    img = img.resize((strips * 10, leds * 10), Image.Resampling.NEAREST)
    img.save(heatmap_path)


def main() -> None:
    args = parse_args()
    image_dir = Path(args.image_dir).resolve()
    pattern_files = [name.strip() for name in args.pattern_files.split(",") if name.strip()]
    if len(pattern_files) != len(PATTERN_SEQUENCE_LABELS):
        raise ValueError(f"Expected exactly {len(PATTERN_SEQUENCE_LABELS)} files in --pattern-files")

    images = load_images(image_dir, pattern_files)
    full_on_image = load_optional_image(image_dir, args.full_on_file)
    full_off_image = load_optional_image(image_dir, args.full_off_file)
    mask_pattern_image = load_optional_image(image_dir, args.mask_pattern_file)

    if args.use_on_off_diff and (full_on_image is None or full_off_image is None):
        raise ValueError("--use-on-off-diff requires both --full-on-file and --full-off-file")
    if args.use_mask_pattern and (mask_pattern_image is None or full_off_image is None):
        raise ValueError("--use-mask-pattern requires --mask-pattern-file and --full-off-file")

    strips = int(args.strips)
    leds = int(args.leds_per_strip)
    expected = expected_luma_patterns(strips, leds)

    if args.corners:
        corners = parse_manual_corners(args.corners)
    else:
        corners = detect_panel_corners_auto(images, downsample=args.auto_downsample)

    h = solve_homography_unit_to_panel(corners)

    flip_x, flip_y = detect_flips(
        images=images,
        expected=expected,
        h=h,
        strips=strips,
        leds=leds,
        flip_x_mode=args.flip_x,
        flip_y_mode=args.flip_y,
    )

    on_off_ratio = None
    if full_on_image is not None and full_off_image is not None:
        on_off_ratio = build_on_off_ratio_map(
            full_on=full_on_image,
            full_off=full_off_image,
            h=h,
            strips=strips,
            leds=leds,
            flip_x=flip_x,
            flip_y=flip_y,
        )

    mask_pattern_ratio = None
    if mask_pattern_image is not None and full_off_image is not None:
        expected_mask_indices = load_mask_indices(args.mask_map_path, strips=strips, leds=leds)
        mask_pattern_ratio = build_mask_pattern_ratio_map(
            mask_pattern=mask_pattern_image,
            full_off=full_off_image,
            expected_mask_indices=expected_mask_indices,
            h=h,
            strips=strips,
            leds=leds,
            flip_x=flip_x,
            flip_y=flip_y,
        )

    covered, stats = classify_covered_pixels(
        images=images,
        expected=expected,
        h=h,
        strips=strips,
        leds=leds,
        flip_x=flip_x,
        flip_y=flip_y,
        med_threshold=args.med_threshold,
        max_threshold=args.max_threshold,
        hard_med_threshold=args.hard_med_threshold,
        drop_led_below=args.drop_led_below,
        on_off_ratio=on_off_ratio,
        mask_pattern_ratio=mask_pattern_ratio,
        use_on_off_diff=args.use_on_off_diff,
        on_off_threshold=args.on_off_threshold,
        use_mask_pattern=args.use_mask_pattern,
        mask_pattern_threshold=args.mask_pattern_threshold,
    )

    output_map = Path(args.output_map).resolve()
    overlay_path = Path(args.overlay_image).resolve()
    heatmap_path = Path(args.heatmap_image).resolve()
    rerun_command = build_rerun_command(args=args, corners=corners)

    write_mask_json(
        output_map=output_map,
        strips=strips,
        leds=leds,
        covered=covered,
        image_names=pattern_files,
        corners=corners,
        stats=stats,
        args=args,
        rerun_command=rerun_command,
        on_off_ratio=on_off_ratio,
        mask_pattern_ratio=mask_pattern_ratio,
    )
    write_overlay(
        overlay_path=overlay_path,
        base_image=images[2],  # checkerboard image
        corners=corners,
        covered=covered,
        strips=strips,
        leds=leds,
        h=h,
        flip_x=flip_x,
        flip_y=flip_y,
    )
    write_heatmap(
        heatmap_path=heatmap_path,
        strips=strips,
        leds=leds,
        covered=covered,
    )

    print("Calibration complete")
    print(f"  Images: {image_dir}")
    print(f"  Output map: {output_map}")
    print(f"  Overlay: {overlay_path}")
    print(f"  Heatmap: {heatmap_path}")
    print(f"  Covered pixels: {stats.covered_after_cleanup}/{strips * leds}")
    print(f"  Flip X: {flip_x}, Flip Y: {flip_y}")
    print(f"  Supplemental full-on/off loaded: {bool(full_on_image and full_off_image)}")
    print(f"  Supplemental mask-pattern loaded: {bool(mask_pattern_image)}")
    print(f"  Use on/off diff fusion: {bool(args.use_on_off_diff)}")
    print(f"  Use mask-pattern fusion: {bool(args.use_mask_pattern)}")
    print("  Corners (upright raw pixels):")
    print(f"    TL={corners.tl}")
    print(f"    TR={corners.tr}")
    print(f"    BR={corners.br}")
    print(f"    BL={corners.bl}")
    print("  Next iteration command template:")
    print(f"    {rerun_command}")


if __name__ == "__main__":
    main()

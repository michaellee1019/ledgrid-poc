#!/usr/bin/env python3
"""
Prepare source GIF files for the LED wall.

This script resizes/normalizes GIFs to target wall dimensions and writes
new GIF files into an output directory used by GifAnimation.
"""

import argparse
from pathlib import Path
from typing import Iterable, List

try:
    from PIL import Image, ImageSequence
except ImportError as exc:  # pragma: no cover - runtime environment dependent
    raise SystemExit("Pillow is required. Install with: pip install pillow") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare GIF assets for LED wall playback")
    parser.add_argument("--input-dir", required=True, help="Directory containing source GIF files")
    parser.add_argument("--output-dir", default="assets/gifs", help="Directory to write normalized GIF files")
    parser.add_argument("--width", type=int, default=32, help="Target wall width in pixels")
    parser.add_argument("--height", type=int, default=140, help="Target wall height in pixels")
    parser.add_argument(
        "--fit-mode",
        choices=["stretch", "contain", "cover"],
        default="stretch",
        help="How to fit source frames into target dimensions",
    )
    parser.add_argument(
        "--contain-background",
        type=int,
        default=0,
        help="Background grayscale level (0-255) used for contain mode letterboxing",
    )
    parser.add_argument(
        "--resample",
        choices=["nearest", "bilinear", "bicubic", "lanczos"],
        default="bilinear",
        help="Resampling filter for scaling",
    )
    parser.add_argument(
        "--max-fps",
        type=float,
        default=0.0,
        help="Optional FPS cap (0 disables cap)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output GIFs",
    )
    return parser.parse_args()


def get_resampling(name: str):
    mapping = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }
    return mapping[name]


def fit_frame(image: Image.Image, width: int, height: int, fit_mode: str, contain_background: int, resample) -> Image.Image:
    if fit_mode == "stretch":
        return image.resize((width, height), resample)

    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", (width, height), (0, 0, 0, 255))

    if fit_mode == "cover":
        scale = max(width / src_w, height / src_h)
    else:
        scale = min(width / src_w, height / src_h)

    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h), resample)

    bg = max(0, min(255, int(contain_background)))
    canvas = Image.new("RGBA", (width, height), (bg, bg, bg, 255))
    offset_x = (width - new_w) // 2
    offset_y = (height - new_h) // 2
    canvas.alpha_composite(resized, (offset_x, offset_y))
    return canvas


def iter_gif_files(input_dir: Path) -> Iterable[Path]:
    for p in sorted(input_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".gif":
            yield p


def normalize_durations_ms(raw_durations: List[int], max_fps: float) -> List[int]:
    floor_ms = 10  # GIF timing granularity
    if max_fps > 0:
        floor_ms = max(floor_ms, int(round(1000.0 / max_fps)))
    normalized = []
    for ms in raw_durations:
        ms_value = max(10, int(ms))
        if ms_value < floor_ms:
            ms_value = floor_ms
        normalized.append(ms_value)
    return normalized


def process_gif(
    source_path: Path,
    output_path: Path,
    width: int,
    height: int,
    fit_mode: str,
    contain_background: int,
    resample,
    max_fps: float,
):
    frames: List[Image.Image] = []
    durations_ms: List[int] = []
    loop_count = 0

    with Image.open(source_path) as img:
        loop_count = int(img.info.get("loop", 0) or 0)
        for frame in ImageSequence.Iterator(img):
            rgba = frame.convert("RGBA")
            fitted = fit_frame(rgba, width, height, fit_mode, contain_background, resample)
            frames.append(fitted.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
            duration = int(frame.info.get("duration", img.info.get("duration", 100)) or 100)
            durations_ms.append(duration)

    if not frames:
        raise ValueError(f"No frames decoded from GIF: {source_path}")

    durations_ms = normalize_durations_ms(durations_ms, max_fps=max_fps)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        loop=loop_count,
        duration=durations_ms,
        optimize=False,
        disposal=2,
    )
    return len(frames)


def main():
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    gif_files = list(iter_gif_files(input_dir))
    if not gif_files:
        raise SystemExit(f"No GIF files found in input directory: {input_dir}")

    resample = get_resampling(args.resample)
    written = 0
    skipped = 0

    for source_path in gif_files:
        output_path = output_dir / source_path.name
        if output_path.exists() and not args.overwrite:
            skipped += 1
            print(f"skip {source_path.name} (exists)")
            continue

        frame_count = process_gif(
            source_path=source_path,
            output_path=output_path,
            width=max(1, args.width),
            height=max(1, args.height),
            fit_mode=args.fit_mode,
            contain_background=args.contain_background,
            resample=resample,
            max_fps=max(0.0, float(args.max_fps)),
        )
        written += 1
        print(f"ok   {source_path.name} -> {output_path.name} ({frame_count} frames)")

    print(f"\nDone. wrote={written} skipped={skipped} output_dir={output_dir}")


if __name__ == "__main__":
    main()

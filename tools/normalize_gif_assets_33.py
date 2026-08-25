#!/usr/bin/env python3
"""Pad curated 32x138 GIFs with one exact black strip and verify invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIRECTORY = ROOT / "animation" / "plugins" / "gif_animation" / "assets"
MANIFEST_PATH = ASSET_DIRECTORY / "normalization_v1.json"
SOURCE_WIDTH = 32
INSTALLED_WIDTH = 33
HEIGHT = 138


def _frame_digest(frame: Image.Image, *, width: int = SOURCE_WIDTH) -> str:
    crop = frame.convert("RGBA").crop((0, 0, width, HEIGHT))
    return hashlib.sha256(crop.tobytes()).hexdigest()


def _metadata(path: Path, *, require_installed: bool) -> dict[str, Any]:
    with Image.open(path) as image:
        expected_size = (INSTALLED_WIDTH if require_installed else SOURCE_WIDTH, HEIGHT)
        if image.size != expected_size:
            raise ValueError(f"{path.name} is {image.size}, expected {expected_size}")
        durations: list[int] = []
        disposals: list[int] = []
        frame_digests: list[str] = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            frame = image.copy()
            durations.append(int(image.info.get("duration", 0)))
            disposals.append(int(getattr(image, "disposal_method", 0)))
            frame_digests.append(_frame_digest(frame))
            if require_installed:
                tail = frame.convert("RGBA").crop(
                    (SOURCE_WIDTH, 0, INSTALLED_WIDTH, HEIGHT)
                )
                expected_tail = bytes((0, 0, 0, 255)) * HEIGHT
                if tail.tobytes() != expected_tail:
                    raise ValueError(
                        f"{path.name} frame {frame_index} tail strip is not opaque black"
                    )
        return {
            "frame_count": image.n_frames,
            "durations_ms": durations,
            "disposal_methods": disposals,
            "loop": int(image.info.get("loop", 0)),
            "transparency": image.info.get("transparency"),
            "source_frame_rgba_sha256": frame_digests,
        }


def _normalize(path: Path) -> dict[str, Any]:
    baseline = _metadata(path, require_installed=False)
    with Image.open(path) as image:
        frames: list[Image.Image] = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            source = image.convert("RGB")
            canvas = Image.new("RGB", (INSTALLED_WIDTH, HEIGHT), (0, 0, 0))
            canvas.paste(source, (0, 0))
            frames.append(canvas)

    temporary = path.with_name(f".{path.stem}.normalized.gif")
    frames[0].save(
        temporary,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=baseline["durations_ms"],
        disposal=baseline["disposal_methods"],
        loop=baseline["loop"],
        optimize=False,
    )
    try:
        normalized = _metadata(temporary, require_installed=True)
        if normalized != baseline:
            raise ValueError(
                f"{path.name} normalization changed decoded frames or animation metadata"
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return baseline


def _manifest(assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "ledgrid.gif-asset-normalization",
        "version": 1,
        "source_geometry": [SOURCE_WIDTH, HEIGHT],
        "installed_geometry": [INSTALLED_WIDTH, HEIGHT],
        "tail_policy": "append_opaque_black_strip_at_global_index_32",
        "digest_encoding": "decoded_frame_rgba_row_major_first_32_strips",
        "assets": dict(sorted(assets.items())),
    }


def normalize_assets() -> dict[str, Any]:
    paths = sorted(ASSET_DIRECTORY.glob("*.gif"))
    if not paths:
        raise ValueError(f"no curated GIF assets found in {ASSET_DIRECTORY}")

    sizes: dict[str, tuple[int, int]] = {}
    for path in paths:
        with Image.open(path) as image:
            sizes[path.name] = image.size

    installed_size = (INSTALLED_WIDTH, HEIGHT)
    source_size = (SOURCE_WIDTH, HEIGHT)
    if all(size == installed_size for size in sizes.values()):
        # A normal rerun is deliberately verification-only.  It must not make
        # drift legitimate by silently re-baselining the preservation hashes.
        return check_assets()
    if not all(size == source_size for size in sizes.values()):
        details = ", ".join(f"{name}={size}" for name, size in sizes.items())
        raise ValueError(f"curated GIF geometries are mixed or unsupported: {details}")

    assets: dict[str, dict[str, Any]] = {}
    for path in paths:
        assets[path.name] = _normalize(path)
    manifest = _manifest(assets)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def check_assets() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        raise ValueError(f"normalization manifest is missing: {MANIFEST_PATH}")
    expected = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    actual_assets = {
        path.name: _metadata(path, require_installed=True)
        for path in sorted(ASSET_DIRECTORY.glob("*.gif"))
    }
    actual = _manifest(actual_assets)
    if actual != expected:
        raise ValueError("curated GIF normalization manifest has drifted")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify without rewriting assets"
    )
    args = parser.parse_args()
    manifest = check_assets() if args.check else normalize_assets()
    print(
        f"verified {len(manifest['assets'])} GIF assets at "
        f"{INSTALLED_WIDTH}x{HEIGHT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

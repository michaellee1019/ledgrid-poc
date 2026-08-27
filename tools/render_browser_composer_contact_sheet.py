#!/usr/bin/env python3
"""Render every curated Python preset into exhaustive composer evidence.

Each card contains the real plugin's frame at t=0 and a second frame reached by
sequentially advancing to the plugin's first authored positive preview capture.
The JSON peer is intended for CI/review tooling; ``--check`` turns visual flags
into a non-zero exit status. Importing this module performs no rendering or I/O.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.base import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.preview_assets import PreviewRenderer, preview_profile
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


SCHEMA = "ledgrid.browser-composer-contact-sheet"
SCHEMA_VERSION = 1
FRAME_WIDTH = DEFAULT_STRIP_COUNT
FRAME_HEIGHT = DEFAULT_LEDS_PER_STRIP


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_label(value: Any) -> str:
    return str(value).encode("ascii", "replace").decode("ascii")


def _canonical_pixels(animation: Any, rendered: Any) -> np.ndarray:
    changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
    pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
    canonical = np.asarray(pixels)
    expected = (FRAME_WIDTH * FRAME_HEIGHT, 3)
    if canonical.shape != expected:
        raise ValueError(f"frame shape {canonical.shape} does not match {expected}")
    if canonical.dtype != np.uint8:
        raise ValueError(f"frame dtype {canonical.dtype} is not uint8")
    canonical = animation.apply_framework_plant_modifiers(
        canonical, changed=bool(changed)
    )
    canonical = np.asarray(canonical)
    if canonical.shape != expected or canonical.dtype != np.uint8:
        raise ValueError("framework modifier output broke the canonical frame contract")
    return np.ascontiguousarray(canonical)


def _image_oriented(canonical: np.ndarray) -> np.ndarray:
    """Convert strip-major, bottom-origin pixels to a top-down image canvas."""
    return canonical.reshape(FRAME_WIDTH, FRAME_HEIGHT, 3).transpose(1, 0, 2)[::-1].copy()


def _frame_summary(canonical: np.ndarray) -> dict[str, Any]:
    active = np.any(canonical != 0, axis=1)
    return {
        "sha256": _digest(canonical.tobytes(order="C")),
        "nonzero_pixels": int(np.count_nonzero(active)),
        "max_channel": int(canonical.max(initial=0)),
        "mean_channel": round(float(canonical.mean()), 6),
    }


def _semantic_target(manifest: dict[str, Any]) -> tuple[float, int, int]:
    captures, simulation_fps = preview_profile(manifest)
    target = next((value for value in captures if value > 0.0), 1.0 / simulation_fps)
    steps = max(1, int(math.ceil(target * simulation_fps - 1e-12)))
    return steps / simulation_fps, simulation_fps, steps


def _render_preset(
    loader: AnimationPluginLoader,
    plugins: dict[str, type],
    path: Path,
) -> tuple[dict[str, Any], tuple[np.ndarray, np.ndarray] | None]:
    relative_path = path.relative_to(ROOT).as_posix()
    payload: dict[str, Any] = {}
    plugin_id = path.parents[1].name
    preset_id = path.stem
    result: dict[str, Any] = {
        "plugin_id": plugin_id,
        "preset_id": preset_id,
        "name": preset_id,
        "path": relative_path,
        "flags": [],
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("preset root must be an object")
        plugin_id = payload.get("animation")
        preset_id = payload.get("preset_id")
        if plugin_id != path.parents[1].name:
            raise ValueError("preset animation does not match its plugin directory")
        if preset_id != path.stem:
            raise ValueError("preset_id does not match its filename")
        params = payload.get("params")
        if not isinstance(params, dict):
            raise ValueError("preset params must be an object")
        animation_class = plugins.get(plugin_id)
        if animation_class is None:
            raise ValueError(f"Python plugin {plugin_id!r} did not load")

        result.update({
            "plugin_id": plugin_id,
            "preset_id": preset_id,
            "name": str(payload.get("name") or preset_id),
        })
        # Hold stochastic input constant within a plugin so identical presets
        # cannot evade the duplicate check merely because their IDs differ.
        comparison_key = f"{plugin_id}/browser-composer-contact-sheet"
        constructor_seed = int(
            hashlib.sha256(comparison_key.encode()).hexdigest()[:8], 16
        )
        random.seed(constructor_seed)
        np.random.seed(constructor_seed & 0xFFFFFFFF)
        controller = PreviewLEDController(FRAME_WIDTH, FRAME_HEIGHT)
        animation = animation_class(controller, dict(params))
        PreviewRenderer._make_deterministic(animation, params, comparison_key)

        first = _canonical_pixels(animation, animation.generate_frame(0.0, 0)).copy()
        semantic_elapsed, simulation_fps, semantic_step = _semantic_target(
            loader.plugin_manifests.get(plugin_id, {})
        )
        second = first
        for step in range(1, semantic_step + 1):
            rendered = animation.generate_frame(step / simulation_fps, step)
            second = _canonical_pixels(animation, rendered).copy()

        first_summary = _frame_summary(first)
        second_summary = _frame_summary(second)
        blank = (
            first_summary["nonzero_pixels"] == 0
            and second_summary["nonzero_pixels"] == 0
        )
        if blank:
            result["flags"].append("blank")
        result.update({
            "semantic_elapsed": semantic_elapsed,
            "simulation_fps": simulation_fps,
            "semantic_step": semantic_step,
            "t0": first_summary,
            "semantic": second_summary,
            "changed": first_summary["sha256"] != second_summary["sha256"],
            "pair_sha256": _digest(first.tobytes(order="C") + second.tobytes(order="C")),
        })
        return result, (_image_oriented(first), _image_oriented(second))
    except Exception as exc:
        result["flags"].append("error")
        result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        return result, None


def _duplicate_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_plugin: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        pair_digest = record.get("pair_sha256")
        if pair_digest:
            by_plugin[record["plugin_id"]][pair_digest].append(record)

    groups: list[dict[str, Any]] = []
    for plugin_id in sorted(by_plugin):
        for pair_digest, matches in sorted(by_plugin[plugin_id].items()):
            if len(matches) < 2:
                continue
            preset_ids = sorted(record["preset_id"] for record in matches)
            groups.append({
                "plugin_id": plugin_id,
                "pair_sha256": pair_digest,
                "preset_ids": preset_ids,
            })
            for record in matches:
                record["flags"].append("duplicate_within_plugin")
    return groups


def _placeholder(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), (43, 16, 18))
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, width - 1, height - 1), fill=(184, 71, 72), width=2)
    draw.line((width - 1, 0, 0, height - 1), fill=(184, 71, 72), width=2)
    return image


def _build_sheet(
    records: list[dict[str, Any]],
    frames: list[tuple[np.ndarray, np.ndarray] | None],
    *,
    scale: int,
    columns: int,
) -> Image.Image:
    font = ImageFont.load_default()
    image_width = FRAME_WIDTH * scale
    image_height = FRAME_HEIGHT * scale
    image_gap = 4
    padding = 5
    label_height = 38
    card_width = max(156, padding * 2 + image_width * 2 + image_gap)
    card_height = padding * 2 + label_height + image_height
    rows = max(1, math.ceil(len(records) / columns))
    sheet = Image.new(
        "RGB", (card_width * columns, card_height * rows), (11, 13, 11)
    )
    draw = ImageDraw.Draw(sheet)

    for index, (record, pair) in enumerate(zip(records, frames)):
        x = (index % columns) * card_width
        y = (index // columns) * card_height
        flagged = bool(record["flags"])
        draw.rectangle(
            (x, y, x + card_width - 1, y + card_height - 1),
            fill=(24, 27, 23),
            outline=(123, 74, 50) if flagged else (51, 57, 48),
        )
        title = f"{record['plugin_id']}/{record['preset_id']}"
        draw.text((x + padding, y + 3), _safe_label(title)[:29], font=font, fill=(238, 239, 230))
        elapsed = record.get("semantic_elapsed")
        timing = "error" if elapsed is None else f"t=0 | t={elapsed:g}s"
        draw.text((x + padding, y + 15), timing, font=font, fill=(149, 158, 142))
        flags = ", ".join(record["flags"]) if record["flags"] else "ok"
        draw.text(
            (x + padding, y + 27),
            _safe_label(flags)[:29],
            font=font,
            fill=(248, 156, 114) if flagged else (166, 205, 114),
        )

        frame_y = y + padding + label_height
        if pair is None:
            left = _placeholder(image_width, image_height)
            right = left.copy()
        else:
            left = Image.fromarray(pair[0], mode="RGB").resize(
                (image_width, image_height), Image.Resampling.NEAREST
            )
            right = Image.fromarray(pair[1], mode="RGB").resize(
                (image_width, image_height), Image.Resampling.NEAREST
            )
        first_x = x + padding
        second_x = first_x + image_width + image_gap
        sheet.paste(left, (first_x, frame_y))
        sheet.paste(right, (second_x, frame_y))

    return sheet


def render_contact_sheet(
    output_path: Path,
    json_path: Path,
    *,
    scale: int = 2,
    columns: int = 8,
) -> dict[str, Any]:
    if scale < 1 or columns < 1:
        raise ValueError("scale and columns must be positive integers")
    output_path = output_path.resolve()
    json_path = json_path.resolve()
    if output_path == json_path:
        raise ValueError("PNG and JSON outputs must be different paths")

    loader = AnimationPluginLoader()
    paths = list(loader.iter_curated_preset_files())
    plugin_ids = sorted({path.parents[1].name for path in paths})
    plugins: dict[str, type] = {}
    for plugin_id in plugin_ids:
        loaded = loader.load_plugin(plugin_id)
        if loaded is not None:
            plugins[plugin_id] = loaded

    records: list[dict[str, Any]] = []
    frame_pairs: list[tuple[np.ndarray, np.ndarray] | None] = []
    for path in paths:
        record, pair = _render_preset(loader, plugins, path)
        records.append(record)
        frame_pairs.append(pair)

    duplicates = _duplicate_groups(records)
    sheet = _build_sheet(records, frame_pairs, scale=scale, columns=columns)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_image = output_path.with_name(f".{output_path.name}.tmp")
    sheet.save(temporary_image, format="PNG", optimize=True)
    temporary_image.replace(output_path)
    image_bytes = output_path.read_bytes()

    blank = [
        f"{item['plugin_id']}/{item['preset_id']}"
        for item in records if "blank" in item["flags"]
    ]
    errors = [
        {
            "preset": f"{item['plugin_id']}/{item['preset_id']}",
            **item["error"],
        }
        for item in records if "error" in item["flags"]
    ]
    duplicate_presets = sum(len(group["preset_ids"]) for group in duplicates)
    summary: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "source": "AnimationPluginLoader.iter_curated_preset_files",
        "geometry": {
            "strip_count": FRAME_WIDTH,
            "leds_per_strip": FRAME_HEIGHT,
            "orientation": "strip-major bottom-origin; contact frames top-down",
        },
        "capture": {
            "frames_per_preset": 2,
            "semantic_frame": "sequential steps to first positive authored preview capture",
        },
        "totals": {
            "plugins": len(plugin_ids),
            "presets": len(records),
            "rendered": sum("error" not in item["flags"] for item in records),
            "blank": len(blank),
            "errors": len(errors),
            "duplicate_groups": len(duplicates),
            "duplicate_presets": duplicate_presets,
        },
        "flags": {
            "blank": blank,
            "errors": errors,
            "duplicate_within_plugin": duplicates,
        },
        "image": {
            "path": str(output_path),
            "sha256": _digest(image_bytes),
            "width": sheet.width,
            "height": sheet.height,
        },
        "presets": records,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_json = json_path.with_name(f".{json_path.name}.tmp")
    temporary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary_json.replace(json_path)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="labeled PNG path")
    parser.add_argument(
        "--json-output",
        type=Path,
        help="machine-readable report path (default: PNG path with .json suffix)",
    )
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when blank, error, or duplicate presets are flagged",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    json_path = args.json_output or args.output.with_suffix(".json")
    summary = render_contact_sheet(
        args.output, json_path, scale=args.scale, columns=args.columns
    )
    totals = summary["totals"]
    print(
        f"rendered {totals['rendered']}/{totals['presets']} curated Python presets "
        f"across {totals['plugins']} plugins"
    )
    print(f"PNG: {summary['image']['path']}")
    print(f"JSON: {json_path.resolve()}")
    print(
        "flags: "
        f"{totals['blank']} blank, {totals['errors']} errors, "
        f"{totals['duplicate_groups']} duplicate groups"
    )
    flagged = totals["blank"] + totals["errors"] + totals["duplicate_groups"]
    return 1 if args.check and flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())

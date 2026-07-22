#!/usr/bin/env python3
"""Generate ignored dashboard preview artifacts from real animation plugins."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.preview_assets import (
    PreviewRenderer,
    clean_stale_assets,
    empty_catalog,
    load_catalog,
    preset_payload,
    write_catalog,
)
from tools.deployment.deploy_manifest import tracked_paths


def _deployable_paths(root: Path) -> set[str]:
    return {path.as_posix() for path in tracked_paths(root, "fast")}


def _controller_busy(status_path: Path) -> bool:
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if not status.get("is_running"):
        return False
    try:
        target = float(status.get("target_fps") or 0)
        actual = float(status.get("actual_fps") or 0)
    except (TypeError, ValueError):
        return True
    performance = status.get("performance") or {}
    deadline_misses = int(performance.get("deadline_misses") or 0)
    p95_generate = float(performance.get("p95_generate_ms") or 0)
    return (
        target <= 0
        or actual < target * 0.98
        or deadline_misses > 0
        or p95_generate > 4.0
    )


def generate_all(args: argparse.Namespace) -> int:
    renderer = PreviewRenderer(
        ROOT,
        args.output,
        args.public_prefix,
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
    )
    catalog = empty_catalog(args.strips, args.leds_per_strip)
    tracked = _deployable_paths(ROOT) if args.tracked_only else None
    failures: list[str] = []
    for animation_name in sorted(renderer.plugins):
        manifest_path = renderer.loader.get_plugin_dir(animation_name) / "manifest.json"
        relative_manifest = manifest_path.relative_to(ROOT).as_posix()
        if tracked is not None:
            relative_init = (manifest_path.parent / "__init__.py").relative_to(ROOT).as_posix()
            if relative_manifest not in tracked or relative_init not in tracked:
                continue
        manifest = renderer.loader.plugin_manifests.get(animation_name, {})
        if manifest.get("gallery", "show") != "show":
            continue
        try:
            catalog["animations"][animation_name] = renderer.render(
                animation_name, force=args.force
            )
        except Exception as exc:
            failures.append(f"{animation_name}: {exc}")
            catalog["animations"][animation_name] = {
                "status": "failed", "error": str(exc)
            }
            continue
        preset_entries: Dict[str, Any] = {}
        for path in renderer.loader.iter_curated_preset_files(animation_name):
            if tracked is not None and path.relative_to(ROOT).as_posix() not in tracked:
                continue
            try:
                preset_id, config = preset_payload(path, animation_name)
                preset_entries[preset_id] = renderer.render(
                    animation_name,
                    preset_id=preset_id,
                    config=config,
                    preset_path=path,
                    force=args.force,
                )
            except Exception as exc:
                failures.append(f"{animation_name}/{path.stem}: {exc}")
                preset_entries[path.stem] = {"status": "failed", "error": str(exc)}
        catalog["presets"][animation_name] = preset_entries
    write_catalog(args.output / "catalog.json", catalog)
    clean_stale_assets(args.output, catalog)
    ready = len(catalog["animations"]) + sum(len(value) for value in catalog["presets"].values())
    print(f"generated preview catalog with {ready} entries at {args.output}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


def generate_single(args: argparse.Namespace) -> int:
    preset_id, config = preset_payload(args.single_preset, args.animation)
    pause_guard = lambda: _controller_busy(args.status_path)
    renderer = PreviewRenderer(
        ROOT,
        args.output,
        args.public_prefix,
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
        throttle_seconds=args.throttle_seconds,
        pause_guard=pause_guard,
    )
    catalog_path = args.output / "catalog.json"
    catalog = load_catalog(catalog_path)
    catalog["layout"] = empty_catalog(args.strips, args.leds_per_strip)["layout"]
    try:
        entry = renderer.render(
            args.animation,
            preset_id=preset_id,
            config=config,
            preset_path=args.single_preset,
            force=args.force,
        )
    except Exception as exc:
        entry = {"status": "failed", "error": str(exc)}
        catalog.setdefault("presets", {}).setdefault(args.animation, {})[preset_id] = entry
        write_catalog(catalog_path, catalog)
        print(str(exc), file=sys.stderr)
        return 1
    catalog.setdefault("presets", {}).setdefault(args.animation, {})[preset_id] = entry
    write_catalog(catalog_path, catalog)
    clean_stale_assets(args.output, catalog)
    print(f"generated runtime preview for {args.animation}/{preset_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "web" / "static" / "generated" / "animation-previews",
    )
    parser.add_argument(
        "--public-prefix", default="/preview-assets/generated"
    )
    parser.add_argument("--strips", type=int, default=32)
    parser.add_argument("--leds-per-strip", type=int, default=138)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--tracked-only", action="store_true")
    parser.add_argument("--single-preset", type=Path)
    parser.add_argument("--animation")
    parser.add_argument("--status-path", type=Path, default=ROOT / "run_state" / "status.json")
    parser.add_argument("--throttle-seconds", type=float, default=0.02)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.single_preset:
        if not args.animation:
            parser.error("--animation is required with --single-preset")
        return generate_single(args)
    return generate_all(args)


if __name__ == "__main__":
    raise SystemExit(main())

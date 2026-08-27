#!/usr/bin/env python3
"""Build the deterministic source bundle consumed by the Pyodide worker."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path
from typing import Dict, Mapping, Sequence


PYODIDE_VERSION = "314.0.5"
SUPPORTED_PLUGINS: Mapping[str, str] = {
    "gradient": "GradientAnimation",
    "rainbow": "RainbowAnimation",
    "sparkle": "SparkleAnimation",
    "wave": "WaveAnimation",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
DEFAULT_OUTPUT = Path("web/static/generated/composer/ledgrid_python_runtime.zip")


def source_mapping(repo_root: Path) -> Dict[str, Path]:
    browser_sources = repo_root / "animation/browser_preview/python"
    members = {
        "animation/__init__.py": browser_sources / "shim_animation.py",
        "animation/core/__init__.py": browser_sources / "shim_core.py",
        "animation/core/plant_awareness.py": repo_root / "animation/core/plant_awareness.py",
        "animation/libraries/__init__.py": browser_sources / "shim_libraries.py",
        "animation/libraries/mask_effects.py": repo_root / "animation/libraries/mask_effects.py",
        "animation/libraries/spatial.py": repo_root / "animation/libraries/spatial.py",
        "animation/plugins/__init__.py": repo_root / "animation/plugins/__init__.py",
        "config/plant_globe_map_32x138.json": repo_root / "config/plant_globe_map_32x138.json",
        "config/plant_pixel_map_32x138.json": repo_root / "config/plant_pixel_map_32x138.json",
        "ledgrid_browser_runtime.py": browser_sources / "runtime.py",
    }
    for plugin_id in SUPPORTED_PLUGINS:
        members[f"animation/plugins/{plugin_id}/__init__.py"] = (
            repo_root / "animation/plugins" / plugin_id / "__init__.py"
        )
    return members


def build_members(repo_root: Path) -> Dict[str, bytes]:
    sources = source_mapping(repo_root)
    members: Dict[str, bytes] = {}
    source_hashes: Dict[str, str] = {}
    for archive_name, source_path in sorted(sources.items()):
        if source_path.is_symlink() or not source_path.is_file():
            raise FileNotFoundError(f"browser Python source is missing: {source_path}")
        payload = source_path.read_bytes()
        members[archive_name] = payload
        source_hashes[archive_name] = hashlib.sha256(payload).hexdigest()
    manifest = {
        "engine": "python-pyodide-wasm",
        "formatVersion": 1,
        "orientation": "strip-major; index = strip * ledsPerStrip + led",
        "pyodideVersion": PYODIDE_VERSION,
        "supportsCalibratedPlantMasks": True,
        "supportsPlantModifiers": True,
        "plugins": [
            {"pluginId": plugin_id, "className": class_name}
            for plugin_id, class_name in sorted(SUPPORTED_PLUGINS.items())
        ],
        "sourceSha256": source_hashes,
    }
    members["ledgrid_browser_manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return members


def deterministic_zip(members: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_STORED,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(members):
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError(f"unsafe browser bundle member: {name!r}")
            info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.flag_bits = 0
            archive.writestr(info, members[name], compress_type=zipfile.ZIP_STORED)
    return output.getvalue()


def build_archive(repo_root: Path) -> bytes:
    return deterministic_zip(build_members(repo_root.resolve()))


def build_bundle(repo_root: Path, output: Path) -> bytes:
    payload = build_archive(repo_root)
    resolved_output = output if output.is_absolute() else repo_root / output
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_bytes(payload)
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: inferred from this script)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output path relative to the repository (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_bundle(args.repo_root.resolve(), args.output)
    digest = hashlib.sha256(payload).hexdigest()
    print(f"built {len(payload)} bytes sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the deterministic all-catalog source bundle consumed by Pyodide."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import stat
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Sequence


PYODIDE_VERSION = "314.0.5"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
DEFAULT_OUTPUT = Path("web/static/generated/composer/ledgrid_python_runtime.zip")
ENGINE = "python-pyodide-wasm"
MAX_RUNTIME_INSTANCES = 8


@dataclass(frozen=True)
class BrowserPlugin:
    plugin_id: str
    class_name: str
    role: str
    timing_adapter: str
    required_packages: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "pluginId": self.plugin_id,
            "className": self.class_name,
            "role": self.role,
            "frameFormat": (
                "premultiplied-rgba" if self.role == "overlay" else "rgb"
            ),
            "timingAdapter": self.timing_adapter,
            "requiredPackages": list(self.required_packages),
        }


def discover_python_plugins(repo_root: Path) -> tuple[BrowserPlugin, ...]:
    """Enumerate the authoritative shipped Python catalog, with no allowlist."""
    plugins = []
    for manifest_path in sorted((repo_root / "animation/plugins").glob("*/manifest.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        provider = payload.get("provider", "python")
        if provider == "receiver_native":
            continue
        if provider != "python":
            raise ValueError(
                f"unsupported component provider {provider!r}: {manifest_path}"
            )
        plugin_id = manifest_path.parent.name
        declared_id = payload.get("plugin_id", payload.get("id", plugin_id))
        if declared_id != plugin_id:
            raise ValueError(
                f"manifest ID {declared_id!r} does not match directory {plugin_id!r}"
            )
        class_name = payload.get("class")
        if not isinstance(class_name, str) or not class_name:
            raise ValueError(f"Python manifest is missing class: {manifest_path}")
        source_path = manifest_path.parent / "__init__.py"
        if not source_path.is_file():
            raise FileNotFoundError(f"Python plugin source is missing: {source_path}")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        if class_name not in {node.name for node in tree.body if isinstance(node, ast.ClassDef)}:
            raise ValueError(
                f"manifest class {class_name!r} is not defined by {source_path}"
            )
        role = payload.get("role", "full_scene" if plugin_id == "clock" else "background")
        if role not in {"background", "overlay", "full_scene"}:
            raise ValueError(f"unsupported component role {role!r}: {manifest_path}")
        vibe = payload.get("vibe") if isinstance(payload.get("vibe"), dict) else {}
        timing_adapter = vibe.get("timing_adapter", "legacy_speed_param")
        if timing_adapter not in {"legacy_speed_param", "scaled_context", "wall_clock"}:
            raise ValueError(
                f"unsupported timing adapter {timing_adapter!r}: {manifest_path}"
            )
        required_packages = ("pillow",) if plugin_id == "gif_animation" else ()
        plugins.append(BrowserPlugin(
            plugin_id=plugin_id,
            class_name=class_name,
            role=role,
            timing_adapter=timing_adapter,
            required_packages=required_packages,
        ))
    if not plugins:
        raise ValueError("no shipped Python animations were discovered")
    return tuple(plugins)


def _audit_imports(sources: Mapping[str, Path]) -> None:
    """Fail the build when unchanged catalog code gains an unshipped dependency."""
    allowed = set(sys.stdlib_module_names) | {"animation", "drivers", "numpy", "PIL"}
    failures = []
    for archive_name, source_path in sorted(sources.items()):
        if source_path.suffix != ".py":
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.partition(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module.partition(".")[0]]
            else:
                continue
            for module in modules:
                if module not in allowed:
                    failures.append(f"{archive_name}: {module}")
    if failures:
        raise ValueError(
            "browser Python catalog has unsupported dependencies:\n"
            + "\n".join(sorted(set(failures)))
        )


def source_mapping(repo_root: Path) -> Dict[str, Path]:
    """Return every unchanged source and runtime asset required by the catalog."""
    browser_sources = repo_root / "animation/browser_preview/python"
    plugins = discover_python_plugins(repo_root)
    members: Dict[str, Path] = {
        "animation/__init__.py": browser_sources / "shim_animation.py",
        "animation/core/__init__.py": browser_sources / "shim_core.py",
        "animation/core/installation_profile.py": (
            repo_root / "animation/core/installation_profile.py"
        ),
        "animation/core/plant_awareness.py": repo_root / "animation/core/plant_awareness.py",
        "animation/core/receiver_optics.py": repo_root / "animation/core/receiver_optics.py",
        "animation/core/presentation_contracts.py": (
            browser_sources / "shim_presentation_contracts.py"
        ),
        "animation/plugins/__init__.py": repo_root / "animation/plugins/__init__.py",
        "drivers/__init__.py": repo_root / "drivers/__init__.py",
        "drivers/led_layout.py": repo_root / "drivers/led_layout.py",
        "config/webcam_pixel_map.json": repo_root / "config/webcam_pixel_map.json",
        "ledgrid_browser_runtime.py": browser_sources / "runtime.py",
    }
    for source_path in sorted((repo_root / "animation/libraries").glob("*.py")):
        members[f"animation/libraries/{source_path.name}"] = source_path
    for plugin in plugins:
        members[f"animation/plugins/{plugin.plugin_id}/__init__.py"] = (
            repo_root / "animation/plugins" / plugin.plugin_id / "__init__.py"
        )
    # Keep the package's 36 small shipped assets intact: preset filenames and
    # normalization metadata remain unchanged, while deflate contains transfer size.
    asset_root = repo_root / "animation/plugins/gif_animation/assets"
    for asset_path in sorted(path for path in asset_root.iterdir() if path.is_file()):
        members[f"animation/plugins/gif_animation/assets/{asset_path.name}"] = asset_path
    _audit_imports(members)
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
    plugins = discover_python_plugins(repo_root)
    manifest = {
        "engine": ENGINE,
        "formatVersion": 2,
        "orientation": "strip-major; index = strip * ledsPerStrip + led",
        "pyodideVersion": PYODIDE_VERSION,
        "supportsCalibratedPlantMasks": True,
        "requiresManagedInstallationProfile": True,
        "supportsPlantModifiers": True,
        "supportsMultipleInstances": True,
        "maxInstances": MAX_RUNTIME_INSTANCES,
        "supportsFixedWallTime": True,
        "plugins": [plugin.to_dict() for plugin in plugins],
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
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(members):
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError(f"unsafe browser bundle member: {name!r}")
            info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.flag_bits = 0
            archive.writestr(
                info,
                members[name],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
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

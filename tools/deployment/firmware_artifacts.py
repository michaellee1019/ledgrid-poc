#!/usr/bin/env python3
"""Identify the exact PlatformIO firmware installation selected for flashing.

The application binary alone is not a firmware installation: PlatformIO also
flashes a bootloader and partition table at layout-defined offsets.  This
module resolves the generated flash map to the build artifacts that PlatformIO
uploads and gives the complete selection one deterministic identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Optional, Sequence


INSTALLATION_IDENTITY_SCHEMA_VERSION = 2
PLATFORMIO_TOOLCHAIN_IDENTITY = "platformio==6.1.19"
GENERATED_LAYOUT_INPUTS = ("flash_args", "flasher_args.json")
SOURCE_LAYOUT_INPUTS = ("platformio.ini", "sdkconfig.defaults")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, *, description: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{description} is missing or unsafe: {path}")
    return path


def _safe_generated_path(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise RuntimeError("PlatformIO flash map contains a non-string artifact path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"PlatformIO flash map contains an unsafe path: {value!r}")
    return path


def _offset_key(value: str) -> int:
    if re.fullmatch(r"0x[0-9a-fA-F]+", value) is None:
        raise RuntimeError(f"PlatformIO flash map contains an invalid offset: {value!r}")
    return int(value, 16)


def _component_aliases(payload: Mapping[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for component, build_name in (
        ("app", "firmware.bin"),
        ("bootloader", "bootloader.bin"),
        ("partition-table", "partitions.bin"),
    ):
        item = payload.get(component)
        if isinstance(item, dict) and isinstance(item.get("file"), str):
            aliases[item["file"]] = build_name
    return aliases


def _resolve_flash_artifact(
    build_directory: Path,
    declared_path: PurePosixPath,
    aliases: Mapping[str, str],
) -> Path:
    relative = aliases.get(declared_path.as_posix(), declared_path.as_posix())
    candidate = build_directory / relative
    try:
        candidate.relative_to(build_directory)
    except ValueError as exc:  # Defensive; PurePosixPath validation should catch it.
        raise RuntimeError(f"flash artifact escapes build directory: {declared_path}") from exc
    return _regular_file(candidate, description="PlatformIO flash artifact")


def inspect_firmware_installation(
    firmware_directory: os.PathLike[str] | str,
    environment: str,
) -> dict[str, Any]:
    """Return a canonical receipt for every byte and layout input flashed."""

    if not environment or "/" in environment or environment in {".", ".."}:
        raise ValueError("firmware environment must be a safe non-empty name")
    firmware = Path(firmware_directory).resolve()
    build = firmware / ".pio" / "build" / environment
    flasher_args = _regular_file(
        build / "flasher_args.json", description="PlatformIO generated flash map"
    )
    try:
        payload = json.loads(flasher_args.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read PlatformIO flash map {flasher_args}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("flash_files"), dict):
        raise RuntimeError("PlatformIO flash map has no flash_files object")
    flash_files = payload["flash_files"]
    if not flash_files:
        raise RuntimeError("PlatformIO flash map selects no artifacts")

    aliases = _component_aliases(payload)
    artifacts = []
    for offset, raw_path in sorted(flash_files.items(), key=lambda item: _offset_key(item[0])):
        declared = _safe_generated_path(raw_path)
        artifact = _resolve_flash_artifact(build, declared, aliases)
        artifacts.append(
            {
                "offset": offset.lower(),
                "declared_path": declared.as_posix(),
                "build_path": artifact.relative_to(build).as_posix(),
                "size": artifact.stat().st_size,
                "sha256": _sha256_file(artifact),
            }
        )

    required_build_paths = {"firmware.bin", "bootloader.bin", "partitions.bin"}
    selected_build_paths = {item["build_path"] for item in artifacts}
    missing = sorted(required_build_paths - selected_build_paths)
    if missing:
        raise RuntimeError(
            "PlatformIO flash map does not select required artifacts: " + ", ".join(missing)
        )

    layout_inputs = []
    for relative in GENERATED_LAYOUT_INPUTS:
        path = _regular_file(build / relative, description="generated flash layout input")
        layout_inputs.append(
            {
                "path": f".pio/build/{environment}/{relative}",
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    for relative in (*SOURCE_LAYOUT_INPUTS, f"sdkconfig.{environment}"):
        path = _regular_file(firmware / relative, description="firmware layout input")
        layout_inputs.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )

    selection: dict[str, Any] = {
        "schema_version": INSTALLATION_IDENTITY_SCHEMA_VERSION,
        "environment": environment,
        "toolchain": PLATFORMIO_TOOLCHAIN_IDENTITY,
        "flash_artifacts": artifacts,
        "layout_inputs": layout_inputs,
    }
    canonical = json.dumps(selection, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    selection["installation_digest"] = hashlib.sha256(canonical).hexdigest()
    selection["firmware_sha256"] = next(
        item["sha256"] for item in artifacts if item["build_path"] == "firmware.bin"
    )
    return selection


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--expect-digest")
    parser.add_argument(
        "--field", choices=("installation_digest", "firmware_sha256")
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    receipt = inspect_firmware_installation(args.firmware_dir, args.environment)
    if args.expect_digest is not None:
        if re.fullmatch(r"[0-9a-f]{64}", args.expect_digest) is None:
            raise RuntimeError("expected firmware installation digest is malformed")
        if receipt["installation_digest"] != args.expect_digest:
            raise RuntimeError(
                "firmware installation artifacts changed after build selection"
            )
    if args.field:
        print(receipt[args.field])
    else:
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

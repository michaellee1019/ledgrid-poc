#!/usr/bin/env python3
"""Build, inspect, verify, and install signed firmware-animation packages."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from firmware_animations import (
    FirmwareAnimationError,
    FirmwareAnimationLibrary,
    build_frame_package,
    build_native_package,
    inspect_package,
)
from firmware_animations.manifest import canonical_json
from firmware_animations.native import native_build_commands, render_host_preview, run_native_build, shell_display, undefined_imports
from firmware_animations.signing import generate_keypair, public_key_id


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _trusted(values: list[str]) -> dict[str, Path]:
    keys: dict[str, Path] = {}
    for value in values:
        if "=" in value:
            key_id, raw_path = value.split("=", 1)
            path = Path(raw_path)
        else:
            path = Path(value)
            key_id = public_key_id(path)
        if key_id in keys:
            raise ValueError(f"duplicate trusted key id: {key_id}")
        keys[key_id] = path
    return keys


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".part", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _package_summary(package: Any) -> dict[str, Any]:
    return {
        "digest": package.digest,
        "id": package.manifest["id"],
        "kind": package.manifest["kind"],
        "name": package.manifest["name"],
        "version": package.manifest["version"],
        "payload_bytes": [len(package.payload_for_device(index)) for index in range(4)],
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    keygen = commands.add_parser("keygen", help="generate an ECDSA P-256 authoring keypair")
    keygen.add_argument("--private", type=Path, required=True)
    keygen.add_argument("--public", type=Path, required=True)

    frames = commands.add_parser("build-frames", help="convert GIF/WebP and build a signed frame package")
    frames.add_argument("--source", type=Path, required=True)
    frames.add_argument("--metadata", type=Path, required=True)
    frames.add_argument("--private-key", type=Path, required=True)
    frames.add_argument("--output", type=Path, required=True)
    frames.add_argument("--keyframe-interval", type=int, default=30)

    native = commands.add_parser("build-native", help="package a trusted compiled module and host-generated preview")
    native.add_argument("--module", type=Path, required=True)
    preview_source = native.add_mutually_exclusive_group(required=True)
    preview_source.add_argument("--host-library", type=Path, help="trusted host build to execute for preview generation")
    preview_source.add_argument("--preview", type=Path, help="pre-generated trusted WebP preview")
    native.add_argument("--metadata", type=Path, required=True)
    native.add_argument("--private-key", type=Path, required=True)
    native.add_argument("--output", type=Path, required=True)
    native.add_argument("--nm", default="xtensa-esp32s3-elf-nm")

    contract = commands.add_parser("native-build", help="show or execute pinned native/host compiler contracts")
    contract.add_argument("--source", action="append", type=Path, required=True)
    contract.add_argument("--sdk-include", type=Path, default=Path("firmware_animations/sdk/include"))
    contract.add_argument("--module-output", type=Path, required=True)
    contract.add_argument("--host-output", type=Path, required=True)
    contract.add_argument("--esp-cxx", default="xtensa-esp32s3-elf-g++")
    contract.add_argument("--host-cxx", default="c++")
    contract.add_argument("--execute", action="store_true")

    verify = commands.add_parser("verify", help="strictly verify a package")
    verify.add_argument("package", type=Path)
    verify.add_argument("--trusted-key", action="append", required=True, metavar="[ID=]PUBLIC.pem")

    install = commands.add_parser("install", help="verify and atomically install a package")
    install.add_argument("package", type=Path)
    install.add_argument("--library", type=Path, required=True)
    install.add_argument("--trusted-key", action="append", required=True, metavar="[ID=]PUBLIC.pem")

    listing = commands.add_parser("list", help="list persistent library packages")
    listing.add_argument("--library", type=Path, required=True)
    listing.add_argument("--trusted-key", action="append", required=True, metavar="[ID=]PUBLIC.pem")

    delete = commands.add_parser("delete", help="delete an inactive persistent library package")
    delete.add_argument("package_id")
    delete.add_argument("--library", type=Path, required=True)
    delete.add_argument("--trusted-key", action="append", required=True, metavar="[ID=]PUBLIC.pem")
    delete.add_argument("--active-id")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "keygen":
            if args.private.exists() or args.public.exists():
                raise ValueError("refusing to overwrite an existing key")
            args.private.parent.mkdir(parents=True, exist_ok=True)
            args.public.parent.mkdir(parents=True, exist_ok=True)
            key_id = generate_keypair(args.private, args.public)
            print(json.dumps({"key_id": key_id, "private": str(args.private), "public": str(args.public)}, sort_keys=True))
        elif args.command == "build-frames":
            payload = build_frame_package(args.source, _json(args.metadata), args.private_key, keyframe_interval=args.keyframe_interval)
            _write_atomic(args.output, payload)
            print(json.dumps({"bytes": len(payload), "output": str(args.output)}, sort_keys=True))
        elif args.command == "build-native":
            metadata = _json(args.metadata)
            imports = undefined_imports(args.module, nm=args.nm)
            preview = render_host_preview(args.host_library, metadata) if args.host_library else args.preview
            payload = build_native_package(args.module, preview, metadata, args.private_key, imports=imports)
            _write_atomic(args.output, payload)
            print(json.dumps({"bytes": len(payload), "imports": imports, "output": str(args.output)}, sort_keys=True))
        elif args.command == "native-build":
            commands = native_build_commands(
                args.source, sdk_include=args.sdk_include, module_output=args.module_output,
                host_output=args.host_output, esp_cxx=args.esp_cxx, host_cxx=args.host_cxx,
            )
            print(json.dumps({"esp32": shell_display(commands.esp32), "host_preview": shell_display(commands.host_preview)}, sort_keys=True))
            if args.execute:
                run_native_build(commands)
        elif args.command == "verify":
            package = inspect_package(args.package, _trusted(args.trusted_key))
            print(canonical_json(_package_summary(package)).decode("utf-8"))
        elif args.command in {"install", "list", "delete"}:
            active = getattr(args, "active_id", None)
            library = FirmwareAnimationLibrary(args.library, _trusted(args.trusted_key), active_id_provider=lambda: active)
            if args.command == "install":
                installed = library.install(args.package)
                print(canonical_json({"digest": installed.digest, "id": installed.package_id}).decode("utf-8"))
            elif args.command == "list":
                print(canonical_json([{"digest": item.digest, "id": item.package_id, "kind": item.kind, "version": item.version} for item in library.list()]).decode("utf-8"))
            else:
                library.delete(args.package_id)
                print(canonical_json({"deleted": args.package_id}).decode("utf-8"))
        return 0
    except (FirmwareAnimationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

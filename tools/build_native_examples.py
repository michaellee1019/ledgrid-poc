#!/usr/bin/env python3
"""Build every checked-in native example into production-signed .lga files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from firmware_animations.package import build_native_package, inspect_package
from firmware_animations.errors import FirmwareAnimationError
from firmware_animations.native import render_host_preview
from firmware_animations.signing import public_key_id
from tools.benchmarks.native_animations import compile_host, compile_target, load_catalog


def source_fingerprint(private_key: Path) -> str:
    paths = [
        private_key,
        REPO_ROOT / "firmware_animations/examples/native_catalog.json",
        REPO_ROOT / "firmware_animations/native.py",
        REPO_ROOT / "firmware_animations/package.py",
    ]
    paths.extend((REPO_ROOT / "firmware_animations/sdk/include").rglob("*"))
    for example in load_catalog():
        paths.append(example["source"])
        paths.append(
            REPO_ROOT / "firmware_animations/examples" /
            example["id"].removesuffix("-native").replace("-", "_") /
            "metadata.json"
        )
    digest = hashlib.sha256()
    for path in sorted({item for item in paths if item.is_file()}):
        relative = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else path.name
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0" + payload)
    return digest.hexdigest()


def build_examples(
    private_key: Path, public_key: Path, output_dir: Path, *, host_cxx: str = "c++"
) -> list[dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trusted = {public_key_id(public_key): public_key}
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="ledgrid-native-release-") as temporary:
        build_root = Path(temporary)
        for example in load_catalog():
            package_id = example["id"]
            host_library = build_root / f"{package_id}.host.so"
            module = build_root / f"{package_id}.esp32.so"
            compile_host(example["source"], host_library, host_cxx=host_cxx)
            target_bytes = compile_target(example["source"], module)
            preview = render_host_preview(host_library, example["metadata"])
            package = build_native_package(
                module, preview, example["metadata"], private_key, imports=[]
            )
            verified = inspect_package(package, trusted)
            destination = output_dir / f"{package_id}.lga"
            partial = destination.with_suffix(".lga.part")
            partial.write_bytes(package)
            os.replace(partial, destination)
            results.append({
                "id": package_id,
                "module_bytes": target_bytes,
                "package_bytes": len(package),
                "digest": verified.digest,
                "path": str(destination),
            })
    return results


def build_examples_if_needed(
    private_key: Path, public_key: Path, output_dir: Path, *, host_cxx: str = "c++"
) -> tuple[bool, list[dict[str, object]]]:
    fingerprint = source_fingerprint(private_key)
    marker = output_dir / ".source_fingerprint"
    expected = [item["id"] for item in load_catalog()]
    trusted = {public_key_id(public_key): public_key}
    if marker.is_file() and marker.read_text(encoding="ascii").strip() == fingerprint:
        cached: list[dict[str, object]] = []
        try:
            for package_id in expected:
                path = output_dir / f"{package_id}.lga"
                package = inspect_package(path, trusted)
                cached.append({
                    "id": package_id, "package_bytes": path.stat().st_size,
                    "digest": package.digest, "path": str(path), "cached": True,
                })
            return False, cached
        except (FirmwareAnimationError, OSError, ValueError):
            pass
    results = build_examples(
        private_key, public_key, output_dir, host_cxx=host_cxx
    )
    temporary = marker.with_suffix(".part")
    temporary.write_text(fingerprint + "\n", encoding="ascii")
    os.replace(temporary, marker)
    return True, results


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--private-key", type=Path, required=True)
    value.add_argument("--public-key", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--host-cxx", default="c++")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        built, results = build_examples_if_needed(
            args.private_key, args.public_key, args.output_dir,
            host_cxx=args.host_cxx,
        )
        print(json.dumps({"built": built, "packages": results}, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

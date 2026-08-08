#!/usr/bin/env python3
"""Compile, exercise, and time every checked-in native animation example."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ecdsa import NIST256p, SigningKey

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from firmware_animations.manifest import validate_parameter_schema, validate_parameters
from firmware_animations.native import (
    native_build_commands,
    render_host_frames,
    render_host_preview,
    shell_display,
)
from firmware_animations.package import build_native_package, inspect_package
from firmware_animations.signing import public_key_id


CATALOG = REPO_ROOT / "firmware_animations/examples/native_catalog.json"
SDK_INCLUDE = REPO_ROOT / "firmware_animations/sdk/include"


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot compute a percentile without samples")
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def stress_parameters(schema: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, spec in schema.items():
        if spec["type"] in {"int", "float"}:
            values[name] = spec["max"]
        elif spec["type"] == "bool":
            values[name] = True
        elif spec["type"] == "enum":
            values[name] = spec["options"][-1]
        else:
            values[name] = spec["default"]
    return validate_parameters(schema, values, require_all=True)


def load_catalog() -> list[dict[str, Any]]:
    raw = json.loads(CATALOG.read_text())
    if not isinstance(raw, list) or not raw:
        raise ValueError("native example catalog must be a non-empty list")
    examples: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in raw:
        source = REPO_ROOT / item["source"]
        metadata_path = REPO_ROOT / item["metadata"]
        metadata = json.loads(metadata_path.read_text())
        metadata["parameter_schema"] = validate_parameter_schema(
            metadata["parameter_schema"]
        )
        package_id = metadata["id"]
        if package_id in ids:
            raise ValueError(f"duplicate native example id: {package_id}")
        if not source.is_file() or not metadata_path.is_file():
            raise ValueError(f"native example has a missing source or metadata file: {package_id}")
        ids.add(package_id)
        examples.append({"id": package_id, "source": source, "metadata": metadata})
    return examples


def compile_host(source: Path, output: Path, *, host_cxx: str) -> None:
    commands = native_build_commands(
        [source], sdk_include=SDK_INCLUDE,
        module_output=output.with_suffix(".esp32.so"), host_output=output,
        host_cxx=host_cxx,
    )
    env = {**os.environ, "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0"}
    completed = subprocess.run(
        commands.host_preview, cwd=REPO_ROOT, env=env,
        check=False, capture_output=True, text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"host build failed for {source.name}: {detail}")


def run_platformio_tool(command: tuple[str, ...]) -> str:
    completed = subprocess.run(
        [
            "pio", "pkg", "exec", "-p", "toolchain-xtensa-esp-elf",
            "-c", shell_display(command),
        ],
        cwd=REPO_ROOT, check=False, capture_output=True, text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"PlatformIO target tool failed: {detail}")
    return completed.stdout


def compile_target(source: Path, output: Path) -> int:
    commands = native_build_commands(
        [source], sdk_include=SDK_INCLUDE,
        module_output=output, host_output=output.with_suffix(".host.so"),
    )
    run_platformio_tool(commands.esp32)
    nm = "xtensa-esp32s3-elf-nm"
    undefined_output = run_platformio_tool((nm, "-u", str(output)))
    undefined = {
        parts[-1]
        for line in undefined_output.splitlines()
        if len(parts := line.split()) >= 2 and parts[-2] == "U"
    }
    if undefined:
        raise RuntimeError(
            f"target build for {source.name} has undefined imports: "
            f"{sorted(undefined)}"
        )
    exports_output = run_platformio_tool((nm, "-g", str(output)))
    exports = {
        parts[-1]
        for line in exports_output.splitlines()
        if (parts := line.split())
    }
    if "ledgrid_animation_v1" not in exports:
        raise RuntimeError(
            f"target build for {source.name} is missing ledgrid_animation_v1"
        )
    return output.stat().st_size


def package_round_trip(
    target: Path, host_library: Path, metadata: Mapping[str, Any]
) -> int:
    # Deterministic test-only key. Production signing material is never stored
    # in the repository or used by this acceptance tool.
    signing_key = SigningKey.from_secret_exponent(
        1, curve=NIST256p, hashfunc=hashlib.sha256
    )
    private = signing_key.to_pem()
    public = signing_key.verifying_key.to_pem()
    preview = render_host_preview(
        host_library, metadata, frame_count=4, duration_ms=80
    )
    package = build_native_package(
        target, preview, metadata, private, imports=[]
    )
    verified = inspect_package(
        package, {public_key_id(public): public}
    )
    target_bytes = target.read_bytes()
    if any(
        verified.payload_for_device(device) != target_bytes
        for device in range(4)
    ):
        raise RuntimeError(
            f"signed package round trip changed payload {metadata['id']}"
        )
    return len(package)


def benchmark(
    *, frames: int, warmup_frames: int, duration_ms: int, host_cxx: str
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ledgrid-native-benchmark-") as temporary:
        output_root = Path(temporary)
        for example in load_catalog():
            library = output_root / f"{example['id']}.so"
            compile_host(example["source"], library, host_cxx=host_cxx)
            target_bytes = compile_target(
                example["source"],
                output_root / f"{example['id']}.esp32.so",
            )
            package_bytes = package_round_trip(
                output_root / f"{example['id']}.esp32.so",
                library, example["metadata"],
            )
            schema = example["metadata"]["parameter_schema"]
            for profile, parameters in (
                ("default", validate_parameters(schema, {}, require_all=True)),
                ("stress", stress_parameters(schema)),
            ):
                run = render_host_frames(
                    library, example["metadata"],
                    frame_count=frames + warmup_frames,
                    duration_ms=duration_ms, parameters=parameters,
                )
                samples = list(run.render_ms[warmup_frames * 4:])
                measured_frames = run.frames[warmup_frames:]
                results.append({
                    "animation": example["id"],
                    "profile": profile,
                    "geometry": "8x138 per callback",
                    "target_bytes": target_bytes,
                    "package_bytes": package_bytes,
                    "samples": len(samples),
                    "mean_ms": statistics.fmean(samples),
                    "p95_ms": percentile(samples, 0.95),
                    "p99_ms": percentile(samples, 0.99),
                    "max_ms": max(samples),
                    "fingerprint": hashlib.sha256(b"".join(measured_frames)).hexdigest()[:16],
                })
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=80)
    parser.add_argument("--warmup-frames", type=int, default=10)
    parser.add_argument("--duration-ms", type=int, default=16)
    parser.add_argument("--host-cxx", default="c++")
    parser.add_argument("--max-p95-ms", type=float, default=4.0)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.frames < 1 or args.warmup_frames < 0 or args.frames + args.warmup_frames > 120:
        parser.error("frames plus warmup must be in [1, 120]")
    results = benchmark(
        frames=args.frames, warmup_frames=args.warmup_frames,
        duration_ms=args.duration_ms, host_cxx=args.host_cxx,
    )
    failed = [
        item for item in results
        if item["p95_ms"] > args.max_p95_ms
    ]
    if args.json:
        print(json.dumps({
            "target_modules_cross_compiled": True,
            "target_undefined_imports": 0,
            "timing_environment": "trusted host preview (not ESP32 timing)",
            "max_p95_ms": args.max_p95_ms,
            "results": results,
        }, sort_keys=True))
    else:
        for item in results:
            print(
                f"{item['animation']} {item['profile']}: "
                f"mean={item['mean_ms']:.4f}ms p95={item['p95_ms']:.4f}ms "
                f"p99={item['p99_ms']:.4f}ms max={item['max_ms']:.4f}ms"
            )
    return 1 if args.check and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

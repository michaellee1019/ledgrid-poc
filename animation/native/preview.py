"""Timeout-bounded host rendering and deterministic preview encoding."""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .constants import GLOBAL_STRIPS, LEDS_PER_STRIP, RECEIVER_VIEWS
from .errors import NativePreviewError
from .schema import validate_parameter_schema, validate_parameters


def percentile(values: tuple[float, ...] | list[float], quantile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


@dataclass(frozen=True)
class PreviewTiming:
    samples: int
    mean_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float

    @classmethod
    def from_samples(cls, samples: tuple[float, ...]) -> "PreviewTiming":
        if not samples:
            raise NativePreviewError("host preview returned no timing samples")
        return cls(
            samples=len(samples),
            mean_ms=statistics.fmean(samples),
            p95_ms=percentile(samples, 0.95),
            p99_ms=percentile(samples, 0.99),
            max_ms=max(samples),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "mean_ms": self.mean_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
        }


@dataclass(frozen=True)
class PreviewRun:
    frames: tuple[bytes, ...]
    render_ms: tuple[float, ...]
    timing: PreviewTiming
    changed_frames: int
    missed_deadlines: int


def render_host_frames(
    host_library: str | Path,
    component_manifest: Mapping[str, Any],
    *,
    parameters: Mapping[str, Any] | None = None,
    frame_count: int | None = None,
    duration_ms: int | None = None,
    timeout_seconds: float = 15.0,
    repo_root: str | Path | None = None,
    vibe_luminance_q8_8: int = 256,
    vibe_palette: Sequence[Sequence[int]] | None = None,
) -> PreviewRun:
    """Render a trusted host peer in a disposable subprocess.

    The worker may still execute arbitrary trusted repository machine code, but
    a crash, hang, or output overwrite cannot take down the caller process.
    """

    manifest = dict(component_manifest)
    schema, _defaults = validate_parameter_schema(manifest["parameter_schema"])
    preview = manifest["preview"]
    captures = [float(value) for value in preview["capture_seconds"]]
    frames_requested = len(captures) if frame_count is None else frame_count
    duration = (
        max(1, round(1000 / int(preview["simulation_fps"])))
        if duration_ms is None
        else duration_ms
    )
    if (
        isinstance(frames_requested, bool)
        or not isinstance(frames_requested, int)
        or not 1 <= frames_requested <= 240
        or isinstance(duration, bool)
        or not isinstance(duration, int)
        or not 1 <= duration <= 1000
    ):
        raise NativePreviewError("host preview frame count or duration is outside safe bounds")
    resolved_parameters = validate_parameters(schema, parameters)
    palette = [list(color) for color in (vibe_palette or ())]
    if vibe_palette is None:
        palette = []
    elif (
        len(palette) != 8
        or any(
            len(color) != 3
            or any(isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255 for channel in color)
            for color in palette
        )
    ):
        raise NativePreviewError("host preview vibe palette must contain eight RGB colors")
    if (
        isinstance(vibe_luminance_q8_8, bool)
        or not isinstance(vibe_luminance_q8_8, int)
        or not 0 <= vibe_luminance_q8_8 <= 256
    ):
        raise NativePreviewError("host preview vibe luminance must fit unsigned Q8.8")
    scene_times_us = (
        [round(value * 1_000_000) for value in captures]
        if frame_count is None
        else [index * duration * 1000 for index in range(frames_requested)]
    )
    if any(not 0 <= value <= 2**64 - 1 for value in scene_times_us):
        raise NativePreviewError("host preview scene time does not fit ABI uint64 microseconds")
    cadence = manifest.get("cadence")
    if not isinstance(cadence, dict) or cadence.get("mode") != "fixed_fps":
        raise NativePreviewError("host preview requires a fixed-FPS component cadence")
    cadence_fps = cadence.get("preferred_fps")
    if (
        isinstance(cadence_fps, bool)
        or not isinstance(cadence_fps, (int, float))
        or not math.isfinite(float(cadence_fps))
        or not 1 <= float(cadence_fps) <= 200
    ):
        raise NativePreviewError("host preview component cadence is invalid")
    library = Path(host_library).resolve()
    if library.is_symlink() or not library.is_file():
        raise NativePreviewError("host preview library must be a regular non-symlink file")
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2]
    request = {
        "host_library": os.fspath(library),
        "manifest": manifest,
        "parameters": resolved_parameters,
        "frame_count": frames_requested,
        "scene_times_us": scene_times_us,
        "cadence_period_us": math.ceil(1_000_000 / float(cadence_fps)),
        "render_budget_ms": 1000 / float(cadence_fps),
        "vibe": {
            "luminance_q8_8": vibe_luminance_q8_8,
            "palette": palette,
        },
    }
    with tempfile.TemporaryDirectory(prefix="ledgrid-native-preview-") as temporary:
        scratch = Path(temporary)
        request_path = scratch / "request.json"
        result_path = scratch / "result.json"
        frames_path = scratch / "frames.rgb"
        request_path.write_text(
            json.dumps(request, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "LC_ALL": "C",
            "PYTHONPATH": os.fspath(root),
        }
        command = (
            sys.executable,
            "-m",
            "animation.native.preview_worker",
            "--request",
            os.fspath(request_path),
            "--result",
            os.fspath(result_path),
            "--frames",
            os.fspath(frames_path),
        )
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise NativePreviewError(
                f"host preview exceeded its {timeout_seconds:.1f}s timeout"
            ) from exc
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
            raise NativePreviewError(f"isolated host preview failed: {detail}")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            raw_frames = frames_path.read_bytes()
            timings = tuple(float(value) for value in result["render_ms"])
            frame_size = int(result["frame_size"])
            if set(result) != {
                "changed_frames", "frame_count", "frame_size", "missed_deadlines", "render_ms"
            }:
                raise ValueError("worker result has unexpected fields")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise NativePreviewError(f"isolated host preview returned malformed evidence: {exc}") from exc
        expected_frame_size = GLOBAL_STRIPS * LEDS_PER_STRIP * 3
        if frame_size != expected_frame_size or result["frame_count"] != frames_requested:
            raise NativePreviewError("isolated host preview returned incompatible geometry/count")
        if len(raw_frames) != frames_requested * frame_size or len(timings) != frames_requested * len(RECEIVER_VIEWS):
            raise NativePreviewError("isolated host preview returned truncated frame/timing bytes")
        frames = tuple(
            raw_frames[index * frame_size : (index + 1) * frame_size]
            for index in range(frames_requested)
        )
        return PreviewRun(
            frames=frames,
            render_ms=timings,
            timing=PreviewTiming.from_samples(timings),
            changed_frames=int(result["changed_frames"]),
            missed_deadlines=int(result["missed_deadlines"]),
        )


def _strip_major_to_row_major(frame: bytes) -> bytes:
    expected = GLOBAL_STRIPS * LEDS_PER_STRIP * 3
    if len(frame) != expected:
        raise NativePreviewError("preview frame has incompatible geometry")
    result = bytearray(expected)
    for strip in range(GLOBAL_STRIPS):
        for led in range(LEDS_PER_STRIP):
            source = (strip * LEDS_PER_STRIP + led) * 3
            destination = (led * GLOBAL_STRIPS + strip) * 3
            result[destination : destination + 3] = frame[source : source + 3]
    return bytes(result)


def generate_preview_webp(frames: tuple[bytes, ...], *, duration_ms: int) -> bytes:
    if len(frames) < 2:
        raise NativePreviewError("animated preview requires at least two frames")
    try:
        from PIL import Image
    except ImportError as exc:
        raise NativePreviewError("preview generation requires Pillow") from exc
    images = [
        Image.frombytes(
            "RGB",
            (GLOBAL_STRIPS, LEDS_PER_STRIP),
            _strip_major_to_row_major(frame),
        )
        for frame in frames
    ]
    import io

    output = io.BytesIO()
    images[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=images[1:],
        duration=[duration_ms] * len(images),
        loop=0,
        lossless=True,
        method=6,
        exact=True,
    )
    return output.getvalue()


def preview_codec_identity() -> dict[str, str]:
    from PIL import __version__ as pillow_version
    from PIL import features

    return {
        "name": "pillow-webp-lossless",
        "pillow_version": pillow_version,
        "webp_version": str(features.version("webp") or "unknown"),
    }


def stress_parameters(schema: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, spec in schema.items():
        if spec["type"] in {"int", "float"}:
            values[name] = spec["max"]
        elif spec["type"] == "bool":
            values[name] = True
        elif spec["type"] == "str":
            values[name] = spec["options"][-1]
    return validate_parameters(schema, values)

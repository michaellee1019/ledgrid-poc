#!/usr/bin/env python3
"""Capture receipt-bound controller/Pi and receiver PERF-01 evidence.

This observer never starts, stops, or reconfigures the wall. It requires one
already-active guarded activation and atomically replaces retained evidence only
after the complete capture passes identity, topology, and integrity checks.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib import request

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from animation.core.activation_qualification import (
    TARGET_EVIDENCE_SCHEMA,
    TARGET_EVIDENCE_VERSION,
    normalize_target_qualification_evidence,
)
from tools.benchmarks.live_display_state import require_active_scene
from tools.benchmarks.receiver_acceptance import (
    INSTALLED_LEDS_PER_STRIP,
    INSTALLED_RECEIVER_COUNT,
    INSTALLED_RECEIVER_STRIP_COUNTS,
    INSTALLED_SPI_SPEED_HZ,
    evaluate_phase3a_status,
)


_DIGEST = __import__("re").compile(r"[0-9a-f]{64}\Z")
_ERROR_COUNTERS = (
    "receiver_crc_errors",
    "receiver_publish_drops",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_status_misses",
)
_INSTALLED_ROUTES = ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2))
_INSTALLED_NATIVE_REVERSALS = (False, False, True, True, False)


class TargetEvidenceError(RuntimeError):
    """The active target could not produce trustworthy retained evidence."""


def _get_json(url: str) -> Any:
    with request.urlopen(url, timeout=10) as response:
        return json.load(response)


def _post_json(url: str, payload: Mapping[str, Any]) -> Any:
    body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=10) as response:
        return json.load(response)


def _number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TargetEvidenceError(f"{label} is unavailable or non-numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise TargetEvidenceError(f"{label} is outside the supported range")
    return result


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TargetEvidenceError(f"{label} is unavailable or invalid")
    return value


def _percentile(values: Sequence[float], ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise TargetEvidenceError("cannot summarize an empty metric sample")
    index = min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1)
    return float(ordered[index])


def metric_stats(values: Sequence[float]) -> dict[str, float]:
    """Return deterministic nearest-rank statistics for retained evidence."""

    finite = [_number(value, "metric sample") for value in values]
    return {
        "mean": float(statistics.fmean(finite)),
        "p95": _percentile(finite, 0.95),
        "p99": _percentile(finite, 0.99),
        "max": max(finite),
    }


def validate_installed_topology(metrics: Any) -> None:
    """Bind host routes, logical widths, offsets, and receiver geometry."""

    if not isinstance(metrics, Mapping):
        raise TargetEvidenceError("metrics sample is malformed")
    driver = metrics.get("driver")
    if not isinstance(driver, Mapping):
        raise TargetEvidenceError("driver metrics are unavailable")
    aggregate = driver.get("aggregate")
    devices = driver.get("devices")
    if not isinstance(aggregate, Mapping) or not isinstance(devices, list):
        raise TargetEvidenceError("installed topology metrics are unavailable")
    if (
        aggregate.get("num_devices") != INSTALLED_RECEIVER_COUNT
        or aggregate.get("strip_count") != sum(INSTALLED_RECEIVER_STRIP_COUNTS)
        or aggregate.get("total_leds")
        != sum(INSTALLED_RECEIVER_STRIP_COUNTS) * INSTALLED_LEDS_PER_STRIP
        or len(devices) != INSTALLED_RECEIVER_COUNT
    ):
        raise TargetEvidenceError("aggregate geometry is not the exact installed wall")
    device_map = aggregate.get("device_map")
    if not isinstance(device_map, list) or len(device_map) != INSTALLED_RECEIVER_COUNT:
        raise TargetEvidenceError("installed logical receiver route map is unavailable")
    by_logical = {
        item.get("logical_device"): item
        for item in device_map
        if isinstance(item, Mapping)
    }
    if set(by_logical) != set(range(INSTALLED_RECEIVER_COUNT)):
        raise TargetEvidenceError("installed logical receiver route map is incomplete")
    offset = 0
    for logical_id, (route, width, native_reversed) in enumerate(zip(
        _INSTALLED_ROUTES,
        INSTALLED_RECEIVER_STRIP_COUNTS,
        _INSTALLED_NATIVE_REVERSALS,
    )):
        mapped = by_logical[logical_id]
        expected_map = {
            "bus": route[0],
            "chip_select": route[1],
            "local_strip_count": width,
            "global_strip_offset": offset,
            "physical_output_lane_mask": 0xFF,
            "reverse_host_strip_order": False,
            "reverse_native_strip_order": native_reversed,
            "spi_mode": 0,
            "spi_speed_hz": INSTALLED_SPI_SPEED_HZ,
        }
        for field, expected in expected_map.items():
            if mapped.get(field) != expected:
                raise TargetEvidenceError(
                    f"receiver {logical_id} topology {field} is "
                    f"{mapped.get(field)!r}, expected {expected!r}"
                )
        device = devices[logical_id]
        if not isinstance(device, Mapping):
            raise TargetEvidenceError(f"receiver {logical_id} metrics are malformed")
        expected_device = {
            "receiver_logical_device": logical_id,
            "receiver_active_strips": width,
            "receiver_global_strip_offset": offset,
            "receiver_leds_per_strip": INSTALLED_LEDS_PER_STRIP,
            "total_leds": width * INSTALLED_LEDS_PER_STRIP,
            "spi_mode": 0,
            "spi_speed_hz": INSTALLED_SPI_SPEED_HZ,
        }
        for field, expected in expected_device.items():
            if device.get(field) != expected:
                raise TargetEvidenceError(
                    f"receiver {logical_id} reported {field} is "
                    f"{device.get(field)!r}, expected {expected!r}"
                )
        offset += width


def validate_active_activation(
    payload: Any,
    *,
    activation_id: str,
    basis_digest: str,
    scene_digest: str,
    global_settings_digest: str,
    profile_digest: str,
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise TargetEvidenceError("activation receipt is unavailable or malformed")
    if payload.get("activation_id") != activation_id:
        raise TargetEvidenceError("activation receipt ID does not match")
    if payload.get("basis_digest") != basis_digest:
        raise TargetEvidenceError("activation receipt basis digest does not match")
    if payload.get("phase") != "active":
        raise TargetEvidenceError("guarded activation is not active")
    requested = payload.get("requested_identity")
    observed = payload.get("observed_identity")
    if not isinstance(requested, Mapping) or requested != observed:
        raise TargetEvidenceError("activation identity is not freshly observed exactly")
    scene = requested.get("scene_identity")
    global_settings = requested.get("global_settings_identity")
    if not isinstance(scene, Mapping) or scene.get("digest") != scene_digest:
        raise TargetEvidenceError("activation scene digest does not match")
    if (
        not isinstance(global_settings, Mapping)
        or global_settings.get("digest") != global_settings_digest
    ):
        raise TargetEvidenceError("activation global-settings digest does not match")
    if requested.get("installation_profile_digest") != profile_digest:
        raise TargetEvidenceError("activation installation-profile digest does not match")
    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, Mapping) or not (
        telemetry.get("complete") is True and telemetry.get("fresh") is True
    ):
        raise TargetEvidenceError("activation telemetry is not complete and fresh")
    return requested


def validate_live_status(
    status: Any,
    *,
    target_fps: int,
    brightness: int,
    profile_digest: str,
    plugin: str,
) -> Mapping[str, Any]:
    if not isinstance(status, Mapping):
        raise TargetEvidenceError("live status is unavailable or malformed")
    expected = {
        "target_fps": target_fps,
        "brightness": brightness,
        "installation_profile_digest": profile_digest,
        "current_animation": plugin,
    }
    for name, value in expected.items():
        if status.get(name) != value:
            raise TargetEvidenceError(
                f"live {name} is {status.get(name)!r}, expected {value!r}"
            )
    if status.get("is_running") is not True:
        raise TargetEvidenceError("live scene is not running")
    return status


def build_target_evidence(
    metrics_samples: Sequence[Mapping[str, Any]],
    *,
    elapsed_seconds: float,
    binding_digest: str,
    captured_at: int,
    target_fps: int,
    brightness: int,
    environment: str,
) -> dict[str, Any]:
    """Build the strict envelope from an already identity-checked window."""

    if len(metrics_samples) < 2 or elapsed_seconds <= 0:
        raise TargetEvidenceError("at least two metrics samples are required")
    first = metrics_samples[0]
    last = metrics_samples[-1]
    performance_summaries: list[Mapping[str, Any]] = []
    controller_windows: list[dict[str, float]] = []
    observed_fps_samples: list[float] = []
    deadline_ratio_samples: list[float] = []
    for sample_index, sample in enumerate(metrics_samples):
        validate_installed_topology(sample)
        performance = sample.get("performance")
        if not isinstance(performance, Mapping):
            raise TargetEvidenceError(
                f"controller performance summary {sample_index} is unavailable"
            )
        window = {
            name: _number(
                performance.get(field),
                f"controller summary {sample_index} {field}",
            )
            for name, field in (
                ("mean", "avg_frame_ms"),
                ("p95", "p95_frame_ms"),
                ("p99", "p99_frame_ms"),
                ("max", "max_frame_ms"),
            )
        }
        # A small number of extreme frames can legitimately pull the mean
        # above p95.  Percentiles themselves, however, must remain ordered.
        if (
            window["mean"] > window["max"]
            or not window["p95"] <= window["p99"] <= window["max"]
        ):
            raise TargetEvidenceError(
                f"controller timing summary {sample_index} statistics are invalid"
            )
        _integer(
            performance.get("samples"),
            f"controller summary {sample_index} sample count",
            minimum=1,
        )
        performance_summaries.append(performance)
        controller_windows.append(window)
        observed_fps_samples.append(_number(
            sample.get("animation", {}).get("actual_fps"),
            f"controller summary {sample_index} observed FPS",
        ))
        deadline_ratio = _number(
            performance.get("deadline_miss_ratio"),
            f"controller summary {sample_index} deadline-miss ratio",
        )
        if deadline_ratio > 1.0:
            raise TargetEvidenceError(
                f"controller summary {sample_index} deadline-miss ratio exceeds one"
            )
        deadline_ratio_samples.append(deadline_ratio)
    performance = performance_summaries[-1]
    # Rolling windows overlap. Summing their internal frame counts would invent
    # independent samples, so the envelope reports the actual number of rolling
    # summaries observed and conservatively retains the worst statistic seen.
    observed_controller_stats = {
        name: max(window[name] for window in controller_windows)
        for name in ("mean", "p95", "p99", "max")
    }
    # The retained evidence schema requires mean <= p95 <= p99 <= max.  Make
    # the independently retained worst observations schema-ordered by raising
    # upper fields only; never discard or reduce an observed statistic.
    controller_stats = {"mean": observed_controller_stats["mean"]}
    for name, lower_name in (("p95", "mean"), ("p99", "p95"), ("max", "p99")):
        controller_stats[name] = max(
            observed_controller_stats[name],
            controller_stats[lower_name],
        )
    controller_samples = len(performance_summaries)
    observed_fps = min(observed_fps_samples)
    deadline_ratio = max(deadline_ratio_samples)
    first_presented = _integer(
        first.get("performance", {}).get("frames_presented"),
        "initial frames presented",
    )
    last_presented = _integer(
        performance.get("frames_presented"), "final frames presented"
    )
    first_skipped = _integer(
        first.get("performance", {}).get("unchanged_frames_skipped"),
        "initial unchanged frames",
    )
    last_skipped = _integer(
        performance.get("unchanged_frames_skipped"), "final unchanged frames"
    )
    presented_delta = last_presented - first_presented
    skipped_delta = last_skipped - first_skipped
    if presented_delta < 0 or skipped_delta < 0 or presented_delta + skipped_delta <= 0:
        raise TargetEvidenceError("controller frame accounting did not advance")
    changed_ratio = presented_delta / (presented_delta + skipped_delta)

    first_devices = first.get("driver", {}).get("devices")
    last_devices = last.get("driver", {}).get("devices")
    if not isinstance(first_devices, list) or not isinstance(last_devices, list):
        raise TargetEvidenceError("receiver metrics are unavailable")
    if len(first_devices) != INSTALLED_RECEIVER_COUNT or len(last_devices) != len(first_devices):
        raise TargetEvidenceError("receiver metrics do not contain the exact five-device wall")
    receiver_times: list[float] = []
    receiver_rates: list[float] = []
    for sample_index, sample in enumerate(metrics_samples):
        devices = sample.get("driver", {}).get("devices")
        if not isinstance(devices, list) or len(devices) != INSTALLED_RECEIVER_COUNT:
            raise TargetEvidenceError(
                f"metrics sample {sample_index} does not contain five receivers"
            )
        for logical_id, device in enumerate(devices):
            if not isinstance(device, Mapping):
                raise TargetEvidenceError(f"receiver {logical_id} metrics are malformed")
            if device.get("receiver_logical_device") != logical_id:
                raise TargetEvidenceError(f"receiver {logical_id} identity does not match")
            if _integer(
                device.get("receiver_status_version"),
                f"receiver {logical_id} status version",
            ) < 3:
                raise TargetEvidenceError(f"receiver {logical_id} status v3 is required")
            encode_ms = _number(
                device.get("receiver_last_encode_us"),
                f"receiver {logical_id} encode time",
            ) / 1000.0
            show_ms = _number(
                device.get("receiver_last_show_us"),
                f"receiver {logical_id} show time",
            ) / 1000.0
            # Encode and LED DMA are independently scheduled pipeline stages.
            # The critical per-stage latency, not their artificial sum, is the
            # receiver's cadence-bound frame-time observation.
            receiver_times.append(max(encode_ms, show_ms))
    for logical_id, (before, after) in enumerate(zip(first_devices, last_devices)):
        for counter in _ERROR_COUNTERS:
            delta = _integer(after.get(counter), f"receiver {logical_id} {counter}") - _integer(
                before.get(counter), f"receiver {logical_id} initial {counter}"
            )
            if delta != 0:
                raise TargetEvidenceError(
                    f"receiver {logical_id} {counter} increased by {delta}"
                )
        displayed = _integer(
            after.get("receiver_frames_displayed"),
            f"receiver {logical_id} displayed frames",
        ) - _integer(
            before.get("receiver_frames_displayed"),
            f"receiver {logical_id} initial displayed frames",
        )
        if displayed <= 0:
            raise TargetEvidenceError(f"receiver {logical_id} displayed no frames")
        receiver_rates.append(displayed / elapsed_seconds)
    receiver_fps = min(receiver_rates)
    receiver_miss_ratio = max(0.0, min(1.0, 1.0 - receiver_fps / target_fps))

    common = {
        "binding_digest": binding_digest,
        "captured_at": captured_at,
        "electrical": None,
    }
    envelope = {
        "schema": TARGET_EVIDENCE_SCHEMA,
        "schema_version": TARGET_EVIDENCE_VERSION,
        "revision": 1,
        "binding_digest": binding_digest,
        "captured_at": captured_at,
        "environment": environment,
        "evidence": [
            {
                **common,
                "source": "controller_pi",
                "environment": (
                    environment
                    + f"; {controller_samples} sampled rolling controller windows; "
                    "worst mean/p95/p99/max retained"
                ),
                "sample_count": controller_samples,
                "frame_time_ms": controller_stats,
                "cadence": {
                    "observed_fps": observed_fps,
                    "missed_frame_ratio": deadline_ratio,
                    "changed_frame_ratio": changed_ratio,
                },
            },
            {
                **common,
                "source": "receiver",
                "environment": (
                    environment
                    + "; five ESP32-S3 receivers; frame time=max(encode,show) stage"
                ),
                "sample_count": len(receiver_times),
                "frame_time_ms": metric_stats(receiver_times),
                "cadence": {
                    "observed_fps": receiver_fps,
                    "missed_frame_ratio": receiver_miss_ratio,
                    "changed_frame_ratio": None,
                },
            },
        ],
    }
    return normalize_target_qualification_evidence(envelope)


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def capture(
    *,
    base_url: str,
    binding_digest: str,
    basis_digest: str,
    scene_digest: str,
    global_settings_digest: str,
    profile_digest: str,
    activation_id: str,
    plugin: str,
    target_fps: int,
    brightness: int,
    warmup: float,
    duration: float,
    interval: float,
    get_json: Callable[[str], Any] = _get_json,
    post_json: Callable[[str, Mapping[str, Any]], Any] = _post_json,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    require_active_scene(
        base,
        scene_digest,
        get_json,
        expected_plugin=plugin,
        expected_provider="python",
    )
    activation_url = f"{base}/api/v1/scene/activations/{activation_id}"
    validate_active_activation(
        get_json(activation_url),
        activation_id=activation_id,
        basis_digest=basis_digest,
        scene_digest=scene_digest,
        global_settings_digest=global_settings_digest,
        profile_digest=profile_digest,
    )
    validate_live_status(
        get_json(f"{base}/api/status"),
        target_fps=target_fps,
        brightness=brightness,
        profile_digest=profile_digest,
        plugin=plugin,
    )
    refresh = post_json(f"{base}/api/v1/receivers/status/refresh", {})
    request_id = refresh.get("request_id") if isinstance(refresh, Mapping) else None
    if not isinstance(request_id, str) or not request_id:
        raise TargetEvidenceError("receiver status refresh was not accepted")
    refresh_deadline = monotonic() + 10.0
    refreshed_metrics = None
    while monotonic() < refresh_deadline:
        refreshed_metrics = get_json(f"{base}/api/metrics")
        proof = (
            refreshed_metrics.get("driver", {})
            .get("aggregate", {})
            .get("receiver_status_refresh")
        )
        if isinstance(proof, Mapping) and proof.get("request_id") == request_id:
            break
        sleep(0.1)
    else:
        raise TargetEvidenceError("receiver status refresh did not complete")
    devices = refreshed_metrics.get("driver", {}).get("devices", [])
    phase3a = evaluate_phase3a_status(
        devices,
        refresh=proof,
        expected_refresh_id=request_id,
    )
    if not phase3a["passed"]:
        raise TargetEvidenceError(
            "receiver identity refresh failed: " + "; ".join(phase3a["failures"])
        )

    sleep(warmup)
    samples: list[Mapping[str, Any]] = []
    started = monotonic()
    while True:
        sample = get_json(f"{base}/api/metrics")
        if not isinstance(sample, Mapping):
            raise TargetEvidenceError("metrics sample is malformed")
        if sample.get("animation", {}).get("target_fps") != target_fps:
            raise TargetEvidenceError("target FPS changed during capture")
        samples.append(sample)
        if monotonic() - started >= duration:
            break
        sleep(interval)
    elapsed = monotonic() - started

    require_active_scene(
        base,
        scene_digest,
        get_json,
        expected_plugin=plugin,
        expected_provider="python",
    )
    validate_active_activation(
        get_json(activation_url),
        activation_id=activation_id,
        basis_digest=basis_digest,
        scene_digest=scene_digest,
        global_settings_digest=global_settings_digest,
        profile_digest=profile_digest,
    )
    validate_live_status(
        get_json(f"{base}/api/status"),
        target_fps=target_fps,
        brightness=brightness,
        profile_digest=profile_digest,
        plugin=plugin,
    )
    captured_at = int(time.time() * 1000)
    model_path = Path("/proc/device-tree/model")
    model = (
        model_path.read_bytes().rstrip(b"\x00").decode("utf-8", "replace")
        if model_path.is_file()
        else platform.machine()
    )
    environment = (
        f"{model}; {platform.system()} {platform.release()}; 33x138; "
        f"{target_fps} FPS; brightness {brightness}; activation {activation_id}; "
        f"basis {basis_digest}"
    )
    return build_target_evidence(
        samples,
        elapsed_seconds=elapsed,
        binding_digest=binding_digest,
        captured_at=captured_at,
        target_fps=target_fps,
        brightness=brightness,
        environment=environment,
    )


def _digest_argument(value: str) -> str:
    if _DIGEST.fullmatch(value or "") is None:
        raise argparse.ArgumentTypeError("must be a lowercase SHA-256 digest")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--binding-digest", required=True, type=_digest_argument)
    parser.add_argument("--basis-digest", required=True, type=_digest_argument)
    parser.add_argument("--expected-scene-digest", required=True, type=_digest_argument)
    parser.add_argument(
        "--expected-global-settings-digest", required=True, type=_digest_argument
    )
    parser.add_argument(
        "--expected-profile-digest", required=True, type=_digest_argument
    )
    parser.add_argument("--activation-id", required=True)
    parser.add_argument("--plugin", default="rainbow")
    parser.add_argument("--target-fps", type=int, default=150)
    parser.add_argument("--brightness", type=int, default=50)
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            _REPOSITORY_ROOT
            / "run_state"
            / "activation_qualification_evidence.json"
        ),
    )
    args = parser.parse_args()
    if not 1 <= args.target_fps <= 200:
        parser.error("--target-fps must be from 1 through 200")
    if not 0 <= args.brightness <= 255:
        parser.error("--brightness must be from 0 through 255")
    for name in ("warmup", "duration", "interval"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name} must be finite and greater than zero")
    try:
        evidence = capture(
            base_url=args.base_url,
            binding_digest=args.binding_digest,
            basis_digest=args.basis_digest,
            scene_digest=args.expected_scene_digest,
            global_settings_digest=args.expected_global_settings_digest,
            profile_digest=args.expected_profile_digest,
            activation_id=args.activation_id,
            plugin=args.plugin,
            target_fps=args.target_fps,
            brightness=args.brightness,
            warmup=args.warmup,
            duration=args.duration,
            interval=args.interval,
        )
        atomic_write_json(args.output, evidence)
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps({
        "passed": True,
        "output": str(args.output),
        "binding_digest": evidence["binding_digest"],
        "captured_at": evidence["captured_at"],
        "evidence": evidence["evidence"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

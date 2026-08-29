#!/usr/bin/env python3
"""Run and evaluate the receiver-side hardware acceptance gates."""

from __future__ import annotations

import argparse
import json
import math
import time
from urllib import request

if __package__:
    from tools.benchmarks.live_display_state import require_active_scene
else:  # Direct script execution from the documented Just recipes.
    from live_display_state import require_active_scene


CAPABILITY_STATIC_LOCAL_BACKGROUND = 1 << 0
CAPABILITY_PRESENTATION_CONTEXT_V1 = 1 << 1
CAPABILITY_STATUS_V3 = 1 << 2
CAPABILITY_EXPLICIT_BASE_OWNERSHIP = 1 << 3
DEGRADED_SPI1_WRITE_ONLY_DEVICES = frozenset((2, 3))
INSTALLED_RECEIVER_STRIP_COUNTS = (8, 8, 8, 8, 1)
INSTALLED_RECEIVER_COUNT = len(INSTALLED_RECEIVER_STRIP_COUNTS)

# Installed full-frame timing facts. Four receivers own eight 138-pixel lanes
# and the tail receiver owns one. Every SET_ALL transaction carries its own
# command byte, RGB8 body, and two-byte CRC.
INSTALLED_STRIPS_PER_RECEIVER = max(INSTALLED_RECEIVER_STRIP_COUNTS)
INSTALLED_LEDS_PER_STRIP = 138
INSTALLED_SPI_SPEED_HZ = 20_000_000
INSTALLED_RECEIVER_SPI_SPEEDS_HZ = (
    20_000_000, 20_000_000, 20_000_000, 8_000_000, 20_000_000,
)
INSTALLED_FULL_FRAME_BYTES = (
    1 + INSTALLED_STRIPS_PER_RECEIVER * INSTALLED_LEDS_PER_STRIP * 3 + 2
)
INSTALLED_RECEIVER_FULL_FRAME_BYTES = tuple(
    1 + width * INSTALLED_LEDS_PER_STRIP * 3 + 2
    for width in INSTALLED_RECEIVER_STRIP_COUNTS
)
INSTALLED_FULL_FRAME_SPI_US = (
    INSTALLED_FULL_FRAME_BYTES * 8 * 1_000_000 // INSTALLED_SPI_SPEED_HZ
)
INSTALLED_RECEIVER_FULL_FRAME_SPI_US = tuple(
    byte_count * 8 * 1_000_000 // speed
    for byte_count, speed in zip(
        INSTALLED_RECEIVER_FULL_FRAME_BYTES,
        INSTALLED_RECEIVER_SPI_SPEEDS_HZ,
    )
)
INSTALLED_NOMINAL_SHOW_US = INSTALLED_LEDS_PER_STRIP * 30 + 300
DEFAULT_TARGET_FPS = 160
DEFAULT_MIN_DISPLAYED_FPS = 150.0


def installed_streamed_timing_facts():
    """Return the documentation-backed installed full-frame timing budget."""

    return {
        "strips_per_receiver": INSTALLED_STRIPS_PER_RECEIVER,
        "receiver_strip_counts": list(INSTALLED_RECEIVER_STRIP_COUNTS),
        "receiver_full_frame_bytes": list(INSTALLED_RECEIVER_FULL_FRAME_BYTES),
        "full_wall_frame_bytes": sum(INSTALLED_RECEIVER_FULL_FRAME_BYTES),
        "leds_per_strip": INSTALLED_LEDS_PER_STRIP,
        "full_frame_bytes": INSTALLED_FULL_FRAME_BYTES,
        "spi_speed_hz": INSTALLED_SPI_SPEED_HZ,
        "receiver_spi_speeds_hz": list(INSTALLED_RECEIVER_SPI_SPEEDS_HZ),
        "full_frame_spi_us": INSTALLED_FULL_FRAME_SPI_US,
        "receiver_full_frame_spi_us": list(INSTALLED_RECEIVER_FULL_FRAME_SPI_US),
        "nominal_show_us": INSTALLED_NOMINAL_SHOW_US,
    }


NEUTRAL_PLANT_MODIFIERS = {
    "version": 1,
    "active": [],
    "strengths": {},
}


def _receiver_status_unreadable(status):
    """Recognize only the exact no-return-path state; never waive partial telemetry."""

    return (
        isinstance(status, dict)
        and "receiver_status_version" in status
        and "receiver_status_seen" in status
        and "receiver_capabilities" in status
        and "receiver_logical_device" in status
        and int(status.get("receiver_status_version", 0) or 0) == 0
        and status.get("receiver_status_seen") is False
        and int(status.get("receiver_capabilities", 0) or 0) == 0
        and status.get("receiver_logical_device") is None
    )


def _acceptance_policy(*, enabled, readable_devices, write_only_devices):
    return {
        "name": (
            "temporary_degraded_spi1_return_path"
            if enabled else "strict_all_receiver_telemetry"
        ),
        "enabled": bool(enabled),
        "telemetry_complete": not write_only_devices,
        "readable_devices": sorted(readable_devices),
        "known_write_only_devices": sorted(write_only_devices),
        "write_only_streaming_proves_display_output": False,
        "visual_verification_required": bool(write_only_devices),
        "miso_dependent_gates_deferred": bool(write_only_devices),
    }


def evaluate_phase3a_status(
    devices,
    *,
    refresh=None,
    expected_refresh_id=None,
    receiver_count=INSTALLED_RECEIVER_COUNT,
    local_canary_device=None,
    allow_degraded_spi1_return_path=False,
):
    """Evaluate fresh receiver-reported Phase 3A identity and capabilities."""

    failures = []
    warnings = []
    receiver_results = {}
    if expected_refresh_id is not None:
        if not isinstance(refresh, dict):
            failures.append("fresh receiver-status proof is unavailable")
        else:
            if refresh.get("request_id") != expected_refresh_id:
                failures.append("receiver-status proof is stale")
            if not isinstance(refresh.get("completed_at"), (int, float)):
                failures.append("receiver-status proof has no completion timestamp")
    if not isinstance(devices, list) or len(devices) != receiver_count:
        actual = len(devices) if isinstance(devices, list) else 0
        failures.append(
            f"receiver telemetry has {actual} devices; expected {receiver_count}"
        )
        devices = devices if isinstance(devices, list) else []
    if local_canary_device is not None and not 0 <= local_canary_device < receiver_count:
        failures.append("local canary device is outside the receiver topology")

    readable_devices = []
    write_only_devices = []
    for index, status in enumerate(devices[:receiver_count]):
        if not isinstance(status, dict):
            failures.append(f"receiver {index} status is unavailable")
            receiver_results[str(index)] = {
                "accepted": False, "telemetry": "unavailable",
            }
            continue
        if (
            allow_degraded_spi1_return_path
            and index in DEGRADED_SPI1_WRITE_ONLY_DEVICES
            and _receiver_status_unreadable(status)
        ):
            write_only_devices.append(index)
            receiver_results[str(index)] = {
                "accepted": True,
                "telemetry": "known_write_only_no_miso_return",
                "status_version": 0,
                "logical_identity_verified": False,
                "capabilities_verified": False,
            }
            if index == local_canary_device:
                failures.append(
                    f"receiver {index} is write-only and cannot be used for the "
                    "local-background canary"
                )
            continue

        readable_devices.append(index)
        version = int(status.get("receiver_status_version", 0) or 0)
        capabilities = int(status.get("receiver_capabilities", 0) or 0)
        logical_id = status.get("receiver_logical_device")
        device_failures = []
        if version < 3:
            device_failures.append(
                f"receiver {index} reports status v{version}; v3 is required"
            )
        required_status = CAPABILITY_STATUS_V3 | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
        missing_status = required_status & ~capabilities
        if missing_status:
            device_failures.append(
                f"receiver {index} lacks Phase 3A status capabilities "
                f"0x{missing_status:08x}"
            )
        if logical_id != index:
            device_failures.append(
                f"receiver {index} reports logical identity {logical_id!r}"
            )
        if index == local_canary_device:
            required = (
                CAPABILITY_STATIC_LOCAL_BACKGROUND
                | CAPABILITY_PRESENTATION_CONTEXT_V1
            )
            missing = required & ~capabilities
            if missing:
                device_failures.append(
                    f"receiver {index} lacks local-canary capabilities 0x{missing:08x}"
                )
        failures.extend(device_failures)
        receiver_results[str(index)] = {
            "accepted": not device_failures,
            "telemetry": "readable",
            "status_version": version,
            "logical_identity_verified": logical_id == index,
            "capabilities_verified": missing_status == 0,
            **({"failures": device_failures} if device_failures else {}),
        }

    if expected_refresh_id is not None and isinstance(refresh, dict):
        refresh_errors = refresh.get("errors", [])
        refresh_error_devices = {
            item.get("logical_device")
            for item in refresh_errors
            if isinstance(item, dict)
        } if isinstance(refresh_errors, list) else set()
        if not refresh.get("passed"):
            if (
                not allow_degraded_spi1_return_path
                or not write_only_devices
                or refresh_error_devices != set(write_only_devices)
            ):
                failures.append(
                    "fresh receiver-status query did not pass on every required readable board"
                )
        elif refresh_errors:
            failures.append("receiver-status refresh reports errors despite passing")

    if write_only_devices:
        if set(write_only_devices) != set(DEGRADED_SPI1_WRITE_ONLY_DEVICES):
            failures.append(
                "degraded SPI1 return-path policy requires the exact write-only "
                "logical-device pair 2 and 3"
            )
        warnings.append(
            "DEGRADED ACCEPTANCE: receivers 2 and 3 have no usable MISO return; "
            "their identity, capabilities, receiver counters, and physical display "
            "output are unverified"
        )
    policy = _acceptance_policy(
        enabled=allow_degraded_spi1_return_path,
        readable_devices=readable_devices,
        write_only_devices=write_only_devices,
    )
    return {
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "acceptance_policy": policy,
        "receivers": receiver_results,
    }


def _percentile(values, ratio):
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1)
    return ordered[index]


def evaluate_samples(
    samples,
    elapsed_seconds,
    min_displayed_fps=DEFAULT_MIN_DISPLAYED_FPS,
):
    if len(samples) < 2 or elapsed_seconds <= 0:
        return {"passed": False, "failures": ["insufficient samples"]}

    first = samples[0]
    last = samples[-1]

    def delta(key):
        return int(last.get(key, 0) or 0) - int(first.get(key, 0) or 0)

    accepted = delta("receiver_frames_accepted")
    displayed = delta("receiver_frames_displayed")
    superseded = delta("receiver_frames_superseded")
    displayed_fps = displayed / elapsed_seconds
    outstanding = (
        int(last.get("receiver_frames_accepted", 0) or 0)
        - int(last.get("receiver_frames_displayed", 0) or 0)
        - int(last.get("receiver_frames_superseded", 0) or 0)
    )
    encode_p95 = _percentile(
        [int(sample.get("receiver_last_encode_us", 0) or 0) for sample in samples],
        0.95,
    )
    show_p95 = _percentile(
        [int(sample.get("receiver_last_show_us", 0) or 0) for sample in samples],
        0.95,
    )

    failures = []
    if any(int(sample.get("receiver_status_version", 0) or 0) < 2 for sample in samples):
        failures.append("receiver status v2+ was not present in every sample")
    for key, label in (
        ("receiver_crc_errors", "CRC errors"),
        ("receiver_publish_drops", "mailbox publish drops"),
        ("receiver_spi_queue_errors", "SPI queue errors"),
        ("receiver_display_errors", "display errors"),
        ("receiver_status_misses", "missing receiver status responses"),
    ):
        if delta(key) != 0:
            failures.append(f"{label} increased by {delta(key)}")
    if show_p95 > 4800:
        failures.append(f"display DMA p95 {show_p95} us exceeds 4800 us")
    if encode_p95 > 1000:
        failures.append(f"frame encode p95 {encode_p95} us exceeds 1000 us")
    if displayed_fps < min_displayed_fps:
        failures.append(
            f"displayed rate {displayed_fps:.1f} FPS is below "
            f"{min_displayed_fps:g} FPS"
        )
    if accepted <= 0:
        failures.append("no frames were accepted")
    elif displayed + superseded < max(0, accepted - 3):
        failures.append(
            f"accepted accounting is incomplete: {accepted} accepted, "
            f"{displayed} displayed, {superseded} superseded"
        )
    if outstanding < 0 or outstanding > 3:
        failures.append(f"mailbox outstanding count {outstanding} is outside 0..3")

    return {
        "passed": not failures,
        "failures": failures,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "accepted_delta": accepted,
        "displayed_delta": displayed,
        "superseded_delta": superseded,
        "displayed_fps": round(displayed_fps, 2),
        "encode_p95_us": encode_p95,
        "show_p95_us": show_p95,
        "outstanding_frames": outstanding,
        "installed_timing_facts": installed_streamed_timing_facts(),
    }


def evaluate_write_only_samples(samples, elapsed_seconds, *, require_progress=True):
    """Prove host-side outbound traffic without claiming receiver/display evidence."""

    failures = []
    if len(samples) < 2 or elapsed_seconds <= 0:
        return {
            "passed": False,
            "failures": ["insufficient samples"],
            "telemetry": "known_write_only_no_miso_return",
            "known_write_only_state": False,
            "receiver_telemetry_verified": False,
            "physical_display_verified": False,
        }
    known_write_only_state = all(
        _receiver_status_unreadable(sample) for sample in samples
    )
    if not known_write_only_state:
        failures.append(
            "write-only exemption requires exact status v0/no-identity/no-capability samples"
        )

    first = samples[0]
    last = samples[-1]

    def delta(key):
        return int(last.get(key, 0) or 0) - int(first.get(key, 0) or 0)

    frames = delta("frames_sent")
    transfers = delta("spi_transfers")
    payload_bytes = delta("bytes_sent")
    errors = delta("errors")
    if require_progress and (frames <= 0 or transfers <= 0 or payload_bytes <= 0):
        failures.append(
            "host-side streamed traffic did not advance frames, transfers, and bytes"
        )
    elif not require_progress and (frames < 0 or transfers < 0 or payload_bytes < 0):
        failures.append("host-side streamed counters moved backwards")
    if errors != 0:
        failures.append(f"host SPI errors increased by {errors}")
    return {
        "passed": not failures,
        "failures": failures,
        "telemetry": "known_write_only_no_miso_return",
        "known_write_only_state": known_write_only_state,
        "receiver_telemetry_verified": False,
        "physical_display_verified": False,
        "visual_verification_required": True,
        "host_frames_delta": frames,
        "host_transfers_delta": transfers,
        "host_bytes_delta": payload_bytes,
        "host_errors_delta": errors,
    }


def _get_json(url):
    with request.urlopen(url, timeout=5) as response:
        return json.load(response)


def _post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://ledgridwall.local:5000")
    parser.add_argument(
        "--device",
        type=int,
        action="append",
        dest="devices",
        help="logical receiver index; repeat to evaluate multiple receivers in one run",
    )
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--animation", default="rainbow")
    parser.add_argument(
        "--expected-scene-digest",
        help="exact digest from the guarded Composer activation receipt",
    )
    parser.add_argument(
        "--min-displayed-fps", type=float, default=DEFAULT_MIN_DISPLAYED_FPS,
    )
    parser.add_argument(
        "--target-fps", type=int, default=DEFAULT_TARGET_FPS,
        help="expected already-active cadence; this observer never changes it",
    )
    parser.add_argument(
        "--phase3a-status-only",
        action="store_true",
        help="check fresh v3 ownership capability and receiver-reported identities",
    )
    parser.add_argument(
        "--local-canary-device",
        type=int,
        help="also require static-background/context capabilities on this receiver",
    )
    parser.add_argument(
        "--allow-degraded-spi1-return-path",
        action="store_true",
        help=(
            "temporary installed-wall policy: permit only exact no-return status "
            "on logical receivers 2 and 3 while requiring full telemetry from 0, 1, and 4"
        ),
    )
    args = parser.parse_args()

    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("--duration must be finite and greater than zero")
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be finite and greater than zero")
    if not math.isfinite(args.warmup) or args.warmup < 0:
        parser.error("--warmup must be finite and non-negative")
    if not math.isfinite(args.min_displayed_fps) or args.min_displayed_fps <= 0:
        parser.error("--min-displayed-fps must be finite and greater than zero")
    if isinstance(args.target_fps, bool) or not 1 <= args.target_fps <= 200:
        parser.error("--target-fps must be between 1 and 200")
    if args.min_displayed_fps > args.target_fps:
        parser.error("--min-displayed-fps cannot exceed --target-fps")
    if args.allow_degraded_spi1_return_path and not args.phase3a_status_only:
        requested_devices = set(args.devices or ())
        if requested_devices != set(range(INSTALLED_RECEIVER_COUNT)):
            parser.error(
                "--allow-degraded-spi1-return-path requires exactly "
                "--device 0 --device 1 --device 2 --device 3 --device 4"
            )

    base_url = args.base_url.rstrip("/")
    if args.phase3a_status_only:
        accepted = _post_json(
            f"{base_url}/api/v1/receivers/status/refresh", {}
        )
        request_id = accepted.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise SystemExit("controller did not accept a receiver-status refresh")
        deadline = time.monotonic() + 10.0
        metrics = None
        refresh = None
        while time.monotonic() < deadline:
            metrics = _get_json(f"{base_url}/api/metrics")
            aggregate = metrics.get("driver", {}).get("aggregate", {})
            refresh = aggregate.get("receiver_status_refresh")
            if isinstance(refresh, dict) and refresh.get("request_id") == request_id:
                break
            time.sleep(0.1)
        devices = (metrics or {}).get("driver", {}).get("devices", [])
        result = evaluate_phase3a_status(
            devices,
            refresh=refresh,
            expected_refresh_id=request_id,
            local_canary_device=args.local_canary_device,
            allow_degraded_spi1_return_path=args.allow_degraded_spi1_return_path,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(0 if result["passed"] else 1)
    if not args.expected_scene_digest:
        parser.error(
            "--expected-scene-digest is required for observation-only acceptance"
        )
    run_failure = None
    result = None
    try:
        identity = require_active_scene(
            base_url, args.expected_scene_digest, _get_json,
            expected_plugin=args.animation, expected_provider="python",
        )
        time.sleep(args.warmup)

        devices_to_check = args.devices or [0]
        samples = {device: [] for device in devices_to_check}
        sample_times = []
        while True:
            metrics = _get_json(f"{base_url}/api/metrics")
            sampled_at = time.monotonic()
            observed_target = int(
                metrics.get("animation", {}).get("target_fps", 0) or 0
            )
            if observed_target != args.target_fps:
                raise RuntimeError(
                    f"active target FPS is {observed_target}, expected "
                    f"{args.target_fps}; change it through the guarded operator "
                    "surface before measuring"
                )
            devices = metrics.get("driver", {}).get("devices", [])
            for device in devices_to_check:
                if device >= len(devices):
                    raise RuntimeError(
                        f"device index {device} is unavailable; metrics has {len(devices)} devices"
                    )
                samples[device].append(devices[device])
            sample_times.append(sampled_at)
            if sampled_at - sample_times[0] >= args.duration:
                break
            time.sleep(args.interval)

        elapsed = sample_times[-1] - sample_times[0]
        write_only_devices = []
        device_results = {}
        for device, device_samples in samples.items():
            if (
                args.allow_degraded_spi1_return_path
                and device in DEGRADED_SPI1_WRITE_ONLY_DEVICES
                and all(_receiver_status_unreadable(sample) for sample in device_samples)
            ):
                write_only_devices.append(device)
                device_results[str(device)] = evaluate_write_only_samples(
                    device_samples, elapsed
                )
            else:
                device_results[str(device)] = evaluate_samples(
                    device_samples, elapsed,
                    min_displayed_fps=args.min_displayed_fps,
                )
        if len(device_results) == 1:
            result = next(iter(device_results.values()))
        else:
            result = {
                "passed": all(item["passed"] for item in device_results.values()),
                "devices": device_results,
            }
        if args.allow_degraded_spi1_return_path:
            readable_devices = sorted(set(samples) - set(write_only_devices))
            if (
                write_only_devices
                and set(write_only_devices) != set(DEGRADED_SPI1_WRITE_ONLY_DEVICES)
            ):
                result["passed"] = False
                result.setdefault("failures", []).append(
                    "degraded SPI1 return-path policy requires the exact "
                    "write-only logical-device pair 2 and 3"
                )
            result["acceptance_policy"] = _acceptance_policy(
                enabled=True,
                readable_devices=readable_devices,
                write_only_devices=write_only_devices,
            )
            result["warnings"] = ([
                "DEGRADED ACCEPTANCE: SPI1 receivers are verified only through "
                "host-side outbound counters; receiver integrity and physical "
                "display output require visual verification and remain unproven"
            ] if write_only_devices else [])
        require_active_scene(
            base_url, args.expected_scene_digest, _get_json,
            expected_plugin=args.animation, expected_provider="python",
        )
        result["observation_only"] = True
        result["active_identity"] = identity.__dict__
    except Exception as exc:
        run_failure = str(exc)

    if run_failure:
        result = {
            "passed": False,
            "observation_only": True,
            "failures": [run_failure],
        }
    assert result is not None
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

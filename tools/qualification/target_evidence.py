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
    canonical_json_sha256,
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
_SESSION_ID = __import__("re").compile(r"[0-9a-f]{32}\Z")
_ERROR_COUNTERS = (
    "receiver_crc_errors",
    "receiver_publish_drops",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_status_misses",
)
_INSTALLED_ROUTES = ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2))
_INSTALLED_NATIVE_REVERSALS = (False, False, True, True, False)
_REQUIRED_RECEIVER_CAPABILITIES = 0x400C
_TRANSPORT_COUNTERS = (
    "spi_transfers",
    "bytes_sent",
    "semantic_bytes_sent",
    "transport_envelope_bytes_sent",
    "transport_padding_bytes_sent",
    "crc_bytes_sent",
    "full_frame_transfers",
    "full_frame_semantic_bytes_sent",
    "full_frame_wire_bytes_sent",
)
_FULL_FRAME_SAMPLING_COUNTERS = (
    "full_frame_status_transfers",
    "full_frame_status_samples",
    "full_frame_status_sample_misses",
    "full_frame_write_only_transfers",
)
_FULL_FRAME_EVIDENCE_COUNTERS = (
    "full_frame_transfers",
    *_FULL_FRAME_SAMPLING_COUNTERS,
)
_MAX_FULL_FRAME_STATUS_SAMPLE_GAP = 256


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


def _require_receiver_capabilities(
    device: Mapping[str, Any], logical_id: int
) -> None:
    capabilities = _integer(
        device.get("receiver_capabilities"),
        f"receiver {logical_id} capabilities",
    )
    if (
        capabilities & _REQUIRED_RECEIVER_CAPABILITIES
        != _REQUIRED_RECEIVER_CAPABILITIES
    ):
        raise TargetEvidenceError(
            f"receiver {logical_id} lacks required aligned transport capabilities"
        )


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TargetEvidenceError(f"{label} is unavailable or invalid")
    return value


def _require_full_frame_sampling_snapshot(
    item: Mapping[str, Any], label: str, *, minimum_buffer_size: int
) -> None:
    total = _integer(item.get("full_frame_transfers"), f"{label} full_frame_transfers")
    status_transfers = _integer(
        item.get("full_frame_status_transfers"),
        f"{label} full_frame_status_transfers",
    )
    samples = _integer(
        item.get("full_frame_status_samples"),
        f"{label} full_frame_status_samples",
    )
    misses = _integer(
        item.get("full_frame_status_sample_misses"),
        f"{label} full_frame_status_sample_misses",
    )
    write_only = _integer(
        item.get("full_frame_write_only_transfers"),
        f"{label} full_frame_write_only_transfers",
    )
    if status_transfers + write_only != total:
        raise TargetEvidenceError(f"{label} full-frame sampling transfer invariant is broken")
    if samples > status_transfers:
        raise TargetEvidenceError(f"{label} full-frame status samples exceed transfers")
    if samples + misses != status_transfers:
        raise TargetEvidenceError(
            f"{label} full-frame status transfer classification is broken"
        )
    current_gap = _integer(
        item.get("full_frame_frames_since_status_sample"),
        f"{label} full_frame_frames_since_status_sample",
    )
    maximum_gap = _integer(
        item.get("full_frame_max_status_sample_gap"),
        f"{label} full_frame_max_status_sample_gap",
    )
    if (
        current_gap > maximum_gap
        or maximum_gap > _MAX_FULL_FRAME_STATUS_SAMPLE_GAP
    ):
        raise TargetEvidenceError(f"{label} full-frame status sample gap is outside 0..256")
    buffer_size = _integer(
        item.get("spidev_buffer_size"), f"{label} spidev_buffer_size", minimum=1
    )
    if buffer_size < minimum_buffer_size:
        raise TargetEvidenceError(
            f"{label} spidev buffer {buffer_size} is below {minimum_buffer_size} bytes"
        )
    if item.get("full_frame_write_only_supported") is not True:
        raise TargetEvidenceError(f"{label} full-frame write-only fast path is unavailable")


def _require_full_frame_sampling_delta(
    before: Mapping[str, Any], after: Mapping[str, Any], label: str
) -> None:
    total_delta = _integer(
        after.get("full_frame_transfers"), f"{label} final full_frame_transfers"
    ) - _integer(
        before.get("full_frame_transfers"), f"{label} initial full_frame_transfers"
    )
    deltas = {
        field: _integer(after.get(field), f"{label} final {field}")
        - _integer(before.get(field), f"{label} initial {field}")
        for field in _FULL_FRAME_SAMPLING_COUNTERS
    }
    if total_delta < 0 or any(delta < 0 for delta in deltas.values()):
        raise TargetEvidenceError(f"{label} full-frame sampling counter reset")
    if (
        deltas["full_frame_status_transfers"]
        + deltas["full_frame_write_only_transfers"]
        != total_delta
    ):
        raise TargetEvidenceError(f"{label} full-frame sampling delta invariant is broken")
    if deltas["full_frame_status_samples"] <= 0:
        raise TargetEvidenceError(f"{label} full-frame status samples did not advance")
    if deltas["full_frame_write_only_transfers"] <= 0:
        raise TargetEvidenceError(f"{label} full-frame write-only transfers did not advance")
    if (
        deltas["full_frame_status_samples"]
        > deltas["full_frame_status_transfers"]
    ):
        raise TargetEvidenceError(f"{label} full-frame status sample delta exceeds transfers")
    if (
        deltas["full_frame_status_samples"]
        + deltas["full_frame_status_sample_misses"]
        != deltas["full_frame_status_transfers"]
    ):
        raise TargetEvidenceError(
            f"{label} full-frame status transfer delta classification is broken"
        )
    if deltas["full_frame_status_sample_misses"] != 0:
        raise TargetEvidenceError(f"{label} full-frame status sample misses increased")
    if _integer(
        after.get("full_frame_max_status_sample_gap"),
        f"{label} final full_frame_max_status_sample_gap",
    ) < _integer(
        before.get("full_frame_max_status_sample_gap"),
        f"{label} initial full_frame_max_status_sample_gap",
    ):
        raise TargetEvidenceError(f"{label} full-frame maximum status sample gap reset")


def _full_frame_transport_evidence_item(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    expected_wire_bytes: int,
    logical_device: int | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "expected_wire_bytes": expected_wire_bytes,
        "deltas": {
            field: _integer(after.get(field), f"final {field}")
            - _integer(before.get(field), f"initial {field}")
            for field in _FULL_FRAME_EVIDENCE_COUNTERS
        },
        "final": {
            "full_frame_frames_since_status_sample": _integer(
                after.get("full_frame_frames_since_status_sample"),
                "final full_frame_frames_since_status_sample",
            ),
            "full_frame_max_status_sample_gap": _integer(
                after.get("full_frame_max_status_sample_gap"),
                "final full_frame_max_status_sample_gap",
            ),
            "spidev_buffer_size": _integer(
                after.get("spidev_buffer_size"),
                "final spidev_buffer_size",
                minimum=1,
            ),
            "full_frame_write_only_supported": (
                after.get("full_frame_write_only_supported") is True
            ),
        },
    }
    if logical_device is not None:
        item["logical_device"] = logical_device
    return item


def _require_transport_accounting_snapshot(driver: Mapping[str, Any]) -> None:
    aggregate = driver.get("aggregate")
    devices = driver.get("devices")
    if not isinstance(aggregate, Mapping) or not isinstance(devices, list):
        raise TargetEvidenceError("aligned transport metrics are unavailable")
    if aggregate.get("transport_envelope_devices") != INSTALLED_RECEIVER_COUNT:
        raise TargetEvidenceError("aligned transport is not enabled on exactly five receivers")
    additive_fields = _TRANSPORT_COUNTERS + _FULL_FRAME_SAMPLING_COUNTERS
    totals = {field: 0 for field in additive_fields}
    current_gaps: list[int] = []
    maximum_gaps: list[int] = []
    buffer_sizes: list[int] = []
    for logical_id, device in enumerate(devices):
        if not isinstance(device, Mapping):
            raise TargetEvidenceError(f"receiver {logical_id} metrics are malformed")
        if device.get("transport_envelope_enabled") is not True:
            raise TargetEvidenceError(
                f"receiver {logical_id} host aligned transport is not enabled"
            )
        if (
            "transport_envelope_negotiation_candidate" not in device
            or device.get("transport_envelope_negotiation_candidate") is not None
            or type(device.get("transport_envelope_negotiation_streak")) is not int
            or device.get("transport_envelope_negotiation_streak") != 0
            or type(device.get("transport_envelope_negotiation_required")) is not int
            or device.get("transport_envelope_negotiation_required") != 3
        ):
            raise TargetEvidenceError(
                f"receiver {logical_id} aligned transport negotiation is not settled"
            )
        minimum_wire_size = 3320 if logical_id < 4 else 424
        _require_full_frame_sampling_snapshot(
            device,
            f"receiver {logical_id}",
            minimum_buffer_size=minimum_wire_size,
        )
        current_gaps.append(int(device["full_frame_frames_since_status_sample"]))
        maximum_gaps.append(int(device["full_frame_max_status_sample_gap"]))
        buffer_sizes.append(int(device["spidev_buffer_size"]))
        for field in additive_fields:
            totals[field] += _integer(
                device.get(field), f"receiver {logical_id} {field}"
            )
    for field, expected in totals.items():
        observed = _integer(aggregate.get(field), f"aggregate {field}")
        if observed != expected:
            raise TargetEvidenceError(
                f"aggregate {field} drifted from per-receiver total"
            )
    _require_full_frame_sampling_snapshot(
        aggregate, "aggregate", minimum_buffer_size=3320
    )
    expected_gauges = {
        "full_frame_frames_since_status_sample": max(current_gaps),
        "full_frame_max_status_sample_gap": max(maximum_gaps),
        "spidev_buffer_size": min(buffer_sizes),
        "full_frame_write_only_supported": True,
    }
    for field, expected in expected_gauges.items():
        if aggregate.get(field) != expected:
            raise TargetEvidenceError(
                f"aggregate {field} drifted from per-receiver value"
            )


def _require_transport_accounting_delta(
    before: Mapping[str, Any], after: Mapping[str, Any], label: str,
    *, expected_full_semantic_bytes: int | None = None,
) -> None:
    deltas = {
        field: _integer(after.get(field), f"{label} final {field}")
        - _integer(before.get(field), f"{label} initial {field}")
        for field in _TRANSPORT_COUNTERS
    }
    for field, delta in deltas.items():
        if delta <= 0:
            raise TargetEvidenceError(f"{label} {field} did not advance")
    transfers = deltas["spi_transfers"]
    if deltas["transport_envelope_bytes_sent"] != 4 * transfers:
        raise TargetEvidenceError(f"{label} envelope accounting is inconsistent")
    if deltas["crc_bytes_sent"] != 2 * transfers:
        raise TargetEvidenceError(f"{label} CRC accounting is inconsistent")
    expected_wire = (
        deltas["semantic_bytes_sent"]
        + deltas["transport_envelope_bytes_sent"]
        + deltas["transport_padding_bytes_sent"]
        + deltas["crc_bytes_sent"]
    )
    if deltas["bytes_sent"] != expected_wire:
        raise TargetEvidenceError(f"{label} wire-byte accounting is inconsistent")
    if expected_full_semantic_bytes is not None:
        full_transfers = deltas["full_frame_transfers"]
        expected_full_wire_bytes = (
            (expected_full_semantic_bytes + 6 + 3) // 4
        ) * 4
        if (
            deltas["full_frame_semantic_bytes_sent"]
            != expected_full_semantic_bytes * full_transfers
            or deltas["full_frame_wire_bytes_sent"]
            != expected_full_wire_bytes * full_transfers
        ):
            raise TargetEvidenceError(
                f"{label} full-frame SET_ALL accounting is inconsistent"
            )


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
    _require_transport_accounting_snapshot(driver)
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
        _require_receiver_capabilities(device, logical_id)
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
    normalized = payload.get("normalized_identity")
    observed = payload.get("observed_identity")
    if (
        not isinstance(requested, Mapping)
        or not isinstance(normalized, Mapping)
        or not isinstance(observed, Mapping)
        or requested != normalized
        or requested != observed
    ):
        raise TargetEvidenceError(
            "activation requested, normalized, and observed identities are not unanimous"
        )
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


def validate_runtime_identity(
    status: Any,
    activation_receipt: Any,
) -> dict[str, Any]:
    """Return the exact release/session/runtime identity of one live receipt."""

    if not isinstance(status, Mapping):
        raise TargetEvidenceError("live status is unavailable or malformed")
    release_id = status.get("release_id")
    controller_release_id = status.get("controller_release_id")
    if (
        status.get("release_consistent") is not True
        or not isinstance(release_id, str)
        or _DIGEST.fullmatch(release_id) is None
        or controller_release_id != release_id
    ):
        raise TargetEvidenceError(
            "web and controller release identities are unavailable or inconsistent"
        )
    session_id = status.get("controller_session_id")
    if (
        not isinstance(session_id, str)
        or _SESSION_ID.fullmatch(session_id) is None
    ):
        raise TargetEvidenceError("controller session identity is unavailable or invalid")
    state_revision = _integer(
        status.get("controller_state_revision"),
        "controller state revision",
    )
    current_identity_digest = status.get("current_identity_digest")
    active_identity = status.get("active_identity")
    if (
        not isinstance(current_identity_digest, str)
        or _DIGEST.fullmatch(current_identity_digest) is None
        or not isinstance(active_identity, Mapping)
        or canonical_json_sha256(active_identity) != current_identity_digest
    ):
        raise TargetEvidenceError("controller runtime identity is unavailable or invalid")
    if not isinstance(activation_receipt, Mapping):
        raise TargetEvidenceError("activation receipt is unavailable or malformed")
    controller = activation_receipt.get("controller")
    requested = activation_receipt.get("requested_identity")
    normalized = activation_receipt.get("normalized_identity")
    observed = activation_receipt.get("observed_identity")
    if (
        not isinstance(controller, Mapping)
        or controller.get("session_id") != session_id
    ):
        raise TargetEvidenceError(
            "activation receipt controller session does not match live status"
        )
    receipt_revision = _integer(
        controller.get("state_revision_after"),
        "activation receipt controller state revision",
    )
    if receipt_revision != state_revision:
        raise TargetEvidenceError(
            "activation receipt controller state revision does not match live status"
        )
    if (
        not isinstance(requested, Mapping)
        or not isinstance(normalized, Mapping)
        or not isinstance(observed, Mapping)
        or requested != normalized
        or requested != observed
    ):
        raise TargetEvidenceError(
            "activation requested, normalized, and observed identities are not unanimous"
        )
    if (
        canonical_json_sha256(requested) != current_identity_digest
    ):
        raise TargetEvidenceError(
            "activation receipt runtime identity does not match live status"
        )
    return {
        "release_id": release_id,
        "controller_session_id": session_id,
        "controller_state_revision": state_revision,
        "current_identity_digest": current_identity_digest,
    }


def build_target_evidence(
    metrics_samples: Sequence[Mapping[str, Any]],
    *,
    elapsed_seconds: float,
    binding_digest: str,
    captured_at: int,
    target_fps: int,
    brightness: int,
    environment: str,
    runtime_identity: Mapping[str, Any],
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
        sample_driver = sample.get("driver", {})
        if not isinstance(sample_driver, Mapping):
            raise TargetEvidenceError(f"metrics sample {sample_index} driver is malformed")
        devices = sample_driver.get("devices")
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
    _require_transport_accounting_delta(
        first["driver"]["aggregate"],
        last["driver"]["aggregate"],
        "aggregate aligned transport",
    )
    _require_full_frame_sampling_delta(
        first["driver"]["aggregate"],
        last["driver"]["aggregate"],
        "aggregate aligned transport",
    )
    for logical_id, (before, after) in enumerate(zip(first_devices, last_devices)):
        _require_transport_accounting_delta(
            before,
            after,
            f"receiver {logical_id} aligned transport",
            expected_full_semantic_bytes=(
                1 + INSTALLED_RECEIVER_STRIP_COUNTS[logical_id]
                * INSTALLED_LEDS_PER_STRIP * 3
            ),
        )
        _require_full_frame_sampling_delta(
            before, after, f"receiver {logical_id} aligned transport"
        )
        full_frames = _integer(
            after.get("full_frame_transfers"),
            f"receiver {logical_id} final full-frame transfers",
        ) - _integer(
            before.get("full_frame_transfers"),
            f"receiver {logical_id} initial full-frame transfers",
        )
        if full_frames / elapsed_seconds < target_fps:
            raise TargetEvidenceError(
                f"receiver {logical_id} full-frame SET_ALL rate is below {target_fps} FPS"
            )
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
    transport = {
        "aggregate": _full_frame_transport_evidence_item(
            first["driver"]["aggregate"],
            last["driver"]["aggregate"],
            expected_wire_bytes=3320,
        ),
        "devices": [
            _full_frame_transport_evidence_item(
                before,
                after,
                expected_wire_bytes=(3320 if logical_id < 4 else 424),
                logical_device=logical_id,
            )
            for logical_id, (before, after) in enumerate(
                zip(first_devices, last_devices)
            )
        ],
    }
    envelope = {
        "schema": TARGET_EVIDENCE_SCHEMA,
        "schema_version": TARGET_EVIDENCE_VERSION,
        "revision": 1,
        "binding_digest": binding_digest,
        "captured_at": captured_at,
        "environment": environment,
        "runtime_identity": dict(runtime_identity),
        "transport": transport,
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
                "transport_digest": canonical_json_sha256(transport),
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
    initial_receipt = get_json(activation_url)
    validate_active_activation(
        initial_receipt,
        activation_id=activation_id,
        basis_digest=basis_digest,
        scene_digest=scene_digest,
        global_settings_digest=global_settings_digest,
        profile_digest=profile_digest,
    )
    initial_status = get_json(f"{base}/api/status")
    validate_live_status(
        initial_status,
        target_fps=target_fps,
        brightness=brightness,
        profile_digest=profile_digest,
        plugin=plugin,
    )
    runtime_identity = validate_runtime_identity(initial_status, initial_receipt)
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
    final_receipt = get_json(activation_url)
    validate_active_activation(
        final_receipt,
        activation_id=activation_id,
        basis_digest=basis_digest,
        scene_digest=scene_digest,
        global_settings_digest=global_settings_digest,
        profile_digest=profile_digest,
    )
    final_status = get_json(f"{base}/api/status")
    validate_live_status(
        final_status,
        target_fps=target_fps,
        brightness=brightness,
        profile_digest=profile_digest,
        plugin=plugin,
    )
    if validate_runtime_identity(final_status, final_receipt) != runtime_identity:
        raise TargetEvidenceError(
            "release, controller session, or runtime identity changed during capture"
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
        runtime_identity=runtime_identity,
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

#!/usr/bin/env python3
"""Retain receipt-bound WALL-02 evidence for an already activated Python scene.

This runner is deliberately observation-only.  Composer Check and guarded
activation must already have produced an active receipt.  The runner binds the
receipt, immutable application release, controller session/revision, exact
canonical scene digest, and installed five-receiver topology for a real
30-minute soak.  It fails on identity drift, stale/incomplete activation
telemetry, counter resets, new transport/display faults, or inadequate cadence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.benchmarks.live_display_state import canonical_scene_digest  # noqa: E402


SCHEMA = "ledgrid.guarded-wall-soak"
SCHEMA_VERSION = 1
DEFAULT_TARGET = "ledgridwall.local"
DEFAULT_DURATION_SECONDS = 30 * 60
MIN_RELEASE_DURATION_SECONDS = 30 * 60
DEFAULT_SAMPLE_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_TARGET_FPS = 150
DEFAULT_MIN_DISPLAYED_FPS = 150.0
REQUIRED_RECEIVER_CAPABILITIES = 0xFC00C
EXPECTED_FULL_FRAME_WIRE_BYTES = (3320, 3320, 3320, 4088, 424)
EXPECTED_GEOMETRY = {"strip_count": 33, "leds_per_strip": 138, "total_leds": 4554}
EXPECTED_TOPOLOGY = (
    {
        "logical_device": 0,
        "local_strip_count": 8,
        "global_strip_offset": 0,
        "bus": 0,
        "chip_select": 0,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": False,
    },
    {
        "logical_device": 1,
        "local_strip_count": 8,
        "global_strip_offset": 8,
        "bus": 0,
        "chip_select": 1,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": False,
    },
    {
        "logical_device": 2,
        "local_strip_count": 8,
        "global_strip_offset": 16,
        "bus": 1,
        "chip_select": 1,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": True,
    },
    {
        "logical_device": 3,
        "local_strip_count": 8,
        "global_strip_offset": 24,
        "bus": 1,
        "chip_select": 0,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": True,
    },
    {
        "logical_device": 4,
        "local_strip_count": 1,
        "global_strip_offset": 32,
        "bus": 1,
        "chip_select": 2,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": False,
    },
)
EXPECTED_TOPOLOGY_BY_ID = {
    item["logical_device"]: item for item in EXPECTED_TOPOLOGY
}
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
ACTIVATION_ID_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}\Z")

ERROR_COUNTERS = (
    "errors",
    "receiver_crc_errors",
    "receiver_publish_drops",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_status_misses",
)
FEC_COUNTERS = (
    "fec_frames_sent",
    "fec_codewords_sent",
    "fec_parity_bytes_sent",
    "fec_data_padding_bytes_sent",
    "receiver_fec_packets_received",
    "receiver_fec_packets_accepted",
    "receiver_fec_corrected_packets",
    "receiver_fec_corrected_codewords",
    "receiver_fec_uncorrectable_packets",
    "receiver_fec_semantic_crc_errors",
    "receiver_fec_framing_errors",
)
CONTINUITY_COUNTERS = (
    "frames_sent",
    "spi_transfers",
    "bytes_sent",
    "semantic_bytes_sent",
    "transport_envelope_bytes_sent",
    "transport_padding_bytes_sent",
    "full_frame_transfers",
    "full_frame_semantic_bytes_sent",
    "full_frame_wire_bytes_sent",
    "full_frame_status_transfers",
    "full_frame_status_samples",
    "full_frame_status_sample_misses",
    "full_frame_write_only_transfers",
    "crc_bytes_sent",
    "receiver_operation_sequence",
    "receiver_packets",
    "receiver_crc_ok_packets",
    "receiver_frames_accepted",
    "receiver_frames_displayed",
    "receiver_frames_superseded",
    "receiver_status_responses",
    *FEC_COUNTERS,
    *ERROR_COUNTERS,
)
AGGREGATE_CONTINUITY_COUNTERS = (
    "frames_sent",
    "logical_frames_sent",
    "spi_transfers",
    "bytes_sent",
    "semantic_bytes_sent",
    "transport_envelope_bytes_sent",
    "transport_padding_bytes_sent",
    "full_frame_transfers",
    "full_frame_semantic_bytes_sent",
    "full_frame_wire_bytes_sent",
    "full_frame_status_transfers",
    "full_frame_status_samples",
    "full_frame_status_sample_misses",
    "full_frame_write_only_transfers",
    "crc_bytes_sent",
    "receiver_packets",
    "receiver_crc_ok_packets",
    "receiver_frames_accepted",
    "receiver_frames_displayed",
    "receiver_frames_superseded",
    *FEC_COUNTERS,
    *ERROR_COUNTERS,
)
DEVICE_SAMPLE_FIELDS = (
    "receiver_status_version",
    "receiver_status_max_version_seen",
    "receiver_status_seen",
    "receiver_capabilities",
    "transport_envelope_enabled",
    "transport_envelope_negotiation_candidate",
    "transport_envelope_negotiation_streak",
    "transport_envelope_negotiation_required",
    "fec_transport_requested",
    "fec_transport_enabled",
    "fec_transport_negotiation_candidate",
    "fec_transport_negotiation_streak",
    "fec_transport_negotiation_required",
    "receiver_logical_device",
    "receiver_active_strips",
    "receiver_global_strip_offset",
    "receiver_lane_mask",
    "receiver_leds_per_strip",
    "receiver_base_mode",
    "receiver_last_encode_us",
    "receiver_last_show_us",
    "full_frame_frames_since_status_sample",
    "full_frame_max_status_sample_gap",
    "spidev_buffer_size",
    "full_frame_write_only_supported",
    "receiver_fec_last_decode_us",
    "receiver_fec_max_decode_us",
    *CONTINUITY_COUNTERS,
)

FULL_FRAME_SAMPLING_COUNTERS = (
    "full_frame_status_transfers",
    "full_frame_status_samples",
    "full_frame_status_sample_misses",
    "full_frame_write_only_transfers",
)
MAX_FULL_FRAME_STATUS_SAMPLE_GAP = 256


class WallSoakError(RuntimeError):
    """Live state cannot produce trustworthy WALL-02 evidence."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _counter_delta(before: Mapping[str, Any], after: Mapping[str, Any], key: str) -> int | None:
    first = _integer(before.get(key))
    last = _integer(after.get(key))
    return None if first is None or last is None else last - first


def _sampling_snapshot_failures(
    item: Mapping[str, Any], label: str, *, minimum_buffer_size: int
) -> list[str]:
    failures: list[str] = []
    total = _integer(item.get("full_frame_transfers"))
    status_transfers = _integer(item.get("full_frame_status_transfers"))
    samples = _integer(item.get("full_frame_status_samples"))
    misses = _integer(item.get("full_frame_status_sample_misses"))
    write_only = _integer(item.get("full_frame_write_only_transfers"))
    counters = (total, status_transfers, samples, misses, write_only)
    if None in counters or any(value < 0 for value in counters if value is not None):
        return [f"{label} full-frame sampling counters are unavailable"]
    if status_transfers + write_only != total:
        failures.append(f"{label} full-frame sampling transfer invariant is broken")
    if samples > status_transfers:
        failures.append(f"{label} full-frame status samples exceed transfers")
    if samples + misses != status_transfers:
        failures.append(
            f"{label} full-frame status transfer classification is broken"
        )
    current_gap = _integer(item.get("full_frame_frames_since_status_sample"))
    maximum_gap = _integer(item.get("full_frame_max_status_sample_gap"))
    if current_gap is None or maximum_gap is None:
        failures.append(f"{label} full-frame status sample gap is unavailable")
    elif (
        current_gap > maximum_gap
        or maximum_gap > MAX_FULL_FRAME_STATUS_SAMPLE_GAP
    ):
        failures.append(
            f"{label} full-frame status sample gap is outside 0..256 "
            f"(current={current_gap}, maximum={maximum_gap}; "
            "expected current <= maximum)"
        )
    buffer_size = _integer(item.get("spidev_buffer_size"))
    if buffer_size is None or buffer_size < minimum_buffer_size:
        failures.append(
            f"{label} spidev buffer is below {minimum_buffer_size} bytes"
        )
    if item.get("full_frame_write_only_supported") is not True:
        failures.append(f"{label} full-frame write-only fast path is unavailable")
    return failures


def _sampling_delta_failures(
    before: Mapping[str, Any], after: Mapping[str, Any], label: str
) -> list[str]:
    total_delta = _counter_delta(before, after, "full_frame_transfers")
    deltas = {
        field: _counter_delta(before, after, field)
        for field in FULL_FRAME_SAMPLING_COUNTERS
    }
    if total_delta is None or any(delta is None for delta in deltas.values()):
        return [f"{label} full-frame sampling delta is unavailable"]
    failures: list[str] = []
    if total_delta < 0 or any(delta < 0 for delta in deltas.values()):
        failures.append(f"{label} full-frame sampling counter reset")
        return failures
    if (
        deltas["full_frame_status_transfers"]
        + deltas["full_frame_write_only_transfers"]
        != total_delta
    ):
        failures.append(f"{label} full-frame sampling delta invariant is broken")
    if deltas["full_frame_status_samples"] <= 0:
        failures.append(f"{label} full-frame status samples did not advance")
    if deltas["full_frame_write_only_transfers"] <= 0:
        failures.append(f"{label} full-frame write-only transfers did not advance")
    if (
        deltas["full_frame_status_samples"]
        > deltas["full_frame_status_transfers"]
    ):
        failures.append(f"{label} full-frame status sample delta exceeds transfers")
    if (
        deltas["full_frame_status_samples"]
        + deltas["full_frame_status_sample_misses"]
        != deltas["full_frame_status_transfers"]
    ):
        failures.append(
            f"{label} full-frame status transfer delta classification is broken"
        )
    if deltas["full_frame_status_sample_misses"] != 0:
        failures.append(f"{label} full-frame status sample misses increased")
    before_max = _integer(before.get("full_frame_max_status_sample_gap"))
    after_max = _integer(after.get("full_frame_max_status_sample_gap"))
    if before_max is None or after_max is None:
        failures.append(f"{label} full-frame maximum status sample gap is unavailable")
    elif after_max < before_max:
        failures.append(f"{label} full-frame maximum status sample gap reset")
    return failures


@dataclass(frozen=True)
class WallSoakConfig:
    activation_id: str
    expected_scene_digest: str
    expected_release_id: str
    expected_basis_digest: str
    target: str = DEFAULT_TARGET
    duration_seconds: float = DEFAULT_DURATION_SECONDS
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    target_fps: int = DEFAULT_TARGET_FPS
    min_displayed_fps: float = DEFAULT_MIN_DISPLAYED_FPS
    expected_plugin: str = "rainbow"

    def __post_init__(self) -> None:
        if ACTIVATION_ID_PATTERN.fullmatch(self.activation_id or "") is None:
            raise ValueError("activation_id must be a stable receipt identifier")
        for value, label in (
            (self.expected_scene_digest, "expected_scene_digest"),
            (self.expected_release_id, "expected_release_id"),
            (self.expected_basis_digest, "expected_basis_digest"),
        ):
            if DIGEST_PATTERN.fullmatch(value or "") is None:
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")
        for value, label in (
            (self.duration_seconds, "duration_seconds"),
            (self.sample_interval_seconds, "sample_interval_seconds"),
            (self.timeout_seconds, "timeout_seconds"),
            (self.min_displayed_fps, "min_displayed_fps"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label} must be finite and positive")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.sample_interval_seconds > self.duration_seconds:
            raise ValueError("sample_interval_seconds cannot exceed duration_seconds")
        if not 1 <= self.target_fps <= 200:
            raise ValueError("target_fps must be from 1 through 200")
        if not isinstance(self.expected_plugin, str) or not self.expected_plugin:
            raise ValueError("expected_plugin must be non-empty")

    @property
    def base_url(self) -> str:
        target = self.target.rstrip("/")
        if target.startswith(("http://", "https://")):
            return target
        return f"http://{target}:5000"


class HTTPAPI:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def get(self, path: str, *, timeout: float) -> Any:
        try:
            with urlopen(
                Request(
                    f"{self.base_url}{path}",
                    headers={"Accept": "application/json"},
                ),
                timeout=timeout,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WallSoakError(f"GET {path} failed: {exc}") from exc


def _identity_digest(status: Mapping[str, Any]) -> str | None:
    direct = status.get("current_identity_digest")
    if isinstance(direct, str):
        return direct
    active = _mapping(status.get("active_identity"))
    candidate = active.get("current_identity", active.get("current_identity_digest"))
    return candidate if isinstance(candidate, str) else None


def _activation_identity(
    activation: Mapping[str, Any], key: str
) -> Mapping[str, Any]:
    return _mapping(activation.get(key))


def _activation_scene_digest(activation: Mapping[str, Any], key: str) -> str | None:
    identity = _activation_identity(activation, key)
    scene = _mapping(identity.get("scene_identity"))
    digest = scene.get("digest")
    return digest if isinstance(digest, str) else None


def _topology_failures(status: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    led_info = _mapping(status.get("led_info"))
    for key, expected in EXPECTED_GEOMETRY.items():
        if led_info.get(key) != expected:
            failures.append(
                f"installed geometry {key} is {led_info.get(key)!r}; expected {expected}"
            )
    driver = _mapping(status.get("driver_stats"))
    aggregate = _mapping(driver.get("aggregate"))
    if aggregate.get("transport_envelope_devices") != len(EXPECTED_TOPOLOGY):
        failures.append("aligned transport is not enabled on exactly 5 receivers")
    if (
        aggregate.get("fec_transport_requested_devices") != 1
        or aggregate.get("fec_transport_enabled_devices") != 1
    ):
        failures.append("FEC transport is not requested and enabled on exactly one receiver")
    if (
        aggregate.get("fec_isolated_full_frame_dispatch") is not True
        or aggregate.get("full_frame_dispatch_phases")
        != [[[0, 1], [2]], [[3, 4]]]
    ):
        failures.append("FEC full-frame dispatch is not isolated at receiver 3")
    device_map = _sequence(aggregate.get("device_map"))
    if len(device_map) != len(EXPECTED_TOPOLOGY):
        failures.append(
            f"driver topology has {len(device_map)} devices; expected 5"
        )
    observed_ids: set[int] = set()
    topology_fields = tuple(next(iter(EXPECTED_TOPOLOGY_BY_ID.values())))
    for index, raw in enumerate(device_map):
        item = _mapping(raw)
        logical_id = _integer(item.get("logical_device"))
        expected = EXPECTED_TOPOLOGY_BY_ID.get(logical_id)
        if expected is None or logical_id in observed_ids:
            failures.append(
                f"driver topology entry {index} has invalid logical identity {logical_id!r}"
            )
            continue
        observed_ids.add(logical_id)
        for field in topology_fields:
            if item.get(field) != expected[field]:
                failures.append(
                    f"receiver {logical_id} topology {field} is {item.get(field)!r}; "
                    f"expected {expected[field]!r}"
                )
    devices = _sequence(driver.get("devices"))
    if len(devices) != len(EXPECTED_TOPOLOGY):
        failures.append(f"receiver status has {len(devices)} devices; expected 5")
    seen: set[int] = set()
    for index, raw in enumerate(devices):
        item = _mapping(raw)
        logical_id = _integer(item.get("receiver_logical_device"))
        expected = EXPECTED_TOPOLOGY_BY_ID.get(logical_id)
        if expected is None or logical_id in seen:
            failures.append(
                f"receiver status entry {index} has invalid logical identity {logical_id!r}"
            )
            continue
        seen.add(logical_id)
        if item.get("receiver_status_seen") is not True:
            failures.append(f"receiver {logical_id} has no readable status")
        latest_version = _integer(item.get("receiver_status_version"))
        max_version_seen = _integer(item.get("receiver_status_max_version_seen"))
        required_observed_version = 7
        if (
            latest_version is None
            or max_version_seen is None
            or latest_version < 3
            or latest_version > max_version_seen
            or max_version_seen < required_observed_version
        ):
            failures.append(
                f"receiver {logical_id} status observation is insufficient: "
                f"latest=v{latest_version!r}, max_seen=v{max_version_seen!r}, "
                f"required observed>=v{required_observed_version}"
            )
        capabilities = _integer(item.get("receiver_capabilities"))
        if (
            capabilities is None
            or capabilities & REQUIRED_RECEIVER_CAPABILITIES
            != REQUIRED_RECEIVER_CAPABILITIES
        ):
            failures.append(
                f"receiver {logical_id} lacks required aligned transport capabilities"
            )
        if item.get("transport_envelope_enabled") is not True:
            failures.append(
                f"receiver {logical_id} host aligned transport is not enabled"
            )
        expected_fec = logical_id == 3
        if (
            item.get("fec_transport_requested") is not expected_fec
            or item.get("fec_transport_enabled") is not expected_fec
        ):
            failures.append(
                f"receiver {logical_id} FEC selection differs from receiver-3 policy"
            )
        if (
            "transport_envelope_negotiation_candidate" not in item
            or item.get("transport_envelope_negotiation_candidate") is not None
            or type(item.get("transport_envelope_negotiation_streak")) is not int
            or item.get("transport_envelope_negotiation_streak") != 0
            or type(item.get("transport_envelope_negotiation_required")) is not int
            or item.get("transport_envelope_negotiation_required") != 3
        ):
            failures.append(
                f"receiver {logical_id} aligned transport negotiation is not settled"
            )
        if (
            "fec_transport_negotiation_candidate" not in item
            or item.get("fec_transport_negotiation_candidate") is not None
            or type(item.get("fec_transport_negotiation_streak")) is not int
            or item.get("fec_transport_negotiation_streak") != 0
            or type(item.get("fec_transport_negotiation_required")) is not int
            or item.get("fec_transport_negotiation_required") != 3
        ):
            failures.append(
                f"receiver {logical_id} FEC transport negotiation is not settled"
            )
        checks = {
            "receiver_active_strips": expected["local_strip_count"],
            "receiver_global_strip_offset": expected["global_strip_offset"],
            "receiver_lane_mask": expected["physical_output_lane_mask"],
            "receiver_leds_per_strip": EXPECTED_GEOMETRY["leds_per_strip"],
        }
        for field, value in checks.items():
            if item.get(field) != value:
                failures.append(
                    f"receiver {logical_id} {field} is {item.get(field)!r}; expected {value!r}"
                )
        failures.extend(_sampling_snapshot_failures(
            item,
            f"receiver {logical_id}",
            minimum_buffer_size=EXPECTED_FULL_FRAME_WIRE_BYTES[logical_id],
        ))
        fec_received = _integer(item.get("receiver_fec_packets_received"))
        fec_accepted = _integer(item.get("receiver_fec_packets_accepted"))
        fec_uncorrectable = _integer(item.get("receiver_fec_uncorrectable_packets"))
        fec_semantic_crc = _integer(item.get("receiver_fec_semantic_crc_errors"))
        fec_framing = _integer(item.get("receiver_fec_framing_errors"))
        if None in (
            fec_received, fec_accepted, fec_uncorrectable,
            fec_semantic_crc, fec_framing,
        ) or fec_received != (
            fec_accepted + fec_uncorrectable + fec_semantic_crc + fec_framing
        ):
            failures.append(f"receiver {logical_id} FEC outcome accounting is inconsistent")
        fec_frames = _integer(item.get("fec_frames_sent"))
        fec_codewords = _integer(item.get("fec_codewords_sent"))
        fec_parity = _integer(item.get("fec_parity_bytes_sent"))
        fec_padding = _integer(item.get("fec_data_padding_bytes_sent"))
        expected_codewords = 68 if expected_fec else 0
        if (
            None in (fec_frames, fec_codewords, fec_parity, fec_padding)
            or (not expected_fec and fec_frames != 0)
            or fec_codewords != expected_codewords * fec_frames
            or fec_parity != 10 * expected_codewords * fec_frames
            or fec_padding != 76 * fec_frames
        ):
            failures.append(f"receiver {logical_id} host FEC accounting is inconsistent")
        corrected_packets = _integer(item.get("receiver_fec_corrected_packets"))
        corrected_codewords = _integer(item.get("receiver_fec_corrected_codewords"))
        if (
            None in (corrected_packets, corrected_codewords, fec_accepted)
            or corrected_packets > fec_accepted
            or corrected_codewords < corrected_packets
            or corrected_codewords > 68 * corrected_packets
        ):
            failures.append(f"receiver {logical_id} corrected FEC accounting is inconsistent")
        last_decode = _integer(item.get("receiver_fec_last_decode_us"))
        max_decode = _integer(item.get("receiver_fec_max_decode_us"))
        if (
            last_decode is None or max_decode is None or last_decode > max_decode
            or (not expected_fec and (last_decode != 0 or max_decode != 0))
        ):
            failures.append(f"receiver {logical_id} FEC decode timing is inconsistent")
    transport_fields = (
        "spi_transfers", "bytes_sent", "semantic_bytes_sent",
        "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
        "crc_bytes_sent", "full_frame_transfers",
        "full_frame_semantic_bytes_sent", "full_frame_wire_bytes_sent",
        "full_frame_status_transfers", "full_frame_status_samples",
        "full_frame_status_sample_misses", "full_frame_write_only_transfers",
        *FEC_COUNTERS,
    )
    for field in transport_fields:
        total = 0
        available = True
        for raw in devices:
            value = _integer(_mapping(raw).get(field))
            if value is None:
                available = False
                break
            total += value
        aggregate_value = _integer(aggregate.get(field))
        if not available or aggregate_value is None:
            failures.append(f"aligned transport counter {field} is unavailable")
        elif aggregate_value != total:
            failures.append(
                f"aggregate {field} drifted from per-receiver total"
            )
    failures.extend(_sampling_snapshot_failures(
        aggregate, "aggregate", minimum_buffer_size=4088
    ))
    if devices:
        gauge_expectations = {
            "receiver_status_version": min(
                (_integer(_mapping(raw).get("receiver_status_version")) or 0)
                for raw in devices
            ),
            "receiver_status_max_version_seen": min(
                (
                    _integer(
                        _mapping(raw).get("receiver_status_max_version_seen")
                    )
                    or 0
                )
                for raw in devices
            ),
            "full_frame_frames_since_status_sample": max(
                (_integer(_mapping(raw).get("full_frame_frames_since_status_sample")) or 0)
                for raw in devices
            ),
            "full_frame_max_status_sample_gap": max(
                (_integer(_mapping(raw).get("full_frame_max_status_sample_gap")) or 0)
                for raw in devices
            ),
            "spidev_buffer_size": min(
                (_integer(_mapping(raw).get("spidev_buffer_size")) or 0)
                for raw in devices
            ),
            "full_frame_write_only_supported": all(
                _mapping(raw).get("full_frame_write_only_supported") is True
                for raw in devices
            ),
            "receiver_fec_last_decode_us": max(
                (_integer(_mapping(raw).get("receiver_fec_last_decode_us")) or 0)
                for raw in devices
            ),
            "receiver_fec_max_decode_us": max(
                (_integer(_mapping(raw).get("receiver_fec_max_decode_us")) or 0)
                for raw in devices
            ),
        }
        for field, expected in gauge_expectations.items():
            if aggregate.get(field) != expected:
                failures.append(
                    f"aggregate {field} drifted from per-receiver value"
                )
    return failures


def evaluate_sample(
    status: Mapping[str, Any],
    activation: Mapping[str, Any],
    config: WallSoakConfig,
    *,
    expected_session_id: str | None = None,
    expected_state_revision: int | None = None,
    expected_identity_digest: str | None = None,
    expected_activation_identity: Mapping[str, Any] | None = None,
) -> list[str]:
    failures = _topology_failures(status)
    releases = (status.get("release_id"), status.get("controller_release_id"))
    if releases != (config.expected_release_id, config.expected_release_id):
        failures.append(
            "web/controller release identity differs from the accepted release: "
            f"{releases!r}"
        )
    if status.get("release_consistent") is not True:
        failures.append("controller/web release consistency is not proven")
    if status.get("is_running") is not True or status.get("mode") != "scene":
        failures.append("the accepted complete scene is not running")
    if status.get("target_fps") != config.target_fps:
        failures.append(
            f"target FPS is {status.get('target_fps')!r}; expected {config.target_fps}"
        )
    scene = _mapping(status.get("scene_state"))
    if not scene:
        failures.append("canonical active scene is unavailable")
    else:
        observed = canonical_scene_digest(scene)
        if observed != config.expected_scene_digest:
            failures.append(
                "active scene digest drifted: "
                f"expected {config.expected_scene_digest}, observed {observed}"
            )
        background = _mapping(scene.get("background"))
        if (
            background.get("provider") != "python"
            or background.get("plugin_id") != config.expected_plugin
        ):
            failures.append(
                "active background is not the accepted Python "
                f"{config.expected_plugin!r} scene"
            )
    session_id = status.get("controller_session_id")
    state_revision = _integer(status.get("controller_state_revision"))
    identity_digest = _identity_digest(status)
    if not isinstance(session_id, str) or not session_id:
        failures.append("controller session identity is unavailable")
    if state_revision is None or state_revision < 0:
        failures.append("controller state revision is unavailable")
    if DIGEST_PATTERN.fullmatch(identity_digest or "") is None:
        failures.append("controller active identity digest is unavailable")
    if expected_session_id is not None and session_id != expected_session_id:
        failures.append("controller session identity changed during soak")
    if expected_state_revision is not None and state_revision != expected_state_revision:
        failures.append("controller state revision changed during soak")
    if expected_identity_digest is not None and identity_digest != expected_identity_digest:
        failures.append("controller active identity changed during soak")

    if activation.get("activation_id") != config.activation_id:
        failures.append("activation receipt correlation changed")
    if activation.get("basis_digest") != config.expected_basis_digest:
        failures.append("activation basis digest differs from the accepted Check basis")
    if activation.get("phase") != "active":
        failures.append(
            f"guarded activation phase is {activation.get('phase')!r}; expected 'active'"
        )
    requested_identity = _activation_identity(activation, "requested_identity")
    normalized_identity = _activation_identity(activation, "normalized_identity")
    observed_identity = _activation_identity(activation, "observed_identity")
    receipt_identities = (
        requested_identity,
        normalized_identity,
        observed_identity,
    )
    if (
        not requested_identity
        or requested_identity != normalized_identity
        or requested_identity != observed_identity
    ):
        failures.append(
            "activation requested/normalized/observed identities are not unanimous"
        )
    if any(
        _mapping(identity.get("scene_identity")).get("digest")
        != config.expected_scene_digest
        for identity in receipt_identities
    ):
        failures.append("activation receipt scene identity differs from the accepted scene")
    active_identity = _mapping(status.get("active_identity"))
    if not active_identity:
        failures.append("controller full active identity is unavailable")
    elif active_identity != observed_identity:
        failures.append("activation receipt/controller full active identity mismatch")
    if (
        expected_activation_identity is not None
        and requested_identity != expected_activation_identity
    ):
        failures.append("activation full identity changed during soak")
    telemetry = _mapping(activation.get("telemetry"))
    if telemetry.get("complete") is not True or telemetry.get("fresh") is not True:
        failures.append("activation telemetry is incomplete or stale")
    if not isinstance(telemetry.get("observed_at"), (int, float)):
        failures.append("activation telemetry has no observation timestamp")
    controller = _mapping(activation.get("controller"))
    if controller.get("session_id") != session_id:
        failures.append("activation receipt/controller session identity mismatch")
    if controller.get("state_revision_after") != state_revision:
        failures.append("activation receipt/controller state revision mismatch")
    return failures


def normalize_sample(
    status: Mapping[str, Any],
    activation: Mapping[str, Any],
    *,
    elapsed_seconds: float,
    sampled_at: float,
) -> dict[str, Any]:
    driver = _mapping(status.get("driver_stats"))
    aggregate = _mapping(driver.get("aggregate"))
    devices = []
    for raw in _sequence(driver.get("devices")):
        item = _mapping(raw)
        devices.append({key: item.get(key) for key in DEVICE_SAMPLE_FIELDS})
    return {
        "sampled_at": _utc_timestamp(sampled_at),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "release_id": status.get("release_id"),
        "controller_release_id": status.get("controller_release_id"),
        "release_consistent": status.get("release_consistent"),
        "controller_session_id": status.get("controller_session_id"),
        "controller_state_revision": status.get("controller_state_revision"),
        "current_identity_digest": _identity_digest(status),
        "status_written_at": status.get("written_at", status.get("updated_at")),
        "actual_fps": status.get("actual_fps"),
        "pipeline_fps": status.get("pipeline_fps"),
        "target_fps": status.get("target_fps"),
        "scene_digest": canonical_scene_digest(_mapping(status.get("scene_state"))),
        "aggregate": {
            key: aggregate.get(key)
            for key in AGGREGATE_CONTINUITY_COUNTERS
        } | {
            "transport_envelope_devices": aggregate.get(
                "transport_envelope_devices"
            ),
            "fec_transport_requested_devices": aggregate.get(
                "fec_transport_requested_devices"
            ),
            "fec_transport_enabled_devices": aggregate.get(
                "fec_transport_enabled_devices"
            ),
            "receiver_status_version": aggregate.get(
                "receiver_status_version"
            ),
            "receiver_status_max_version_seen": aggregate.get(
                "receiver_status_max_version_seen"
            ),
            "full_frame_frames_since_status_sample": aggregate.get(
                "full_frame_frames_since_status_sample"
            ),
            "full_frame_max_status_sample_gap": aggregate.get(
                "full_frame_max_status_sample_gap"
            ),
            "spidev_buffer_size": aggregate.get("spidev_buffer_size"),
            "full_frame_write_only_supported": aggregate.get(
                "full_frame_write_only_supported"
            ),
            "receiver_fec_last_decode_us": aggregate.get(
                "receiver_fec_last_decode_us"
            ),
            "receiver_fec_max_decode_us": aggregate.get(
                "receiver_fec_max_decode_us"
            ),
        },
        "devices": devices,
        "activation": {
            "activation_id": activation.get("activation_id"),
            "phase": activation.get("phase"),
            "basis_digest": activation.get("basis_digest"),
            "requested_identity": dict(
                _activation_identity(activation, "requested_identity")
            ),
            "normalized_identity": dict(
                _activation_identity(activation, "normalized_identity")
            ),
            "observed_identity": dict(
                _activation_identity(activation, "observed_identity")
            ),
            "telemetry": dict(_mapping(activation.get("telemetry"))),
            "controller": dict(_mapping(activation.get("controller"))),
            "rollback": dict(_mapping(activation.get("rollback"))),
        },
    }


def _transport_delta_failures(
    before: Mapping[str, Any], after: Mapping[str, Any], label: str,
    *, expected_full_semantic_bytes: int | None = None,
    expected_full_wire_bytes: int | None = None,
) -> list[str]:
    fields = (
        "spi_transfers", "bytes_sent", "semantic_bytes_sent",
        "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
        "crc_bytes_sent", "full_frame_transfers",
        "full_frame_semantic_bytes_sent", "full_frame_wire_bytes_sent",
    )
    deltas = {field: _counter_delta(before, after, field) for field in fields}
    failures = []
    for field, delta in deltas.items():
        if delta is None:
            failures.append(f"{label} counter {field} is unavailable")
        elif delta <= 0:
            failures.append(f"{label} counter {field} did not advance")
    if failures:
        return failures
    transfers = deltas["spi_transfers"] or 0
    fec_frames = _counter_delta(before, after, "fec_frames_sent")
    fec_parity = _counter_delta(before, after, "fec_parity_bytes_sent")
    if fec_frames is None or fec_parity is None or fec_frames < 0 or fec_parity < 0:
        failures.append(f"{label} FEC wire accounting is unavailable")
        return failures
    if deltas["transport_envelope_bytes_sent"] != 4 * transfers + 12 * fec_frames:
        failures.append(f"{label} envelope accounting is inconsistent")
    if deltas["crc_bytes_sent"] != 2 * transfers:
        failures.append(f"{label} CRC accounting is inconsistent")
    expected_wire = (
        (deltas["semantic_bytes_sent"] or 0)
        + (deltas["transport_envelope_bytes_sent"] or 0)
        + (deltas["transport_padding_bytes_sent"] or 0)
        + (deltas["crc_bytes_sent"] or 0)
        + fec_parity
    )
    if deltas["bytes_sent"] != expected_wire:
        failures.append(f"{label} wire-byte accounting is inconsistent")
    if expected_full_semantic_bytes is not None:
        full_transfers = deltas["full_frame_transfers"] or 0
        if expected_full_wire_bytes is None:
            failures.append(f"{label} expected full-frame wire size is unavailable")
            return failures
        if (
            deltas["full_frame_semantic_bytes_sent"]
            != expected_full_semantic_bytes * full_transfers
            or deltas["full_frame_wire_bytes_sent"]
            != expected_full_wire_bytes * full_transfers
        ):
            failures.append(
                f"{label} full-frame SET_ALL accounting is inconsistent"
            )
    return failures


def _fec_delta_failures(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    label: str,
    *,
    logical_device: int | None,
    expected_frames: int | None,
) -> list[str]:
    deltas = {
        field: _counter_delta(before, after, field) for field in FEC_COUNTERS
    }
    if any(value is None or value < 0 for value in deltas.values()):
        return [f"{label} FEC counters are unavailable or reset"]
    failures: list[str] = []
    selected = logical_device is None or logical_device == 3
    if not selected:
        if any(deltas.values()):
            failures.append(f"{label} emitted or received unconfigured FEC traffic")
        return failures
    frames = deltas["fec_frames_sent"]
    if expected_frames is not None and frames != expected_frames:
        failures.append(f"{label} FEC sent frames do not match full-frame transfers")
    if (
        deltas["fec_codewords_sent"] != 68 * frames
        or deltas["fec_parity_bytes_sent"] != 680 * frames
        or deltas["fec_data_padding_bytes_sent"] != 76 * frames
    ):
        failures.append(f"{label} host FEC accounting is inconsistent")
    received = deltas["receiver_fec_packets_received"]
    accepted = deltas["receiver_fec_packets_accepted"]
    terminal = (
        deltas["receiver_fec_uncorrectable_packets"]
        + deltas["receiver_fec_semantic_crc_errors"]
        + deltas["receiver_fec_framing_errors"]
    )
    if received != accepted + terminal:
        failures.append(f"{label} receiver FEC outcomes do not partition received packets")
    if received <= 0 or received != accepted or terminal != 0:
        failures.append(
            f"{label} receiver FEC traffic was not accepted without terminal faults"
        )
    corrected_packets = deltas["receiver_fec_corrected_packets"]
    corrected_codewords = deltas["receiver_fec_corrected_codewords"]
    if not (
        0 <= corrected_packets <= accepted
        and corrected_packets <= corrected_codewords <= 68 * corrected_packets
    ):
        failures.append(f"{label} corrected FEC accounting is inconsistent")
    return failures


def evaluate_transition(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[str]:
    """Reject stale publication, resets, and new faults between two samples."""

    failures: list[str] = []
    before_written = _finite(before.get("status_written_at"))
    after_written = _finite(after.get("status_written_at"))
    if before_written is None or after_written is None:
        failures.append("controller status publication timestamp is unavailable")
    elif after_written <= before_written:
        failures.append("controller status publication did not advance")

    before_aggregate = _mapping(before.get("aggregate"))
    after_aggregate = _mapping(after.get("aggregate"))
    for field in AGGREGATE_CONTINUITY_COUNTERS:
        delta = _counter_delta(before_aggregate, after_aggregate, field)
        if delta is None:
            failures.append(f"aggregate counter {field} is unavailable")
        elif delta < 0:
            failures.append(f"aggregate counter {field} reset by {delta}")
    for field in ERROR_COUNTERS:
        delta = _counter_delta(before_aggregate, after_aggregate, field)
        if delta is not None and delta > 0:
            failures.append(f"aggregate error counter {field} increased by {delta}")
    failures.extend(_transport_delta_failures(
        before_aggregate, after_aggregate, "aggregate aligned transport"
    ))
    failures.extend(_fec_delta_failures(
        before_aggregate,
        after_aggregate,
        "aggregate",
        logical_device=None,
        expected_frames=None,
    ))
    failures.extend(_sampling_delta_failures(
        before_aggregate, after_aggregate, "aggregate aligned transport"
    ))

    before_devices = {
        _integer(_mapping(item).get("receiver_logical_device")): _mapping(item)
        for item in _sequence(before.get("devices"))
    }
    after_devices = {
        _integer(_mapping(item).get("receiver_logical_device")): _mapping(item)
        for item in _sequence(after.get("devices"))
    }
    for receiver_id in range(5):
        first = before_devices.get(receiver_id, {})
        last = after_devices.get(receiver_id, {})
        for field in CONTINUITY_COUNTERS:
            delta = _counter_delta(first, last, field)
            if delta is not None and delta < 0:
                failures.append(
                    f"receiver {receiver_id} counter {field} reset by {delta}"
                )
        for field in ERROR_COUNTERS:
            delta = _counter_delta(first, last, field)
            if delta is not None and delta > 0:
                failures.append(
                    f"receiver {receiver_id} error counter {field} increased by {delta}"
                )
        failures.extend(_transport_delta_failures(
            first,
            last,
            f"receiver {receiver_id} aligned transport",
            expected_full_semantic_bytes=(
                1
                + EXPECTED_TOPOLOGY_BY_ID[receiver_id]["local_strip_count"]
                * EXPECTED_GEOMETRY["leds_per_strip"] * 3
            ),
            expected_full_wire_bytes=EXPECTED_FULL_FRAME_WIRE_BYTES[receiver_id],
        ))
        full_frames = _counter_delta(first, last, "full_frame_transfers")
        failures.extend(_fec_delta_failures(
            first,
            last,
            f"receiver {receiver_id}",
            logical_device=receiver_id,
            expected_frames=full_frames,
        ))
        failures.extend(_sampling_delta_failures(
            first, last, f"receiver {receiver_id} aligned transport"
        ))
    return failures


def evaluate_series(samples: Sequence[Mapping[str, Any]], config: WallSoakConfig) -> dict[str, Any]:
    failures: list[str] = []
    if len(samples) < 2:
        return {"passed": False, "failures": ["insufficient soak samples"]}
    first = _mapping(samples[0])
    last = _mapping(samples[-1])
    observed_seconds = _finite(last.get("elapsed_seconds"))
    if config.duration_seconds < MIN_RELEASE_DURATION_SECONDS:
        failures.append(
            f"requested soak {config.duration_seconds:g}s is below the 1800s release minimum"
        )
    if observed_seconds is None or observed_seconds < MIN_RELEASE_DURATION_SECONDS:
        failures.append(
            f"observed soak {observed_seconds!r}s is below the 1800s release minimum"
        )
    aggregate_deltas: dict[str, int | None] = {}
    first_aggregate = _mapping(first.get("aggregate"))
    last_aggregate = _mapping(last.get("aggregate"))
    for field in AGGREGATE_CONTINUITY_COUNTERS:
        delta = _counter_delta(first_aggregate, last_aggregate, field)
        aggregate_deltas[field] = delta
        if delta is None:
            failures.append(f"aggregate counter {field} is unavailable")
        elif delta < 0:
            failures.append(f"aggregate counter {field} reset by {delta}")
    for field in ERROR_COUNTERS:
        delta = aggregate_deltas.get(field)
        if delta is None:
            failures.append(f"aggregate error counter {field} is unavailable")
        elif delta != 0:
            failures.append(f"aggregate error counter {field} increased by {delta}")
    failures.extend(_transport_delta_failures(
        first_aggregate, last_aggregate, "aggregate aligned transport"
    ))
    failures.extend(_fec_delta_failures(
        first_aggregate,
        last_aggregate,
        "aggregate",
        logical_device=None,
        expected_frames=None,
    ))
    failures.extend(_sampling_delta_failures(
        first_aggregate, last_aggregate, "aggregate aligned transport"
    ))

    device_results: dict[str, Any] = {}
    first_devices = {
        _integer(_mapping(item).get("receiver_logical_device")): _mapping(item)
        for item in _sequence(first.get("devices"))
    }
    last_devices = {
        _integer(_mapping(item).get("receiver_logical_device")): _mapping(item)
        for item in _sequence(last.get("devices"))
    }
    elapsed = observed_seconds or 0.0
    for receiver_id in range(5):
        before = first_devices.get(receiver_id, {})
        after = last_devices.get(receiver_id, {})
        deltas: dict[str, int | None] = {}
        receiver_failures: list[str] = []
        for field in CONTINUITY_COUNTERS:
            delta = _counter_delta(before, after, field)
            deltas[field] = delta
            if delta is None:
                receiver_failures.append(f"counter {field} is unavailable")
            elif delta < 0:
                receiver_failures.append(f"counter {field} reset by {delta}")
        for field in ERROR_COUNTERS:
            delta = deltas[field]
            if delta is not None and delta != 0:
                receiver_failures.append(f"error counter {field} increased by {delta}")
        receiver_failures.extend(_transport_delta_failures(
            before,
            after,
            f"receiver {receiver_id} aligned transport",
            expected_full_semantic_bytes=(
                1
                + EXPECTED_TOPOLOGY_BY_ID[receiver_id]["local_strip_count"]
                * EXPECTED_GEOMETRY["leds_per_strip"] * 3
            ),
            expected_full_wire_bytes=EXPECTED_FULL_FRAME_WIRE_BYTES[receiver_id],
        ))
        receiver_failures.extend(_fec_delta_failures(
            before,
            after,
            f"receiver {receiver_id}",
            logical_device=receiver_id,
            expected_frames=deltas.get("full_frame_transfers"),
        ))
        receiver_failures.extend(_sampling_delta_failures(
            before, after, f"receiver {receiver_id} aligned transport"
        ))
        accepted = deltas.get("receiver_frames_accepted") or 0
        displayed = deltas.get("receiver_frames_displayed") or 0
        superseded = deltas.get("receiver_frames_superseded") or 0
        displayed_fps = displayed / elapsed if elapsed > 0 else 0.0
        full_frame_transfers = deltas.get("full_frame_transfers") or 0
        full_frame_fps = full_frame_transfers / elapsed if elapsed > 0 else 0.0
        if accepted <= 0 or displayed <= 0:
            receiver_failures.append("frame counters did not advance")
        if displayed_fps < config.min_displayed_fps:
            receiver_failures.append(
                f"displayed rate {displayed_fps:.3f} FPS is below "
                f"{config.min_displayed_fps:g} FPS"
            )
        if full_frame_fps < config.min_displayed_fps:
            receiver_failures.append(
                f"full-frame SET_ALL rate {full_frame_fps:.3f} FPS is below "
                f"{config.min_displayed_fps:g} FPS"
            )
        if displayed + superseded < max(0, accepted - 3):
            receiver_failures.append(
                f"accounting incomplete: {accepted} accepted, {displayed} displayed, "
                f"{superseded} superseded"
            )
        outstanding = (
            (_integer(after.get("receiver_frames_accepted")) or 0)
            - (_integer(after.get("receiver_frames_displayed")) or 0)
            - (_integer(after.get("receiver_frames_superseded")) or 0)
        )
        if not 0 <= outstanding <= 3:
            receiver_failures.append(
                f"mailbox outstanding count {outstanding} is outside 0..3"
            )
        device_results[str(receiver_id)] = {
            "passed": not receiver_failures,
            "failures": receiver_failures,
            "deltas": deltas,
            "displayed_fps": displayed_fps,
            "full_frame_fps": full_frame_fps,
            "mailbox_outstanding": outstanding,
        }
        failures.extend(
            f"receiver {receiver_id}: {failure}" for failure in receiver_failures
        )
    return {
        "passed": not failures,
        "failures": failures,
        "requested_seconds": config.duration_seconds,
        "observed_seconds": observed_seconds,
        "sample_count": len(samples),
        "aggregate_deltas": aggregate_deltas,
        "receivers": device_results,
    }


def run_soak(
    config: WallSoakConfig,
    api: Any,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    started_at = wall_time()
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "gate": "WALL-02",
        "claim": "release-soak",
        "observation_only": True,
        "target": config.target,
        "activation_id": config.activation_id,
        "expected_scene_digest": config.expected_scene_digest,
        "expected_release_id": config.expected_release_id,
        "expected_basis_digest": config.expected_basis_digest,
        "target_fps": config.target_fps,
        "min_displayed_fps": config.min_displayed_fps,
        "expected_plugin": config.expected_plugin,
        "expected_topology": list(EXPECTED_TOPOLOGY),
        "requested_duration_seconds": config.duration_seconds,
        "sample_interval_seconds": config.sample_interval_seconds,
        "started_at": _utc_timestamp(started_at),
        "samples": [],
        "failures": [],
    }
    interrupted = False
    try:
        started = monotonic()
        deadline = started + config.duration_seconds
        expected_session: str | None = None
        expected_revision: int | None = None
        expected_identity: str | None = None
        expected_activation_identity: Mapping[str, Any] | None = None
        while True:
            elapsed = max(0.0, monotonic() - started)
            status = api.get("/api/status", timeout=config.timeout_seconds)
            activation = api.get(
                f"/api/v1/scene/activations/{config.activation_id}",
                timeout=config.timeout_seconds,
            )
            if not isinstance(status, Mapping) or not isinstance(activation, Mapping):
                raise WallSoakError("status or activation response is malformed")
            failures = evaluate_sample(
                status,
                activation,
                config,
                expected_session_id=expected_session,
                expected_state_revision=expected_revision,
                expected_identity_digest=expected_identity,
                expected_activation_identity=expected_activation_identity,
            )
            if failures:
                raise WallSoakError("; ".join(failures))
            if expected_session is None:
                expected_session = str(status["controller_session_id"])
                expected_revision = int(status["controller_state_revision"])
                expected_identity = str(_identity_digest(status))
                expected_activation_identity = dict(
                    _activation_identity(activation, "requested_identity")
                )
                report["controller_session_id"] = expected_session
                report["controller_state_revision"] = expected_revision
                report["current_identity_digest"] = expected_identity
                report["activation_basis_digest"] = config.expected_basis_digest
                report["activation_identity"] = dict(expected_activation_identity)
            sample = normalize_sample(
                status,
                activation,
                elapsed_seconds=elapsed,
                sampled_at=wall_time(),
            )
            if report["samples"]:
                transition_failures = evaluate_transition(
                    _mapping(report["samples"][-1]), sample
                )
                if transition_failures:
                    raise WallSoakError("; ".join(transition_failures))
            report["samples"].append(sample)
            remaining = deadline - monotonic()
            if remaining <= 1e-6:
                break
            sleep(min(config.sample_interval_seconds, remaining))
        report["evaluation"] = evaluate_series(report["samples"], config)
    except KeyboardInterrupt:
        interrupted = True
        report["failures"].append("soak interrupted by operator")
    except Exception as exc:
        report["failures"].append(str(exc))
        if len(report["samples"]) >= 2:
            report["evaluation"] = evaluate_series(report["samples"], config)
    report["interrupted"] = interrupted
    report["completed_at"] = _utc_timestamp(wall_time())
    report["passed"] = bool(
        not report["failures"]
        and _mapping(report.get("evaluation")).get("passed") is True
    )
    return report


def _reject_symlink_components(path: Path) -> None:
    lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        candidate = current / part
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            current = candidate
            continue
        if stat.S_ISLNK(metadata.st_mode):
            # macOS exposes stable filesystem-root aliases such as /var ->
            # /private/var and /tmp -> /private/tmp.  Resolve only that first
            # root-owned hop; caller-controlled links anywhere below it remain
            # forbidden.
            if current == Path(lexical.anchor):
                current = candidate.resolve(strict=True)
                continue
            raise WallSoakError(f"evidence path contains a symbolic link: {path}")
        current = candidate


def write_report(report: Mapping[str, Any], path: Path) -> tuple[Path, str]:
    """Atomically create one append-only report and return its SHA-256."""

    destination = Path(os.path.abspath(os.fspath(path.expanduser())))
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise WallSoakError(f"refusing to overwrite retained evidence: {destination}")
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    temporary.unlink()
    return destination, hashlib.sha256(encoded).hexdigest()


def _default_output(evidence_dir: Path, report: Mapping[str, Any]) -> Path:
    timestamp = str(report["started_at"]).replace(":", "").replace("+00:00", "Z")
    timestamp = timestamp.replace("-", "").replace(".", "")
    return evidence_dir / f"{timestamp}-wall-02-soak.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("activation_id")
    parser.add_argument("--expected-scene-digest", required=True)
    parser.add_argument("--expected-release-id", required=True)
    parser.add_argument("--expected-basis-digest", required=True)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--sample-interval", type=float, default=DEFAULT_SAMPLE_INTERVAL_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--target-fps", type=int, default=DEFAULT_TARGET_FPS)
    parser.add_argument("--min-displayed-fps", type=float, default=DEFAULT_MIN_DISPLAYED_FPS)
    parser.add_argument("--expected-plugin", default="rainbow")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("run_state/physical-acceptance"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = WallSoakConfig(
            activation_id=args.activation_id,
            expected_scene_digest=args.expected_scene_digest,
            expected_release_id=args.expected_release_id,
            expected_basis_digest=args.expected_basis_digest,
            target=args.target,
            duration_seconds=args.duration,
            sample_interval_seconds=args.sample_interval,
            timeout_seconds=args.timeout,
            target_fps=args.target_fps,
            min_displayed_fps=args.min_displayed_fps,
            expected_plugin=args.expected_plugin,
        )
        report = run_soak(config, HTTPAPI(config.base_url))
        output = args.output or _default_output(args.evidence_dir, report)
        resolved_output, digest = write_report(report, output)
        print(json.dumps({
            **report,
            "evidence_path": os.fspath(resolved_output),
            "evidence_sha256": digest,
        }, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1
    except (ValueError, WallSoakError, OSError) as exc:
        print(json.dumps({
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "passed": False,
            "failures": [str(exc)],
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

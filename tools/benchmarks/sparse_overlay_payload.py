#!/usr/bin/env python3
"""Deterministic Phase 3B0 sparse-overlay SPI payload accounting.

This is a byte-accounting model, not a throughput benchmark. It renders the
real installed-geometry ``clock_overlay`` at fixed wall-clock seconds and then
accounts for the exact frozen command packets and the driver's two-deep queued
acknowledgement protocol. SPI is full duplex, so response bytes do not consume
additional clocks; both clocked bytes and bidirectional endpoint bytes are
reported to make that distinction explicit.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Iterable, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.plugins.clock_overlay import ClockOverlayAnimation  # noqa: E402
from animation.core.installation_profile_topology import (  # noqa: E402
    RECEIVER_STRIP_COUNTS,
)
from drivers.led_layout import (  # noqa: E402
    DEFAULT_LEDS_PER_STRIP,
    DEFAULT_STRIP_COUNT,
)


WALL_STRIPS = DEFAULT_STRIP_COUNT
RECEIVER_STRIPS = max(RECEIVER_STRIP_COUNTS)
LEDS_PER_STRIP = DEFAULT_LEDS_PER_STRIP
RECEIVER_COUNT = len(RECEIVER_STRIP_COUNTS)
RECEIVER_PIXELS = tuple(
    width * LEDS_PER_STRIP for width in RECEIVER_STRIP_COUNTS
)
RECEIVER_PIXEL_OFFSETS = tuple(
    sum(RECEIVER_PIXELS[:index]) for index in range(RECEIVER_COUNT)
)
LOCAL_PIXELS = RECEIVER_STRIPS * LEDS_PER_STRIP
WALL_PIXELS = WALL_STRIPS * LEDS_PER_STRIP
# Frozen semantic command/status sizes plus the production DMA-safe envelope.
CRC_BYTES = 2
ALIGNED_ENVELOPE_HEADER_BYTES = 4
SPI_DMA_ALIGNMENT_BYTES = 4
CONTROLLER_SESSION_BEGIN_BYTES = 58
OVERLAY_BEGIN_BYTES = 66
OVERLAY_PATCH_HEADER_BYTES = 30
OVERLAY_PATCH_BATCH_HEADER_BYTES = 28
OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES = 4
OVERLAY_COMMIT_BYTES = 50
OVERLAY_RENEW_BYTES = 30
RECEIVER_STATUS_BYTES_V3 = 320
RECEIVER_STATUS_BYTES_V4 = 416
SPI_RESPONSE_QUEUE_DEPTH = 2
MAX_SPI_TRANSFER = 4096
MAX_ALIGNED_SEMANTIC_BYTES = (
    MAX_SPI_TRANSFER - ALIGNED_ENVELOPE_HEADER_BYTES - CRC_BYTES
)
MAX_RGBA_PIXELS_PER_PATCH = (
    MAX_ALIGNED_SEMANTIC_BYTES - OVERLAY_PATCH_HEADER_BYTES
) // 4
MAX_RGBA_PIXELS_PER_BATCH_SPAN = (
    MAX_ALIGNED_SEMANTIC_BYTES
    - OVERLAY_PATCH_BATCH_HEADER_BYTES
    - OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES
) // 4


def aligned_wire_size(semantic_bytes: int) -> int:
    unpadded = ALIGNED_ENVELOPE_HEADER_BYTES + semantic_bytes + CRC_BYTES
    return unpadded + (-unpadded) % SPI_DMA_ALIGNMENT_BYTES


LOCAL_RGB_PACKET_BYTES = aligned_wire_size(1 + LOCAL_PIXELS * 3)
RECEIVER_RGB_PACKET_BYTES = tuple(
    aligned_wire_size(1 + pixels * 3) for pixels in RECEIVER_PIXELS
)
FULL_WALL_RGB_PACKET_BYTES = sum(RECEIVER_RGB_PACKET_BYTES)
# Sparse commands enter a mixed v3/v4 two-deep response queue: two pre-drain
# queries establish the prior sequence, then three post-command queries clock
# past the queued prior-v4 and v3 snapshots to observe the command's v4 result.
ACK_STATUS_TRANSFERS = SPI_RESPONSE_QUEUE_DEPTH * 2 + 1
STATUS_V3_QUERY_TRANSFER_BYTES = aligned_wire_size(RECEIVER_STATUS_BYTES_V3)
STATUS_V4_QUERY_TRANSFER_BYTES = aligned_wire_size(RECEIVER_STATUS_BYTES_V4)


@dataclass
class WireAccount:
    """One-direction transfer accounting plus full-duplex observability."""

    spi_clocked_bytes: int = 0
    command_packet_bytes: int = 0
    status_query_transfer_bytes: int = 0
    meaningful_status_response_bytes: int = 0
    command_count: int = 0
    status_query_count: int = 0
    acknowledgement_count: int = 0
    event_counts: Counter = field(default_factory=Counter)

    def add_status_query(self, *, version: int, purpose: str | None = None) -> None:
        if version == 3:
            transfer_bytes = STATUS_V3_QUERY_TRANSFER_BYTES
            response_bytes = RECEIVER_STATUS_BYTES_V3
        elif version == 4:
            transfer_bytes = STATUS_V4_QUERY_TRANSFER_BYTES
            response_bytes = RECEIVER_STATUS_BYTES_V4
        else:
            raise ValueError("status version must be 3 or 4")
        self.spi_clocked_bytes += transfer_bytes
        self.status_query_transfer_bytes += transfer_bytes
        self.meaningful_status_response_bytes += response_bytes
        self.status_query_count += 1
        self.event_counts[f"status_v{version}_query"] += 1
        if purpose is not None:
            self.event_counts[purpose] += 1

    def add_acknowledged_command(self, name: str, pre_crc_bytes: int) -> None:
        if pre_crc_bytes < 1:
            raise ValueError("command packet must include at least its command byte")
        packet_bytes = aligned_wire_size(pre_crc_bytes)
        self.spi_clocked_bytes += packet_bytes
        self.command_packet_bytes += packet_bytes
        self.command_count += 1
        self.acknowledgement_count += 1
        self.event_counts[name] += 1
        for _ in range(ACK_STATUS_TRANSFERS):
            self.add_status_query(version=4)

    def to_dict(self) -> dict:
        return {
            "spi_clocked_bytes": self.spi_clocked_bytes,
            "mosi_bytes": self.spi_clocked_bytes,
            "miso_bytes": self.spi_clocked_bytes,
            "bidirectional_endpoint_bytes": self.spi_clocked_bytes * 2,
            "command_packet_bytes": self.command_packet_bytes,
            "status_query_transfer_bytes": self.status_query_transfer_bytes,
            "meaningful_status_response_bytes": self.meaningful_status_response_bytes,
            "command_count": self.command_count,
            "status_query_count": self.status_query_count,
            "acknowledgement_count": self.acknowledgement_count,
            "event_counts": dict(sorted(self.event_counts.items())),
        }


class _WallController:
    strip_count = WALL_STRIPS
    leds_per_strip = LEDS_PER_STRIP
    total_leds = WALL_PIXELS
    debug = False


class _DeterministicClockOverlay(ClockOverlayAnimation):
    fixed_now = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)

    def _clock_now(self):
        return self.fixed_now


def _local_patch_ranges(
    dirty_ranges: Iterable[Sequence[int]], receiver_index: int
) -> tuple[tuple[int, int], ...]:
    """Mirror the current publisher's sorted/clipped delta patch policy."""
    local_start = RECEIVER_PIXEL_OFFSETS[receiver_index]
    local_end = local_start + RECEIVER_PIXELS[receiver_index]
    ranges: list[tuple[int, int]] = []
    for start, end in sorted(dirty_ranges):
        clipped_start = max(local_start, int(start))
        clipped_end = min(local_end, int(end))
        if clipped_start >= clipped_end:
            continue
        first = clipped_start - local_start
        last = clipped_end - local_start
        if ranges and first <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], last))
        else:
            ranges.append((first, last))

    patches: list[tuple[int, int]] = []
    for start, end in ranges:
        while start < end:
            patch_end = min(end, start + MAX_RGBA_PIXELS_PER_BATCH_SPAN)
            patches.append((start, patch_end))
            start = patch_end
    return tuple(patches)


def _full_snapshot_ranges(
    local_pixels: int = LOCAL_PIXELS,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (start, min(local_pixels, start + MAX_RGBA_PIXELS_PER_BATCH_SPAN))
        for start in range(0, local_pixels, MAX_RGBA_PIXELS_PER_BATCH_SPAN)
    )


def patch_packet_bytes(ranges: Iterable[Sequence[int]]) -> int:
    """Return legacy v1 single-span packet bytes for before/after evidence."""

    total = 0
    for start, end in ranges:
        count = int(end) - int(start)
        if not 1 <= count <= MAX_RGBA_PIXELS_PER_PATCH:
            raise ValueError("patch count is outside the frozen wire bound")
        total += aligned_wire_size(OVERLAY_PATCH_HEADER_BYTES + count * 4)
    return total


def _batch_packets(
    ranges: Iterable[Sequence[int]],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Greedily mirror the live host's ordered batch packing policy."""

    packets: list[tuple[tuple[int, int], ...]] = []
    packet: list[tuple[int, int]] = []
    packet_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES
    for raw_start, raw_end in ranges:
        start = int(raw_start)
        end = int(raw_end)
        count = end - start
        if not 1 <= count <= MAX_RGBA_PIXELS_PER_BATCH_SPAN:
            raise ValueError("batch span count is outside the frozen wire bound")
        span_bytes = OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES + count * 4
        if packet and packet_bytes + span_bytes > MAX_ALIGNED_SEMANTIC_BYTES:
            packets.append(tuple(packet))
            packet = []
            packet_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES
        packet.append((start, end))
        packet_bytes += span_bytes
    if packet:
        packets.append(tuple(packet))
    return tuple(packets)


def batch_packet_bytes(ranges: Iterable[Sequence[int]]) -> int:
    total = 0
    for packet in _batch_packets(ranges):
        semantic_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES + sum(
            OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES + (end - start) * 4
            for start, end in packet
        )
        total += aligned_wire_size(semantic_bytes)
    return total


def _render_trace(seconds: int) -> list[dict]:
    overlay = _DeterministicClockOverlay(
        _WallController(),
        {
            "face": "digital",
            "show_seconds": True,
            "scale": 1,
            "glow": 0.45,
            "brightness": 1.0,
            "opacity": 1.0,
        },
    )
    start = _DeterministicClockOverlay.fixed_now
    frames = []
    for second in range(seconds):
        overlay.fixed_now = start + timedelta(seconds=second)
        rendered = overlay.generate_frame(float(second), second)
        if not rendered.changed or not rendered.dirty_ranges:
            raise RuntimeError(
                f"the fixed 1 Hz clock trace did not change at second {second}"
            )
        pixels = rendered.pixels
        if (
            pixels.shape != (WALL_PIXELS, 4)
            or pixels.dtype != np.uint8
            or not pixels.flags.c_contiguous
            or np.any(pixels[:, :3] > pixels[:, 3:4])
        ):
            raise RuntimeError("clock_overlay violated the installed RGBA contract")
        local = tuple(
            _local_patch_ranges(rendered.dirty_ranges, receiver)
            for receiver in range(RECEIVER_COUNT)
        )
        frames.append({
            "second": second,
            "revision": rendered.revision,
            "dirty_ranges": tuple(rendered.dirty_ranges),
            "local_patches": local,
            "patch_count": sum(len(item) for item in local),
            "batch_count": sum(len(_batch_packets(item)) for item in local),
            "patch_pixels": sum(end - start for item in local for start, end in item),
            "legacy_patch_packet_bytes": sum(
                patch_packet_bytes(item) for item in local
            ),
            "batch_packet_bytes": sum(batch_packet_bytes(item) for item in local),
        })
    return frames


def _add_generation(
    account: WireAccount,
    patches_by_receiver: Sequence[Sequence[Sequence[int]]],
    *,
    full_snapshot: bool,
    retry_latest_patch: bool,
) -> None:
    event_prefix = "repair" if full_snapshot else "delta"
    for _ in range(RECEIVER_COUNT):
        account.add_acknowledged_command(
            f"{event_prefix}_begin", OVERLAY_BEGIN_BYTES
        )
    for receiver_patches in patches_by_receiver:
        packets = _batch_packets(receiver_patches)
        for packet in packets:
            account.add_acknowledged_command(
                f"{event_prefix}_patch_batch",
                OVERLAY_PATCH_BATCH_HEADER_BYTES
                + sum(
                    OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES
                    + (int(end) - int(start)) * 4
                    for start, end in packet
                ),
            )
        if retry_latest_patch and packets:
            packet = packets[-1]
            account.add_acknowledged_command(
                "exact_batch_retry",
                OVERLAY_PATCH_BATCH_HEADER_BYTES
                + sum(
                    OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES
                    + (int(end) - int(start)) * 4
                    for start, end in packet
                ),
            )
    for _ in range(RECEIVER_COUNT):
        account.add_acknowledged_command(
            f"{event_prefix}_commit", OVERLAY_COMMIT_BYTES
        )


def build_report(
    *,
    seconds: int = 60,
    native_hz: int = 60,
    repair_interval_seconds: int = 30,
    renewal_interval_seconds: int = 1,
    retry_second: int = 20,
) -> dict:
    if seconds < 2 or native_hz < 1:
        raise ValueError("trace must cover at least two seconds and one native frame/second")
    if repair_interval_seconds < 1 or renewal_interval_seconds < 1:
        raise ValueError("repair and renewal intervals must be positive")
    if not 1 <= retry_second < seconds:
        raise ValueError("retry_second must select a non-startup trace second")

    frames = _render_trace(seconds)
    ordinary = frames[1]
    ordinary_patch_ratio = ordinary["batch_packet_bytes"] / FULL_WALL_RGB_PACKET_BYTES
    ordinary_account = WireAccount()
    _add_generation(
        ordinary_account,
        ordinary["local_patches"],
        full_snapshot=False,
        retry_latest_patch=False,
    )
    for _ in range(RECEIVER_COUNT):
        ordinary_account.add_acknowledged_command("lease_renew", OVERLAY_RENEW_BYTES)

    account = WireAccount()
    # One legacy-safe v3 capability-discovery query per receiver. Every
    # acknowledged sparse command below uses negotiated 416-byte status v4.
    for _ in range(RECEIVER_COUNT):
        account.add_status_query(version=3, purpose="publish_preflight_query")

    repair_seconds = set(range(0, seconds, repair_interval_seconds))
    renewal_seconds = set(range(0, seconds, renewal_interval_seconds))
    retry_receivers = [
        receiver
        for receiver, patches in enumerate(frames[retry_second]["local_patches"])
        if patches
    ]
    full_patches = tuple(
        _full_snapshot_ranges(local_pixels)
        for local_pixels in RECEIVER_PIXELS
    )
    for frame in frames:
        second = frame["second"]
        is_repair = second in repair_seconds
        if second != 0:
            for _ in range(RECEIVER_COUNT):
                account.add_status_query(
                    version=4, purpose="publish_preflight_query"
                )
        if second == 0:
            for _ in range(RECEIVER_COUNT):
                account.add_acknowledged_command(
                    "controller_session_begin", CONTROLLER_SESSION_BEGIN_BYTES
                )
        patches = full_patches if is_repair else frame["local_patches"]
        _add_generation(
            account,
            patches,
            full_snapshot=is_repair,
            retry_latest_patch=second == retry_second,
        )
        for _ in range(RECEIVER_COUNT):
            account.add_status_query(
                version=4, purpose="publish_verification_query"
            )
        # The frozen clock policy names a 1,000 ms normal renewal. Counting the
        # independent renewal loop is conservative when a changed commit may
        # already refresh the lease, and prevents the trace from hiding it.
        if second in renewal_seconds:
            for _ in range(RECEIVER_COUNT):
                account.add_status_query(
                    version=4, purpose="renewal_preflight_query"
                )
            for _ in range(RECEIVER_COUNT):
                account.add_acknowledged_command("lease_renew", OVERLAY_RENEW_BYTES)

    baseline_frames = seconds * native_hz
    baseline_spi_bytes = baseline_frames * FULL_WALL_RGB_PACKET_BYTES
    trace_savings = 1.0 - account.spi_clocked_bytes / baseline_spi_bytes
    ordinary_rgba_bytes = ordinary["patch_pixels"] * 4
    ordinary_header_crc_bytes = ordinary["batch_packet_bytes"] - ordinary_rgba_bytes

    patch_bytes = [
        frame["batch_packet_bytes"]
        for frame in frames
        if frame["second"] not in repair_seconds
    ]
    patch_counts = [frame["patch_count"] for frame in frames if frame["second"] not in repair_seconds]
    return {
        "$schema": "ledgrid.sparse-overlay-payload-accounting",
        "version": 1,
        "dimensions": {
            "wall_strips": WALL_STRIPS,
            "receiver_strips": RECEIVER_STRIPS,
            "receiver_strip_counts": list(RECEIVER_STRIP_COUNTS),
            "leds_per_strip": LEDS_PER_STRIP,
            "receiver_count": RECEIVER_COUNT,
            "wall_pixels": WALL_PIXELS,
            "local_pixels": LOCAL_PIXELS,
            "receiver_pixels": list(RECEIVER_PIXELS),
        },
        "wire_contract": {
            "crc_bytes": CRC_BYTES,
            "max_rgba_pixels_per_patch": MAX_RGBA_PIXELS_PER_PATCH,
            "overlay_patch_batch_header_bytes": OVERLAY_PATCH_BATCH_HEADER_BYTES,
            "overlay_patch_batch_span_header_bytes": OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES,
            "max_rgba_pixels_per_batch_span": MAX_RGBA_PIXELS_PER_BATCH_SPAN,
            "batch_capacity_formula": "sum(pixel_counts) + span_count <= 1015",
            "status_response_queue_depth": SPI_RESPONSE_QUEUE_DEPTH,
            "status_transfers_per_acknowledged_command": ACK_STATUS_TRANSFERS,
            "status_v3_query_transfer_bytes": STATUS_V3_QUERY_TRANSFER_BYTES,
            "status_v4_query_transfer_bytes": STATUS_V4_QUERY_TRANSFER_BYTES,
            "local_rgb_packet_bytes": LOCAL_RGB_PACKET_BYTES,
            "receiver_rgb_packet_bytes": list(RECEIVER_RGB_PACKET_BYTES),
            "full_wall_rgb_packet_bytes": FULL_WALL_RGB_PACKET_BYTES,
            "full_wall_rgb_packets": RECEIVER_COUNT,
            "full_snapshot_local_patch_pixels": [
                [end - start for start, end in _full_snapshot_ranges(local_pixels)]
                for local_pixels in RECEIVER_PIXELS
            ],
            "spi_full_duplex_note": (
                "response bytes share the same SPI clocks; bidirectional endpoint "
                "bytes are twice spi_clocked_bytes"
            ),
        },
        "policy": {
            "duration_seconds": seconds,
            "native_background_hz": native_hz,
            "clock_hz": 1,
            "clock_config": {
                "face": "digital",
                "show_seconds": True,
                "scale": 1,
                "glow": 0.45,
            },
            "lease_ms": 3000,
            "renewal_interval_seconds": renewal_interval_seconds,
            "repair_interval_seconds": repair_interval_seconds,
            "repair_seconds": sorted(repair_seconds),
            "retry_policy": (
                "one exact retry of each changed receiver's latest accepted "
                f"batch at second {retry_second}"
            ),
            "retry_receivers": retry_receivers,
        },
        "ordinary_changed_tick": {
            "second": ordinary["second"],
            "dirty_ranges": len(ordinary["dirty_ranges"]),
            "patches": ordinary["patch_count"],
            "batch_packets": ordinary["batch_count"],
            "patch_pixels": ordinary["patch_pixels"],
            "rgba_body_bytes": ordinary_rgba_bytes,
            "batch_overhead_bytes": ordinary_header_crc_bytes,
            "legacy_single_span_packet_bytes": ordinary["legacy_patch_packet_bytes"],
            "patch_packet_bytes_including_headers_crc": ordinary["batch_packet_bytes"],
            "full_wall_rgb_packet_bytes": FULL_WALL_RGB_PACKET_BYTES,
            "patch_ratio": round(ordinary_patch_ratio, 6),
            "below_10_percent": ordinary_patch_ratio < 0.10,
            "acknowledged_generation_plus_renewal": ordinary_account.to_dict(),
            "acknowledged_ratio_to_one_full_wall_packet": round(
                ordinary_account.spi_clocked_bytes / FULL_WALL_RGB_PACKET_BYTES, 6
            ),
            "all_delta_patch_bytes": {
                "minimum": min(patch_bytes),
                "maximum": max(patch_bytes),
                "mean": round(sum(patch_bytes) / len(patch_bytes), 2),
            },
            "all_delta_patch_counts": {
                "minimum": min(patch_counts),
                "maximum": max(patch_counts),
                "mean": round(sum(patch_counts) / len(patch_counts), 2),
            },
        },
        "trace": {
            "clock_changed_ticks": len(frames),
            "repair_snapshots": len(repair_seconds),
            "baseline_full_rgb_frames": baseline_frames,
            "baseline_spi_clocked_bytes": baseline_spi_bytes,
            "baseline_bidirectional_endpoint_bytes": baseline_spi_bytes * 2,
            "sparse": account.to_dict(),
            "savings_ratio": round(trace_savings, 6),
            "at_least_90_percent_savings": trace_savings >= 0.90,
        },
        "acceptance": {
            "ordinary_changed_tick_below_10_percent": ordinary_patch_ratio < 0.10,
            "sixty_second_trace_at_least_90_percent_savings": trace_savings >= 0.90,
            "all_gates_pass": ordinary_patch_ratio < 0.10 and trace_savings >= 0.90,
            "check_enforces_acceptance_gates": True,
        },
        "diagnosis": (
            "Sorted dirty runs share one 28-byte batch header per receiver, and "
            "one command/result proof now acknowledges every span in that batch."
        ),
        "architectural_cause": {
            "ordinary_rgba_body_bytes": ordinary_rgba_bytes,
            "ordinary_batch_overhead_bytes": ordinary_header_crc_bytes,
            "ordinary_header_crc_fraction": round(
                ordinary_header_crc_bytes / ordinary["batch_packet_bytes"], 6
            ),
            "trace_status_query_fraction": round(
                account.status_query_transfer_bytes / account.spi_clocked_bytes, 6
            ),
            "required_direction": (
                "implemented: batch multiple sorted spans under one CRC-bound, "
                "operation-sequenced acknowledgement without weakening result proof"
            ),
        },
    }


def validate_report(report: dict) -> None:
    contract = report["wire_contract"]
    if contract["local_rgb_packet_bytes"] != 3320:
        raise RuntimeError("receiver-local RGB accounting drifted")
    if contract["receiver_rgb_packet_bytes"] != list(RECEIVER_RGB_PACKET_BYTES):
        raise RuntimeError("heterogeneous receiver RGB accounting drifted")
    if contract["full_wall_rgb_packet_bytes"] != sum(RECEIVER_RGB_PACKET_BYTES):
        raise RuntimeError("full-wall RGB accounting drifted")
    expected_chunks = [
        [end - start for start, end in _full_snapshot_ranges(local_pixels)]
        for local_pixels in RECEIVER_PIXELS
    ]
    if contract["full_snapshot_local_patch_pixels"] != expected_chunks:
        raise RuntimeError("full-snapshot chunking drifted")
    sparse = report["trace"]["sparse"]
    orchestration_queries = sum(
        sparse["event_counts"].get(name, 0)
        for name in (
            "publish_preflight_query",
            "publish_verification_query",
            "renewal_preflight_query",
        )
    )
    if sparse["status_query_count"] != (
        sparse["command_count"] * ACK_STATUS_TRANSFERS + orchestration_queries
    ):
        raise RuntimeError("status-query accounting does not match queued acknowledgements")
    if sparse["acknowledgement_count"] != sparse["command_count"]:
        raise RuntimeError("every sparse command must have one proven acknowledgement")
    if sparse["spi_clocked_bytes"] != (
        sparse["command_packet_bytes"] + sparse["status_query_transfer_bytes"]
    ):
        raise RuntimeError("SPI transfer categories do not sum to the trace total")
    events = sparse["event_counts"]
    duration = report["policy"]["duration_seconds"]
    repair_count = len(report["policy"]["repair_seconds"])
    renewal_count = len(
        range(0, duration, report["policy"]["renewal_interval_seconds"])
    )
    exact_counts = {
        "controller_session_begin": RECEIVER_COUNT,
        "repair_begin": RECEIVER_COUNT * repair_count,
        "repair_commit": RECEIVER_COUNT * repair_count,
        "delta_begin": RECEIVER_COUNT * (duration - repair_count),
        "delta_commit": RECEIVER_COUNT * (duration - repair_count),
        "lease_renew": RECEIVER_COUNT * renewal_count,
        "exact_batch_retry": len(report["policy"]["retry_receivers"]),
        "publish_preflight_query": RECEIVER_COUNT * duration,
        "publish_verification_query": RECEIVER_COUNT * duration,
        "renewal_preflight_query": RECEIVER_COUNT * renewal_count,
        "status_v3_query": RECEIVER_COUNT,
        "status_v4_query": sparse["status_query_count"] - RECEIVER_COUNT,
    }
    for name, expected in exact_counts.items():
        if events.get(name, 0) != expected:
            raise RuntimeError(
                f"deterministic trace omitted or added {name}: "
                f"expected {expected}, got {events.get(name, 0)}"
            )
    if not report["acceptance"]["all_gates_pass"]:
        raise RuntimeError("sparse-overlay payload acceptance gates failed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--native-hz", type=int, default=60)
    parser.add_argument("--repair-interval-seconds", type=int, default=30)
    parser.add_argument("--renewal-interval-seconds", type=int, default=1)
    parser.add_argument("--retry-second", type=int, default=20)
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "validate accounting invariants and enforce both frozen acceptance gates"
        ),
    )
    args = parser.parse_args(argv)
    report = build_report(
        seconds=args.seconds,
        native_hz=args.native_hz,
        repair_interval_seconds=args.repair_interval_seconds,
        renewal_interval_seconds=args.renewal_interval_seconds,
        retry_second=args.retry_second,
    )
    if args.check:
        validate_report(report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

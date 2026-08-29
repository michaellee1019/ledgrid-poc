#!/usr/bin/env python3
"""Compare installed SPI dispatch orders using display-black full frames.

This is an exploratory electrical diagnostic, not PERF-01 or WALL-02 evidence.
It never renders an animation: every receiver is forced to brightness zero and
receives only an all-black frame.  The controller service is restored and
stopped at the API boundary before the tool exits.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence
from urllib import request

import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from drivers.led_layout import (  # noqa: E402
    DEFAULT_LEDS_PER_STRIP,
    DEFAULT_STRIP_COUNT,
    WALL_DEVICE_MAP,
    WALL_PHYSICAL_OUTPUT_LANE_MASKS,
    WALL_RECEIVER_GLOBAL_STRIP_OFFSETS,
    WALL_RECEIVER_SPI_SPEEDS_HZ,
    WALL_RECEIVER_STRIP_COUNTS,
    WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER,
    WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
)
if TYPE_CHECKING:
    from drivers.multi_device import MultiDeviceLEDController


SERVICE = "ledgrid.service"
STATUS_URL = "http://127.0.0.1:5000/api/status"
DEVICE_STATE_URL = "http://127.0.0.1:5000/api/device/state"
PRODUCTION_STAGGER_PHASES = 3
FEC_RECEIVER_ID = 3
DEFAULT_ORDERS = ((0, 1, 2, 3, 4), (0, 1, 3, 2, 4), (0, 1, 2, 3, 4))
DELTA_FIELDS = (
    "frames_sent",
    "full_frame_transfers",
    "full_frame_status_transfers",
    "full_frame_status_sample_misses",
    "fec_frames_sent",
    "receiver_packets",
    "receiver_crc_errors",
    "receiver_frames_accepted",
    "receiver_frames_displayed",
    "receiver_publish_drops",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_status_misses",
    "receiver_fec_packets_received",
    "receiver_fec_packets_accepted",
    "receiver_fec_corrected_packets",
    "receiver_fec_corrected_codewords",
    "receiver_fec_uncorrectable_packets",
    "receiver_fec_semantic_crc_errors",
    "receiver_fec_framing_errors",
)


class DispatchDiagnosticError(RuntimeError):
    """The display-black dispatch diagnostic could not complete safely."""


def validate_dispatch_order(
    order: Sequence[int], device_map: Sequence[tuple[int, int]] = WALL_DEVICE_MAP
) -> tuple[int, ...]:
    """Return one exact logical permutation without crossing bus membership."""

    normalized = tuple(order)
    expected = tuple(range(len(device_map)))
    if (
        len(normalized) != len(expected)
        or any(type(value) is not int for value in normalized)
        or set(normalized) != set(expected)
    ):
        raise ValueError(f"dispatch order must be a permutation of {expected}")
    bus_sequence = tuple(dict.fromkeys(device_map[index][0] for index in normalized))
    expected_buses = tuple(dict.fromkeys(bus for bus, _chip_select in device_map))
    if bus_sequence != expected_buses:
        raise ValueError("dispatch order must retain the installed bus-group order")
    for bus in expected_buses:
        positions = [
            position
            for position, logical_id in enumerate(normalized)
            if device_map[logical_id][0] == bus
        ]
        if positions != list(range(min(positions), max(positions) + 1)):
            raise ValueError("dispatch order must keep each SPI bus contiguous")
    return normalized


def apply_dispatch_order(
    controller: MultiDeviceLEDController, order: Sequence[int]
) -> tuple[int, ...]:
    """Change only the within-bus diagnostic schedule, never logical routing."""

    normalized = validate_dispatch_order(order, controller.device_map)
    by_bus: dict[int, list[int]] = {}
    for logical_id in normalized:
        bus = controller.device_map[logical_id][0]
        by_bus.setdefault(bus, []).append(logical_id)
    controller._devices_by_bus = by_bus
    return normalized


def counter_deltas(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[dict[str, int]]:
    """Return strict nonnegative receiver deltas for retained diagnostics."""

    if len(before) != len(after):
        raise ValueError("receiver snapshots must have equal lengths")
    result = []
    for logical_id, (start, finish) in enumerate(zip(before, after)):
        deltas = {"logical_device": logical_id}
        for field in DELTA_FIELDS:
            start_value = start.get(field)
            finish_value = finish.get(field)
            if (
                type(start_value) is not int
                or type(finish_value) is not int
                or finish_value < start_value
            ):
                raise DispatchDiagnosticError(
                    f"receiver {logical_id} {field} counter is unavailable or reset"
                )
            deltas[field] = finish_value - start_value
        result.append(deltas)
    return result


def _service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE], check=False
    )
    return result.returncode == 0


def _service(action: str) -> None:
    subprocess.run(["systemctl", action, SERVICE], check=True)


def _json_request(
    url: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None
) -> Mapping[str, Any]:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise DispatchDiagnosticError(f"{url} returned a malformed response")
    return payload


def _wait_for_safe_idle(timeout: float = 30.0) -> Mapping[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _json_request(
                DEVICE_STATE_URL,
                method="POST",
                payload={"power": False, "brightness": 0},
            )
            status = _json_request(STATUS_URL)
            if (
                status.get("mode") == "idle"
                and status.get("is_running") is False
                and status.get("current_animation") is None
                and status.get("brightness") == 0
                and status.get("frame_data_length") == 0
            ):
                return status
        except Exception as exc:  # Service may still be starting.
            last_error = exc
        time.sleep(0.25)
    raise DispatchDiagnosticError(
        f"controller did not return to black idle state: {last_error}"
    )


def _fresh_snapshots(
    controller: MultiDeviceLEDController,
) -> list[dict[str, Any]]:
    snapshots = []
    for device in controller.devices:
        status = device.query_fresh_receiver_status()
        if not isinstance(status, dict):
            raise DispatchDiagnosticError("fresh receiver status is unavailable")
        snapshots.append(status)
    return snapshots


def _send_black_frames(
    controller: MultiDeviceLEDController,
    frame: np.ndarray,
    *,
    target_fps: int,
    duration: float,
) -> tuple[int, float]:
    period = 1.0 / target_fps
    start = time.monotonic()
    deadline = start
    frames = 0
    while True:
        now = time.monotonic()
        if now - start >= duration:
            break
        if now < deadline:
            time.sleep(deadline - now)
        controller.set_all_pixels(frame)
        frames += 1
        deadline += period
        now = time.monotonic()
        if deadline < now:
            missed = int((now - deadline) // period) + 1
            deadline += missed * period
    return frames, time.monotonic() - start


def _controller() -> MultiDeviceLEDController:
    from drivers.multi_device import MultiDeviceLEDController

    return MultiDeviceLEDController(
        num_devices=len(WALL_DEVICE_MAP),
        device_map=list(WALL_DEVICE_MAP),
        strip_count=DEFAULT_STRIP_COUNT,
        leds_per_strip=DEFAULT_LEDS_PER_STRIP,
        receiver_strip_counts=WALL_RECEIVER_STRIP_COUNTS,
        receiver_global_strip_offsets=WALL_RECEIVER_GLOBAL_STRIP_OFFSETS,
        receiver_lane_masks=WALL_PHYSICAL_OUTPUT_LANE_MASKS,
        receiver_spi_speeds_hz=WALL_RECEIVER_SPI_SPEEDS_HZ,
        reverse_host_strips_by_logical_receiver=(
            WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER
        ),
        reverse_native_strips_by_logical_receiver=(
            WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
        ),
        fec_receiver_ids=(FEC_RECEIVER_ID,),
        parallel=True,
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    orders = tuple(validate_dispatch_order(order) for order in args.orders)
    rates = tuple(args.target_fps)
    service_was_active = _service_active()
    controller: MultiDeviceLEDController | None = None
    arms: list[dict[str, Any]] = []
    cleanup_error: Exception | None = None
    try:
        if service_was_active:
            _wait_for_safe_idle()
            _service("stop")
        controller = _controller()
        controller.set_brightness(0)
        controller.set_stagger_phases(PRODUCTION_STAGGER_PHASES)
        frame = np.zeros((DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 3), dtype=np.uint8)

        apply_dispatch_order(controller, orders[0])
        _send_black_frames(
            controller,
            frame,
            target_fps=max(rates),
            duration=args.warmup_seconds,
        )
        warm = _fresh_snapshots(controller)
        if warm[FEC_RECEIVER_ID].get("fec_transport_enabled") is not True:
            raise DispatchDiagnosticError("receiver 3 did not enable FEC during warmup")

        for target_fps in rates:
            for arm_index, order in enumerate(orders):
                applied = apply_dispatch_order(controller, order)
                before = _fresh_snapshots(controller)
                frames, elapsed = _send_black_frames(
                    controller,
                    frame,
                    target_fps=target_fps,
                    duration=args.duration,
                )
                after = _fresh_snapshots(controller)
                arms.append(
                    {
                        "target_fps": target_fps,
                        "arm_index": arm_index,
                        "dispatch_order": list(applied),
                        "frames_attempted": frames,
                        "elapsed_seconds": elapsed,
                        "attempted_fps": frames / elapsed,
                        "receivers": counter_deltas(before, after),
                    }
                )
        return {
            "schema": "ledgrid.receiver-dispatch-diagnostic",
            "schema_version": 1,
            "display_contract": "brightness-zero-all-black",
            "acceptance_evidence": False,
            "stagger_phases": PRODUCTION_STAGGER_PHASES,
            "spi_speeds_hz": list(WALL_RECEIVER_SPI_SPEEDS_HZ),
            "arms": arms,
        }
    finally:
        if controller is not None:
            try:
                controller.set_brightness(0)
                controller.clear()
            except Exception as exc:
                cleanup_error = exc
            finally:
                controller.close()
        if service_was_active:
            try:
                _service("start")
                _wait_for_safe_idle()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise DispatchDiagnosticError(f"dark-state cleanup failed: {cleanup_error}")


def _order(value: str) -> tuple[int, ...]:
    try:
        return validate_dispatch_order(tuple(int(item) for item in value.split(",")))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--warmup-seconds", type=float, default=5.0)
    parser.add_argument(
        "--target-fps", type=int, action="append", default=None,
        help="Repeatable diagnostic cadence (default: 120 and 160).",
    )
    parser.add_argument(
        "--order", dest="orders", type=_order, action="append", default=None,
        help="Repeatable full logical order, for example 0,1,3,2,4.",
    )
    args = parser.parse_args(argv)
    args.orders = args.orders or list(DEFAULT_ORDERS)
    args.target_fps = args.target_fps or [120, 160]
    if args.duration <= 0 or args.warmup_seconds <= 0:
        parser.error("durations must be positive")
    if any(rate <= 0 for rate in args.target_fps):
        parser.error("target FPS values must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    result = run(parse_args(argv))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

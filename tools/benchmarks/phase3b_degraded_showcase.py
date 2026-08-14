#!/usr/bin/env python3
"""Explicitly degraded Phase 3B0 four-wall hybrid showcase.

This tool is demonstration evidence, never release acceptance.  It accepts only
the installed wall's exact temporary topology: logical receivers 0 and 1 must
provide strict Phase 3B status-v4 identity/capability telemetry, while logical
receivers 2 and 3 must both expose the exact known no-return-path state.  The
same compiled-background and sparse-foreground packets are still written to all
four receivers, but receivers 2 and 3 remain visually unverified.

The ordinary multi-device transaction API deliberately requires every receiver
to acknowledge commands.  This file contains the only degraded exception: it
uses normal acknowledged methods for 0/1 and raw *pre-serialized, allowlisted*
packets for 2/3.  There is no asset/cache/upload surface.  A complete RGB host
frame restores display ownership in ``finally`` before the exact prior desired
display state is restored for the next controller start.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.compositing import HostForegroundCompositor, PlacedOverlay
from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import OverlayFrame, resolve_vibe
from animation.core.receiver_presentation import (
    ReceiverPresentationContext,
    encode_presentation_context_begin,
    encode_presentation_context_commit,
    encode_presentation_context_set,
)
from animation.core.receiver_sparse_publisher import ReceiverSparsePublisher
from animation.plugins.clock_overlay import ClockOverlayAnimation
from drivers.multi_device import MultiDeviceLEDController
from drivers.degraded_receiver_hybrid import (
    DegradedReceiverHybridController as ProductionDegradedHybridTransport,
)
from drivers.spi_controller import (
    CAPABILITY_EXPLICIT_BASE_OWNERSHIP,
    CAPABILITY_PRESENTATION_CONTEXT_V1,
    CAPABILITY_SPARSE_OVERLAY_BATCH_V1,
    CAPABILITY_SPARSE_OVERLAY_V1,
    CAPABILITY_STATIC_LOCAL_BACKGROUND,
    CAPABILITY_STATUS_V3,
    CMD_CONFIG,
    COMMAND_ACK_POLL_INTERVAL_SECONDS,
    CRC_BYTES,
    LEDController,
    MAX_RGBA_PIXELS_PER_BATCH_SPAN,
    OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
    OVERLAY_UPDATE_DELTA,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
    SPI_RESPONSE_QUEUE_DEPTH,
)


POLICY_NAME = "phase3b0_degraded_four_wall_showcase"
READABLE_DEVICES = (0, 1)
UNVERIFIED_DEVICES = (2, 3)
EXPECTED_DEVICE_MAP = ((0, 0), (0, 1), (1, 1), (1, 0))
EXPECTED_STATUS_VERSION = 4
EXPECTED_CAPABILITIES = (
    CAPABILITY_STATIC_LOCAL_BACKGROUND
    | CAPABILITY_PRESENTATION_CONTEXT_V1
    | CAPABILITY_STATUS_V3
    | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
    | CAPABILITY_SPARSE_OVERLAY_V1
    | CAPABILITY_SPARSE_OVERLAY_BATCH_V1
)
LOCAL_STRIPS = 8
LEDS_PER_STRIP = 138
LOCAL_PIXELS = LOCAL_STRIPS * LEDS_PER_STRIP
WALL_STRIPS = 32
WALL_PIXELS = WALL_STRIPS * LEDS_PER_STRIP
COMPILED_RAINBOW_COMPONENT_ID = 1
BASE_LOCAL_BACKGROUND = 1
BASE_HOST_FULL_SCENE = 2
RESULT_OK = 1
OVERLAY_RESULT_OK = frozenset((1, 2))
CONFIRMATION_SCHEMA = "ledgrid.phase3b0-visual-confirmation"
CONFIRMATION_VERSION = 1
DESIRED_DISPLAY_SCHEMA = "ledgrid.desired-display-state"
DESIRED_DISPLAY_VERSION = 1


class ShowcaseFailure(RuntimeError):
    """The degraded showcase failed but still requires exact cleanup."""


@dataclass(frozen=True)
class ShowcaseConfig:
    duration_seconds: float = 15.0
    foreground_poll_hz: float = 20.0
    background_cadence_hz: int = 30
    common_seed: int = 0x3B00CAFE
    vibe_id: str = "cozy"
    lease_ms: int = 3_000

    def __post_init__(self) -> None:
        for name in ("duration_seconds", "foreground_poll_hz"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise ValueError(f"{name} must be finite and greater than zero")
        if (
            isinstance(self.background_cadence_hz, bool)
            or not isinstance(self.background_cadence_hz, int)
            or not 1 <= self.background_cadence_hz <= 200
        ):
            raise ValueError("background_cadence_hz must be between 1 and 200")
        if (
            isinstance(self.common_seed, bool)
            or not isinstance(self.common_seed, int)
            or not 0 <= self.common_seed <= 0xFFFFFFFF
        ):
            raise ValueError("common_seed must fit uint32")
        if self.vibe_id not in ("neutral", "quiet", "cozy", "vivid", "celebration"):
            raise ValueError("vibe_id must be a canonical Phase 2D vibe")
        if (
            isinstance(self.lease_ms, bool)
            or not isinstance(self.lease_ms, int)
            or not 2 <= self.lease_ms <= 0xFFFFFFFF
        ):
            raise ValueError("lease_ms must be between 2 and uint32 maximum")
        foreground_interval = 1.0 / float(self.foreground_poll_hz)
        renewal_interval = min(1.0, self.lease_ms / 2000.0)
        if foreground_interval >= renewal_interval:
            raise ValueError(
                "foreground poll interval must be shorter than the scheduled "
                "lease-renewal interval"
            )


@dataclass(frozen=True)
class RestorationSnapshot:
    """Exact persisted desired state plus its current complete host frame."""

    desired_display: Mapping[str, Any]
    complete_host_frame: np.ndarray

    def __post_init__(self) -> None:
        state = self.desired_display
        if not isinstance(state, Mapping):
            raise TypeError("desired display snapshot must be an object")
        if (
            state.get("schema") != DESIRED_DISPLAY_SCHEMA
            or state.get("schema_version") != DESIRED_DISPLAY_VERSION
        ):
            raise ValueError("restore snapshot is not desired-display-state v1")
        frame = np.asarray(self.complete_host_frame)
        if frame.shape != (WALL_PIXELS, 3):
            raise ValueError(
                f"restore frame must have shape ({WALL_PIXELS}, 3), got {frame.shape}"
            )
        if frame.dtype != np.uint8:
            raise TypeError("restore frame must use uint8 RGB")
        object.__setattr__(self, "desired_display", deepcopy(dict(state)))
        object.__setattr__(self, "complete_host_frame", np.ascontiguousarray(frame).copy())


def _integer(status: Mapping[str, Any], key: str) -> int:
    value = status.get(key, 0)
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _write_only_status(status: Any) -> bool:
    """Recognize only the installed pair's exact no-return telemetry fields."""

    return bool(
        isinstance(status, Mapping)
        and status.get("receiver_status_seen") is False
        and _integer(status, "receiver_status_version") == 0
        and _integer(status, "receiver_capabilities") == 0
        and status.get("receiver_logical_device") is None
    )


def evaluate_preflight(statuses: Any) -> dict[str, Any]:
    """Evaluate the only topology authorized for this degraded showcase."""

    failures: list[str] = []
    receivers: dict[str, Any] = {}
    if not isinstance(statuses, Sequence) or isinstance(statuses, (str, bytes)):
        statuses = ()
    if len(statuses) != 4:
        failures.append(f"receiver telemetry has {len(statuses)} devices; expected exactly 4")

    for logical_id in range(min(4, len(statuses))):
        status = statuses[logical_id]
        if logical_id in READABLE_DEVICES:
            device_failures = []
            if not isinstance(status, Mapping):
                device_failures.append("status is unavailable")
            else:
                if status.get("receiver_status_seen") is not True:
                    device_failures.append("receiver_status_seen is not true")
                version = _integer(status, "receiver_status_version")
                if version != EXPECTED_STATUS_VERSION:
                    device_failures.append(
                        f"status version is {version}; expected exactly {EXPECTED_STATUS_VERSION}"
                    )
                capabilities = _integer(status, "receiver_capabilities")
                if capabilities != EXPECTED_CAPABILITIES:
                    device_failures.append(
                        f"capabilities are 0x{capabilities:08x}; expected exactly "
                        f"0x{EXPECTED_CAPABILITIES:08x}"
                    )
                if status.get("receiver_logical_device") != logical_id:
                    device_failures.append(
                        f"logical identity is {status.get('receiver_logical_device')!r}; "
                        f"expected {logical_id}"
                    )
            failures.extend(
                f"receiver {logical_id} {failure}" for failure in device_failures
            )
            receivers[str(logical_id)] = {
                "telemetry": "strict_phase3b_status_v4",
                "accepted": not device_failures,
                "logical_identity_verified": not device_failures,
                "capabilities_verified": not device_failures,
            }
        else:
            accepted = _write_only_status(status)
            if not accepted:
                failures.append(
                    f"receiver {logical_id} is not the exact status-v0, seen-false, "
                    "capabilities-0, identity-none write-only shape"
                )
            receivers[str(logical_id)] = {
                "telemetry": "known_write_only_no_miso_return",
                "accepted": accepted,
                "logical_identity_verified": False,
                "capabilities_verified": False,
                "physical_display_verified": False,
            }

    readable = [
        index for index in READABLE_DEVICES
        if receivers.get(str(index), {}).get("accepted")
    ]
    unverified = [
        index for index in UNVERIFIED_DEVICES
        if receivers.get(str(index), {}).get("accepted")
    ]
    if readable != list(READABLE_DEVICES) or unverified != list(UNVERIFIED_DEVICES):
        failures.append("degraded policy requires exact readable pair 0/1 and write-only pair 2/3")
    return {
        "passed": not failures,
        "failures": failures,
        "receivers": receivers,
        "acceptance_policy": {
            "name": POLICY_NAME,
            "telemetry_complete": False,
            "readable_devices": list(READABLE_DEVICES),
            "unverified_devices": list(UNVERIFIED_DEVICES),
            "visual_confirmation_required": True,
            "release_acceptance": False,
        },
    }


def validate_visual_confirmation(payload: Any, challenge: str) -> dict[str, Any]:
    """Validate a nonce-bound statement made after the showcase was visible."""

    if not isinstance(payload, Mapping):
        raise ShowcaseFailure("visual confirmation is unavailable")
    expected = {
        "schema": CONFIRMATION_SCHEMA,
        "schema_version": CONFIRMATION_VERSION,
        "challenge": challenge,
        "verdict": "pass",
        "observed_logical_devices": [0, 1, 2, 3],
        "acknowledged_unverified_devices": [2, 3],
    }
    failures = [
        f"{key} is {payload.get(key)!r}; expected {value!r}"
        for key, value in expected.items()
        if payload.get(key) != value
    ]
    operator = payload.get("operator")
    if not isinstance(operator, str) or not operator.strip():
        failures.append("operator must be a non-empty string")
    if failures:
        raise ShowcaseFailure("visual confirmation rejected: " + "; ".join(failures))
    return {
        "confirmed": True,
        "operator": operator.strip(),
        "challenge": challenge,
        "observed_logical_devices": [0, 1, 2, 3],
        "unverified_devices": [2, 3],
    }


class FileVisualConfirmation:
    """Nonblocking file exchange for confirmation while the scene stays live."""

    def __init__(
        self,
        challenge_path: Path,
        response_path: Path,
        *,
        timeout: float,
        poll_interval: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.challenge_path = Path(challenge_path)
        self.response_path = Path(response_path)
        if self.response_path.exists():
            raise ValueError("visual confirmation response must not exist before the run")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("confirmation timeout must be finite and greater than zero")
        if (
            isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or not math.isfinite(float(poll_interval))
            or float(poll_interval) <= 0.0
        ):
            raise ValueError(
                "confirmation poll interval must be finite and greater than zero"
            )
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self.clock = clock
        self._challenge: Optional[str] = None
        self._deadline: Optional[float] = None

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def begin(self, challenge: str) -> None:
        if self._challenge is not None:
            raise RuntimeError("visual confirmation exchange already started")
        if not isinstance(challenge, str) or not challenge:
            raise ValueError("visual confirmation challenge must be non-empty")
        self._atomic_json(self.challenge_path, {
            "schema": CONFIRMATION_SCHEMA,
            "schema_version": CONFIRMATION_VERSION,
            "challenge": challenge,
            "required_verdict": "pass",
            "observed_logical_devices": [0, 1, 2, 3],
            "acknowledged_unverified_devices": [2, 3],
            "instructions": (
                "After observing every wall lane, write the response file with "
                "this challenge, verdict=pass, and a non-empty operator."
            ),
        })
        self._challenge = challenge
        self._deadline = self.clock() + self.timeout

    def remaining_seconds(self) -> float:
        if self._challenge is None or self._deadline is None:
            raise RuntimeError("visual confirmation exchange has not started")
        return max(0.0, self._deadline - self.clock())

    def poll(self) -> Optional[Mapping[str, Any]]:
        if self._challenge is None or self._deadline is None:
            raise RuntimeError("visual confirmation exchange has not started")
        # The observation must arrive strictly before the bounded deadline.  A
        # response file that appears at or after it cannot race this check and
        # turn a timed-out run into a pass.
        if self.clock() >= self._deadline:
            raise ShowcaseFailure("visual confirmation response timed out")
        if self.response_path.exists():
            try:
                payload = json.loads(self.response_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise ShowcaseFailure(
                    f"visual confirmation response is malformed: {exc}"
                ) from exc
            if not isinstance(payload, Mapping):
                raise ShowcaseFailure("visual confirmation response is not an object")
            return payload
        return None


class ClockForegroundSource:
    """Existing clock overlay rendered as one aggregate sparse foreground."""

    def __init__(self, controller: Any) -> None:
        self.animation = ClockOverlayAnimation(controller, {
            "face": "digital",
            "palette": "amber",
            "format_24h": False,
            "show_seconds": True,
            "position_y": 0.5,
            "scale": 1,
            "glow": 0.45,
            "brightness": 1.0,
            "opacity": 1.0,
        })
        self.compositor = HostForegroundCompositor(
            controller.strip_count, controller.leds_per_strip
        )

    def start(self) -> None:
        self.animation.start()

    def render(self, elapsed: float, frame_count: int) -> OverlayFrame:
        frame = self.animation.generate_frame(elapsed, frame_count)
        return self.compositor.compose((PlacedOverlay(frame),))

    def stop(self) -> None:
        self.animation.stop()

    def cleanup(self) -> None:
        self.animation.cleanup()


class DegradedHybridTransport:
    """Strict/readable + write-only Phase 3B command transport."""

    def __init__(
        self,
        controller: Any,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.controller = controller
        self.devices = list(getattr(controller, "devices", ()))
        self.num_devices = getattr(controller, "num_devices", len(self.devices))
        self.strips_per_device = getattr(controller, "strips_per_device", None)
        self.leds_per_strip = getattr(controller, "leds_per_strip", None)
        self.strip_count = getattr(controller, "strip_count", None)
        self.total_leds = getattr(controller, "total_leds", None)
        self.leds_per_device = LOCAL_PIXELS
        if (
            self.num_devices != 4
            or len(self.devices) != 4
            or self.strips_per_device != LOCAL_STRIPS
            or self.leds_per_strip != LEDS_PER_STRIP
            or self.strip_count != WALL_STRIPS
            or self.total_leds != WALL_PIXELS
        ):
            raise ShowcaseFailure("controller geometry is not the installed 4 x 8 x 138 wall")
        device_map = getattr(controller, "device_map", None)
        if device_map is not None and tuple(device_map) != EXPECTED_DEVICE_MAP:
            raise ShowcaseFailure(
                f"controller device map is {tuple(device_map)!r}; expected {EXPECTED_DEVICE_MAP!r}"
            )
        observed_devices = tuple(
            (getattr(device, "bus", None), getattr(device, "device", None))
            for device in self.devices
        )
        if observed_devices != EXPECTED_DEVICE_MAP:
            raise ShowcaseFailure(
                f"controller device objects route to {observed_devices!r}; "
                f"expected {EXPECTED_DEVICE_MAP!r}"
            )
        self._sleeper = sleeper
        self._session: Optional[bytes] = None
        self._generation = 0
        self._scene_revision: Optional[int] = None
        self._expected_alpha_coverage = (0, 0, 0, 0)
        self._coverage_checks = 0
        self._identity_configuration: Optional[dict[str, Any]] = None
        self._status: dict[str, Any] = {
            "state": "initialized",
            "operation": "none",
            "telemetry_complete": False,
        }

    @staticmethod
    def _fresh_status(device: Any) -> Mapping[str, Any]:
        status = None
        for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 1):
            status = device.query_receiver_status()
        if not isinstance(status, Mapping):
            return {}
        return dict(status)

    def preflight(self) -> dict[str, Any]:
        result = evaluate_preflight([
            self._fresh_status(device) for device in self.devices
        ])
        if not result["passed"]:
            raise ShowcaseFailure("topology preflight: " + "; ".join(result["failures"]))
        return result

    @staticmethod
    def _require_ack(status: Any, stage: str, logical_id: int, *, overlay: bool = False) -> None:
        if not isinstance(status, Mapping) or _integer(status, "receiver_last_result") != RESULT_OK:
            raise ShowcaseFailure(f"receiver {logical_id} did not acknowledge {stage}")
        if overlay and status.get("receiver_overlay_operation_result") not in OVERLAY_RESULT_OK:
            raise ShowcaseFailure(f"receiver {logical_id} rejected {stage}")

    def _write_only_packet(self, device: Any, stage: str, payload: bytes) -> None:
        """Transmit one validated allowlisted packet without claiming an ACK."""

        test_hook = getattr(device, "write_only_packet", None)
        if callable(test_hook):
            test_hook(stage, bytes(payload))
        else:
            xfer = getattr(device, "_xfer", None)
            if not callable(xfer):
                raise ShowcaseFailure(f"write-only receiver lacks raw transport for {stage}")
            xfer(bytes(payload))
        # A completed master transfer does not mean the receiver task has
        # processed and re-queued its two-deep slave DMA slot.  Apply the same
        # bounded refill interval used by acknowledged commands after every raw
        # write, including identity, snapshot batches, renewals, and cleanup.
        self._sleeper(COMMAND_ACK_POLL_INTERVAL_SECONDS)

    @staticmethod
    def _identity_packet(logical_id: int) -> bytes:
        if logical_id not in range(4):
            raise ValueError("logical identity must be 0..3")
        return bytes((
            CMD_CONFIG,
            LOCAL_STRIPS,
            (LEDS_PER_STRIP >> 8) & 0xFF,
            LEDS_PER_STRIP & 0xFF,
            0,
            logical_id,
        ))

    def configure_logical_identities(self) -> dict[str, Any]:
        """Explicitly bind every freshly flashed receiver before runtime commands."""

        devices: dict[str, Any] = {}
        for logical_id, device in enumerate(self.devices):
            packet = self._identity_packet(logical_id)
            if logical_id in READABLE_DEVICES:
                command = getattr(device, "_command_status", None)
                if not callable(command):
                    raise ShowcaseFailure(
                        f"receiver {logical_id} lacks exact CONFIG acknowledgement path"
                    )
                status = command(packet, required_status_version=EXPECTED_STATUS_VERSION)
                self._require_ack(status, "logical identity CONFIG", logical_id)
                expected = {
                    "receiver_status_version": EXPECTED_STATUS_VERSION,
                    "receiver_logical_device": logical_id,
                    "receiver_last_processed_command": CMD_CONFIG,
                }
                for key, value in expected.items():
                    if status.get(key) != value:
                        raise ShowcaseFailure(
                            f"receiver {logical_id} CONFIG reported {key}="
                            f"{status.get(key)!r}; expected {value!r}"
                        )
                devices[str(logical_id)] = {
                    "payload_bytes": len(packet),
                    "wire_bytes": len(packet) + CRC_BYTES,
                    "receiver_acknowledged": True,
                    "logical_identity_verified": True,
                    "logical_device": logical_id,
                }
            else:
                self._write_only_packet(device, "logical identity CONFIG", packet)
                devices[str(logical_id)] = {
                    "payload_bytes": len(packet),
                    "wire_bytes": len(packet) + CRC_BYTES,
                    "receiver_acknowledged": False,
                    "logical_identity_verified": False,
                    "logical_device_requested": logical_id,
                    "warning": (
                        "outbound-only CONFIG; receiver identity remains unverified "
                        "until the SPI1 return path is repaired"
                    ),
                }
        result = {
            "passed": True,
            "telemetry_complete": False,
            "devices": devices,
            "unverified_devices": list(UNVERIFIED_DEVICES),
        }
        self._identity_configuration = result
        self._status.update({
            "operation": "logical_identity_config",
            "identity_configuration": result,
        })
        return result

    def _invoke(
        self,
        logical_id: int,
        stage: str,
        method_name: str,
        payload: bytes,
        *,
        overlay: bool = False,
        kwargs: Optional[dict[str, Any]] = None,
    ) -> Any:
        device = self.devices[logical_id]
        if logical_id in READABLE_DEVICES:
            status = getattr(device, method_name)(**(kwargs or {}))
            self._require_ack(status, stage, logical_id, overlay=overlay)
            return status
        self._write_only_packet(device, stage, payload)
        return None

    def start_local_background(
        self,
        context: ReceiverPresentationContext,
        *,
        component_id: int,
        preferred_cadence_hz: int,
        common_seed: int,
    ) -> bool:
        if component_id != COMPILED_RAINBOW_COMPONENT_ID:
            raise ShowcaseFailure("only the compiled static rainbow is allowed")
        begin = encode_presentation_context_begin(context)
        setting = encode_presentation_context_set(context)
        commit = encode_presentation_context_commit(context)
        start_packets = [
            LEDController.serialize_local_background_start(
                component_id=component_id,
                preferred_cadence_hz=preferred_cadence_hz,
                global_strip_offset=index * LOCAL_STRIPS,
                common_seed=common_seed,
                scene_epoch=context.scene_epoch,
            )
            for index in range(4)
        ]
        for index in range(4):
            self._invoke(index, "presentation begin", "begin_presentation_context", begin,
                         kwargs={"context": context})
            self._invoke(index, "presentation set", "set_presentation_context", setting,
                         kwargs={"context": context})
        anchor = time.monotonic_ns()
        for index in range(4):
            self._invoke(index, "presentation commit", "commit_presentation_context", commit,
                         kwargs={"context": context, "host_monotonic_anchor_ns": anchor})
        for index in range(4):
            kwargs = {
                "component_id": component_id,
                "preferred_cadence_hz": preferred_cadence_hz,
                "global_strip_offset": index * LOCAL_STRIPS,
                "common_seed": common_seed,
                "scene_epoch": context.scene_epoch,
            }
            self._invoke(index, "local background start", "start_local_background",
                         start_packets[index], kwargs=kwargs)

        expected = {
            "receiver_base_mode": BASE_LOCAL_BACKGROUND,
            "receiver_component_id": component_id,
            "receiver_declared_cadence_hz": preferred_cadence_hz,
            "receiver_common_seed": common_seed,
            "receiver_scene_epoch": context.scene_epoch,
            "receiver_active_scene_revision": context.scene_revision,
            "receiver_active_context_digest": context.context_digest.hex(),
            "receiver_active_session_id": context.controller_session_id.hex(),
        }
        for index in READABLE_DEVICES:
            status = self._fresh_status(self.devices[index])
            for key, value in {**expected, "receiver_global_strip_offset": index * LOCAL_STRIPS}.items():
                if status.get(key) != value:
                    raise ShowcaseFailure(
                        f"receiver {index} reported {key}={status.get(key)!r}; expected {value!r}"
                    )
        self._status.update({
            "state": "active",
            "operation": "local_background_start",
            "context_digest": context.context_digest.hex(),
        })
        return True

    @staticmethod
    def _normalize_overlay(pixels: Any) -> np.ndarray:
        overlay = np.asarray(pixels)
        if overlay.shape != (WALL_PIXELS, 4):
            raise ValueError(f"foreground must have shape ({WALL_PIXELS}, 4)")
        if overlay.dtype != np.uint8:
            raise TypeError("foreground must use uint8 premultiplied RGBA")
        if np.any(overlay[:, :3] > overlay[:, 3:4]):
            raise ValueError("foreground RGB must not exceed alpha")
        return np.ascontiguousarray(overlay)

    @staticmethod
    def _dirty_ranges(ranges: Any) -> tuple[tuple[int, int], ...]:
        if ranges is None:
            raise ValueError("delta foreground requires dirty_ranges")
        result = []
        prior = 0
        for item in ranges:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError("dirty ranges must be (start, end) pairs")
            start, end = item
            if (
                isinstance(start, bool) or not isinstance(start, (int, np.integer))
                or isinstance(end, bool) or not isinstance(end, (int, np.integer))
            ):
                raise TypeError("dirty range bounds must be integers")
            first, last = int(start), int(end)
            if first < 0 or last > WALL_PIXELS or first >= last or (result and first < prior):
                raise ValueError("dirty ranges must be sorted, non-overlapping, and in bounds")
            result.append((first, last))
            prior = last
        return tuple(result)

    @staticmethod
    def _local_patches(
        pixels: np.ndarray,
        logical_id: int,
        *,
        full_snapshot: bool,
        dirty_ranges: Optional[tuple[tuple[int, int], ...]],
    ) -> list[tuple[int, np.ndarray]]:
        local_start = logical_id * LOCAL_PIXELS
        local = pixels[local_start:local_start + LOCAL_PIXELS]
        if full_snapshot:
            return [
                (start, local[start:start + MAX_RGBA_PIXELS_PER_BATCH_SPAN])
                for start in range(0, LOCAL_PIXELS, MAX_RGBA_PIXELS_PER_BATCH_SPAN)
            ]
        patches = []
        for start, end in dirty_ranges or ():
            first = max(local_start, start)
            last = min(local_start + LOCAL_PIXELS, end)
            for first in range(first, last, MAX_RGBA_PIXELS_PER_BATCH_SPAN):
                count = min(MAX_RGBA_PIXELS_PER_BATCH_SPAN, last - first)
                local_first = first - local_start
                patches.append((local_first, local[local_first:local_first + count]))
        return patches

    def publish_sparse_overlay(
        self,
        pixels: Any,
        *,
        controller_session_id: bytes,
        generation: int,
        prior_generation: int,
        scene_revision: int,
        scene_epoch: int,
        base_revision: int,
        lease_ms: int,
        present_at_scene_time_us: int,
        dirty_ranges: Any = None,
        full_snapshot: bool = False,
    ) -> bool:
        overlay = self._normalize_overlay(pixels)
        expected_coverage = tuple(
            int(np.count_nonzero(
                overlay[index * LOCAL_PIXELS:(index + 1) * LOCAL_PIXELS, 3]
            ))
            for index in range(4)
        )
        missing = [
            index for index, count in enumerate(expected_coverage) if count <= 0
        ]
        if missing:
            raise ShowcaseFailure(
                "foreground has zero expected alpha coverage on logical receiver(s) "
                + ", ".join(str(index) for index in missing)
            )
        session = LEDController._controller_session(controller_session_id)
        if generation <= prior_generation or generation >= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("foreground generation is not a safe successor")
        new_session = session != self._session
        if new_session and (not full_snapshot or prior_generation != 0):
            raise ValueError("new foreground authority must begin with generation-zero snapshot")
        if not new_session and prior_generation != self._generation:
            raise ValueError("prior foreground generation disagrees with transport")
        ranges = None if full_snapshot else self._dirty_ranges(dirty_ranges)
        kind = OVERLAY_UPDATE_FULL_SNAPSHOT if full_snapshot else OVERLAY_UPDATE_DELTA
        digest = hashlib.sha256(memoryview(overlay).cast("B")).digest()
        per_device = [
            self._local_patches(
                overlay, index, full_snapshot=full_snapshot, dirty_ranges=ranges
            )
            for index in range(4)
        ]

        session_packet = LEDController.serialize_controller_session_begin(
            controller_session_id=session,
            desired_revision=scene_revision,
            authoritative_snapshot_digest=digest,
        )
        begin_packets = []
        patch_packets = []
        for patches in per_device:
            begin_packets.append(LEDController.serialize_overlay_begin(
                controller_session_id=session,
                generation=generation,
                prior_generation=prior_generation,
                scene_revision=scene_revision,
                scene_epoch=scene_epoch,
                base_revision=base_revision,
                format=OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
                update_kind=kind,
                expected_patches=len(patches),
                lease_ms=lease_ms,
            ))
            patch_packets.append(LEDController.serialize_overlay_patch_batches(
                controller_session_id=session,
                generation=generation,
                patches=patches,
                update_kind=kind,
            ))
        commit_packet = LEDController.serialize_overlay_commit(
            controller_session_id=session,
            generation=generation,
            scene_epoch=scene_epoch,
            base_revision=base_revision,
            present_at_scene_time_us=present_at_scene_time_us,
        )

        if new_session:
            for index in range(4):
                self._invoke(index, "foreground session", "begin_controller_session",
                             session_packet, overlay=True, kwargs={
                                 "controller_session_id": session,
                                 "desired_revision": scene_revision,
                                 "authoritative_snapshot_digest": digest,
                             })
        for index, patches in enumerate(per_device):
            begin_kwargs = {
                "controller_session_id": session,
                "generation": generation,
                "prior_generation": prior_generation,
                "scene_revision": scene_revision,
                "scene_epoch": scene_epoch,
                "base_revision": base_revision,
                "format": OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
                "update_kind": kind,
                "expected_patches": len(patches),
                "lease_ms": lease_ms,
            }
            self._invoke(index, "foreground begin", "begin_overlay",
                         begin_packets[index], overlay=True, kwargs=begin_kwargs)
            if index in READABLE_DEVICES:
                statuses = self.devices[index].send_overlay_patches(
                    controller_session_id=session,
                    generation=generation,
                    patches=patches,
                    update_kind=kind,
                )
                for status in statuses:
                    self._require_ack(status, "foreground patch batch", index, overlay=True)
            else:
                for packet in patch_packets[index]:
                    self._write_only_packet(
                        self.devices[index], "foreground patch batch", packet
                    )
            commit_kwargs = {
                "controller_session_id": session,
                "generation": generation,
                "scene_epoch": scene_epoch,
                "base_revision": base_revision,
                "present_at_scene_time_us": present_at_scene_time_us,
            }
            self._invoke(index, "foreground commit", "commit_overlay",
                         commit_packet, overlay=True, kwargs=commit_kwargs)

        for index in READABLE_DEVICES:
            status = self._fresh_status(self.devices[index])
            expected = {
                "receiver_base_mode": BASE_LOCAL_BACKGROUND,
                "receiver_foreground_state": 2,
                "receiver_overlay_committed_generation": generation,
                "receiver_overlay_staged_generation": 0,
                "receiver_overlay_session_id": session.hex(),
                "receiver_foreground_scene_revision": scene_revision,
                "receiver_foreground_scene_epoch": scene_epoch,
                "receiver_foreground_base_revision": base_revision,
                "receiver_foreground_present_at_scene_time_us": present_at_scene_time_us,
                "receiver_overlay_committed_coverage_pixels": expected_coverage[index],
            }
            for key, value in expected.items():
                if status.get(key) != value:
                    raise ShowcaseFailure(
                        f"receiver {index} reported {key}={status.get(key)!r}; expected {value!r}"
                    )
        self._session = session
        self._generation = generation
        self._scene_revision = scene_revision
        self._expected_alpha_coverage = expected_coverage
        self._coverage_checks += 1
        self._status.update({
            "state": "active",
            "operation": "foreground_publish",
            "foreground_generation": generation,
            "expected_alpha_coverage_by_receiver": {
                str(index): count for index, count in enumerate(expected_coverage)
            },
            "coverage_checks": self._coverage_checks,
        })
        return True

    def renew_sparse_overlay(
        self, *, controller_session_id: bytes, generation: int, lease_ms: int
    ) -> bool:
        session = LEDController._controller_session(controller_session_id)
        if session != self._session or generation != self._generation:
            raise ValueError("foreground renewal does not match active authority")
        packet = LEDController.serialize_overlay_renew(
            controller_session_id=session, generation=generation, lease_ms=lease_ms
        )
        kwargs = {
            "controller_session_id": session,
            "generation": generation,
            "lease_ms": lease_ms,
        }
        for index in range(4):
            self._invoke(index, "foreground renew", "renew_overlay", packet,
                         overlay=True, kwargs=kwargs)
        for index in READABLE_DEVICES:
            status = self._fresh_status(self.devices[index])
            if (
                status.get("receiver_foreground_state") != 2
                or status.get("receiver_overlay_committed_generation") != generation
                or status.get("receiver_overlay_session_id") != session.hex()
                or status.get("receiver_overlay_committed_coverage_pixels")
                != self._expected_alpha_coverage[index]
            ):
                raise ShowcaseFailure(
                    f"receiver {index} lost foreground authority or exact coverage on renew"
                )
        self._status.update({"operation": "foreground_renew"})
        return True

    def clear_sparse_overlay(
        self, *, controller_session_id: bytes, generation: int, scene_revision: int
    ) -> bool:
        session = LEDController._controller_session(controller_session_id)
        packet = LEDController.serialize_overlay_clear(
            controller_session_id=session,
            generation=generation,
            scene_revision=scene_revision,
        )
        kwargs = {
            "controller_session_id": session,
            "generation": generation,
            "scene_revision": scene_revision,
        }
        for index in range(4):
            self._invoke(index, "foreground clear", "clear_overlay", packet,
                         overlay=True, kwargs=kwargs)
        self._generation = generation
        self._expected_alpha_coverage = (0, 0, 0, 0)
        self._status.update({"operation": "foreground_clear"})
        return True

    def foreground_visibility(self) -> dict[str, Any]:
        expected = {
            str(index): count
            for index, count in enumerate(self._expected_alpha_coverage)
        }
        readable = {}
        failures = []
        for index in READABLE_DEVICES:
            status = self._fresh_status(self.devices[index])
            status_version = _integer(status, "receiver_status_version")
            logical_device = status.get("receiver_logical_device")
            foreground_state = status.get("receiver_foreground_state")
            observed_session = status.get("receiver_overlay_session_id")
            observed_generation = _integer(
                status, "receiver_overlay_committed_generation"
            )
            lease_remaining_ms = _integer(
                status, "receiver_overlay_lease_remaining_ms"
            )
            observed_coverage = _integer(
                status, "receiver_overlay_committed_coverage_pixels"
            )
            proof = {
                "status_v4_exact": status_version == EXPECTED_STATUS_VERSION,
                "logical_identity_exact": logical_device == index,
                "foreground_active": foreground_state == 2,
                "session_exact": (
                    self._session is not None
                    and observed_session == self._session.hex()
                ),
                "generation_exact": observed_generation == self._generation,
                "lease_remaining_positive": lease_remaining_ms > 0,
                "coverage_expected_positive": (
                    self._expected_alpha_coverage[index] > 0
                ),
                "coverage_exact": (
                    observed_coverage == self._expected_alpha_coverage[index]
                ),
            }
            readable[str(index)] = {
                "status_version": status_version,
                "logical_device": logical_device,
                "expected_alpha_pixels": self._expected_alpha_coverage[index],
                "observed_committed_coverage_pixels": observed_coverage,
                "expected_session_id": (
                    None if self._session is None else self._session.hex()
                ),
                "observed_session_id": observed_session,
                "expected_generation": self._generation,
                "observed_generation": observed_generation,
                "lease_remaining_ms": lease_remaining_ms,
                **proof,
            }
            failed_checks = [name for name, passed in proof.items() if not passed]
            if failed_checks:
                failures.append(
                    f"receiver {index} pass-boundary visibility failed "
                    + ", ".join(failed_checks)
                )
        write_only = {
            str(index): {
                "expected_alpha_pixels": self._expected_alpha_coverage[index],
                "receiver_telemetry_verified": False,
                "physical_display_verified": False,
                "warning": "expected host coverage only; receiver/display is unverified",
            }
            for index in UNVERIFIED_DEVICES
        }
        if any(count <= 0 for count in self._expected_alpha_coverage):
            failures.append("expected foreground alpha coverage is not positive on every lane")
        return {
            "passed": not failures,
            "failures": failures,
            "all_lanes_expected_nonzero": all(
                count > 0 for count in self._expected_alpha_coverage
            ),
            "expected_alpha_coverage_by_receiver": expected,
            "readable_receivers": readable,
            "write_only_receivers": write_only,
            "coverage_checks": self._coverage_checks,
            "sampled_at_pass_boundary": True,
        }

    def host_stats(self, logical_ids: Sequence[int]) -> dict[int, dict[str, int]]:
        result = {}
        for index in logical_ids:
            stats = self.devices[index].get_stats()
            result[index] = {
                key: _integer(stats, key)
                for key in ("spi_transfers", "bytes_sent", "errors")
            }
        return result

    def set_all_pixels(self, pixels: Any) -> None:
        frame = np.asarray(pixels)
        if frame.shape != (WALL_PIXELS, 3) or frame.dtype != np.uint8:
            raise ValueError("complete host takeover must be installed-wall uint8 RGB")
        failures = []
        for index, device in enumerate(self.devices):
            local = np.ascontiguousarray(
                frame[index * LOCAL_PIXELS:(index + 1) * LOCAL_PIXELS]
            )
            try:
                accepted = device.set_all_pixels(local)
                if accepted is False:
                    raise RuntimeError("receiver rejected complete RGB frame")
            except Exception as exc:
                failures.append(f"receiver {index}: {exc}")
        for index in READABLE_DEVICES:
            try:
                status = self._fresh_status(self.devices[index])
                if status.get("receiver_base_mode") != BASE_HOST_FULL_SCENE:
                    raise RuntimeError(
                        f"reported base mode {status.get('receiver_base_mode')!r}"
                    )
            except Exception as exc:
                failures.append(f"receiver {index} takeover proof: {exc}")
        if failures:
            raise ShowcaseFailure("complete host takeover failed: " + "; ".join(failures))
        self._expected_alpha_coverage = (0, 0, 0, 0)
        self._status.update({"state": "host_full_scene", "operation": "set_all_takeover"})

    def get_stats(self) -> dict[str, Any]:
        return {"aggregate": {"local_background": dict(self._status)}}


# The bounded showcase and persistent service share exactly one degraded wire
# implementation.  The legacy class above is retained temporarily as readable
# historical context for the evidence format; all runtime construction below
# resolves this production facade.
DegradedHybridTransport = ProductionDegradedHybridTransport


def evaluate_write_only_host_evidence(
    before: Mapping[int, Mapping[str, int]],
    after: Mapping[int, Mapping[str, int]],
) -> dict[str, Any]:
    failures = []
    devices = {}
    for index in UNVERIFIED_DEVICES:
        first = before.get(index, {})
        last = after.get(index, {})
        deltas = {
            key: int(last.get(key, 0)) - int(first.get(key, 0))
            for key in ("spi_transfers", "bytes_sent", "errors")
        }
        if deltas["spi_transfers"] <= 0:
            failures.append(f"receiver {index} host SPI transfers did not advance")
        if deltas["bytes_sent"] <= 0:
            failures.append(f"receiver {index} host SPI bytes did not advance")
        if deltas["errors"] != 0:
            failures.append(f"receiver {index} host SPI errors changed by {deltas['errors']}")
        devices[str(index)] = {
            "host_counter_deltas": deltas,
            "receiver_telemetry_verified": False,
            "physical_display_verified": False,
        }
    return {"passed": not failures, "failures": failures, "devices": devices}


class Phase3BDegradedShowcase:
    """Run the bounded demo and make restoration unconditional."""

    def __init__(
        self,
        config: ShowcaseConfig,
        restoration: RestorationSnapshot,
        *,
        controller_factory: Callable[[], Any],
        restore_desired_display: Callable[[Mapping[str, Any]], None],
        confirmation_provider: Any,
        frame_source_factory: Callable[[Any], Any] = ClockForegroundSource,
        transport_factory: Optional[
            Callable[[Any], DegradedHybridTransport]
        ] = None,
        clock: Callable[[], float] = time.monotonic,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sleeper: Callable[[float], None] = time.sleep,
        session_factory: Callable[[int], bytes] = secrets.token_bytes,
        challenge_factory: Callable[[int], str] = secrets.token_hex,
    ) -> None:
        self.config = config
        self.restoration = restoration
        self.controller_factory = controller_factory
        self.restore_desired_display = restore_desired_display
        self.confirmation_provider = confirmation_provider
        self.frame_source_factory = frame_source_factory
        self.transport_factory = transport_factory
        self.clock = clock
        self.monotonic_ns = monotonic_ns
        self.sleeper = sleeper
        self.session_factory = session_factory
        self.challenge_factory = challenge_factory

    def _context(self, publisher: ReceiverSparsePublisher) -> ReceiverPresentationContext:
        epoch = self.monotonic_ns() & 0xFFFFFFFFFFFFFFFF
        return ReceiverPresentationContext(
            controller_session_id=publisher.controller_session_id,
            scene_revision=epoch,
            scene_epoch=epoch,
            present_at_scene_time_us=0,
            vibe=resolve_vibe(self.config.vibe_id, revision=1),
            plant_modifiers=PlantModifierState.from_payload({}),
            plant_revision=1,
        )

    def _begin_confirmation_exchange(
        self, challenge: str
    ) -> tuple[
        Callable[[], Optional[Mapping[str, Any]]],
        str,
        Optional[float],
        Optional[Callable[[], float]],
    ]:
        begin = getattr(self.confirmation_provider, "begin", None)
        poll = getattr(self.confirmation_provider, "poll", None)
        if callable(begin) and callable(poll):
            begin(challenge)
            poll_interval = getattr(
                self.confirmation_provider, "poll_interval", None
            )
            if poll_interval is not None:
                if (
                    isinstance(poll_interval, bool)
                    or not isinstance(poll_interval, (int, float))
                    or not math.isfinite(float(poll_interval))
                    or float(poll_interval) <= 0.0
                ):
                    raise TypeError(
                        "live confirmation poll_interval must be a positive "
                        "finite number"
                    )
                poll_interval = float(poll_interval)
            remaining = getattr(
                self.confirmation_provider, "remaining_seconds", None
            )
            if remaining is not None and not callable(remaining):
                raise TypeError(
                    "live confirmation remaining_seconds must be callable"
                )
            return (
                poll,
                "live_nonblocking_exchange",
                poll_interval,
                remaining,
            )
        if callable(self.confirmation_provider):
            # Portable fakes historically returned an immediate confirmation.
            # Keep that narrow compatibility without using it for the CLI/live
            # path, where blocking here would stop lease renewal.
            payload = self.confirmation_provider(challenge)
            return (
                lambda: payload,
                "immediate_callable_compatibility",
                None,
                None,
            )
        raise TypeError(
            "confirmation provider must expose begin()/poll() or be an immediate callable"
        )

    def run(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "passed": False,
            "acceptance_policy": {
                "name": POLICY_NAME,
                "telemetry_complete": False,
                "readable_devices": list(READABLE_DEVICES),
                "unverified_devices": list(UNVERIFIED_DEVICES),
                "visual_confirmation_required": True,
                "release_acceptance": False,
            },
            "warnings": [
                "DEGRADED SHOWCASE: telemetry_complete=false; logical receivers "
                "2 and 3 have no receiver/display proof and require direct visual observation."
            ],
            "artifact_policy": {
                "cached_artifact_operations_allowed": False,
                "dynamic_module_operations_allowed": False,
                "compiled_background_only": True,
            },
            "duration_seconds_requested": self.config.duration_seconds,
        }
        controller = None
        transport = None
        publisher = None
        source = None
        failure = None
        cleanup_failures = []
        mutation_started = False
        try:
            controller = self.controller_factory()
            transport = (
                self.transport_factory(controller)
                if self.transport_factory is not None
                else DegradedHybridTransport(controller, sleeper=self.sleeper)
            )
            preflight = transport.preflight()
            report["preflight"] = preflight
            before = transport.host_stats(UNVERIFIED_DEVICES)

            publisher = ReceiverSparsePublisher(
                transport,
                lease_ms=self.config.lease_ms,
                renewal_interval_seconds=min(1.0, self.config.lease_ms / 2000.0),
                repair_interval_seconds=max(2.0, self.config.duration_seconds + 1.0),
                monotonic=self.clock,
                session_factory=lambda: self.session_factory(16),
            )
            context = self._context(publisher)
            # Keep an identity/topology rejection observation-only.  Once this
            # flag is set, every exit path must reclaim host ownership and
            # restore the exact persisted desired state.
            mutation_started = True
            report["identity_configuration"] = (
                transport.configure_logical_identities()
            )
            transport.start_local_background(
                context,
                component_id=COMPILED_RAINBOW_COMPONENT_ID,
                preferred_cadence_hz=self.config.background_cadence_hz,
                common_seed=self.config.common_seed,
            )
            source = self.frame_source_factory(transport)
            source.start()

            started = self.clock()
            minimum_deadline = started + self.config.duration_seconds
            interval = 1.0 / self.config.foreground_poll_hz
            frame_count = 0
            published_calls = 0
            challenge = self.challenge_factory(16)
            if not isinstance(challenge, str) or not challenge:
                raise ShowcaseFailure("confirmation challenge factory returned no challenge")
            confirmation_poll = None
            confirmation_result = None
            confirmation_poll_interval = None
            confirmation_remaining = None
            initial_host_evidence_checked = False
            while True:
                now = self.clock()
                frame = source.render(max(0.0, now - started), frame_count)
                scene_time_us = min(
                    0xFFFFFFFFFFFFFFFF, int(max(0.0, now - started) * 1_000_000)
                )
                if not publisher.publish_frame(
                    frame,
                    scene_revision=context.scene_revision,
                    scene_epoch=context.scene_epoch,
                    base_revision=context.scene_revision,
                    present_at_scene_time_us=scene_time_us,
                    now=now,
                ):
                    raise ShowcaseFailure(
                        publisher.get_status().get("last_error")
                        or "sparse foreground publication failed"
                    )
                if not initial_host_evidence_checked:
                    initial_host_evidence = evaluate_write_only_host_evidence(
                        before, transport.host_stats(UNVERIFIED_DEVICES)
                    )
                    report["initial_write_only_host_evidence"] = (
                        initial_host_evidence
                    )
                    if not initial_host_evidence["passed"]:
                        raise ShowcaseFailure(
                            "; ".join(initial_host_evidence["failures"])
                        )
                    initial_host_evidence_checked = True
                published_calls += 1
                frame_count += 1
                if confirmation_poll is None:
                    (
                        confirmation_poll,
                        exchange_mode,
                        confirmation_poll_interval,
                        confirmation_remaining,
                    ) = self._begin_confirmation_exchange(challenge)
                    report["confirmation_exchange"] = {
                        "mode": exchange_mode,
                        "challenge_published_while_scene_active": True,
                        "minimum_visible_duration_seconds": (
                            self.config.duration_seconds
                        ),
                        "poll_interval_seconds": confirmation_poll_interval,
                        "timeout_remaining_cap_available": bool(
                            confirmation_remaining is not None
                        ),
                    }
                if confirmation_result is None:
                    candidate = confirmation_poll()
                    if candidate is not None:
                        confirmation_result = validate_visual_confirmation(
                            candidate, challenge
                        )
                        report["visual_confirmation"] = confirmation_result
                        report["confirmation_exchange"][
                            "confirmed_elapsed_seconds"
                        ] = round(self.clock() - started, 3)

                current = self.clock()
                if confirmation_result is not None and current >= minimum_deadline:
                    visibility = transport.foreground_visibility()
                    report["foreground_visibility_at_confirmation"] = visibility
                    report["confirmation_exchange"][
                        "pass_boundary_elapsed_seconds"
                    ] = round(self.clock() - started, 3)
                    if not visibility["passed"]:
                        raise ShowcaseFailure(
                            "foreground visibility at pass boundary: "
                            + "; ".join(visibility["failures"])
                        )
                    break
                sleep_caps = [interval]
                publisher_status = publisher.get_status()
                last_lease_at = publisher_status.get("last_lease_at")
                renewal_interval = publisher_status.get(
                    "renewal_interval_seconds"
                )
                if (
                    isinstance(last_lease_at, (int, float))
                    and not isinstance(last_lease_at, bool)
                    and isinstance(renewal_interval, (int, float))
                    and not isinstance(renewal_interval, bool)
                ):
                    sleep_caps.append(max(
                        0.0,
                        float(last_lease_at) + float(renewal_interval) - current,
                    ))
                if confirmation_result is None:
                    if confirmation_poll_interval is not None:
                        sleep_caps.append(confirmation_poll_interval)
                    if confirmation_remaining is not None:
                        remaining = confirmation_remaining()
                        if (
                            isinstance(remaining, bool)
                            or not isinstance(remaining, (int, float))
                            or not math.isfinite(float(remaining))
                            or float(remaining) < 0.0
                        ):
                            raise ShowcaseFailure(
                                "confirmation remaining time is invalid"
                            )
                        sleep_caps.append(float(remaining))
                if confirmation_result is not None:
                    sleep_caps.append(
                        max(0.0, minimum_deadline - current)
                    )
                sleep_seconds = min(sleep_caps)
                if sleep_seconds <= 0:
                    continue
                before_sleep = self.clock()
                self.sleeper(sleep_seconds)
                if self.clock() <= before_sleep:
                    raise ShowcaseFailure("showcase clock did not advance after sleep")

            report["duration_seconds_actual"] = round(self.clock() - started, 3)
            after = transport.host_stats(UNVERIFIED_DEVICES)
            host_evidence = evaluate_write_only_host_evidence(before, after)
            report["write_only_host_evidence"] = host_evidence
            if not host_evidence["passed"]:
                raise ShowcaseFailure("; ".join(host_evidence["failures"]))
            report["publisher"] = publisher.get_status()
            report["foreground_poll_calls"] = published_calls
            report["passed"] = True
        except Exception as exc:
            failure = str(exc)
        finally:
            if source is not None:
                try:
                    source.stop()
                except Exception as exc:
                    cleanup_failures.append(f"clock source stop: {exc}")
                try:
                    source.cleanup()
                except Exception as exc:
                    cleanup_failures.append(f"clock source cleanup: {exc}")
            if publisher is not None:
                try:
                    publisher.close(clear=False)
                except Exception as exc:
                    cleanup_failures.append(f"publisher close: {exc}")
            if mutation_started and transport is not None:
                try:
                    transport.set_all_pixels(self.restoration.complete_host_frame)
                    report["complete_host_frame_restored"] = True
                except Exception as exc:
                    report["complete_host_frame_restored"] = False
                    cleanup_failures.append(f"complete host frame restore: {exc}")
            else:
                report["complete_host_frame_restored"] = False
                if transport is not None:
                    report["preflight_non_mutating"] = True
            if mutation_started:
                try:
                    self.restore_desired_display(
                        deepcopy(dict(self.restoration.desired_display))
                    )
                    report["desired_display_restored"] = True
                except Exception as exc:
                    report["desired_display_restored"] = False
                    cleanup_failures.append(f"desired display restore: {exc}")
            else:
                report["desired_display_restored"] = False
            if controller is not None:
                try:
                    controller.close()
                except Exception as exc:
                    cleanup_failures.append(f"controller close: {exc}")

        if failure:
            report["failure"] = failure
        if cleanup_failures:
            report["cleanup_failures"] = cleanup_failures
        report["passed"] = bool(report["passed"] and not cleanup_failures)
        return report


def _atomic_restore_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the explicitly degraded Phase 3B0 four-wall showcase. The "
            "controller service must already be stopped; this tool never flashes, "
            "installs, uploads, or mutates cached receiver artifacts."
        )
    )
    parser.add_argument("--desired-display-state", type=Path, required=True)
    parser.add_argument("--restore-frame-npy", type=Path, required=True)
    parser.add_argument("--confirmation-challenge", type=Path, required=True)
    parser.add_argument("--confirmation-response", type=Path, required=True)
    parser.add_argument("--confirmation-timeout", type=float, default=60.0)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--foreground-poll-hz", type=float, default=20.0)
    parser.add_argument("--background-cadence-hz", type=int, default=30)
    parser.add_argument("--common-seed", type=lambda text: int(text, 0), default=0x3B00CAFE)
    parser.add_argument(
        "--vibe", choices=("neutral", "quiet", "cozy", "vivid", "celebration"),
        default="cozy",
    )
    args = parser.parse_args()

    raw_state = args.desired_display_state.read_bytes()
    desired_state = json.loads(raw_state.decode("utf-8"))
    frame = np.load(args.restore_frame_npy, allow_pickle=False)
    restoration = RestorationSnapshot(desired_state, frame)
    confirmation = FileVisualConfirmation(
        args.confirmation_challenge,
        args.confirmation_response,
        timeout=args.confirmation_timeout,
    )
    config = ShowcaseConfig(
        duration_seconds=args.duration,
        foreground_poll_hz=args.foreground_poll_hz,
        background_cadence_hz=args.background_cadence_hz,
        common_seed=args.common_seed,
        vibe_id=args.vibe,
    )

    def controller_factory() -> MultiDeviceLEDController:
        return MultiDeviceLEDController(
            num_devices=4,
            strips_per_device=LOCAL_STRIPS,
            leds_per_strip=LEDS_PER_STRIP,
            device_map=list(EXPECTED_DEVICE_MAP),
        )

    result = Phase3BDegradedShowcase(
        config,
        restoration,
        controller_factory=controller_factory,
        restore_desired_display=lambda _state: _atomic_restore_bytes(
            args.desired_display_state, raw_state
        ),
        confirmation_provider=confirmation,
    ).run()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

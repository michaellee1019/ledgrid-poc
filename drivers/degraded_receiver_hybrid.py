"""Explicit degraded receiver-hybrid transport for the installed wall.

This module is the one production exception to the normal all-receiver
acknowledgement contract.  Logical receivers 0 and 1 must provide exact
status-v4 capability/identity telemetry.  Logical receivers 2 and 3 must have
the installed SPI1 no-return-path shape and receive only pre-serialized,
allowlisted commands.  Nothing in this module treats an outbound transfer as
receiver or display proof.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from animation.core.receiver_presentation import (
    ReceiverPresentationContext,
    encode_presentation_context_begin,
    encode_presentation_context_commit,
    encode_presentation_context_set,
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


DEGRADED_SPI1_TRANSPORT_POLICY = "degraded_spi1_01_readable"
READABLE_DEVICES = (0, 1)
UNVERIFIED_DEVICES = (2, 3)
EXPECTED_DEVICE_MAP = ((0, 0), (0, 1), (1, 1), (1, 0))
DEFAULT_PHYSICAL_LANE_ORDER = (0, 1, 2, 3)
EXPECTED_STATUS_VERSION = 4
WRITE_ONLY_FOREGROUND_SETTLE_SECONDS = 0.050
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


class DegradedReceiverHybridError(RuntimeError):
    """The explicit degraded transport could not prove its readable subset."""


def _integer(status: Mapping[str, Any], key: str) -> int:
    value = status.get(key, 0)
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _write_only_status(status: Any) -> bool:
    return bool(
        isinstance(status, Mapping)
        and status.get("receiver_status_seen") is False
        and _integer(status, "receiver_status_version") == 0
        and _integer(status, "receiver_capabilities") == 0
        and status.get("receiver_logical_device") is None
    )


def evaluate_degraded_receiver_topology(statuses: Any) -> dict[str, Any]:
    """Accept only the installed readable-0/1, write-only-2/3 topology."""

    failures: list[str] = []
    receivers: dict[str, Any] = {}
    if not isinstance(statuses, Sequence) or isinstance(statuses, (str, bytes)):
        statuses = ()
    if len(statuses) != 4:
        failures.append(
            f"receiver telemetry has {len(statuses)} devices; expected exactly 4"
        )

    for logical_id in range(min(4, len(statuses))):
        status = statuses[logical_id]
        if logical_id in READABLE_DEVICES:
            device_failures: list[str] = []
            if not isinstance(status, Mapping):
                device_failures.append("status is unavailable")
            else:
                if status.get("receiver_status_seen") is not True:
                    device_failures.append("receiver_status_seen is not true")
                version = _integer(status, "receiver_status_version")
                if version != EXPECTED_STATUS_VERSION:
                    device_failures.append(
                        f"status version is {version}; expected {EXPECTED_STATUS_VERSION}"
                    )
                capabilities = _integer(status, "receiver_capabilities")
                if capabilities != EXPECTED_CAPABILITIES:
                    device_failures.append(
                        f"capabilities are 0x{capabilities:08x}; expected "
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

    return {
        "passed": not failures,
        "failures": failures,
        "receivers": receivers,
        "transport_policy": DEGRADED_SPI1_TRANSPORT_POLICY,
        "telemetry_complete": False,
        "readable_devices": list(READABLE_DEVICES),
        "unverified_devices": list(UNVERIFIED_DEVICES),
        "release_acceptance": False,
    }


class DegradedReceiverHybridController:
    """Controller facade for the one explicitly supported degraded topology."""

    def __init__(
        self,
        controller: Any,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        physical_lane_order: Sequence[int] = DEFAULT_PHYSICAL_LANE_ORDER,
    ) -> None:
        self.controller = controller
        self.devices = list(getattr(controller, "devices", ()))
        self.num_devices = getattr(controller, "num_devices", len(self.devices))
        self.strips_per_device = getattr(controller, "strips_per_device", None)
        self.leds_per_strip = getattr(controller, "leds_per_strip", None)
        self.strip_count = getattr(controller, "strip_count", None)
        self.total_leds = getattr(controller, "total_leds", None)
        self.leds_per_device = LOCAL_PIXELS
        self.inline_show = getattr(controller, "inline_show", True)
        self.debug = getattr(controller, "debug", False)
        self.physical_lane_order = self._normalize_physical_lane_order(
            physical_lane_order
        )
        self._physical_lane_by_logical = tuple(
            self.physical_lane_order.index(logical_id)
            for logical_id in range(4)
        )
        self._validate_installed_controller()
        self._sleeper = sleeper
        self._monotonic_ns = monotonic_ns
        self._session: Optional[bytes] = None
        self._generation = 0
        self._scene_revision: Optional[int] = None
        self._context: Optional[ReceiverPresentationContext] = None
        self._foreground_binding: Optional[dict[str, int]] = None
        self._expected_alpha_coverage = (0, 0, 0, 0)
        self._coverage_checks = 0
        self._identity_configuration: Optional[dict[str, Any]] = None
        self._status: dict[str, Any] = {}
        self._record_status(state="initialized", operation="none", operational=False)

    @staticmethod
    def _normalize_physical_lane_order(
        value: Sequence[int],
    ) -> tuple[int, int, int, int]:
        if isinstance(value, (str, bytes)) or len(value) != 4:
            raise DegradedReceiverHybridError(
                "physical lane order must contain four logical receiver ids"
            )
        if any(type(item) is not int for item in value):
            raise DegradedReceiverHybridError(
                "physical lane order values must be integers"
            )
        normalized = tuple(value)
        if set(normalized) != {0, 1, 2, 3}:
            raise DegradedReceiverHybridError(
                "physical lane order must be a permutation of 0,1,2,3"
            )
        return normalized

    def _physical_lane(self, logical_id: int) -> int:
        return self._physical_lane_by_logical[logical_id]

    def _global_strip_offset(self, logical_id: int) -> int:
        return self._physical_lane(logical_id) * LOCAL_STRIPS

    def _wall_slice(self, logical_id: int) -> slice:
        start = self._physical_lane(logical_id) * LOCAL_PIXELS
        return slice(start, start + LOCAL_PIXELS)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.controller, name)

    def _validate_installed_controller(self) -> None:
        if (
            self.num_devices != 4
            or len(self.devices) != 4
            or self.strips_per_device != LOCAL_STRIPS
            or self.leds_per_strip != LEDS_PER_STRIP
            or self.strip_count != WALL_STRIPS
            or self.total_leds != WALL_PIXELS
        ):
            raise DegradedReceiverHybridError(
                "controller geometry is not the installed 4 x 8 x 138 wall"
            )
        device_map = tuple(getattr(self.controller, "device_map", ()))
        if device_map != EXPECTED_DEVICE_MAP:
            raise DegradedReceiverHybridError(
                f"controller device map is {device_map!r}; expected "
                f"{EXPECTED_DEVICE_MAP!r}"
            )
        observed = tuple(
            (getattr(device, "bus", None), getattr(device, "device", None))
            for device in self.devices
        )
        if observed != EXPECTED_DEVICE_MAP:
            raise DegradedReceiverHybridError(
                f"controller device objects route to {observed!r}; expected "
                f"{EXPECTED_DEVICE_MAP!r}"
            )

    def _lock(self):
        getter = getattr(self.controller, "_controller_lock", None)
        return getter() if callable(getter) else contextlib.nullcontext()

    def _record_status(self, **updates: Any) -> None:
        if updates.get("state") not in {"degraded", "failed"}:
            updates.setdefault("error", None)
        self._status.update(updates)
        self._status.update({
            "transport_policy": DEGRADED_SPI1_TRANSPORT_POLICY,
            "telemetry_complete": False,
            "readable_devices": list(READABLE_DEVICES),
            "unverified_devices": list(UNVERIFIED_DEVICES),
            "degraded": True,
            "healthy": False,
            "fallback_active": False,
            "release_acceptance": False,
            "physical_lane_order": list(self.physical_lane_order),
            "physical_lane_by_logical_receiver": {
                str(logical_id): self._physical_lane(logical_id)
                for logical_id in range(4)
            },
        })

    @staticmethod
    def _fresh_status(device: Any) -> Mapping[str, Any]:
        status = None
        # Drain both queued replies plus a command-side response already in
        # flight; this matches the strict physical canary's freshness floor.
        for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 2):
            status = device.query_receiver_status()
        return dict(status) if isinstance(status, Mapping) else {}

    def preflight(self) -> dict[str, Any]:
        statuses = []
        for index, device in enumerate(self.devices):
            if index in READABLE_DEVICES:
                statuses.append(self._fresh_status(device))
            else:
                # A write-only link cannot produce a fresh receiver response.
                # Inspect its exact host-observed no-return shape without
                # sending a burst of meaningless STATUS_QUERY transfers.
                getter = getattr(device, "get_stats", None)
                statuses.append(getter() if callable(getter) else {})
        result = evaluate_degraded_receiver_topology(statuses)
        if not result["passed"]:
            raise DegradedReceiverHybridError(
                "topology preflight: " + "; ".join(result["failures"])
            )
        return result

    @staticmethod
    def _require_ack(
        status: Any, stage: str, logical_id: int, *, overlay: bool = False
    ) -> None:
        if (
            not isinstance(status, Mapping)
            or _integer(status, "receiver_last_result") != RESULT_OK
        ):
            raise DegradedReceiverHybridError(
                f"receiver {logical_id} did not acknowledge {stage}"
            )
        if (
            overlay
            and status.get("receiver_overlay_operation_result")
            not in OVERLAY_RESULT_OK
        ):
            raise DegradedReceiverHybridError(
                f"receiver {logical_id} rejected {stage}"
            )

    def _write_only_packet(self, device: Any, stage: str, payload: bytes) -> None:
        test_hook = getattr(device, "write_only_packet", None)
        if callable(test_hook):
            test_hook(stage, bytes(payload))
        else:
            transfer = getattr(device, "_xfer", None)
            if not callable(transfer):
                raise DegradedReceiverHybridError(
                    f"write-only receiver lacks raw transport for {stage}"
                )
            transfer(bytes(payload))
        # The two-deep SPI slave queue cannot advertise readiness on the
        # installed no-MISO lane. Foreground session/begin/batch/commit work
        # performs CRC, digest, staging, and authority updates before the
        # receiver can safely accept the next transaction, so give that whole
        # command family a conservative settle window. Other small controls
        # retain the established 1 ms pacing.
        self._sleeper(
            WRITE_ONLY_FOREGROUND_SETTLE_SECONDS
            if stage.startswith("foreground ")
            else COMMAND_ACK_POLL_INTERVAL_SECONDS
        )

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
        devices: dict[str, Any] = {}
        for logical_id, device in enumerate(self.devices):
            packet = self._identity_packet(logical_id)
            if logical_id in READABLE_DEVICES:
                command = getattr(device, "_command_status", None)
                if not callable(command):
                    raise DegradedReceiverHybridError(
                        f"receiver {logical_id} lacks exact CONFIG acknowledgement path"
                    )
                status = command(
                    packet, required_status_version=EXPECTED_STATUS_VERSION
                )
                self._require_ack(status, "logical identity CONFIG", logical_id)
                expected = {
                    "receiver_status_version": EXPECTED_STATUS_VERSION,
                    "receiver_logical_device": logical_id,
                    "receiver_last_processed_command": CMD_CONFIG,
                }
                for key, value in expected.items():
                    if status.get(key) != value:
                        raise DegradedReceiverHybridError(
                            f"receiver {logical_id} CONFIG reported {key}="
                            f"{status.get(key)!r}; expected {value!r}"
                        )
                devices[str(logical_id)] = {
                    "receiver_acknowledged": True,
                    "logical_identity_verified": True,
                    "logical_device": logical_id,
                    "wire_bytes": len(packet) + CRC_BYTES,
                }
            else:
                self._write_only_packet(device, "logical identity CONFIG", packet)
                devices[str(logical_id)] = {
                    "receiver_acknowledged": False,
                    "logical_identity_verified": False,
                    "logical_device_requested": logical_id,
                    "wire_bytes": len(packet) + CRC_BYTES,
                    "warning": "outbound-only CONFIG; identity remains unverified",
                }
        result = {
            "passed": True,
            "telemetry_complete": False,
            "devices": devices,
            "unverified_devices": list(UNVERIFIED_DEVICES),
        }
        self._identity_configuration = result
        self._record_status(
            operation="logical_identity_config", identity_configuration=result
        )
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

    def _mark_receiver_owned(self) -> None:
        self.controller._local_background_active = True
        self.controller._display_ownership_known = True
        self.controller._local_background_context_digest = (
            None if self._context is None else self._context.context_digest.hex()
        )
        self.controller._sparse_overlay_session_id = self._session
        self.controller._sparse_overlay_generation = self._generation

    def _mark_host_owned(self) -> None:
        self.controller._local_background_active = False
        self.controller._display_ownership_known = True
        self.controller._local_background_context_digest = None
        self.controller._local_background_parameters = {}
        self.controller._sparse_overlay_session_id = None
        self.controller._sparse_overlay_generation = 0
        self.controller._sparse_overlay_snapshot_digest = None

    def _stage_context(self, context: ReceiverPresentationContext) -> None:
        begin = encode_presentation_context_begin(context)
        setting = encode_presentation_context_set(context)
        commit = encode_presentation_context_commit(context)
        for index in range(4):
            self._invoke(
                index,
                "presentation begin",
                "begin_presentation_context",
                begin,
                kwargs={"context": context},
            )
            self._invoke(
                index,
                "presentation set",
                "set_presentation_context",
                setting,
                kwargs={"context": context},
            )
        anchor = self._monotonic_ns()
        for index in range(4):
            self._invoke(
                index,
                "presentation commit",
                "commit_presentation_context",
                commit,
                kwargs={
                    "context": context,
                    "host_monotonic_anchor_ns": anchor,
                },
            )

    def _verify_context(
        self, context: ReceiverPresentationContext, *, include_base: bool
    ) -> dict[str, Any]:
        readable: dict[str, Any] = {}
        for index in READABLE_DEVICES:
            status = self._fresh_status(self.devices[index])
            expected = {
                "receiver_status_version": EXPECTED_STATUS_VERSION,
                "receiver_logical_device": index,
                "receiver_active_scene_revision": context.scene_revision,
                "receiver_active_context_digest": context.context_digest.hex(),
                "receiver_active_session_id": context.controller_session_id.hex(),
            }
            if include_base:
                expected["receiver_base_mode"] = BASE_LOCAL_BACKGROUND
            for key, value in expected.items():
                if status.get(key) != value:
                    raise DegradedReceiverHybridError(
                        f"receiver {index} reported {key}={status.get(key)!r}; "
                        f"expected {value!r}"
                    )
            readable[str(index)] = {
                "status_version": EXPECTED_STATUS_VERSION,
                "logical_device": index,
                "context_digest": context.context_digest.hex(),
                "context_exact": True,
            }
        return readable

    def start_local_background(
        self,
        context: ReceiverPresentationContext,
        *,
        component_id: int,
        preferred_cadence_hz: int,
        common_seed: int,
    ) -> bool:
        if component_id != COMPILED_RAINBOW_COMPONENT_ID:
            raise DegradedReceiverHybridError(
                "only the compiled static rainbow is allowed"
            )
        packets = [
            LEDController.serialize_local_background_start(
                component_id=component_id,
                preferred_cadence_hz=preferred_cadence_hz,
                global_strip_offset=self._global_strip_offset(index),
                common_seed=common_seed,
                scene_epoch=context.scene_epoch,
            )
            for index in range(4)
        ]
        with self._lock():
            preflight = self.preflight()
            identities = self.configure_logical_identities()
            # From the first presentation mutation onward, conservatively route
            # any host frame through this facade's all-four takeover even if a
            # later command raises before local start can be verified.
            self._context = context
            self._session = None
            self._generation = 0
            self._scene_revision = context.scene_revision
            self._foreground_binding = None
            self._mark_receiver_owned()
            self._stage_context(context)
            for index, packet in enumerate(packets):
                self._invoke(
                    index,
                    "local background start",
                    "start_local_background",
                    packet,
                    kwargs={
                        "component_id": component_id,
                        "preferred_cadence_hz": preferred_cadence_hz,
                        "global_strip_offset": self._global_strip_offset(index),
                        "common_seed": common_seed,
                        "scene_epoch": context.scene_epoch,
                    },
                )
            readable = self._verify_context(context, include_base=True)
            for index in READABLE_DEVICES:
                status = self._fresh_status(self.devices[index])
                expected = {
                    "receiver_component_id": component_id,
                    "receiver_declared_cadence_hz": preferred_cadence_hz,
                    "receiver_common_seed": common_seed,
                    "receiver_scene_epoch": context.scene_epoch,
                    "receiver_global_strip_offset": self._global_strip_offset(index),
                }
                for key, value in expected.items():
                    if status.get(key) != value:
                        raise DegradedReceiverHybridError(
                            f"receiver {index} reported {key}={status.get(key)!r}; "
                            f"expected {value!r}"
                        )
            self.controller._local_background_parameters = {
                "preferred_cadence_hz": preferred_cadence_hz,
                "common_seed": common_seed,
            }
            self._mark_receiver_owned()
            self._record_status(
                state="active",
                operation="local_background_start",
                operational=False,
                context_digest=context.context_digest.hex(),
                topology_preflight=preflight,
                identity_configuration=identities,
                readable_receivers=readable,
            )
        return True

    def update_presentation_context(
        self, context: ReceiverPresentationContext
    ) -> bool:
        with self._lock():
            if self._context is None:
                raise DegradedReceiverHybridError(
                    "presentation context requires an active local background"
                )
            self._stage_context(context)
            self._context = context
            readable = self._verify_context(context, include_base=True)
            self._session = None
            self._generation = 0
            self._scene_revision = context.scene_revision
            self._expected_alpha_coverage = (0, 0, 0, 0)
            self._foreground_binding = None
            self._mark_receiver_owned()
            self._record_status(
                state="foreground_repair_required",
                operation="presentation_context_update",
                operational=False,
                context_digest=context.context_digest.hex(),
                readable_receivers=readable,
            )
        return True

    def update_local_background_params(
        self, *, preferred_cadence_hz: int, common_seed: int
    ) -> bool:
        packets = [
            LEDController.serialize_local_background_params(
                preferred_cadence_hz=preferred_cadence_hz,
                global_strip_offset=self._global_strip_offset(index),
                common_seed=common_seed,
            )
            for index in range(4)
        ]
        with self._lock():
            if self._context is None:
                raise DegradedReceiverHybridError("local background is not active")
            for index, packet in enumerate(packets):
                self._invoke(
                    index,
                    "local background parameters",
                    "update_local_background_params",
                    packet,
                    kwargs={
                        "preferred_cadence_hz": preferred_cadence_hz,
                        "global_strip_offset": self._global_strip_offset(index),
                        "common_seed": common_seed,
                    },
                )
            for index in READABLE_DEVICES:
                status = self._fresh_status(self.devices[index])
                expected = {
                    "receiver_base_mode": BASE_LOCAL_BACKGROUND,
                    "receiver_declared_cadence_hz": preferred_cadence_hz,
                    "receiver_global_strip_offset": self._global_strip_offset(index),
                    "receiver_common_seed": common_seed,
                }
                for key, value in expected.items():
                    if status.get(key) != value:
                        raise DegradedReceiverHybridError(
                            f"receiver {index} reported {key}={status.get(key)!r}; "
                            f"expected {value!r}"
                        )
            self.controller._local_background_parameters = {
                "preferred_cadence_hz": preferred_cadence_hz,
                "common_seed": common_seed,
            }
            self._record_status(state="active", operation="parameter_update")
        return True

    @staticmethod
    def _normalize_overlay(pixels: Any) -> np.ndarray:
        overlay = np.asarray(pixels)
        if overlay.shape != (WALL_PIXELS, 4):
            raise ValueError(
                f"foreground must have shape ({WALL_PIXELS}, 4), got {overlay.shape}"
            )
        if overlay.dtype != np.uint8:
            raise TypeError("foreground must use uint8 premultiplied RGBA")
        if np.any(overlay[:, :3] > overlay[:, 3:4]):
            raise ValueError("foreground RGB must not exceed alpha")
        return np.ascontiguousarray(overlay)

    @staticmethod
    def _dirty_ranges(ranges: Any) -> tuple[tuple[int, int], ...]:
        if ranges is None:
            raise ValueError("delta foreground requires dirty_ranges")
        result: list[tuple[int, int]] = []
        prior_end = 0
        for item in ranges:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError("dirty ranges must be (start, end) pairs")
            start, end = item
            if (
                isinstance(start, bool)
                or not isinstance(start, (int, np.integer))
                or isinstance(end, bool)
                or not isinstance(end, (int, np.integer))
            ):
                raise TypeError("dirty range bounds must be integers")
            first, last = int(start), int(end)
            if (
                first < 0
                or last > WALL_PIXELS
                or first >= last
                or (result and first < prior_end)
            ):
                raise ValueError(
                    "dirty ranges must be sorted, non-overlapping, and in bounds"
                )
            result.append((first, last))
            prior_end = last
        return tuple(result)

    @staticmethod
    def _local_patches(
        pixels: np.ndarray,
        logical_id: int,
        *,
        full_snapshot: bool,
        dirty_ranges: Optional[tuple[tuple[int, int], ...]],
        physical_lane_by_logical: Sequence[int] = DEFAULT_PHYSICAL_LANE_ORDER,
    ) -> list[tuple[int, np.ndarray]]:
        local_start = physical_lane_by_logical[logical_id] * LOCAL_PIXELS
        local = pixels[local_start:local_start + LOCAL_PIXELS]
        if full_snapshot:
            return [
                (start, local[start:start + MAX_RGBA_PIXELS_PER_BATCH_SPAN])
                for start in range(
                    0, LOCAL_PIXELS, MAX_RGBA_PIXELS_PER_BATCH_SPAN
                )
            ]
        patches: list[tuple[int, np.ndarray]] = []
        for start, end in dirty_ranges or ():
            first = max(local_start, start)
            last = min(local_start + LOCAL_PIXELS, end)
            for global_first in range(
                first, last, MAX_RGBA_PIXELS_PER_BATCH_SPAN
            ):
                count = min(
                    MAX_RGBA_PIXELS_PER_BATCH_SPAN, last - global_first
                )
                local_first = global_first - local_start
                patches.append(
                    (local_first, local[local_first:local_first + count])
                )
        return patches

    def _verify_foreground(
        self,
        *,
        session: bytes,
        generation: int,
        scene_revision: int,
        scene_epoch: int,
        base_revision: int,
        present_at_scene_time_us: int,
        expected_coverage: tuple[int, int, int, int],
        require_positive_lease: bool,
    ) -> dict[str, Any]:
        readable: dict[str, Any] = {}
        for index in READABLE_DEVICES:
            status = self._fresh_status(self.devices[index])
            expected = {
                "receiver_status_version": EXPECTED_STATUS_VERSION,
                "receiver_logical_device": index,
                "receiver_base_mode": BASE_LOCAL_BACKGROUND,
                "receiver_foreground_state": 2,
                "receiver_overlay_committed_generation": generation,
                "receiver_overlay_staged_generation": 0,
                "receiver_overlay_session_id": session.hex(),
                "receiver_foreground_scene_revision": scene_revision,
                "receiver_foreground_scene_epoch": scene_epoch,
                "receiver_foreground_base_revision": base_revision,
                "receiver_foreground_present_at_scene_time_us": (
                    present_at_scene_time_us
                ),
                "receiver_overlay_committed_coverage_pixels": (
                    expected_coverage[index]
                ),
            }
            for key, value in expected.items():
                if status.get(key) != value:
                    raise DegradedReceiverHybridError(
                        f"receiver {index} reported {key}={status.get(key)!r}; "
                        f"expected {value!r}"
                    )
            lease_remaining = _integer(
                status, "receiver_overlay_lease_remaining_ms"
            )
            if require_positive_lease and lease_remaining <= 0:
                raise DegradedReceiverHybridError(
                    f"receiver {index} reported an expired foreground lease"
                )
            readable[str(index)] = {
                "status_version": EXPECTED_STATUS_VERSION,
                "logical_device": index,
                "foreground_active": True,
                "session_exact": True,
                "generation_exact": True,
                "coverage_exact": True,
                "expected_alpha_pixels": expected_coverage[index],
                "observed_committed_coverage_pixels": expected_coverage[index],
                "lease_remaining_ms": lease_remaining,
            }
        return readable

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
                overlay[self._wall_slice(index), 3]
            ))
            for index in range(4)
        )
        missing = [
            index for index, count in enumerate(expected_coverage) if count <= 0
        ]
        if missing:
            raise DegradedReceiverHybridError(
                "foreground has zero expected alpha coverage on logical receiver(s) "
                + ", ".join(str(index) for index in missing)
            )
        session = LEDController._controller_session(controller_session_id)
        if generation <= prior_generation or generation >= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("foreground generation is not a safe successor")
        new_session = session != self._session
        if new_session and (not full_snapshot or prior_generation != 0):
            raise ValueError(
                "new foreground authority must begin with generation-zero snapshot"
            )
        if not new_session and prior_generation != self._generation:
            raise ValueError(
                "prior foreground generation disagrees with transport"
            )
        ranges = None if full_snapshot else self._dirty_ranges(dirty_ranges)
        kind = (
            OVERLAY_UPDATE_FULL_SNAPSHOT
            if full_snapshot else OVERLAY_UPDATE_DELTA
        )
        digest = hashlib.sha256(memoryview(overlay).cast("B")).digest()
        per_device = [
            self._local_patches(
                overlay,
                index,
                full_snapshot=full_snapshot,
                dirty_ranges=ranges,
                physical_lane_by_logical=self._physical_lane_by_logical,
            )
            for index in range(4)
        ]
        session_packet = LEDController.serialize_controller_session_begin(
            controller_session_id=session,
            desired_revision=scene_revision,
            authoritative_snapshot_digest=digest,
        )
        begin_packets = [
            LEDController.serialize_overlay_begin(
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
            )
            for patches in per_device
        ]
        patch_packets = [
            LEDController.serialize_overlay_patch_batches(
                controller_session_id=session,
                generation=generation,
                patches=patches,
                update_kind=kind,
            )
            for patches in per_device
        ]
        commit_packet = LEDController.serialize_overlay_commit(
            controller_session_id=session,
            generation=generation,
            scene_epoch=scene_epoch,
            base_revision=base_revision,
            present_at_scene_time_us=present_at_scene_time_us,
        )

        with self._lock():
            if new_session:
                for index in range(4):
                    self._invoke(
                        index,
                        "foreground session",
                        "begin_controller_session",
                        session_packet,
                        overlay=True,
                        kwargs={
                            "controller_session_id": session,
                            "desired_revision": scene_revision,
                            "authoritative_snapshot_digest": digest,
                        },
                    )
            for index, patches in enumerate(per_device):
                kwargs = {
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
                self._invoke(
                    index,
                    "foreground begin",
                    "begin_overlay",
                    begin_packets[index],
                    overlay=True,
                    kwargs=kwargs,
                )
                if index in READABLE_DEVICES:
                    statuses = self.devices[index].send_overlay_patches(
                        controller_session_id=session,
                        generation=generation,
                        patches=patches,
                        update_kind=kind,
                    )
                    for status in statuses:
                        self._require_ack(
                            status,
                            "foreground patch batch",
                            index,
                            overlay=True,
                        )
                else:
                    for packet in patch_packets[index]:
                        self._write_only_packet(
                            self.devices[index], "foreground patch batch", packet
                        )
                self._invoke(
                    index,
                    "foreground commit",
                    "commit_overlay",
                    commit_packet,
                    overlay=True,
                    kwargs={
                        "controller_session_id": session,
                        "generation": generation,
                        "scene_epoch": scene_epoch,
                        "base_revision": base_revision,
                        "present_at_scene_time_us": present_at_scene_time_us,
                    },
                )
            readable = self._verify_foreground(
                session=session,
                generation=generation,
                scene_revision=scene_revision,
                scene_epoch=scene_epoch,
                base_revision=base_revision,
                present_at_scene_time_us=present_at_scene_time_us,
                expected_coverage=expected_coverage,
                require_positive_lease=True,
            )
            self._session = session
            self._generation = generation
            self._scene_revision = scene_revision
            self._expected_alpha_coverage = expected_coverage
            self._foreground_binding = {
                "scene_revision": scene_revision,
                "scene_epoch": scene_epoch,
                "base_revision": base_revision,
                "present_at_scene_time_us": present_at_scene_time_us,
            }
            self._coverage_checks += 1
            self.controller._sparse_overlay_session_id = session
            self.controller._sparse_overlay_generation = generation
            self.controller._sparse_overlay_snapshot_digest = digest
            self._record_status(
                state="active",
                operation="foreground_publish",
                operational=True,
                foreground_generation=generation,
                expected_alpha_coverage_by_receiver={
                    str(index): count
                    for index, count in enumerate(expected_coverage)
                },
                coverage_checks=self._coverage_checks,
                readable_receivers=readable,
            )
        return True

    def renew_sparse_overlay(
        self, *, controller_session_id: bytes, generation: int, lease_ms: int
    ) -> bool:
        session = LEDController._controller_session(controller_session_id)
        if session != self._session or generation != self._generation:
            raise ValueError(
                "foreground renewal does not match active authority"
            )
        packet = LEDController.serialize_overlay_renew(
            controller_session_id=session,
            generation=generation,
            lease_ms=lease_ms,
        )
        with self._lock():
            for index in range(4):
                self._invoke(
                    index,
                    "foreground renew",
                    "renew_overlay",
                    packet,
                    overlay=True,
                    kwargs={
                        "controller_session_id": session,
                        "generation": generation,
                        "lease_ms": lease_ms,
                    },
                )
            assert self._foreground_binding is not None
            binding = self._foreground_binding
            readable = self._verify_foreground(
                session=session,
                generation=generation,
                scene_revision=binding["scene_revision"],
                scene_epoch=binding["scene_epoch"],
                base_revision=binding["base_revision"],
                present_at_scene_time_us=binding["present_at_scene_time_us"],
                expected_coverage=self._expected_alpha_coverage,
                require_positive_lease=True,
            )
            self._record_status(
                state="active",
                operation="foreground_renew",
                operational=True,
                readable_receivers=readable,
            )
        return True

    def clear_sparse_overlay(
        self,
        *,
        controller_session_id: bytes,
        generation: int,
        scene_revision: int,
    ) -> bool:
        session = LEDController._controller_session(controller_session_id)
        if session != self._session:
            raise ValueError("foreground clear does not match active authority")
        packet = LEDController.serialize_overlay_clear(
            controller_session_id=session,
            generation=generation,
            scene_revision=scene_revision,
        )
        with self._lock():
            for index in range(4):
                self._invoke(
                    index,
                    "foreground clear",
                    "clear_overlay",
                    packet,
                    overlay=True,
                    kwargs={
                        "controller_session_id": session,
                        "generation": generation,
                        "scene_revision": scene_revision,
                    },
                )
            readable: dict[str, Any] = {}
            for index in READABLE_DEVICES:
                status = self._fresh_status(self.devices[index])
                expected = {
                    "receiver_status_version": EXPECTED_STATUS_VERSION,
                    "receiver_logical_device": index,
                    "receiver_foreground_state": 0,
                    "receiver_overlay_committed_generation": generation,
                    "receiver_overlay_committed_coverage_pixels": 0,
                }
                for key, value in expected.items():
                    if status.get(key) != value:
                        raise DegradedReceiverHybridError(
                            f"receiver {index} reported {key}="
                            f"{status.get(key)!r}; expected {value!r}"
                        )
                readable[str(index)] = {
                    "status_version": EXPECTED_STATUS_VERSION,
                    "logical_device": index,
                    "foreground_cleared": True,
                    "generation_exact": True,
                    "coverage_exact": True,
                }
            self._generation = generation
            self._expected_alpha_coverage = (0, 0, 0, 0)
            self._foreground_binding = None
            self.controller._sparse_overlay_generation = generation
            self.controller._sparse_overlay_snapshot_digest = None
            self._record_status(
                state="active",
                operation="foreground_clear",
                operational=True,
                readable_receivers=readable,
            )
        return True

    def foreground_visibility(self) -> dict[str, Any]:
        if self._session is None or self._foreground_binding is None:
            return {
                "passed": False,
                "failures": ["no committed foreground authority"],
                "readable_receivers": {},
                "write_only_receivers": {},
            }
        readable: dict[str, Any] = {}
        failures: list[str] = []
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
                "session_exact": observed_session == self._session.hex(),
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
                "expected_session_id": self._session.hex(),
                "observed_session_id": observed_session,
                "expected_generation": self._generation,
                "observed_generation": observed_generation,
                "lease_remaining_ms": lease_remaining_ms,
                **proof,
            }
            failed = [name for name, passed in proof.items() if not passed]
            if failed:
                failures.append(
                    f"receiver {index} pass-boundary visibility failed "
                    + ", ".join(failed)
                )
        all_lanes_nonzero = all(
            count > 0 for count in self._expected_alpha_coverage
        )
        if not all_lanes_nonzero:
            failures.append(
                "expected foreground alpha coverage is not positive on every lane"
            )
        return {
            "passed": not failures,
            "failures": failures,
            "all_lanes_expected_nonzero": all_lanes_nonzero,
            "expected_alpha_coverage_by_receiver": {
                str(index): count
                for index, count in enumerate(self._expected_alpha_coverage)
            },
            "readable_receivers": readable,
            "write_only_receivers": {
                str(index): {
                    "expected_alpha_pixels": self._expected_alpha_coverage[index],
                    "receiver_telemetry_verified": False,
                    "physical_display_verified": False,
                }
                for index in UNVERIFIED_DEVICES
            },
            "coverage_checks": self._coverage_checks,
            "sampled_at_pass_boundary": True,
        }

    def host_stats(
        self, logical_ids: Sequence[int]
    ) -> dict[int, dict[str, int]]:
        return {
            index: {
                key: _integer(self.devices[index].get_stats(), key)
                for key in ("spi_transfers", "bytes_sent", "errors")
            }
            for index in logical_ids
        }

    @staticmethod
    def _normalize_host_frame(pixels: Any) -> np.ndarray:
        frame = np.asarray(pixels)
        if frame.shape != (WALL_PIXELS, 3):
            raise ValueError(
                f"complete host takeover must have shape ({WALL_PIXELS}, 3)"
            )
        if not np.issubdtype(frame.dtype, np.integer):
            raise TypeError("complete host takeover must use integer RGB")
        if np.any(frame < 0) or np.any(frame > 255):
            raise ValueError("complete host takeover RGB must fit uint8")
        return np.ascontiguousarray(frame, dtype=np.uint8)

    def set_all_pixels(self, pixels: Any) -> bool:
        frame = self._normalize_host_frame(pixels)
        failures: list[str] = []
        with self._lock():
            for index, device in enumerate(self.devices):
                local = np.ascontiguousarray(
                    frame[self._wall_slice(index)]
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
                            f"reported base mode "
                            f"{status.get('receiver_base_mode')!r}"
                        )
                except Exception as exc:
                    failures.append(f"receiver {index} takeover proof: {exc}")
            self.controller._logical_frames_sent = int(
                getattr(self.controller, "_logical_frames_sent", 0)
            ) + 1
            if failures:
                self.controller._display_ownership_known = False
                self._record_status(
                    state="degraded",
                    operation="set_all_takeover_failed",
                    operational=False,
                    error="; ".join(failures),
                )
                raise DegradedReceiverHybridError(
                    "complete host takeover failed: " + "; ".join(failures)
                )
            self._session = None
            self._generation = 0
            self._scene_revision = None
            self._context = None
            self._foreground_binding = None
            self._expected_alpha_coverage = (0, 0, 0, 0)
            self._mark_host_owned()
            self._record_status(
                state="host_full_scene",
                operation="set_all_takeover",
                operational=False,
                error=None,
                readable_receivers={
                    str(index): {
                        "status_version": EXPECTED_STATUS_VERSION,
                        "logical_device": index,
                        "host_full_scene": True,
                    }
                    for index in READABLE_DEVICES
                },
            )
        return True

    def set_frame(self, colors: Any, dirty_ranges: Any = None) -> Any:
        if self._context is not None or self._session is not None:
            return self.set_all_pixels(colors)
        # After the proven complete takeover, retain the underlying controller's
        # normal streaming path but present it in logical-device order. Dirty
        # ranges belong to physical wall coordinates and cannot be forwarded
        # across a non-identity permutation without remapping, so this explicit
        # degraded path sends a complete frame.
        frame = self._normalize_host_frame(colors)
        transport_order = np.empty_like(frame)
        for logical_id in range(4):
            destination = slice(
                logical_id * LOCAL_PIXELS, (logical_id + 1) * LOCAL_PIXELS
            )
            transport_order[destination] = frame[self._wall_slice(logical_id)]
        return self.controller.set_frame(transport_order, dirty_ranges=None)

    def clear(self) -> bool:
        return self.set_all_pixels(
            np.zeros((WALL_PIXELS, 3), dtype=np.uint8)
        )

    def get_stats(self) -> dict[str, Any]:
        getter = getattr(self.controller, "get_stats", None)
        raw = getter() if callable(getter) else {
            "devices": [device.get_stats() for device in self.devices],
            "aggregate": {
                "num_devices": self.num_devices,
                "total_leds": self.total_leds,
                "device_map": [
                    {
                        "logical_device": index,
                        "bus": bus,
                        "chip_select": chip_select,
                    }
                    for index, (bus, chip_select) in enumerate(EXPECTED_DEVICE_MAP)
                ],
            },
        }
        result = dict(raw) if isinstance(raw, Mapping) else {}
        aggregate = dict(result.get("aggregate", {}))
        aggregate["local_background"] = dict(self._status)
        aggregate["receiver_hybrid_transport_policy"] = (
            DEGRADED_SPI1_TRANSPORT_POLICY
        )
        aggregate["receiver_hybrid_telemetry_complete"] = False
        aggregate["receiver_hybrid_readable_devices"] = list(READABLE_DEVICES)
        aggregate["receiver_hybrid_unverified_devices"] = list(
            UNVERIFIED_DEVICES
        )
        aggregate["receiver_hybrid_release_acceptance"] = False
        result["aggregate"] = aggregate
        return result


__all__ = [
    "DEGRADED_SPI1_TRANSPORT_POLICY",
    "DegradedReceiverHybridController",
    "DegradedReceiverHybridError",
    "EXPECTED_CAPABILITIES",
    "EXPECTED_DEVICE_MAP",
    "EXPECTED_STATUS_VERSION",
    "READABLE_DEVICES",
    "UNVERIFIED_DEVICES",
    "WRITE_ONLY_FOREGROUND_SETTLE_SECONDS",
    "evaluate_degraded_receiver_topology",
]

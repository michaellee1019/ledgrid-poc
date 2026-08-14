#!/usr/bin/env python3
"""Strict Phase 3B sparse-foreground canary for one readable receiver.

The web controller/systemd service must already be stopped.  This runner never
flashes firmware, manages services, or addresses more than the explicitly
selected receiver.  Once receiver mutation starts it always attempts a complete
black host frame before closing the last available SPI path.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import secrets
import sys
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import resolve_vibe
from animation.core.receiver_presentation import ReceiverPresentationContext
from drivers.spi_controller import (
    CAPABILITY_EXPLICIT_BASE_OWNERSHIP,
    CAPABILITY_PRESENTATION_CONTEXT_V1,
    CAPABILITY_SPARSE_OVERLAY_BATCH_V1,
    CAPABILITY_SPARSE_OVERLAY_V1,
    CAPABILITY_STATIC_LOCAL_BACKGROUND,
    CAPABILITY_STATUS_V3,
    CMD_CONTROLLER_SESSION_BEGIN,
    CMD_LOCAL_BACKGROUND_START,
    CMD_OVERLAY_BEGIN,
    CMD_OVERLAY_COMMIT,
    CMD_OVERLAY_PATCH_BATCH,
    CMD_PING,
    CMD_PRESENTATION_CONTEXT_BEGIN,
    CMD_PRESENTATION_CONTEXT_COMMIT,
    CMD_PRESENTATION_CONTEXT_SET,
    CMD_SET_ALL,
    LEDController,
    MAX_RGBA_PIXELS_PER_BATCH_SPAN,
    OVERLAY_UPDATE_DELTA,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
    SPI_RESPONSE_QUEUE_DEPTH,
)


REQUIRED_CAPABILITIES = (
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
BASE_LOCAL_BACKGROUND = 1
BASE_HOST_FULL_SCENE = 2
FOREGROUND_CLEARED = 0
FOREGROUND_ACTIVE = 2
COMPONENT_STATIC_RAINBOW = 1
RESULT_OK = 1
OVERLAY_RESULT_OK = 1
OVERLAY_RESULT_LEASE_EXPIRED = 17
TRANSITION_LOCAL_START = 1
TRANSITION_HOST_TAKEOVER = 3

FAULT_COUNTERS = (
    ("receiver_local_missed_deadlines", "local cadence misses"),
    ("receiver_crc_errors", "CRC errors"),
    ("receiver_spi_queue_errors", "SPI queue errors"),
    ("receiver_display_errors", "display errors"),
)
TIMING_METRICS = (
    ("base_render_us", "receiver_last_local_render_us"),
    ("foreground_composite_us", "receiver_overlay_last_composite_us"),
    ("encode_us", "receiver_last_encode_us"),
    ("display_us", "receiver_last_show_us"),
)


class CanaryFailure(RuntimeError):
    """Acceptance failure that still triggers the complete-frame safety path."""


@dataclass(frozen=True)
class CanaryConfig:
    bus: int
    device: int
    logical_id: int
    disconnect_seconds: float = 5.0
    cadence_hz: int = 30
    common_seed: int = 0x3B0C4A7E
    lease_ms: int = 3_000
    observation_timeout_seconds: float = 1.0
    poll_interval_seconds: float = 0.005
    timing_sample_seconds: float = 0.5

    def __post_init__(self) -> None:
        for name in ("bus", "device"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            isinstance(self.logical_id, bool)
            or not isinstance(self.logical_id, int)
            or not 0 <= self.logical_id <= 3
        ):
            raise ValueError("logical_id must be between 0 and 3")
        if (
            isinstance(self.cadence_hz, bool)
            or not isinstance(self.cadence_hz, int)
            or not 1 <= self.cadence_hz <= 200
        ):
            raise ValueError("cadence_hz must be between 1 and 200")
        if (
            isinstance(self.common_seed, bool)
            or not isinstance(self.common_seed, int)
            or not 0 <= self.common_seed <= 0xFFFFFFFF
        ):
            raise ValueError("common_seed must fit in uint32")
        if (
            isinstance(self.lease_ms, bool)
            or not isinstance(self.lease_ms, int)
            or not 1 <= self.lease_ms <= 0xFFFFFFFF
        ):
            raise ValueError("lease_ms must be between 1 and 4294967295")
        for name in (
            "disconnect_seconds",
            "observation_timeout_seconds",
            "poll_interval_seconds",
            "timing_sample_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.disconnect_seconds * 1000.0 <= self.lease_ms:
            raise ValueError("disconnect_seconds must be longer than the foreground lease")
        if self.poll_interval_seconds >= self.observation_timeout_seconds:
            raise ValueError("poll_interval_seconds must be shorter than the timeout")
        if (
            (self.timing_sample_seconds + self.poll_interval_seconds) * 1000.0
            >= self.lease_ms
        ):
            raise ValueError(
                "timing sample window plus one poll must be shorter than the lease"
            )

    @property
    def global_strip_offset(self) -> int:
        return self.logical_id * LOCAL_STRIPS


def _int(status: Mapping[str, Any], key: str) -> int:
    try:
        return int(status.get(key, 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def evaluate_identity_status(status: Any, logical_id: int) -> list[str]:
    """Require the deliberately built status-v4 canary image exactly."""
    if not isinstance(status, Mapping):
        return ["receiver status is unavailable"]
    failures = []
    version = _int(status, "receiver_status_version")
    capabilities = _int(status, "receiver_capabilities")
    if version != 4:
        failures.append(f"receiver status version is {version}; expected exactly 4")
    if capabilities != REQUIRED_CAPABILITIES:
        failures.append(
            f"receiver capabilities are 0x{capabilities:08x}; "
            f"expected exact Phase 3B canary capabilities 0x{REQUIRED_CAPABILITIES:08x}"
        )
    if status.get("receiver_logical_device") != logical_id:
        failures.append(
            f"receiver logical identity is {status.get('receiver_logical_device')!r}; "
            f"expected {logical_id}"
        )
    return failures


def evaluate_exact_status(
    status: Any, expected: Mapping[str, Any], *, require_ok: bool = False
) -> list[str]:
    if not isinstance(status, Mapping):
        return ["receiver status is unavailable"]
    failures = []
    for key, expected_value in expected.items():
        if status.get(key) != expected_value:
            failures.append(
                f"{key} is {status.get(key)!r}; expected {expected_value!r}"
            )
    if require_ok and _int(status, "receiver_last_result") != RESULT_OK:
        failures.append(
            f"receiver_last_result is {_int(status, 'receiver_last_result')}; "
            f"expected {RESULT_OK}"
        )
    return failures


def evaluate_command_ack(
    status: Any,
    *,
    logical_id: int,
    command: int,
    overlay: bool = False,
) -> list[str]:
    failures = evaluate_identity_status(status, logical_id)
    failures.extend(evaluate_exact_status(
        status,
        {"receiver_last_processed_command": command},
        require_ok=True,
    ))
    if overlay and isinstance(status, Mapping):
        if _int(status, "receiver_overlay_operation_result") != OVERLAY_RESULT_OK:
            failures.append(
                "receiver_overlay_operation_result is "
                f"{_int(status, 'receiver_overlay_operation_result')}; "
                f"expected {OVERLAY_RESULT_OK}"
            )
    return failures


def expected_local_status(
    context: ReceiverPresentationContext, config: CanaryConfig
) -> dict[str, Any]:
    return {
        "receiver_status_version": 4,
        "receiver_capabilities": REQUIRED_CAPABILITIES,
        "receiver_logical_device": config.logical_id,
        "receiver_base_mode": BASE_LOCAL_BACKGROUND,
        "receiver_transition_reason": TRANSITION_LOCAL_START,
        "receiver_component_id": COMPONENT_STATIC_RAINBOW,
        "receiver_declared_cadence_hz": config.cadence_hz,
        "receiver_global_strip_offset": config.global_strip_offset,
        "receiver_common_seed": config.common_seed,
        "receiver_scene_epoch": context.scene_epoch,
        "receiver_active_scene_revision": context.scene_revision,
        "receiver_active_context_digest": context.context_digest.hex(),
        "receiver_vibe_revision": context.vibe.state.revision,
        "receiver_vibe_digest": context.vibe.state.resolved_profile_digest,
        "receiver_plant_modifier_revision": context.plant_revision,
        "receiver_plant_modifier_digest": context.plant_digest.hex(),
        "receiver_active_session_id": context.controller_session_id.hex(),
    }


def expected_foreground_status(
    context: ReceiverPresentationContext,
    config: CanaryConfig,
    *,
    generation: int,
    update_kind: int,
    patch_count: int,
    coverage_pixels: int,
) -> dict[str, Any]:
    return {
        **expected_local_status(context, config),
        "receiver_foreground_state": FOREGROUND_ACTIVE,
        "receiver_overlay_operation_result": OVERLAY_RESULT_OK,
        "receiver_overlay_update_kind": update_kind,
        "receiver_overlay_expected_patches": patch_count,
        "receiver_overlay_accepted_patches": patch_count,
        "receiver_overlay_committed_coverage_pixels": coverage_pixels,
        "receiver_overlay_committed_generation": generation,
        "receiver_overlay_staged_generation": 0,
        "receiver_foreground_scene_revision": context.scene_revision,
        "receiver_foreground_scene_epoch": context.scene_epoch,
        "receiver_foreground_base_revision": context.scene_revision,
        "receiver_foreground_present_at_scene_time_us": 0,
        "receiver_overlay_lease_ms": config.lease_ms,
        "receiver_overlay_session_id": context.controller_session_id.hex(),
    }


def evaluate_fault_deltas(before: Any, after: Any) -> list[str]:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return ["fault-counter status pair is unavailable"]
    failures = []
    for key, label in FAULT_COUNTERS:
        delta = _int(after, key) - _int(before, key)
        if delta < 0:
            failures.append(f"{key} regressed by {-delta}")
        elif delta != 0:
            failures.append(f"{label} increased by {delta}")
    return failures


def evaluate_timing_status(status: Any) -> list[str]:
    if not isinstance(status, Mapping):
        return ["receiver timing status is unavailable"]
    failures = []
    pairs = (
        ("receiver_last_local_render_us", "receiver_max_local_render_us", "base render"),
        (
            "receiver_overlay_last_composite_us",
            "receiver_overlay_max_composite_us",
            "foreground composite",
        ),
    )
    for last_key, max_key, label in pairs:
        last = _int(status, last_key)
        maximum = _int(status, max_key)
        if last <= 0:
            failures.append(f"last {label} duration is not positive")
        if maximum < last:
            failures.append(f"maximum {label} duration is below the last duration")
    for key, label in (
        ("receiver_last_encode_us", "encode"),
        ("receiver_last_show_us", "display"),
    ):
        if _int(status, key) <= 0:
            failures.append(f"last {label} duration is not positive")
    return failures


def evaluate_cadence_independence(
    before: Any,
    after: Any,
    *,
    expected_status: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove one foreground commit did not advance the compiled base."""
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return {"passed": False, "failures": ["cadence status pair is unavailable"]}
    failures = evaluate_exact_status(after, expected_status, require_ok=True)
    failures.extend(evaluate_fault_deltas(before, after))
    exact_deltas = {
        "receiver_local_frames_rendered": 0,
        "receiver_local_cadence_deadlines": 0,
        "receiver_last_frame_scene_time_us": 0,
        "receiver_overlay_composite_frames": 1,
        "receiver_overlay_commits": 1,
        "receiver_overlay_expirations": 0,
        "receiver_operation_sequence": 3,
    }
    observed = {}
    for key, expected_delta in exact_deltas.items():
        delta = _int(after, key) - _int(before, key)
        observed[key] = delta
        if delta != expected_delta:
            failures.append(
                f"{key} advanced by {delta}; expected exactly {expected_delta}"
            )
    failures.extend(evaluate_timing_status(after))
    lease_remaining = _int(after, "receiver_overlay_lease_remaining_ms")
    lease_ms = _int(after, "receiver_overlay_lease_ms")
    if not 0 < lease_remaining <= lease_ms:
        failures.append(
            f"foreground lease remaining is {lease_remaining}; expected 1..{lease_ms}"
        )
    return {"passed": not failures, "failures": failures, "deltas": observed}


def evaluate_expiry_window(
    before: Any,
    after: Any,
    *,
    elapsed_seconds: float,
    cadence_hz: int,
    expected_base_status: Mapping[str, Any],
    generation: int,
    session_id: bytes,
) -> dict[str, Any]:
    """Prove the lease clears only foreground while local playback continues."""
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return {"passed": False, "failures": ["expiry status pair is unavailable"]}
    expected = {
        **expected_base_status,
        "receiver_foreground_state": FOREGROUND_CLEARED,
        "receiver_overlay_operation_result": OVERLAY_RESULT_LEASE_EXPIRED,
        "receiver_overlay_update_kind": OVERLAY_UPDATE_DELTA,
        "receiver_overlay_expected_patches": 2,
        "receiver_overlay_accepted_patches": 2,
        "receiver_overlay_committed_coverage_pixels": 0,
        "receiver_overlay_committed_generation": generation,
        "receiver_overlay_staged_generation": 0,
        "receiver_foreground_scene_revision": 0,
        "receiver_foreground_scene_epoch": 0,
        "receiver_foreground_base_revision": 0,
        "receiver_foreground_present_at_scene_time_us": 0,
        "receiver_overlay_lease_ms": 0,
        "receiver_overlay_lease_remaining_ms": 0,
        "receiver_overlay_session_id": session_id.hex(),
        # LEDController performs one ownership-neutral PING when the SPI path
        # reopens after the deliberate disconnect.
        "receiver_last_processed_command": CMD_PING,
    }
    failures = evaluate_exact_status(after, expected, require_ok=True)
    failures.extend(evaluate_fault_deltas(before, after))

    expected_frames = elapsed_seconds * cadence_hz
    minimum_frames = max(1, math.floor(expected_frames * 0.80))
    maximum_frames = math.ceil(expected_frames * 1.20) + 2
    rendered = _int(after, "receiver_local_frames_rendered") - _int(
        before, "receiver_local_frames_rendered"
    )
    deadlines = _int(after, "receiver_local_cadence_deadlines") - _int(
        before, "receiver_local_cadence_deadlines"
    )
    for value, label in ((rendered, "rendered-frame"), (deadlines, "cadence-deadline")):
        if not minimum_frames <= value <= maximum_frames:
            failures.append(
                f"local {label} delta {value} is outside {minimum_frames}..{maximum_frames}"
            )
    if _int(after, "receiver_last_frame_scene_time_us") <= _int(
        before, "receiver_last_frame_scene_time_us"
    ):
        failures.append("local scene time did not advance after foreground expiry")
    commits = _int(after, "receiver_overlay_commits") - _int(
        before, "receiver_overlay_commits"
    )
    expirations = _int(after, "receiver_overlay_expirations") - _int(
        before, "receiver_overlay_expirations"
    )
    if commits != 0:
        failures.append(f"foreground commits advanced by {commits} during disconnect")
    if expirations != 1:
        failures.append(f"foreground expirations advanced by {expirations}; expected exactly 1")
    operation_delta = _int(after, "receiver_operation_sequence") - _int(
        before, "receiver_operation_sequence"
    )
    if operation_delta != 1:
        failures.append(
            f"operation sequence advanced by {operation_delta}; expected the one reopen PING"
        )
    composites = _int(after, "receiver_overlay_composite_frames") - _int(
        before, "receiver_overlay_composite_frames"
    )
    if not rendered <= composites <= rendered + 1:
        failures.append(
            f"composite-frame delta {composites} is not base delta {rendered} or expiry refresh"
        )
    failures.extend(evaluate_timing_status(after))
    return {
        "passed": not failures,
        "failures": failures,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "rendered_delta": rendered,
        "deadline_delta": deadlines,
        "composite_delta": composites,
        "expiration_delta": expirations,
    }


def evaluate_host_takeover(status: Any, logical_id: int) -> list[str]:
    failures = evaluate_identity_status(status, logical_id)
    failures.extend(evaluate_exact_status(
        status,
        {
            "receiver_base_mode": BASE_HOST_FULL_SCENE,
            "receiver_foreground_state": FOREGROUND_CLEARED,
            "receiver_transition_reason": TRANSITION_HOST_TAKEOVER,
            "receiver_last_processed_command": CMD_SET_ALL,
            "receiver_overlay_committed_coverage_pixels": 0,
            "receiver_overlay_committed_generation": 0,
            "receiver_overlay_staged_generation": 0,
            "receiver_foreground_scene_revision": 0,
            "receiver_foreground_scene_epoch": 0,
            "receiver_foreground_base_revision": 0,
            "receiver_foreground_present_at_scene_time_us": 0,
            "receiver_overlay_lease_ms": 0,
            "receiver_overlay_lease_remaining_ms": 0,
            "receiver_overlay_session_id": bytes(16).hex(),
        },
        require_ok=True,
    ))
    return failures


def summarize_timing_samples(
    samples: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    """Summarize positive on-device microsecond samples deterministically."""
    if not samples:
        raise ValueError("at least one displayed-frame timing sample is required")
    summary = {}
    for output_key, status_key in TIMING_METRICS:
        values = np.asarray([_int(sample, status_key) for sample in samples], dtype=np.float64)
        if np.any(values <= 0):
            raise ValueError(f"{status_key} samples must all be positive")
        percentiles = np.percentile(values, (50, 95, 99))
        summary[output_key] = {
            "mean": round(float(np.mean(values)), 3),
            "p50": round(float(percentiles[0]), 3),
            "p95": round(float(percentiles[1]), 3),
            "p99": round(float(percentiles[2]), 3),
            "max": round(float(np.max(values)), 3),
        }
    return summary


def make_foreground_frames() -> tuple[
    np.ndarray, np.ndarray, tuple[tuple[int, int], ...]
]:
    """Build the deterministic premultiplied-RGBA snapshot and movement delta."""
    snapshot = np.zeros((LOCAL_PIXELS, 4), dtype=np.uint8)
    snapshot[16:24] = (64, 32, 16, 64)
    # Opaque black is deliberately covered foreground, not transparency.
    snapshot[80:84] = (0, 0, 0, 255)
    delta = snapshot.copy()
    delta[16:24] = (0, 0, 0, 0)
    delta[80:87] = (0, 96, 32, 128)
    return snapshot, delta, ((16, 24), (80, 87))


def _snapshot_patches(frame: np.ndarray) -> tuple[tuple[int, np.ndarray], ...]:
    return tuple(
        (start, frame[start : start + MAX_RGBA_PIXELS_PER_BATCH_SPAN])
        for start in range(0, LOCAL_PIXELS, MAX_RGBA_PIXELS_PER_BATCH_SPAN)
    )


def _delta_patches(
    frame: np.ndarray, dirty_ranges: Sequence[tuple[int, int]]
) -> tuple[tuple[int, np.ndarray], ...]:
    return tuple((start, frame[start:end]) for start, end in dirty_ranges)


class SingleReceiverPhase3BCanary:
    def __init__(
        self,
        config: CanaryConfig,
        *,
        controller_factory: Optional[Callable[[], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sleeper: Callable[[float], None] = time.sleep,
        session_factory: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self.config = config
        self.clock = clock
        self.monotonic_ns = monotonic_ns
        self.sleeper = sleeper
        self.session_factory = session_factory
        self.controller_factory = controller_factory or self._open_controller
        self.black_frame = np.zeros((LOCAL_PIXELS, 3), dtype=np.uint8)

    def _open_controller(self):
        return LEDController(
            bus=self.config.bus,
            device=self.config.device,
            strips=LOCAL_STRIPS,
            leds_per_strip=LEDS_PER_STRIP,
            logical_device_id=self.config.logical_id,
        )

    @staticmethod
    def _fresh_status(controller) -> Mapping[str, Any]:
        status = None
        # A newly opened sparse-capable controller must first discover v4 in
        # the v3 prefix.  Four reads drain both prequeued v3 snapshots and
        # return a v4 snapshot produced after this observation began.
        for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 2):
            status = controller.query_receiver_status()
        if not isinstance(status, Mapping):
            raise CanaryFailure("receiver returned no status")
        return status

    @staticmethod
    def _require(stage: str, failures: Sequence[str]) -> None:
        if failures:
            raise CanaryFailure(f"{stage}: {'; '.join(failures)}")

    def _require_ack(
        self,
        stage: str,
        status: Any,
        command: int,
        *,
        controller: Any,
        overlay: bool = False,
    ) -> Mapping[str, Any]:
        # Non-overlay command helpers require only the v3 prefix and may return
        # the legacy-safe snapshot already queued before a short command.  The
        # strict canary still evaluates every acknowledgement against a fresh
        # v4 snapshot after the receiver has had enough transfers to publish it.
        if _int(status, "receiver_status_version") != 4:
            status = self._fresh_status(controller)
        self._require(stage, evaluate_command_ack(
            status,
            logical_id=self.config.logical_id,
            command=command,
            overlay=overlay,
        ))
        return status

    def _context(self) -> ReceiverPresentationContext:
        epoch = self.monotonic_ns()
        return ReceiverPresentationContext(
            controller_session_id=self.session_factory(16),
            scene_revision=epoch,
            scene_epoch=epoch,
            present_at_scene_time_us=50_000,
            vibe=resolve_vibe("cozy", revision=1),
            plant_modifiers=PlantModifierState.from_payload({
                "active": ["illuminate", "obstacle"],
                "strengths": {"illuminate": 0.5, "obstacle": 1.0},
            }),
            plant_revision=1,
        )

    def _wait_for_status(
        self,
        controller,
        predicate: Callable[[Mapping[str, Any]], bool],
        description: str,
    ) -> Mapping[str, Any]:
        deadline = self.clock() + self.config.observation_timeout_seconds
        latest = self._fresh_status(controller)
        while not predicate(latest):
            if self.clock() >= deadline:
                diagnostic_keys = (
                    "receiver_status_version",
                    "receiver_last_processed_command",
                    "receiver_operation_sequence",
                    "receiver_last_result",
                    "receiver_overlay_operation_result",
                    "receiver_base_mode",
                    "receiver_foreground_state",
                    "receiver_crc_errors",
                    "receiver_spi_queue_errors",
                    "receiver_display_errors",
                )
                diagnostic = ", ".join(
                    f"{key}={latest.get(key)!r}" for key in diagnostic_keys
                )
                raise CanaryFailure(
                    f"timed out waiting for {description}; last status: {diagnostic}"
                )
            self.sleeper(self.config.poll_interval_seconds)
            latest = self._fresh_status(controller)
        return latest

    def _black_takeover(self, controller) -> Mapping[str, Any]:
        controller.set_all_pixels(self.black_frame)
        # SET_ALL publishes through the receiver display task.  On hardware its
        # operation result can trail the first queued status snapshots even
        # though the full packet has already transferred successfully.
        status = self._wait_for_status(
            controller,
            lambda value: (
                _int(value, "receiver_base_mode") == BASE_HOST_FULL_SCENE
                and _int(value, "receiver_foreground_state")
                == FOREGROUND_CLEARED
                and _int(value, "receiver_last_processed_command") == CMD_SET_ALL
            ),
            "completed black complete-host-frame takeover",
        )
        self._require(
            "black complete-host-frame takeover",
            evaluate_host_takeover(status, self.config.logical_id),
        )
        return status

    def _collect_timing_window(
        self, controller, initial_status: Mapping[str, Any]
    ) -> tuple[dict[str, Any], Mapping[str, Any]]:
        """Collect bounded samples after completion-gated local frames advance.

        ``receiver_frames_displayed`` is the host-mailbox counter and remains
        static during receiver-local playback.  Firmware increments
        ``receiver_local_frames_rendered`` only after the physical driver reports
        completion for a due local-background frame, making it the correct local
        sampling trigger.
        """
        started = self.clock()
        deadline = started + self.config.timing_sample_seconds
        status = dict(initial_status)
        initial_remaining = _int(status, "receiver_overlay_lease_remaining_ms")
        required_lease_ms = int(math.ceil(
            (self.config.timing_sample_seconds + self.config.poll_interval_seconds)
            * 1000.0
        ))
        if initial_remaining < required_lease_ms:
            raise CanaryFailure(
                "timing window does not fit inside the active foreground lease: "
                f"need {required_lease_ms} ms, have {initial_remaining} ms"
            )

        initial_completed = _int(status, "receiver_local_frames_rendered")
        last_completed = initial_completed
        samples: list[Mapping[str, Any]] = []
        max_polls = math.ceil(
            self.config.timing_sample_seconds / self.config.poll_interval_seconds
        ) + 1
        polls = 0
        while polls < max_polls:
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            self.sleeper(min(self.config.poll_interval_seconds, remaining))
            observed = self._fresh_status(controller)
            polls += 1
            completed = _int(observed, "receiver_local_frames_rendered")
            if completed < last_completed:
                raise CanaryFailure(
                    "receiver_local_frames_rendered regressed in timing window"
                )
            if completed > last_completed:
                samples.append(dict(observed))
                last_completed = completed
            status = dict(observed)

        elapsed = self.clock() - started
        if self.clock() < deadline and polls >= max_polls:
            raise CanaryFailure("timing sample polling exhausted its bounded iteration limit")
        if not samples:
            raise CanaryFailure(
                "timing window observed no newly completed local receiver frames"
            )
        self._require(
            "timing sample window faults",
            evaluate_fault_deltas(initial_status, status),
        )
        self._require(
            "timing sample window foreground",
            evaluate_exact_status(
                status,
                {
                    "receiver_base_mode": BASE_LOCAL_BACKGROUND,
                    "receiver_foreground_state": FOREGROUND_ACTIVE,
                    "receiver_overlay_committed_generation": 2,
                    "receiver_overlay_staged_generation": 0,
                },
                require_ok=True,
            ),
        )
        final_remaining = _int(status, "receiver_overlay_lease_remaining_ms")
        if final_remaining <= 0:
            raise CanaryFailure("foreground lease expired inside the timing sample window")
        try:
            metrics = summarize_timing_samples(samples)
        except ValueError as exc:
            raise CanaryFailure(f"invalid timing sample: {exc}") from exc
        result = {
            "sampled_on": "receiver_local_frames_rendered_after_display_completion",
            "sample_count": len(samples),
            "poll_count": polls,
            "max_poll_count": max_polls,
            "completed_local_frame_delta": last_completed - initial_completed,
            "cadence_miss_delta": (
                _int(status, "receiver_local_missed_deadlines")
                - _int(initial_status, "receiver_local_missed_deadlines")
            ),
            "elapsed_seconds": round(elapsed, 3),
            "requested_seconds": self.config.timing_sample_seconds,
            "initial_lease_remaining_ms": initial_remaining,
            "final_lease_remaining_ms": final_remaining,
            "metrics": metrics,
        }
        return result, status

    def _publish_generation(
        self,
        controller,
        context: ReceiverPresentationContext,
        *,
        generation: int,
        prior_generation: int,
        update_kind: int,
        patches: Sequence[tuple[int, np.ndarray]],
    ) -> None:
        self._require_ack(
            f"foreground generation {generation} BEGIN acknowledgement",
            controller.begin_overlay(
                controller_session_id=context.controller_session_id,
                generation=generation,
                prior_generation=prior_generation,
                scene_revision=context.scene_revision,
                scene_epoch=context.scene_epoch,
                base_revision=context.scene_revision,
                update_kind=update_kind,
                expected_patches=len(patches),
                lease_ms=self.config.lease_ms,
            ),
            CMD_OVERLAY_BEGIN,
            controller=controller,
            overlay=True,
        )
        statuses = controller.send_overlay_patches(
            controller_session_id=context.controller_session_id,
            generation=generation,
            patches=patches,
            update_kind=update_kind,
        )
        if not statuses:
            raise CanaryFailure(
                f"foreground generation {generation} emitted no patch packets"
            )
        for index, status in enumerate(statuses):
            self._require_ack(
                f"foreground generation {generation} patch batch {index} acknowledgement",
                status,
                CMD_OVERLAY_PATCH_BATCH,
                controller=controller,
                overlay=True,
            )
        self._require_ack(
            f"foreground generation {generation} COMMIT acknowledgement",
            controller.commit_overlay(
                controller_session_id=context.controller_session_id,
                generation=generation,
                scene_epoch=context.scene_epoch,
                base_revision=context.scene_revision,
                present_at_scene_time_us=0,
            ),
            CMD_OVERLAY_COMMIT,
            controller=controller,
            overlay=True,
        )

    def run(self) -> dict[str, Any]:
        controller = None
        report: dict[str, Any] = {
            "passed": False,
            "bus": self.config.bus,
            "device": self.config.device,
            "logical_id": self.config.logical_id,
            "cadence_hz": self.config.cadence_hz,
            "lease_ms": self.config.lease_ms,
            "disconnect_seconds_requested": self.config.disconnect_seconds,
        }
        failure = None
        cleanup_error = None
        mutation_started = False
        context = None
        try:
            controller = self.controller_factory()
            preflight = self._fresh_status(controller)
            # Prove the exact physical binding and the deliberately enabled
            # canary image before CONFIG or any other state-changing packet.
            self._require(
                "receiver identity/capability preflight",
                evaluate_identity_status(preflight, self.config.logical_id),
            )
            fault_baseline = dict(preflight)

            mutation_started = True
            controller.configure()
            self._require(
                "receiver identity/capability post-config",
                evaluate_identity_status(
                    self._fresh_status(controller), self.config.logical_id
                ),
            )

            context = self._context()
            self._require_ack(
                "presentation BEGIN acknowledgement",
                controller.begin_presentation_context(context),
                CMD_PRESENTATION_CONTEXT_BEGIN,
                controller=controller,
            )
            self._require_ack(
                "presentation SET acknowledgement",
                controller.set_presentation_context(context),
                CMD_PRESENTATION_CONTEXT_SET,
                controller=controller,
            )
            self._require_ack(
                "presentation COMMIT acknowledgement",
                controller.commit_presentation_context(
                    context, host_monotonic_anchor_ns=self.monotonic_ns()
                ),
                CMD_PRESENTATION_CONTEXT_COMMIT,
                controller=controller,
            )
            self._require_ack(
                "compiled rainbow START acknowledgement",
                controller.start_local_background(
                    component_id=COMPONENT_STATIC_RAINBOW,
                    preferred_cadence_hz=self.config.cadence_hz,
                    global_strip_offset=self.config.global_strip_offset,
                    common_seed=self.config.common_seed,
                    scene_epoch=context.scene_epoch,
                ),
                CMD_LOCAL_BACKGROUND_START,
                controller=controller,
            )
            local_expected = expected_local_status(context, self.config)
            local_started = self._wait_for_status(
                controller,
                lambda status: (
                    _int(status, "receiver_local_frames_rendered")
                    > _int(preflight, "receiver_local_frames_rendered")
                ),
                "the first compiled-background cadence frame",
            )
            self._require(
                "compiled rainbow start",
                evaluate_exact_status(local_started, local_expected, require_ok=True),
            )
            self._require(
                "compiled rainbow timing", evaluate_timing_status({
                    **local_started,
                    # Foreground timing begins only after the first commit.
                    "receiver_overlay_last_composite_us": 1,
                    "receiver_overlay_max_composite_us": 1,
                })
            )

            snapshot, delta, dirty_ranges = make_foreground_frames()
            snapshot_patches = _snapshot_patches(snapshot)
            snapshot_digest = hashlib.sha256(snapshot.tobytes()).digest()
            before_snapshot = dict(self._fresh_status(controller))
            self._require_ack(
                "foreground controller-session acknowledgement",
                controller.begin_controller_session(
                    controller_session_id=context.controller_session_id,
                    desired_revision=context.scene_revision,
                    authoritative_snapshot_digest=snapshot_digest,
                ),
                CMD_CONTROLLER_SESSION_BEGIN,
                controller=controller,
                overlay=True,
            )
            self._publish_generation(
                controller,
                context,
                generation=1,
                prior_generation=0,
                update_kind=OVERLAY_UPDATE_FULL_SNAPSHOT,
                patches=snapshot_patches,
            )
            snapshot_status = self._wait_for_status(
                controller,
                lambda status: (
                    _int(status, "receiver_foreground_state") == FOREGROUND_ACTIVE
                    and _int(status, "receiver_overlay_committed_generation") == 1
                    and _int(status, "receiver_overlay_composite_frames")
                    > _int(before_snapshot, "receiver_overlay_composite_frames")
                ),
                "committed full foreground snapshot composition",
            )
            snapshot_expected = expected_foreground_status(
                context,
                self.config,
                generation=1,
                update_kind=OVERLAY_UPDATE_FULL_SNAPSHOT,
                patch_count=len(snapshot_patches),
                coverage_pixels=int(np.count_nonzero(snapshot[:, 3])),
            )
            self._require(
                "full foreground snapshot status",
                evaluate_exact_status(snapshot_status, snapshot_expected, require_ok=True),
            )
            self._require(
                "full foreground snapshot faults",
                evaluate_fault_deltas(fault_baseline, snapshot_status),
            )
            self._require(
                "full foreground snapshot timings",
                evaluate_timing_status(snapshot_status),
            )
            expected_snapshot_operations = 1 + 1 + len(snapshot_patches) + 1
            observed_snapshot_operations = _int(
                snapshot_status, "receiver_operation_sequence"
            ) - _int(before_snapshot, "receiver_operation_sequence")
            snapshot_counter_failures = []
            if observed_snapshot_operations != expected_snapshot_operations:
                snapshot_counter_failures.append(
                    f"operation sequence advanced by {observed_snapshot_operations}; "
                    f"expected exactly {expected_snapshot_operations}"
                )
            for key, expected_delta in (
                ("receiver_local_frames_rendered", 0),
                ("receiver_local_cadence_deadlines", 0),
                ("receiver_overlay_composite_frames", 1),
                ("receiver_overlay_commits", 1),
                ("receiver_overlay_expirations", 0),
            ):
                delta_value = _int(snapshot_status, key) - _int(before_snapshot, key)
                if delta_value != expected_delta:
                    snapshot_counter_failures.append(
                        f"{key} advanced by {delta_value}; expected exactly {expected_delta}"
                    )
            self._require("full foreground snapshot counters", snapshot_counter_failures)

            # Align immediately after a natural base cadence frame.  The small
            # delta transaction must then produce one foreground recomposition
            # before the next base deadline, with no simulation-time advance.
            aligned = self._wait_for_status(
                controller,
                lambda status: (
                    _int(status, "receiver_local_frames_rendered")
                    > _int(snapshot_status, "receiver_local_frames_rendered")
                ),
                "a base cadence boundary before the sparse delta",
            )
            before_delta = dict(aligned)
            delta_patches = _delta_patches(delta, dirty_ranges)
            self._publish_generation(
                controller,
                context,
                generation=2,
                prior_generation=1,
                update_kind=OVERLAY_UPDATE_DELTA,
                patches=delta_patches,
            )
            delta_status = self._wait_for_status(
                controller,
                lambda status: (
                    _int(status, "receiver_overlay_committed_generation") == 2
                    and _int(status, "receiver_overlay_composite_frames")
                    > _int(before_delta, "receiver_overlay_composite_frames")
                ),
                "sparse delta composition",
            )
            cadence_result = evaluate_cadence_independence(
                before_delta,
                delta_status,
                expected_status=expected_foreground_status(
                    context,
                    self.config,
                    generation=2,
                    update_kind=OVERLAY_UPDATE_DELTA,
                    patch_count=len(delta_patches),
                    coverage_pixels=int(np.count_nonzero(delta[:, 3])),
                ),
            )
            self._require("foreground/base cadence independence", cadence_result["failures"])
            report["cadence_independence"] = cadence_result
            report["snapshot"] = {
                "generation": 1,
                "coverage_pixels": int(np.count_nonzero(snapshot[:, 3])),
                "patches": len(snapshot_patches),
                "digest": snapshot_digest.hex(),
            }
            report["delta"] = {
                "generation": 2,
                "coverage_pixels": int(np.count_nonzero(delta[:, 3])),
                "patches": len(delta_patches),
                "dirty_ranges": [list(item) for item in dirty_ranges],
            }

            timing_result, timing_status = self._collect_timing_window(
                controller, delta_status
            )
            report["receiver_timing_window"] = timing_result
            expiry_started = self.clock()
            before_expiry = dict(timing_status)
            controller.close()
            controller = None
            self.sleeper(self.config.disconnect_seconds)
            controller = self.controller_factory()
            after_expiry = dict(self._fresh_status(controller))
            elapsed = self.clock() - expiry_started
            expiry_result = evaluate_expiry_window(
                before_expiry,
                after_expiry,
                elapsed_seconds=elapsed,
                cadence_hz=self.config.cadence_hz,
                expected_base_status=local_expected,
                generation=2,
                session_id=context.controller_session_id,
            )
            self._require("lease expiry/base continuation", expiry_result["failures"])
            report["expiry"] = expiry_result

            takeover = self._black_takeover(controller)
            report["takeover_base_mode"] = takeover["receiver_base_mode"]
            report["context_digest"] = context.context_digest.hex()
            report["passed"] = True
        except Exception as exc:
            failure = str(exc)
        finally:
            if mutation_started and controller is None:
                try:
                    controller = self.controller_factory()
                except Exception as exc:
                    cleanup_error = f"SPI reopen for black takeover failed: {exc}"
            if mutation_started and controller is not None:
                try:
                    self._black_takeover(controller)
                    report["finally_black_takeover"] = True
                except Exception as exc:
                    cleanup_error = f"finally black takeover failed: {exc}"
                finally:
                    try:
                        controller.close()
                    except Exception as exc:
                        cleanup_error = cleanup_error or f"controller close failed: {exc}"
            elif controller is not None:
                report["preflight_non_mutating"] = True
                try:
                    controller.close()
                except Exception as exc:
                    cleanup_error = f"controller close failed: {exc}"

        if failure:
            report["failure"] = failure
        if cleanup_error:
            report["cleanup_failure"] = cleanup_error
        report["passed"] = bool(report["passed"] and not cleanup_error)
        return report


def _integer(text: str) -> int:
    return int(text, 0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the strict single-readable-receiver Phase 3B canary. Stop the "
            "ledgrid systemd service before invoking this command."
        )
    )
    parser.add_argument("--bus", type=int, required=True)
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--logical-id", type=int, required=True)
    parser.add_argument("--disconnect-seconds", type=float, default=5.0)
    parser.add_argument("--cadence-hz", type=int, default=30)
    parser.add_argument("--common-seed", type=_integer, default=0x3B0C4A7E)
    parser.add_argument("--lease-ms", type=int, default=3_000)
    parser.add_argument("--observation-timeout-seconds", type=float, default=1.0)
    parser.add_argument("--timing-sample-seconds", type=float, default=0.5)
    args = parser.parse_args()

    result = SingleReceiverPhase3BCanary(CanaryConfig(
        bus=args.bus,
        device=args.device,
        logical_id=args.logical_id,
        disconnect_seconds=args.disconnect_seconds,
        cadence_hz=args.cadence_hz,
        common_seed=args.common_seed,
        lease_ms=args.lease_ms,
        observation_timeout_seconds=args.observation_timeout_seconds,
        timing_sample_seconds=args.timing_sample_seconds,
    )).run()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

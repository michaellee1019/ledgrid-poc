#!/usr/bin/env python3
"""Explicitly degraded Phase 3B0 five-receiver hybrid showcase.

This tool is demonstration evidence, never release acceptance.  It accepts only
the installed wall's exact temporary topology: logical receivers 0 and 1 must
provide strict Phase 3B status-v4 identity/capability telemetry, while logical
receivers 2, 3, and 4 must expose the exact known no-return-path state.  The
same compiled-background and sparse-foreground packets are still written to all
five receivers, but receivers 2, 3, and 4 remain visually unverified.

The ordinary multi-device transaction API deliberately requires every receiver
to acknowledge commands.  This file contains the only degraded exception: it
uses normal acknowledged methods for 0/1 and raw *pre-serialized, allowlisted*
packets for 2/3/4.  There is no asset/cache/upload surface.  A complete RGB host
frame restores display ownership in ``finally`` before the exact prior desired
display state is restored for the next controller start.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
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
)
from animation.core.receiver_sparse_publisher import ReceiverSparsePublisher
from animation.plugins.clock_overlay import ClockOverlayAnimation
from drivers.multi_device import MultiDeviceLEDController
from drivers.degraded_receiver_hybrid import (
    DegradedReceiverHybridController as ProductionDegradedHybridTransport,
    DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
    DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER,
    EXPECTED_CAPABILITIES,
    EXPECTED_DEVICE_MAP,
    EXPECTED_STATUS_VERSION,
    LEDS_PER_STRIP,
    LOCAL_PIXELS,
    LOCAL_STRIPS,
    READABLE_DEVICES,
    RECEIVER_COUNT,
    RECEIVER_GLOBAL_STRIP_OFFSETS,
    RECEIVER_LANE_MASKS,
    RECEIVER_PIXEL_COUNTS,
    RECEIVER_PIXEL_OFFSETS,
    RECEIVER_STRIP_COUNTS,
    UNVERIFIED_DEVICES,
    WALL_PIXELS,
    WALL_STRIPS,
)


POLICY_NAME = "phase3b0_degraded_five_receiver_showcase"
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
    if len(statuses) != RECEIVER_COUNT:
        failures.append(
            f"receiver telemetry has {len(statuses)} devices; expected exactly "
            f"{RECEIVER_COUNT}"
        )

    for logical_id in range(min(RECEIVER_COUNT, len(statuses))):
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
        failures.append(
            "degraded policy requires exact readable pair 0/1 and write-only "
            "receivers 2/3/4"
        )
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
        "observed_logical_devices": list(range(RECEIVER_COUNT)),
        "acknowledged_unverified_devices": list(UNVERIFIED_DEVICES),
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
        "observed_logical_devices": list(range(RECEIVER_COUNT)),
        "unverified_devices": list(UNVERIFIED_DEVICES),
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
        overlay = self.compositor.compose((PlacedOverlay(frame),))
        # The clock glyph does not naturally occupy the one-column tail at all
        # times. Keep one stable, dim premultiplied pixel there so the degraded
        # showcase exercises receiver 4 without claiming telemetry or mirroring
        # the column onto inactive lanes.
        overlay.pixels[RECEIVER_PIXEL_OFFSETS[4]] = (8, 4, 2, 8)
        return overlay

    def stop(self) -> None:
        self.animation.stop()

    def cleanup(self) -> None:
        self.animation.cleanup()


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
                "2, 3, and 4 have no receiver/display proof and require direct "
                "visual observation."
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
            "Run the explicitly degraded Phase 3B0 five-receiver showcase. The "
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
            num_devices=RECEIVER_COUNT,
            strips_per_device=LOCAL_STRIPS,
            strip_count=WALL_STRIPS,
            leds_per_strip=LEDS_PER_STRIP,
            device_map=list(EXPECTED_DEVICE_MAP),
            receiver_strip_counts=RECEIVER_STRIP_COUNTS,
            receiver_global_strip_offsets=RECEIVER_GLOBAL_STRIP_OFFSETS,
            receiver_lane_masks=RECEIVER_LANE_MASKS,
            reverse_host_strips_by_logical_receiver=(
                DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER
            ),
            reverse_native_strips_by_logical_receiver=(
                DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
            ),
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

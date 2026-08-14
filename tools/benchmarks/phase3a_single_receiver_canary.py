#!/usr/bin/env python3
"""Safely exercise the Phase 3A local-background canary on one receiver.

The web controller/systemd service must already be stopped. This runner never
flashes firmware or manages services. It always attempts a complete black host
frame before closing any SPI path that remains available.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import secrets
import sys
import time
from typing import Any, Callable, Mapping, Optional

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
    CAPABILITY_STATIC_LOCAL_BACKGROUND,
    CAPABILITY_STATUS_V3,
    LEDController,
    SPI_RESPONSE_QUEUE_DEPTH,
)


REQUIRED_CAPABILITIES = (
    CAPABILITY_STATIC_LOCAL_BACKGROUND
    | CAPABILITY_PRESENTATION_CONTEXT_V1
    | CAPABILITY_STATUS_V3
    | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
)
LOCAL_STRIPS = 8
LEDS_PER_STRIP = 138
LOCAL_PIXELS = LOCAL_STRIPS * LEDS_PER_STRIP
BASE_LOCAL_BACKGROUND = 1
BASE_HOST_FULL_SCENE = 2
COMPONENT_STATIC_RAINBOW = 1
RESULT_OK = 1
TRANSITION_LOCAL_START = 1
CMD_SET_ALL = 0x06


class CanaryFailure(RuntimeError):
    """Acceptance failure that still triggers the black-frame safety path."""


@dataclass(frozen=True)
class CanaryConfig:
    bus: int
    device: int
    logical_id: int
    disconnect_seconds: float = 60.0
    initial_cadence_hz: int = 30
    updated_cadence_hz: int = 45
    initial_seed: int = 0x13579BDF
    updated_seed: int = 0x2468ACE0

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
            isinstance(self.disconnect_seconds, bool)
            or not isinstance(self.disconnect_seconds, (int, float))
            or not math.isfinite(float(self.disconnect_seconds))
            or self.disconnect_seconds <= 0
        ):
            raise ValueError("disconnect_seconds must be finite and greater than zero")
        for name in ("initial_cadence_hz", "updated_cadence_hz"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
                raise ValueError(f"{name} must be between 1 and 200")
        for name in ("initial_seed", "updated_seed"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{name} must fit in uint32")

    @property
    def global_strip_offset(self) -> int:
        return self.logical_id * LOCAL_STRIPS


def _int(status: Mapping[str, Any], key: str) -> int:
    try:
        return int(status.get(key, 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def evaluate_identity_status(status: Any, logical_id: int) -> list[str]:
    failures = []
    if not isinstance(status, Mapping):
        return ["receiver status is unavailable"]
    version = _int(status, "receiver_status_version")
    capabilities = _int(status, "receiver_capabilities")
    if version != 3:
        failures.append(f"receiver status version is {version}; expected exactly 3")
    if capabilities != REQUIRED_CAPABILITIES:
        failures.append(
            f"receiver capabilities are 0x{capabilities:08x}; "
            f"expected exactly 0x{REQUIRED_CAPABILITIES:08x}"
        )
    if status.get("receiver_logical_device") != logical_id:
        failures.append(
            f"receiver logical identity is {status.get('receiver_logical_device')!r}; "
            f"expected {logical_id}"
        )
    return failures


def expected_local_status(
    context: ReceiverPresentationContext,
    config: CanaryConfig,
    *,
    cadence_hz: int,
    seed: int,
) -> dict[str, Any]:
    return {
        "receiver_status_version": 3,
        "receiver_capabilities": REQUIRED_CAPABILITIES,
        "receiver_logical_device": config.logical_id,
        "receiver_base_mode": BASE_LOCAL_BACKGROUND,
        "receiver_component_id": COMPONENT_STATIC_RAINBOW,
        "receiver_declared_cadence_hz": cadence_hz,
        "receiver_global_strip_offset": config.global_strip_offset,
        "receiver_common_seed": seed,
        "receiver_scene_epoch": context.scene_epoch,
        "receiver_active_scene_revision": context.scene_revision,
        "receiver_active_context_digest": context.context_digest.hex(),
        "receiver_vibe_revision": context.vibe.state.revision,
        "receiver_vibe_digest": context.vibe.state.resolved_profile_digest,
        "receiver_plant_modifier_revision": context.plant_revision,
        "receiver_plant_modifier_digest": context.plant_digest.hex(),
        "receiver_active_session_id": context.controller_session_id.hex(),
    }


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


def evaluate_disconnect_window(
    before: Any,
    after: Any,
    *,
    elapsed_seconds: float,
    cadence_hz: int,
    expected_binding: Mapping[str, Any],
) -> dict[str, Any]:
    failures = []
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return {"passed": False, "failures": ["disconnect status pair is unavailable"]}
    failures.extend(evaluate_exact_status(after, expected_binding, require_ok=True))
    if _int(after, "receiver_transition_reason") != TRANSITION_LOCAL_START:
        failures.append("receiver transition reason drifted during disconnect")

    deltas = {}
    for key in (
        "receiver_local_cadence_deadlines",
        "receiver_local_frames_rendered",
        "receiver_local_missed_deadlines",
        "receiver_crc_errors",
        "receiver_spi_queue_errors",
        "receiver_display_errors",
    ):
        deltas[key] = _int(after, key) - _int(before, key)
        if deltas[key] < 0:
            failures.append(f"{key} regressed across receiver reopen")

    rendered = deltas["receiver_local_frames_rendered"]
    deadlines = deltas["receiver_local_cadence_deadlines"]
    expected_frames = elapsed_seconds * cadence_hz
    minimum_frames = max(1, math.floor(expected_frames * 0.80))
    maximum_frames = math.ceil(expected_frames * 1.20) + 2
    if rendered < minimum_frames or rendered > maximum_frames:
        failures.append(
            f"local rendered-frame delta {rendered} is outside "
            f"{minimum_frames}..{maximum_frames}"
        )
    if deadlines < minimum_frames or deadlines > maximum_frames:
        failures.append(
            f"local cadence-deadline delta {deadlines} is outside "
            f"{minimum_frames}..{maximum_frames}"
        )
    for key, label in (
        ("receiver_local_missed_deadlines", "local cadence misses"),
        ("receiver_crc_errors", "CRC errors"),
        ("receiver_spi_queue_errors", "SPI queue errors"),
        ("receiver_display_errors", "display errors"),
    ):
        if deltas[key] != 0:
            failures.append(f"{label} increased by {deltas[key]}")
    last_render_us = _int(after, "receiver_last_local_render_us")
    max_render_us = _int(after, "receiver_max_local_render_us")
    if last_render_us <= 0:
        failures.append("last local render duration is not positive")
    if max_render_us < last_render_us:
        failures.append("maximum local render duration is below the last render duration")
    if _int(after, "receiver_last_frame_scene_time_us") <= _int(
        before, "receiver_last_frame_scene_time_us"
    ):
        failures.append("local scene time did not advance during disconnect")

    return {
        "passed": not failures,
        "failures": failures,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "expected_cadence_hz": cadence_hz,
        "rendered_delta": rendered,
        "deadline_delta": deadlines,
        "missed_deadline_delta": deltas["receiver_local_missed_deadlines"],
    }


def evaluate_host_takeover(status: Any) -> list[str]:
    return evaluate_exact_status(
        status,
        {
            "receiver_base_mode": BASE_HOST_FULL_SCENE,
            "receiver_last_processed_command": CMD_SET_ALL,
        },
        require_ok=True,
    )


class SingleReceiverPhase3ACanary:
    def __init__(
        self,
        config: CanaryConfig,
        *,
        controller_factory: Optional[Callable[[], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sleeper: Callable[[float], None] = time.sleep,
        session_factory: Callable[[int], bytes] = secrets.token_bytes,
    ):
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
        # The receiver prequeues two snapshots. The third transfer returns the
        # first snapshot generated after this explicit read began.
        for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 1):
            status = controller.query_receiver_status()
        if not isinstance(status, Mapping):
            raise CanaryFailure("receiver returned no status")
        return status

    @staticmethod
    def _require(stage: str, failures: list[str]) -> None:
        if failures:
            raise CanaryFailure(f"{stage}: {'; '.join(failures)}")

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

    def _black_takeover(self, controller) -> Mapping[str, Any]:
        controller.set_all_pixels(self.black_frame)
        status = self._fresh_status(controller)
        self._require("black host takeover", evaluate_host_takeover(status))
        return status

    def run(self) -> dict[str, Any]:
        controller = None
        context = None
        report: dict[str, Any] = {
            "passed": False,
            "bus": self.config.bus,
            "device": self.config.device,
            "logical_id": self.config.logical_id,
            "disconnect_seconds_requested": self.config.disconnect_seconds,
        }
        failure = None
        cleanup_error = None
        mutation_started = False
        try:
            controller = self.controller_factory()
            status = self._fresh_status(controller)
            # Prove the exact physical binding before CONFIG or any other
            # state-changing packet. A bad address must remain observation-only.
            self._require(
                "receiver identity/capability preflight",
                evaluate_identity_status(status, self.config.logical_id),
            )
            mutation_started = True
            controller.configure()
            status = self._fresh_status(controller)
            self._require(
                "receiver identity/capability preflight",
                evaluate_identity_status(status, self.config.logical_id),
            )

            context = self._context()
            self._require(
                "presentation BEGIN acknowledgement",
                evaluate_exact_status(
                    controller.begin_presentation_context(context), {}, require_ok=True
                ),
            )
            self._require(
                "presentation SET acknowledgement",
                evaluate_exact_status(
                    controller.set_presentation_context(context), {}, require_ok=True
                ),
            )
            commit_status = controller.commit_presentation_context(
                context, host_monotonic_anchor_ns=self.monotonic_ns()
            )
            self._require(
                "presentation COMMIT acknowledgement",
                evaluate_exact_status(commit_status, {}, require_ok=True),
            )
            start_status = controller.start_local_background(
                component_id=COMPONENT_STATIC_RAINBOW,
                preferred_cadence_hz=self.config.initial_cadence_hz,
                global_strip_offset=self.config.global_strip_offset,
                common_seed=self.config.initial_seed,
                scene_epoch=context.scene_epoch,
            )
            self._require(
                "local rainbow START acknowledgement",
                evaluate_exact_status(start_status, {}, require_ok=True),
            )
            status = self._fresh_status(controller)
            initial_expected = expected_local_status(
                context, self.config,
                cadence_hz=self.config.initial_cadence_hz,
                seed=self.config.initial_seed,
            )
            self._require(
                "local rainbow start",
                evaluate_exact_status(status, initial_expected, require_ok=True),
            )
            before_disconnect = dict(status)

            window_started = self.clock()
            controller.close()
            controller = None
            self.sleeper(self.config.disconnect_seconds)
            controller = self.controller_factory()
            after_disconnect = dict(self._fresh_status(controller))
            elapsed = self.clock() - window_started
            disconnect_result = evaluate_disconnect_window(
                before_disconnect,
                after_disconnect,
                elapsed_seconds=elapsed,
                cadence_hz=self.config.initial_cadence_hz,
                expected_binding=initial_expected,
            )
            self._require("disconnect/reopen window", disconnect_result["failures"])
            report["disconnect"] = disconnect_result

            parameter_status = controller.update_local_background_params(
                preferred_cadence_hz=self.config.updated_cadence_hz,
                global_strip_offset=self.config.global_strip_offset,
                common_seed=self.config.updated_seed,
            )
            self._require(
                "live parameter acknowledgement",
                evaluate_exact_status(parameter_status, {}, require_ok=True),
            )
            updated = self._fresh_status(controller)
            self._require(
                "live parameter update",
                evaluate_exact_status(
                    updated,
                    expected_local_status(
                        context, self.config,
                        cadence_hz=self.config.updated_cadence_hz,
                        seed=self.config.updated_seed,
                    ),
                    require_ok=True,
                ),
            )
            report["updated_cadence_hz"] = self.config.updated_cadence_hz
            report["updated_seed"] = self.config.updated_seed

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
            "Run the single-receiver Phase 3A physical canary. Stop the "
            "ledgrid systemd service before invoking this command."
        )
    )
    parser.add_argument("--bus", type=int, required=True)
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--logical-id", type=int, required=True)
    parser.add_argument("--disconnect-seconds", type=float, default=60.0)
    parser.add_argument("--initial-cadence-hz", type=int, default=30)
    parser.add_argument("--updated-cadence-hz", type=int, default=45)
    parser.add_argument("--initial-seed", type=_integer, default=0x13579BDF)
    parser.add_argument("--updated-seed", type=_integer, default=0x2468ACE0)
    args = parser.parse_args()

    config = CanaryConfig(
        bus=args.bus,
        device=args.device,
        logical_id=args.logical_id,
        disconnect_seconds=args.disconnect_seconds,
        initial_cadence_hz=args.initial_cadence_hz,
        updated_cadence_hz=args.updated_cadence_hz,
        initial_seed=args.initial_seed,
        updated_seed=args.updated_seed,
    )
    result = SingleReceiverPhase3ACanary(config).run()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Isolate receiver-3 corruption by board phase and output-lane activity.

This is exploratory electrical evidence, never PERF-01 or WALL-02 acceptance.
Every emitted frame is all black, brightness is held at zero, and logical IDs,
routes, widths, and physical mapping remain unchanged.  Each experiment is
bracketed by production-state A arms with the same full-frame transfer count.
The controller service must be active on entry and is restored to a verified
black idle state in ``finally``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import numpy as np

REPOSITORY_ROOT = Path(
    os.environ.get("LEDGRID_REPOSITORY_ROOT", Path(__file__).resolve().parents[2])
).resolve()
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != REPOSITORY_ROOT]
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
from tools.diagnostics.receiver_dispatch_order import (  # noqa: E402
    DELTA_FIELDS,
    DispatchDiagnosticError,
    _fresh_snapshots,
    _service,
    _service_active,
    _wait_for_safe_idle,
)

if TYPE_CHECKING:
    from drivers.multi_device import MultiDeviceLEDController


SERVICE = "ledgrid.service"
PRODUCTION_STAGGER_PHASES = 3
DIAGNOSTIC_STAGGER_PHASES = 1
FEC_RECEIVER_ID = 3
RECEIVER_COUNT = len(WALL_DEVICE_MAP)
STATUS_SETTLE_TIMEOUT_SECONDS = 5.0
RESET_COUNTER_FIELD = "receiver_fec_terminal_counter_resets"
STRICT_DELTA_FIELDS = DELTA_FIELDS + (RESET_COUNTER_FIELD,)
FAULT_FIELDS = (
    "receiver_crc_errors",
    "receiver_status_misses",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_publish_drops",
    "receiver_fec_corrected_packets",
    "receiver_fec_corrected_codewords",
    "receiver_fec_uncorrectable_packets",
    "receiver_fec_semantic_crc_errors",
    "receiver_fec_framing_errors",
    RESET_COUNTER_FIELD,
)


@dataclass(frozen=True)
class OutputState:
    """Exact receiver output state for one retained diagnostic arm."""

    label: str
    phases: tuple[int, ...]
    lane_masks: tuple[int, ...]


@dataclass(frozen=True)
class ArmPlan:
    """One A/B/A arm with a stable pairing key."""

    pair_id: str
    role: str
    state: OutputState


def production_state() -> OutputState:
    return OutputState(
        label="production-phase3",
        phases=(PRODUCTION_STAGGER_PHASES,) * RECEIVER_COUNT,
        lane_masks=tuple(WALL_PHYSICAL_OUTPUT_LANE_MASKS),
    )


def three_phase_group_masks(active_mask: int = 0xFF) -> tuple[int, int, int]:
    """Return the exact lane groups produced by ``lane % 3`` staggering."""

    if type(active_mask) is not int or not 0 <= active_mask <= 0xFF:
        raise ValueError("active mask must be an unsigned byte")
    groups = []
    for phase in range(PRODUCTION_STAGGER_PHASES):
        mask = 0
        for lane in range(8):
            if lane % PRODUCTION_STAGGER_PHASES == phase:
                mask |= 1 << lane
        groups.append(mask & active_mask)
    return tuple(groups)  # type: ignore[return-value]


def phase_experiments() -> tuple[OutputState, ...]:
    """Change exactly one receiver from phase 3 to phase 1 per experiment."""

    result = []
    baseline = production_state()
    for receiver_id in range(RECEIVER_COUNT):
        phases = list(baseline.phases)
        phases[receiver_id] = DIAGNOSTIC_STAGGER_PHASES
        result.append(
            OutputState(
                label=f"receiver-{receiver_id}-phase1",
                phases=tuple(phases),
                lane_masks=baseline.lane_masks,
            )
        )
    return tuple(result)


def receiver3_lane_experiments() -> tuple[OutputState, ...]:
    """Silence or isolate receiver 3's lanes while all boards remain phase 3."""

    baseline = production_state()
    active = baseline.lane_masks[FEC_RECEIVER_ID]
    masks = [("none", 0)]
    masks.extend((f"lane-{lane}", (1 << lane) & active) for lane in range(8))
    masks.extend(
        (f"phase-group-{phase}", mask)
        for phase, mask in enumerate(three_phase_group_masks(active))
    )
    masks.append(("all", active))

    result = []
    for label, mask in masks:
        lane_masks = list(baseline.lane_masks)
        lane_masks[FEC_RECEIVER_ID] = mask
        result.append(
            OutputState(
                label=f"receiver-3-mask-{label}",
                phases=baseline.phases,
                lane_masks=tuple(lane_masks),
            )
        )
    return tuple(result)


def build_arm_plan(plan: str) -> tuple[ArmPlan, ...]:
    """Build deterministic A/B/A triplets for the requested isolation plan."""

    if plan not in {"phase", "lane", "all"}:
        raise ValueError("plan must be phase, lane, or all")
    experiments = []
    if plan in {"phase", "all"}:
        experiments.extend(phase_experiments())
    if plan in {"lane", "all"}:
        experiments.extend(receiver3_lane_experiments())

    baseline = production_state()
    arms = []
    for index, experiment in enumerate(experiments):
        pair_id = f"{index:02d}-{experiment.label}"
        arms.extend(
            (
                ArmPlan(pair_id, "A-pre", baseline),
                ArmPlan(pair_id, "B", experiment),
                ArmPlan(pair_id, "A-post", baseline),
            )
        )
    return tuple(arms)


def validate_snapshot(
    snapshot: Mapping[str, Any], logical_id: int, expected: OutputState | None = None
) -> None:
    """Reject stale, legacy, reset, or incompletely baselined status evidence."""

    if snapshot.get("receiver_status_seen") is not True:
        raise DispatchDiagnosticError(f"receiver {logical_id} status was not observed")
    if snapshot.get("receiver_status_version") != 7:
        raise DispatchDiagnosticError(
            f"receiver {logical_id} did not return current status v7"
        )
    if snapshot.get("receiver_status_max_version_seen") != 7:
        raise DispatchDiagnosticError(
            f"receiver {logical_id} lacks sticky status-v7 proof"
        )
    topology = {
        "receiver_logical_device": logical_id,
        "receiver_active_strips": WALL_RECEIVER_STRIP_COUNTS[logical_id],
        "receiver_leds_per_strip": DEFAULT_LEDS_PER_STRIP,
        "receiver_global_strip_offset": WALL_RECEIVER_GLOBAL_STRIP_OFFSETS[logical_id],
    }
    for field, value in topology.items():
        if snapshot.get(field) != value:
            raise DispatchDiagnosticError(
                f"receiver {logical_id} {field} does not match installed topology"
            )
    for field in STRICT_DELTA_FIELDS:
        if type(snapshot.get(field)) is not int or snapshot[field] < 0:
            raise DispatchDiagnosticError(
                f"receiver {logical_id} {field} evidence is unavailable"
            )
    if snapshot.get("receiver_fec_terminal_baseline_invalid") is not False:
        raise DispatchDiagnosticError(
            f"receiver {logical_id} FEC terminal baseline is invalid"
        )
    if snapshot[RESET_COUNTER_FIELD] != 0:
        raise DispatchDiagnosticError(
            f"receiver {logical_id} reported a terminal counter reset"
        )
    if logical_id == FEC_RECEIVER_ID:
        if snapshot.get("receiver_fec_terminal_baseline_established") is not True:
            raise DispatchDiagnosticError("receiver 3 FEC terminal baseline is missing")
        if snapshot.get("fec_transport_enabled") is not True:
            raise DispatchDiagnosticError("receiver 3 FEC transport is not enabled")
    if expected is not None:
        if snapshot.get("receiver_stagger_phases") != expected.phases[logical_id]:
            raise DispatchDiagnosticError(
                f"receiver {logical_id} did not apply the requested phase"
            )
        if snapshot.get("receiver_lane_mask") != expected.lane_masks[logical_id]:
            raise DispatchDiagnosticError(
                f"receiver {logical_id} did not apply the requested lane mask"
            )


def strict_counter_deltas(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[dict[str, int]]:
    """Return exact deltas and reject reset/invalid status on either boundary."""

    if len(before) != RECEIVER_COUNT or len(after) != RECEIVER_COUNT:
        raise DispatchDiagnosticError("exactly five receiver snapshots are required")
    result = []
    for logical_id, (start, finish) in enumerate(zip(before, after)):
        validate_snapshot(start, logical_id)
        validate_snapshot(finish, logical_id)
        delta = {"logical_device": logical_id}
        for field in STRICT_DELTA_FIELDS:
            if finish[field] < start[field]:
                raise DispatchDiagnosticError(
                    f"receiver {logical_id} {field} counter reset during the arm"
                )
            delta[field] = finish[field] - start[field]
        if delta[RESET_COUNTER_FIELD] != 0:
            raise DispatchDiagnosticError(
                f"receiver {logical_id} reset evidence changed during the arm"
            )
        result.append(delta)
    return result


def _fresh_validated_snapshots(
    controller: MultiDeviceLEDController,
    expected: OutputState,
    *,
    timeout: float = STATUS_SETTLE_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Drain queued legacy replies until every receiver returns fresh exact v7."""

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            snapshots = _fresh_snapshots(controller)
            for logical_id, snapshot in enumerate(snapshots):
                validate_snapshot(snapshot, logical_id, expected)
            return snapshots
        except Exception as exc:
            last_error = exc
            time.sleep(0.05)
    raise DispatchDiagnosticError(
        f"fresh exact-v7 receiver status did not settle: {last_error}"
    )


def build_ab_comparisons(arms: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize completed triplets into machine-readable A/B comparisons."""

    grouped: dict[str, dict[str, Mapping[str, Any]]] = {}
    for arm in arms:
        pair_id = arm.get("pair_id")
        role = arm.get("role")
        if not isinstance(pair_id, str) or role not in {"A-pre", "B", "A-post"}:
            raise DispatchDiagnosticError("malformed A/B/A arm evidence")
        if role in grouped.setdefault(pair_id, {}):
            raise DispatchDiagnosticError(f"duplicate {role} arm for {pair_id}")
        grouped[pair_id][role] = arm

    comparisons = []
    for pair_id, roles in grouped.items():
        if set(roles) != {"A-pre", "B", "A-post"}:
            raise DispatchDiagnosticError(f"incomplete A/B/A evidence for {pair_id}")
        fault_deltas: dict[str, dict[str, list[int]]] = {}
        for role in ("A-pre", "B", "A-post"):
            receivers = roles[role].get("receivers")
            if not isinstance(receivers, list) or len(receivers) != RECEIVER_COUNT:
                raise DispatchDiagnosticError(
                    f"{pair_id} {role} lacks exact receiver evidence"
                )
            rows: dict[str, list[int]] = {field: [] for field in FAULT_FIELDS}
            for logical_id, receiver in enumerate(receivers):
                if not isinstance(receiver, Mapping) or receiver.get(
                    "logical_device"
                ) != logical_id:
                    raise DispatchDiagnosticError(
                        f"{pair_id} {role} receiver ordering is invalid"
                    )
                for field in FAULT_FIELDS:
                    value = receiver.get(field)
                    if type(value) is not int or value < 0:
                        raise DispatchDiagnosticError(
                            f"{pair_id} {role} {field} is invalid"
                        )
                    rows[field].append(value)
            fault_deltas[role] = rows
        comparisons.append(
            {
                "pair_id": pair_id,
                "experiment": roles["B"]["state"],
                "fault_deltas_by_role": fault_deltas,
            }
        )
    return comparisons


def _apply_output_state(
    controller: MultiDeviceLEDController,
    state: OutputState,
    *,
    timeout: float = STATUS_SETTLE_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    if len(state.phases) != RECEIVER_COUNT or len(state.lane_masks) != RECEIVER_COUNT:
        raise DispatchDiagnosticError("output state must describe exactly five receivers")
    for logical_id, device in enumerate(controller.devices):
        device.set_lane_mask(state.lane_masks[logical_id])
        device.set_stagger_phases(state.phases[logical_id])

    return _fresh_validated_snapshots(controller, state, timeout=timeout)


def _report(
    args: argparse.Namespace,
    arms: Sequence[Mapping[str, Any]],
    *,
    complete: bool,
) -> dict[str, Any]:
    result = {
        "schema": "ledgrid.receiver-phase-lane-isolation",
        "schema_version": 1,
        "acceptance_evidence": False,
        "complete": complete,
        "display_contract": "brightness-zero-all-black",
        "plan": args.plan,
        "target_fps": args.target_fps,
        "transfers_per_arm": args.transfers,
        "device_map": [list(route) for route in WALL_DEVICE_MAP],
        "spi_speeds_hz": list(WALL_RECEIVER_SPI_SPEEDS_HZ),
        "repository_root": str(REPOSITORY_ROOT),
        "release_id": REPOSITORY_ROOT.name,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_identity": dict(getattr(args, "source_identity", {})),
        "arms": list(arms),
    }
    if complete:
        result["comparisons"] = build_ab_comparisons(arms)
    return result


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    """Atomically retain pure JSON after every completed arm."""

    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise DispatchDiagnosticError("refusing to overwrite a symbolic link")
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _send_black_transfers(
    controller: MultiDeviceLEDController,
    frame: np.ndarray,
    *,
    target_fps: int,
    transfers: int,
) -> float:
    period = 1.0 / target_fps
    start = time.monotonic()
    deadline = start
    for _ in range(transfers):
        now = time.monotonic()
        if now < deadline:
            time.sleep(deadline - now)
        controller.set_all_pixels(frame)
        deadline += period
        now = time.monotonic()
        if deadline < now:
            missed = int((now - deadline) // period) + 1
            deadline += missed * period
    return time.monotonic() - start


def _preflight_source_and_topology() -> dict[str, Any]:
    """Bind staged code to one selected immutable release and durable topology."""

    import drivers.led_layout as led_layout
    import drivers.multi_device as multi_device
    import tools.diagnostics.receiver_dispatch_order as dispatch

    module_paths = {
        "drivers.led_layout": Path(led_layout.__file__).resolve(),
        "drivers.multi_device": Path(multi_device.__file__).resolve(),
        "tools.diagnostics.receiver_dispatch_order": Path(dispatch.__file__).resolve(),
    }
    for name, path in module_paths.items():
        if not path.is_relative_to(REPOSITORY_ROOT):
            raise DispatchDiagnosticError(
                f"{name} resolved outside the selected release: {path}"
            )

    deployment_root = (
        REPOSITORY_ROOT.parent.parent
        if REPOSITORY_ROOT.parent.name == "releases"
        else None
    )
    if deployment_root is None or not deployment_root.is_dir():
        raise DispatchDiagnosticError(
            "live diagnostic requires an immutable releases/<release-id> root"
        )
    current = deployment_root / "current"
    if not current.is_symlink() or current.resolve() != REPOSITORY_ROOT:
        raise DispatchDiagnosticError(
            "selected current release does not match LEDGRID_REPOSITORY_ROOT"
        )
    config_path = deployment_root / "run_state" / "receiver_hybrid.json"
    try:
        raw = config_path.read_bytes()
        config = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchDiagnosticError(
            f"could not load durable receiver topology: {exc}"
        ) from exc
    expected = {
        "schema": "ledgrid.receiver-hybrid-rollout",
        "schema_version": 5,
        "enabled": False,
        "native_modules_enabled": False,
        "transport_policy": "off",
        "physical_lane_order": list(range(RECEIVER_COUNT)),
        "physical_output_lane_masks": list(WALL_PHYSICAL_OUTPUT_LANE_MASKS),
        "receiver_global_strip_offsets": list(WALL_RECEIVER_GLOBAL_STRIP_OFFSETS),
        "receiver_strip_counts": list(WALL_RECEIVER_STRIP_COUNTS),
        "reverse_strips_by_logical_receiver": list(
            WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER
        ),
        "reverse_native_strips_by_logical_receiver": list(
            WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
        ),
    }
    if config != expected:
        raise DispatchDiagnosticError(
            "durable receiver topology differs from the installed feature-off contract"
        )
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return {
        "release_id": REPOSITORY_ROOT.name,
        "current_release": str(current.resolve()),
        "receiver_hybrid_digest": hashlib.sha256(canonical).hexdigest(),
        "receiver_hybrid_file_sha256": hashlib.sha256(raw).hexdigest(),
        "module_sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in module_paths.items()
        },
    }


def _controller() -> MultiDeviceLEDController:
    from drivers.multi_device import MultiDeviceLEDController

    with contextlib.redirect_stdout(sys.stderr):
        return MultiDeviceLEDController(
            num_devices=RECEIVER_COUNT,
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
    plan = build_arm_plan(args.plan)
    args.source_identity = _preflight_source_and_topology()
    if not _service_active():
        raise DispatchDiagnosticError(
            f"{SERVICE} must be active so cleanup can be verified through its API"
        )

    controller: MultiDeviceLEDController | None = None
    arms: list[dict[str, Any]] = []
    cleanup_errors: list[BaseException] = []
    try:
        _wait_for_safe_idle()
        _service("stop")
        controller = _controller()
        if tuple(controller.device_map) != tuple(WALL_DEVICE_MAP):
            raise DispatchDiagnosticError("controller routes differ from installed topology")
        controller.set_brightness(0)
        black = np.zeros(
            (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 3), dtype=np.uint8
        )

        _apply_output_state(controller, production_state())
        _send_black_transfers(
            controller,
            black,
            target_fps=args.target_fps,
            transfers=args.warmup_transfers,
        )
        _fresh_validated_snapshots(controller, production_state())

        if args.output is not None:
            _write_report(args.output, _report(args, arms, complete=False))

        for index, arm in enumerate(plan):
            _apply_output_state(controller, arm.state)
            before = _fresh_validated_snapshots(controller, arm.state)
            elapsed = _send_black_transfers(
                controller,
                black,
                target_fps=args.target_fps,
                transfers=args.transfers,
            )
            after = _fresh_validated_snapshots(controller, arm.state)
            deltas = strict_counter_deltas(before, after)
            for receiver in deltas:
                logical_id = receiver["logical_device"]
                for field in ("frames_sent", "full_frame_transfers"):
                    if receiver[field] != args.transfers:
                        raise DispatchDiagnosticError(
                            f"receiver {logical_id} {field} advanced "
                            f"{receiver[field]}, expected {args.transfers}"
                        )
            arms.append(
                {
                    "arm_index": index,
                    "pair_id": arm.pair_id,
                    "role": arm.role,
                    "state": {
                        "label": arm.state.label,
                        "phases": list(arm.state.phases),
                        "lane_masks": list(arm.state.lane_masks),
                    },
                    "frames_attempted": args.transfers,
                    "elapsed_seconds": elapsed,
                    "attempted_fps": args.transfers / elapsed,
                    "receivers": deltas,
                }
            )
            if args.output is not None:
                _write_report(args.output, _report(args, arms, complete=False))

        result = _report(args, arms, complete=True)
        if args.output is not None:
            _write_report(args.output, result)
        return result
    finally:
        primary_error = sys.exc_info()[1]
        if controller is not None:
            try:
                controller.set_brightness(0)
                _apply_output_state(controller, production_state())
                controller.clear()
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                controller.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            _service("start")
            _wait_for_safe_idle()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            message = "; ".join(
                f"{type(error).__name__}: {error}" for error in cleanup_errors
            )
            if primary_error is not None:
                primary_error.add_note(f"black-idle cleanup also failed: {message}")
            else:
                raise DispatchDiagnosticError(
                    f"black-idle cleanup failed: {message}"
                )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", choices=("phase", "lane", "all"), default="all")
    parser.add_argument("--target-fps", type=int, default=120)
    parser.add_argument("--transfers", type=int, default=2000)
    parser.add_argument("--warmup-transfers", type=int, default=512)
    parser.add_argument(
        "--output",
        type=Path,
        help="atomically retain pure JSON progress and the completed report",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.target_fps <= 200:
        parser.error("target FPS must be from 1 through 200")
    if not 1 <= args.transfers <= 100_000:
        parser.error("transfers must be from 1 through 100000")
    if not 1 <= args.warmup_transfers <= 10_000:
        parser.error("warmup transfers must be from 1 through 10000")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    def interrupted(signum: int, _frame: Any) -> None:
        raise DispatchDiagnosticError(f"diagnostic interrupted by signal {signum}")

    handlers = {}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        handlers[signum] = signal.signal(signum, interrupted)
    try:
        result = run(parse_args(argv))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())

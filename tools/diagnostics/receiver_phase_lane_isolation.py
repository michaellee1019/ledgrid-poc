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
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
import re
import signal
import socket
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
    _json_request,
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
OPERATIONS_TELEMETRY_URL = (
    "http://127.0.0.1:5000/api/v1/composer/operations/telemetry"
)
RECEIVER_REFRESH_URL = "http://127.0.0.1:5000/api/v1/receivers/status/refresh"
RESET_COUNTER_FIELD = "receiver_fec_terminal_counter_resets"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
HARDWARE_SERIAL_PATTERN = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
BOARD_INVENTORY_SCHEMA = "ledgrid.receiver-board-route-inventory"
BOARD_INVENTORY_VERSION = 1
SWAP_CLASSIFICATION_MIN_FAULT_DELTA = 10
SWAP_CLASSIFICATION_MIN_RATE_RATIO = 3.0
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
    block_id: str | None = None
    condition: str | None = None


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


def build_arm_plan(plan: str, *, repeats: int = 2) -> tuple[ArmPlan, ...]:
    """Build deterministic A/B/A triplets for the requested isolation plan."""

    if plan not in {"phase", "lane", "all", "swap"}:
        raise ValueError("plan must be phase, lane, all, or swap")
    if type(repeats) is not int or not 2 <= repeats <= 10 or repeats % 2:
        raise ValueError("repeats must be an even value from two through ten")
    if plan == "swap":
        baseline = production_state()
        experiment = phase_experiments()[FEC_RECEIVER_ID]
        arms = []
        for repeat in range(repeats):
            block_id = f"swap-{repeat:02d}-receiver-3-phase1"
            conditions = ("A", "B", "B", "A") if repeat % 2 == 0 else (
                "B", "A", "A", "B"
            )
            counts = {"A": 0, "B": 0}
            for condition in conditions:
                counts[condition] += 1
                arms.append(
                    ArmPlan(
                        pair_id=block_id,
                        role=f"{condition}-{counts[condition]}",
                        state=baseline if condition == "A" else experiment,
                        block_id=block_id,
                        condition=condition,
                    )
                )
        return tuple(arms)
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


def require_exact_transfer_deltas(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    *,
    transfers: int,
) -> list[dict[str, int]]:
    """Reject a window unless every logical receiver completed every host transfer."""

    deltas = strict_counter_deltas(before, after)
    for receiver in deltas:
        logical_id = receiver["logical_device"]
        for field in ("frames_sent", "full_frame_transfers"):
            if receiver[field] != transfers:
                raise DispatchDiagnosticError(
                    f"receiver {logical_id} {field} advanced "
                    f"{receiver[field]}, expected {transfers}"
                )
    return deltas


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


def classify_targeted_swap_arms(
    arms: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify repeated counterbalanced receiver-3 phase arms conservatively."""

    blocks: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for arm in arms:
        block_id = arm.get("block_id")
        condition = arm.get("condition")
        if not isinstance(block_id, str) or condition not in {"A", "B"}:
            raise DispatchDiagnosticError("targeted swap arm metadata is malformed")
        blocks.setdefault(block_id, {"A": [], "B": []})[condition].append(arm)
    if len(blocks) < 2:
        raise DispatchDiagnosticError(
            "targeted swap evidence requires at least two counterbalanced blocks"
        )

    block_results = []
    pooled = {"A": {"faults": 0, "transfers": 0}, "B": {"faults": 0, "transfers": 0}}
    for block_id, conditions in blocks.items():
        if len(conditions["A"]) != 2 or len(conditions["B"]) != 2:
            raise DispatchDiagnosticError(
                f"{block_id} must contain exactly two A and two B arms"
            )
        row = {"block_id": block_id}
        for condition in ("A", "B"):
            faults = 0
            transfers = 0
            for arm in conditions[condition]:
                receivers = arm.get("receivers")
                if not isinstance(receivers, list) or len(receivers) != RECEIVER_COUNT:
                    raise DispatchDiagnosticError(
                        f"{block_id} {condition} lacks exact receiver evidence"
                    )
                victim = receivers[FEC_RECEIVER_ID]
                if not isinstance(victim, Mapping) or victim.get(
                    "logical_device"
                ) != FEC_RECEIVER_ID:
                    raise DispatchDiagnosticError(
                        f"{block_id} {condition} lacks receiver-3 evidence"
                    )
                fault = victim.get("receiver_crc_errors")
                sent = victim.get("full_frame_transfers")
                if (
                    type(fault) is not int
                    or fault < 0
                    or type(sent) is not int
                    or sent <= 0
                ):
                    raise DispatchDiagnosticError(
                        f"{block_id} {condition} has invalid fault-rate evidence"
                    )
                faults += fault
                transfers += sent
            row[condition] = {
                "receiver_3_crc_errors": faults,
                "full_frame_transfers": transfers,
                "errors_per_1000_transfers": faults * 1000.0 / transfers,
            }
            pooled[condition]["faults"] += faults
            pooled[condition]["transfers"] += transfers
        block_results.append(row)

    a_faults = pooled["A"]["faults"]
    b_faults = pooled["B"]["faults"]
    a_rate = a_faults / pooled["A"]["transfers"]
    b_rate = b_faults / pooled["B"]["transfers"]
    consistent_increase = all(row["B"]["errors_per_1000_transfers"] > row["A"]["errors_per_1000_transfers"] for row in block_results)
    consistent_decrease = all(row["B"]["errors_per_1000_transfers"] < row["A"]["errors_per_1000_transfers"] for row in block_results)
    if (
        consistent_increase
        and b_faults - a_faults >= SWAP_CLASSIFICATION_MIN_FAULT_DELTA
        and (a_rate == 0.0 or b_rate / a_rate >= SWAP_CLASSIFICATION_MIN_RATE_RATIO)
    ):
        classification = "experiment_increases_receiver_3_faults"
    elif (
        consistent_decrease
        and a_faults - b_faults >= SWAP_CLASSIFICATION_MIN_FAULT_DELTA
        and (b_rate == 0.0 or a_rate / b_rate >= SWAP_CLASSIFICATION_MIN_RATE_RATIO)
    ):
        classification = "experiment_decreases_receiver_3_faults"
    else:
        classification = "inconclusive"
    return {
        "classification": classification,
        "decision_rule": {
            "minimum_counterbalanced_blocks": 2,
            "minimum_fault_delta": SWAP_CLASSIFICATION_MIN_FAULT_DELTA,
            "minimum_rate_ratio": SWAP_CLASSIFICATION_MIN_RATE_RATIO,
            "requires_same_direction_in_every_block": True,
        },
        "conditions": {
            "A": "all receivers at production phase 3",
            "B": "logical receiver 3 at phase 1; all others at phase 3",
        },
        "pooled": {
            condition: {
                "receiver_3_crc_errors": values["faults"],
                "full_frame_transfers": values["transfers"],
                "errors_per_1000_transfers": (
                    values["faults"] * 1000.0 / values["transfers"]
                ),
            }
            for condition, values in pooled.items()
        },
        "blocks": block_results,
    }


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise DispatchDiagnosticError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _load_board_inventory(path: Path, *, label: str) -> dict[str, Any]:
    """Load one operator-reviewed physical-board-to-route binding."""

    try:
        metadata = path.lstat()
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DispatchDiagnosticError(f"could not load {label} board inventory: {exc}") from exc
    if path.is_symlink() or not path.is_file() or metadata.st_size > 64 * 1024:
        raise DispatchDiagnosticError(
            f"{label} board inventory must be a small regular non-symlink file"
        )
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "schema_version", "captured_at_utc", "boards"
    }:
        raise DispatchDiagnosticError(f"{label} board inventory keys are not exact")
    if (
        payload["schema"] != BOARD_INVENTORY_SCHEMA
        or payload["schema_version"] != BOARD_INVENTORY_VERSION
    ):
        raise DispatchDiagnosticError(f"{label} board inventory schema is unsupported")
    captured = payload["captured_at_utc"]
    if not isinstance(captured, str):
        raise DispatchDiagnosticError(f"{label} captured_at_utc is invalid")
    try:
        parsed = datetime.fromisoformat(captured.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DispatchDiagnosticError(f"{label} captured_at_utc is invalid") from exc
    if parsed.tzinfo is None:
        raise DispatchDiagnosticError(f"{label} captured_at_utc must include a timezone")
    boards = payload["boards"]
    if not isinstance(boards, list) or len(boards) != RECEIVER_COUNT:
        raise DispatchDiagnosticError(f"{label} must bind exactly five physical boards")
    normalized = []
    serials: set[str] = set()
    labels: set[str] = set()
    for logical_id, board in enumerate(boards):
        if not isinstance(board, dict) or set(board) != {
            "logical_device", "spi_route", "hardware_serial", "physical_label"
        }:
            raise DispatchDiagnosticError(f"{label} board {logical_id} keys are not exact")
        route = board["spi_route"]
        serial = board["hardware_serial"]
        physical_label = board["physical_label"]
        if board["logical_device"] != logical_id or route != list(WALL_DEVICE_MAP[logical_id]):
            raise DispatchDiagnosticError(
                f"{label} board {logical_id} does not match the installed logical route"
            )
        if not isinstance(serial, str) or HARDWARE_SERIAL_PATTERN.fullmatch(serial) is None:
            raise DispatchDiagnosticError(f"{label} board {logical_id} serial is invalid")
        if (
            not isinstance(physical_label, str)
            or not physical_label.strip()
            or len(physical_label) > 64
        ):
            raise DispatchDiagnosticError(f"{label} board {logical_id} label is invalid")
        if serial in serials or physical_label in labels:
            raise DispatchDiagnosticError(
                f"{label} contains duplicate hardware serials or physical labels"
            )
        serials.add(serial)
        labels.add(physical_label)
        normalized.append(dict(board))
    canonical = {
        "schema": BOARD_INVENTORY_SCHEMA,
        "schema_version": BOARD_INVENTORY_VERSION,
        "captured_at_utc": captured,
        "boards": normalized,
    }
    return {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        **canonical,
    }


def _bind_board_inventories(
    before_path: Path, after_path: Path, *, require_swap: bool
) -> dict[str, Any]:
    before = _load_board_inventory(before_path, label="pre-swap")
    after = _load_board_inventory(after_path, label="post-swap")
    before_time = datetime.fromisoformat(
        before["captured_at_utc"].replace("Z", "+00:00")
    )
    after_time = datetime.fromisoformat(after["captured_at_utc"].replace("Z", "+00:00"))
    if before_time > after_time or after_time > datetime.now(timezone.utc):
        raise DispatchDiagnosticError(
            "pre/post board inventory timestamps are reversed or in the future"
        )
    before_by_serial = {
        board["hardware_serial"]: board for board in before["boards"]
    }
    after_by_serial = {
        board["hardware_serial"]: board for board in after["boards"]
    }
    if set(before_by_serial) != set(after_by_serial):
        raise DispatchDiagnosticError(
            "pre/post board inventories must contain the same physical-board roster"
        )
    for serial, first in before_by_serial.items():
        if first["physical_label"] != after_by_serial[serial]["physical_label"]:
            raise DispatchDiagnosticError(
                f"physical label changed for board {serial} across the swap"
            )
    moved = [
        {
            "hardware_serial": serial,
            "physical_label": first["physical_label"],
            "from_logical_device": first["logical_device"],
            "to_logical_device": after_by_serial[serial]["logical_device"],
        }
        for serial, first in before_by_serial.items()
        if first["logical_device"] != after_by_serial[serial]["logical_device"]
    ]
    if require_swap and len(moved) != 2:
        raise DispatchDiagnosticError(
            "targeted swap plan requires exactly two physical boards to exchange routes"
        )
    if not require_swap and moved:
        raise DispatchDiagnosticError(
            "phase/lane diagnostics require an unchanged physical-board mapping"
        )
    return {"pre_swap": before, "post_swap": after, "moved_boards": moved}


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
        "warmup_transfers": args.warmup_transfers,
        "repeats": getattr(args, "repeats", 1),
        "device_map": [list(route) for route in WALL_DEVICE_MAP],
        "spi_speeds_hz": list(WALL_RECEIVER_SPI_SPEEDS_HZ),
        "repository_root": str(REPOSITORY_ROOT),
        "release_id": REPOSITORY_ROOT.name,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_identity": dict(getattr(args, "source_identity", {})),
        "run_identity": dict(getattr(args, "run_identity", {})),
        "physical_board_inventory": dict(
            getattr(args, "physical_board_inventory", {})
        ),
        "arms": list(arms),
    }
    if complete:
        if args.plan == "swap":
            result["targeted_swap_analysis"] = classify_targeted_swap_arms(arms)
        else:
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


def _preflight_source_and_topology(expected_script_sha256: str) -> dict[str, Any]:
    """Bind staged code to one selected immutable release and durable topology."""

    expected_script_sha256 = _strict_sha256(
        expected_script_sha256, "reviewed script SHA-256"
    )
    script_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if script_sha256 != expected_script_sha256:
        raise DispatchDiagnosticError(
            "executed diagnostic script does not match the reviewed SHA-256"
        )

    import drivers.led_layout as led_layout
    import drivers.multi_device as multi_device
    import drivers.spi_controller as spi_controller
    import tools.diagnostics.receiver_dispatch_order as dispatch

    module_paths = {
        "drivers.led_layout": Path(led_layout.__file__).resolve(),
        "drivers.multi_device": Path(multi_device.__file__).resolve(),
        "drivers.spi_controller": Path(spi_controller.__file__).resolve(),
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
    firmware_paths = {
        "receiver_firmware_inventory": (
            deployment_root / "run_state" / "receiver_firmware_inventory.json"
        ),
        "receiver_firmware_commit": (
            deployment_root / "run_state" / "receiver_firmware_commit.json"
        ),
        "receiver_firmware_installation": deployment_root / ".esp32_firmware_hash",
    }
    firmware_sha256 = {}
    for name, path in firmware_paths.items():
        try:
            metadata = path.lstat()
            content = path.read_bytes()
        except OSError as exc:
            raise DispatchDiagnosticError(
                f"could not bind {name}: {exc}"
            ) from exc
        if path.is_symlink() or not path.is_file() or metadata.st_size > 256 * 1024:
            raise DispatchDiagnosticError(
                f"{name} must be a bounded regular non-symlink file"
            )
        firmware_sha256[name] = hashlib.sha256(content).hexdigest()
    installation_digest = _strict_sha256(
        firmware_paths["receiver_firmware_installation"]
        .read_text(encoding="utf-8")
        .strip(),
        "receiver firmware installation digest",
    )
    machine_id_path = Path("/etc/machine-id")
    try:
        machine_id_sha256 = hashlib.sha256(machine_id_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DispatchDiagnosticError(f"could not bind target machine identity: {exc}") from exc
    return {
        "release_id": REPOSITORY_ROOT.name,
        "current_release": str(current.resolve()),
        "reviewed_script_sha256": expected_script_sha256,
        "executed_script_sha256": script_sha256,
        "hostname": socket.gethostname(),
        "machine_id_sha256": machine_id_sha256,
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "receiver_hybrid_digest": hashlib.sha256(canonical).hexdigest(),
        "receiver_hybrid_file_sha256": hashlib.sha256(raw).hexdigest(),
        "receiver_firmware_installation_digest": installation_digest,
        "firmware_authority_sha256": firmware_sha256,
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


def _post_service_production_observation(
    *, timeout: float = STATUS_SETTLE_TIMEOUT_SECONDS
) -> list[dict[str, Any]]:
    """Require service-owned fresh receiver proof after direct SPI cleanup."""

    accepted = _json_request(RECEIVER_REFRESH_URL, method="POST", payload={})
    request_id = accepted.get("request_id")
    if accepted.get("accepted") is not True or not isinstance(request_id, str) or not request_id:
        raise DispatchDiagnosticError(
            "service rejected the authoritative receiver-status refresh"
        )
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            telemetry = _json_request(OPERATIONS_TELEMETRY_URL)
            if (
                telemetry.get("schema") != "ledgrid.composer-operations-telemetry"
                or telemetry.get("schema_version") != 1
            ):
                raise DispatchDiagnosticError(
                    "service operations telemetry contract is unsupported"
                )
            diagnostics = telemetry.get("diagnostics")
            driver = (
                diagnostics.get("driver_stats")
                if isinstance(diagnostics, Mapping) else None
            )
            aggregate = driver.get("aggregate") if isinstance(driver, Mapping) else None
            refresh = (
                aggregate.get("receiver_status_refresh")
                if isinstance(aggregate, Mapping)
                else None
            )
            if (
                not isinstance(refresh, Mapping)
                or refresh.get("request_id") != request_id
                or refresh.get("passed") is not True
                or refresh.get("errors") != []
            ):
                raise DispatchDiagnosticError(
                    "service has not completed the requested receiver-status refresh"
                )
            devices = driver.get("devices") if isinstance(driver, Mapping) else None
            if not isinstance(devices, list) or len(devices) != RECEIVER_COUNT:
                raise DispatchDiagnosticError(
                    "service metrics lack the exact five-receiver roster"
                )
            snapshots = [dict(device) for device in devices if isinstance(device, Mapping)]
            if len(snapshots) != RECEIVER_COUNT:
                raise DispatchDiagnosticError("service receiver metrics are malformed")
            for logical_id, snapshot in enumerate(snapshots):
                validate_snapshot(snapshot, logical_id, production_state())
            return snapshots
        except Exception as exc:
            last_error = exc
            time.sleep(0.05)
    raise DispatchDiagnosticError(
        f"service did not authoritatively observe production receiver state: {last_error}"
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    plan = build_arm_plan(args.plan, repeats=args.repeats)
    args.source_identity = _preflight_source_and_topology(
        args.expected_script_sha256
    )
    args.physical_board_inventory = _bind_board_inventories(
        args.pre_board_inventory,
        args.post_board_inventory,
        require_swap=args.plan == "swap",
    )
    args.run_identity = {
        "capture_started_at_utc": _utc_now(),
        "capture_completed_at_utc": None,
        "cleanup_status": "pending",
    }
    if not _service_active():
        raise DispatchDiagnosticError(
            f"{SERVICE} must be active so cleanup can be verified through its API"
        )

    controller: MultiDeviceLEDController | None = None
    arms: list[dict[str, Any]] = []
    cleanup_errors: list[BaseException] = []
    execution_error: BaseException | None = None
    execution_traceback = None
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
        warm_before = _fresh_validated_snapshots(controller, production_state())
        _send_black_transfers(
            controller,
            black,
            target_fps=args.target_fps,
            transfers=args.warmup_transfers,
        )
        warm_after = _fresh_validated_snapshots(controller, production_state())
        require_exact_transfer_deltas(
            warm_before, warm_after, transfers=args.warmup_transfers
        )

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
            deltas = require_exact_transfer_deltas(
                before, after, transfers=args.transfers
            )
            arms.append(
                {
                    "arm_index": index,
                    "pair_id": arm.pair_id,
                    "role": arm.role,
                    "block_id": arm.block_id,
                    "condition": arm.condition,
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
    except BaseException as exc:
        execution_error = exc
        execution_traceback = exc.__traceback__
    finally:
        if controller is not None:
            for operation in (
                lambda: controller.set_brightness(0),
                lambda: _apply_output_state(controller, production_state()),
                controller.clear,
                controller.close,
            ):
                try:
                    operation()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        for operation in (
            lambda: _service("start"),
            _wait_for_safe_idle,
            _post_service_production_observation,
        ):
            try:
                operation()
            except BaseException as exc:
                cleanup_errors.append(exc)

    args.run_identity["capture_completed_at_utc"] = _utc_now()
    if cleanup_errors:
        message = "; ".join(
            f"{type(error).__name__}: {error}" for error in cleanup_errors
        )
        args.run_identity["cleanup_status"] = "failed"
        args.run_identity["cleanup_errors"] = message
        if args.output is not None:
            _write_report(args.output, _report(args, arms, complete=False))
        if execution_error is not None:
            execution_error.add_note(f"black-idle cleanup also failed: {message}")
            raise execution_error.with_traceback(execution_traceback)
        raise DispatchDiagnosticError(f"black-idle cleanup failed: {message}")
    args.run_identity["cleanup_status"] = "verified_production_black_idle"
    if execution_error is not None:
        if args.output is not None:
            _write_report(args.output, _report(args, arms, complete=False))
        raise execution_error.with_traceback(execution_traceback)

    result = _report(args, arms, complete=True)
    if args.output is not None:
        _write_report(args.output, result)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan", choices=("phase", "lane", "all", "swap"), default="all"
    )
    parser.add_argument("--target-fps", type=int, default=120)
    parser.add_argument("--transfers", type=int, default=2000)
    parser.add_argument("--warmup-transfers", type=int, default=512)
    parser.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="counterbalanced block count for the targeted swap plan",
    )
    parser.add_argument(
        "--expected-script-sha256",
        required=True,
        help="reviewed SHA-256 of the exact staged diagnostic script",
    )
    parser.add_argument(
        "--pre-board-inventory",
        type=Path,
        required=True,
        help="reviewed physical-board mapping before the swap or diagnostic",
    )
    parser.add_argument(
        "--post-board-inventory",
        type=Path,
        required=True,
        help="reviewed physical-board mapping after the swap or diagnostic",
    )
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
    if not 2 <= args.repeats <= 10 or args.repeats % 2:
        parser.error("repeats must be an even value from two through ten")
    if SHA256_PATTERN.fullmatch(args.expected_script_sha256) is None:
        parser.error("expected script SHA-256 must be 64 lowercase hexadecimal characters")
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

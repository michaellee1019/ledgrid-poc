#!/usr/bin/env python3
"""Generate the language-neutral animation-pipeline version 1 golden fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.compositing import (  # noqa: E402
    board_flat_slices,
    canvas_to_logical_flat_index,
    logical_flat_index,
    receiver_local_index,
    scale_premultiplied_rgba,
    source_over_rgb,
    source_over_rgba,
    union_dirty_ranges,
)


DEFAULT_OUTPUT = REPO_ROOT / "tests" / "fixtures" / "animation_pipeline_v1.json"
FIXTURE_SCHEMA = "ledgrid.animation-pipeline-golden"
FIXTURE_VERSION = 1


def _u16(value: int) -> bytes:
    return value.to_bytes(2, "big")


def _u32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def _u64(value: int) -> bytes:
    return value.to_bytes(8, "big")


def _crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (
                ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
            )
    return crc


def _packet_vector(
    vector_id: str,
    command: int,
    header: bytes,
    *,
    fields: Mapping[str, Any],
    payload: bytes = b"",
) -> Dict[str, Any]:
    if header[:2] != bytes((command, FIXTURE_VERSION)):
        raise ValueError(f"{vector_id} header does not start with command/version")
    before_crc = header + payload
    crc = _crc16_ccitt_false(before_crc)
    packet = before_crc + _u16(crc)
    return {
        "id": vector_id,
        "command": command,
        "header_bytes": len(header),
        "payload_bytes": len(payload),
        "packet_bytes": len(packet),
        "fields": dict(fields),
        "packet_hex": packet.hex(),
        "expected_crc16": crc,
    }


def _premultiplied_payload(start: int, count: int) -> bytes:
    """Return deterministic, valid premultiplied RGBA for local pixels."""

    payload = bytearray()
    for local_index in range(start, start + count):
        alpha = (local_index * 29 + 17) & 0xFF
        payload.extend((alpha // 4, alpha // 2, (alpha * 3) // 4, alpha))
    return bytes(payload)


def _receiver_slices(
    global_start: int,
    global_end: int,
    board_slices: Sequence[Sequence[int]],
) -> list[Dict[str, int]]:
    slices = []
    for board_index, (board_start, board_end) in enumerate(board_slices):
        clipped_start = max(global_start, board_start)
        clipped_end = min(global_end, board_end)
        if clipped_start >= clipped_end:
            continue
        slices.append(
            {
                "board_index": board_index,
                "global_start": clipped_start,
                "global_end": clipped_end,
                "local_start": clipped_start - board_start,
                "local_end": clipped_end - board_start,
                "source_offset": clipped_start - global_start,
            }
        )
    return slices


def _digest(seed: int) -> list[int]:
    return [((seed + index) & 0xFF) for index in range(32)]


def build_fixture() -> Dict[str, Any]:
    """Build deterministic inputs and exact expected bytes/indices."""

    blends = [
        ("transparent_black", [12, 34, 56], [0, 0, 0, 0]),
        ("opaque_black", [12, 34, 56], [0, 0, 0, 255]),
        ("opaque_color", [250, 200, 150], [17, 31, 47, 255]),
        ("half_up_rounding", [1, 2, 3], [0, 0, 0, 127]),
        ("channel_saturation", [255, 255, 255], [254, 253, 252, 254]),
        ("mixed_channels", [241, 17, 99], [32, 64, 16, 128]),
    ]
    opacity = [
        ("zero_endpoint", [99, 66, 33, 128], 0),
        ("opaque_endpoint", [99, 66, 33, 128], 255),
        ("half_up_to_one", [1, 1, 1, 1], 128),
        ("half_down_to_zero", [1, 1, 1, 1], 127),
        ("mixed_opacity", [120, 60, 30, 128], 200),
    ]
    folds = [
        ("transparent_top", [80, 40, 20, 128], [0, 0, 0, 0]),
        ("opaque_top", [80, 40, 20, 128], [5, 7, 9, 255]),
        ("ordered_overlap", [80, 20, 10, 128], [5, 60, 15, 96]),
        ("round_each_fold", [1, 1, 1, 1], [1, 0, 0, 128]),
    ]

    global_strips = 32
    leds_per_strip = 138
    local_strips = 8
    coordinate_inputs = [
        ("wall_origin", 0, 0, 0, True),
        ("board_0_last", 7, 137, 0, True),
        ("board_1_first", 8, 0, 8, True),
        ("board_1_last", 15, 137, 8, True),
        ("board_2_first", 16, 0, 16, True),
        ("board_2_last", 23, 137, 16, True),
        ("board_3_first", 24, 0, 24, True),
        ("wall_last", 31, 137, 24, True),
        ("before_local_offset", 7, 137, 8, False),
        ("after_local_range", 16, 0, 8, False),
        ("led_out_of_range", 24, 138, 24, False),
        ("strip_out_of_range", 32, 0, 24, False),
    ]
    coordinate_vectors = []
    for vector_id, strip, led, offset, valid in coordinate_inputs:
        global_valid = 0 <= strip < global_strips and 0 <= led < leds_per_strip
        expected_global = (
            logical_flat_index(
                strip, led, strip_count=global_strips, leds_per_strip=leds_per_strip
            )
            if global_valid
            else None
        )
        expected_local = (
            receiver_local_index(
                strip,
                led,
                global_strip_offset=offset,
                local_strip_count=local_strips,
                leds_per_strip=leds_per_strip,
            )
            if valid
            else None
        )
        coordinate_vectors.append(
            {
                "id": vector_id,
                "global_strip": strip,
                "led": led,
                "global_strip_offset": offset,
                "global_strips": global_strips,
                "local_strips": local_strips,
                "leds_per_strip": leds_per_strip,
                "global_valid": global_valid,
                "valid": valid,
                "expected_global_index": expected_global,
                "expected_local_index": expected_local,
            }
        )

    slices = board_flat_slices(
        global_strip_count=global_strips,
        leds_per_strip=leds_per_strip,
        strips_per_board=local_strips,
    )
    command_ids = {
        "controller_session_begin": 0x20,
        "overlay_begin": 0x30,
        "overlay_patch": 0x31,
        "overlay_commit": 0x32,
        "overlay_clear": 0x33,
        "overlay_renew": 0x34,
    }
    header_bytes = {
        "controller_session_begin": 58,
        "overlay_begin": 66,
        "overlay_patch": 30,
        "overlay_commit": 50,
        "overlay_clear": 34,
        "overlay_renew": 30,
    }
    session = bytes(range(0x10, 0x20))
    snapshot_digest = bytes(range(0x80, 0xA0))
    desired_revision = 0x0102030405060708
    generation = 0x1112131415161718
    prior_generation = 0x0102030405060708
    scene_revision = 0x2122232425262728
    scene_epoch = 0x3132333435363738
    base_revision = 0x4142434445464748
    present_at_scene_time = 0x5152535455565758
    lease_ms = 3000

    session_header = (
        bytes((command_ids["controller_session_begin"], FIXTURE_VERSION))
        + session
        + _u64(desired_revision)
        + snapshot_digest
    )

    def begin_header(update_kind: int, expected_patches: int) -> bytes:
        return (
            bytes((command_ids["overlay_begin"], FIXTURE_VERSION))
            + session
            + _u64(generation)
            + _u64(prior_generation)
            + _u64(scene_revision)
            + _u64(scene_epoch)
            + _u64(base_revision)
            + bytes((1, update_kind))
            + _u16(expected_patches)
            + _u32(lease_ms)
        )

    def patch_header(start: int, count: int) -> bytes:
        return (
            bytes((command_ids["overlay_patch"], FIXTURE_VERSION))
            + session
            + _u64(generation)
            + _u16(start)
            + _u16(count)
        )

    commit_header = (
        bytes((command_ids["overlay_commit"], FIXTURE_VERSION))
        + session
        + _u64(generation)
        + _u64(scene_epoch)
        + _u64(base_revision)
        + _u64(present_at_scene_time)
    )
    clear_header = (
        bytes((command_ids["overlay_clear"], FIXTURE_VERSION))
        + session
        + _u64(generation)
        + _u64(scene_revision)
    )
    renew_header = (
        bytes((command_ids["overlay_renew"], FIXTURE_VERSION))
        + session
        + _u64(generation)
        + _u32(lease_ms)
    )
    wire_packet_vectors = [
        _packet_vector(
            "controller_session_begin",
            command_ids["controller_session_begin"],
            session_header,
            fields={
                "controller_session_hex": session.hex(),
                "desired_revision": desired_revision,
                "authoritative_snapshot_digest_hex": snapshot_digest.hex(),
            },
        ),
        _packet_vector(
            "overlay_begin_full_snapshot",
            command_ids["overlay_begin"],
            begin_header(1, 2),
            fields={
                "controller_session_hex": session.hex(),
                "generation": generation,
                "prior_generation": prior_generation,
                "scene_revision": scene_revision,
                "scene_epoch": scene_epoch,
                "base_revision": base_revision,
                "format": 1,
                "update_kind": 1,
                "expected_patches": 2,
                "lease_ms": lease_ms,
            },
        ),
        _packet_vector(
            "overlay_begin_delta_noop",
            command_ids["overlay_begin"],
            begin_header(2, 0),
            fields={
                "controller_session_hex": session.hex(),
                "generation": generation,
                "prior_generation": prior_generation,
                "scene_revision": scene_revision,
                "scene_epoch": scene_epoch,
                "base_revision": base_revision,
                "format": 1,
                "update_kind": 2,
                "expected_patches": 0,
                "lease_ms": lease_ms,
            },
        ),
        _packet_vector(
            "overlay_patch_maximum",
            command_ids["overlay_patch"],
            patch_header(0, 1016),
            fields={
                "controller_session_hex": session.hex(),
                "generation": generation,
                "start": 0,
                "count": 1016,
            },
            payload=_premultiplied_payload(0, 1016),
        ),
        _packet_vector(
            "overlay_patch_tail",
            command_ids["overlay_patch"],
            patch_header(1016, 88),
            fields={
                "controller_session_hex": session.hex(),
                "generation": generation,
                "start": 1016,
                "count": 88,
            },
            payload=_premultiplied_payload(1016, 88),
        ),
        _packet_vector(
            "overlay_commit",
            command_ids["overlay_commit"],
            commit_header,
            fields={
                "controller_session_hex": session.hex(),
                "generation": generation,
                "scene_epoch": scene_epoch,
                "base_revision": base_revision,
                "present_at_scene_time": present_at_scene_time,
            },
        ),
        _packet_vector(
            "overlay_clear",
            command_ids["overlay_clear"],
            clear_header,
            fields={
                "controller_session_hex": session.hex(),
                "generation": generation,
                "scene_revision": scene_revision,
            },
        ),
        _packet_vector(
            "overlay_renew",
            command_ids["overlay_renew"],
            renew_header,
            fields={
                "controller_session_hex": session.hex(),
                "generation": generation,
                "lease_ms": lease_ms,
            },
        ),
    ]
    expected_headers = {command: size for command, size in header_bytes.items()}
    for vector in wire_packet_vectors:
        command_name = next(
            name
            for name, command_id in command_ids.items()
            if command_id == vector["command"]
        )
        if vector["header_bytes"] != expected_headers[command_name]:
            raise ValueError(f"{vector['id']} header size drifted")

    receiver_slice_inputs = (
        ("receiver_0_full", 0, 1104),
        ("receiver_1_full", 1104, 2208),
        ("receiver_2_full", 2208, 3312),
        ("receiver_3_full", 3312, 4416),
        ("boundary_0_to_1", 1103, 1105),
        ("boundary_1_to_2", 2207, 2209),
        ("boundary_2_to_3", 3311, 3313),
        ("whole_wall", 0, 4416),
    )
    receiver_slice_vectors = [
        {
            "id": vector_id,
            "global_start": start,
            "global_end": end,
            "expected_slices": _receiver_slices(start, end, slices),
        }
        for vector_id, start, end in receiver_slice_inputs
    ]

    operation_results = {
        "ok": 1,
        "idempotent": 2,
        "stale_generation": 9,
        "generation_conflict": 10,
        "prior_generation_mismatch": 11,
        "base_binding_mismatch": 15,
        "incomplete": 16,
        "lease_expired": 17,
        "invalid_state": 18,
        "counter_exhausted": 19,
    }
    generation_begin_vectors = [
        {
            "id": vector_id,
            "state": {
                "committed_generation": committed,
                "has_staged_generation": has_staged,
                "staged_generation": staged,
                "staged_operation_digest": _digest(staged_digest_seed),
            },
            "generation": candidate,
            "prior_generation": prior,
            "operation_digest": _digest(operation_digest_seed),
            "expected_result": operation_results[result],
        }
        for (
            vector_id,
            committed,
            has_staged,
            staged,
            staged_digest_seed,
            candidate,
            prior,
            operation_digest_seed,
            result,
        ) in (
            ("next_generation", 8, False, 0, 0, 9, 8, 0x12, "ok"),
            (
                "prior_generation_cas_mismatch",
                8,
                False,
                0,
                0,
                10,
                7,
                0x12,
                "prior_generation_mismatch",
            ),
            ("equal_to_committed", 8, False, 0, 0, 8, 8, 0x12, "stale_generation"),
            ("staged_exact_retry", 8, True, 9, 0x12, 9, 8, 0x12, "idempotent"),
            (
                "staged_conflicting_retry",
                8,
                True,
                9,
                0x12,
                9,
                8,
                0x56,
                "generation_conflict",
            ),
            ("older_than_staged", 8, True, 9, 0x12, 8, 8, 0x12, "stale_generation"),
            (
                "new_generation_while_staging",
                8,
                True,
                9,
                0x12,
                10,
                8,
                0x12,
                "invalid_state",
            ),
            (
                "counter_exhausted",
                (1 << 64) - 1,
                False,
                0,
                0,
                (1 << 64) - 1,
                (1 << 64) - 2,
                0x12,
                "counter_exhausted",
            ),
        )
    ]
    counter_order_vectors = [
        {
            "id": vector_id,
            "candidate": candidate,
            "current": current,
            "expected_relation": expected,
        }
        for vector_id, candidate, current, expected in (
            ("zero_equal", 0, 0, 0),
            ("adjacent_stale", 4, 5, -1),
            ("equal", 5, 5, 0),
            ("adjacent_newer", 6, 5, 1),
            ("maximum_newer", (1 << 64) - 1, (1 << 64) - 2, 1),
        )
    ]
    lease_commit_vectors = [
        {
            "id": vector_id,
            "expected_patches": expected_patches,
            "accepted_patches": accepted_patches,
            "last_start": last_start,
            "last_count": last_count,
            "update_kind": update_kind,
            "has_last_patch": has_last_patch,
            "base_binding_matches": base_binding_matches,
            "lease_expired": lease_expired,
            "expected_result": operation_results[result],
        }
        for (
            vector_id,
            expected_patches,
            accepted_patches,
            last_start,
            last_count,
            update_kind,
            has_last_patch,
            base_binding_matches,
            lease_expired,
            result,
        ) in (
            ("ready_before_expiry", 2, 2, 1016, 88, 1, True, True, False, "ok"),
            (
                "ready_after_expiry",
                2,
                2,
                1016,
                88,
                1,
                True,
                True,
                True,
                "lease_expired",
            ),
            (
                "binding_precedes_expiry",
                2,
                2,
                1016,
                88,
                1,
                True,
                False,
                True,
                "base_binding_mismatch",
            ),
            (
                "interrupted_before_expiry",
                2,
                1,
                0,
                1016,
                1,
                True,
                True,
                False,
                "incomplete",
            ),
            (
                "full_snapshot_missing_tail",
                1,
                1,
                0,
                1016,
                1,
                True,
                True,
                False,
                "incomplete",
            ),
            ("delta_ready", 1, 1, 24, 3, 2, True, True, False, "ok"),
        )
    ]
    commit_schedule_vectors = [
        {
            "id": vector_id,
            "present_at_scene_time": present_at_scene_time,
            "current_scene_time": current_scene_time,
            "should_present": should_present,
        }
        for vector_id, current_scene_time, should_present in (
            ("one_tick_before", present_at_scene_time - 1, False),
            ("exact_deadline", present_at_scene_time, True),
            ("one_tick_after", present_at_scene_time + 1, True),
        )
    ]
    return {
        "$schema": FIXTURE_SCHEMA,
        "version": FIXTURE_VERSION,
        "arithmetic": {
            "encoding": "premultiplied_rgba8",
            "rounding": "round_half_up_divide_by_255",
            "product_formula": "(value * factor + 127) // 255",
            "source_over_order": "bottom_to_top",
        },
        "firmware_protocol": {
            "version": 1,
            "max_transaction_bytes": 4096,
            "crc_bytes": 2,
            "command_ids": command_ids,
            "header_bytes": header_bytes,
            "max_rgba_pixels_per_patch": 1016,
            "local_pixels": 1104,
            "full_snapshot_patches": [
                {"start": 0, "count": 1016},
                {"start": 1016, "count": 88},
            ],
            "wire_packet_vectors": wire_packet_vectors,
            "counter_order_vectors": counter_order_vectors,
            "generation_begin_vectors": generation_begin_vectors,
            "lease_commit_vectors": lease_commit_vectors,
            "commit_schedule_vectors": commit_schedule_vectors,
            "receiver_slice_vectors": receiver_slice_vectors,
        },
        "blend_vectors": [
            {
                "id": vector_id,
                "base_rgb": base,
                "overlay_rgba": overlay,
                "expected_rgb": list(source_over_rgb(base, overlay)),
            }
            for vector_id, base, overlay in blends
        ],
        "opacity_vectors": [
            {
                "id": vector_id,
                "input_rgba": rgba,
                "opacity": factor,
                "expected_rgba": list(scale_premultiplied_rgba(rgba, factor)),
            }
            for vector_id, rgba, factor in opacity
        ],
        "overlay_fold_vectors": [
            {
                "id": vector_id,
                "bottom_rgba": bottom,
                "top_rgba": top,
                "expected_rgba": list(source_over_rgba(bottom, top)),
            }
            for vector_id, bottom, top in folds
        ],
        "dirty_range_vectors": [
            {
                "id": vector_id,
                "pixel_count": pixel_count,
                "previous_coverage": previous,
                "next_coverage": current,
                "expected_union": [
                    list(item)
                    for item in union_dirty_ranges(
                        previous, current, pixel_count=pixel_count
                    )
                ],
            }
            for vector_id, pixel_count, previous, current in (
                ("movement", 64, [[10, 13]], [[20, 23]]),
                (
                    "overlap_and_adjacency",
                    64,
                    [[10, 14], [30, 34]],
                    [[13, 18], [34, 36]],
                ),
                ("complete_clear", 64, [[4, 9], [40, 44]], []),
                ("empty", 64, [], []),
            )
        ],
        "coordinate_vectors": coordinate_vectors,
        "canvas_to_logical_vectors": [
            {
                "id": vector_id,
                "canvas_row": row,
                "canvas_column": column,
                "global_strips": global_strips,
                "leds_per_strip": leds_per_strip,
                "expected_global_index": canvas_to_logical_flat_index(
                    row,
                    column,
                    strip_count=global_strips,
                    leds_per_strip=leds_per_strip,
                ),
            }
            for vector_id, row, column in (
                ("canvas_top_left", 0, 0),
                ("canvas_first_row_last_strip", 0, 31),
                ("canvas_last_row_first_strip", 137, 0),
                ("canvas_bottom_right", 137, 31),
                ("canvas_interior", 73, 18),
            )
        ],
        "board_slices": {
            "global_strips": global_strips,
            "leds_per_strip": leds_per_strip,
            "strips_per_board": local_strips,
            "boards": [
                {
                    "board_index": board_index,
                    "global_strip_offset": board_index * local_strips,
                    "start_flat_index": start,
                    "end_flat_index": end,
                    "pixel_count": end - start,
                }
                for board_index, (start, end) in enumerate(slices)
            ],
        },
    }


def render_fixture() -> str:
    return json.dumps(build_fixture(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when output differs",
    )
    args = parser.parse_args()
    rendered = render_fixture()
    if args.check:
        if (
            not args.output.is_file()
            or args.output.read_text(encoding="utf-8") != rendered
        ):
            parser.error(f"fixture is stale; regenerate {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

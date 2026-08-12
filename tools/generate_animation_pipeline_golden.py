#!/usr/bin/env python3
"""Generate the language-neutral animation-pipeline version 1 golden fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.compositing import (
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
            "command_ids": {
                "controller_session_begin": 32,
                "overlay_begin": 48,
                "overlay_patch": 49,
                "overlay_commit": 50,
                "overlay_clear": 51,
                "overlay_renew": 52,
            },
            "header_bytes": {
                "controller_session_begin": 58,
                "overlay_begin": 66,
                "overlay_patch": 30,
                "overlay_commit": 50,
                "overlay_clear": 34,
                "overlay_renew": 30,
            },
            "max_rgba_pixels_per_patch": 1016,
            "local_pixels": 1104,
            "full_snapshot_patches": [
                {"start": 0, "count": 1016},
                {"start": 1016, "count": 88},
            ],
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
                    for item in union_dirty_ranges(previous, current, pixel_count=pixel_count)
                ],
            }
            for vector_id, pixel_count, previous, current in (
                ("movement", 64, [[10, 13]], [[20, 23]]),
                ("overlap_and_adjacency", 64, [[10, 14], [30, 34]], [[13, 18], [34, 36]]),
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
        "--check", action="store_true", help="fail instead of writing when output differs"
    )
    args = parser.parse_args()
    rendered = render_fixture()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            parser.error(f"fixture is stale; regenerate {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

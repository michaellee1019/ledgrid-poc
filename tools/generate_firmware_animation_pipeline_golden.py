#!/usr/bin/env python3
"""Derive the portable C++ animation-pipeline vectors from the JSON authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "tests" / "fixtures" / "animation_pipeline_v1.json"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "firmware"
    / "esp32"
    / "test"
    / "fixtures"
    / "animation_pipeline_v1.hpp"
)
MAX_DIRTY_RANGES_PER_GROUP = 8
MAX_DIRTY_UNION_RANGES = MAX_DIRTY_RANGES_PER_GROUP * 2


def _bytes(values: Sequence[int]) -> str:
    return "{" + ", ".join(str(value) for value in values) + "}"


def _rows(items: Iterable[str]) -> str:
    return "\n".join(f"    {item}," for item in items)


def _range_slots(ranges: Sequence[Sequence[int]], capacity: int) -> str:
    if len(ranges) > capacity:
        raise ValueError(f"dirty-range fixture exceeds bounded capacity {capacity}")
    slots = [f"{{{start}, {end}}}" for start, end in ranges]
    slots.extend("{0, 0}" for _ in range(capacity - len(slots)))
    return "{" + ", ".join(slots) + "}"


def render_header(fixture: Mapping[str, Any]) -> str:
    if fixture.get("$schema") != "ledgrid.animation-pipeline-golden":
        raise ValueError("unexpected golden fixture schema")
    if fixture.get("version") != 1:
        raise ValueError("unexpected golden fixture version")

    protocol = fixture["firmware_protocol"]
    commands = protocol["command_ids"]
    headers = protocol["header_bytes"]
    patches = protocol["full_snapshot_patches"]
    blends = fixture["blend_vectors"]
    opacities = fixture["opacity_vectors"]
    folds = fixture["overlay_fold_vectors"]
    dirty_ranges = fixture["dirty_range_vectors"]
    coordinates = fixture["coordinate_vectors"]
    boards = fixture["board_slices"]["boards"]

    blend_rows = _rows(
        f'{{"{vector["id"]}", {_bytes(vector["base_rgb"])}, '
        f'{_bytes(vector["overlay_rgba"])}, {_bytes(vector["expected_rgb"])}}}'
        for vector in blends
    )
    opacity_rows = _rows(
        f'{{"{vector["id"]}", {_bytes(vector["input_rgba"])}, '
        f'{vector["opacity"]}, {_bytes(vector["expected_rgba"])}}}'
        for vector in opacities
    )
    fold_rows = _rows(
        f'{{"{vector["id"]}", {_bytes(vector["bottom_rgba"])}, '
        f'{_bytes(vector["top_rgba"])}, {_bytes(vector["expected_rgba"])}}}'
        for vector in folds
    )
    dirty_range_rows = _rows(
        f'{{"{vector["id"]}", {vector["pixel_count"]}, '
        f'{len(vector["previous_coverage"])}, '
        f'{_range_slots(vector["previous_coverage"], MAX_DIRTY_RANGES_PER_GROUP)}, '
        f'{len(vector["next_coverage"])}, '
        f'{_range_slots(vector["next_coverage"], MAX_DIRTY_RANGES_PER_GROUP)}, '
        f'{len(vector["expected_union"])}, '
        f'{_range_slots(vector["expected_union"], MAX_DIRTY_UNION_RANGES)}}}'
        for vector in dirty_ranges
    )
    coordinate_rows = _rows(
        f'{{"{vector["id"]}", {vector["global_strip"]}, {vector["led"]}, '
        f'{vector["global_strip_offset"]}, {vector["global_strips"]}, '
        f'{vector["local_strips"]}, {vector["leds_per_strip"]}, '
        f'{str(vector["global_valid"]).lower()}, '
        f'{str(vector["valid"]).lower()}, '
        f'{vector["expected_global_index"] if vector["expected_global_index"] is not None else 0}, '
        f'{vector["expected_local_index"] if vector["expected_local_index"] is not None else 0}}}'
        for vector in coordinates
    )
    board_rows = _rows(
        f'{{{board["board_index"]}, {board["global_strip_offset"]}, '
        f'{board["start_flat_index"]}, {board["end_flat_index"]}, '
        f'{board["pixel_count"]}}}'
        for board in boards
    )
    patch_rows = _rows(
        f'{{{patch["start"]}, {patch["count"]}}}' for patch in patches
    )

    return f"""// Generated from tests/fixtures/animation_pipeline_v1.json.
// Run tools/generate_firmware_animation_pipeline_golden.py after changing it.
#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {{
namespace golden_v1 {{

struct BlendVector {{
  const char* id;
  std::uint8_t base_rgb[3];
  std::uint8_t overlay_rgba[4];
  std::uint8_t expected_rgb[3];
}};

struct OpacityVector {{
  const char* id;
  std::uint8_t input_rgba[4];
  std::uint8_t opacity;
  std::uint8_t expected_rgba[4];
}};

struct OverlayFoldVector {{
  const char* id;
  std::uint8_t bottom_rgba[4];
  std::uint8_t top_rgba[4];
  std::uint8_t expected_rgba[4];
}};

constexpr std::size_t kMaxDirtyRangesPerGroup = {MAX_DIRTY_RANGES_PER_GROUP};
constexpr std::size_t kMaxDirtyUnionRanges = {MAX_DIRTY_UNION_RANGES};

struct DirtyRange {{
  std::uint32_t start;
  std::uint32_t end;
}};

struct DirtyRangeVector {{
  const char* id;
  std::uint32_t pixel_count;
  std::size_t previous_count;
  DirtyRange previous_coverage[kMaxDirtyRangesPerGroup];
  std::size_t next_count;
  DirtyRange next_coverage[kMaxDirtyRangesPerGroup];
  std::size_t expected_count;
  DirtyRange expected_union[kMaxDirtyUnionRanges];
}};

struct CoordinateVector {{
  const char* id;
  std::uint16_t global_strip;
  std::uint16_t led;
  std::uint16_t global_strip_offset;
  std::uint16_t global_strips;
  std::uint16_t local_strips;
  std::uint16_t leds_per_strip;
  bool global_valid;
  bool valid;
  std::uint32_t expected_global_index;
  std::uint32_t expected_local_index;
}};

struct BoardSliceVector {{
  std::uint8_t board_index;
  std::uint16_t global_strip_offset;
  std::uint32_t start_flat_index;
  std::uint32_t end_flat_index;
  std::uint32_t pixel_count;
}};

struct SnapshotPatchVector {{
  std::uint16_t start;
  std::uint16_t count;
}};

constexpr std::uint8_t kProtocolVersion = {protocol["version"]};
constexpr std::size_t kMaxTransactionBytes = {protocol["max_transaction_bytes"]};
constexpr std::size_t kCrcBytes = {protocol["crc_bytes"]};
constexpr std::size_t kMaxRgbaPixelsPerPatch = {protocol["max_rgba_pixels_per_patch"]};
constexpr std::size_t kLocalPixels = {protocol["local_pixels"]};

constexpr std::uint8_t kControllerSessionBeginCommand = {commands["controller_session_begin"]};
constexpr std::uint8_t kOverlayBeginCommand = {commands["overlay_begin"]};
constexpr std::uint8_t kOverlayPatchCommand = {commands["overlay_patch"]};
constexpr std::uint8_t kOverlayCommitCommand = {commands["overlay_commit"]};
constexpr std::uint8_t kOverlayClearCommand = {commands["overlay_clear"]};
constexpr std::uint8_t kOverlayRenewCommand = {commands["overlay_renew"]};

constexpr std::size_t kControllerSessionBeginHeaderBytes = {headers["controller_session_begin"]};
constexpr std::size_t kOverlayBeginHeaderBytes = {headers["overlay_begin"]};
constexpr std::size_t kOverlayPatchHeaderBytes = {headers["overlay_patch"]};
constexpr std::size_t kOverlayCommitHeaderBytes = {headers["overlay_commit"]};
constexpr std::size_t kOverlayClearHeaderBytes = {headers["overlay_clear"]};
constexpr std::size_t kOverlayRenewHeaderBytes = {headers["overlay_renew"]};

constexpr BlendVector kBlendVectors[] = {{
{blend_rows}
}};

constexpr OpacityVector kOpacityVectors[] = {{
{opacity_rows}
}};

constexpr OverlayFoldVector kOverlayFoldVectors[] = {{
{fold_rows}
}};

constexpr DirtyRangeVector kDirtyRangeVectors[] = {{
{dirty_range_rows}
}};

constexpr CoordinateVector kCoordinateVectors[] = {{
{coordinate_rows}
}};

constexpr BoardSliceVector kBoardSlices[] = {{
{board_rows}
}};

constexpr SnapshotPatchVector kFullSnapshotPatches[] = {{
{patch_rows}
}};

}}  // namespace golden_v1
}}  // namespace ledgrid
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing when stale"
    )
    args = parser.parse_args()
    fixture = json.loads(args.input.read_text(encoding="utf-8"))
    rendered = render_header(fixture)
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            parser.error(f"firmware fixture is stale; regenerate {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

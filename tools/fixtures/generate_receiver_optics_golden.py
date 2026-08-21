#!/usr/bin/env python3
"""Generate the shared Phase 3C fixed-point receiver-optics goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.installation_profile import compile_installation_profile  # noqa: E402
from animation.core.installation_profile_topology import (  # noqa: E402
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
    slice_installation_profile,
)
from animation.core.receiver_optics import (  # noqa: E402
    HUE_MATRIX_ROUND,
    HUE_MATRIX_SCALE,
    HUE_MATRIX_SHIFT,
    HUE_ROTATION_MATRICES_Q14,
    HUE_STRENGTH_MAX,
    apply_hue_shift_u8,
    hue_rotation_matrix_q14,
)


DEFAULT_OUTPUT = REPO_ROOT / "tests" / "fixtures" / "receiver_optics_v1.json"
DEFAULT_CPP_OUTPUT = (
    REPO_ROOT / "firmware" / "esp32" / "test" / "fixtures"
    / "receiver_optics_v1.hpp"
)
DEFAULT_COEFFICIENTS_OUTPUT = (
    REPO_ROOT / "firmware" / "esp32" / "include" / "ledgrid"
    / "receiver_optics_coefficients_v1.hpp"
)
FIXTURE_SCHEMA = "ledgrid.receiver-optics-golden"
FIXTURE_VERSION = 1
VECTOR_STRENGTHS = (0, 1, 64, 128, 256)
TOPOLOGY_STRENGTHS = (64, 256)
RGB_VECTORS = (
    ("black", (0, 0, 0)),
    ("white", (255, 255, 255)),
    ("red_extreme", (255, 0, 0)),
    ("green_extreme", (0, 255, 0)),
    ("blue_extreme", (0, 0, 255)),
    ("warm_orange", (255, 128, 0)),
    ("cool_teal", (12, 160, 200)),
    ("muted_blue", (73, 99, 141)),
    ("magenta_clip", (255, 0, 255)),
    ("yellow_clip", (255, 255, 0)),
)


def _matrix_bytes() -> bytes:
    return b"".join(
        struct.pack(">h", coefficient)
        for matrix in HUE_ROTATION_MATRICES_Q14
        for row in matrix
        for coefficient in row
    )


def _unclamped_rgb(input_rgb: Sequence[int], strength: int) -> tuple[int, int, int]:
    matrix = hue_rotation_matrix_q14(strength)
    return tuple(
        (
            sum(matrix[output][channel] * input_rgb[channel]
                for channel in range(3))
            + HUE_MATRIX_ROUND
        ) // HUE_MATRIX_SCALE
        for output in range(3)
    )


def _vector(vector_id: str, rgb: tuple[int, int, int], strength: int) -> dict[str, Any]:
    unclamped = _unclamped_rgb(rgb, strength)
    expected = tuple(min(255, max(0, channel)) for channel in unclamped)
    pixels = np.asarray((rgb,), dtype=np.uint8)
    result = apply_hue_shift_u8(
        pixels.copy(), strength, np.ones(1, dtype=np.bool_)
    )
    if tuple(int(channel) for channel in result[0]) != expected:
        raise AssertionError("vector scalar and array references disagree")
    return {
        "id": f"{vector_id}_s{strength}",
        "strength_q8_8": strength,
        "input_rgb": list(rgb),
        "unclamped_rgb": list(unclamped),
        "expected_rgb": list(expected),
    }


def _analytic_rgb_field() -> np.ndarray:
    """Return a deterministic 32x138 field keyed only by global coordinates."""

    strip = np.arange(32, dtype=np.uint16)[:, None]
    led = np.arange(138, dtype=np.uint16)[None, :]
    field = np.empty((32, 138, 3), dtype=np.uint8)
    field[..., 0] = (strip * 37 + led * 11 + 17) & 0xFF
    field[..., 1] = (strip * 7 + led * 29 + 73) & 0xFF
    field[..., 2] = (strip * 53 + led * 3 + 151) & 0xFF
    return field


def _topology_vectors() -> list[dict[str, Any]]:
    profile = compile_installation_profile(clearance_radius=1)
    views = slice_installation_profile(
        profile, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
    )
    source = _analytic_rgb_field()
    vectors: list[dict[str, Any]] = []
    for strength in TOPOLOGY_STRENGTHS:
        receiver_digests: list[str] = []
        stitched = np.empty_like(source)
        for logical_id in range(4):
            view = views[logical_id]
            local = source[
                view.strip_origin:view.strip_origin + view.strip_count
            ]
            if view.reversed_strip_order:
                local = local[::-1]
            local = np.array(local, dtype=np.uint8, order="C", copy=True)
            apply_hue_shift_u8(
                local.reshape(-1, 3),
                strength,
                view.obstacle.reshape(-1),
            )
            receiver_digests.append(hashlib.sha256(local.tobytes()).hexdigest())
            global_rows = local[::-1] if view.reversed_strip_order else local
            stitched[
                view.strip_origin:view.strip_origin + view.strip_count
            ] = global_rows

        direct = source.copy()
        apply_hue_shift_u8(
            direct.reshape(-1, 3), strength, profile.obstacle.reshape(-1)
        )
        if not np.array_equal(stitched, direct):
            raise AssertionError("installed receiver slices do not stitch globally")
        vectors.append({
            "strength_q8_8": strength,
            "receiver_sha256": receiver_digests,
            "stitched_global_sha256": hashlib.sha256(stitched.tobytes()).hexdigest(),
        })
    return vectors


def build_fixture() -> dict[str, Any]:
    return {
        "$schema": FIXTURE_SCHEMA,
        "version": FIXTURE_VERSION,
        "fixed_point": {
            "strength_format": "unsigned_q8_8",
            "strength_range": [0, HUE_STRENGTH_MAX],
            "coefficient_format": "signed_q14",
            "coefficient_scale": HUE_MATRIX_SCALE,
            "coefficient_rounding": "nearest_ties_away_from_zero",
            "channel_apply": (
                "floor((matrix_row_dot_rgb + 8192) / 16384), then clamp 0..255"
            ),
            "matrix_layout": "strength_output_channel_input_rgb",
            "zero_strength": "explicit_exact_identity",
        },
        "matrix_table": {
            "shape": [HUE_STRENGTH_MAX + 1, 3, 3],
            "digest_encoding": "row_major_signed_int16_big_endian",
            "sha256": hashlib.sha256(_matrix_bytes()).hexdigest(),
            "matrices_q14": HUE_ROTATION_MATRICES_Q14,
        },
        "rgb_vectors": [
            _vector(vector_id, rgb, strength)
            for strength in VECTOR_STRENGTHS
            for vector_id, rgb in RGB_VECTORS
        ],
        "installed_topology": {
            "logical_receiver_order": [0, 1, 2, 3],
            "strip_origins": [0, 8, 24, 16],
            "reverse_native_strips": [False, False, True, True],
            "field_rgb": [
                "(global_strip * 37 + led * 11 + 17) & 255",
                "(global_strip * 7 + led * 29 + 73) & 255",
                "(global_strip * 53 + led * 3 + 151) & 255",
            ],
            "target": "compiled_v1_profile_exact_obstacle",
            "vectors": _topology_vectors(),
        },
    }


def render_fixture(fixture: dict[str, Any] | None = None) -> str:
    payload = build_fixture() if fixture is None else fixture
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_coefficients_header(fixture: dict[str, Any] | None = None) -> str:
    payload = build_fixture() if fixture is None else fixture
    digest = payload["matrix_table"]["sha256"]
    lines = [
        "// Generated by tools/fixtures/generate_receiver_optics_golden.py.",
        "// Do not edit by hand.",
        "#pragma once",
        "",
        "#include <cstdint>",
        "",
        "namespace ledgrid::receiver_optics_v1 {",
        "",
        f"inline constexpr std::uint16_t kHueStrengthMax = {HUE_STRENGTH_MAX};",
        f"inline constexpr std::uint8_t kMatrixShift = {HUE_MATRIX_SHIFT};",
        f"inline constexpr std::int32_t kMatrixRound = {HUE_MATRIX_ROUND};",
        f'inline constexpr char kMatrixSha256[] = "{digest}";',
        "",
        "inline constexpr std::int16_t kHueShiftMatricesQ14[257][3][3] = {",
    ]
    for strength, matrix in enumerate(HUE_ROTATION_MATRICES_Q14):
        rows = ", ".join(
            "{" + ", ".join(str(value) for value in row) + "}"
            for row in matrix
        )
        lines.append(f"    {{{rows}}},  // {strength}/256")
    lines.extend((
        "};",
        "",
        "}  // namespace ledgrid::receiver_optics_v1",
        "",
    ))
    return "\n".join(lines)


def render_cpp_header(fixture: dict[str, Any] | None = None) -> str:
    payload = build_fixture() if fixture is None else fixture
    lines = [
        "// Generated by tools/fixtures/generate_receiver_optics_golden.py.",
        "// Do not edit by hand.",
        "#pragma once",
        "",
        "#include <cstdint>",
        '#include "ledgrid/receiver_optics_coefficients_v1.hpp"',
        "",
        "namespace ledgrid::golden_receiver_optics_v1 {",
        "",
        "struct RgbVector {",
        "  const char* id;",
        "  std::uint16_t strength_q8_8;",
        "  std::uint8_t input_rgb[3];",
        "  std::int16_t unclamped_rgb[3];",
        "  std::uint8_t expected_rgb[3];",
        "};",
        "",
        "constexpr RgbVector kRgbVectors[] = {",
    ]
    for vector in payload["rgb_vectors"]:
        input_rgb = ", ".join(str(value) for value in vector["input_rgb"])
        unclamped = ", ".join(str(value) for value in vector["unclamped_rgb"])
        expected = ", ".join(str(value) for value in vector["expected_rgb"])
        lines.append(
            f"    {{{json.dumps(vector['id'])}, {vector['strength_q8_8']}, "
            f"{{{input_rgb}}}, {{{unclamped}}}, {{{expected}}}}},"
        )
    lines.extend((
        "};",
        "",
        "struct InstalledTopologyVector {",
        "  std::uint16_t strength_q8_8;",
        "  const char* receiver_sha256[4];",
        "  const char* stitched_global_sha256;",
        "};",
        "",
        "constexpr InstalledTopologyVector kInstalledTopologyVectors[] = {",
    ))
    for vector in payload["installed_topology"]["vectors"]:
        digests = ", ".join(
            json.dumps(digest) for digest in vector["receiver_sha256"]
        )
        lines.append(
            f"    {{{vector['strength_q8_8']}, {{{digests}}}, "
            f"{json.dumps(vector['stitched_global_sha256'])}}},"
        )
    lines.extend((
        "};",
        "",
        "static_assert(receiver_optics_v1::kHueStrengthMax == 256);",
        "static_assert(receiver_optics_v1::kHueShiftMatricesQ14[0][0][0] == 16384);",
        "",
        "}  // namespace ledgrid::golden_receiver_optics_v1",
        "",
    ))
    return "\n".join(lines)


def _check_or_write(path: Path, rendered: str, *, check: bool) -> None:
    if check:
        if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
            raise ValueError(f"fixture is stale; regenerate {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cpp-output", type=Path, default=DEFAULT_CPP_OUTPUT)
    parser.add_argument(
        "--coefficients-output", type=Path, default=DEFAULT_COEFFICIENTS_OUTPUT
    )
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing when output differs"
    )
    args = parser.parse_args(argv)
    fixture = build_fixture()
    rendered_outputs = (
        (args.output, render_fixture(fixture)),
        (args.cpp_output, render_cpp_header(fixture)),
        (args.coefficients_output, render_coefficients_header(fixture)),
    )
    try:
        for path, rendered in rendered_outputs:
            _check_or_write(path, rendered, check=args.check)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

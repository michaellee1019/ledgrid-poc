#!/usr/bin/env python3
"""Generate language-neutral Phase 3A receiver-presentation vectors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.plant_awareness import PLANT_MODIFIER_IDS, PlantModifierState
from animation.core.presentation_contracts import VIBE_PALETTE_ROLES, resolve_vibe
from animation.core.receiver_presentation import (
    BEGIN_BYTES,
    COMMIT_BYTES,
    PRESENTATION_CONTEXT_BEGIN,
    PRESENTATION_CONTEXT_COMMIT,
    PRESENTATION_CONTEXT_SET,
    PRESENTATION_CONTEXT_VERSION,
    Q8_8_ONE,
    SET_BASE_BYTES,
    SET_ENTRY_BYTES,
    SET_MAX_BYTES,
    VIBE_ID_TO_WIRE,
    ReceiverPresentationContext,
    apply_luminance_u8,
    quantize_q8_8,
    serialize_presentation_context,
)


DEFAULT_OUTPUT = REPO_ROOT / "tests" / "fixtures" / "receiver_presentation_v1.json"
DEFAULT_CPP_OUTPUT = (
    REPO_ROOT / "firmware" / "esp32" / "test" / "fixtures"
    / "receiver_presentation_v1.hpp"
)
FIXTURE_SCHEMA = "ledgrid.receiver-presentation-golden"
FIXTURE_VERSION = 1


def _case(
    case_id: str,
    *,
    session_hex: str,
    scene_revision: int,
    scene_epoch: int,
    present_at_scene_time_us: int,
    vibe_id: str,
    vibe_revision: int,
    plant_revision: int,
    plant_payload: dict[str, Any],
) -> dict[str, Any]:
    vibe = resolve_vibe(vibe_id, revision=vibe_revision)
    plants = PlantModifierState.from_payload(plant_payload)
    context = ReceiverPresentationContext(
        controller_session_id=bytes.fromhex(session_hex),
        scene_revision=scene_revision,
        scene_epoch=scene_epoch,
        present_at_scene_time_us=present_at_scene_time_us,
        vibe=vibe,
        plant_modifiers=plants,
        plant_revision=plant_revision,
    )
    begin, set_packet, commit = serialize_presentation_context(context)
    profile = vibe.profile
    return {
        "id": case_id,
        "input": {
            "controller_session_id_hex": session_hex,
            "scene_revision": scene_revision,
            "scene_epoch": scene_epoch,
            "present_at_scene_time_us": present_at_scene_time_us,
            "vibe": {
                "vibe_id": vibe_id,
                "profile_version": profile.profile_version,
                "revision": vibe_revision,
                "resolved_profile_digest": vibe.state.resolved_profile_digest,
                "palette_roles": {
                    role: list(profile.palette_roles[role]) for role in VIBE_PALETTE_ROLES
                },
                "scalars": {
                    "tempo_scale": profile.tempo_scale,
                    "luminance_scale": profile.luminance_scale,
                    "chroma_scale": profile.capability_values["chroma_scale"],
                    "energy": profile.capability_values["energy"],
                },
            },
            "plant_revision": plant_revision,
            "plant_modifiers": plants.to_dict(),
        },
        "expected": {
            "plant_digest_hex": context.plant_digest.hex(),
            "context_digest_hex": context.context_digest.hex(),
            "begin_bytes": len(begin),
            "begin_hex": begin.hex(),
            "set_bytes": len(set_packet),
            "set_hex": set_packet.hex(),
            "commit_bytes": len(commit),
            "commit_hex": commit.hex(),
        },
    }


def build_fixture() -> dict[str, Any]:
    scalar_inputs = (
        ("zero", 0.0),
        ("below_half_lsb", 0.001953124),
        ("half_lsb_up", 0.001953125),
        ("above_half_lsb", 0.001953126),
        ("quiet_luminance", 0.55),
        ("unity", 1.0),
        ("celebration_tempo", 1.35),
        ("large_bounded", 100.0),
    )
    luminance_inputs = (
        ("zero_endpoint", 255, 0),
        ("below_half_rounds_down", 1, 127),
        ("half_rounds_up", 1, 128),
        ("half_max_channel", 255, 128),
        ("cozy_three_quarters", 17, 192),
        ("quiet_quantized", 255, 141),
        ("unity_endpoint", 255, Q8_8_ONE),
    )
    return {
        "$schema": FIXTURE_SCHEMA,
        "version": FIXTURE_VERSION,
        "wire": {
            "version": PRESENTATION_CONTEXT_VERSION,
            "byte_order": "big_endian",
            "packet_bytes_exclude": "trailing_crc16_ccitt_false",
            "command_ids": {
                "presentation_context_begin": PRESENTATION_CONTEXT_BEGIN,
                "presentation_context_set": PRESENTATION_CONTEXT_SET,
                "presentation_context_commit": PRESENTATION_CONTEXT_COMMIT,
            },
            "packet_bytes": {
                "begin": BEGIN_BYTES,
                "set_base": SET_BASE_BYTES,
                "set_per_modifier": SET_ENTRY_BYTES,
                "set_max": SET_MAX_BYTES,
                "commit": COMMIT_BYTES,
            },
            "vibe_ids": dict(VIBE_ID_TO_WIRE),
            "palette_role_order": list(VIBE_PALETTE_ROLES),
            "plant_modifier_ids": {
                modifier_id: index
                for index, modifier_id in enumerate(PLANT_MODIFIER_IDS, start=1)
            },
            "digests": {
                "plant": "sha256(version_u8 || count_u8 || canonical_entries)",
                "context": (
                    "sha256(set_packet[18:]); bytes start at scene_revision and "
                    "end after the last canonical plant entry"
                ),
            },
        },
        "fixed_point": {
            "format": "unsigned_q8_8",
            "unity": Q8_8_ONE,
            "quantize": "floor(value * 256 + 0.5)",
            "luminance_range": [0, Q8_8_ONE],
            "luminance_apply_once": "min(255, (channel_u8 * factor_q8_8 + 128) // 256)",
        },
        "scalar_vectors": [
            {"id": vector_id, "input": value, "expected_q8_8": quantize_q8_8(value)}
            for vector_id, value in scalar_inputs
        ],
        "luminance_vectors": [
            {
                "id": vector_id,
                "channel_u8": channel,
                "factor_q8_8": factor,
                "expected_u8": apply_luminance_u8(channel, factor),
            }
            for vector_id, channel, factor in luminance_inputs
        ],
        "presentation_vectors": [
            _case(
                "neutral_empty",
                session_hex="00112233445566778899aabbccddeeff",
                scene_revision=1,
                scene_epoch=0x0102030405060708,
                present_at_scene_time_us=0,
                vibe_id="neutral",
                vibe_revision=0,
                plant_revision=0,
                plant_payload={},
            ),
            _case(
                "quiet_plants",
                session_hex="ffeeddccbbaa99887766554433221100",
                scene_revision=0x0102030405060708,
                scene_epoch=0x1020304050607080,
                present_at_scene_time_us=5_000_001,
                vibe_id="quiet",
                vibe_revision=9,
                plant_revision=17,
                plant_payload={
                    "active": ["emitter", "obstacle", "attractor", "hue_shift", "illuminate"],
                    "strengths": {
                        "illuminate": 0.5,
                        "hue_shift": 0.25,
                        "attractor": 0.75,
                        "obstacle": 1.0,
                        "emitter": 0.125,
                    },
                },
            ),
            _case(
                "celebration_glass_portal",
                session_hex="808182838485868788898a8b8c8d8e8f",
                scene_revision=0xFFFF_FFFF_FFFF_FFFE,
                scene_epoch=0xFFFF_FFFF_FFFF_FFFD,
                present_at_scene_time_us=0xFFFF_FFFF_FFFF_FFFC,
                vibe_id="celebration",
                vibe_revision=0xFFFF_FFFF_FFFF_FFFB,
                plant_revision=0xFFFF_FFFF_FFFF_FFFA,
                plant_payload={
                    "active": ["portal", "repulsor", "liquid_glass", "refract", "shadow"],
                    "strengths": {
                        "shadow": 0.0,
                        "refract": 0.1,
                        "liquid_glass": 0.333,
                        "repulsor": 0.9,
                        "portal": 1.0,
                    },
                },
            ),
        ],
    }


def render_fixture() -> str:
    return json.dumps(build_fixture(), indent=2, sort_keys=True) + "\n"


def render_cpp_header(fixture: dict[str, Any] | None = None) -> str:
    """Render the firmware vectors from the same canonical fixture payload."""

    payload = build_fixture() if fixture is None else fixture
    lines = [
        "// Generated by tools/fixtures/generate_receiver_presentation_golden.py.",
        "#pragma once",
        "",
        "#include <cstdint>",
        "",
        "namespace ledgrid::golden_presentation_v1 {",
        "",
        "struct LuminanceVector {",
        "  const char* id;",
        "  std::uint8_t channel;",
        "  std::uint16_t factor;",
        "  std::uint8_t expected;",
        "};",
        "",
        "struct PresentationVector {",
        "  const char* id;",
        "  const char* begin_hex;",
        "  const char* set_hex;",
        "  const char* commit_hex;",
        "};",
        "",
        "constexpr LuminanceVector kLuminanceVectors[] = {",
    ]
    for vector in payload["luminance_vectors"]:
        lines.append(
            "    {"
            f"{json.dumps(vector['id'])}, {vector['channel_u8']}, "
            f"{vector['factor_q8_8']}, {vector['expected_u8']}"
            "},"
        )
    lines.extend(("};", "", "constexpr PresentationVector kPresentationVectors[] = {"))
    for vector in payload["presentation_vectors"]:
        expected = vector["expected"]
        lines.extend((
            f"    {{{json.dumps(vector['id'])},",
            f"     {json.dumps(expected['begin_hex'])},",
            f"     {json.dumps(expected['set_hex'])},",
            f"     {json.dumps(expected['commit_hex'])}}},",
        ))
    lines.extend(("};", "", "}  // namespace ledgrid::golden_presentation_v1", ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cpp-output", type=Path, default=DEFAULT_CPP_OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing when output differs"
    )
    args = parser.parse_args()
    rendered = render_fixture()
    rendered_cpp = render_cpp_header()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            parser.error(f"fixture is stale; regenerate {args.output}")
        if (
            not args.cpp_output.is_file()
            or args.cpp_output.read_text(encoding="utf-8") != rendered_cpp
        ):
            parser.error(f"firmware fixture is stale; regenerate {args.cpp_output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    args.cpp_output.parent.mkdir(parents=True, exist_ok=True)
    args.cpp_output.write_text(rendered_cpp, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

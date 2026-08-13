"""Phase 3A receiver presentation-context contract acceptance."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import sys
import unittest
from pathlib import Path

from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import (
    VIBE_PALETTE_ROLES,
    ResolvedVibe,
    VibeProfile,
    resolve_vibe,
)
from animation.core.receiver_presentation import (
    BEGIN_BYTES,
    COMMIT_BYTES,
    PRESENTATION_CONTEXT_BEGIN,
    PRESENTATION_CONTEXT_COMMIT,
    PRESENTATION_CONTEXT_SET,
    Q8_8_MAX,
    Q8_8_ONE,
    SET_BASE_BYTES,
    SET_ENTRY_BYTES,
    ReceiverPresentationContext,
    apply_luminance_u8,
    encode_presentation_context_begin,
    encode_presentation_context_commit,
    encode_presentation_context_set,
    plant_modifier_digest,
    quantize_q8_8,
    serialize_presentation_context,
)
from tools.fixtures.generate_receiver_presentation_golden import (
    render_cpp_header,
    render_fixture,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "receiver_presentation_v1.json"
GENERATOR_PATH = ROOT / "tools" / "fixtures" / "generate_receiver_presentation_golden.py"
CPP_FIXTURE_PATH = (
    ROOT / "firmware" / "esp32" / "test" / "fixtures"
    / "receiver_presentation_v1.hpp"
)


def _context_from_vector(vector):
    source = vector["input"]
    vibe_source = source["vibe"]
    resolved = resolve_vibe(vibe_source["vibe_id"], revision=vibe_source["revision"])
    return ReceiverPresentationContext(
        controller_session_id=bytes.fromhex(source["controller_session_id_hex"]),
        scene_revision=source["scene_revision"],
        scene_epoch=source["scene_epoch"],
        present_at_scene_time_us=source["present_at_scene_time_us"],
        vibe=resolved,
        plant_modifiers=PlantModifierState.from_payload(source["plant_modifiers"]),
        plant_revision=source["plant_revision"],
    )


class ReceiverPresentationGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_is_deterministically_regenerated(self):
        self.assertEqual(FIXTURE_PATH.read_text(encoding="utf-8"), render_fixture())
        self.assertEqual(
            CPP_FIXTURE_PATH.read_text(encoding="utf-8"),
            render_cpp_header(self.fixture),
        )
        subprocess.run(
            [sys.executable, str(GENERATOR_PATH), "--check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_fixture_freezes_language_neutral_vocabulary(self):
        self.assertEqual(self.fixture["$schema"], "ledgrid.receiver-presentation-golden")
        self.assertEqual(self.fixture["version"], 1)
        wire = self.fixture["wire"]
        self.assertEqual(wire["byte_order"], "big_endian")
        self.assertEqual(wire["packet_bytes_exclude"], "trailing_crc16_ccitt_false")
        self.assertEqual(wire["command_ids"], {
            "presentation_context_begin": PRESENTATION_CONTEXT_BEGIN,
            "presentation_context_commit": PRESENTATION_CONTEXT_COMMIT,
            "presentation_context_set": PRESENTATION_CONTEXT_SET,
        })
        self.assertEqual(wire["palette_role_order"], list(VIBE_PALETTE_ROLES))
        self.assertEqual(wire["packet_bytes"]["begin"], BEGIN_BYTES)
        self.assertEqual(wire["packet_bytes"]["set_base"], SET_BASE_BYTES)
        self.assertEqual(wire["packet_bytes"]["set_per_modifier"], SET_ENTRY_BYTES)
        self.assertEqual(wire["packet_bytes"]["commit"], COMMIT_BYTES)

    def test_all_packet_vectors_match_exact_bytes_and_digests(self):
        for vector in self.fixture["presentation_vectors"]:
            with self.subTest(vector=vector["id"]):
                context = _context_from_vector(vector)
                begin, set_packet, commit = serialize_presentation_context(context)
                expected = vector["expected"]
                self.assertEqual(begin.hex(), expected["begin_hex"])
                self.assertEqual(set_packet.hex(), expected["set_hex"])
                self.assertEqual(commit.hex(), expected["commit_hex"])
                self.assertEqual(len(begin), expected["begin_bytes"])
                self.assertEqual(len(set_packet), expected["set_bytes"])
                self.assertEqual(len(commit), expected["commit_bytes"])
                self.assertEqual(context.context_digest.hex(), expected["context_digest_hex"])
                self.assertEqual(context.plant_digest.hex(), expected["plant_digest_hex"])

    def test_packet_field_offsets_and_digest_domains_are_exact(self):
        context = _context_from_vector(self.fixture["presentation_vectors"][1])
        begin, set_packet, commit = serialize_presentation_context(context)

        self.assertEqual(begin[:2], bytes((0x21, 1)))
        self.assertEqual(begin[2:18], context.controller_session_id)
        self.assertEqual(struct.unpack(">Q", begin[18:26])[0], context.scene_revision)
        self.assertEqual(begin[26:58], context.context_digest)

        self.assertEqual(set_packet[:2], bytes((0x22, 1)))
        self.assertEqual(set_packet[2:18], context.controller_session_id)
        self.assertEqual(hashlib.sha256(set_packet[18:]).digest(), context.context_digest)
        self.assertEqual(struct.unpack(">Q", set_packet[18:26])[0], context.scene_revision)
        self.assertEqual(set_packet[26], 2)  # quiet
        self.assertEqual(struct.unpack(">I", set_packet[27:31])[0], 1)
        self.assertEqual(struct.unpack(">Q", set_packet[31:39])[0], 9)
        self.assertEqual(set_packet[39:71].hex(), context.vibe.state.resolved_profile_digest)
        expected_palette = bytes(
            channel
            for role in VIBE_PALETTE_ROLES
            for channel in context.vibe.profile.palette_roles[role]
        )
        self.assertEqual(set_packet[71:95], expected_palette)
        self.assertEqual(struct.unpack(">HHHH", set_packet[95:103]), (166, 141, 159, 46))
        self.assertEqual(set_packet[103], 1)
        self.assertEqual(struct.unpack(">Q", set_packet[104:112])[0], 17)
        self.assertEqual(set_packet[112:144], context.plant_digest)
        self.assertEqual(set_packet[144], 5)
        canonical_plant_digest_body = bytes((set_packet[103], set_packet[144])) + set_packet[145:]
        self.assertEqual(hashlib.sha256(canonical_plant_digest_body).digest(), context.plant_digest)

        self.assertEqual(commit[:2], bytes((0x23, 1)))
        self.assertEqual(commit[2:18], context.controller_session_id)
        self.assertEqual(struct.unpack(">QQQ", commit[18:42]), (
            context.scene_revision, context.scene_epoch, context.present_at_scene_time_us
        ))
        self.assertEqual(commit[42:74], context.context_digest)

    def test_driver_facing_encode_aliases_match_contract_serializers(self):
        context = _context_from_vector(self.fixture["presentation_vectors"][0])
        self.assertEqual(serialize_presentation_context(context), (
            encode_presentation_context_begin(context),
            encode_presentation_context_set(context),
            encode_presentation_context_commit(context),
        ))

    def test_packet_size_is_bounded_by_resolved_modifier_count(self):
        for vector in self.fixture["presentation_vectors"]:
            context = _context_from_vector(vector)
            expected = SET_BASE_BYTES + SET_ENTRY_BYTES * len(context.plant_modifiers.active)
            self.assertEqual(len(encode_presentation_context_set(context)), expected)
            self.assertLess(expected, 4096 - 2)


class ReceiverPresentationFixedPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_scalar_vectors_cover_boundaries_and_rounding(self):
        for vector in self.fixture["scalar_vectors"]:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(
                    quantize_q8_8(vector["input"]), vector["expected_q8_8"]
                )

    def test_luminance_vectors_cover_endpoints_and_half_up_rounding(self):
        for vector in self.fixture["luminance_vectors"]:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(
                    apply_luminance_u8(vector["channel_u8"], vector["factor_q8_8"]),
                    vector["expected_u8"],
                )

    def test_unity_is_an_exact_no_op_and_scale_is_applied_once(self):
        for channel in range(256):
            self.assertEqual(apply_luminance_u8(channel, Q8_8_ONE), channel)
        once = apply_luminance_u8(101, 128)
        twice = apply_luminance_u8(once, 128)
        self.assertEqual(once, 51)
        self.assertEqual(twice, 26)
        self.assertNotEqual(once, twice)

    def test_fixed_point_helpers_reject_wrong_types_nonfinite_and_overflow(self):
        for value in (True, "1.0", None):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    quantize_q8_8(value)
        for value in (-0.01, math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    quantize_q8_8(value)
        with self.assertRaisesRegex(ValueError, "overflows"):
            quantize_q8_8((Q8_8_MAX + 1) / Q8_8_ONE)
        for channel, factor in ((-1, 1), (256, 1), (1, -1), (1, 257), (True, 1)):
            with self.subTest(channel=channel, factor=factor):
                with self.assertRaises(ValueError):
                    apply_luminance_u8(channel, factor)


class ReceiverPresentationValidationTests(unittest.TestCase):
    def make_context(self, **changes):
        values = {
            "controller_session_id": bytes(range(16)),
            "scene_revision": 4,
            "scene_epoch": 5,
            "present_at_scene_time_us": 6,
            "vibe": resolve_vibe("neutral", revision=7),
            "plant_modifiers": PlantModifierState.empty(),
            "plant_revision": 8,
        }
        values.update(changes)
        return ReceiverPresentationContext(**values)

    def test_context_rejects_bad_session_and_unsigned_integer_fields(self):
        for session in (b"short", b"x" * 17, "0" * 32, bytearray(16)):
            with self.subTest(session=session):
                with self.assertRaises((TypeError, ValueError)):
                    self.make_context(controller_session_id=session)
        for field in (
            "scene_revision", "scene_epoch", "present_at_scene_time_us", "plant_revision"
        ):
            for bad in (-1, 2**64, True, 1.5):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises((TypeError, ValueError)):
                        self.make_context(**{field: bad})

    def test_context_requires_canonical_resolved_vibe_shape_without_local_lookup(self):
        canonical = resolve_vibe("neutral", revision=12).profile
        custom_colors = dict(canonical.palette_roles)
        custom_colors["primary"] = (1, 2, 3)
        custom = VibeProfile(
            vibe_id="neutral",
            profile_version=1,
            palette_roles=custom_colors,
            tempo_scale=1.125,
            luminance_scale=0.625,
            capability_values={"chroma_scale": 0.75, "energy": 0.25},
        )
        resolved = ResolvedVibe(custom, custom.to_state(revision=12))
        context = self.make_context(vibe=resolved)
        set_packet = encode_presentation_context_set(context)
        self.assertEqual(set_packet[39:71].hex(), custom.resolved_profile_digest)
        primary_offset = 71 + VIBE_PALETTE_ROLES.index("primary") * 3
        self.assertEqual(set_packet[primary_offset:primary_offset + 3], b"\x01\x02\x03")
        self.assertEqual(struct.unpack(">HHHH", set_packet[95:103]), (288, 160, 192, 64))

    def test_context_rejects_noncanonical_or_unknown_vibe_fields(self):
        canonical = resolve_vibe("neutral").profile
        reordered = {key: canonical.palette_roles[key] for key in reversed(VIBE_PALETTE_ROLES)}
        bad_order = VibeProfile(
            "neutral", 1, reordered, 1.0, 1.0,
            {"chroma_scale": 1.0, "energy": 0.5},
        )
        with self.assertRaisesRegex(ValueError, "canonical role order"):
            self.make_context(vibe=ResolvedVibe(bad_order, bad_order.to_state()))

        unknown_capability = VibeProfile(
            "neutral", 1, canonical.palette_roles, 1.0, 1.0,
            {"chroma_scale": 1.0, "energy": 0.5, "optic": 1.0},
        )
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.make_context(
                vibe=ResolvedVibe(unknown_capability, unknown_capability.to_state())
            )
        with self.assertRaises(TypeError):
            self.make_context(vibe=canonical)

    def test_context_rejects_noncanonical_and_unresolved_plant_state(self):
        invalid_states = (
            PlantModifierState(active=("unknown",), strengths={"unknown": 0.5}),
            PlantModifierState(active=("obstacle", "illuminate"), strengths={
                "obstacle": 1.0, "illuminate": 0.5,
            }),
            PlantModifierState(active=("illuminate",), strengths={}),
            PlantModifierState(active=(), strengths={"illuminate": 0.5}),
            PlantModifierState(active=("illuminate",), strengths={"illuminate": math.nan}),
            PlantModifierState(version=2),
        )
        for state in invalid_states:
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    self.make_context(plant_modifiers=state)
        with self.assertRaises(TypeError):
            self.make_context(plant_modifiers={"active": []})

    def test_plant_digest_is_canonical_content_not_revision_or_geometry(self):
        plants = PlantModifierState.from_payload({
            "active": ["obstacle", "illuminate"],
            "strengths": {"obstacle": 1.0, "illuminate": 0.5},
        })
        first = self.make_context(plant_modifiers=plants, plant_revision=1)
        revised = self.make_context(plant_modifiers=plants, plant_revision=2)
        changed = self.make_context(
            plant_modifiers=PlantModifierState.from_payload({
                "active": ["obstacle", "illuminate"],
                "strengths": {"obstacle": 1.0, "illuminate": 0.75},
            }),
            plant_revision=1,
        )
        self.assertEqual(first.plant_digest, revised.plant_digest)
        self.assertNotEqual(first.context_digest, revised.context_digest)
        self.assertNotEqual(first.plant_digest, changed.plant_digest)
        self.assertEqual(first.plant_digest, plant_modifier_digest(plants))
        self.assertFalse(hasattr(first, "plant_geometry"))

    def test_context_digest_binds_state_but_not_session_or_commit_schedule(self):
        original = self.make_context()
        rescheduled = self.make_context(
            controller_session_id=b"z" * 16,
            scene_epoch=999,
            present_at_scene_time_us=123456,
        )
        revised = self.make_context(scene_revision=5)
        vibe_changed = self.make_context(vibe=resolve_vibe("vivid", revision=7))
        self.assertEqual(original.context_digest, rescheduled.context_digest)
        self.assertNotEqual(original.context_digest, revised.context_digest)
        self.assertNotEqual(original.context_digest, vibe_changed.context_digest)
        self.assertNotEqual(
            encode_presentation_context_commit(original),
            encode_presentation_context_commit(rescheduled),
        )

    def test_context_owns_an_immutable_modifier_snapshot(self):
        authored = PlantModifierState.from_payload({
            "active": ["illuminate"], "strengths": {"illuminate": 0.5}
        })
        context = self.make_context(plant_modifiers=authored)
        authored.strengths["illuminate"] = 1.0
        self.assertEqual(context.plant_modifiers.strengths["illuminate"], 0.5)
        with self.assertRaises(TypeError):
            context.plant_modifiers.strengths["illuminate"] = 0.25


if __name__ == "__main__":
    unittest.main()

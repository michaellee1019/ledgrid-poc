"""Checked-in golden acceptance for the Phase 3C installation profile."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from animation.core.installation_profile import (
    compile_installation_profile,
    decode_installation_profile,
    encode_installation_profile,
)
from animation.core.plant_awareness import GLOBE_REGION_ORDER, PlantMaskCache
from tools.fixtures.generate_installation_profile_golden import (
    CLEARANCE_RADIUS,
    FOLIAGE_EVIDENCE_INPUT,
    GLOBES_EVIDENCE_INPUT,
    REGIONS_EVIDENCE_INPUT,
    WALL_INPUT,
    build_fixture,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"
GENERATOR_PATH = (
    ROOT / "tools" / "fixtures" / "generate_installation_profile_golden.py"
)

FIXED_HEADER_BYTES = 112
SECTION_ENTRY_BYTES = 24
SECTION_COUNT = 9
PIXEL_COUNT = 33 * 138
CAMERA_EVIDENCE_PIXEL_COUNT = 32 * 138
PROFILE_BYTES = FIXED_HEADER_BYTES + SECTION_COUNT * SECTION_ENTRY_BYTES + (
    SECTION_COUNT * PIXEL_COUNT
)
SECTION_NAMES = (
    "category",
    "clearance",
    "foliage_edge",
    "globe_edge",
    "obstacle_edge",
    "globe_region",
    "distance",
    "normal_x",
    "normal_y",
)
EXPECTED_REGION_COUNTS = (52, 52, 52, 52, 48, 52, 48)

# These values intentionally freeze the canonical calibration generation rather
# than merely proving that the encoder agrees with itself.
EXPECTED_CALIBRATION_DIGEST = (
    "580aca497078fe64a6b182e6ff0de9c92c58ab14a039062e95ece1961415ffe3"
)
EXPECTED_CONTENT_DIGEST = (
    "ce457a14efd131395507c449f35a7701ca78ddca059620dc3757806ef553ca6a"
)
EXPECTED_SECTION_CRCS = (
    0x9A6783CF,
    0x8C323F0A,
    0x524A562A,
    0x92E2B138,
    0xF1652F29,
    0x995976EB,
    0x8E8CCB32,
    0xECED1AB1,
    0x7B9412FB,
)


class _PlantOwner:
    params = {
        "plant_mask_path": str(FOLIAGE_EVIDENCE_INPUT),
        "plant_globe_mask_path": str(GLOBES_EVIDENCE_INPUT),
        "plant_clearance": CLEARANCE_RADIUS,
    }

    @staticmethod
    def get_strip_info() -> tuple[int, int]:
        return 32, 138

    @staticmethod
    def get_pixel_count() -> int:
        return CAMERA_EVIDENCE_PIXEL_COUNT


def _header(data: bytes) -> dict[str, Any]:
    return {
        "magic": data[0:4],
        "version": struct.unpack_from(">H", data, 4)[0],
        "fixed_header_bytes": struct.unpack_from(">H", data, 6)[0],
        "flags": struct.unpack_from(">I", data, 8)[0],
        "global_strip_count": struct.unpack_from(">H", data, 12)[0],
        "leds_per_strip": struct.unpack_from(">H", data, 14)[0],
        "strip_origin": struct.unpack_from(">H", data, 16)[0],
        "strip_count": struct.unpack_from(">H", data, 18)[0],
        "pixel_count": struct.unpack_from(">I", data, 20)[0],
        "clearance_radius": data[24],
        "region_count": data[25],
        "section_count": struct.unpack_from(">H", data, 26)[0],
        "section_entry_bytes": struct.unpack_from(">H", data, 28)[0],
        "reserved": struct.unpack_from(">H", data, 30)[0],
        "profile_bytes": struct.unpack_from(">I", data, 32)[0],
        "calibration_digest": data[36:68],
        "content_digest": data[68:100],
        "reserved_tail": data[100:112],
    }


def _section_entries(data: bytes) -> list[dict[str, int]]:
    entries = []
    for index in range(SECTION_COUNT):
        offset = FIXED_HEADER_BYTES + index * SECTION_ENTRY_BYTES
        values = struct.unpack_from(">HBBIIIII", data, offset)
        entries.append(
            dict(
                zip(
                    (
                        "id",
                        "encoding",
                        "element_width",
                        "element_count",
                        "offset",
                        "length",
                        "crc32",
                        "reserved",
                    ),
                    values,
                )
            )
        )
    return entries


def _reverse_object_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _reverse_object_keys(value[key])
            for key in reversed(tuple(value.keys()))
        }
    if isinstance(value, list):
        return [_reverse_object_keys(item) for item in value]
    return value


class InstallationProfileGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = FIXTURE_PATH.read_bytes()
        cls.profile = decode_installation_profile(cls.data)

    def test_fixture_is_deterministically_regenerated(self) -> None:
        self.assertEqual(self.data, build_fixture())
        subprocess.run(
            [sys.executable, str(GENERATOR_PATH), "--check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_generator_output_and_check_mode_have_exact_file_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "nested" / "profile.bin"
            subprocess.run(
                [sys.executable, str(GENERATOR_PATH), "--output", str(output)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(output.read_bytes(), self.data)

            output.write_bytes(b"stale sentinel")
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR_PATH),
                    "--output",
                    str(output),
                    "--check",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fixture is stale", result.stderr)
            self.assertEqual(output.read_bytes(), b"stale sentinel")

            missing = Path(temp_dir) / "missing.bin"
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR_PATH),
                    "--output",
                    str(missing),
                    "--check",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(missing.exists())

    def test_header_sizes_and_digests_are_frozen(self) -> None:
        header = _header(self.data)
        self.assertEqual(len(self.data), PROFILE_BYTES)
        self.assertEqual(
            header,
            {
                "magic": b"LGIP",
                "version": 1,
                "fixed_header_bytes": FIXED_HEADER_BYTES,
                "flags": 0,
                "global_strip_count": 33,
                "leds_per_strip": 138,
                "strip_origin": 0,
                "strip_count": 33,
                "pixel_count": PIXEL_COUNT,
                "clearance_radius": CLEARANCE_RADIUS,
                "region_count": len(GLOBE_REGION_ORDER),
                "section_count": SECTION_COUNT,
                "section_entry_bytes": SECTION_ENTRY_BYTES,
                "reserved": 0,
                "profile_bytes": PROFILE_BYTES,
                "calibration_digest": bytes.fromhex(EXPECTED_CALIBRATION_DIGEST),
                "content_digest": bytes.fromhex(EXPECTED_CONTENT_DIGEST),
                "reserved_tail": bytes(12),
            },
        )
        digest_source = self.data[:68] + bytes(32) + self.data[100:]
        self.assertEqual(hashlib.sha256(digest_source).digest(), header["content_digest"])
        self.assertEqual(self.profile.calibration_digest, header["calibration_digest"])

    def test_section_table_offsets_crcs_and_decoded_payloads_are_exact(self) -> None:
        entries = _section_entries(self.data)
        payload_offset = FIXED_HEADER_BYTES + SECTION_COUNT * SECTION_ENTRY_BYTES
        self.assertEqual([entry["id"] for entry in entries], list(range(1, 10)))
        self.assertEqual(
            [entry["encoding"] for entry in entries],
            [1, 2, 2, 2, 2, 1, 3, 4, 4],
        )
        self.assertEqual([entry["crc32"] for entry in entries], list(EXPECTED_SECTION_CRCS))

        for index, (name, entry) in enumerate(zip(SECTION_NAMES, entries)):
            with self.subTest(section=name):
                expected_offset = payload_offset + index * PIXEL_COUNT
                self.assertEqual(entry["element_width"], 1)
                self.assertEqual(entry["element_count"], PIXEL_COUNT)
                self.assertEqual(entry["offset"], expected_offset)
                self.assertEqual(entry["length"], PIXEL_COUNT)
                self.assertEqual(entry["reserved"], 0)
                raw = self.data[expected_offset : expected_offset + PIXEL_COUNT]
                self.assertEqual(zlib.crc32(raw) & 0xFFFF_FFFF, entry["crc32"])
                decoded = getattr(self.profile, name)
                self.assertEqual(raw, decoded.reshape(-1).tobytes())

    def test_decoded_canonical_counts_and_region_order_are_stable(self) -> None:
        profile = self.profile
        self.assertEqual(
            (profile.global_strip_count, profile.leds_per_strip), (33, 138)
        )
        self.assertEqual((profile.strip_origin, profile.strip_count), (0, 33))
        self.assertEqual(profile.pixel_count, PIXEL_COUNT)
        self.assertFalse(profile.reversed_strip_order)
        self.assertEqual(np.bincount(profile.category.ravel(), minlength=3).tolist(), [
            3819,
            379,
            356,
        ])
        self.assertEqual(int(np.count_nonzero(profile.clearance)), 1257)
        self.assertEqual(int(np.count_nonzero(profile.foliage_edge)), 284)
        self.assertEqual(int(np.count_nonzero(profile.globe_edge)), 140)
        self.assertEqual(int(np.count_nonzero(profile.obstacle_edge)), 345)
        self.assertEqual(int(profile.distance.max()), 30)
        self.assertEqual(tuple(GLOBE_REGION_ORDER), (
            "top_left",
            "top_right",
            "upper_middle",
            "middle_left",
            "middle_right",
            "lower_left",
            "lower_right",
        ))
        self.assertEqual(
            tuple(
                int(np.count_nonzero(profile.globe_region == region_id))
                for region_id in range(1, len(GLOBE_REGION_ORDER) + 1)
            ),
            EXPECTED_REGION_COUNTS,
        )

    def test_decoded_geometry_has_exact_plant_mask_geometry_parity(self) -> None:
        expected = PlantMaskCache(_PlantOwner()).get(clearance=CLEARANCE_RADIUS)
        profile = self.profile
        category = np.zeros((32, 138), dtype=np.uint8)
        category[expected.foliage] = 1
        category[expected.globes] = 2

        np.testing.assert_array_equal(profile.category[:32], category)
        np.testing.assert_array_equal(
            profile.clearance[:32].astype(bool), expected.clearance
        )
        np.testing.assert_array_equal(
            profile.foliage_edge[:32].astype(bool), expected.foliage_edge
        )
        np.testing.assert_array_equal(
            profile.globe_edge[:32].astype(bool), expected.globe_edge
        )
        np.testing.assert_array_equal(
            profile.obstacle_edge[:32].astype(bool), expected.obstacle_edge
        )
        np.testing.assert_array_equal(
            profile.distance[:32], expected.distance.astype(np.uint8)
        )
        self.assertFalse(profile.category[32].any())
        self.assertFalse(profile.clearance[32].any())

        for region_id, region_name in enumerate(GLOBE_REGION_ORDER, start=1):
            with self.subTest(region=region_name):
                np.testing.assert_array_equal(
                    (profile.globe_region == region_id)[:32],
                    expected.globe_region_masks[region_name],
                )

    def test_normals_use_documented_signed_q0_7_half_away_from_zero(self) -> None:
        geometry = PlantMaskCache(_PlantOwner()).get(clearance=CLEARANCE_RADIUS)
        for name, source in (
            ("normal_x", geometry.normal_x),
            ("normal_y", geometry.normal_y),
        ):
            with self.subTest(section=name):
                self.assertTrue(np.all(np.isfinite(source)))
                expected = (
                    np.sign(source) * np.floor(np.abs(source) * 127.0 + 0.5)
                ).astype(np.int8)
                encoded = getattr(self.profile, name)
                self.assertEqual(encoded.dtype, np.dtype(np.int8))
                np.testing.assert_array_equal(encoded[:31], expected[:31])
                self.assertGreaterEqual(int(encoded.min()), -127)
                self.assertLessEqual(int(encoded.max()), 127)

    def test_json_object_key_order_does_not_change_canonical_bytes(self) -> None:
        inputs = (
            FOLIAGE_EVIDENCE_INPUT,
            GLOBES_EVIDENCE_INPUT,
            REGIONS_EVIDENCE_INPUT,
            WALL_INPUT,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            reordered = []
            for source in inputs:
                payload = json.loads(source.read_text(encoding="utf-8"))
                destination = Path(temp_dir) / source.name
                destination.write_text(
                    json.dumps(
                        _reverse_object_keys(payload),
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                reordered.append(destination)

            profile = compile_installation_profile(
                foliage_path=reordered[0],
                globes_path=reordered[1],
                regions_path=reordered[2],
                wall_path=reordered[3],
                clearance_radius=CLEARANCE_RADIUS,
            )
            self.assertEqual(encode_installation_profile(profile), self.data)

    def test_canonical_input_lists_use_the_frozen_semantic_order(self) -> None:
        foliage = json.loads(FOLIAGE_EVIDENCE_INPUT.read_text(encoding="utf-8"))
        globes = json.loads(GLOBES_EVIDENCE_INPUT.read_text(encoding="utf-8"))
        regions = json.loads(REGIONS_EVIDENCE_INPUT.read_text(encoding="utf-8"))

        for name, values in (
            ("foliage.covered_indices", foliage["covered_indices"]),
            ("globes.globe_indices", globes["globe_indices"]),
            ("globes.covered_indices", globes["covered_indices"]),
        ):
            with self.subTest(list=name):
                self.assertEqual(values, sorted(set(values)))
        self.assertEqual(
            [pixel["index"] for pixel in foliage["pixels"]],
            list(range(CAMERA_EVIDENCE_PIXEL_COUNT)),
        )
        self.assertEqual(
            [pixel["index"] for pixel in globes["pixels"]],
            sorted(globes["globe_indices"]),
        )
        self.assertEqual(
            [region["id"] for region in globes["regions"]],
            list(GLOBE_REGION_ORDER),
        )
        self.assertEqual(
            [region["id"] for region in regions["regions"]],
            list(GLOBE_REGION_ORDER),
        )


if __name__ == "__main__":
    unittest.main()

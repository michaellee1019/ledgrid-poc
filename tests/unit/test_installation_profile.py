from __future__ import annotations

import copy
import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

from animation.core.installation_profile import (
    DEFAULT_FOLIAGE_PATH,
    DEFAULT_GLOBES_PATH,
    DEFAULT_REGIONS_PATH,
    DEFAULT_WALL_PATH,
    FIXED_HEADER_BYTES,
    InstallationProfile,
    InstallationProfileError,
    PROFILE_HEADER_BYTES,
    SECTION_ENTRY_BYTES,
    SECTION_NAMES,
    compile_installation_profile,
    decode_installation_profile,
    encode_installation_profile,
)
from animation.core.plant_awareness import GLOBE_REGION_ORDER, PlantMaskCache


CANONICAL_PATHS = (
    DEFAULT_FOLIAGE_PATH,
    DEFAULT_GLOBES_PATH,
    DEFAULT_REGIONS_PATH,
    DEFAULT_WALL_PATH,
)


def _rehash(data: bytearray) -> None:
    data[68:100] = bytes(32)
    data[68:100] = hashlib.sha256(data).digest()


def _replace_section_byte(data: bytearray, section_position: int, pixel: int, value: int) -> None:
    entry_offset = FIXED_HEADER_BYTES + section_position * SECTION_ENTRY_BYTES
    entry = list(struct.unpack_from(">HBBIIIII", data, entry_offset))
    payload_offset, payload_length = entry[4], entry[5]
    data[payload_offset + pixel] = value
    payload = data[payload_offset:payload_offset + payload_length]
    entry[6] = zlib.crc32(payload) & 0xFFFFFFFF
    struct.pack_into(">HBBIIIII", data, entry_offset, *entry)
    _rehash(data)


class _PlantOwner:
    def __init__(self, clearance: int = 1):
        self.params = {
            "plant_clearance": clearance,
            "plant_mask_path": str(DEFAULT_FOLIAGE_PATH),
            "plant_globe_mask_path": str(DEFAULT_GLOBES_PATH),
        }

    @staticmethod
    def get_strip_info():
        return 32, 138

    @staticmethod
    def get_pixel_count():
        return 32 * 138


class InstallationProfileCompileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payloads = [json.loads(path.read_text(encoding="utf-8")) for path in CANONICAL_PATHS]

    def _compile_mutated(self, mutator):
        payloads = copy.deepcopy(self.payloads)
        mutator(*payloads)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for position, payload in enumerate(payloads):
                path = Path(temp_dir) / f"input-{position}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths.append(path)
            return compile_installation_profile(*paths)

    def _assert_mutation_rejected(self, mutator, pattern: str):
        with self.assertRaisesRegex(InstallationProfileError, pattern):
            self._compile_mutated(mutator)

    def test_canonical_profile_has_expected_geometry_counts_and_regions(self):
        profile = compile_installation_profile()

        self.assertEqual(profile.pixel_count, 4416)
        self.assertEqual((profile.global_strip_count, profile.leds_per_strip), (32, 138))
        self.assertEqual((profile.strip_origin, profile.strip_count), (0, 32))
        self.assertFalse(profile.reversed_strip_order)
        self.assertEqual(
            np.bincount(profile.category.ravel(), minlength=3).tolist(),
            [3681, 379, 356],
        )
        self.assertEqual(int(profile.clearance.sum()), 1257)
        self.assertEqual(int(profile.foliage_edge.sum()), 284)
        self.assertEqual(int(profile.globe_edge.sum()), 140)
        self.assertEqual(int(profile.obstacle_edge.sum()), 345)
        self.assertEqual(
            [int((profile.globe_region == region_id).sum()) for region_id in range(1, 8)],
            [52, 52, 52, 52, 48, 52, 48],
        )
        self.assertEqual(tuple(profile.globe_region_masks), GLOBE_REGION_ORDER)
        self.assertTrue(np.array_equal(profile.obstacle, profile.category != 0))
        self.assertTrue(np.array_equal(profile.safe, profile.clearance == 0))

    def test_derivation_matches_shared_plant_awareness_exactly(self):
        profile = compile_installation_profile(clearance_radius=2)
        geometry = PlantMaskCache(_PlantOwner(clearance=2)).get()

        np.testing.assert_array_equal(profile.foliage, geometry.foliage)
        np.testing.assert_array_equal(profile.globes, geometry.globes)
        np.testing.assert_array_equal(profile.obstacle, geometry.obstacle)
        np.testing.assert_array_equal(profile.clearance != 0, geometry.clearance)
        np.testing.assert_array_equal(profile.foliage_edge != 0, geometry.foliage_edge)
        np.testing.assert_array_equal(profile.globe_edge != 0, geometry.globe_edge)
        np.testing.assert_array_equal(profile.obstacle_edge != 0, geometry.obstacle_edge)
        np.testing.assert_array_equal(profile.distance, geometry.distance.astype(np.uint8))
        expected_x = (
            np.sign(geometry.normal_x)
            * np.floor(np.abs(geometry.normal_x) * 127 + 0.5)
        ).astype(np.int8)
        expected_y = (
            np.sign(geometry.normal_y)
            * np.floor(np.abs(geometry.normal_y) * 127 + 0.5)
        ).astype(np.int8)
        np.testing.assert_array_equal(profile.normal_x, expected_x)
        np.testing.assert_array_equal(profile.normal_y, expected_y)

    def test_globes_take_precedence_over_overlapping_foliage(self):
        globe_index = self.payloads[1]["globe_indices"][0]

        def add_overlap(foliage, _globes, _regions, _wall):
            foliage["covered_indices"].append(globe_index)
            foliage["covered_indices"].sort()
            foliage["occluded_indices"].append(globe_index)
            foliage["occluded_indices"].sort()
            foliage["covered_count"] += 1
            foliage["occluded_count"] += 1
            foliage["pixels"][globe_index]["occluded"] = True

        profile = self._compile_mutated(add_overlap)
        self.assertEqual(int(profile.category.ravel()[globe_index]), 2)
        self.assertEqual(int(profile.globe_region.ravel()[globe_index]), 6)

    def test_compilation_is_deterministic_and_input_formatting_independent(self):
        first = encode_installation_profile(compile_installation_profile())
        second = encode_installation_profile(compile_installation_profile())
        self.assertEqual(first, second)

        profile = self._compile_mutated(lambda *_: None)
        self.assertEqual(encode_installation_profile(profile), first)

    def test_clearance_radius_accepts_contract_bounds(self):
        for radius in (0, 4):
            with self.subTest(radius=radius):
                profile = compile_installation_profile(clearance_radius=radius)
                self.assertEqual(profile.clearance_radius, radius)
                self.assertGreaterEqual(int(profile.clearance.sum()), int(profile.obstacle.sum()))
        for value in (-1, 5, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(InstallationProfileError):
                    compile_installation_profile(clearance_radius=value)  # type: ignore[arg-type]

    def test_malformed_duplicate_key_and_non_object_json_reject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid = Path(temp_dir) / "bad.json"
            for raw, pattern in (
                ("{", "failed to read"),
                ('{"geometry": {}, "geometry": {}}', "duplicate JSON object key"),
                ("[]", "root must be an object"),
                ('{"value": NaN}', "non-finite JSON number"),
            ):
                with self.subTest(raw=raw):
                    invalid.write_text(raw, encoding="utf-8")
                    with self.assertRaisesRegex(InstallationProfileError, pattern):
                        compile_installation_profile(foliage_path=invalid)

    def test_each_input_must_assert_measured_32_by_138_geometry(self):
        mutations = (
            lambda foliage, _g, _r, _w: foliage["geometry"].update(strip_count=31),
            lambda _f, globes, _r, _w: globes["geometry"].update(leds_per_strip=140),
            lambda _f, _g, regions, _w: regions["geometry"].update(total_leds=4415),
            lambda _f, _g, _r, wall: wall["measured_layout"].update(leds_per_strip=140),
            lambda _f, _g, _r, wall: wall["measured_layout"].update(
                verification_status="unverified"
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_mutation_rejected(mutation, "exactly 32x138|camera_verified")

    def test_index_lists_and_pixel_records_must_be_sorted_unique_and_in_range(self):
        mutations = (
            lambda foliage, _g, _r, _w: foliage[
                "covered_indices"
            ].__setitem__(1, foliage["covered_indices"][0]),
            lambda foliage, _g, _r, _w: foliage["pixels"][1].update(index=0),
            lambda _f, globes, _r, _w: globes["globe_indices"].__setitem__(-1, 4416),
            lambda _f, globes, _r, _w: globes["pixels"].__setitem__(1, globes["pixels"][0]),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_mutation_rejected(
                    mutation,
                    "sorted|integer|membership|index|0 through 4415",
                )

    def test_declared_counts_must_match_calibration_records(self):
        mutations = (
            lambda foliage, _g, _r, _w: foliage.update(covered_count=0),
            lambda foliage, _g, _r, _w: foliage.update(observed_count=0),
            lambda _f, globes, _r, _w: globes.update(globe_count=0),
            lambda _f, globes, _r, _w: globes["region_pixel_counts"].update(top_left=51),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_mutation_rejected(mutation, "count|one measured record")

    def test_region_order_definition_overlap_and_cross_file_agreement_are_strict(self):
        def swap_regions(_f, _g, regions, _w):
            regions["regions"][0], regions["regions"][1] = (
                regions["regions"][1],
                regions["regions"][0],
            )

        def overlap_regions(_f, globes, regions, _w):
            for payload in (globes, regions):
                payload["regions"][1].update(strip_start=0, led_start=85)

        mutations = (
            swap_regions,
            overlap_regions,
            lambda _f, globes, _r, _w: globes["regions"][0].update(width=7),
            lambda _f, globes, _r, _w: globes.update(region_count=6),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_mutation_rejected(mutation, "stable|overlapping|disagree|seven|8x8")

    def test_every_globe_has_one_valid_region_membership(self):
        mutations = (
            lambda _f, globes, _r, _w: globes["pixels"][0].update(region="unknown"),
            lambda _f, globes, _r, _w: globes["pixels"][0].update(region="top_right"),
            lambda _f, globes, _r, _w: globes["pixels"].pop(),
            lambda _f, globes, _r, _w: globes["region_pixel_counts"].pop("top_left"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_mutation_rejected(mutation, "region|membership|count")


class InstallationProfileCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = compile_installation_profile()
        cls.encoded = encode_installation_profile(cls.profile)

    def test_header_table_crcs_and_content_digest_are_exact(self):
        self.assertEqual(len(self.encoded), 40_072)
        self.assertEqual(self.encoded[:4], b"LGIP")
        self.assertEqual(struct.unpack_from(">H", self.encoded, 4)[0], 1)
        self.assertEqual(struct.unpack_from(">H", self.encoded, 6)[0], 112)
        self.assertEqual(struct.unpack_from(">I", self.encoded, 8)[0], 0)
        self.assertEqual(struct.unpack_from(">HHHHI", self.encoded, 12), (32, 138, 0, 32, 4416))
        self.assertEqual(self.encoded[24:26], bytes((1, 7)))
        self.assertEqual(struct.unpack_from(">HHH", self.encoded, 26), (9, 24, 0))
        self.assertEqual(struct.unpack_from(">I", self.encoded, 32)[0], len(self.encoded))
        self.assertEqual(self.encoded[100:112], bytes(12))

        digest_input = bytearray(self.encoded)
        digest_input[68:100] = bytes(32)
        self.assertEqual(self.encoded[68:100], hashlib.sha256(digest_input).digest())
        expected_encoding = (1, 2, 2, 2, 2, 1, 3, 4, 4)
        expected_offset = PROFILE_HEADER_BYTES
        for position, encoding in enumerate(expected_encoding):
            entry = struct.unpack_from(
                ">HBBIIIII",
                self.encoded,
                FIXED_HEADER_BYTES + position * SECTION_ENTRY_BYTES,
            )
            section_id, actual_encoding, width, count, offset, length, crc, reserved = entry
            self.assertEqual(entry[:6], (position + 1, encoding, 1, 4416, expected_offset, 4416))
            self.assertEqual(actual_encoding, encoding)
            self.assertEqual(width, 1)
            self.assertEqual(count, 4416)
            self.assertEqual(reserved, 0)
            self.assertEqual(crc, zlib.crc32(self.encoded[offset:offset + length]) & 0xFFFFFFFF)
            expected_offset += length
        self.assertEqual(expected_offset, len(self.encoded))

    def test_round_trip_preserves_all_fields_and_read_only_arrays(self):
        decoded = decode_installation_profile(self.encoded)
        for field in (
            "global_strip_count", "leds_per_strip", "strip_origin", "strip_count",
            "clearance_radius", "calibration_digest", "reversed_strip_order",
        ):
            self.assertEqual(getattr(decoded, field), getattr(self.profile, field))
        for name in SECTION_NAMES:
            actual = getattr(decoded, name)
            np.testing.assert_array_equal(actual, getattr(self.profile, name))
            self.assertTrue(actual.flags.c_contiguous)
            self.assertFalse(actual.flags.writeable)
            with self.assertRaises(ValueError):
                actual.flat[0] = 0

    def test_decoder_rejects_bad_magic_version_flags_sizes_and_reserved_bytes(self):
        mutations = []
        for offset, replacement in (
            (0, b"NOPE"),
            (4, struct.pack(">H", 2)),
            (6, struct.pack(">H", 111)),
            (8, struct.pack(">I", 2)),
            (30, struct.pack(">H", 1)),
            (100, b"\x01"),
        ):
            mutated = bytearray(self.encoded)
            mutated[offset:offset + len(replacement)] = replacement
            _rehash(mutated)
            mutations.append(bytes(mutated))
        for mutated in mutations:
            with self.subTest(offset=mutated[:112]):
                with self.assertRaises(InstallationProfileError):
                    decode_installation_profile(mutated)
        with self.assertRaisesRegex(InstallationProfileError, "truncated|byte count"):
            decode_installation_profile(self.encoded[:-1])
        with self.assertRaisesRegex(InstallationProfileError, "65,535"):
            decode_installation_profile(self.encoded + bytes(65_536 - len(self.encoded)))

    def test_decoder_rejects_content_digest_and_payload_crc_corruption(self):
        bad_digest = bytearray(self.encoded)
        bad_digest[68] ^= 1
        with self.assertRaisesRegex(InstallationProfileError, "SHA-256"):
            decode_installation_profile(bytes(bad_digest))

        bad_crc = bytearray(self.encoded)
        bad_crc[PROFILE_HEADER_BYTES] ^= 1
        _rehash(bad_crc)
        with self.assertRaisesRegex(InstallationProfileError, "CRC-32"):
            decode_installation_profile(bytes(bad_crc))

    def test_decoder_rejects_every_noncanonical_section_entry_field(self):
        base = list(struct.unpack_from(">HBBIIIII", self.encoded, FIXED_HEADER_BYTES))
        changes = {
            "id": (0, 2),
            "encoding": (1, 4),
            "width": (2, 2),
            "count": (3, 4415),
            "offset": (4, PROFILE_HEADER_BYTES + 1),
            "length": (5, 4415),
            "reserved": (7, 1),
        }
        for label, (position, value) in changes.items():
            with self.subTest(field=label):
                mutated = bytearray(self.encoded)
                entry = base.copy()
                entry[position] = value
                struct.pack_into(">HBBIIIII", mutated, FIXED_HEADER_BYTES, *entry)
                _rehash(mutated)
                with self.assertRaises(InstallationProfileError):
                    decode_installation_profile(bytes(mutated))

    def test_decoder_rejects_invalid_section_values_and_semantic_membership(self):
        empty_pixel = int(np.flatnonzero(self.profile.category == 0)[0])
        globe_pixel = int(np.flatnonzero(self.profile.category == 2)[0])
        mutations = (
            (0, empty_pixel, 3, "category"),
            (1, empty_pixel, 2, "clearance"),
            (5, globe_pixel, 0, "region"),
            (6, empty_pixel, 0, "distance"),
            (7, empty_pixel, 128, "Q0.7"),
        )
        for section, pixel, value, pattern in mutations:
            with self.subTest(section=SECTION_NAMES[section]):
                mutated = bytearray(self.encoded)
                _replace_section_byte(mutated, section, pixel, value)
                with self.assertRaisesRegex(InstallationProfileError, pattern):
                    decode_installation_profile(bytes(mutated))

    def test_constructor_rejects_wrong_shape_dtype_digest_geometry_and_invariants(self):
        values = {
            field: getattr(self.profile, field)
            for field in self.profile.__dataclass_fields__
        }
        cases = []
        wrong_shape = values.copy()
        wrong_shape["category"] = np.zeros((31, 138), dtype=np.uint8)
        cases.append(wrong_shape)
        wrong_dtype = values.copy()
        wrong_dtype["normal_x"] = self.profile.normal_x.astype(np.int16)
        cases.append(wrong_dtype)
        wrong_digest = values.copy()
        wrong_digest["calibration_digest"] = bytes(31)
        cases.append(wrong_digest)
        wrong_geometry = values.copy()
        wrong_geometry["leds_per_strip"] = 140
        cases.append(wrong_geometry)
        outside = values.copy()
        outside["strip_origin"] = 1
        cases.append(outside)
        for case in cases:
            with self.subTest(case=next(key for key in case if case[key] is not values.get(key))):
                with self.assertRaises(InstallationProfileError):
                    InstallationProfile(**case)

    def test_encoder_and_decoder_reject_wrong_public_input_types(self):
        with self.assertRaisesRegex(InstallationProfileError, "InstallationProfile"):
            encode_installation_profile(object())  # type: ignore[arg-type]
        for value in (bytearray(self.encoded), memoryview(self.encoded), "LGIP"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(InstallationProfileError, "must be bytes"):
                    decode_installation_profile(value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

"""Phase 3C host/reference acceptance for fixed-point receiver optics."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation import AnimationBase
from animation.core.receiver_optics import (
    HUE_MATRIX_ROUND,
    HUE_MATRIX_SCALE,
    HUE_ROTATION_MATRICES_Q14,
    HUE_STRENGTH_MAX,
    apply_hue_shift_u8,
    hue_rotation_matrix_q14,
)
from animation.core.receiver_presentation import quantize_q8_8
from tools.fixtures.generate_receiver_optics_golden import (
    build_fixture,
    render_coefficients_header,
    render_cpp_header,
    render_fixture,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "receiver_optics_v1.json"
CPP_FIXTURE_PATH = (
    ROOT / "firmware" / "esp32" / "test" / "fixtures"
    / "receiver_optics_v1.hpp"
)
COEFFICIENTS_PATH = (
    ROOT / "firmware" / "esp32" / "include" / "ledgrid"
    / "receiver_optics_coefficients_v1.hpp"
)
GENERATOR_PATH = ROOT / "tools" / "fixtures" / "generate_receiver_optics_golden.py"
EXPECTED_MATRIX_SHA256 = (
    "df4f6386ad5cf27f697804dac4aff862f73c12e3b27768c36b64f6b7c76f8431"
)


class _Controller:
    strip_count = 3
    leds_per_strip = 4
    total_leds = strip_count * leds_per_strip


class _Animation(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()


class _PoisonMask:
    def __array__(self):
        raise AssertionError("zero strength inspected the target mask")


def _matrix_bytes() -> bytes:
    return b"".join(
        struct.pack(">h", coefficient)
        for matrix in HUE_ROTATION_MATRICES_Q14
        for row in matrix
        for coefficient in row
    )


class ReceiverOpticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_complete_matrix_table_has_frozen_shape_digest_and_identity(self) -> None:
        self.assertEqual(len(HUE_ROTATION_MATRICES_Q14), 257)
        self.assertTrue(all(len(matrix) == 3 for matrix in HUE_ROTATION_MATRICES_Q14))
        self.assertTrue(all(
            len(row) == 3
            for matrix in HUE_ROTATION_MATRICES_Q14
            for row in matrix
        ))
        self.assertEqual(
            hue_rotation_matrix_q14(0),
            ((16384, 0, 0), (0, 16384, 0), (0, 0, 16384)),
        )
        coefficients = [
            coefficient
            for matrix in HUE_ROTATION_MATRICES_Q14
            for row in matrix
            for coefficient in row
        ]
        self.assertGreaterEqual(min(coefficients), -32768)
        self.assertLessEqual(max(coefficients), 32767)
        digest = hashlib.sha256(_matrix_bytes()).hexdigest()
        self.assertEqual(digest, EXPECTED_MATRIX_SHA256)
        self.assertEqual(self.fixture["matrix_table"]["sha256"], digest)
        self.assertEqual(
            self.fixture["matrix_table"]["matrices_q14"],
            json.loads(json.dumps(HUE_ROTATION_MATRICES_Q14)),
        )

    def test_rgb_vectors_cover_strengths_extrema_clipping_and_exact_noop(self) -> None:
        vectors = self.fixture["rgb_vectors"]
        self.assertEqual({vector["strength_q8_8"] for vector in vectors}, {
            0, 1, 64, 128, 256,
        })
        self.assertTrue({
            tuple(vector["input_rgb"])
            for vector in vectors
            if vector["strength_q8_8"] == 128
        }.issuperset({
            (0, 0, 0), (255, 255, 255),
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
        }))
        self.assertTrue(any(
            min(vector["unclamped_rgb"]) < 0
            for vector in vectors
        ))
        self.assertTrue(any(
            max(vector["unclamped_rgb"]) > 255
            for vector in vectors
        ))

        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                strength = vector["strength_q8_8"]
                source = np.asarray((vector["input_rgb"],), dtype=np.uint8)
                actual = source.copy()
                returned = apply_hue_shift_u8(
                    actual, strength, np.ones(1, dtype=np.bool_)
                )
                self.assertIs(returned, actual)
                np.testing.assert_array_equal(
                    actual[0], np.asarray(vector["expected_rgb"], dtype=np.uint8)
                )
                if strength == 0:
                    np.testing.assert_array_equal(actual, source)

    def test_mask_is_exact_and_untargeted_pixels_remain_byte_exact(self) -> None:
        source = np.asarray([
            [255, 0, 0],
            [0, 255, 0],
            [0, 0, 255],
            [12, 160, 200],
            [73, 99, 141],
        ], dtype=np.uint8)
        target = np.asarray([False, True, False, True, False], dtype=np.bool_)
        actual = source.copy()

        self.assertIs(apply_hue_shift_u8(actual, 128, target), actual)
        np.testing.assert_array_equal(actual[~target], source[~target])
        self.assertFalse(np.array_equal(actual[target], source[target]))

        for index in np.flatnonzero(target):
            rgb = tuple(int(channel) for channel in source[index])
            matrix = hue_rotation_matrix_q14(128)
            expected = [
                min(255, max(0, (
                    sum(matrix[row][column] * rgb[column] for column in range(3))
                    + HUE_MATRIX_ROUND
                ) // HUE_MATRIX_SCALE))
                for row in range(3)
            ]
            self.assertEqual(actual[index].tolist(), expected)

    def test_zero_strength_returns_before_pixel_or_mask_validation(self) -> None:
        source = np.arange(15, dtype=np.uint8).reshape(5, 3)
        before = source.tobytes()
        self.assertIs(apply_hue_shift_u8(source, 0, _PoisonMask()), source)
        self.assertEqual(source.tobytes(), before)

    def test_empty_mask_is_an_in_place_byte_exact_noop(self) -> None:
        source = np.arange(15, dtype=np.uint8).reshape(5, 3)
        before = source.copy()
        returned = apply_hue_shift_u8(
            source, HUE_STRENGTH_MAX, np.zeros(5, dtype=np.bool_)
        )
        self.assertIs(returned, source)
        np.testing.assert_array_equal(source, before)

    def test_invalid_strength_pixels_and_masks_fail_closed(self) -> None:
        pixels = np.zeros((2, 3), dtype=np.uint8)
        mask = np.ones(2, dtype=np.bool_)
        for invalid in (True, -1, 257, 0.5, "64"):
            with self.subTest(strength=invalid), self.assertRaises((TypeError, ValueError)):
                apply_hue_shift_u8(pixels, invalid, mask)
        with self.assertRaises(TypeError):
            apply_hue_shift_u8(pixels.astype(np.int16), 64, mask)
        with self.assertRaises(ValueError):
            apply_hue_shift_u8(np.zeros((2, 4), dtype=np.uint8), 64, mask)
        with self.assertRaises(TypeError):
            apply_hue_shift_u8(pixels, 64, np.ones(2, dtype=np.uint8))
        with self.assertRaises(ValueError):
            apply_hue_shift_u8(pixels, 64, np.ones(1, dtype=np.bool_))

    def test_framework_hue_shift_uses_shared_quantized_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            foliage_path = Path(directory) / "foliage.json"
            globe_path = Path(directory) / "globes.json"
            foliage_path.write_text(json.dumps({"covered_indices": [1, 4, 8]}))
            globe_path.write_text(json.dumps({"globe_indices": [6]}))
            animation = _Animation(_Controller(), {
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
                "plant_modifiers": {
                    "active": ["hue_shift"],
                    "strengths": {"hue_shift": 0.25},
                },
            })
            source = np.asarray([
                [(index * 61 + 17) & 0xFF,
                 (index * 29 + 73) & 0xFF,
                 (index * 43 + 151) & 0xFF]
                for index in range(_Controller.total_leds)
            ], dtype=np.uint8)
            target = animation.get_plant_masks().obstacle_flat
            expected = source.copy()
            strength = quantize_q8_8(0.25, maximum=HUE_STRENGTH_MAX)
            self.assertEqual(strength, 64)
            apply_hue_shift_u8(expected, strength, target)

            actual = animation.apply_framework_plant_modifiers(source)
            np.testing.assert_array_equal(actual, expected)
            np.testing.assert_array_equal(actual[~target], source[~target])
            self.assertFalse(np.shares_memory(actual, source))

    def test_framework_sub_half_lsb_strength_is_exact_inactive_noop(self) -> None:
        animation = _Animation(_Controller(), {
            "plant_modifiers": {
                "active": ["hue_shift"],
                "strengths": {"hue_shift": 0.001953124},
            },
        })
        source = np.arange(_Controller.total_leds * 3, dtype=np.uint8).reshape(-1, 3)
        self.assertFalse(animation.framework_plant_modifiers_active())
        self.assertFalse(animation.framework_plant_modifier_refresh_pending())
        self.assertIs(animation.apply_framework_plant_modifiers(source), source)

    def test_installed_topology_vectors_cover_all_receivers_and_stitched_field(self) -> None:
        topology = self.fixture["installed_topology"]
        self.assertEqual(topology["strip_origins"], [0, 8, 24, 16, 32])
        self.assertEqual(topology["strip_counts"], [8, 8, 8, 8, 1])
        self.assertEqual(topology["physical_receiver_order"], [0, 1, 3, 2, 4])
        self.assertEqual(
            topology["reverse_native_strips"], [False, False, True, True, False]
        )
        self.assertEqual(
            {vector["strength_q8_8"] for vector in topology["vectors"]},
            {64, 256},
        )
        for vector in topology["vectors"]:
            self.assertEqual(len(vector["receiver_sha256"]), 5)
            self.assertTrue(all(len(digest) == 64 for digest in vector["receiver_sha256"]))
            self.assertEqual(len(vector["stitched_global_sha256"]), 64)

    def test_all_generated_artifacts_are_exact_derivatives(self) -> None:
        rebuilt = build_fixture()
        self.assertEqual(FIXTURE_PATH.read_text(encoding="utf-8"), render_fixture(rebuilt))
        self.assertEqual(
            CPP_FIXTURE_PATH.read_text(encoding="utf-8"),
            render_cpp_header(rebuilt),
        )
        self.assertEqual(
            COEFFICIENTS_PATH.read_text(encoding="utf-8"),
            render_coefficients_header(rebuilt),
        )
        subprocess.run(
            [sys.executable, str(GENERATOR_PATH), "--check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_generator_custom_outputs_and_check_mode_are_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "nested" / "vectors.json"
            cpp_path = root / "nested" / "vectors.hpp"
            coefficients_path = root / "nested" / "coefficients.hpp"
            command = [
                sys.executable,
                str(GENERATOR_PATH),
                "--output", str(json_path),
                "--cpp-output", str(cpp_path),
                "--coefficients-output", str(coefficients_path),
            ]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertEqual(json_path.read_text(encoding="utf-8"), render_fixture())
            self.assertEqual(cpp_path.read_text(encoding="utf-8"), render_cpp_header())
            self.assertEqual(
                coefficients_path.read_text(encoding="utf-8"),
                render_coefficients_header(),
            )
            subprocess.run(
                [*command, "--check"], cwd=ROOT, check=True,
                capture_output=True, text=True,
            )

            json_path.write_text("stale sentinel", encoding="utf-8")
            result = subprocess.run(
                [*command, "--check"], cwd=ROOT, check=False,
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fixture is stale", result.stderr)
            self.assertEqual(json_path.read_text(encoding="utf-8"), "stale sentinel")


if __name__ == "__main__":
    unittest.main()

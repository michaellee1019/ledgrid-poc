"""Catalog and byte-rendering acceptance for the compiled receiver rainbow."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_BUNDLE_DIGEST,
    COMPILED_RAINBOW_COMPONENT_ID,
    COMPILED_RAINBOW_CONTRACT_DIGEST,
    COMPILED_RAINBOW_CYCLE_US,
    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
    COMPILED_RAINBOW_PLUGIN_ID,
    Q8_8_ONE,
    preview_elapsed_us,
    receiver_static_component_catalog,
    receiver_static_component_descriptor,
    render_compiled_rainbow_preview,
    validate_compiled_rainbow_parameters,
)


class ReceiverStaticDescriptorTests(unittest.TestCase):
    def test_descriptor_requires_both_receiver_hybrid_rollout_gates(self):
        cases = (
            ({}, False),
            ({"receiver_local_background": True}, False),
            ({"receiver_sparse_overlay": True}, False),
            ({
                "receiver_local_background": True,
                "receiver_sparse_overlay": True,
            }, True),
        )
        for mapping, expected in cases:
            with self.subTest(flags=mapping):
                descriptor = receiver_static_component_descriptor(mapping)
                self.assertEqual(descriptor is not None, expected)
                self.assertEqual(
                    len(receiver_static_component_catalog(mapping)), int(expected)
                )

        flags = AnimationPipelineFeatureFlags(
            receiver_local_background=True,
            receiver_sparse_overlay=True,
        )
        self.assertIsNotNone(receiver_static_component_descriptor(flags))

    def test_descriptor_freezes_builtin_schema_defaults_and_honest_proof(self):
        descriptor = receiver_static_component_descriptor({
            "receiver_local_background": True,
            "receiver_sparse_overlay": True,
        })
        self.assertIsNotNone(descriptor)
        self.assertEqual(descriptor["schema"], "ledgrid.component-descriptor")
        self.assertEqual(descriptor["manifest_version"], 1)
        self.assertEqual(descriptor["plugin_id"], COMPILED_RAINBOW_PLUGIN_ID)
        self.assertEqual(descriptor["provider"], "receiver_native")
        self.assertEqual(descriptor["role"], "background")
        self.assertEqual(
            descriptor["entrypoint"],
            f"receiver_builtin:{COMPILED_RAINBOW_COMPONENT_ID}",
        )
        self.assertEqual(descriptor["defaults"], {
            "preferred_cadence_hz": 30,
            "common_seed": 0,
        })
        self.assertEqual(descriptor["parameter_schema"]["preferred_cadence_hz"], {
            "type": "int", "min": 1, "max": 200, "default": 30,
            "description": "Receiver-local render cadence in frames per second",
        })
        self.assertEqual(
            descriptor["parameter_schema"]["common_seed"]["max"],
            0xFFFF_FFFF,
        )
        self.assertEqual(descriptor["vibe_capabilities"], ["luminance"])
        self.assertEqual(descriptor["vibe_color_policy"], "preserve")
        self.assertEqual(descriptor["cadence"]["preferred_fps"], 30)
        self.assertTrue(descriptor["compatibility"]["composable"])
        self.assertFalse(descriptor["preview"]["framebuffer_readback"])
        self.assertIn("Host preview", descriptor["preview"]["label"])

        build = descriptor["build"]
        self.assertEqual(build["artifact_kind"], "firmware_builtin")
        self.assertEqual(build["component_id"], 1)
        self.assertEqual(build["contract_digest"], COMPILED_RAINBOW_CONTRACT_DIGEST)
        self.assertEqual(build["bundle_digest"], COMPILED_RAINBOW_BUNDLE_DIGEST)
        self.assertEqual(
            build["expected_payload_digest"],
            COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
        )
        self.assertRegex(build["contract_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(build["expected_payload_digest"], r"^[0-9a-f]{64}$")
        self.assertFalse(build["payload_digest_proven"])
        self.assertIn("not a receiver-reported", build["digest_semantics"])
        self.assertEqual(
            build["runtime_proof"]["authority"],
            "receiver capability/status agreement",
        )

    def test_descriptor_results_are_detached_and_python_scanner_stays_python_only(self):
        flags = {
            "receiver_local_background": True,
            "receiver_sparse_overlay": True,
        }
        first = receiver_static_component_descriptor(flags)
        first["defaults"]["common_seed"] = 99
        first["build"]["runtime_proof"]["required_component_id"] = 99
        second = receiver_static_component_descriptor(flags)
        self.assertEqual(second["defaults"]["common_seed"], 0)
        self.assertEqual(
            second["build"]["runtime_proof"]["required_component_id"], 1
        )

        # The receiver builtin is a catalog peer supplied by this module. It is
        # never discovered as, or injected into, a Python plugin package.
        loader = AnimationPluginLoader()
        self.assertEqual(loader.component_catalog(provider="receiver_native"), [])
        self.assertIsNone(loader.get_component_descriptor(COMPILED_RAINBOW_PLUGIN_ID))

    def test_parameter_validation_is_complete_bounded_and_fail_closed(self):
        self.assertEqual(validate_compiled_rainbow_parameters(), {
            "preferred_cadence_hz": 30,
            "common_seed": 0,
        })
        self.assertEqual(
            validate_compiled_rainbow_parameters({
                "preferred_cadence_hz": 200,
                "common_seed": 0xFFFF_FFFF,
            }),
            {"preferred_cadence_hz": 200, "common_seed": 0xFFFF_FFFF},
        )
        for value in (0, 201, True, 30.0):
            with self.subTest(cadence=value), self.assertRaises((TypeError, ValueError)):
                validate_compiled_rainbow_parameters({
                    "preferred_cadence_hz": value
                })
        for value in (-1, 2**32, True, 1.0):
            with self.subTest(seed=value), self.assertRaises((TypeError, ValueError)):
                validate_compiled_rainbow_parameters({"common_seed": value})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_compiled_rainbow_parameters({"brightness": 1})


class CompiledRainbowPreviewTests(unittest.TestCase):
    def test_integer_hue_wheel_matches_firmware_sector_boundaries(self):
        frame = render_compiled_rainbow_preview(
            0, strip_count=1, leds_per_strip=32
        )
        expected = {
            0: (255, 0, 0),
            5: (255, 240, 0),
            6: (223, 255, 0),
            11: (0, 255, 16),
            16: (0, 255, 255),
            22: (32, 0, 255),
            27: (255, 0, 239),
            31: (255, 0, 47),
        }
        for pixel, rgb in expected.items():
            with self.subTest(pixel=pixel):
                self.assertEqual(tuple(frame[pixel]), rgb)
        self.assertEqual(frame.dtype, np.uint8)
        self.assertEqual(frame.shape, (32, 3))
        self.assertTrue(frame.flags.c_contiguous)

    def test_elapsed_seed_and_luminance_follow_receiver_integer_math(self):
        initial = render_compiled_rainbow_preview(
            0, strip_count=1, leds_per_strip=2
        )
        quarter = render_compiled_rainbow_preview(
            COMPILED_RAINBOW_CYCLE_US // 4,
            strip_count=1,
            leds_per_strip=2,
        )
        seeded = render_compiled_rainbow_preview(
            0,
            {"common_seed": 384},
            strip_count=1,
            leds_per_strip=2,
        )
        half_luminance = render_compiled_rainbow_preview(
            0,
            strip_count=1,
            leds_per_strip=2,
            luminance_q8_8=128,
        )

        self.assertEqual(tuple(initial[0]), (255, 0, 0))
        self.assertEqual(tuple(initial[1]), (255, 48, 0))
        self.assertEqual(tuple(quarter[0]), (128, 0, 255))
        self.assertEqual(tuple(seeded[0]), (127, 255, 0))
        self.assertEqual(tuple(half_luminance[0]), (128, 0, 0))
        self.assertEqual(tuple(half_luminance[1]), (128, 24, 0))

        black = render_compiled_rainbow_preview(
            0, strip_count=2, leds_per_strip=3, luminance_q8_8=0
        )
        self.assertFalse(np.any(black))

    def test_cycle_is_exact_and_cadence_does_not_change_selected_time(self):
        for elapsed in (0, 1, 123_456, 999_999):
            with self.subTest(elapsed=elapsed):
                first = render_compiled_rainbow_preview(
                    elapsed,
                    {"preferred_cadence_hz": 1},
                    strip_count=4,
                    leds_per_strip=17,
                )
                repeated = render_compiled_rainbow_preview(
                    elapsed + COMPILED_RAINBOW_CYCLE_US,
                    {"preferred_cadence_hz": 200},
                    strip_count=4,
                    leds_per_strip=17,
                )
                np.testing.assert_array_equal(first, repeated)

    def test_four_receiver_offsets_stitch_to_one_global_wall(self):
        elapsed = 654_321
        parameters = {"common_seed": 0x1234_5678}
        full = render_compiled_rainbow_preview(
            elapsed,
            parameters,
            strip_count=32,
            leds_per_strip=138,
            luminance_q8_8=141,
        ).reshape(32, 138, 3)
        boards = [
            render_compiled_rainbow_preview(
                elapsed,
                parameters,
                strip_count=8,
                leds_per_strip=138,
                global_strip_offset=offset,
                luminance_q8_8=141,
            ).reshape(8, 138, 3)
            for offset in (0, 8, 16, 24)
        ]
        np.testing.assert_array_equal(full, np.concatenate(boards, axis=0))

    def test_output_reuse_and_input_bounds_are_explicit(self):
        output = np.empty((12, 3), dtype=np.uint8)
        returned = render_compiled_rainbow_preview(
            0, strip_count=3, leds_per_strip=4, out=output
        )
        self.assertIs(returned, output)
        self.assertEqual(preview_elapsed_us(0.25), 250_000)
        self.assertEqual(preview_elapsed_us(1), COMPILED_RAINBOW_CYCLE_US)

        for kwargs in (
            {"elapsed_us": -1},
            {"elapsed_us": True},
            {"elapsed_us": 0, "strip_count": 0},
            {"elapsed_us": 0, "leds_per_strip": 0},
            {"elapsed_us": 0, "global_strip_offset": -1},
            {"elapsed_us": 0, "luminance_q8_8": Q8_8_ONE + 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                render_compiled_rainbow_preview(**kwargs)
        with self.assertRaises(ValueError):
            render_compiled_rainbow_preview(
                0, strip_count=2, leds_per_strip=2,
                out=np.empty((4, 3), dtype=np.float32),
            )
        with self.assertRaises(ValueError):
            render_compiled_rainbow_preview(
                0,
                strip_count=2,
                leds_per_strip=2,
                out=np.empty((4, 6), dtype=np.uint8)[:, ::2],
            )
        for elapsed in (-1.0, float("inf"), float("nan"), True):
            with self.subTest(elapsed=elapsed), self.assertRaises((TypeError, ValueError)):
                preview_elapsed_us(elapsed)


if __name__ == "__main__":
    unittest.main()

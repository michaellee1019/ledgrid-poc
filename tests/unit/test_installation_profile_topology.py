from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from animation.core.installation_profile import (
    GLOBAL_STRIP_COUNT,
    InstallationProfile,
    SECTION_NAMES,
    decode_installation_profile,
    encode_installation_profile,
)
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
    InstallationProfileTopologyError,
    RECEIVER_COUNT,
    RECEIVER_STRIP_COUNTS,
    reassemble_installation_profile,
    slice_installation_profile,
)


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"


class InstallationProfileTopologyTests(unittest.TestCase):
    @staticmethod
    def topology(**overrides: object) -> InstallationProfileTopology:
        values: dict[str, object] = {
            "logical_to_transport_routes": (
                (0, 0), (0, 1), (1, 0), (1, 1), (1, 2)
            ),
            "physical_lane_order": (0, 1, 2, 3, 4),
            "reverse_host_strips_by_logical_receiver": (
                False,
                False,
                False,
                False,
                False,
            ),
            "reverse_native_strips_by_logical_receiver": (
                False,
                False,
                False,
                False,
                False,
            ),
        }
        values.update(overrides)
        return InstallationProfileTopology(**values)

    @staticmethod
    def canonical_profile() -> InstallationProfile:
        rows = np.arange(GLOBAL_STRIP_COUNT, dtype=np.uint16)[:, None]
        columns = np.arange(138, dtype=np.uint16)[None, :]
        category = ((rows + columns) % 3).astype(np.uint8)
        globe_region = np.zeros((GLOBAL_STRIP_COUNT, 138), dtype=np.uint8)
        globe_region[category == 2] = (
            ((rows + columns) % 7 + 1)[category == 2]
        ).astype(np.uint8)
        obstacle = category != 0
        clearance = obstacle | ((rows * 5 + columns) % 7 < 3)
        distance = np.where(
            obstacle,
            0,
            (rows * 17 + columns * 3) % 255 + 1,
        ).astype(np.uint8)
        return InstallationProfile(
            global_strip_count=GLOBAL_STRIP_COUNT,
            leds_per_strip=138,
            strip_origin=0,
            strip_count=GLOBAL_STRIP_COUNT,
            clearance_radius=1,
            calibration_digest=bytes(range(32)),
            reversed_strip_order=False,
            category=category,
            clearance=clearance.astype(np.uint8),
            foliage_edge=(category == 1).astype(np.uint8),
            globe_edge=(category == 2).astype(np.uint8),
            obstacle_edge=obstacle.astype(np.uint8),
            globe_region=globe_region,
            distance=distance,
            normal_x=(
                ((rows.astype(np.int32) * 19 + columns) % 255) - 127
            ).astype(np.int8),
            normal_y=(
                ((rows.astype(np.int32) + columns * 23) % 255) - 127
            ).astype(np.int8),
        )

    @staticmethod
    def clone_profile(
        profile: InstallationProfile, **overrides: object
    ) -> InstallationProfile:
        values: dict[str, object] = {
            "global_strip_count": profile.global_strip_count,
            "leds_per_strip": profile.leds_per_strip,
            "strip_origin": profile.strip_origin,
            "strip_count": profile.strip_count,
            "clearance_radius": profile.clearance_radius,
            "calibration_digest": profile.calibration_digest,
            "reversed_strip_order": profile.reversed_strip_order,
            **{name: getattr(profile, name) for name in SECTION_NAMES},
        }
        values.update(overrides)
        return InstallationProfile(**values)

    def assert_profiles_equal(
        self, actual: InstallationProfile, expected: InstallationProfile
    ) -> None:
        for name in (
            "global_strip_count",
            "leds_per_strip",
            "strip_origin",
            "strip_count",
            "clearance_radius",
            "calibration_digest",
            "reversed_strip_order",
        ):
            self.assertEqual(getattr(actual, name), getattr(expected, name), name)
        for name in SECTION_NAMES:
            np.testing.assert_array_equal(
                getattr(actual, name), getattr(expected, name), err_msg=name
            )

    def assert_round_trip(
        self,
        source: InstallationProfile,
        topology: InstallationProfileTopology,
    ) -> None:
        receiver_profiles = slice_installation_profile(source, topology)
        reassembled = reassemble_installation_profile(receiver_profiles, topology)
        self.assert_profiles_equal(reassembled, source)
        self.assertEqual(
            encode_installation_profile(reassembled),
            encode_installation_profile(source),
        )

    def test_identity_and_installed_topologies_match_frozen_coordinate_domains(self):
        self.assertEqual(
            IDENTITY_INSTALLATION_PROFILE_TOPOLOGY.physical_lane_order,
            (0, 1, 2, 3, 4),
        )
        self.assertEqual(
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.logical_to_transport_routes,
            ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2)),
        )
        self.assertEqual(
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.physical_lane_order,
            (0, 1, 2, 3, 4),
        )
        self.assertEqual(
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.reverse_host_strips_by_logical_receiver,
            (False, False, False, False, False),
        )
        self.assertEqual(
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.reverse_native_strips_by_logical_receiver,
            (False, False, True, True, False),
        )

    def test_topology_validation_fails_closed_and_normalizes_sequences(self):
        normalized = self.topology(
            logical_to_transport_routes=[
                [0, 0], [0, 1], [1, 0], [1, 1], [1, 2]
            ],
            physical_lane_order=[4, 3, 2, 1, 0],
            reverse_host_strips_by_logical_receiver=[
                True, False, True, False, True
            ],
            reverse_native_strips_by_logical_receiver=[
                False, True, False, True, False
            ],
        )
        self.assertEqual(normalized.physical_lane_order, (4, 3, 2, 1, 0))
        self.assertEqual(
            normalized.logical_to_transport_routes,
            ((0, 0), (0, 1), (1, 0), (1, 1), (1, 2)),
        )

        invalid_cases = (
            ({"logical_to_transport_routes": ((0, 0),) * 5}, "unique"),
            ({"logical_to_transport_routes": ((0, 0),) * 4}, "5"),
            (
                {
                    "logical_to_transport_routes": (
                        (0, 0),
                        (0, 1),
                        (1, 0),
                        (-1, 1),
                        (1, 2),
                    )
                },
                "non-negative",
            ),
            (
                {
                    "logical_to_transport_routes": (
                        (0, 0),
                        (0, 1),
                        (1, 0),
                        (True, 1),
                        (1, 2),
                    )
                },
                "integer pair",
            ),
            ({"physical_lane_order": (0, 1, 2, 3)}, "5"),
            ({"physical_lane_order": (0, 1, 2, 3, 3)}, "permutation"),
            ({"physical_lane_order": (0, 1, 2, 3, True)}, "integer"),
            (
                {"reverse_host_strips_by_logical_receiver": (False,) * 4},
                "5",
            ),
            (
                {
                    "reverse_native_strips_by_logical_receiver": (
                        False,
                        False,
                        False,
                        0,
                        False,
                    )
                },
                "booleans",
            ),
        )
        for overrides, message in invalid_cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(
                InstallationProfileTopologyError, message
            ):
                self.topology(**overrides)

    def test_identity_slices_every_global_section_without_boundary_recomputation(self):
        source = self.canonical_profile()
        receiver_profiles = slice_installation_profile(
            source, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )

        self.assertEqual(set(receiver_profiles), set(range(RECEIVER_COUNT)))
        for logical_id, receiver_profile in receiver_profiles.items():
            start = sum(RECEIVER_STRIP_COUNTS[:logical_id])
            width = RECEIVER_STRIP_COUNTS[logical_id]
            self.assertEqual(receiver_profile.strip_origin, start)
            self.assertEqual(receiver_profile.strip_count, width)
            self.assertFalse(receiver_profile.reversed_strip_order)
            for name in SECTION_NAMES:
                np.testing.assert_array_equal(
                    getattr(receiver_profile, name),
                    getattr(source, name)[start:start + width],
                    err_msg=f"logical={logical_id}, section={name}",
                )
                self.assertFalse(
                    np.shares_memory(
                        getattr(receiver_profile, name), getattr(source, name)
                    )
                )

        self.assert_round_trip(source, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY)

    def test_camera_measured_lane_assignment_and_native_reversal_round_trip_all_bytes(self):
        source = self.canonical_profile()
        topology = INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
        receiver_profiles = slice_installation_profile(source, topology)
        expected_origins = (0, 8, 16, 24, 32)

        self.assertEqual(topology.physical_lane_order, (0, 1, 2, 3, 4))
        self.assertEqual(
            topology.logical_to_transport_routes,
            ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2)),
        )

        for logical_id, receiver_profile in receiver_profiles.items():
            start = expected_origins[logical_id]
            reversed_order = logical_id in (2, 3)
            self.assertEqual(receiver_profile.strip_origin, start)
            self.assertIs(receiver_profile.reversed_strip_order, reversed_order)
            for name in SECTION_NAMES:
                expected = getattr(source, name)[
                    start:start + RECEIVER_STRIP_COUNTS[logical_id]
                ]
                if reversed_order:
                    expected = expected[::-1]
                np.testing.assert_array_equal(
                    getattr(receiver_profile, name),
                    expected,
                    err_msg=f"logical={logical_id}, section={name}",
                )

        self.assert_round_trip(source, topology)

    def test_checked_in_golden_round_trips_both_topologies_byte_exactly(self):
        golden_bytes = GOLDEN_PATH.read_bytes()
        source = decode_installation_profile(golden_bytes)

        for topology in (
            IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
        ):
            with self.subTest(topology=topology):
                slices = slice_installation_profile(source, topology)
                reassembled = reassemble_installation_profile(slices, topology)
                self.assertEqual(
                    encode_installation_profile(reassembled),
                    golden_bytes,
                )

    def test_transport_routes_and_host_reversal_are_inert_profile_metadata(self):
        source = self.canonical_profile()
        baseline = self.topology(
            physical_lane_order=(0, 1, 3, 2, 4),
            reverse_native_strips_by_logical_receiver=(
                False, False, True, True, False
            ),
        )
        unrelated_maps_changed = self.topology(
            logical_to_transport_routes=(
                (9, 9), (9, 8), (8, 8), (8, 9), (7, 7)
            ),
            physical_lane_order=(0, 1, 3, 2, 4),
            reverse_host_strips_by_logical_receiver=(True,) * RECEIVER_COUNT,
            reverse_native_strips_by_logical_receiver=(
                False, False, True, True, False
            ),
        )
        baseline_slices = slice_installation_profile(source, baseline)
        changed_slices = slice_installation_profile(source, unrelated_maps_changed)

        for logical_id in range(RECEIVER_COUNT):
            self.assertEqual(
                encode_installation_profile(changed_slices[logical_id]),
                encode_installation_profile(baseline_slices[logical_id]),
            )

    def test_physical_assignment_and_native_direction_are_independent_maps(self):
        source = self.canonical_profile()
        identity_slices = slice_installation_profile(
            source, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )
        lane_swap = self.topology(physical_lane_order=(1, 0, 2, 3, 4))
        lane_slices = slice_installation_profile(source, lane_swap)
        self.assertEqual(lane_slices[0].strip_origin, 8)
        self.assertFalse(lane_slices[0].reversed_strip_order)
        np.testing.assert_array_equal(
            lane_slices[0].distance, source.distance[8:16]
        )
        self.assertNotEqual(
            encode_installation_profile(lane_slices[0]),
            encode_installation_profile(identity_slices[0]),
        )

        native_reverse = self.topology(
            reverse_native_strips_by_logical_receiver=(
                True, False, False, False, False
            )
        )
        native_slices = slice_installation_profile(source, native_reverse)
        self.assertEqual(native_slices[0].strip_origin, 0)
        self.assertTrue(native_slices[0].reversed_strip_order)
        np.testing.assert_array_equal(
            native_slices[0].distance, source.distance[:8][::-1]
        )
        self.assertNotEqual(
            encode_installation_profile(native_slices[0]),
            encode_installation_profile(identity_slices[0]),
        )

    def test_slicing_rejects_receiver_views_and_reversed_global_sources(self):
        source = self.canonical_profile()
        receiver_profile = slice_installation_profile(
            source, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )[0]
        with self.assertRaisesRegex(
            InstallationProfileTopologyError, "canonical.*global"
        ):
            slice_installation_profile(
                receiver_profile, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
            )

        reversed_global = self.clone_profile(
            source,
            reversed_strip_order=True,
            **{
                name: np.ascontiguousarray(getattr(source, name)[::-1])
                for name in SECTION_NAMES
            },
        )
        with self.assertRaisesRegex(
            InstallationProfileTopologyError, "non-reversed.*global"
        ):
            slice_installation_profile(
                reversed_global, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
            )

    def test_reassembly_rejects_missing_duplicate_and_mismatched_origins(self):
        source = self.canonical_profile()
        topology = IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        receiver_profiles = slice_installation_profile(source, topology)

        missing = dict(receiver_profiles)
        del missing[3]
        with self.assertRaisesRegex(
            InstallationProfileTopologyError, "each logical receiver"
        ):
            reassemble_installation_profile(missing, topology)

        duplicate_origin = dict(receiver_profiles)
        duplicate_origin[1] = self.clone_profile(
            duplicate_origin[1], strip_origin=0
        )
        with self.assertRaisesRegex(InstallationProfileTopologyError, "unique"):
            reassemble_installation_profile(duplicate_origin, topology)

        swapped_origins = dict(receiver_profiles)
        swapped_origins[0] = self.clone_profile(
            swapped_origins[0], strip_origin=8
        )
        swapped_origins[1] = self.clone_profile(
            swapped_origins[1], strip_origin=0
        )
        with self.assertRaisesRegex(InstallationProfileTopologyError, "origin"):
            reassemble_installation_profile(swapped_origins, topology)

    def test_reassembly_rejects_mixed_geometry_digest_radius_and_direction(self):
        source = self.canonical_profile()
        topology = INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
        original = slice_installation_profile(source, topology)

        geometry = dict(original)
        profile = geometry[0]
        geometry[0] = self.clone_profile(
            profile,
            strip_count=7,
            **{name: getattr(profile, name)[:7] for name in SECTION_NAMES},
        )
        digest = dict(original)
        digest[1] = self.clone_profile(
            digest[1], calibration_digest=b"x" * 32
        )
        radius = dict(original)
        radius[2] = self.clone_profile(radius[2], clearance_radius=2)
        direction = dict(original)
        direction[3] = self.clone_profile(
            direction[3], reversed_strip_order=False
        )

        cases = (
            (geometry, "geometry"),
            (digest, "calibration digest"),
            (radius, "clearance radius"),
            (direction, "native strip direction"),
        )
        for receiver_profiles, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                InstallationProfileTopologyError, message
            ):
                reassemble_installation_profile(receiver_profiles, topology)

    def test_adapter_boundaries_reject_wrong_object_types(self):
        source = self.canonical_profile()
        with self.assertRaisesRegex(TypeError, "topology"):
            slice_installation_profile(source, object())  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "mapping"):
            reassemble_installation_profile((), self.topology())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

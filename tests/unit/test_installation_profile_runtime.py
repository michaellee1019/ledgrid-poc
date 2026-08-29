"""Focused acceptance for managed installation-profile runtime geometry."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from animation.core.base import AnimationBase
from animation.core.installation_profile import decode_installation_profile
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_runtime import (
    EMPTY_INSTALLATION_PROFILE_DIGEST,
    InstallationProfileRuntimeError,
    InstallationProfileRuntimeView,
    InstallationProfileSelection,
)
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
)
from animation.core.plant_awareness import GLOBE_REGION_ORDER, PlantMaskCache
from animation.core.presentation_contracts import AnimationRuntimeContext
from tools.fixtures.generate_installation_profile_golden import (
    CLEARANCE_RADIUS,
    FOLIAGE_EVIDENCE_INPUT,
    GLOBES_EVIDENCE_INPUT,
)


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"
PROFILE_DIGEST = (
    "ce457a14efd131395507c449f35a7701"
    "ca78ddca059620dc3757806ef553ca6a"
)


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


class _LegacyMaskOwner:
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
        return 32 * 138


class _RuntimeAnimation(AnimationBase):
    def __init__(self, config=None):
        super().__init__(_Controller(), config)
        self.rng = np.random.default_rng(731)
        self.semantic_state = [3, 1, 4]
        self.presentation_changes = 0

    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()

    def on_presentation_context_changed(self, old, new):
        # Deliberately do not call super(): base-owned invalidation must remain
        # correct for existing plugins whose hooks predate this runtime view.
        self.presentation_changes += 1


def _context(
    installation_profile_view,
    *,
    wall_time: float = 10.0,
    frame_index: int = 0,
    vibe_id: str = "neutral",
) -> AnimationRuntimeContext:
    return AnimationRuntimeContext(
        wall_time=wall_time,
        unscaled_elapsed=2.0,
        scaled_elapsed=2.0,
        frame_index=frame_index,
        scene_epoch=7,
        global_width=33,
        height=138,
        local_strip_offset=0,
        local_width=33,
        vibe_id=vibe_id,
        vibe_profile_version=1,
        palette_roles={},
        capability_values={},
        installation_profile_view=installation_profile_view,
        plant_modifiers={},
    )


class InstallationProfileRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.library = InstallationProfileLibrary(Path(self.temporary.name) / "library")
        self.golden = GOLDEN_PATH.read_bytes()
        self.library.publish(self.golden)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def selection(self, *, installed: bool = True) -> InstallationProfileSelection:
        topology = (
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
            if installed
            else IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )
        return InstallationProfileSelection(self.library, topology)

    def selected_view(self) -> InstallationProfileRuntimeView:
        selection = self.selection()
        self.assertTrue(selection.select(PROFILE_DIGEST))
        view = selection.view
        self.assertIsInstance(view, InstallationProfileRuntimeView)
        return view

    def test_empty_selection_and_default_legacy_context_remain_compatible(self) -> None:
        selection = InstallationProfileSelection()
        self.assertEqual(selection.selected_digest, EMPTY_INSTALLATION_PROFILE_DIGEST)
        self.assertEqual(selection.revision, 0)
        self.assertIsNone(selection.view)
        self.assertIsNone(selection.resolved)
        self.assertFalse(selection.select(None))
        self.assertEqual(selection.revision, 0)
        self.assertEqual(
            selection.status(),
            {
                "selected_digest": EMPTY_INSTALLATION_PROFILE_DIGEST,
                "revision": 0,
                "selected": False,
                "view": None,
            },
        )
        json.dumps(selection.status())

        animation = _RuntimeAnimation()
        direct = animation.get_plant_masks()
        animation.set_presentation_context(_context({}))
        legacy_context = animation.get_plant_masks()
        for field in (
            "foliage",
            "globes",
            "obstacle",
            "clearance",
            "foliage_edge",
            "globe_edge",
            "obstacle_edge",
            "distance",
            "normal_x",
            "normal_y",
        ):
            np.testing.assert_array_equal(
                getattr(legacy_context, field), getattr(direct, field)
            )

    def test_runtime_geometry_has_exact_fixture_and_legacy_mask_parity(self) -> None:
        view = self.selected_view()
        geometry = view.plant_masks
        profile = decode_installation_profile(self.golden)
        legacy = PlantMaskCache(_LegacyMaskOwner()).get(CLEARANCE_RADIUS)

        expected = {
            "foliage": profile.category == 1,
            "globes": profile.category == 2,
            "obstacle": profile.category != 0,
            "clearance": profile.clearance.astype(bool),
            "foliage_edge": profile.foliage_edge.astype(bool),
            "globe_edge": profile.globe_edge.astype(bool),
            "obstacle_edge": profile.obstacle_edge.astype(bool),
            "distance": profile.distance.astype(np.float32),
            "normal_x": profile.normal_x.astype(np.float32) / 127.0,
            "normal_y": profile.normal_y.astype(np.float32) / 127.0,
        }
        for field, value in expected.items():
            with self.subTest(field=field):
                np.testing.assert_array_equal(getattr(geometry, field), value)

        for field in (
            "foliage",
            "globes",
            "obstacle",
            "clearance",
            "foliage_edge",
            "globe_edge",
            "obstacle_edge",
            "distance",
        ):
            with self.subTest(legacy_field=field):
                np.testing.assert_array_equal(
                    getattr(geometry, field)[:32], getattr(legacy, field)
                )
        self.assertEqual(geometry.foliage_count, legacy.foliage_count)
        self.assertEqual(geometry.globe_count, legacy.globe_count)
        self.assertEqual(geometry.globe_regions, legacy.globe_regions)
        for name in GLOBE_REGION_ORDER:
            np.testing.assert_array_equal(
                geometry.globe_region_masks[name][:32],
                legacy.globe_region_masks[name],
            )

    def test_every_runtime_geometry_array_is_non_writeable(self) -> None:
        geometry = self.selected_view().geometry
        arrays = {
            field: getattr(geometry, field)
            for field in (
                "foliage",
                "globes",
                "obstacle",
                "clearance",
                "foliage_flat",
                "globes_flat",
                "obstacle_flat",
                "clearance_flat",
                "foliage_edge",
                "globe_edge",
                "obstacle_edge",
                "distance",
                "normal_x",
                "normal_y",
                "safe",
                "safe_flat",
            )
        }
        arrays.update(
            {
                f"globe_region_masks.{name}": value
                for name, value in geometry.globe_region_masks.items()
            }
        )
        for name, value in arrays.items():
            with self.subTest(array=name):
                self.assertFalse(value.flags.writeable)
                with self.assertRaises(ValueError):
                    value.flat[0] = not bool(value.flat[0])
        with self.assertRaises(TypeError):
            geometry.globe_region_masks["new"] = geometry.globes

    def test_public_view_construction_rejects_mutable_or_malformed_geometry(self) -> None:
        view = self.selected_view()
        mutable = replace(view.plant_masks, foliage=view.plant_masks.foliage.copy())
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "non-writeable"):
            replace(view, plant_masks=mutable)

        wrong_shape = view.plant_masks.foliage[:-1].copy()
        wrong_shape.setflags(write=False)
        malformed = replace(view.plant_masks, foliage=wrong_shape)
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "shape"):
            replace(view, plant_masks=malformed)

        writeable_region = dict(view.plant_masks.globe_region_masks)
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "immutable mapping"):
            replace(
                view,
                plant_masks=replace(
                    view.plant_masks, globe_region_masks=writeable_region
                ),
            )

    def test_runtime_constructor_and_selection_validate_boundary_types(self) -> None:
        view = self.selected_view()
        with self.assertRaises(TypeError):
            InstallationProfileRuntimeView.from_resolved(object())
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "empty digest"):
            replace(view, profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST)
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "format_version"):
            replace(view, format_version=2)
        with self.assertRaises(TypeError):
            replace(view, topology=object())
        with self.assertRaises(TypeError):
            replace(view, plant_masks=object())
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "global 33x138"):
            replace(view, global_width=31)
        with self.assertRaises(TypeError):
            InstallationProfileSelection(library=object())
        with self.assertRaises(TypeError):
            InstallationProfileSelection(topology=object())
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "required"):
            InstallationProfileSelection().select(PROFILE_DIGEST)
        self.assertEqual(view.compact_identity, view.presentation_identity)

    def test_selection_is_idempotent_and_failure_is_atomic(self) -> None:
        selection = self.selection()
        self.assertTrue(selection.select(PROFILE_DIGEST))
        self.assertEqual(selection.revision, 1)
        prior = (
            selection.selected_digest,
            selection.revision,
            selection.view,
            selection.resolved,
        )
        with mock.patch.object(
            self.library, "resolve", wraps=self.library.resolve
        ) as resolve:
            self.assertFalse(selection.select(PROFILE_DIGEST))
            resolve.assert_not_called()
        self.assertEqual(
            (
                selection.selected_digest,
                selection.revision,
                selection.view,
                selection.resolved,
            ),
            prior,
        )

        missing = "1" * 64
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "failed to resolve"):
            selection.select(missing)
        self.assertEqual(
            (
                selection.selected_digest,
                selection.revision,
                selection.view,
                selection.resolved,
            ),
            prior,
        )
        with self.assertRaisesRegex(InstallationProfileRuntimeError, "lowercase"):
            selection.select("../unsafe")
        self.assertEqual(selection.revision, 1)

        self.assertTrue(selection.select(EMPTY_INSTALLATION_PROFILE_DIGEST))
        self.assertEqual(selection.selected_digest, EMPTY_INSTALLATION_PROFILE_DIGEST)
        self.assertEqual(selection.revision, 2)
        self.assertIsNone(selection.view)
        self.assertIsNone(selection.resolved)

    def test_initial_selection_and_topology_participate_in_compact_identity(self) -> None:
        installed = InstallationProfileSelection(
            self.library,
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
            selected_digest=PROFILE_DIGEST,
        )
        identity = InstallationProfileSelection(
            self.library,
            IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
            selected_digest=PROFILE_DIGEST,
        )
        self.assertEqual(installed.revision, 1)
        self.assertEqual(installed.view.profile_digest, PROFILE_DIGEST)
        self.assertEqual(installed.view.content_digest, PROFILE_DIGEST)
        self.assertEqual(installed.resolved.id, PROFILE_DIGEST)
        self.assertIs(installed.view.topology, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY)
        self.assertNotEqual(
            installed.view.presentation_identity,
            identity.view.presentation_identity,
        )
        self.assertEqual(
            installed.view.topology_identity,
            (
                INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.physical_lane_order,
                INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.reverse_native_strips_by_logical_receiver,
                INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.strip_counts_by_logical_receiver,
            ),
        )
        json.dumps(installed.status())

    def test_transport_and_host_direction_are_inert_but_semantic_topology_is_not(self) -> None:
        base = INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
        inert = InstallationProfileTopology(
            logical_to_transport_routes=(
                (4, 0), (4, 1), (5, 0), (5, 1), (5, 2)
            ),
            physical_lane_order=base.physical_lane_order,
            reverse_host_strips_by_logical_receiver=(
                True, True, False, False, True
            ),
            reverse_native_strips_by_logical_receiver=(
                base.reverse_native_strips_by_logical_receiver
            ),
        )
        changed_lane = InstallationProfileTopology(
            logical_to_transport_routes=base.logical_to_transport_routes,
            physical_lane_order=(0, 1, 3, 2, 4),
            reverse_host_strips_by_logical_receiver=(
                base.reverse_host_strips_by_logical_receiver
            ),
            reverse_native_strips_by_logical_receiver=(
                base.reverse_native_strips_by_logical_receiver
            ),
        )
        changed_native = InstallationProfileTopology(
            logical_to_transport_routes=base.logical_to_transport_routes,
            physical_lane_order=base.physical_lane_order,
            reverse_host_strips_by_logical_receiver=(
                base.reverse_host_strips_by_logical_receiver
            ),
            reverse_native_strips_by_logical_receiver=(False,) * 5,
        )
        views = []
        for topology in (base, inert, changed_lane, changed_native):
            selection = InstallationProfileSelection(
                self.library, topology, selected_digest=PROFILE_DIGEST
            )
            views.append(selection.view)
        self.assertEqual(
            views[0].presentation_identity, views[1].presentation_identity
        )
        self.assertNotEqual(
            views[0].status()["topology"], views[1].status()["topology"]
        )
        self.assertNotEqual(
            views[0].presentation_identity, views[2].presentation_identity
        )
        self.assertNotEqual(
            views[0].presentation_identity, views[3].presentation_identity
        )

    def test_concurrent_selection_and_status_never_expose_mixed_state(self) -> None:
        selection = self.selection()

        def toggle(index: int) -> None:
            selection.select(PROFILE_DIGEST if index % 2 else None)

        def read(_: int) -> None:
            status = selection.status()
            if status["selected"]:
                self.assertEqual(status["selected_digest"], PROFILE_DIGEST)
                self.assertEqual(status["view"]["profile_digest"], PROFILE_DIGEST)
            else:
                self.assertEqual(
                    status["selected_digest"], EMPTY_INSTALLATION_PROFILE_DIGEST
                )
                self.assertIsNone(status["view"])

        with ThreadPoolExecutor(max_workers=8) as executor:
            operations = []
            for index in range(80):
                operations.append(executor.submit(toggle, index))
                operations.append(executor.submit(read, index))
            for operation in operations:
                operation.result()

    def test_runtime_context_keeps_view_by_reference_and_uses_compact_identity(self) -> None:
        selection = self.selection()
        self.assertTrue(selection.select(PROFILE_DIGEST))
        view = selection.view
        equivalent = InstallationProfileRuntimeView.from_resolved(selection.resolved)
        first = _context(view, frame_index=1)
        later = _context(equivalent, wall_time=20.0, frame_index=2)
        self.assertIs(first.installation_profile_view, view)
        self.assertIs(later.installation_profile_view, equivalent)
        self.assertEqual(first.presentation_identity, later.presentation_identity)
        self.assertEqual(first.installation_profile_identity, view.presentation_identity)
        self.assertTrue(
            all(not isinstance(item, np.ndarray) for item in first.presentation_identity)
        )

        legacy = _context({"digest": PROFILE_DIGEST})
        self.assertEqual(dict(legacy.installation_profile_view), {"digest": PROFILE_DIGEST})
        with self.assertRaises(TypeError):
            legacy.installation_profile_view["digest"] = "0" * 64

    def test_context_change_invalidates_only_presentation_caches(self) -> None:
        animation = _RuntimeAnimation({"brightness": 0.37, "seed": 42})
        view = self.selected_view()
        first = _context(view, frame_index=1)
        animation.set_presentation_context(first)
        animation._framework_modifier_cached_frame = np.zeros((33 * 138, 3), np.uint8)
        cached_legacy = animation._plant_mask_cache.get()
        authored = animation.authored_params_snapshot()
        semantic = deepcopy(animation.semantic_state)
        rng = deepcopy(animation.rng.bit_generator.state)
        start_time = animation.start_time

        # Clock-only changes are absent from presentation identity.
        animation.set_presentation_context(_context(view, wall_time=30.0, frame_index=8))
        self.assertIsNotNone(animation._framework_modifier_cached_frame)
        self.assertEqual(animation.presentation_changes, 1)

        animation.set_presentation_context(_context({}))
        self.assertIsNone(animation._framework_modifier_cached_frame)
        self.assertIsNot(animation.get_plant_masks(), cached_legacy)
        self.assertEqual(animation.presentation_changes, 2)
        self.assertEqual(animation.authored_params_snapshot(), authored)
        self.assertEqual(animation.semantic_state, semantic)
        self.assertEqual(animation.rng.bit_generator.state, rng)
        self.assertEqual(animation.start_time, start_time)
        self.assertEqual(animation.frame_count, 0)

    def test_managed_view_preserves_explicit_clearance_api_without_frame_copies(self) -> None:
        animation = _RuntimeAnimation()
        view = self.selected_view()
        animation.set_presentation_context(_context(view))

        base = animation.get_plant_masks()
        zero = animation.get_plant_masks(0)
        wide = animation.get_plant_masks(4)

        self.assertIs(base, view.plant_masks)
        self.assertIs(animation.get_plant_masks(0), zero)
        self.assertIs(animation.get_plant_masks(4), wide)
        np.testing.assert_array_equal(zero.clearance, base.distance <= 0)
        np.testing.assert_array_equal(wide.clearance, base.distance <= 4)
        self.assertFalse(zero.clearance.flags.writeable)
        self.assertFalse(wide.clearance_flat.flags.writeable)
        self.assertIs(zero.foliage, base.foliage)
        self.assertIs(wide.distance, base.distance)

    def test_managed_hue_shift_targets_selected_profile_geometry(self) -> None:
        animation = _RuntimeAnimation(
            {
                "plant_modifiers": {
                    "version": 1,
                    "active": ["hue_shift"],
                    "strengths": {"hue_shift": 1.0},
                },
                # If the runtime path accidentally falls back to JSON, this
                # deliberately missing calibration yields an empty wall.
                "plant_mask_path": "/missing/runtime-foliage.json",
                "plant_globe_mask_path": "/missing/runtime-globes.json",
            }
        )
        view = self.selected_view()
        animation.set_presentation_context(_context(view))
        source = np.tile(np.asarray((240, 30, 10), dtype=np.uint8), (33 * 138, 1))
        shifted = animation.apply_framework_plant_modifiers(source)
        changed = np.any(shifted != source, axis=1)
        np.testing.assert_array_equal(changed, view.plant_masks.obstacle_flat)
        self.assertFalse(np.shares_memory(shifted, source))


if __name__ == "__main__":
    unittest.main()

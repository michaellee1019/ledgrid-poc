"""Managed installation-profile authoring acceptance coverage."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from animation.core.installation_profile import decode_installation_profile
from animation.core.installation_profile_authoring import (
    InstallationProfileAuthoring,
    InstallationProfileAuthoringError,
    InstallationProfileDraftConflict,
    compile_installation_profile_draft,
    validate_installation_profile_draft,
)
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.plant_awareness import GLOBE_REGION_ORDER


ROOT = Path(__file__).resolve().parents[2]
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"


class InstallationProfileAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = InstallationProfileLibrary(self.root / "library")
        self.source_receipt = self.library.publish(PROFILE_FIXTURE.read_bytes())
        self.digest = self.source_receipt.content_digest
        self.state_root = self.root / "authoring"
        self.authoring = InstallationProfileAuthoring(
            self.library, self.state_root
        )

    @staticmethod
    def first_open_pixel(draft: dict) -> int:
        masks = draft["masks"]
        occupied = set(masks["foliage"])
        for values in masks["globes"].values():
            occupied.update(values)
        return next(index for index in range(33 * 138) if index not in occupied)

    def changed_draft(self, draft: dict) -> dict:
        changed = deepcopy(draft)
        changed["masks"]["foliage"].append(self.first_open_pixel(draft))
        changed["masks"]["foliage"].sort()
        return changed

    def test_exact_source_artifact_becomes_explicit_stable_seven_region_draft(self) -> None:
        draft = self.authoring.load(self.digest)
        source = decode_installation_profile(PROFILE_FIXTURE.read_bytes())

        self.assertEqual(draft["digest"], self.digest)
        self.assertRegex(draft["revision"], r"^ipd-[0-9a-f]{32}$")
        self.assertEqual(draft["led_info"], {
            "strip_count": 33,
            "leds_per_strip": 138,
            "total_leds": 4554,
        })
        self.assertEqual(draft["unobserved_non_plant_strips"], [32])
        self.assertEqual(
            tuple(draft["masks"]["globes"]), GLOBE_REGION_ORDER
        )
        self.assertEqual(
            draft["masks"]["foliage"],
            source.foliage.ravel().nonzero()[0].tolist(),
        )
        for region_id, name in enumerate(GLOBE_REGION_ORDER, start=1):
            self.assertEqual(
                draft["masks"]["globes"][name],
                (source.globe_region.ravel() == region_id).nonzero()[0].tolist(),
            )

    def test_validation_rejects_geometry_bounds_overlap_and_region_drift(self) -> None:
        draft = self.authoring.load(self.digest)
        cases: list[tuple[str, dict, str]] = []

        geometry = deepcopy(draft)
        geometry["led_info"]["strip_count"] = 32
        cases.append(("geometry", geometry, "canonical 33x138"))

        bounds = deepcopy(draft)
        bounds["masks"]["foliage"] = [4554]
        cases.append(("bounds", bounds, "outside the canonical 33x138"))

        unobserved_marker = deepcopy(draft)
        unobserved_marker["unobserved_non_plant_strips"] = []
        cases.append(("unobserved marker", unobserved_marker, "physical strip 32"))

        unobserved_mask = deepcopy(draft)
        unobserved_mask["masks"]["foliage"] = [32 * 138]
        cases.append(("unobserved mask", unobserved_mask, "unobserved non-plant strip"))

        overlap = deepcopy(draft)
        shared = overlap["masks"]["globes"][GLOBE_REGION_ORDER[0]][0]
        overlap["masks"]["foliage"] = sorted(
            set(overlap["masks"]["foliage"]) | {shared}
        )
        cases.append(("overlap", overlap, "semantic layers overlap"))

        region_identity = deepcopy(draft)
        region_identity["masks"]["globes"]["unknown_region"] = (
            region_identity["masks"]["globes"].pop(GLOBE_REGION_ORDER[-1])
        )
        cases.append(("region identity", region_identity, "stable seven-region"))

        missing_region = deepcopy(draft)
        del missing_region["masks"]["globes"][GLOBE_REGION_ORDER[-1]]
        cases.append(("missing region", missing_region, "stable seven-region"))

        for label, candidate, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                InstallationProfileAuthoringError, message
            ):
                validate_installation_profile_draft(
                    candidate, expected_digest=self.digest
                )

    def test_update_is_restart_safe_and_stale_update_has_zero_mutation(self) -> None:
        initial = self.authoring.load(self.digest)
        changed = self.changed_draft(initial)
        updated = self.authoring.update(
            self.digest,
            expected_revision=initial["revision"],
            draft=changed,
        )
        draft_path = self.state_root / "drafts" / f"{self.digest}.json"
        persisted_after_update = draft_path.read_bytes()

        restarted = InstallationProfileAuthoring(
            InstallationProfileLibrary(self.root / "library"), self.state_root
        )
        self.assertEqual(restarted.load(self.digest), updated)
        self.assertNotEqual(updated["revision"], initial["revision"])

        stale_candidate = deepcopy(initial)
        stale_candidate["masks"]["foliage"] = []
        with self.assertRaises(InstallationProfileDraftConflict) as raised:
            restarted.update(
                self.digest,
                expected_revision=initial["revision"],
                draft=stale_candidate,
            )
        self.assertEqual(raised.exception.current_revision, updated["revision"])
        self.assertEqual(draft_path.read_bytes(), persisted_after_update)
        self.assertEqual(restarted.load(self.digest), updated)

    def test_stale_publish_does_not_create_an_artifact(self) -> None:
        initial = self.authoring.load(self.digest)
        updated = self.authoring.update(
            self.digest,
            expected_revision=initial["revision"],
            draft=self.changed_draft(initial),
        )
        profiles = self.library.profiles_directory
        before = {path.name for path in profiles.iterdir()}

        with self.assertRaises(InstallationProfileDraftConflict):
            self.authoring.publish(
                self.digest, expected_revision=initial["revision"]
            )

        self.assertEqual({path.name for path in profiles.iterdir()}, before)
        self.assertEqual(self.authoring.load(self.digest), updated)

    def test_publish_compiles_one_immutable_33x138_artifact_and_keeps_draft(self) -> None:
        initial = self.authoring.load(self.digest)
        updated = self.authoring.update(
            self.digest,
            expected_revision=initial["revision"],
            draft=self.changed_draft(initial),
        )

        receipt, published_from = self.authoring.publish(
            self.digest, expected_revision=updated["revision"]
        )
        resolved = self.library.resolve(receipt.content_digest)

        self.assertEqual(published_from, updated)
        self.assertEqual(self.authoring.load(self.digest), updated)
        self.assertEqual(resolved.global_profile.category.shape, (33, 138))
        self.assertFalse(resolved.global_profile.obstacle[32].any())
        self.assertEqual(
            tuple(resolved.global_profile.globe_region_masks),
            GLOBE_REGION_ORDER,
        )
        self.assertEqual(
            {path.name for path in self.library.profiles_directory.iterdir()},
            {self.digest, receipt.content_digest},
        )
        self.assertEqual(
            self.library.artifact_path(receipt.content_digest).stat().st_mode & 0o222,
            0,
        )

    def test_direct_compiler_rejects_noncanonical_draft_before_output(self) -> None:
        draft = self.authoring.load(self.digest)
        draft["masks"]["foliage"] = [2, 1]
        with self.assertRaisesRegex(
            InstallationProfileAuthoringError, "sorted, unique, and ascending"
        ):
            compile_installation_profile_draft(draft, clearance_radius=1)


if __name__ == "__main__":
    unittest.main()

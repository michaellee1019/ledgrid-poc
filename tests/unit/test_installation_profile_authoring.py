"""Managed installation-profile authoring acceptance coverage."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from flask import Flask

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
from web.installation_profile_api import (
    register_installation_profile_api,
    undo_draft,
)


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

    def test_edit_undo_save_publish_and_reload_preserve_canonical_profile(self) -> None:
        """A UI undo is a revisioned save, never a hidden mutable profile mode."""

        initial = self.authoring.load(self.digest)
        edited = self.authoring.update(
            self.digest,
            expected_revision=initial["revision"],
            draft=self.changed_draft(initial),
        )
        restored = undo_draft(
            self.authoring,
            self.digest,
            expected_revision=edited["revision"],
            historical_draft=initial,
        )
        self.assertNotEqual(restored["revision"], initial["revision"])
        self.assertEqual(restored["masks"], initial["masks"])

        receipt, published_from = self.authoring.publish(
            self.digest, expected_revision=restored["revision"]
        )
        reloaded = InstallationProfileAuthoring(
            InstallationProfileLibrary(self.root / "library"), self.state_root
        ).load(self.digest)

        # Canonical authoring deliberately derives its calibration identity from
        # the public draft document, not the camera-evidence artifact it began
        # from.  Re-publishing the unchanged draft is nevertheless exact.
        repeated, _ = self.authoring.publish(
            self.digest, expected_revision=restored["revision"]
        )
        self.assertEqual(receipt.content_digest, repeated.content_digest)
        self.assertEqual(published_from, restored)
        self.assertEqual(reloaded, restored)
        self.assertEqual(
            tuple(reloaded["masks"]["globes"]), GLOBE_REGION_ORDER
        )

    def test_http_draft_edit_undo_save_publish_activation_intent_and_reload(self) -> None:
        app = Flask(__name__)
        register_installation_profile_api(app, self.authoring)
        client = app.test_client()
        base = f"/api/v1/installation-profiles/{self.digest}"

        loaded = client.get(base + "/draft")
        self.assertEqual(loaded.status_code, 200)
        initial = loaded.get_json()

        saved = client.put(
            base + "/draft",
            json=self.changed_draft(initial),
            headers={"If-Match": loaded.headers["ETag"]},
        )
        self.assertEqual(saved.status_code, 200)
        edited = saved.get_json()

        # A UI undo carries the earlier geometry forward under the revision
        # produced by the edit, preserving compare-and-swap semantics.
        undo = deepcopy(initial)
        undo["revision"] = edited["revision"]
        restored_response = client.put(
            base + "/draft",
            json=undo,
            headers={"If-Match": saved.headers["ETag"]},
        )
        self.assertEqual(restored_response.status_code, 200)
        restored = restored_response.get_json()
        self.assertEqual(restored["masks"], initial["masks"])

        published = client.post(
            base + "/publish",
            headers={"If-Match": restored_response.headers["ETag"]},
        )
        self.assertEqual(published.status_code, 200)
        publication = published.get_json()
        self.assertFalse(publication["selected"])
        self.assertEqual(
            publication["activation_intent"],
            {
                "source_installation_profile_digest": self.digest,
                "installation_profile_digest": publication["published_digest"],
            },
        )
        self.assertEqual(
            client.get(publication["artifact_url"]).data,
            self.library.resolve(publication["published_digest"]).encoded,
        )

        reloaded = client.get(base + "/draft")
        self.assertEqual(reloaded.status_code, 200)
        self.assertEqual(reloaded.get_json(), restored)

    def test_direct_compiler_rejects_noncanonical_draft_before_output(self) -> None:
        draft = self.authoring.load(self.digest)
        draft["masks"]["foliage"] = [2, 1]
        with self.assertRaisesRegex(
            InstallationProfileAuthoringError, "sorted, unique, and ascending"
        ):
            compile_installation_profile_draft(draft, clearance_radius=1)


if __name__ == "__main__":
    unittest.main()

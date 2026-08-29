"""Managed installation-profile authoring contracts in the Composer UI."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSER = ROOT / "web/static/js/composer.js"
RUNTIME = ROOT / "web/static/js/composer_runtime.js"
PYTHON_WORKER = ROOT / "web/static/js/composer_python_worker.js"
NATIVE_WORKER = ROOT / "web/static/js/composer_native_worker.js"
SHA256_MODULE = ROOT / "web/static/js/composer_sha256.js"
TEMPLATE = ROOT / "web/templates/composer.html"
BUNDLE_BUILDER = ROOT / "tools/build_browser_python_bundle.py"

GLOBE_REGION_ORDER = (
    "top_left",
    "top_right",
    "upper_middle",
    "middle_left",
    "middle_right",
    "lower_left",
    "lower_right",
)


class BrowserComposerProfileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.composer = COMPOSER.read_text(encoding="utf-8")
        cls.runtime = RUNTIME.read_text(encoding="utf-8")
        cls.python_worker = PYTHON_WORKER.read_text(encoding="utf-8")
        cls.native_worker = NATIVE_WORKER.read_text(encoding="utf-8")
        cls.sha256_module = SHA256_MODULE.read_text(encoding="utf-8")
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.builder = BUNDLE_BUILDER.read_text(encoding="utf-8")

    def test_all_seven_globes_are_first_class_ordered_layers(self) -> None:
        offsets = [self.composer.index(f"'{name}'") for name in GLOBE_REGION_ORDER]
        self.assertEqual(offsets, sorted(offsets))
        for index, name in enumerate(GLOBE_REGION_ORDER, start=2):
            self.assertIn(f'data-mask-tool="{name}"', self.template)
            self.assertIn(f"<kbd>{index}</kbd>", self.template)
            self.assertIn(f'id="editor-{name.replace("_", "-")}-count"', self.template)
        self.assertNotIn("planter_bowls", self.composer)
        self.assertNotIn("Planter bowls", self.template)

    def test_draft_save_publish_and_stale_preservation_are_separate(self) -> None:
        for action in (
            "installation_profile_draft_url",
            "installation_profile_publish_url",
            "installation_profile_artifact_url",
        ):
            self.assertIn(action, self.composer)
        self.assertIn("method: 'PUT'", self.composer)
        self.assertIn("'If-Match': `\"${submittedRevision}\"`", self.composer)
        self.assertIn("'If-Match': `\"${state.masks.revision}\"`", self.composer)
        self.assertIn("if (error.status === 409)", self.composer)
        self.assertIn("Your exact local draft is preserved", self.composer)
        self.assertIn("payload.selected !== false", self.composer)
        self.assertIn("state.installationProfile.candidate", self.composer)
        self.assertIn('id="profileCandidateDialog"', self.template)
        self.assertIn("stageProfileCandidate", self.composer)
        self.assertIn("desiredDigest = candidate.digest", self.composer)
        self.assertIn("does not select the profile on the wall", self.template.lower())

    def test_profile_draft_is_local_restart_state_and_invalidates_check(self) -> None:
        self.assertIn(".profile-draft.${digest}", self.composer)
        self.assertIn("saved_cells", self.composer)
        self.assertIn("restoredMaskDraft", self.composer)
        self.assertIn("persistMaskDraft();\n        resetChecker({preserveDocumentRevision: true});", self.composer)
        self.assertIn("updateSelectedInstallationProfile", self.composer)
        self.assertIn("resetChecker({preserveDocumentRevision: true});", self.composer)

    def test_every_worker_init_and_recovery_descriptor_carries_profile(self) -> None:
        self.assertIn("installationProfile: descriptor.installationProfile", self.runtime)
        self.assertIn("installationProfileArtifact: profileArtifact", self.runtime)
        self.assertIn("await installationProfileArtifact(", self.runtime)
        self.assertIn("installationProfile: this.installationProfile", self.runtime)
        self.assertIn("descriptor.initializedGeneration = -1", self.runtime)
        self.assertIn("installationProfileDescriptor(options.installationProfile)", self.runtime)
        self.assertIn("composerRuntimeOptions", self.composer)
        self.assertEqual(self.composer.count("new ComposerRuntime("), 6)
        self.assertEqual(self.composer.count("composerRuntimeOptions("), 7)

    def test_both_workers_verify_embedded_lgip_content_digest(self) -> None:
        for worker in (self.python_worker, self.native_worker):
            self.assertIn("import {sha256Bytes} from './composer_sha256.js'", worker)
            self.assertIn("await sha256Bytes(digestInput, self.crypto)", worker)
            self.assertIn("digestInput.fill(0", worker)
            self.assertIn("LGIP", worker)
            self.assertIn("verifiedProfile", worker)
            self.assertIn("installationProfile", worker)
            self.assertNotIn("fallbackMask", worker)
        self.assertIn("export function sha256Portable", self.sha256_module)
        self.assertIn("cryptoProvider?.subtle", self.sha256_module)
        self.assertIn("bind_installation_profile_path", self.python_worker)
        self.assertIn("const PROFILE_DIGEST_OFFSET = 68", self.python_worker)
        self.assertIn("message.installationProfileArtifact", self.python_worker)
        self.assertIn("suppliedArtifact?.bytes instanceof ArrayBuffer", self.python_worker)

    def test_python_bundle_has_codec_but_no_calibration_json_fallbacks(self) -> None:
        self.assertIn('"animation/core/installation_profile.py"', self.builder)
        self.assertNotIn('"config/plant_pixel_map_32x138.json"', self.builder)
        self.assertNotIn('"config/plant_globe_map_32x138.json"', self.builder)
        self.assertIn('"requiresManagedInstallationProfile": True', self.builder)

    def test_browser_evidence_is_labeled_and_not_a_power_claim(self) -> None:
        for field in (
            "source: 'browser'",
            "capturedAt: Date.now()",
            "sampleCount: SAMPLE_FRAMES",
            "frameTimeMs",
            "missedFrameRatio",
            "changedFrameRatio",
            "kind: 'uncalibrated_estimate'",
            "nominalVoltageVolts: 5",
        ):
            self.assertIn(field, self.composer)


if __name__ == "__main__":
    unittest.main()

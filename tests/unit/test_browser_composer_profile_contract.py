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
        self.assertIn("const isEmptyProfile = nextDigest === EMPTY_PROFILE_DIGEST", self.composer)
        self.assertIn("if (!isEmptyProfile)", self.composer)
        self.assertIn("const canReplacePreviewProfile = !isEmptyProfile", self.composer)
        self.assertIn("if (followsSelected && canReplacePreviewProfile)", self.composer)
        self.assertIn("restart the renderer with a null profile", self.composer)
        self.assertIn("resetChecker({preserveDocumentRevision: true});", self.composer)

    def test_mask_editor_uses_the_complete_canonical_profile_geometry(self) -> None:
        self.assertIn("stripCount !== 33", self.composer)
        self.assertIn("totalLeds !== 4554", self.composer)
        self.assertIn("unobserved_non_plant_strips", self.composer)
        self.assertIn("unobservedNonPlantStrips", self.composer)
        self.assertIn("33 × 138", self.template)

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

    def test_preview_defaults_to_canonical_output_with_an_explicit_optional_foreground(self) -> None:
        compositor = (ROOT / "web/static/js/composer_compositor.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("decodeInstallationProfile", compositor)
        self.assertIn("applyInstallationForeground", compositor)
        self.assertIn("installationProfileView", self.runtime)
        self.assertIn("installationForegroundEnabled: false", self.composer)
        self.assertNotIn("state.installationForeground = await draft.installationProfileView()", self.composer)
        self.assertIn("setInstallationForegroundEnabled", self.composer)
        self.assertIn("state.installationForegroundEnabled && state.installationForeground", self.composer)
        self.assertIn("applyInstallationForeground({", self.composer)
        self.assertIn("optional browser-presentation artifact", self.runtime)
        self.assertIn("installationProfileViewPromise = null", self.runtime)
        self.assertIn('id="installationForegroundToggle"', self.template)
        self.assertIn('role="switch"', self.template)
        self.assertIn("Simulate installed plant foreground", self.template)
        self.assertIn("Output-accurate preview", self.template)

    def test_foreground_switch_is_preview_only_and_has_a_nonblocking_fallback(self) -> None:
        start = self.composer.index("async function setInstallationForegroundEnabled")
        end = self.composer.index("\n    function showCatalogUnavailable", start)
        switch = self.composer[start:end]
        self.assertIn("await runtime.installationProfileView()", switch)
        self.assertIn("preview remains output-accurate", switch)
        self.assertNotIn("resetChecker", switch)
        self.assertNotIn("commitHistory", switch)
        self.assertNotIn("scheduleAutosave", switch)
        self.assertNotIn("queueLiveEdit", switch)
        self.assertIn("state.installationForegroundEnabled && state.installationForeground", self.composer)
        self.assertIn("frameCanvas(originalFrame)", self.composer)
        self.assertIn("frameCanvas(draftFrame)", self.composer)
        self.assertIn("mobile-dual-pane .preview-foreground-control", (ROOT / "web/static/css/composer.css").read_text(encoding="utf-8"))

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

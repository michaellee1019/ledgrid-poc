"""Static product-contract checks for the painter draft/live interaction model."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "web" / "templates" / "painter.html"
SCRIPT = ROOT / "web" / "static" / "js" / "painter.js"


class PainterDraftUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8")
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def method_body(self, name: str, next_name: str) -> str:
        return self.script.split(f"async {name}(", 1)[1].split(
            f"async {next_name}(", 1
        )[0]

    def test_initialization_loads_a_private_draft_without_pushing_a_frame(self):
        initialize = self.method_body("initialize", "responseJson")

        self.assertIn("fetch('/api/painter/masks'", initialize)
        self.assertNotIn("pushPreview", initialize)
        self.assertNotIn("/api/painter/frame", initialize)
        self.assertIn("The wall output was not changed", initialize)

    def test_live_output_requires_an_explicit_action_and_can_be_stopped(self):
        self.assertIn('id="outputStateBadge">Draft', self.html)
        self.assertIn('id="mirrorWallBtn"', self.html)
        self.assertIn('id="returnToDraftBtn"', self.html)
        self.assertIn("click', () => this.startMirroring()", self.script)
        self.assertIn("click', () => this.returnToDraft()", self.script)
        self.assertIn("fetch('/api/stop', {method: 'POST'}", self.script)

    def test_draft_edits_never_call_the_retired_mask_writer(self):
        schedule = self.script.split("schedulePreview(delay = 70) {", 1)[1].split(
            "async pushPreview(", 1
        )[0]

        self.assertIn("if (!this.mirrorActive)", schedule)
        self.assertNotIn("async save()", self.script)
        self.assertNotIn("saveMasksBtn", self.script)
        self.assertNotIn(
            "fetch('/api/painter/masks', {\n                    method: 'POST'",
            self.script,
        )
        self.assertIn('id="manageProfileBtn"', self.html)
        self.assertIn("Manage profile in Composer", self.html)

    def test_leave_cleanup_is_limited_to_an_active_mirror_session(self):
        cleanup = self.script.split("cleanupLiveOutputOnLeave() {", 1)[1].split(
            "async loadPresetCatalog(", 1
        )[0]

        self.assertIn("if (!this.liveSessionEntered || this.leaveCleanupSent)", cleanup)
        self.assertIn("navigator.sendBeacon('/api/stop')", cleanup)

    def test_surface_copy_explains_mask_and_installation_geometry(self):
        self.assertIn("32 × 138 plant-mask surface", self.html)
        self.assertIn("33 output columns", self.html)
        self.assertNotIn("wall mirrors this canvas exactly", self.html.lower())

    def test_frame_presets_load_only_into_the_private_draft(self):
        for marker in (
            'id="painterPresetSelect"',
            'id="loadPainterPresetBtn"',
            'id="painterPresetName"',
            'id="savePainterPresetBtn"',
        ):
            self.assertIn(marker, self.html)
        load_block = self.script.split("async loadSelectedPreset()", 1)[1].split(
            "async savePreset()", 1
        )[0]
        self.assertIn("/api/painter/presets/", load_block)
        self.assertIn("private draft", load_block)
        self.assertNotIn("/api/painter/frame", load_block)
        self.assertNotIn("startMirroring", load_block)
        self.assertIn("The wall was not changed", self.script)

    def test_frame_preset_copy_never_claims_managed_profile_authority(self):
        self.assertIn("Painter frame presets", self.html)
        self.assertIn(
            "They never publish or select a managed installation profile", self.html
        )
        self.assertIn("Saving never publishes or selects a managed profile", self.script)
        self.assertIn("selected managed profile", self.script)
        self.assertNotIn("Mask presets", self.html)
        self.assertNotIn("Mask presets", self.script)


if __name__ == "__main__":
    unittest.main()

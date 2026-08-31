"""UI ownership contracts for moving Composer overlays on the local preview."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class ComposerDirectManipulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web/static/css/composer_slice.css").read_text(encoding="utf-8")

    def test_preview_and_layer_rows_expose_one_local_selection_surface(self) -> None:
        self.assertIn('id="scenePreview"', self.html)
        self.assertIn('tabindex="0"', self.html)
        self.assertIn("selectedOverlaySlot", self.script)
        self.assertIn("row.dataset.overlaySlot = overlay.slot_id", self.script)
        self.assertIn("aria-selected", self.script)
        self.assertIn(".overlay.is-selected", self.css)

    def test_drag_uses_scaled_strip_major_and_led_zero_bottom_deltas(self) -> None:
        self.assertIn("rect.width / 33", self.script)
        self.assertIn("rect.height / 138", self.script)
        self.assertIn("const leds = -Math.round", self.script)
        self.assertIn("pointerdown", self.script)
        self.assertIn("pointercancel", self.script)
        self.assertIn("touch-action: none", self.css)

    def test_keyboard_nudge_and_canonical_numeric_bounds_share_one_placement_path(self) -> None:
        self.assertIn("event.shiftKey ? 5 : 1", self.script)
        for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
            self.assertIn(key, self.script)
        self.assertIn("canonicalTranslation", self.script)
        self.assertIn("clip_policy: 'clip_to_wall'", self.script)
        self.assertIn("min=\"${TRANSLATION_MIN}\"", self.script)

    def test_drag_keeps_object_identity_and_newest_preview_write_wins(self) -> None:
        self.assertIn("state.overlays.includes(drag.overlay)", self.script)
        self.assertIn("selectedOverlay() !== drag.overlay", self.script)
        self.assertIn("state.autosaveChain", self.script)
        self.assertIn("generation !== state.previewGeneration", self.script)
        self.assertIn("requestAnimationFrame", self.script)

    def test_direct_manipulation_does_not_touch_live_controls(self) -> None:
        start = self.script.index("function beginPreviewDrag(")
        end = self.script.index("function render()", start)
        gesture_code = self.script[start:end]
        for forbidden in ("goLive(", "stopScene(", "activate", "check`", "fetch(`${api}/stop"):
            self.assertNotIn(forbidden, gesture_code)


if __name__ == "__main__":
    unittest.main()

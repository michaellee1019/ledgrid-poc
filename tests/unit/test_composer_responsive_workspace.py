"""Responsive and compact-control contracts for the Composer panel grid."""

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]


class ComposerResponsiveWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.layout = (ROOT / "web/static/js/composer_palette_layout.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web/static/css/composer_slice.css").read_text(encoding="utf-8")

    def test_phone_uses_one_bounded_column_with_usable_targets(self) -> None:
        self.assertIn("@media (max-width: 760px)", self.css)
        for token in (
            ".desktop-workspace { grid-template-columns: minmax(0, 1fr); height: auto; overflow: visible; }",
            ".panel-toggle { min-width: 44px; min-height: 44px;",
            ".operations-summary #liveAction { min-height: 44px; }",
            ".button, input:not([type=\"checkbox\"]) { min-height: 44px; }",
            "select { height: 44px; min-height: 44px;",
        ):
            self.assertIn(token, self.css)
        self.assertIn("html, body { max-width: 100%; overflow-x: hidden; }", self.css)

    def test_operations_stop_precedes_collapsible_controls(self) -> None:
        for selector in (
            'id="connectionState"', 'id="liveAction"', 'id="secondaryOperations"',
            'id="checkScene"', 'class="diagnostics"',
        ):
            self.assertEqual(self.html.count(selector), 1, selector)
        self.assertLess(self.html.index('id="liveAction"'), self.html.index('id="secondaryOperations"'))
        self.assertLess(self.html.index('id="secondaryOperations"'), self.html.index('id="checkScene"'))
        self.assertNotIn('id="secondaryOperations" class="secondary-operations" open', self.html)
        self.assertNotIn("syncSecondaryOperations", self.script)
        self.assertNotIn("matchMedia('(max-width: 760px)')", self.script)

    def test_collapse_state_survives_breakpoint_changes_without_scene_writes(self) -> None:
        self.assertNotIn("matchMedia", self.layout)
        self.assertIn("localStorage.setItem(preferenceKey", self.layout)
        self.assertIn("expanded = {...expanded, [id]: !expanded[id]}", self.layout)
        for forbidden in ("fetch(", "/api/", "sceneFromControls", "submit("):
            self.assertNotIn(forbidden, self.layout)

    def test_preview_and_gallery_are_independent_collapsible_panels(self) -> None:
        self.assertIn('class="gallery" aria-labelledby="gallery-title" data-panel-id="gallery"', self.html)
        self.assertIn('class="preview-pane" aria-labelledby="preview-title" data-panel-id="preview"', self.html)
        self.assertIn("['gallery', document.querySelector('.gallery')]", self.layout)
        self.assertIn("['preview', document.querySelector('.preview-pane')]", self.layout)
        self.assertIn("['playlists', document.querySelector('.playlist-pane')]", self.layout)
        self.assertIn("current?.run_id !== state.playlist.runId", self.script)
        self.assertIn("status.run_id === state.playlist.runId", self.script)
        self.assertIn(".preview-stage { display: flex; align-items: center; justify-content: center; min-height: min(70vh, 640px)", self.css)
        self.assertIn('width="33" height="138"', self.html)


if __name__ == "__main__":
    unittest.main()

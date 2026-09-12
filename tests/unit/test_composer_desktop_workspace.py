"""Static acceptance checks for the responsive compact Composer panels."""

from pathlib import Path
import unittest


class ComposerDesktopWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = Path("web/templates/composer.html").read_text(encoding="utf-8")
        self.script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.layout = Path("web/static/js/composer_palette_layout.js").read_text(encoding="utf-8")
        self.css = Path("web/static/css/composer_slice.css").read_text(encoding="utf-8")

    def test_workspace_is_a_wrapping_panel_grid(self) -> None:
        self.assertIn('data-layout="responsive-panels"', self.html)
        self.assertIn(
            "grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr))",
            self.css,
        )
        self.assertIn(".inspector-dock, .control-workspace, .inspectors { display: contents; }", self.css)
        self.assertNotIn("dock-resizer", self.html)
        self.assertNotIn("position: sticky", self.css)
        self.assertNotIn("--inspector-width", self.css)
        self.assertLess(self.html.index("Global scene"), self.html.index("Background"))

    def test_every_user_panel_has_a_stable_collapsed_preference(self) -> None:
        for panel_id in (
            "scenes", "gallery", "preview", "global-scene", "background",
            "animation", "widgets", "plants", "look", "snake", "canopy",
            "reef", "arcade-trio", "operations",
        ):
            self.assertIn(f"['{panel_id}',", self.layout)
        self.assertIn("ledgrid.composer.compact-panels.v2", self.layout)
        self.assertIn("value.version !== 2", self.layout)
        self.assertIn("[id, false]", self.layout)
        self.assertIn("value.expanded[id] === true", self.layout)
        self.assertIn("localStorage.removeItem(preferenceKey)", self.layout)
        self.assertIn("ledgrid.composer.constrained-docks.v1", self.layout)

    def test_panel_headers_are_keyboard_and_screen_reader_operable(self) -> None:
        for token in (
            "toggle.type = 'button'",
            "aria-controls",
            "aria-expanded",
            "'Collapse' : 'Expand'",
            "collapsible-panel.is-collapsed > .panel-content { display: none",
        ):
            self.assertIn(token, self.layout if token != "collapsible-panel.is-collapsed > .panel-content { display: none" else self.css)

    def test_layout_preferences_cannot_publish_or_control_the_wall(self) -> None:
        for forbidden in ("fetch(", "/api/", "liveAction", "dispatchEvent", "submit("):
            self.assertNotIn(forbidden, self.layout)
        self.assertIn("owns no Scene data", self.layout)

    def test_operations_header_keeps_stop_and_short_status_visible(self) -> None:
        heading_start = self.html.index('<div class="pane-heading"><div><p class="eyebrow">Operations</p>')
        heading_end = self.html.index("</div>\n      <details id=\"secondaryOperations\"", heading_start)
        heading = self.html[heading_start:heading_end]
        for element_id in ("saveState", "connectionState", "liveAction", "wallActivationFailure"):
            self.assertIn(f'id="{element_id}"', heading)
        self.assertNotIn('class="button primary wide"', heading)
        self.assertIn(".operations-pane { position: static", self.css)

    def test_exact_identities_live_only_in_diagnostics(self) -> None:
        diagnostics = self.html[self.html.index('<details class="diagnostics">'):]
        for element_id in ("sceneIdentity", "previewIdentity", "desiredIdentity", "observedIdentity", "sceneRevision"):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1)
            self.assertIn(f'id="{element_id}"', diagnostics)
        preview_heading = self.html[self.html.index('id="preview-title"') - 100:self.html.index('id="scenePreview"')]
        self.assertNotIn("previewIdentity", preview_heading)

    def test_existing_scene_and_preview_contract_remains(self) -> None:
        for element_id in ("sceneSpeed", "targetFps", "liveAction", "scenePreview"):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1)
        self.assertIn('width="33" height="138"', self.html)
        self.assertIn("pace: number('#sceneSpeed')", self.script)
        self.assertIn("$('#sceneSpeed').addEventListener('input', edit)", self.script)
        self.assertIn("aspect-ratio: 33 / 138", self.css)


if __name__ == "__main__":
    unittest.main()

"""Static contract coverage for the Scene v2 desktop Composer workspace."""

from pathlib import Path
import re
import unittest


class ComposerDesktopWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = Path("web/templates/composer.html").read_text(encoding="utf-8")
        self.script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.css = Path("web/static/css/composer_slice.css").read_text(encoding="utf-8")

    def test_desktop_has_persistent_columns_and_no_retired_workspace_controls(self) -> None:
        for token in ("library-pane", "preview-pane", "inspectors", "operations-pane", "Background", "Animation", "Widgets", "Plants", "Look"):
            self.assertIn(token, self.html)
        self.assertIn("grid-template-columns: minmax(175px, 220px) minmax(120px, 150px) minmax(0, 1fr) minmax(245px, 290px)", self.css)
        self.assertIn("overflow-x: hidden", self.css)
        for retired in ("composer-heading", "composer-nav", "Timeline", "FPS", "Leave Live", "Editing locally", "Tools", "Layers", "Wall"):
            self.assertNotIn(retired, self.html)
        self.assertNotIn('id="composerShell"', self.html)
        self.assertNotIn("composer_shell.js", self.html)

    def test_controller_palettes_collapse_without_sticky_overlap(self) -> None:
        layout = Path("web/static/js/composer_palette_layout.js").read_text(encoding="utf-8")
        self.assertIn("makeButton('Collapse palette'", layout)
        self.assertIn("aria-label', `${layout.collapsed[id] ? 'Expand' : 'Collapse'}", layout)
        self.assertIn(".palette-shell.is-collapsed", self.css)
        self.assertIn(".palette-shell .pane-heading { position: static", self.css)
        self.assertNotIn(".palette-shell .pane-heading { position: sticky", self.css)

    def test_scene_v2_edits_publish_and_dialogs_have_keyboard_contracts(self) -> None:
        self.assertIn("'/scene'", self.script)
        self.assertIn("'/built-ins/open'", self.script)
        self.assertIn("'/api/v1/scene/checks'", self.script)
        self.assertIn("'Idempotency-Key': newUuid()", self.script)
        self.assertIn("await waitForExactActivation", self.script)
        self.assertIn("`${api}/undo-ack`", self.script)
        self.assertIn("event.key === 'Escape'", self.script)
        self.assertIn("event.key !== 'Tab'", self.script)
        self.assertIn("prior?.focus()", self.script)
        self.assertIn("Object.values(body.widget_placements || {})", self.script)
        self.assertIn("previewScheduler.submitAuthored", self.script)
        self.assertIn("setInterval(() => { if (!document.hidden) refreshStatus(); }, 2500)", self.script)
        self.assertIn("state.status?.running && state.status?.current && !state.wall.dirty", self.script)
        self.assertIn("await refreshWallStatus({adopt: true})", self.script)
        self.assertIn("composerSceneFromWall", self.script)
        self.assertIn("state.wall.adoptedVibeId", self.script)
        self.assertIn("const lookUnchanged", self.script)
        self.assertIn("if (componentId === 'clock_overlay') delete managedParameters.color;", self.script)
        self.assertIn("state.wall.observation?.installation_profile_digest", self.script)
        self.assertIn("state.revision = Math.max(state.revision || 0, status.revision || 0);", self.script)
        self.assertIn("controller_state_revision", self.script)

    def test_current_scene_edits_preserve_unrepresented_components_and_show_dirty_state(self) -> None:
        for token in ("const clockIndexes", "clockIndexes.length === 1", "choice !== next.animation.component_id", "next.animation.parameters = {...next.animation.parameters", "state.dirty = true", "Unsaved changes"):
            self.assertIn(token, self.script)
        self.assertIn("grid-template-columns: repeat(5, minmax(170px, 1fr))", self.css)
        self.assertIn("strip_translation: clock.placement.strip_translation ?? 0", self.script)

    def test_component_instruments_are_nested_before_operations_claims_the_fourth_column(self) -> None:
        self.assertIn("function nestComponentControls()", self.script)
        self.assertIn("animationInspector.append(region);", self.script)
        self.assertIn("region.dataset.animationComponents = componentIds;", self.script)
        self.assertIn(".control-workspace > .inspector[data-animation-components]", self.script)
        self.assertIn("document.querySelectorAll('[data-animation-components]')", self.script)

        workspace = re.search(r'<div class="desktop-workspace">(?P<body>.*?)</div>\s*</main>', self.html, re.DOTALL)
        self.assertIsNotNone(workspace)
        body = workspace.group("body")
        self.assertLess(body.index('class="library-pane"'), body.index('class="preview-pane"'))
        self.assertLess(body.index('class="preview-pane"'), body.index('class="inspectors"'))
        self.assertLess(body.index('class="inspectors"'), body.index('class="operations-pane"'))
        self.assertEqual(body.count('class="operations-pane"'), 1)

    def test_rendered_layout_probe_covers_both_desktop_widths_and_selected_component_path(self) -> None:
        probe = Path("tools/browser_qualification/composer_hierarchy_probe.mjs").read_text(encoding="utf-8")
        self.assertIn("for (const width of [1280, 1440])", probe)
        self.assertIn("assert.deepEqual(layout.children, ['library-pane', 'preview-pane', 'inspectors', 'operations-pane']);", probe)
        self.assertIn("Operations was displaced", probe)
        self.assertIn("await page.selectOption('#animationChoice', 'snake');", probe)


if __name__ == "__main__":
    unittest.main()

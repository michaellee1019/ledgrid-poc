"""Static acceptance checks for the compact docked Composer studio."""

from pathlib import Path
import re
import unittest


class ComposerDesktopWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = Path("web/templates/composer.html").read_text(encoding="utf-8")
        self.script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.layout = Path("web/static/js/composer_palette_layout.js").read_text(encoding="utf-8")
        self.css = Path("web/static/css/composer_slice.css").read_text(encoding="utf-8")

    def test_desktop_is_a_fixed_three_dock_studio(self) -> None:
        for token in ("library-pane", "preview-pane", "inspector-dock", "operations-pane",
                      "Background", "Animation", "Widgets", "Plants", "Look"):
            self.assertIn(token, self.html)
        self.assertIn(
            "grid-template-columns: clamp(176px, var(--library-width), 360px) .75rem minmax(0, 1fr) .75rem clamp(252px, var(--inspector-width), 480px)",
            self.css,
        )
        self.assertIn(".preview-pane { display: flex; flex-direction: column", self.css)
        self.assertIn(".operations-pane { position: sticky; top: 0", self.css)
        self.assertIn("overflow-x: hidden", self.css)
        self.assertNotIn("palette-board", self.css)
        self.assertNotIn("palette-shell", self.css)
        self.assertNotIn("palette-stack", self.css)

    def test_docks_have_a_single_intentional_vertical_scroll_owner(self) -> None:
        self.assertIn(".library-pane { min-height: 0; padding: .6rem; overflow-y: auto; overflow-x: hidden", self.css)
        self.assertIn(".inspector-dock { display: flex; flex-direction: column; min-height: 0; padding: .45rem; overflow-y: auto; overflow-x: hidden", self.css)
        self.assertIn(".control-workspace { display: grid; align-content: start", self.css)
        self.assertNotIn("overflow: auto", self.css)

    def test_live_operations_lead_the_desktop_scroll_dock(self) -> None:
        # The controls precede operations in source so phone display: contents
        # can retain its operator-first order.  Desktop flex order must still
        # put identity and Stop at the top of the dock before its long control
        # tree, rather than relying on sticky after that tree has scrolled.
        self.assertIn(".inspector-dock { display: flex; flex-direction: column", self.css)
        self.assertIn(".operations-pane { position: sticky; top: 0; z-index: 1; order: -1", self.css)
        self.assertIn(".operations-pane { order: 1; }", self.css)

    def test_layout_bootstrap_keeps_preferences_out_of_scene_state(self) -> None:
        self.assertIn("desktop-palette-layout.v3", self.layout)
        self.assertIn("localStorage.removeItem", self.layout)
        self.assertIn("data-layout', 'docked-studio", self.layout)
        self.assertIn("ledgrid.composer.constrained-docks.v1", self.layout)
        self.assertIn("version === 1", self.layout)
        self.assertIn("JSON.parse", self.layout)
        self.assertIn("catch (_) { return defaults; }", self.layout)
        self.assertNotIn("/api/", self.layout)
        self.assertNotIn("fetch(", self.layout)
        for retired in ("dragstart", "moveToOwnColumn", "palette-resize-handle", "palette-header-controls"):
            self.assertNotIn(retired, self.layout)

    def test_layout_has_bounded_pointer_and_keyboard_resizers(self) -> None:
        self.assertEqual(self.html.count('data-dock-resizer='), 2)
        self.assertEqual(self.html.count('role="separator"'), 2)
        for token in (
            "bounds = Object.freeze({library: [176, 360], inspector: [252, 480]})",
            "pointerdown",
            "ArrowLeft",
            "ArrowRight",
            "aria-valuemin",
            "aria-valuemax",
            "aria-valuenow",
            "resetComposerLayout",
        ):
            self.assertIn(token, self.layout if token != "resetComposerLayout" else self.html)

    def test_named_inspectors_collapse_accessibly(self) -> None:
        for name in ("Background", "Animation", "Widgets", "Plants", "Look"):
            self.assertIn(name, self.layout)
        self.assertIn("aria-expanded", self.layout)
        self.assertIn("aria-controls", self.layout)
        self.assertIn("collapsible-inspector.is-collapsed > .inspector-content { display: none", self.css)
        self.assertIn("inspector-content", self.layout)
        self.assertIn("inspector-toggle", self.layout)

    def test_dense_controls_preserve_the_existing_controller_contract(self) -> None:
        self.assertEqual(self.html.count('id="sceneSpeed"'), 1)
        self.assertEqual(self.html.count('id="targetFps"'), 1)
        self.assertEqual(self.html.count('id="liveAction"'), 1)
        self.assertIn('class="global-scene-controls inspector"', self.html)
        self.assertIn(".field-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 9.5rem), 1fr))", self.css)
        self.assertIn("font: 13px/1.35", self.css)
        self.assertIn("min-height: 1.9rem", self.css)
        self.assertIn("pace: number('#sceneSpeed')", self.script)
        self.assertIn("$('#sceneSpeed').addEventListener('input', edit)", self.script)
        self.assertNotIn("function queueOperatorSpeed", self.script)

    def test_workspace_dom_keeps_preview_between_library_and_controls(self) -> None:
        workspace = re.search(r'<div class="desktop-workspace"[^>]*>(?P<body>.*?)</div>\s*</main>', self.html, re.DOTALL)
        self.assertIsNotNone(workspace)
        body = workspace.group("body")
        self.assertLess(body.index('class="library-pane"'), body.index('class="preview-pane"'))
        self.assertLess(body.index('class="preview-pane"'), body.index('class="inspector-dock"'))
        self.assertEqual(body.count('class="operations-pane"'), 1)

    def test_gallery_thumbnails_keep_the_physical_wall_aspect_at_each_breakpoint(self) -> None:
        self.assertIn(".gallery-thumb { inline-size: 1.2rem; block-size: auto; aspect-ratio: 33 / 138", self.css)
        self.assertIn(".gallery-thumb { inline-size: 1.5rem; block-size: auto; aspect-ratio: 33 / 138", self.css)
        self.assertIn(".gallery-select { grid-template-columns: 1.5rem minmax(0, 1fr); min-height: 6.9rem", self.css)


if __name__ == "__main__":
    unittest.main()

"""Static contract coverage for the Scene v2 desktop Composer workspace."""

from pathlib import Path
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

    def test_scene_v2_edits_publish_and_dialogs_have_keyboard_contracts(self) -> None:
        self.assertIn("'/scene'", self.script)
        self.assertIn("'/built-ins/open'", self.script)
        self.assertIn("`${api}/go-live`", self.script)
        self.assertIn("`${api}/undo-ack`", self.script)
        self.assertIn("event.key === 'Escape'", self.script)
        self.assertIn("event.key !== 'Tab'", self.script)
        self.assertIn("prior?.focus()", self.script)
        self.assertIn("Object.values(body.widget_placements || {})", self.script)
        self.assertIn("previewScheduler.submitAuthored", self.script)
        self.assertIn("setInterval(() => { if (!document.hidden) refreshStatus(); }, 2500)", self.script)
        self.assertIn("state.status?.running && state.status?.armed && state.status?.current", self.script)
        self.assertIn("if (!state.status?.current && state.scene) await submit(structuredClone(state.scene));", self.script)
        self.assertIn("state.revision = Math.max(state.revision || 0, status.revision || 0);", self.script)
        self.assertIn("const newerRemoteRevision", self.script)

    def test_current_scene_edits_preserve_unrepresented_components_and_show_dirty_state(self) -> None:
        for token in ("const clockIndexes", "clockIndexes.length === 1", "choice !== next.animation.component_id", "next.animation.parameters = {...next.animation.parameters", "state.dirty = true", "Unsaved changes"):
            self.assertIn(token, self.script)
        self.assertIn("grid-template-columns: repeat(5, minmax(170px, 1fr))", self.css)
        self.assertIn("strip_translation: clock.placement.strip_translation ?? 0", self.script)


if __name__ == "__main__":
    unittest.main()

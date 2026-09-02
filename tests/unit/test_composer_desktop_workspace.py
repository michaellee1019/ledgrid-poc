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
        for retired in ("composer-heading", "composer-nav", "Timeline", "Leave Live", "Editing locally", "Tools", "Layers", "Wall"):
            self.assertNotIn(retired, self.html)
        self.assertNotIn('id="composerShell"', self.html)
        self.assertNotIn("composer_shell.js", self.html)

    def test_controller_palettes_collapse_without_sticky_overlap(self) -> None:
        layout = Path("web/static/js/composer_palette_layout.js").read_text(encoding="utf-8")
        for palette in ("library", "installed-final", "operations"):
            self.assertIn(f"['{palette}'", layout)
        self.assertIn("controlsWorkspace.querySelectorAll('.inspector')", layout)
        self.assertIn("ledgrid.composer.desktop-palette-layout.v3", layout)
        self.assertNotIn("palette-arrange-menu", layout)
        self.assertNotIn("summary.textContent = 'Arrange'", layout)
        self.assertIn("makeButton(`Collapse ${title(id)} palette`, '⌄'", layout)
        self.assertIn("makeButton(`Drag ${title(id)} palette`, '⠿'", layout)
        self.assertIn("collapse.setAttribute('aria-expanded', String(!collapsed))", layout)
        for retired in ("Move left", "Move right", "Stack left", "Unstack", "Shorter", "Taller", "Hide palette"):
            self.assertNotIn(retired, layout)
        self.assertIn("moveToOwnColumn", layout)
        self.assertIn(".palette-shell.is-collapsed", self.css)
        self.assertIn(".desktop-workspace.palette-board", self.css)
        self.assertIn("touch-action: pan-y", self.css)
        self.assertIn(".palette-shell .pane-heading { position: static", self.css)
        self.assertNotIn(".palette-shell .pane-heading { position: sticky", self.css)
        self.assertIn("repeat(auto-fit, minmax(min(100%, 12rem), 1fr))", self.css)
        self.assertIn(".primary-control-grid, .advanced-control-grid { grid-template-columns: 1fr; }", self.css)

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
        self.assertIn("placementWarning(body.widget_placements || {})", self.script)
        self.assertIn("previewScheduler.submitAuthored", self.script)
        self.assertIn("setInterval(() => { if (!document.hidden) refreshStatus(); }, 2500)", self.script)
        self.assertIn("try { await guardedWallActivation(entry.body.scene, true); }", self.script)
        self.assertIn("async function stopOutput()", self.script)
        self.assertNotIn("`${api}/go-live`", self.script)
        self.assertIn("await refreshWallStatus({preserveAuthored: true})", self.script)
        self.assertIn("composerSceneFromWall", self.script)
        self.assertIn("state.wall.adoptedVibeId", self.script)
        self.assertIn("const lookUnchanged", self.script)
        self.assertIn("if (componentId === 'clock_overlay') delete managedParameters.color;", self.script)
        self.assertIn("state.wall.observation?.installation_profile_digest", self.script)
        self.assertIn("state.revision = Math.max(state.revision || 0, status.revision || 0);", self.script)
        self.assertIn("controller_state_revision", self.script)

    def test_global_speed_is_a_single_persistent_scene_pace_control(self) -> None:
        self.assertEqual(self.html.count('id="sceneSpeed"'), 1)
        self.assertNotIn('id="wallPace"', self.html)
        self.assertIn('class="global-scene-controls inspector"', self.html)
        self.assertIn('Speed <span>Applies immediately to the entire scene', self.html)
        self.assertIn('type="range" min=".1" max="2" step=".05" value=".7"', self.html)
        self.assertIn('id="resetSceneSpeed"', self.html)
        self.assertIn("pace: number('#sceneSpeed')", self.script)
        self.assertIn("syncSceneSpeed(scene.look?.pace ?? DEFAULT_SCENE_PACE)", self.script)
        self.assertIn("$('#sceneSpeed').addEventListener('input'", self.script)
        self.assertIn("function queueOperatorSpeed", self.script)
        self.assertIn("'/api/config/animation-speed'", self.script)
        self.assertIn("$('#resetSceneSpeed').addEventListener('click'", self.script)
        self.assertIn(".global-scene-controls { order: -1", self.css)
        layout = Path("web/static/js/composer_palette_layout.js").read_text(encoding="utf-8")
        self.assertIn("['global-scene-controls', 'background', 'look']", layout)
        self.assertIn("controlsWorkspace.querySelectorAll('.inspector')", layout)
        self.assertNotIn("function setVisibility(id, visible)", layout)
        self.assertIn(".global-scene-controls.palette-shell .pane-heading { flex-wrap: wrap; }", self.css)
        self.assertIn(".global-scene-controls.palette-shell .pane-heading > :first-child { flex: 1 0 100%; }", self.css)
        probe = Path("tools/browser_qualification/composer_hierarchy_probe.mjs").read_text(encoding="utf-8")
        self.assertIn("'global-scene-controls-title'", probe)
        self.assertNotIn("openPaletteChooser", probe)
        self.assertNotIn("openPaletteArrangeMenu", probe)
        self.assertIn('[data-palette-action="collapse"]', probe)
        self.assertIn('[data-palette-action="drag"]', probe)
        self.assertIn("Direct drag handle is not visible", probe)
        self.assertIn("Global scene title controls overflow", probe)

    def test_motion_keeps_authored_target_separate_from_measured_cadence(self) -> None:
        self.assertEqual(self.html.count('id="targetFps"'), 1)
        self.assertEqual(self.html.count('id="actualFps"'), 1)
        self.assertIn('type="range" min="1" max="200" step="1"', self.html)
        self.assertIn('Host presentation cadence. This does not change Scene pace', self.html)
        self.assertIn("'/api/config/target-fps'", self.script)
        self.assertIn("function queueTargetFps", self.script)
        self.assertIn("function renderActualFps", self.script)
        self.assertIn("if (state.frameRate.queued == null) syncTargetFps(applied)", self.script)
        self.assertIn("const DEFAULT_TARGET_FPS = 200", self.script)
        self.assertIn("value=\"200\"", self.html)
        self.assertIn("actual_fps", self.script)
        self.assertIn("target_fps: boundedTargetFps($('#targetFps').value)", self.script)
        self.assertNotIn('id="plantsStatus"', self.html)

    def test_semantic_controls_cover_every_scene_inspector_and_keep_final_optics_compact(self) -> None:
        self.assertIn("document.querySelectorAll('.inspectors .inspector')", self.script)
        self.assertIn("function decorateNumeric", self.script)
        self.assertIn("function decorateSwitch", self.script)
        self.assertIn("function decorateChoices", self.script)
        self.assertIn("function controlLabelText", self.script)
        self.assertIn("const compactPlantOptic", self.script)
        self.assertIn("control.setAttribute('aria-hidden', 'true')", self.script)
        self.assertNotIn("label.firstChild.textContent.trim()", self.script)
        self.assertIn('id="backgroundGain" type="number" min="0" max="1"', self.html)
        self.assertIn('class="final-optic-row"', self.html)
        self.assertEqual(self.html.count('strength <input'), 0)
        self.assertIn(".final-optic-row { display: grid", self.css)

    def test_current_scene_edits_preserve_unrepresented_components_and_show_dirty_state(self) -> None:
        for token in ("const clockIndexes", "clockIndexes.length === 1", "choice !== next.animation.component_id", "next.animation.parameters = {...next.animation.parameters", "state.dirty = true", "Unsaved changes"):
            self.assertIn(token, self.script)
        self.assertIn("grid-template-columns: repeat(5, minmax(170px, 1fr))", self.css)
        self.assertIn("strip_translation: Math.trunc(number('#clockAcross'))", self.script)
        self.assertIn("led_translation: Math.trunc(number('#clockOffset'))", self.script)

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
        self.assertIn("'Direct drag handle is not visible'", probe)
        self.assertIn("dragTo(page.locator('.operations-pane'))", probe)
        self.assertIn("await page.selectOption('#animationChoice', targetAnimation);", probe)
        self.assertIn("'A normal edit did not publish immediately'", probe)
        self.assertIn("'Speed did not use the immediate controller path'", probe)


if __name__ == "__main__":
    unittest.main()

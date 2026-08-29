"""Product-level acceptance contracts for the current Studio UX."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CurrentUxContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (ROOT / "web/templates/base.html").read_text(encoding="utf-8")
        cls.dashboard = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")
        cls.dashboard_js = (ROOT / "web/static/js/dashboard.js").read_text(encoding="utf-8")
        cls.dashboard_css = (ROOT / "web/static/css/dashboard.css").read_text(encoding="utf-8")
        cls.control = (ROOT / "web/templates/control.html").read_text(encoding="utf-8")
        cls.painter = (ROOT / "web/templates/painter.html").read_text(encoding="utf-8")
        cls.emoji = (ROOT / "web/templates/emoji_arranger.html").read_text(encoding="utf-8")

    def test_shared_navigation_is_task_oriented_and_responsive(self):
        self.assertIn('id="studioNavigation"', self.base)
        self.assertIn('aria-label="Toggle navigation"', self.base)
        for label in ("Studio", "Painter", "Emoji", "Advanced"):
            self.assertIn(f"> {label}\n", self.base)

    def test_every_workspace_inherits_one_server_reconciled_live_bar(self):
        self.assertIn('id="globalLiveLabel"', self.base)
        self.assertIn('id="globalStopLiveButton"', self.base)
        self.assertIn("applyGlobalLiveStatus(status)", self.base)
        self.assertIn("ledgrid:live-status", self.base)
        self.assertNotIn('class="card now-playing-bar', self.dashboard)
        self.assertNotIn('class="card live-strip', self.control)

    def test_dashboard_has_four_task_areas_and_progressive_library_disclosure(self):
        for area in ("library", "now-playing", "compose", "system"):
            self.assertIn(f'id="dashboard-{area}"', self.dashboard)
        self.assertIn('id="librarySearch"', self.dashboard)
        self.assertIn('id="libraryShowMore"', self.dashboard)
        self.assertIn("const LIBRARY_BATCH_SIZE", self.dashboard_js)
        self.assertIn("item.hidden = true", self.dashboard_js)

    def test_library_supports_durable_favorites_and_recent_history(self):
        for marker in ('data-library-saved="favorites"', 'data-library-saved="recent"', 'data-library-favorite'):
            self.assertIn(marker, self.dashboard)
        self.assertIn("ledgrid.library.favorites.v1", self.dashboard_js)
        self.assertIn("ledgrid.library.recents.v1", self.dashboard_js)
        self.assertIn("LIBRARY_RECENT_LIMIT = 12", self.dashboard_js)
        self.assertIn("recordLibraryRecent", self.dashboard_js)

    def test_dashboard_selection_and_adjustment_are_separate_from_live_actions(self):
        adjust = self.dashboard_js.split("function openAnimationControls(", 1)[1].split(
            "// Renderer control functions", 1
        )[0]
        self.assertNotIn("startAnimation", adjust)
        self.assertIn("showDashboardArea('now-playing'", adjust)
        self.assertIn("Check &amp; activate", self.dashboard)
        self.assertNotIn('onclick="takeAnimationLive', self.dashboard)
        self.assertNotIn('onclick="takePresetLive', self.dashboard)
        self.assertIn('id="activateSceneInComposerLink" href="/composer"', self.dashboard)
        self.assertNotIn('onclick="startEditedScene()', self.dashboard)
        self.assertNotIn("function startEditedScene", self.dashboard_js)

    def test_advanced_selection_is_private_until_take_live(self):
        inspect = self.control.split("async function inspectAnimation(", 1)[1].split(
            "function renderParameterControls(", 1
        )[0]
        self.assertNotIn("startAnimation", inspect)
        self.assertIn('id="takeLiveBtn" href="/composer"', self.control)
        self.assertNotIn('onclick="takeSelectedLive()', self.control)
        self.assertIn("Selection is private and never changes the wall", self.control)
        self.assertIn("Preset management lives in Studio", self.control)
        self.assertIn("Manage presets in Studio", self.control)
        self.assertNotIn('id="presetSelect"', self.control)

    def test_advanced_parameters_preserve_precision_and_hide_internal_data(self):
        self.assertIn("paramInfo.step !== undefined", self.control)
        self.assertIn("decimalPlaces(paramInfo.min)", self.control)
        self.assertIn("normalized.endsWith('_path')", self.control)
        self.assertIn("complexValue", self.control)
        self.assertIn('aria-label="${escapeHtml(prettyName)} slider"', self.control)
        self.assertIn('aria-label="${escapeHtml(prettyName)} exact value"', self.control)
        self.assertNotIn("[object Object]", self.control)

    def test_live_action_language_is_consistent(self):
        self.assertIn("Mirror to wall", self.painter)
        self.assertIn("Stop / Return to draft", self.painter)
        self.assertIn("Stop / Return to draft", self.emoji)
        self.assertIn('id="activateInComposerLink" href="/composer"', self.emoji)
        self.assertIn("activationRequiresComposer: true", self.emoji)
        self.assertNotIn("/api/start/", self.emoji)
        self.assertNotIn("async function takeLive", self.emoji)
        self.assertNotIn("Start live", self.dashboard)
        self.assertNotIn("Take selected look live", self.dashboard)
        self.assertNotIn("function startAnimation", self.base)
        self.assertNotIn("function startAnimation", self.dashboard_js)
        self.assertNotIn("function takeSelectedLive", self.control)
        self.assertNotIn("until Take live", self.dashboard)

    def test_studio_next_routes_execution_through_composer(self):
        studio = (ROOT / "web/templates/studio_next.html").read_text(encoding="utf-8")
        studio_js = (ROOT / "web/static/js/studio_next.js").read_text(encoding="utf-8")
        self.assertIn('id="activateLookInComposer" href="/composer"', studio)
        self.assertIn('id="activateSceneInComposer" href="/composer"', studio)
        self.assertNotIn("/api/v1/studio-next/take-look", studio_js)
        self.assertNotIn("/api/v1/studio-next/take-scene", studio_js)

    def test_global_live_controls_use_one_clear_vocabulary(self):
        for label in ("Animation speed", "Speed multiplier", "Wall mood", "Plant behavior"):
            self.assertIn(label, self.dashboard)
        for legacy_label in ("Global tempo", "Global live vibe", "Global live plant behavior"):
            self.assertNotIn(legacy_label, self.dashboard)
        self.assertIn("Slow 0.65×", self.dashboard)
        self.assertIn("Normal 1×", self.dashboard)
        self.assertIn("Very fast 2.25×", self.dashboard)

    def test_preset_management_has_one_owner_per_preset_type(self):
        self.assertEqual(self.dashboard.count("Save as preset"), 1)
        self.assertIn('id="controlPresetSelect"', self.dashboard)
        self.assertNotIn('id="previewSavePresetButton"', self.dashboard)
        self.assertNotIn("savePreviewPreset", self.dashboard_js)
        self.assertNotIn("loadAnimationPresets", self.control)

    def test_scene_preset_loading_is_draft_only(self):
        loader = self.dashboard_js.split("async function loadScenePresetDraft()", 1)[1].split(
            "\n    }", 1
        )[0]
        self.assertIn("loadSceneIntoEditor", loader)
        self.assertNotIn("/apply", loader)
        self.assertNotIn("method: 'POST'", loader)
        self.assertIn("Load draft", self.dashboard)

    def test_user_facing_text_is_not_ellipsized_or_line_clamped(self):
        self.assertNotIn("text-overflow: ellipsis", self.dashboard_css)
        self.assertNotIn("-webkit-line-clamp", self.dashboard_css)
        self.assertNotIn(".dashboard-task-tab small { display: none", self.dashboard_css)
        self.assertNotIn("slice(0, 12)", self.dashboard_js)
        self.assertNotIn("slice(0, 15)", self.dashboard_js)

    def test_numeric_and_plant_controls_show_labels_and_values(self):
        self.assertIn('<label class="parameter-label" for=', self.dashboard_js)
        self.assertIn('aria-label="${escapeHtml(prettyName)} slider"', self.dashboard_js)
        self.assertIn('aria-label="${escapeHtml(prettyName)} exact value"', self.dashboard_js)
        self.assertIn("plantModifier-${id}-value", self.dashboard_js)
        self.assertIn("This animation does not support plant behavior controls.", self.dashboard_js)
        self.assertIn('id="sceneOverlayOpacityValue"', self.dashboard)
        self.assertIn('class="form-label small fw-semibold mb-1" for="painterPresetSelect"', self.painter)
        self.assertIn('class="form-label small fw-semibold mb-1" for="painterPresetName"', self.painter)

    def test_durable_ux_decisions_are_mapped_to_current_acceptance(self):
        contract = (ROOT / "docs" / "CURRENT_UX_ACCEPTANCE.md").read_text(
            encoding="utf-8"
        )
        for decision in (
            "Desired state is never presented as observed wall state.",
            "Phone operation has one clear active workspace and no hidden focus path.",
            "Every consequential operation names its target and its action scope.",
            "Provider, readiness, and preview provenance are distinct facts.",
            "Divergence preserves work and has an explicit recovery path.",
            "Discovery remains complete without taking output live.",
        ):
            with self.subTest(decision=decision):
                self.assertIn(decision, contract)
        self.assertIn("Pending until correlated identity evidence is observed", contract)
        self.assertIn("durable Favorites and Recent views", contract)
        self.assertIn("No historical executable UI, API example, scorecard", contract)


if __name__ == "__main__":
    unittest.main()

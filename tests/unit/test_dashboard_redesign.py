import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "web/static/js/dashboard.js").read_text(encoding="utf-8")
STYLES = (ROOT / "web/static/css/dashboard.css").read_text(encoding="utf-8")


class DashboardRedesignTests(unittest.TestCase):
    def test_task_areas_are_first_class_and_accessible(self):
        for area in ("library", "now-playing", "compose", "system"):
            self.assertIn(f'id="dashboard-tab-{area}"', TEMPLATE)
            self.assertIn(f'id="dashboard-{area}"', TEMPLATE)
            self.assertIn(f'aria-controls="dashboard-{area}"', TEMPLATE)
        self.assertIn("ArrowRight", SCRIPT)
        self.assertIn("ArrowLeft", SCRIPT)

    def test_library_filters_and_progressively_discloses_matches(self):
        for marker in (
            'id="librarySearch"',
            'id="libraryCategory"',
            'data-library-kind="animation"',
            'data-library-kind="preset"',
            'data-library-saved="favorites"',
            'data-library-saved="recent"',
            'data-library-favorite',
            'id="libraryClearFilters"',
            'id="libraryShowMore"',
        ):
            self.assertIn(marker, TEMPLATE)
        self.assertIn("const LIBRARY_BATCH_SIZE = 24", SCRIPT)
        self.assertIn("const LIBRARY_RECENT_LIMIT = 12", SCRIPT)
        self.assertIn("matches.slice(0, visible)", SCRIPT)
        self.assertIn("libraryVisibleLimit = LIBRARY_BATCH_SIZE", SCRIPT)
        self.assertIn("window.localStorage.setItem", SCRIPT)
        self.assertIn("recordLibraryRecent", SCRIPT)
        self.assertIn("libraryRecentIds.indexOf", SCRIPT)
        self.assertIn('onclick="clearLibraryFilters()"', TEMPLATE)

    def test_studio_numeric_controls_honor_schema_contract(self):
        self.assertIn("info.step !== undefined", SCRIPT)
        self.assertIn("decimalPlaces(info.default)", SCRIPT)
        self.assertIn('` min="${escapeHtml(paramInfo.min)}"`', SCRIPT)
        self.assertIn('` max="${escapeHtml(paramInfo.max)}"`', SCRIPT)
        self.assertIn("constrainParameterValue", SCRIPT)

    def test_system_and_advanced_have_explicitly_distinct_roles(self):
        self.assertIn("Run guided installation checks here", TEMPLATE)
        self.assertIn("Open Advanced", TEMPLATE)
        self.assertNotIn('id="diagnosticsAccordion"', TEMPLATE)

    def test_selection_and_preview_do_not_start_live_output(self):
        select_block = re.search(
            r"function selectControlAnimation\(.*?\n    }\n\n    function updateControlMode",
            SCRIPT,
            re.DOTALL,
        )
        preview_block = re.search(
            r"function previewAnimation\(.*?\n    }\n\n    function requestRandomHole",
            SCRIPT,
            re.DOTALL,
        )
        self.assertIsNotNone(select_block)
        self.assertIsNotNone(preview_block)
        self.assertNotIn("startAnimation(", select_block.group(0))
        self.assertNotIn("startAnimation(", preview_block.group(0))
        self.assertNotIn("/apply", TEMPLATE)
        self.assertNotIn("Take live", TEMPLATE)
        self.assertNotIn("Take scene live", TEMPLATE)
        self.assertIn("Check &amp; activate in Composer", TEMPLATE)

    def test_preset_actions_accept_the_direct_get_api_contract(self):
        self.assertIn("function normalizeDashboardPresetPayload(payload)", SCRIPT)
        self.assertIn("const preset = payload?.preset || payload", SCRIPT)
        self.assertIn("!preset.params || typeof preset.params !== 'object'", SCRIPT)
        self.assertIn("return normalizeDashboardPresetPayload(payload)", SCRIPT)
        self.assertIn("Preset settings are unavailable.", SCRIPT)

    def test_installation_parameters_are_not_normal_controls(self):
        self.assertIn("function isInstallationParameter", SCRIPT)
        self.assertIn("mask_path", SCRIPT)
        self.assertIn("Installation settings are protected here", SCRIPT)
        self.assertIn("parameter-installation-notice", STYLES)

    def test_icon_only_parameter_reset_has_an_accessible_name(self):
        self.assertIn('aria-label="Reset ${escapeHtml(prettyName)}', SCRIPT)
        self.assertIn('fa-rotate-left" aria-hidden="true"', SCRIPT)

    def test_dashboard_has_no_independent_sidebar_scroller(self):
        self.assertNotIn("dashboard-sidebar-scroll", TEMPLATE)
        self.assertNotIn("overscroll-behavior-y: contain", STYLES)
        self.assertNotIn("max-height: calc(100vh - 2rem)", STYLES)


if __name__ == "__main__":
    unittest.main()

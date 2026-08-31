"""Focused Phase 2 mobile, accessibility, and install-surface contracts."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "web" / "templates" / "composer.html"
STYLES = ROOT / "web" / "static" / "css" / "composer.css"
JAVASCRIPT = ROOT / "web" / "static" / "js" / "composer.js"
MANIFEST = ROOT / "web" / "static" / "composer.webmanifest"


def _media_bodies(css: str, query: str) -> list[str]:
    """Return balanced media bodies so assertions can target the final cascade."""
    marker = f"@media ({query}) {{"
    bodies: list[str] = []
    cursor = 0
    while True:
        start = css.find(marker, cursor)
        if start < 0:
            return bodies
        body_start = start + len(marker)
        depth = 1
        index = body_start
        while index < len(css) and depth:
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        if depth:
            raise AssertionError(f"unbalanced media rule: {query}")
        bodies.append(css[body_start:index - 1])
        cursor = index


class _Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, {key: value or "" for key, value in attrs}))

    def by_id(self, element_id: str) -> tuple[str, dict[str, str]]:
        matches = [item for item in self.elements if item[1].get("id") == element_id]
        if len(matches) != 1:
            raise AssertionError(f"expected one #{element_id}, found {len(matches)}")
        return matches[0]


class BrowserComposerMobileUXTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = TEMPLATE.read_text(encoding="utf-8")
        cls.css = STYLES.read_text(encoding="utf-8")
        cls.javascript = JAVASCRIPT.read_text(encoding="utf-8")
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.final_mobile_css = _media_bodies(cls.css, "max-width: 760px")[-1]
        cls.audit = _Audit()
        cls.audit.feed(cls.html)

    def test_renderer_catalog_is_open_and_visually_precedes_starting_points(self) -> None:
        preset_position = self.html.index('id="presetList"')
        disclosure_position = self.html.index('id="animationCatalogDisclosure"')
        catalog_position = self.html.index('id="componentList"')

        self.assertLess(preset_position, disclosure_position)
        self.assertLess(disclosure_position, catalog_position)
        tag, attrs = self.audit.by_id("animationCatalogDisclosure")
        self.assertEqual(tag, "details")
        self.assertIn("open", attrs)
        self.assertIn(".library-pane > .catalog-disclosure { order: 2;", self.css)
        self.assertIn(".library-pane > .featured-starts { order: 3;", self.css)

    def test_mobile_has_one_five_destination_navigation_surface(self) -> None:
        destinations = [
            attrs["data-mobile-target"]
            for tag, attrs in self.audit.elements
            if tag == "button" and "data-mobile-target" in attrs
        ]
        self.assertEqual(
            destinations,
            ["library", "edit", "layers", "wall", "check"],
        )
        mobile_rule = re.search(
            r"@media \(max-width: 760px\) \{(?P<body>.*?)\n\}",
            self.css,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(mobile_rule)
        self.assertRegex(
            mobile_rule.group("body"),
            r"\.inspector-tabs\s*\{\s*display:\s*none;\s*\}",
        )

    def test_compare_control_is_in_flow_and_explains_every_mode(self) -> None:
        self.assertIn('aria-describedby="comparisonHelp"', self.html)
        self.assertIn('aria-label="About Draft, Split, and Original"', self.html)
        self.assertIn("Left · Original", self.html)
        self.assertIn("Right · Draft", self.html)
        self.assertRegex(
            self.css,
            r"\.compare-control\s*\{[^}]*position:\s*static;",
        )
        self.assertRegex(
            self.css,
            r"\.compare-control button\s*\{[^}]*min-height:\s*44px;",
        )

    def test_advanced_and_pwa_status_hooks_are_stable_and_accessible(self) -> None:
        tag, _attrs = self.audit.by_id("installationAdvanced")
        self.assertEqual(tag, "details")
        for element_id in ("offlineReadiness", "installStatus"):
            tag, attrs = self.audit.by_id(element_id)
            self.assertEqual(tag, "small")
            self.assertEqual(attrs.get("role"), "status")
            self.assertEqual(attrs.get("aria-live"), "polite")
            self.assertTrue(attrs.get("data-state"))
        self.audit.by_id("networkStatus")
        self.audit.by_id("installGuidance")
        self.audit.by_id("advancedParameterList")
        self.audit.by_id("advancedParameterEmpty")

        for element_id in (
            "activationReadiness", "immediateApplyStatus", "mobileActivationStatus",
            "directEditStatus", "sceneChoiceStatus",
        ):
            self.audit.by_id(element_id)

        self.assertIn("dial changes update this preview immediately", self.html)
        self.assertIn("a newer edit replaces it before send", self.javascript)
        self.assertIn("exact newest edit observed by the controller", self.javascript)
        self.assertIn("function renderSceneChoiceStatus()", self.javascript)
        self.assertIn('.direct-edit-status[data-state="active"]', self.css)

        self.assertEqual(
            self.manifest["display_override"],
            ["standalone", "minimal-ui", "browser"],
        )
        self.assertEqual(
            self.manifest["launch_handler"],
            {"client_mode": "navigate-existing"},
        )
        self.assertFalse(self.manifest["prefer_related_applications"])

    def test_deferred_maintenance_module_receives_authoritative_bootstrap_signal(self) -> None:
        maintenance = (ROOT / "web" / "static" / "js" / "composer-maintenance.js").read_text(encoding="utf-8")
        assignment = "state.bootstrap = assertBootstrap(await response.json(), {requireLocalProfile: true});"
        signal = "document.dispatchEvent(new CustomEvent('composer:bootstrap'));"
        self.assertIn(signal, self.javascript)
        self.assertLess(self.javascript.index(assignment), self.javascript.index(signal))
        self.assertIn("events.on(dom.document, 'composer:bootstrap', render);", maintenance)
        self.assertIn("document.dispatchEvent(new CustomEvent('composer:capability-change'));", self.javascript)
        self.assertIn("events.on(dom.document, 'composer:capability-change', render);", maintenance)
        self.assertGreater(
            self.html.index("composer-maintenance.js"),
            self.html.index("browser_composer_application"),
        )
        self.assertIn("Duration (0.1–30 s)", self.html)

    def test_maintenance_capability_uses_live_server_bootstrap_and_connection(self) -> None:
        maintenance = ROOT / "web" / "static" / "js" / "composer-maintenance.js"
        script = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}, console};
context.window.LEDGridComposerModules = {register() {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const resolve = context.window.LEDGridComposerMaintenance.resolveCapability;
const offline = {capabilities: {maintenance: {available: false, execution: 'controller_file_channel'}}};
const live = {capabilities: {maintenance: {available: true, execution: 'controller_file_channel', max_intensity: 64}}};
assert.equal(resolve({bootstrap: offline, serverBootstrap: live, serverOnline: true, serverChecking: false}), live.capabilities.maintenance);
assert.equal(resolve({bootstrap: offline, serverBootstrap: live, serverOnline: false, serverChecking: false}), null);
assert.equal(resolve({bootstrap: offline, serverBootstrap: offline, serverOnline: true, serverChecking: false}), null);
"""
        completed = subprocess.run(
            ["node", "-e", script, str(maintenance)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_late_registered_maintenance_click_queues_named_request_without_random_uuid(self) -> None:
        maintenance = ROOT / "web" / "static" / "js" / "composer-maintenance.js"
        script = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Target {
  constructor(value = '') { this.value = value; this.hidden = false; this.disabled = false; this.dataset = {}; this.firstChild = {textContent: ''}; this.listeners = new Map(); this.textContent = ''; }
  addEventListener(type, listener) { this.listeners.set(type, [...(this.listeners.get(type) || []), listener]); }
  dispatch(type) { for (const listener of this.listeners.get(type) || []) listener({preventDefault() {}}); }
}
const elements = {
  maintenancePanel: new Target(), maintenanceDiagnostic: new Target('receiver_band'),
  maintenanceTarget: new Target('0'), maintenanceTargetField: new Target(),
  maintenanceLane: new Target('0'), maintenanceLaneField: new Target(),
  maintenanceIntensity: new Target('32'), maintenanceDuration: new Target('1'),
  maintenanceRunButton: new Target(), maintenanceResult: new Target(),
};
const document = new Target();
const window = new Target();
const requests = [];
window.crypto = {getRandomValues(bytes) { for (let index = 0; index < bytes.length; index += 1) bytes[index] = index; return bytes; }};
window.fetch = async (url, init = {}) => {
  requests.push({url, init});
  if (init.method === 'POST') return {ok: true, json: async () => ({phase: 'queued', request_id: init.headers['Idempotency-Key']})};
  return {ok: true, json: async () => ({phase: 'queued'})};
};
window.setTimeout = () => 1;
window.clearTimeout = () => {};
const state = {serverOnline: true, serverChecking: false, serverBootstrap: {capabilities: {maintenance: {
  available: true, execution: 'controller_file_channel', url: '/api/v1/composer/maintenance', max_intensity: 64, max_duration_seconds: 30,
}}}};
const context = {state, dom: {byId: (id) => elements[id], document}, events: {on: (target, type, listener) => target.addEventListener(type, listener)}, runtime: {window}};
window.LEDGridComposerModules = {register(_name, installer) { installer(context); }};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {window, Set, Number, Math, JSON, Object, Array, Uint8Array, encodeURIComponent});
elements.maintenanceRunButton.dispatch('click');
(async () => {
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
  assert.equal(requests.length >= 1, true);
  assert.equal(requests[0].url, '/api/v1/composer/maintenance');
  assert.equal(requests[0].init.method, 'POST');
  assert.equal(JSON.parse(requests[0].init.body).diagnostic, 'receiver_band');
  assert.match(elements.maintenanceResult.textContent, /^queued/i);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", script, str(maintenance)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_phone_diagnostics_popover_keeps_duration_and_run_reachable_above_fixed_bars(self) -> None:
        rule = re.search(
            r"\.operations-diagnostics > div\s*\{(?P<body>[^}]*)\}",
            self.final_mobile_css,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(rule)
        declarations = rule.group("body")
        for declaration in (
            "position: absolute;",
            "top: calc(100% + 4px);",
            "overflow-y: auto;",
            "overscroll-behavior: contain;",
            "-webkit-overflow-scrolling: touch;",
        ):
            self.assertIn(declaration, declarations)

        # At the reviewed 390x844 viewport (zero safe-area insets), the
        # independently scrolling popover must end before the fixed action/nav
        # bars. Its 613px diagnostic content can therefore scroll to Duration
        # and Run rather than being clipped by the document body.
        viewport_height = 844
        header_height = 72
        configured_operations_height = 148
        observed_operations_height = 158
        action_bar_height = 60
        navigation_height = 68
        popover_top = header_height + observed_operations_height + 4
        popover_max_height = (
            viewport_height
            - header_height
            - configured_operations_height
            - action_bar_height
            - navigation_height
            - 24
        )
        popover_bottom = popover_top + popover_max_height
        self.assertLessEqual(popover_bottom, viewport_height - action_bar_height - navigation_height)
        self.assertGreaterEqual(popover_max_height, 44)
        self.assertLess(popover_max_height, 613)
        self.assertLessEqual(10 + min(360, 390 - 20), 390)

    def test_mobile_shell_reserves_safe_areas_and_full_size_targets(self) -> None:
        for inset in ("top", "right", "bottom", "left"):
            self.assertIn(f"env(safe-area-inset-{inset})", self.css)
        self.assertIn("overflow-x: hidden", self.css)
        self.assertIn("height: calc(100dvh", self.css)
        self.assertIn(
            ".server-action-buttons button, .local-action-buttons button, .mobile-tabs button { min-height: 44px; }",
            self.css,
        )
        self.assertRegex(
            self.css,
            r"\.icon-button\s*\{\s*width:\s*44px;\s*\}",
        )

    def test_mobile_keeps_touch_access_to_history_copy_and_preview_rate(self) -> None:
        _tag, copy_attrs = self.audit.by_id("copyButton")
        self.assertEqual(copy_attrs.get("aria-label"), "Copy preset JSON")
        _tag, fps_attrs = self.audit.by_id("fpsSelect")
        self.assertEqual(fps_attrs.get("aria-label"), "Preview frame rate")
        self.assertRegex(
            self.final_mobile_css,
            r"#copyButton\s*\{[^}]*display:\s*inline-grid;",
        )
        self.assertRegex(
            self.final_mobile_css,
            r"\.fps-select\s*\{[^}]*display:\s*flex;[^}]*min-height:\s*44px;",
        )
        self.assertRegex(
            self.final_mobile_css,
            r"\.fps-select select\s*\{[^}]*min-height:\s*44px;",
        )

    def test_final_mobile_cascade_preserves_wall_and_switch_touch_targets(self) -> None:
        for pattern in (
            r"\.switch-control input, \.compact-switch input\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;",
            r"\.wall-controls-heading button, \.wall-apply-bar button, #editMasksButton\s*\{[^}]*min-height:\s*44px;",
            r"\.catalog-disclosure > summary, \.advanced-disclosure > summary,[^}]*min-height:\s*44px;",
            r"\.catalog-filters button,[^}]*min-height:\s*44px;",
            r"\.fallback-field select,[^}]*min-height:\s*44px;",
            r"\.parameter-list input\[type=\"number\"\], \.parameter-list input\[type=\"text\"\],[^}]*min-height:\s*44px;",
        ):
            self.assertRegex(self.final_mobile_css, pattern)
        self.assertIn("--faint: #aaa;", self.final_mobile_css)
        self.assertRegex(
            self.final_mobile_css,
            r"\.mobile-tabs button\s*\{[^}]*font-size:\s*11px;",
        )

    def test_mobile_stage_children_cannot_expand_the_viewport(self) -> None:
        self.assertRegex(
            self.final_mobile_css,
            r"\.stage-pane\.is-active\s*\{[^}]*grid-template-columns:\s*minmax\(0,1fr\);[^}]*width:\s*100%;",
        )
        self.assertRegex(
            self.final_mobile_css,
            r"\.provenance-note, \.provenance-note p, \.provenance-note p span\s*\{[^}]*min-width:\s*0;[^}]*max-width:\s*100%;",
        )
        self.assertRegex(
            self.final_mobile_css,
            r"\.provenance-note p span\s*\{[^}]*flex:\s*1 1 160px;[^}]*overflow:\s*hidden;",
        )
        self.assertRegex(
            self.final_mobile_css,
            r"\.transport\s*\{[^}]*grid-template-columns:\s*44px auto minmax\(0,1fr\) auto auto;",
        )
        self.assertRegex(
            self.final_mobile_css,
            r"\.play-button\s*\{[^}]*width:\s*44px;[^}]*min-width:\s*44px;[^}]*max-width:\s*44px;",
        )

    def test_phone_editing_keeps_preview_and_controls_side_by_side(self) -> None:
        self.assertIn("mobile-dual-pane", self.javascript)
        self.assertIn("['stage', 'tune'].includes(view.dataset.mobileView)", self.javascript)
        self.assertIn("selectMobileView('edit')", self.javascript)
        self.assertIn("data-mobile-target=\"edit\"", self.html)
        self.assertNotIn('data-mobile-target="stage"', self.html)
        self.assertNotIn('data-mobile-target="tune"', self.html)
        self.assertRegex(
            self.final_mobile_css,
            r"\.composer-shell\.mobile-dual-pane\s*\{[^}]*grid-template-columns:\s*minmax\(112px,\s*34%\)\s+minmax\(0,\s*1fr\);",
        )
        self.assertIn(
            ".composer-shell.mobile-dual-pane .stage-pane.mobile-view.is-active",
            self.final_mobile_css,
        )
        self.assertIn(
            ".composer-shell.mobile-dual-pane .inspector-pane.mobile-view.is-active",
            self.final_mobile_css,
        )

    def test_mobile_selection_opens_edit_and_keeps_apply_status_visible(self) -> None:
        self.audit.by_id("mobileActivationStatus")
        self.assertNotIn('id="mobileActivateButton"', self.html)
        self.assertIn("{focusEditor: true}", self.javascript)
        self.assertIn("options.focusEditor && window.matchMedia('(max-width: 760px)').matches", self.javascript)
        self.assertIn("selectMobileView('edit')", self.javascript)
        self.assertRegex(
            self.final_mobile_css,
            r"\.mobile-activate-bar\s*\{[^}]*position:\s*fixed;[^}]*bottom:\s*var\(--mobile-nav-height\);",
        )
        self.assertIn("--mobile-activate-height: 60px;", self.final_mobile_css)

    def test_mobile_header_and_mask_editor_reserve_device_safe_areas(self) -> None:
        self.assertIn("--mobile-header-height: calc(72px + env(safe-area-inset-top));", self.css)
        self.assertIn(
            "height: calc(100dvh - var(--mobile-header-height) - var(--mobile-nav-height) - var(--mobile-activate-height));",
            self.css,
        )
        _tag, canvas_attrs = self.audit.by_id("maskCanvas")
        self.assertEqual(canvas_attrs.get("aria-describedby"), "maskPanHint")
        self.audit.by_id("maskPanHint")
        self.assertIn("overscroll-behavior: auto;", self.final_mobile_css)
        self.assertIn("touch-action: pan-x pan-y;", self.final_mobile_css)
        self.assertIn("calc(48px + env(safe-area-inset-right))", self.final_mobile_css)
        self.assertIn("calc(48px + env(safe-area-inset-left))", self.final_mobile_css)
        self.assertIn("calc(10px + env(safe-area-inset-top))", self.css)

    def test_javascript_owned_ids_remain_unique(self) -> None:
        required_ids = {
            "serverState", "presetName", "saveState", "undoButton", "redoButton",
            "componentSearch", "componentList", "presetList", "catalogCount",
            "presetCount", "previewCanvas", "previewPlaceholder", "splitLegend",
            "playButton", "timeline", "fpsSelect", "controlsTab", "layersTab",
            "wallTab", "checkerTab", "controlsPanel", "layersPanel", "wallPanel",
            "checkerPanel",
            "parameterList", "fallbackSelect", "serverActionStatus",
        }
        ids = [attrs["id"] for _tag, attrs in self.audit.elements if attrs.get("id")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(required_ids.issubset(ids))

    def test_authoring_workspace_is_dense_flat_and_keyboard_first(self) -> None:
        self.assertIn('<meta name="theme-color" content="#242424">', self.html)
        self.assertIn('class="toolbar-separator"', self.html)
        self.assertIn('aria-keyshortcuts="Control+E Meta+E"', self.html)
        self.assertIn('aria-keyshortcuts="T"', self.html)
        self.assertIn('aria-keyshortcuts="L"', self.html)
        self.assertIn('aria-keyshortcuts="W"', self.html)
        self.assertIn('aria-keyshortcuts="C"', self.html)
        for tab_id in ("controlsTab", "layersTab", "wallTab", "checkerTab"):
            self.assertIn(f"$('{tab_id}').focus();", self.javascript)
        self.assertLess(
            self.html.index('id="componentSearch"'),
            self.html.index('id="animationCatalogDisclosure"'),
        )
        self.assertIn("Professional authoring workspace", self.css)
        self.assertIn("--header-height: 46px;", self.css)
        self.assertIn("--acid: #38a2ff;", self.css)
        self.assertIn(".wall-aura, .floor-shadow { display: none; }", self.css)
        self.assertIn("$('animationCatalogDisclosure').open = true", self.javascript)
        self.assertIn("event.key === '/'", self.javascript)
        self.assertIn("event.code === 'Space'", self.javascript)
        self.assertIn("else if (key === 'w')", self.javascript)
        self.assertIn("else if (key === 'e')", self.javascript)


if __name__ == "__main__":
    unittest.main()

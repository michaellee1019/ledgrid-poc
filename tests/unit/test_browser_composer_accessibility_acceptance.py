"""Accessibility, responsive-layout, and retired-authority Composer contracts."""

from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "web" / "templates" / "composer.html"
STYLES = ROOT / "web" / "static" / "css" / "composer.css"
SCRIPT = ROOT / "web" / "static" / "js" / "composer.js"
PAINTER_TEMPLATE = ROOT / "web" / "templates" / "painter.html"
PAINTER_SCRIPT = ROOT / "web" / "static" / "js" / "painter.js"
WEB_APP = ROOT / "web" / "app.py"

# These are the explicit portable layout contracts from the production plan.
VIEWPORTS = (
    (375, 667, "compact phone"),
    (390, 844, "standard phone"),
    (430, 932, "large phone"),
    (768, 1024, "portrait tablet"),
    (1440, 1000, "desktop"),
)


def _media_bodies(css: str, query: str) -> list[str]:
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


class _Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.elements.append((tag, {key: value or "" for key, value in attrs}))

    def by_id(self, element_id: str) -> tuple[str, dict[str, str]]:
        matches = [item for item in self.elements if item[1].get("id") == element_id]
        if len(matches) != 1:
            raise AssertionError(f"expected one #{element_id}, found {len(matches)}")
        return matches[0]

    @property
    def ids(self) -> set[str]:
        return {attrs["id"] for _tag, attrs in self.elements if attrs.get("id")}


class BrowserComposerAccessibilityAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = TEMPLATE.read_text(encoding="utf-8")
        cls.css = STYLES.read_text(encoding="utf-8")
        cls.script = SCRIPT.read_text(encoding="utf-8")
        cls.painter_html = PAINTER_TEMPLATE.read_text(encoding="utf-8")
        cls.painter_script = PAINTER_SCRIPT.read_text(encoding="utf-8")
        cls.web_app = WEB_APP.read_text(encoding="utf-8")
        cls.elements = _Elements()
        cls.elements.feed(cls.html)
        mobile_bodies = _media_bodies(cls.css, "max-width: 760px")
        cls.mobile_css = mobile_bodies[-1]
        cls.all_mobile_css = "\n".join(mobile_bodies)
        cls.tablet_css = _media_bodies(
            cls.css, "min-width: 761px) and (max-width: 1040px",
        )[-1]

    def test_modal_name_description_focus_and_return_are_deterministic(self) -> None:
        dialogs = (
            "overwriteDialog", "activateDialog", "wallReviewDialog",
            "profileCandidateDialog", "maskEditorDialog",
        )
        for dialog_id in dialogs:
            with self.subTest(dialog=dialog_id):
                tag, attrs = self.elements.by_id(dialog_id)
                self.assertEqual(tag, "dialog")
                self.assertEqual(attrs.get("aria-modal"), "true")
                self.assertIn(attrs.get("aria-labelledby"), self.elements.ids)
                for described_by in attrs.get("aria-describedby", "").split():
                    self.assertIn(described_by, self.elements.ids)

        # All openings go through one helper, so initial and return focus cannot
        # drift independently between activation, wall, profile, and mask flows.
        self.assertEqual(self.script.count(".showModal()"), 1)
        self.assertIn("function showComposerModal(", self.script)
        self.assertIn("function restoreModalFocus(", self.script)
        self.assertIn("function trapModalFocus(", self.script)
        self.assertIn("event.key !== 'Tab'", self.script)
        self.assertIn("active === first || !dialog.contains(active)", self.script)
        self.assertIn("active === last || !dialog.contains(active)", self.script)
        self.assertIn("last.focus({preventScroll: true})", self.script)
        self.assertIn("first.focus({preventScroll: true})", self.script)
        self.assertIn("dialog.addEventListener('close', restoreModalFocus)", self.script)
        self.assertIn("dialog.addEventListener('keydown', trapModalFocus)", self.script)
        self.assertIn("initialFocus.focus({preventScroll: true})", self.script)
        self.assertIn("window.setTimeout(() => {", self.script)
        self.assertIn("returnFocus.focus({preventScroll: true})", self.script)

    def test_browser_electrical_evidence_preserves_mean_peak_order(self) -> None:
        self.assertIn(
            "meanCurrentAmps: Math.min(peakCurrent, currentTotal / SAMPLE_FRAMES)",
            self.script,
        )

    def test_keyboard_navigation_covers_tabs_catalog_and_mask_grid(self) -> None:
        for tab_id in ("controlsTab", "layersTab", "wallTab", "checkerTab"):
            tag, attrs = self.elements.by_id(tab_id)
            self.assertEqual(tag, "button")
            self.assertEqual(attrs.get("role"), "tab")
            self.assertIn(attrs.get("aria-controls"), self.elements.ids)
        for key in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"):
            self.assertIn(key, self.script)
        self.assertIn("items[next].focus()", self.script)
        self.assertIn("items[next].click()", self.script)

        tag, canvas = self.elements.by_id("maskCanvas")
        self.assertEqual(tag, "canvas")
        self.assertEqual(canvas.get("tabindex"), "0")
        self.assertEqual(canvas.get("role"), "region")
        self.assertEqual(canvas.get("aria-describedby"), "maskPanHint")
        for key in ("PageUp", "PageDown", "Delete", "Backspace"):
            self.assertIn(key, self.script)
        self.assertIn("event.key === ' ' || event.key === 'Enter'", self.script)
        self.assertIn("paintKeyboardMaskCell", self.script)
        for shortcut in tuple(str(index) for index in range(1, 9)) + ("E",):
            self.assertIn(f'aria-keyshortcuts="{shortcut}"', self.html)

    def test_reduced_motion_starts_paused_but_keeps_explicit_play_control(self) -> None:
        reduced_css = _media_bodies(
            self.css, "prefers-reduced-motion: reduce",
        )[-1]
        for declaration in (
            "scroll-behavior: auto !important",
            "animation-duration: .01ms !important",
            "animation-iteration-count: 1 !important",
            "transition-duration: .01ms !important",
        ):
            self.assertIn(declaration, reduced_css)
        self.assertIn("matchMedia?.('(prefers-reduced-motion: reduce)')", self.script)
        self.assertIn("if (state.reducedMotion) state.playing = false", self.script)
        self.assertIn("Reduced motion is enabled", self.script)
        self.assertIn("aria-describedby=\"previewMotionStatus\"", self.html)
        self.assertIn(
            "state.playing = !state.playing",
            self.script,
            "A user must still be able to opt into preview motion.",
        )

    def test_phone_tablet_and_desktop_width_contracts_are_bounded(self) -> None:
        self.assertEqual(
            VIEWPORTS,
            (
                (375, 667, "compact phone"),
                (390, 844, "standard phone"),
                (430, 932, "large phone"),
                (768, 1024, "portrait tablet"),
                (1440, 1000, "desktop"),
            ),
        )
        self.assertRegex(
            self.css,
            r"html, body\s*\{[^}]*max-width:\s*100%;[^}]*overflow-x:\s*hidden;",
        )
        self.assertIn("repeat(6, 1fr)", self.all_mobile_css)
        self.assertRegex(
            self.all_mobile_css,
            r"\.mobile-tabs button\s*\{[^}]*min-width:\s*0;",
        )
        self.assertRegex(
            self.mobile_css,
            r"\.stage-pane\.is-active\s*\{[^}]*grid-template-columns:\s*minmax\(0,1fr\);[^}]*width:\s*100%;",
        )
        self.assertIn(
            "grid-template-columns: minmax(190px,24vw) minmax(0,1fr) minmax(240px,30vw);",
            self.tablet_css,
        )
        # The former tablet minimum was 220 + 350 + 270 = 840px. The new
        # side-track floor leaves the middle track shrinkable at 768px.
        tablet_width = next(width for width, _height, name in VIEWPORTS if name == "portrait tablet")
        self.assertLess(190 + 240, tablet_width)
        for width, height, name in VIEWPORTS:
            with self.subTest(viewport=name):
                self.assertGreaterEqual(width, 375)
                self.assertGreaterEqual(height, 667)

    def test_primary_actions_and_mobile_interactions_are_at_least_44px(self) -> None:
        final_primary_rule = self.css.rfind(
            ".primary-button, .dialog-actions button { min-height: 44px; }",
        )
        desktop_compaction = self.css.rfind(
            ".icon-button, .quiet-button, .primary-button, .text-button { min-height: 30px;",
        )
        self.assertGreater(final_primary_rule, desktop_compaction)
        for contract in (
            ".preset-button, .component-card { min-height: 44px; }",
            ".wall-controls-heading button, .wall-apply-bar button, #editMasksButton { min-height: 44px; }",
            ".mask-tools button, .mask-editor-toolbar > button { min-height: 44px; }",
            ".mask-editor-actions button { min-height: 44px; }",
        ):
            self.assertIn(contract, self.all_mobile_css)

    def test_guarded_activation_and_managed_profile_are_the_only_authorities(self) -> None:
        self.assertNotIn("/api/start/", self.script)
        self.assertNotIn("/api/painter/masks", self.script)
        self.assertIn("await createServerCheck()", self.script)
        self.assertIn("check_token: serverCheck.token", self.script)
        self.assertIn("expected_controller_state_revision", self.script)
        self.assertIn("headers: {'Idempotency-Key': serverCheck.idempotencyKey}", self.script)
        self.assertIn("globalActions().installation_profile_draft_url", self.script)
        self.assertIn("globalActions().installation_profile_publish_url", self.script)
        self.assertIn("headers: {'If-Match':", self.script)

        self.assertNotIn("saveMasksBtn", self.painter_script)
        self.assertNotIn("async save()", self.painter_script)
        self.assertNotRegex(
            self.painter_script,
            r"fetch\('/api/painter/masks'.*?method:\s*'POST'",
        )
        self.assertNotIn("saves both mask files", self.painter_html)
        self.assertIn("Manage profile in Composer", self.painter_html)

    def test_legacy_http_aliases_are_retained_only_as_fail_closed_boundaries(self) -> None:
        start_alias = self.web_app.split(
            "@self.app.route('/api/start/<animation_name>'", 1,
        )[1].split("@self.app.route('/api/stop'", 1)[0]
        self.assertIn("_guarded_scene_error", start_alias)
        self.assertNotIn("send_command", start_alias)

        mask_alias = self.web_app.split(
            "@self.app.route('/api/painter/masks', methods=['POST'])", 1,
        )[1].split("@self.app.route('/api/painter/presets')", 1)[0]
        self.assertIn("status_code = 405", mask_alias)
        self.assertIn("response.headers['Allow'] = 'GET'", mask_alias)
        self.assertNotIn("write_text", mask_alias)


if __name__ == "__main__":
    unittest.main()

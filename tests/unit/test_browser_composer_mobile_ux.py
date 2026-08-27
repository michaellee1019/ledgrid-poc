"""Focused Phase 2 mobile, accessibility, and install-surface contracts."""

from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "web" / "templates" / "composer.html"
STYLES = ROOT / "web" / "static" / "css" / "composer.css"
JAVASCRIPT = ROOT / "web" / "static" / "js" / "composer.js"
MANIFEST = ROOT / "web" / "static" / "composer.webmanifest"


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
        cls.audit = _Audit()
        cls.audit.feed(cls.html)

    def test_starting_points_precede_the_disclosed_renderer_catalog(self) -> None:
        preset_position = self.html.index('id="presetList"')
        disclosure_position = self.html.index('id="animationCatalogDisclosure"')
        catalog_position = self.html.index('id="componentList"')

        self.assertLess(preset_position, disclosure_position)
        self.assertLess(disclosure_position, catalog_position)
        tag, attrs = self.audit.by_id("animationCatalogDisclosure")
        self.assertEqual(tag, "details")
        self.assertNotIn("open", attrs)

    def test_mobile_has_one_six_destination_navigation_surface(self) -> None:
        destinations = [
            attrs["data-mobile-target"]
            for tag, attrs in self.audit.elements
            if tag == "button" and "data-mobile-target" in attrs
        ]
        self.assertEqual(
            destinations,
            ["library", "stage", "tune", "layers", "wall", "check"],
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
            "activationReadiness", "activateProvider", "activateRuntimeDigest",
            "activateRevision", "activateCheck", "activateDestination",
        ):
            self.audit.by_id(element_id)

        self.assertEqual(
            self.manifest["display_override"],
            ["standalone", "minimal-ui", "browser"],
        )
        self.assertEqual(
            self.manifest["launch_handler"],
            {"client_mode": "navigate-existing"},
        )
        self.assertFalse(self.manifest["prefer_related_applications"])

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

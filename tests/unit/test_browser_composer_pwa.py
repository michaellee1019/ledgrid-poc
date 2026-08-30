"""Installability, offline-shell, and mobile-accessibility acceptance checks."""

from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

import numpy as np

from tools import render_browser_composer_contact_sheet as contact_sheet


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "web" / "static"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _service_worker_shell_assets(source: str) -> set[str]:
    match = re.search(
        r"const\s+SHELL_ASSETS\s*=\s*\[(?P<body>.*?)\];",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("service worker has no declarative shell asset list")
    return set(re.findall(r"['\"](/[^'\"]+)['\"]", match.group("body")))


class _ComposerHTMLAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.scripts: list[dict[str, str]] = []
        self.buttons: list[dict[str, str]] = []
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        self.elements.append((tag, values))
        if tag == "meta":
            self.metas.append(values)
        elif tag == "link":
            self.links.append(values)
        elif tag == "script":
            self.scripts.append(values)
        elif tag == "button":
            self.buttons.append(values)


class BrowserComposerPWATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = _read("web/templates/composer.html")
        cls.css = _read("web/static/css/composer.css")
        cls.javascript = _read("web/static/js/composer.js")
        cls.worker = _read("web/static/js/composer_service_worker.js")
        cls.manifest = json.loads(_read("web/static/composer.webmanifest"))
        cls.audit = _ComposerHTMLAudit()
        cls.audit.feed(cls.html)

    def test_manifest_and_apple_metadata_are_installable(self) -> None:
        self.assertEqual(self.manifest["id"], "/composer")
        self.assertEqual(self.manifest["start_url"], "/composer")
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertEqual(self.manifest["scope"], "/")
        self.assertTrue(self.manifest["name"])
        self.assertTrue(self.manifest["short_name"])
        self.assertRegex(self.manifest["theme_color"], r"^#[0-9a-fA-F]{6}$")
        self.assertRegex(self.manifest["background_color"], r"^#[0-9a-fA-F]{6}$")

        icons = self.manifest["icons"]
        self.assertTrue(any("512x512" in icon["sizes"] for icon in icons))
        self.assertTrue(any("any" in icon.get("purpose", "") for icon in icons))
        self.assertTrue(any("maskable" in icon.get("purpose", "") for icon in icons))
        for icon in icons:
            self.assertTrue((STATIC / icon["src"].removeprefix("/static/")).is_file())

        links_by_rel = {link.get("rel"): link for link in self.audit.links}
        self.assertIn("manifest", links_by_rel)
        self.assertIn("apple-touch-icon", links_by_rel)
        apple_icon = links_by_rel["apple-touch-icon"]
        self.assertEqual(apple_icon.get("sizes"), "180x180")
        self.assertTrue((STATIC / "icons" / "composer-180.png").is_file())
        apple_meta = {
            item.get("name"): item.get("content") for item in self.audit.metas
        }
        self.assertEqual(apple_meta.get("apple-mobile-web-app-capable"), "yes")

    def test_precache_contains_the_complete_versioned_local_shell(self) -> None:
        assets = _service_worker_shell_assets(self.worker)
        expected = {
            "/composer",
            "/composer-service-worker.js",
            "/static/css/composer.css",
            "/static/js/composer_compositor.js",
            "/static/js/composer_interactions.js",
            "/static/js/composer-operations.js",
            "/static/js/composer_state.js",
            "/static/js/composer_runtime.js",
            "/static/js/composer_sha256.js",
            "/composer-app.js",
            "/static/js/composer_native_worker.js",
            "/static/js/composer_python_worker.js",
            "/static/generated/composer/aurora_curtains_native.wasm",
            "/static/generated/composer/compiled_rainbow.wasm",
            "/static/generated/composer/bootstrap.v1.json",
            (
                "/static/generated/composer/installation_profile_"
                "ce457a14efd131395507c449f35a7701ca78ddca059620dc3757806ef553ca6a.bin"
            ),
            "/static/generated/composer/ledgrid_python_runtime.zip",
            "/static/generated/composer/offline_assets.json",
            "/static/composer.webmanifest",
            "/static/icons/composer-180.png",
            "/static/icons/composer-512.png",
            "/static/icons/composer.svg",
        }
        self.assertEqual(assets, expected)
        route_backed = {"/composer", "/composer-service-worker.js", "/composer-app.js"}
        for asset in sorted(assets - route_backed):
            self.assertTrue(
                (STATIC / asset.removeprefix("/static/")).is_file(),
                f"precache asset does not exist: {asset}",
            )
        self.assertRegex(self.worker, r"CACHE_VERSION\s*=\s*['\"]v\d+['\"]")
        self.assertIn(
            "CACHE_NAME = `${CACHE_PREFIX}${CACHE_VERSION}`",
            self.worker,
        )
        self.assertIn("installVersionedShell", self.worker)
        self.assertIn("Offline asset digest mismatch", self.worker)
        self.assertIn("name.startsWith(CACHE_PREFIX)", self.worker)
        self.assertIn("name !== RUNTIME_CACHE_NAME", self.worker)
        self.assertIn("self.clients.claim()", self.worker)
        self.assertNotIn("client.navigate('/composer')", self.worker)
        self.assertNotIn("includeUncontrolled: true", self.worker)

    def test_navigation_and_static_bootstrap_have_explicit_offline_fallbacks(self) -> None:
        self.assertIn("event.request.mode === 'navigate'", self.worker)
        self.assertRegex(
            self.worker,
            r"url\.pathname\s*===\s*['\"]/composer['\"]",
        )
        self.assertIn("networkFirst(event.request, '/composer')", self.worker)
        self.assertIn("const BUNDLED_BOOTSTRAP_URL", self.worker)
        self.assertIn("shell.match(BUNDLED_BOOTSTRAP_URL)", self.worker)
        self.assertNotIn("networkFirst(event.request, BOOTSTRAP_URL)", self.worker)
        self.assertIn("const cached = await caches.match(fallbackKey)", self.worker)
        self.assertIn("if (cached) return cached", self.worker)

    def test_ready_offline_requires_verified_catalog_and_python_runtime(self) -> None:
        self.assertIn("type: 'OFFLINE_STATUS'", self.worker)
        self.assertIn("'PYTHON_RUNTIME_READY'", self.worker)
        self.assertIn("bootstrapPayload?.artifact?.kind !== 'bundled'", self.worker)
        self.assertIn("activeProfile", self.worker)
        self.assertIn("Python runtime asset changed", self.worker)
        self.assertIn("readyOffline: true", self.worker)
        self.assertIn("readyOffline: false", self.worker)

    def test_selected_profile_artifact_is_verified_and_available_offline(self) -> None:
        self.assertIn("PROFILE_ARTIFACT_PATH", self.worker)
        self.assertIn("BUNDLED_PROFILE_PATH", self.worker)
        self.assertIn("verifiedProfileArtifact", self.worker)
        self.assertIn("canonical.fill(0, 68, 100)", self.worker)
        self.assertIn("cacheImmutableProfileArtifact", self.worker)
        self.assertIn("profileArtifactDigest", self.worker)
        self.assertIn("profileArtifacts", self.worker)
        self.assertIn("INSTALLATION_PROFILE_ARTIFACT", self.worker)
        self.assertIn("deliverInstallationProfileArtifact", self.worker)

    def test_connectivity_and_mutating_actions_are_never_cached(self) -> None:
        assets = _service_worker_shell_assets(self.worker)
        forbidden = {
            "/api/v1/composer/connectivity",
            "/api/v1/composer/presets/validate",
            "/api/v1/composer/presets",
            "/api/v1/scene-presets",
            "/api/v1/scene/validate",
            "/api/v1/scene/checks",
            "/api/v1/scene/activations/example",
            "/api/v1/scene",
        }
        self.assertTrue(assets.isdisjoint(forbidden))
        self.assertIn("if (event.request.method !== 'GET') return", self.worker)
        self.assertIn("if (url.pathname.startsWith('/api/')) return", self.worker)
        self.assertEqual(
            set(re.findall(r"/api/[A-Za-z0-9_./:-]+", self.worker)),
            set(),
        )

    def test_compositor_is_loaded_as_part_of_the_document_shell(self) -> None:
        local_script_sources = {
            script.get("src", "") for script in self.audit.scripts
        }
        self.assertTrue(
            any("composer_compositor.js" in source for source in local_script_sources),
            "the cached compositor must also be loaded by the document",
        )
        self.assertIn(
            "/static/js/composer_compositor.js",
            _service_worker_shell_assets(self.worker),
        )

    def test_iphone_safe_areas_touch_and_accessibility_basics_are_preserved(self) -> None:
        viewport = next(
            item.get("content", "")
            for item in self.audit.metas
            if item.get("name") == "viewport"
        )
        self.assertIn("viewport-fit=cover", viewport)
        self.assertIn("env(safe-area-inset-top)", self.css)
        self.assertIn("env(safe-area-inset-bottom)", self.css)
        self.assertIn("100dvh", self.css)
        self.assertRegex(self.css, r"button\s*\{[^}]*touch-action:\s*manipulation")
        self.assertIn("@media (max-width: 760px)", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)

        self.assertTrue(self.audit.buttons)
        by_id = {
            attrs.get("id"): (tag, attrs)
            for tag, attrs in self.audit.elements
            if attrs.get("id")
        }
        skip_link = next(
            attrs for tag, attrs in self.audit.elements
            if tag == "a" and "skip-link" in attrs.get("class", "").split()
        )
        self.assertEqual(skip_link.get("href"), "#composerWorkspace")
        self.assertEqual(by_id["composerWorkspace"][1].get("tabindex"), "-1")
        self.assertTrue(by_id["previewCanvas"][1].get("aria-label"))
        self.assertEqual(by_id["saveState"][1].get("role"), "status")
        self.assertEqual(by_id["toastRegion"][1].get("aria-live"), "polite")
        self.assertIn("setAttribute('aria-selected'", self.javascript)
        self.assertIn("setAttribute('aria-pressed'", self.javascript)
        self.assertIn("setAttribute('aria-invalid'", self.javascript)
        self.assertIn("document.addEventListener('keydown'", self.javascript)


class BrowserComposerContactSheetUnitTests(unittest.TestCase):
    def test_physical_led_zero_is_drawn_at_the_bottom_without_transposing_strips(self) -> None:
        canonical = np.zeros(
            (contact_sheet.FRAME_WIDTH * contact_sheet.FRAME_HEIGHT, 3),
            dtype=np.uint8,
        )
        canonical[0] = (255, 0, 0)
        canonical[contact_sheet.FRAME_HEIGHT] = (0, 255, 0)

        canvas = contact_sheet._image_oriented(canonical)

        self.assertEqual(
            canvas.shape,
            (contact_sheet.FRAME_HEIGHT, contact_sheet.FRAME_WIDTH, 3),
        )
        np.testing.assert_array_equal(canvas[-1, 0], (255, 0, 0))
        np.testing.assert_array_equal(canvas[-1, 1], (0, 255, 0))

    def test_duplicate_pairs_are_flagged_only_within_the_same_plugin(self) -> None:
        records = [
            {"plugin_id": "one", "preset_id": "a", "pair_sha256": "same", "flags": []},
            {"plugin_id": "one", "preset_id": "b", "pair_sha256": "same", "flags": []},
            {"plugin_id": "two", "preset_id": "c", "pair_sha256": "same", "flags": []},
        ]

        groups = contact_sheet._duplicate_groups(records)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["plugin_id"], "one")
        self.assertEqual(groups[0]["preset_ids"], ["a", "b"])
        self.assertIn("duplicate_within_plugin", records[0]["flags"])
        self.assertNotIn("duplicate_within_plugin", records[2]["flags"])

    def test_semantic_capture_uses_sequential_authored_preview_timing(self) -> None:
        elapsed, fps, steps = contact_sheet._semantic_target({
            "preview": {"capture_seconds": [0, 0.25, 1.0], "simulation_fps": 30}
        })

        self.assertEqual(fps, 30)
        self.assertEqual(steps, 8)
        self.assertAlmostEqual(elapsed, 8 / 30)


if __name__ == "__main__":
    unittest.main()

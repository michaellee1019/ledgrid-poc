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
def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


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
    """Check the served current shell, not the retired browser compositor."""

    def setUp(self) -> None:
        from tests.unit.test_composer_looks import _PreviewManager, _WallChannel
        from web.app import AnimationWebInterface
        self.client = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True).app.test_client()
        self.audit = _ComposerHTMLAudit()
        self.audit.feed(self.client.get('/').get_data(as_text=True))

    def test_manifest_and_apple_metadata_are_installable(self) -> None:
        links = {item.get('rel'): item for item in self.audit.links}
        manifest = self.client.get(links['manifest']['href'])
        self.addCleanup(manifest.close)
        value = json.loads(manifest.data)
        self.assertEqual(value['start_url'], '/')
        self.assertEqual(value['scope'], '/')
        self.assertEqual(value['display'], 'standalone')
        for icon in value['icons']:
            response = self.client.get(icon['src'])
            self.addCleanup(response.close)
            self.assertEqual(response.status_code, 200)
        apple = links['apple-touch-icon']
        self.assertEqual(apple['sizes'], '180x180')
        response = self.client.get(apple['href'])
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/png')
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(response.data)) as image:
            self.assertEqual(image.size, (180, 180))
        metas = {item.get('name'): item.get('content') for item in self.audit.metas}
        self.assertEqual(metas['apple-mobile-web-app-capable'], 'yes')

    def test_current_preview_and_offline_shell_are_loaded(self) -> None:
        sources = [item.get('src', '') for item in self.audit.scripts]
        scripts = {}
        for source in sources:
            response = self.client.get(source)
            self.addCleanup(response.close)
            self.assertEqual(response.status_code, 200, source)
            scripts[source.split('?')[0]] = response.get_data(as_text=True)
        self.assertIn('/static/js/composer_preview_scheduler.js', scripts)
        self.assertIn('/static/js/composer_shell.js', scripts)
        self.assertIn('fetch(`${api}/preview`', scripts['/static/js/composer_slice.js'])
        self.assertNotIn('/static/js/composer_compositor.js', scripts)
        # The server composes final preview; the browser schedules and paints it.
        self.assertIn('ComposerPreviewScheduler', scripts['/static/js/composer_slice.js'])
        ids = {attrs.get('id') for _, attrs in self.audit.elements}
        for identity in ('composerShell', 'composerShellMessage', 'composerShellRetry', 'composerShellUpdate', 'scenePreview'):
            self.assertIn(identity, ids)

    def test_safe_areas_survive_phone_layout_override(self) -> None:
        viewport = next(item['content'] for item in self.audit.metas if item.get('name') == 'viewport')
        self.assertIn('viewport-fit=cover', viewport)
        css = _read('web/static/css/composer_slice.css')
        for edge in ('top', 'right', 'bottom', 'left'):
            self.assertIn(f'env(safe-area-inset-{edge})', css)
        phone = css.split('@media (max-width: 760px)', 1)[1]
        rule = re.search(r'\.composer\s*\{([^}]+)\}', phone).group(1)
        self.assertNotIn('padding:', rule)
        self.assertIn('100dvh', css)


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

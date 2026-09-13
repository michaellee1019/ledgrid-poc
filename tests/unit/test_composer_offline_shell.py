"""Focused contracts for Composer's local, offline-ready shell."""

from __future__ import annotations

import unittest
import re
from pathlib import Path

from tests.unit.test_composer_looks import _PreviewManager, _WallChannel
from web.app import AnimationWebInterface, COMPOSER_SHELL_VERSION


ROOT = Path(__file__).parents[2]


class ComposerOfflineShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        self.client = self.app.app.test_client()
        self.template = (ROOT / 'web/templates/composer.html').read_text(encoding='utf-8')
        self.shell = (ROOT / 'web/static/js/composer_shell.js').read_text(encoding='utf-8')
        self.worker = (ROOT / 'web/static/composer/composer_sw.js').read_text(encoding='utf-8')
        self.slice = (ROOT / 'web/static/js/composer_slice.js').read_text(encoding='utf-8')

    def test_root_declares_the_versioned_local_manifest_and_root_worker(self) -> None:
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'v={COMPOSER_SHELL_VERSION}', response.get_data(as_text=True))
        browser_response = self.client.get('/composer')
        self.assertEqual(browser_response.status_code, 200)
        self.assertIn(f'v={COMPOSER_SHELL_VERSION}', browser_response.get_data(as_text=True))
        self.assertIn('composer/manifest.webmanifest', self.template)
        worker = self.client.get('/composer-sw.js')
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.headers['Service-Worker-Allowed'], '/')
        self.assertIn('application/javascript', worker.headers['Content-Type'])
        self.assertEqual(worker.headers['Cache-Control'], 'no-cache')
        worker.close()

    def test_worker_precaches_only_versioned_shell_assets_and_never_api_state(self) -> None:
        self.assertEqual(set(re.findall(r'composer-shell-v\d+', self.worker + self.shell)), {COMPOSER_SHELL_VERSION})
        self.assertIn(f"const CACHE_NAME = '{COMPOSER_SHELL_VERSION}'", self.worker)
        for asset in ('composer_preview_scheduler.js', 'composer_slice.js', 'composer_palette_layout.js', 'composer_shell.js', 'manifest.webmanifest', 'icon.svg', 'composer-180.png', 'offline.html'):
            self.assertIn(asset, self.worker)
        self.assertIn("url.pathname.startsWith('/api/')", self.worker)
        self.assertIn("'Cache-Control':'no-store'", self.worker)
        self.assertIn("request.mode === 'navigate'", self.worker)
        self.assertIn(f"caches.match('/static/composer/offline.html?v={COMPOSER_SHELL_VERSION}')", self.worker)
        self.assertNotIn("offline.html?v=composer-shell-v8", self.worker)
        self.assertNotIn("caches.put", self.worker)
        self.assertNotIn('/api/composer', self.worker)

    def test_offline_shell_disables_mutations_and_requires_fresh_reconnect(self) -> None:
        for selector in ('.composer button', '.composer input', '.composer select', '#scenePreview'):
            self.assertIn(selector, self.shell)
        self.assertIn("['click', 'pointerdown', 'keydown', 'input', 'change']", self.shell)
        self.assertIn("window.__composerShellUnavailable=true", self.shell)
        self.assertIn("composer-server-unavailable", self.shell)
        self.assertIn("window.location.reload()", self.shell)
        self.assertIn("hasProtectedScene()", self.shell)
        self.assertIn("let activatingUpdate = false", self.shell)
        self.assertIn("activatingUpdate=true", self.shell)
        self.assertIn("if (activatingUpdate) window.location.reload()", self.shell)
        self.assertIn("['#saveState', 'Unavailable offline']", self.shell)
        self.assertIn("Reload Composer to reconnect and fetch current local state.", self.shell)
        self.assertNotIn("Local Composer server is available", self.shell)
        self.assertNotIn("window.addEventListener('offline', setOffline)", self.shell)
        self.assertNotIn("navigator.onLine", self.shell)
        self.assertIn(f"navigator.serviceWorker.register('/composer-sw.js?v={COMPOSER_SHELL_VERSION}', {{scope:'/'}})", self.shell)
        self.assertIn("fetch(`${api}/recovery?client_id=${encodeURIComponent(clientId)}`)", self.slice)
        self.assertIn("window.dispatchEvent(new Event('composer-server-unavailable'))", self.slice)


if __name__ == '__main__':
    unittest.main()

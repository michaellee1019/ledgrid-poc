"""Focused revision-qualified local Stop contracts."""

import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _scene
from web.app import AnimationWebInterface
from web.scene_look_store import SceneLookStore


class ComposerStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel(); self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True); self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / 'looks.json'); self.client = self.interface.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_stop_is_exact_safe_idle_and_refreshable(self):
        checked = self.client.post('/api/composer/check', json=_scene()).get_json()
        self.client.post('/api/composer/activate', json={'token': checked['token'], 'basis': checked['basis'], 'idempotency_key': 'live'})
        stopped = self.client.post('/api/composer/stop', json={'basis': checked['basis']})
        self.assertEqual(stopped.status_code, 200); status = stopped.get_json()['status']
        self.assertEqual((status['state'], status['observed'], status['safe_idle']), ('stopped', checked['basis'], True))
        self.assertEqual(self.client.get('/api/composer/status').get_json()['state'], 'stopped')
        self.assertEqual(self.wall.commands, []); self.assertEqual(self.interface.composer_control.commands[-1]['action'], 'stop_scene')

    def test_stale_stop_does_not_claim_idle(self):
        response = self.client.post('/api/composer/stop', json={'basis': {'revision': 1, 'digest': '0' * 64}})
        self.assertEqual(response.status_code, 409); self.assertFalse(response.get_json()['status']['safe_idle'])
        script = Path('web/static/js/composer_slice.js').read_text(encoding='utf-8')
        self.assertIn('Retry once', script); self.assertIn('`${api}/stop`', script)

    def test_stop_rejection_and_timeout_keep_truthful_observation(self):
        checked = self.client.post('/api/composer/check', json=_scene()).get_json()
        self.client.post('/api/composer/activate', json={'token': checked['token'], 'basis': checked['basis'], 'idempotency_key': 'live'})
        with patch.object(self.interface.composer_adapter, 'accept_stop', side_effect=TimeoutError('timed out')):
            timeout = self.client.post('/api/composer/stop', json={'basis': checked['basis']})
        self.assertEqual(timeout.status_code, 504); self.assertFalse(timeout.get_json()['status']['safe_idle'])
        self.assertEqual(timeout.get_json()['status']['observed'], checked['basis'])
        with patch.object(self.interface.composer_adapter, 'accept_stop', side_effect=ValueError('rejected')):
            rejected = self.client.post('/api/composer/stop', json={'basis': checked['basis']})
        self.assertEqual(rejected.status_code, 409); self.assertFalse(rejected.get_json()['status']['safe_idle'])

    def test_stop_leaves_draft_look_and_preview_basis_unchanged(self):
        draft = _scene(); preview = self.client.post('/api/composer/preview', json={**draft, 'preview': {'monotonic_elapsed': 1, 'wall_time': '2026-08-31T00:00:00+00:00'}}).get_json()['basis']
        saved = self.client.post('/api/composer/looks', json={'name': 'Keep', 'draft': draft}).get_json()['look']
        checked = self.client.post('/api/composer/check', json=draft).get_json(); self.client.post('/api/composer/activate', json={'token': checked['token'], 'basis': checked['basis'], 'idempotency_key': 'live'})
        self.client.post('/api/composer/stop', json={'basis': checked['basis']})
        self.assertEqual(self.client.get(f"/api/composer/looks/{saved['id']}").get_json()['look']['basis'], saved['basis'])
        self.assertEqual(self.client.post('/api/composer/preview', json={**draft, 'preview': {'monotonic_elapsed': 1, 'wall_time': '2026-08-31T00:00:00+00:00'}}).get_json()['basis'], preview)

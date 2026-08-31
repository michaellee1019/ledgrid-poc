"""Focused current-Schema look persistence checks."""

import tempfile
import unittest
from pathlib import Path

from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _conway, _overlay, _scene
from web.app import AnimationWebInterface
from web.scene_look_store import SceneLookStore


class ComposerLooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / "looks.json")
        self.client = self.interface.app.test_client()

    def tearDown(self): self.tmp.cleanup()

    def test_save_reload_open_duplicate_rename_delete_without_activation(self):
        draft = _scene([_conway(), _overlay("clock_upper", {"show_seconds": True})])
        saved = self.client.post('/api/composer/looks', json={'name': 'Garden', 'draft': draft}).get_json()['look']
        self.assertEqual(self.client.get('/api/composer/looks').get_json()['looks'][0]['name'], 'Garden')
        reloaded = SceneLookStore(Path(self.tmp.name) / "looks.json")
        self.assertEqual(reloaded.get(saved['id'])['basis'], saved['basis'])
        opened = self.client.get(f"/api/composer/looks/{saved['id']}").get_json()['look']
        self.assertEqual(opened['basis'], saved['basis']); self.assertEqual([o['slot_id'] for o in opened['scene']['overlays']], ['conway_lower', 'clock_upper'])
        copied = self.client.post(f"/api/composer/looks/{saved['id']}/duplicate", json={'name': 'Garden copy'}).get_json()['look']
        self.assertNotEqual(copied['id'], saved['id']); self.assertEqual(copied['basis'], saved['basis'])
        self.assertEqual(self.client.patch(f"/api/composer/looks/{copied['id']}", json={'name': 'Night'}).status_code, 200)
        renamed = reloaded.get(copied['id'])
        self.assertEqual((renamed['name'], renamed['basis'], renamed['scene']), ('Night', copied['basis'], copied['scene']))
        self.assertEqual(self.client.delete(f"/api/composer/looks/{saved['id']}").status_code, 200)
        self.assertEqual([look['id'] for look in reloaded.list()], [copied['id']])
        self.assertEqual(self.wall.commands, []); self.assertEqual(self.interface.composer_control.commands, [])

    def test_bad_or_old_records_reject_without_control_side_effect(self):
        Path(self.tmp.name, 'looks.json').write_text('{"schema":"old","looks":[]}', encoding='utf-8')
        response = self.client.get('/api/composer/looks')
        self.assertEqual(response.status_code, 400); self.assertIn('unsupported', response.get_json()['error'])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_open_source_resets_visible_draft_state_once_without_erasing_observed(self):
        script = Path('web/static/js/composer_slice.js').read_text(encoding='utf-8')
        self.assertIn("function markDraftLocal()", script)
        self.assertIn("markDraftLocal(); render(); const preview=await queuePreview()", script)
        self.assertNotIn("markDraftLocal(); queuePreview(); render(); const preview=await queuePreview()", script)

    def test_look_actions_are_inline_and_delete_confirms_the_exact_id(self):
        script = Path('web/static/js/composer_slice.js').read_text(encoding='utf-8')
        self.assertNotIn('window.prompt', script)
        self.assertNotIn('window.confirm', script)
        self.assertIn("state.deleteLookId !== look.id", script)
        self.assertIn("state.deleteLookId = look.id", script)
        self.assertIn("copyName(look.name)", script)

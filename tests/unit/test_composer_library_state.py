"""Focused local Composer library persistence and projection contracts."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.unit.test_composer_looks import _PreviewManager, _WallChannel, _scene
from web.app import AnimationWebInterface
from web.composer_library_state import ComposerLibraryState, ComposerLibraryStateError
from web.scene_look_store import SceneLookStore
from web.working_draft_store import WorkingDraftStore


class ComposerLibraryStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'library.json'

    def tearDown(self):
        self.tmp.cleanup()

    def test_favorites_are_typed_persistent_and_recents_are_bounded_unique(self):
        state = ComposerLibraryState(self.path)
        state.favorite({'kind': 'starter', 'id': 'aurora'})
        for index in range(10):
            state.revisit({'kind': 'look', 'id': str(index)})
        state.revisit({'kind': 'look', 'id': '4'})
        reloaded = ComposerLibraryState(self.path).get()
        self.assertEqual(reloaded['favorites'], [{'kind': 'starter', 'id': 'aurora'}])
        self.assertEqual(reloaded['recents'][0], {'kind': 'look', 'id': '4'})
        self.assertEqual(len(reloaded['recents']), 8)
        self.assertEqual(len({(item['kind'], item['id']) for item in reloaded['recents']}), 8)

    def test_corrupt_or_old_state_does_not_overwrite_valid_file(self):
        self.path.write_text(json.dumps({'schema': 'old', 'favorites': [], 'recents': []}), encoding='utf-8')
        state = ComposerLibraryState(self.path)
        with self.assertRaisesRegex(ComposerLibraryStateError, 'not current'):
            state.favorite({'kind': 'starter', 'id': 'aurora'})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['schema'], 'old')
        self.path.write_text(json.dumps({'schema': state._SCHEMA, 'favorites': [{'kind': 'starter', 'id': 'aurora'}, {'kind': 'starter', 'id': 'aurora'}], 'recents': []}), encoding='utf-8')
        with self.assertRaisesRegex(ComposerLibraryStateError, 'duplicate'):
            state.get()

    def test_projection_uses_current_names_and_hides_deleted_look_references(self):
        state = ComposerLibraryState(self.path)
        state.favorite({'kind': 'look', 'id': 'gone'})
        state.revisit({'kind': 'starter', 'id': 'aurora'})
        projected = state.project(state.get(), [{'kind': 'starter', 'id': 'aurora', 'name': 'Aurora only'}])
        self.assertEqual(projected['favorites'], [])
        self.assertEqual(projected['recents'][0]['name'], 'Aurora only')


class ComposerLibraryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / 'looks.json')
        self.interface.composer_library = ComposerLibraryState(Path(self.tmp.name) / 'library.json')
        self.interface.working_draft = WorkingDraftStore(Path(self.tmp.name) / 'draft.json')
        self.client = self.interface.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_library_has_exact_starters_searchable_names_and_prunes_deleted_look(self):
        first = self.client.get('/api/composer/library').get_json()
        self.assertEqual([(item['kind'], item['id']) for item in first['items']], [
            ('starter', 'aurora'), ('starter', 'aurora_clock'), ('starter', 'aurora_conway'), ('starter', 'aurora_conway_clock'),
            ('starter', 'human_conway_chaos'), ('starter', 'human_fancy_coral'),
            ('starter', 'human_neon_microverse'), ('starter', 'human_twilight_sparkle'),
            ('starter', 'human_avalanche_factory'),
        ])
        scene = self.client.get('/api/composer/starters/aurora').get_json()['starter']['scene']
        saved = self.client.post('/api/composer/looks', json={'name': 'Evening Garden', 'scene': scene}).get_json()['look']
        favorite = self.client.post('/api/composer/library/favorites', json={'reference': {'kind': 'look', 'id': saved['id']}})
        self.assertEqual(favorite.status_code, 200)
        recent = self.client.post('/api/composer/library/recents', json={'reference': {'kind': 'look', 'id': saved['id']}})
        self.assertEqual(recent.status_code, 200)
        self.assertEqual(self.client.patch(f"/api/composer/looks/{saved['id']}", json={'name': 'Night Garden'}).status_code, 200)
        renamed = self.client.get('/api/composer/library').get_json()
        self.assertEqual([item['name'] for item in renamed['favorites']], ['Night Garden'])
        self.assertEqual(self.client.delete(f"/api/composer/looks/{saved['id']}").status_code, 200)
        pruned = self.client.get('/api/composer/library').get_json()
        self.assertEqual((pruned['favorites'], pruned['recents']), ([], []))
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_invalid_library_state_rejects_without_changing_draft_look_or_live_observation(self):
        saved = self.client.post('/api/composer/looks', json={'name': 'Keep', 'draft': _scene()}).get_json()['look']
        checked = self.client.post('/api/composer/check', json=_scene()).get_json()
        self.client.post('/api/composer/activate', json={'token': checked['token'], 'basis': checked['basis'], 'idempotency_key': 'library-proof'})
        before = self.client.get('/api/composer/status').get_json()
        self.interface.composer_library.path.write_text('{"schema":"old","favorites":[],"recents":[]}', encoding='utf-8')
        preflight = self.client.post('/api/composer/library/preflight', json={'reference': {'kind': 'look', 'id': saved['id']}})
        self.assertEqual(preflight.status_code, 400)
        self.assertEqual(self.client.get('/api/composer/status').get_json(), before)
        rejected = self.client.post('/api/composer/library/favorites', json={'reference': {'kind': 'look', 'id': saved['id']}})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.client.delete(f"/api/composer/looks/{saved['id']}").status_code, 400)
        self.assertEqual(self.client.get(f"/api/composer/looks/{saved['id']}").status_code, 200)
        self.assertEqual(self.client.get('/api/composer/status').get_json(), before)

    def test_client_declares_one_local_library_and_explicit_recent_actions(self):
        html = Path('web/templates/composer.html').read_text(encoding='utf-8')
        script = Path('web/static/js/composer_slice.js').read_text(encoding='utf-8')
        self.assertIn('id="librarySearch"', html)
        self.assertIn('data-library-filter="favorites"', html)
        self.assertIn('function filteredLibrary()', script)
        self.assertIn("item.name.toLocaleLowerCase().includes(query)", script)
        self.assertIn("await recordRecent(item);", script)
        self.assertIn("if (state.reference) await recordRecent(state.reference);", script)
        self.assertIn("state.library.recents.map((reference) => state.library.items.find", script)
        self.assertIn("item.favorite ? 'Remove favorite' : 'Favorite'", script)
        self.assertIn("'librarySearch'", script)
        self.assertLess(script.index("'librarySearch'"), script.index("input.addEventListener('change', edit)"))
        self.assertLess(script.index('async function openLibraryItem(item) {\n    await preflightLibrary(item);'), script.index('const value=(await starters'))
        go_live = script.index('async function goLive()')
        self.assertLess(script.index('if (state.reference) await preflightLibrary(state.reference);', go_live), script.index('if (!state.checked)', go_live))
        self.assertNotIn('window.prompt', script)


if __name__ == '__main__':
    unittest.main()

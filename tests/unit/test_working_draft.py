"""Focused atomic recovery contracts for Composer's one working draft."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tests.unit.test_composer_looks import _PreviewManager, _WallChannel, _scene
from web.app import AnimationWebInterface
from web.scene_look_store import SceneLookStore
from web.working_draft_store import WorkingDraftStore


class WorkingDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel()
        self.app = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.app.working_draft = WorkingDraftStore(Path(self.tmp.name) / 'draft.json')
        self.app.composer_looks = SceneLookStore(Path(self.tmp.name) / 'looks.json')
        self.client = self.app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _saved_look_reference(self):
        saved = self.client.post('/api/composer/looks', json={'name': 'Basis', 'draft': _scene()}).get_json()['look']
        return saved, {'kind': 'look', 'id': saved['id'], 'basis': saved['basis']}

    def _starter_reference(self, starter_id='aurora_clock', vibe='vivid'):
        starter = self.client.get(f'/api/composer/starters/{starter_id}').get_json()['starter']
        baseline = _scene(starter['overlays'])
        baseline['scene']['vibe'] = vibe
        baseline['scene']['background'] = starter['background']
        basis = self.client.post('/api/composer/preview', json=baseline).get_json()['basis']
        return baseline, {'kind': 'starter', 'id': starter_id, 'basis': basis, 'baseline': baseline}

    def test_valid_previewed_draft_is_atomic_canonical_and_invalid_update_keeps_it(self):
        saved, reference = self._saved_look_reference()
        changed = _scene()
        changed['scene']['background']['parameters']['seed'] = 77
        response = self.client.post('/api/composer/draft', json={'draft': changed, 'reference': reference})
        self.assertEqual(response.status_code, 200)
        record = response.get_json()['draft']
        self.assertEqual(record['reference'], reference)
        self.assertEqual(record['draft']['scene']['vibe_source'], 'quiet')
        self.assertEqual(WorkingDraftStore(Path(self.tmp.name) / 'draft.json').get()['basis'], record['basis'])
        bad = self.client.post('/api/composer/draft', json={'draft': {'origin': 'bad'}, 'reference': reference})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get('/api/composer/draft').get_json()['draft']['basis'], record['basis'])
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.app.composer_control.commands, [])
        self.assertEqual(self.client.get(f"/api/composer/looks/{saved['id']}").status_code, 200)

    def test_exact_saved_basis_clears_recovery_and_reference_must_match_current_look(self):
        saved, reference = self._saved_look_reference()
        exact = self.client.post('/api/composer/draft', json={'draft': _scene(), 'reference': reference})
        self.assertEqual(exact.status_code, 200)
        self.assertIsNone(exact.get_json()['draft'])
        self.assertIsNone(self.client.get('/api/composer/draft').get_json()['draft'])
        wrong = {**reference, 'basis': {'revision': 1, 'digest': '0' * 64}}
        rejected = self.client.post('/api/composer/draft', json={'draft': _scene(), 'reference': wrong})
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('no longer matches', rejected.get_json()['error'])
        self.assertEqual(self.client.get(f"/api/composer/looks/{saved['id']}").status_code, 200)

    def test_vivid_starter_baseline_round_trips_and_remains_exact_for_discard(self):
        baseline, reference = self._starter_reference()
        changed = deepcopy(baseline)
        changed['scene']['background']['parameters']['glow_intensity'] = 0.91
        response = self.client.post('/api/composer/draft', json={'draft': changed, 'reference': reference})
        self.assertEqual(response.status_code, 200)
        record = response.get_json()['draft']
        self.assertEqual(record['reference']['basis'], reference['basis'])
        self.assertEqual(record['reference']['baseline']['scene']['vibe_source'], 'vivid')
        self.assertEqual(self.app._composer_working_draft(record['reference']['baseline']).identity.to_dict(), reference['basis'])
        self.assertEqual(self.client.get('/api/composer/draft').get_json()['draft']['reference'], record['reference'])
        bad = deepcopy(reference)
        bad['baseline']['scene']['background']['parameters']['seed'] = 999
        self.assertEqual(self.client.post('/api/composer/draft', json={'draft': changed, 'reference': bad}).status_code, 400)

    def test_deleted_basis_still_offers_valid_restore_but_corrupt_or_unknown_data_only_discards(self):
        saved, reference = self._saved_look_reference()
        changed = _scene()
        changed['scene']['background']['parameters']['seed'] = 88
        self.assertEqual(self.client.post('/api/composer/draft', json={'draft': changed, 'reference': reference}).status_code, 200)
        checked = self.client.post('/api/composer/check', json=_scene()).get_json()
        self.client.post('/api/composer/activate', json={'token': checked['token'], 'basis': checked['basis'], 'idempotency_key': 'live'})
        self.client.post('/api/composer/stop', json={'basis': checked['basis']})
        before = self.client.get('/api/composer/status').get_json()
        command_count = len(self.app.composer_control.commands)
        self.assertEqual(self.client.get('/api/composer/draft').status_code, 200)
        self.assertEqual(self.client.get('/api/composer/status').get_json(), before)
        self.assertEqual(len(self.app.composer_control.commands), command_count)
        self.assertEqual(self.client.delete(f"/api/composer/looks/{saved['id']}").status_code, 200)
        recovered = self.client.get('/api/composer/draft')
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.get_json()['draft']['reference'], reference)
        path = Path(self.tmp.name) / 'draft.json'
        path.write_text(json.dumps({'schema': 'old', 'draft': {}, 'basis': {}, 'reference': {}, 'saved_at': 1}), encoding='utf-8')
        old = self.client.get('/api/composer/draft')
        self.assertEqual(old.status_code, 400)
        self.assertIn('not current', old.get_json()['error'])
        self.assertEqual(self.client.delete('/api/composer/draft').status_code, 200)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(len(self.app.composer_control.commands), command_count)

    def test_client_has_one_starter_handler_and_non_modal_recovery_lifecycle(self):
        script = Path('web/static/js/composer_slice.js').read_text(encoding='utf-8')
        html = Path('web/templates/composer.html').read_text(encoding='utf-8')
        self.assertEqual(script.count('function renderStarters('), 1)
        self.assertIn('function renderStarterChoices', script)
        self.assertIn('sameBasis(body.basis, state.reference.basis)', script)
        self.assertIn("setUnsaved(true)", script)
        self.assertIn("state.recovery.draft", script)
        self.assertIn("starterDraft(value, true)", script)
        self.assertIn("recoveryDraft(ref.baseline)", script)
        self.assertIn("state.recovery ? await discardTarget(state.recovery) : await auroraFallback()", script)
        self.assertIn("async function auroraFallback()", script)
        self.assertIn("if(scene.vibe){$('#vibe').value=scene.vibe;setVibeDefaults();}", script)
        self.assertNotIn('window.prompt', script)
        self.assertNotIn('window.confirm', script)
        self.assertIn('id="draftState"', html)

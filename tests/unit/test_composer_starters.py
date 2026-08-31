import unittest
from pathlib import Path
from unittest.mock import patch

from tests.unit.test_composer_looks import ComposerLooksTests, _scene


class ComposerStarterTests(ComposerLooksTests):
    def test_exact_four_current_starters_and_remix_is_independent(self):
        starters = self.client.get('/api/composer/starters').get_json()['starters']
        self.assertEqual([item['id'] for item in starters], ['aurora', 'aurora_clock', 'aurora_conway', 'aurora_conway_clock'])
        detail = self.client.get('/api/composer/starters/aurora_conway_clock').get_json()['starter']
        self.assertEqual([item['slot_id'] for item in detail['overlays']], ['conway_lower', 'clock_upper'])
        self.assertEqual(detail['overlays'][0]['component']['parameters']['seed'], 404)
        self.assertEqual(detail['overlays'][1]['component']['parameters']['color'], [80, 220, 255])
        remix = self.client.post('/api/composer/starters/aurora_conway_clock/remix', json={'name': 'Mine', 'draft': _scene(detail['overlays'])})
        self.assertEqual(remix.status_code, 200); self.assertEqual(self.client.get('/api/composer/starters/unknown').status_code, 400)
        self.assertEqual(self.client.get('/api/composer/looks').get_json()['looks'][0]['name'], 'Mine')

    def test_invalid_starter_is_rejected_before_any_mutation(self):
        bad = {'id': 'bad', 'name': 'Bad', 'background': {'component_id': 'unknown', 'version': 1, 'provider': 'python', 'role': 'background', 'parameters': {}}, 'overlays': []}
        with patch('web.app.get_starter', return_value=bad):
            response = self.client.get('/api/composer/starters/bad')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/composer/looks').get_json()['looks'], [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_client_has_local_browse_and_no_modal(self):
        script = Path('web/static/js/composer_slice.js').read_text()
        self.assertIn('renderStarters', script); self.assertIn('id="remixStarter"', Path('web/templates/composer.html').read_text())
        self.assertIn("$('#remixStarter').addEventListener('click'", script); self.assertNotIn("$('#remixName').addEventListener('change'", script)
        self.assertIn('const candidate=draft()', script); self.assertIn('await validatePreview(candidate)', script)
        self.assertIn('validatePreview(candidate)', script); self.assertIn('commitPreview(preview)', script)
        self.assertIn('state.background={seed:p.seed,source_fps:p.source_fps}', script)
        self.assertIn('source_fps: state.background.source_fps, seed: state.background.seed', script)
        self.assertIn('const generation=++state.previewGeneration', script)
        self.assertIn('if (generation !== state.previewGeneration) return', script)
        self.assertGreaterEqual(script.count('state.background={seed:p.seed,source_fps:p.source_fps}'), 2)
        self.assertNotIn('window.prompt', script); self.assertNotIn('window.confirm', script)

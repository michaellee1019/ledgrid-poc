import unittest
from pathlib import Path
from unittest.mock import patch

from tests.unit.test_composer_looks import ComposerLooksTests, _scene
from web.starter_looks import get_starter, list_starters


class ComposerStarterTests(ComposerLooksTests):
    def test_exact_four_current_starters_and_remix_is_independent(self):
        starters = self.client.get('/api/composer/starters').get_json()['starters']
        self.assertEqual([item['id'] for item in starters], ['aurora', 'aurora_clock', 'aurora_conway', 'aurora_conway_clock'])
        detail = self.client.get('/api/composer/starters/aurora_conway_clock').get_json()['starter']
        self.assertEqual(detail['name'], 'Fern Gully Cup')
        self.assertEqual(detail['scene']['animation']['component_id'], 'canopy_cup')
        self.assertEqual(detail['scene']['animation']['parameters']['seed'], 1107)
        self.assertEqual(detail['scene']['widgets'][0]['component']['parameters']['color'], [190, 255, 190])
        remix = self.client.post('/api/composer/starters/aurora_conway_clock/remix', json={
            'name': 'Mine', 'draft': {'origin': 'composer', 'scene': detail['scene']},
        })
        self.assertEqual(remix.status_code, 200); self.assertEqual(self.client.get('/api/composer/starters/unknown').status_code, 400)
        self.assertEqual(self.client.get('/api/composer/looks').get_json()['looks'][0]['name'], 'Mine')

    def test_starters_cover_distinct_scene_families_without_output_or_calibration_state(self):
        scenes = [get_starter(item['id'])['scene'] for item in list_starters()]
        self.assertEqual(
            [scene['animation']['component_id'] for scene in scenes],
            ['aurora_curtains', 'firefly_synchrony', 'fireworks', 'canopy_cup'],
        )
        self.assertEqual([len(scene['widgets']) for scene in scenes], [0, 1, 0, 1])
        self.assertEqual([scene['look']['palette_id'] for scene in scenes], ['mist', 'ember', 'spectrum', 'neutral'])
        self.assertEqual([scene['background']['parameters']['seed'] for scene in scenes], [4201, 12107, 808, 1107])
        self.assertEqual(scenes[1]['widgets'][0]['component']['parameters']['clock_offset_minutes'], 0)
        self.assertEqual(scenes[3]['widgets'][0]['component']['parameters']['clock_offset_minutes'], 60)

        def keys(value):
            if isinstance(value, dict):
                return {*value, *(key for item in value.values() for key in keys(item))}
            if isinstance(value, list):
                return {key for item in value for key in keys(item)}
            return set()

        self.assertFalse({'output_power', 'calibration', 'geometry'} & keys(scenes))

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
        self.assertIn('const candidate=starterDraft(value, true)', script); self.assertIn('await validatePreview(candidate)', script)
        self.assertIn('validatePreview(candidate)', script); self.assertIn('commitPreview(preview)', script)
        self.assertIn('state.background={seed:p.seed,source_fps:p.source_fps}', script)
        self.assertIn('source_fps: state.background.source_fps, seed: state.background.seed', script)
        self.assertIn('const generation=++state.previewGeneration', script)
        self.assertIn('if (generation !== state.previewGeneration) return', script)
        self.assertIn('setReference(reference(\'starter\'', script)
        self.assertNotIn('window.prompt', script); self.assertNotIn('window.confirm', script)

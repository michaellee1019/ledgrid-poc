"""Current media selection, local controls and exact Composer activation."""
import unittest

from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface


class MediaComposerTests(unittest.TestCase):
    def test_retained_media_presets_render_through_host_preview(self):
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        response = client.get('/api/composer/components/gif_animation/presets')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()['presets']), 32)
        for preset in response.get_json()['presets']:
            with self.subTest(preset=preset['preset_id']):
                scene = _current_scene()
                scene['animation'] = dict(component_id='gif_animation', version=1, provider='python',
                                          role='animation', parameters=preset['parameters'])
                response = client.post('/api/composer/preview', json={'origin':'composer', 'scene':scene})
                self.assertEqual(response.status_code, 200, response.get_json())

    def test_catalog_media_defaults_and_remix_preserve_scene(self):
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        gallery = client.get('/api/composer/gallery').get_json()
        media = next(entry for entry in gallery['entries'] if entry['component_id'] == 'gif_animation')
        public = next(entry for entry in gallery['components'] if entry['component_id'] == 'gif_animation')
        schema = media['parameter_schema']
        self.assertEqual(schema, public['parameter_schema'])
        self.assertIn('gif_name', media['preferred_parameters'])
        self.assertIn(media['parameters']['gif_name'], schema['gif_name']['options'])
        self.assertEqual(schema['playback_speed']['type'], 'float')
        self.assertEqual(schema['flip_y']['type'], 'bool')

        scene = _current_scene()
        scene['background']['parameters']['seed'] = 8012
        scene['plants']['effects'] = {'version': 1, 'active': ['shadow'], 'strengths': {'shadow': .35}}
        scene['animation'] = {key: media[key] for key in ('component_id', 'provider', 'version', 'role', 'parameters')}
        parameters = {**media['parameters'], 'playback_speed': 2, 'flip_y': not media['parameters']['flip_y']}
        scene['animation']['parameters'] = parameters
        result = client.post('/api/composer/scene', json={'origin': 'composer', 'scene': scene})
        self.assertEqual(result.status_code, 200, result.get_json())
        recovery = client.get('/api/composer/recovery').get_json()['recovery']['scene']
        self.assertEqual(recovery, scene)

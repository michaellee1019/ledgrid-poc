"""Tests for disk-backed per-animation presets."""

import json
import tempfile
import unittest
from pathlib import Path

from web.app import AnimationWebInterface


class _Controller:
    strip_count = 1
    leds_per_strip = 1
    total_leds = 1


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def list_animations(self):
        return []

    def get_animation_info(self, _name):
        return None


class _RecordingChannel:
    def __init__(self):
        self.commands = []

    def send_command(self, action, **data):
        command = {'action': action, 'data': data}
        self.commands.append(command)
        return command

    def read_status(self):
        return None


class AnimationPresetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.channel = _RecordingChannel()
        self.interface = AnimationWebInterface(self.channel, _PreviewManager())
        self.interface.animation_presets_dir = Path(self.temp_dir.name)
        self.client = self.interface.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multiple_presets_are_scoped_to_one_animation(self):
        for name, speed in [('Calm', 0.5), ('Fast', 2.0)]:
            response = self.client.post(
                '/api/animations/sparkle/presets',
                json={'name': name, 'params': {'speed': speed}},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.get('/api/animations/sparkle/presets')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {preset['name'] for preset in response.get_json()['presets']},
            {'Calm', 'Fast'},
        )
        self.assertEqual(
            self.client.get('/api/animations/rainbow/presets').get_json()['presets'],
            [],
        )

    def test_apply_rereads_modified_json_from_disk(self):
        response = self.client.post(
            '/api/animations/sparkle/presets',
            json={'name': 'Evening', 'params': {'brightness': 0.4}},
        )
        self.assertEqual(response.status_code, 200)

        # List once before changing the file to catch accidental startup/request caching.
        self.client.get('/api/animations/sparkle/presets')
        path = Path(self.temp_dir.name) / 'sparkle' / 'evening.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['params']['brightness'] = 0.9
        path.write_text(json.dumps(payload), encoding='utf-8')

        response = self.client.post('/api/animations/sparkle/presets/evening/apply')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.channel.commands[-1], {
            'action': 'start',
            'data': {'animation': 'sparkle', 'config': {'brightness': 0.9}},
        })

    def test_list_sorts_mixed_runtime_and_curated_timestamps(self):
        preset_dir = Path(self.temp_dir.name) / 'conway_life'
        preset_dir.mkdir()
        payloads = [
            {
                'preset_id': 'runtime',
                'name': 'Runtime',
                'animation': 'conway_life',
                'params': {},
                'updated_at': 1784606199.0,
            },
            {
                'preset_id': 'curated',
                'name': 'Curated',
                'animation': 'conway_life',
                'params': {},
                'updated_at': '2026-07-20T00:00:00Z',
            },
        ]
        for payload in payloads:
            path = preset_dir / f"{payload['preset_id']}.json"
            path.write_text(json.dumps(payload), encoding='utf-8')

        response = self.client.get('/api/animations/conway_life/presets')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [preset['preset_id'] for preset in response.get_json()['presets']],
            ['runtime', 'curated'],
        )

    def test_overwrite_preserves_created_time_and_delete_removes_file(self):
        first = self.client.post(
            '/api/animations/rainbow/presets',
            json={'name': 'Desk', 'params': {'speed': 1}},
        ).get_json()['preset']
        second = self.client.post(
            '/api/animations/rainbow/presets',
            json={'name': 'Desk', 'params': {'speed': 3}},
        ).get_json()['preset']
        self.assertEqual(first['created_at'], second['created_at'])

        response = self.client.delete('/api/animations/rainbow/presets/desk')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get('/api/animations/rainbow/presets').get_json()['presets'],
            [],
        )

    def test_save_rejects_non_object_params(self):
        response = self.client.post(
            '/api/animations/sparkle/presets',
            json={'name': 'Bad', 'params': ['not', 'an', 'object']},
        )
        self.assertEqual(response.status_code, 400)

    def test_version_two_metadata_is_returned_in_list_summary(self):
        response = self.client.post(
            '/api/animations/sparkle/presets',
            json={
                'name': 'Gallery', 'params': {'brightness': 0.5},
                'category': 'Installation', 'description': 'Warm gallery light',
                'tags': ['warm'],
                'palette': {'name': 'Amber', 'colors': ['#FFAA22']},
            },
        )
        self.assertEqual(response.status_code, 200)
        preset = self.client.get('/api/animations/sparkle/presets').get_json()['presets'][0]
        self.assertEqual(preset['version'], 2)
        self.assertEqual(preset['category'], 'Installation')
        self.assertEqual(preset['palette']['colors'], ['#FFAA22'])

    def test_numeric_and_iso_timestamp_presets_sort_together(self):
        preset_dir = Path(self.temp_dir.name) / 'sparkle'
        preset_dir.mkdir(parents=True)
        (preset_dir / 'shipped.json').write_text(json.dumps({
            'version': 2, 'preset_id': 'shipped', 'name': 'Shipped',
            'animation': 'sparkle', 'updated_at': '2026-07-20T00:00:00Z',
            'params': {'brightness': 0.4},
        }), encoding='utf-8')
        self.client.post('/api/animations/sparkle/presets', json={
            'name': 'Personal', 'params': {'brightness': 0.7},
        })
        response = self.client.get('/api/animations/sparkle/presets')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()['presets']), 2)


if __name__ == '__main__':
    unittest.main()

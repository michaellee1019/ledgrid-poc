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


if __name__ == '__main__':
    unittest.main()

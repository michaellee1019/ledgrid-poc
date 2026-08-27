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

    def get_animation_info(self, name):
        if name not in {'sparkle', 'rainbow', 'conway_life'}:
            return None
        return {
            'parameters': {
                'speed': {'type': 'float', 'min': 0.1, 'max': 5.0},
                'brightness': {'type': 'float', 'min': 0.0, 'max': 1.0},
                'base_red': {'type': 'int', 'min': 0, 'max': 255},
                'base_green': {'type': 'int', 'min': 0, 'max': 255},
                'base_blue': {'type': 'int', 'min': 0, 'max': 255},
                'mode': {'type': 'str', 'options': ['calm', 'active']},
                'enabled': {'type': 'bool'},
                'plant_aware': {'type': 'bool'},
            },
        }


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
        self.assertEqual(self.channel.commands, [])
        preset_path = Path(self.temp_dir.name) / 'sparkle' / 'calm.json'
        self.assertNotIn('plant_aware', json.loads(preset_path.read_text())['params'])

    def test_get_preset_returns_the_direct_preset_payload(self):
        self.client.post(
            '/api/animations/sparkle/presets',
            json={'name': 'Calm', 'params': {'speed': 0.5}},
        )

        response = self.client.get('/api/animations/sparkle/presets/calm')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['preset_id'], 'calm')
        self.assertEqual(payload['params'], {'speed': 0.5})
        self.assertNotIn('preset', payload)

    def test_apply_alias_is_guarded_even_when_preset_changes_on_disk(self):
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

        before = list(self.channel.commands)
        response = self.client.post('/api/animations/sparkle/presets/evening/apply')
        self.assertEqual(response.status_code, 428)
        self.assertEqual(response.get_json()['code'], 'guarded_activation_required')
        self.assertEqual(self.channel.commands, before)

    def test_output_brightness_endpoint_validates_hardware_range(self):
        response = self.client.post(
            '/api/config/brightness', json={'brightness': 128}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['brightness'], 128)
        self.assertEqual(self.channel.commands[-1], {
            'action': 'set_output_brightness',
            'data': {'brightness': 128},
        })

        for invalid in (-1, 256, 12.5, True, '128', None):
            with self.subTest(brightness=invalid):
                command_count = len(self.channel.commands)
                response = self.client.post(
                    '/api/config/brightness', json={'brightness': invalid}
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(len(self.channel.commands), command_count)

    def test_device_state_preserves_operations_but_guards_execution(self):
        preset_dir = Path(self.temp_dir.name) / 'sparkle'
        preset_dir.mkdir()
        (preset_dir / 'evening.json').write_text(json.dumps({
            'preset_id': 'evening',
            'name': 'Evening',
            'animation': 'sparkle',
            'params': {'brightness': 0.4, 'speed': 0.6},
        }), encoding='utf-8')

        guarded = self.client.post('/api/device/state', json={
            'power': True,
            'brightness': 72,
            'animation': 'sparkle',
            'preset': 'evening',
        })
        self.assertEqual(guarded.status_code, 428)
        self.assertEqual(guarded.get_json()['code'], 'guarded_activation_required')
        self.assertEqual(self.channel.commands, [])

        response = self.client.post('/api/device/state', json={
            'power': True, 'brightness': 72,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.channel.commands), 1)
        self.assertEqual(self.channel.commands[0], {
            'action': 'set_device_state',
            'data': {'power': True, 'brightness': 72},
        })

    def test_device_state_rejects_conflicts_before_writing_command(self):
        invalid_payloads = (
            {},
            {'power': 'on'},
            {'brightness': 300},
            {'animation': 'sparkle', 'power': False},
            {'preset': 'evening'},
            {'power': True, 'unknown': 1},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post('/api/device/state', json=payload)
                expected = 428 if {'animation', 'preset'} & set(payload) else 400
                self.assertEqual(response.status_code, expected)
        self.assertEqual(self.channel.commands, [])

    def test_scene_only_overlay_is_rejected_by_legacy_start_and_device_routes(self):
        started = self.client.post('/api/start/clock_overlay', json={})
        device = self.client.post('/api/device/state', json={
            'power': True, 'animation': 'clock_overlay',
        })

        self.assertEqual(started.status_code, 428)
        self.assertEqual(device.status_code, 428)
        self.assertEqual(self.channel.commands, [])

    def test_list_alphabetizes_presets_with_mixed_timestamp_formats(self):
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
            ['curated', 'runtime'],
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

    def test_save_rejects_unknown_animation_and_invalid_schema_values(self):
        cases = (
            ('missing', {'speed': 1.0}, 'Unknown animation'),
            ('sparkle', {'mystery': 1}, 'Unsupported parameter'),
            ('sparkle', {'base_red': 1.5}, 'must be int'),
            ('sparkle', {'brightness': 1.5}, 'at most 1.0'),
            ('sparkle', {'mode': 'turbo'}, 'must be one of'),
            ('sparkle', {'speed': float('inf')}, 'must be finite'),
            ('sparkle', {'speed': 10 ** 1000}, 'must be finite'),
        )
        for animation, params, message in cases:
            with self.subTest(animation=animation, params=params):
                response = self.client.post(
                    f'/api/animations/{animation}/presets',
                    json={'name': 'Invalid', 'params': params},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.get_json()['error'])

    def test_dashboard_reads_each_preset_only_once(self):
        self.interface.preview_manager.list_animations = lambda: [{
            'plugin_name': 'sparkle', 'name': 'Sparkle',
            'description': 'Twinkling points', 'author': 'Test', 'version': '1',
        }]
        for name in ('First', 'Second'):
            self.client.post('/api/animations/sparkle/presets', json={
                'name': name,
                'params': {'base_red': 1, 'base_green': 2, 'base_blue': 3},
            })
        original_read = self.interface._read_json_file
        reads = []

        def recording_read(path):
            reads.append(path)
            return original_read(path)

        self.interface._read_json_file = recording_read

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(reads), 2)

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

    def test_global_speed_control_sends_scaled_controller_command(self):
        response = self.client.post(
            '/api/config/animation-speed', json={'multiplier': 1.5}
        )

        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.get_json()['animation_speed_scale'], 0.45)
        self.assertEqual(self.channel.commands[-1]['action'], 'set_animation_speed_scale')
        self.assertAlmostEqual(
            self.channel.commands[-1]['data']['animation_speed_scale'], 0.45
        )

    def test_global_speed_control_has_no_artificial_upper_limit(self):
        response = self.client.post(
            '/api/config/animation-speed', json={'multiplier': 25}
        )

        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(
            self.channel.commands[-1]['data']['animation_speed_scale'], 7.5
        )

    def test_global_speed_control_rejects_non_positive_values(self):
        response = self.client.post(
            '/api/config/animation-speed', json={'multiplier': 0}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.channel.commands, [])

    def test_global_plant_aware_control_requires_boolean(self):
        response = self.client.post('/api/config/plant-aware', json={'plant_aware': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.channel.commands[-1], {
            'action': 'set_plant_aware', 'data': {'plant_aware': False},
        })

        response = self.client.post('/api/config/plant-aware', json={'plant_aware': 'yes'})
        self.assertEqual(response.status_code, 400)

    def test_composable_plant_modifier_control_validates_and_sends_canonical_state(self):
        response = self.client.post('/api/config/plant-modifiers', json={
            'plant_modifiers': {
                'active': ['obstacle', 'illuminate'],
                'strengths': {'illuminate': 0.7},
            }
        })
        self.assertEqual(response.status_code, 200)
        expected = {
            'version': 1,
            'active': ['illuminate', 'obstacle'],
            'strengths': {'illuminate': 0.7, 'obstacle': 1.0},
        }
        self.assertEqual(response.get_json()['plant_modifiers'], expected)
        self.assertEqual(self.channel.commands[-1], {
            'action': 'set_plant_modifiers', 'data': {'plant_modifiers': expected},
        })

        for state in (
            {'active': ['portal', 'hazard']},
            {'active': ['shadow', 'shadow']},
            {'active': ['shadow'], 'strengths': {'shadow': 2.0}},
        ):
            response = self.client.post(
                '/api/config/plant-modifiers', json={'plant_modifiers': state}
            )
            self.assertEqual(response.status_code, 400)

    def test_dashboard_promotes_presets_and_separates_task_areas(self):
        self.interface.preview_manager.list_animations = lambda: [
            {
                'plugin_name': 'sparkle', 'name': 'Sparkle',
                'description': 'Twinkling points', 'author': 'Test', 'version': '1',
            },
            {
                'plugin_name': 'simple_test', 'name': 'Simple Test',
                'description': 'Hardware test', 'author': 'Test', 'version': '1',
            },
        ]
        self.client.post('/api/animations/sparkle/presets', json={
            'name': 'Calm Stars',
            'category': 'Ambient',
            'params': {
                'speed': 0.5,
                'base_red': 1, 'base_green': 2, 'base_blue': 16,
            },
        })

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Calm Stars', html)
        self.assertIn('background: #010210', html)
        self.assertIn('Animation speed', html)
        self.assertIn('Speed multiplier', html)
        self.assertIn('id="librarySearch"', html)
        self.assertIn('id="libraryCategory"', html)
        self.assertIn('id="libraryShowMore"', html)
        self.assertIn('id="dashboard-library"', html)
        self.assertIn('id="dashboard-now-playing"', html)
        self.assertIn('id="dashboard-compose"', html)
        self.assertIn('id="dashboard-system"', html)
        self.assertIn('Check &amp; activate', html)
        self.assertNotIn("onclick=\"takeAnimationLive", html)
        self.assertNotIn('id="showAnimationAccordion"', html)
        self.assertIn('Test & calibration animations', html)
        self.assertIn('id="testAnimationCollapse" class="accordion-collapse collapse"', html)
        self.assertIn('/static/css/dashboard.css', html)
        self.assertIn('/static/js/dashboard.js', html)
        self.assertNotIn('id="previewSavePresetButton"', html)
        self.assertEqual(html.count('Save as preset'), 1)
        css_response = self.client.get('/static/css/dashboard.css')
        js_response = self.client.get('/static/js/dashboard.js')
        try:
            self.assertEqual(css_response.status_code, 200)
            self.assertEqual(js_response.status_code, 200)
            javascript = js_response.get_data(as_text=True)
            self.assertIn('function parameterPresets(info)', javascript)
            self.assertIn('applyParameterPreset', javascript)
            self.assertNotIn('function savePreviewPreset()', javascript)
            self.assertIn('function saveControlPreset()', javascript)
            self.assertNotIn('/api/start/', javascript)
            self.assertIn('Check &amp; activate in Composer', html)
        finally:
            css_response.close()
            js_response.close()

    def test_dashboard_alphabetizes_animations_and_places_tempo_below_preview(self):
        self.interface.preview_manager.list_animations = lambda: [
            {
                'plugin_name': 'wave', 'name': 'Wave',
                'description': 'Rolling bands', 'author': 'Test', 'version': '1',
            },
            {
                'plugin_name': 'sparkle', 'name': 'Sparkle',
                'description': 'Twinkling points', 'author': 'Test', 'version': '1',
            },
        ]

        html = self.client.get('/').get_data(as_text=True)

        self.assertLess(html.index('data-animation-card="sparkle"'), html.index('data-animation-card="wave"'))
        self.assertLess(html.index('id="ledCanvas"'), html.index('id="globalSpeedRange"'))
        self.assertLess(html.index('id="globalSpeedRange"'), html.index('aria-label="Speed presets"'))
        self.assertLess(html.index('id="globalSpeedRange"'), html.index('id="plantModifierControls"'))

    def test_dashboard_uses_task_areas_instead_of_an_independent_sidebar(self):
        html = self.client.get('/').get_data(as_text=True)
        css_response = self.client.get('/static/css/dashboard.css')
        try:
            css = css_response.get_data(as_text=True)
        finally:
            css_response.close()

        self.assertNotIn('dashboard-sidebar-scroll', html)
        self.assertNotIn('max-height: calc(100vh - 2rem)', css)
        self.assertIn('.dashboard-area[hidden]', css)
        self.assertIn('aria-label="Dashboard areas"', html)

    def test_animation_presets_are_alphabetized_by_display_name(self):
        for name in ('zebra', 'Aurora', 'calm'):
            self.client.post('/api/animations/sparkle/presets', json={
                'name': name, 'params': {'brightness': 0.7},
            })

        presets = self.client.get('/api/animations/sparkle/presets').get_json()['presets']

        self.assertEqual([preset['name'] for preset in presets], ['Aurora', 'calm', 'zebra'])

if __name__ == '__main__':
    unittest.main()

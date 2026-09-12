"""Exercise the shipped HA script/templates with a delayed fake HTTP controller.

This is a contract harness, not a replacement for HA configuration validation or
an authorized installed-wall scene/automation acceptance exercise.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment
import yaml

PACKAGE = yaml.safe_load((Path(__file__).resolve().parents[2] /
                          'integrations/home_assistant/led_grid_wall.yaml').read_text())


class ScriptFailure(Exception):
    pass


class WallHarness:
    def __init__(self):
        self.time = 1000.0
        self.state = dict(schema='ledgrid.composer-settings-observation', schema_version=1,
                          controller_session_id='session-a', controller_state_revision=7,
                          observed_at=self.time, is_running=True, brightness=255,
                          animation_speed_scale=0.3, last_command_id=0,
                          active_identity={'scene_digest': 'retained-scene'})
        self.writes = []
        self.reads = 0
        self.pending = None
        self.http_status = 200
        self.apply_commands = True
        self.restart_on_write = False
        self.manual_on_write = False
        self.env = NativeEnvironment(undefined=StrictUndefined)
        self.env.filters['to_json'] = json.dumps
        self.env.globals.update(now=lambda: datetime.fromtimestamp(self.time, timezone.utc),
                                as_timestamp=lambda value: value.timestamp(),
                                has_value=lambda entity: self.available(),
                                state_attr=lambda entity, key: self.state.get(key))

    def render(self, value, variables=None):
        if isinstance(value, str) and ('{{' in value or '{%' in value):
            return self.env.from_string(value).render(**(variables or {}))
        if isinstance(value, dict):
            return {k: self.render(v, variables) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render(v, variables) for v in value]
        return value

    def available(self):
        return self.http_status == 200 and bool(self.render(
            PACKAGE['template'][0]['sensor'][0]['availability'], {'observation_response': {'status': self.http_status, 'content': self.state}}))

    def observe(self):
        self.reads += 1
        if self.pending and self.apply_commands:
            payload, command_id = self.pending
            self.pending = None
            matches = (payload['expected_controller_session_id'] == self.state['controller_session_id']
                       and payload['expected_controller_state_revision'] == self.state['controller_state_revision']
                       and payload['expires_at'] >= self.time)
            self.state['last_command_id'] = command_id
            if matches:
                for field, value in payload.items():
                    if field in ('brightness', 'power', 'multiplier'):
                        key = {'power': 'is_running', 'multiplier': 'animation_speed_scale'}.get(field, field)
                        self.state[key] = value * 0.3 if field == 'multiplier' else value
                self.state['controller_state_revision'] += 1
        self.state['observed_at'] = self.time
        return {'status': self.http_status, 'content': copy.deepcopy(self.state)}

    def run(self, **settings):
        self.sequence(PACKAGE['script']['led_grid_wall_apply']['sequence'], dict(settings))

    def sequence(self, sequence, variables):
        for step in sequence:
            if 'variables' in step:
                for key, value in step['variables'].items():
                    variables[key] = self.render(value, variables)
            elif 'if' in step:
                if self.render(step['if'], variables):
                    self.sequence(step['then'], variables)
            elif 'stop' in step:
                if variables.get('result', {}).get('success'):
                    return
                raise ScriptFailure(step['stop'])
            elif 'delay' in step:
                self.time += step['delay']['milliseconds'] / 1000
            elif 'repeat' in step:
                repeat = step['repeat']
                previous = variables.get('repeat')
                if 'for_each' in repeat:
                    for index, item in enumerate(repeat['for_each'], 1):
                        variables['repeat'] = dict(item=item, index=index)
                        self.sequence(repeat['sequence'], variables)
                else:
                    for index in range(1, 22):
                        variables['repeat'] = dict(index=index)
                        self.sequence(repeat['sequence'], variables)
                        if self.render(repeat['until'], variables):
                            break
                    else:
                        raise AssertionError('Unbounded repeat')
                variables['repeat'] = previous
            elif step.get('action') == 'rest_command.led_grid_wall_observe':
                variables[step['response_variable']] = self.observe()
            elif step.get('action') == 'rest_command.led_grid_wall_command':
                data = self.render(step['data'], variables)
                command_id = len(self.writes) + 1
                self.writes.append(data)
                self.pending = (data['payload'], command_id)
                if self.restart_on_write:
                    self.state['controller_session_id'] = 'session-b'
                if self.manual_on_write:
                    self.state['controller_state_revision'] += 1
                    self.state['brightness'] = 17
                variables[step['response_variable']] = {'status': self.http_status,
                                                       'content': {'command_id': command_id}}
            elif step.get('event') == 'led_grid_wall_refresh':
                self.observe()
            else:
                raise AssertionError(step)


class HomeAssistantPackageTests(unittest.TestCase):
    def test_startup_reconnect_and_manual_changes_only_observe(self):
        wall = WallHarness()
        for brightness in (255, 0, 26, 17):
            wall.state['brightness'] = brightness
            wall.observe()
            self.assertTrue(wall.available())
            light = PACKAGE['template'][1]['light'][0]
            self.assertEqual(wall.render(light['level']), brightness)
        self.assertEqual(wall.writes, [])
        self.assertNotIn('automation', PACKAGE)
        self.assertNotIn('input_number', PACKAGE)
        polling = PACKAGE['template'][0]
        self.assertEqual([step['action'] for step in polling['actions'] if 'action' in step],
                         ['rest_command.led_grid_wall_observe'])
        self.assertEqual(PACKAGE['script']['led_grid_wall_apply']['mode'], 'parallel')

    def test_zero_and_ten_percent_recall_retain_scene(self):
        for brightness in (0, 26):
            with self.subTest(brightness=brightness):
                wall = WallHarness()
                wall.state['is_running'] = False
                wall.run(brightness=brightness, power=True)
                self.assertEqual(wall.state['brightness'], brightness)
                self.assertTrue(wall.state['is_running'])
                self.assertEqual(wall.state['active_identity'], {'scene_digest': 'retained-scene'})
                self.assertEqual([call['route'] for call in wall.writes], ['brightness', 'power'])
                self.assertEqual(wall.writes[1]['payload']['expected_controller_state_revision'], 8)

    def test_power_and_pace_are_independent_of_brightness(self):
        wall = WallHarness()
        wall.run(power=False, multiplier=1.25)
        self.assertFalse(wall.state['is_running'])
        self.assertEqual(wall.state['brightness'], 255)
        self.assertEqual(wall.state['animation_speed_scale'], 0.375)
        number = PACKAGE['template'][2]['number'][0]
        self.assertEqual(wall.render(number['state']), 1.25)
        self.assertEqual(number['unit_of_measurement'], 'x')
        self.assertFalse(number['optimistic'])

    def test_unchanged_request_does_not_send_commands(self):
        wall = WallHarness()
        wall.run(power=True, brightness=255, multiplier=1)
        self.assertEqual(wall.writes, [])

    def test_invalid_settings_never_send_commands(self):
        cases = [{}, {'brightness': True}, {'brightness': 256}, {'brightness': -1},
                 {'brightness': 1.5}, {'power': 1}, {'power': 'false'},
                 {'multiplier': 0}, {'multiplier': True}, {'multiplier': float('nan')},
                 {'multiplier': float('inf')}, {'multiplier': '2'}]
        for settings in cases:
            with self.subTest(settings=settings):
                wall = WallHarness()
                with self.assertRaises(ScriptFailure):
                    wall.run(**settings)
                self.assertEqual(wall.writes, [])

    def test_http_failure_is_unavailable_and_does_not_send(self):
        wall = WallHarness()
        wall.http_status = 503
        self.assertFalse(wall.available())
        with self.assertRaises(ScriptFailure):
            wall.run(brightness=26)
        self.assertEqual(wall.writes, [])

    def test_stale_observation_is_unavailable(self):
        wall = WallHarness()
        wall.time += 16
        self.assertFalse(wall.available())

    def test_command_timeout_never_retries_or_reports_success(self):
        wall = WallHarness()
        wall.apply_commands = False
        with self.assertRaisesRegex(ScriptFailure, 'did not confirm'):
            wall.run(brightness=26, power=False)
        self.assertEqual(len(wall.writes), 1)
        self.assertTrue(wall.state['is_running'])
        self.assertEqual(wall.state['brightness'], 255)
        self.assertLessEqual(wall.reads, 21)

    def test_restart_or_manual_change_rejects_pending_command_without_replay(self):
        for event in ('restart_on_write', 'manual_on_write'):
            with self.subTest(event=event):
                wall = WallHarness()
                setattr(wall, event, True)
                with self.assertRaises(ScriptFailure):
                    wall.run(brightness=26)
                self.assertEqual(len(wall.writes), 1)
                self.assertNotEqual(wall.state['brightness'], 26)
                wall.observe()
                self.assertEqual(len(wall.writes), 1)

    def test_missing_identity_is_unavailable(self):
        wall = WallHarness()
        wall.state['controller_session_id'] = None
        self.assertFalse(wall.available())
        with self.assertRaises(ScriptFailure):
            wall.run(brightness=26)
        self.assertEqual(wall.writes, [])


if __name__ == '__main__':
    unittest.main()

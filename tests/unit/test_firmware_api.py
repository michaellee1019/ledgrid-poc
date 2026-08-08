import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from animation.core.manager import AnimationManager, PreviewLEDController
from ipc.control_channel import FileControlChannel
from web.app import AnimationWebInterface


class Installed:
    def __init__(self, root, package_id='native-rainbow'):
        self.package_id = package_id; self.name = 'Native Rainbow'; self.version = '1.2.0'
        self.kind = 'native'; self.digest = 'a' * 64; self.installed_at = 123.0
        self.manifest = {'description': 'Runs locally', 'parameter_schema': {
            'speed': {'type': 'float', 'min': 0.1, 'max': 3.0, 'default': 1.0},
            'reverse': {'type': 'bool', 'default': False},
        }}
        self.package_path = root / f'{package_id}.lga'; self.package_path.write_bytes(b'installed')


class Library:
    def __init__(self, root): self.root = root; self.items = {'native-rainbow': Installed(root)}
    def list(self): return list(self.items.values())
    def get(self, package_id): return self.items.get(package_id)
    def install(self, _path):
        item = Installed(self.root, 'uploaded'); self.items[item.package_id] = item; return item


class FirmwareApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        os.environ['LEDGRID_DISABLE_PREVIEW_WORKER'] = '1'
        self.channel = FileControlChannel(
            str(root / 'control.json'), str(root / 'status.json'), str(root)
        )
        preview = AnimationManager(PreviewLEDController(32, 138), auto_start=False)
        self.interface = AnimationWebInterface(
            self.channel, preview, firmware_library=Library(root)
        )
        self.client = self.interface.app.test_client()

    def tearDown(self): self.temp.cleanup()

    def test_list_upload_and_dashboard_gallery(self):
        response = self.client.get('/api/firmware-animations')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['animations'][0]['kind'], 'native')
        self.assertFalse(payload['controller']['connected'])
        self.assertEqual(payload['receiver_operation']['state'], 'idle')
        self.assertNotIn('health', payload['animations'][0])
        self.assertNotIn('progress', payload['animations'][0])
        upload = self.client.post('/api/firmware-animations/upload', data={
            'package': (io.BytesIO(b'package'), 'sample.lga')
        }, content_type='multipart/form-data')
        self.assertEqual(upload.status_code, 201)
        self.assertTrue(upload.get_json()['queued'])
        self.assertEqual(self.channel.read_control()['action'], 'firmware_install')
        html = self.client.get('/firmware-animations').get_data(as_text=True)
        for phrase in (
            'Receiver animations', 'Package preview — not live', 'Reported wall playback',
            'Start on wall', 'Apply to playing wall', 'Install on receivers', 'Remove package',
        ):
            self.assertIn(phrase, html)
        self.assertNotIn('>Retry<', html)
        self.assertIn('aria-live="polite"', html)
        script_response = self.client.get('/static/js/firmware_animations.js')
        script = script_response.get_data(as_text=True)
        script_response.close()
        self.assertIn('Number.isInteger', script)
        self.assertIn("window.confirm", script)
        self.assertIn("/api/firmware-animations/stop", script)
        self.assertIn("/${id}/install", script)

    def test_play_validates_types_before_ipc(self):
        invalid = self.client.post('/api/firmware-animations/native-rainbow/play', json={
            'parameters': {'reverse': 'yes'}
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertIsNone(self.channel.read_control())
        valid = self.client.post('/api/firmware-animations/native-rainbow/play', json={
            'parameters': {'speed': 1.5, 'reverse': True}
        })
        self.assertEqual(valid.status_code, 202)
        self.assertTrue(valid.get_json()['queued'])
        self.assertEqual(self.channel.read_control()['action'], 'firmware_play')

    def test_receiver_capability_failure_surfaces_as_page_level_operation(self):
        self.channel.write_status({'driver_stats': {'aggregate': {
            'firmware_install': {
                'state': 'unsupported',
                'progress': 0.0,
                'error': 'one or more receivers lack required capabilities',
            },
        }}})
        response = self.client.get('/api/firmware-animations')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['receiver_operation']['state'], 'unsupported')
        self.assertEqual(payload['receiver_operation']['progress'], 0.0)
        self.assertNotIn('health', payload['animations'][0])

    def test_receiver_operation_reports_affected_devices_once_at_page_level(self):
        self.channel.write_status({'driver_stats': {'aggregate': {
            'firmware_install': {
                'state': 'unsupported',
                'progress': 4,
                'capability_report': {'devices': [
                    {'supported': True}, {'supported': False},
                    {'supported': True}, {'supported': False},
                ]},
            },
        }}})
        payload = self.client.get('/api/firmware-animations').get_json()
        self.assertEqual(payload['receiver_operation']['unsupported_devices'], [1, 3])
        self.assertEqual(payload['receiver_operation']['progress'], 1.0)
        self.assertNotIn('receiver_operation', payload['animations'][0])

    def test_degraded_runtime_reconciliation_is_visible_at_page_level(self):
        self.channel.write_status({'driver_stats': {'aggregate': {
            'firmware_install': {'state': 'ready', 'progress': 1.0},
            'firmware_runtime': {
                'state': 'degraded',
                'operation': 'start_rollback',
                'error': 'could not prove that every receiver stopped local playback',
                'devices': [
                    {'logical_device': 0, 'stopped': True},
                    {'logical_device': 1, 'stopped': False},
                    {'logical_device': 2, 'stopped': True},
                    {'logical_device': 3, 'stopped': False},
                ],
                'command_errors': [
                    {'logical_device': 3, 'error': 'stop failed'},
                ],
            },
        }}})

        payload = self.client.get('/api/firmware-animations').get_json()
        operation = payload['receiver_operation']
        self.assertEqual(operation['state'], 'degraded')
        self.assertEqual(operation['operation'], 'start_rollback')
        self.assertEqual(operation['degraded_devices'], [1, 3])
        self.assertIn('could not prove', operation['error'])

        script = self.client.get(
            '/static/js/firmware_animations.js'
        ).get_data(as_text=True)
        self.assertIn('State uncertain', script)
        self.assertIn('Do not assume playback stopped', script)

    def test_patch_requires_active_and_delete_rejects_active(self):
        inactive = self.client.patch('/api/firmware-animations/native-rainbow/parameters', json={'parameters': {'speed': 2}})
        self.assertEqual(inactive.status_code, 409)
        self.channel.write_status({'mode': 'firmware_animation', 'firmware_animation': {'package_id': 'native-rainbow'}})
        patched = self.client.patch('/api/firmware-animations/native-rainbow/parameters', json={'parameters': {'speed': 2}})
        self.assertEqual(patched.status_code, 202)
        deleted = self.client.delete('/api/firmware-animations/native-rainbow')
        self.assertEqual(deleted.status_code, 409)

    def test_active_values_and_stale_report_are_not_presented_as_live_state(self):
        self.channel.write_status({
            'updated_at': time.time() - 11,
            'mode': 'firmware_animation',
            'firmware_animation': {
                'package_id': 'native-rainbow',
                'parameters': {'speed': 2.5, 'reverse': True},
            },
        })
        payload = self.client.get('/api/firmware-animations').get_json()
        self.assertFalse(payload['controller']['connected'])
        self.assertTrue(payload['controller']['stale'])
        self.assertEqual(payload['controller']['active_package_id'], 'native-rainbow')
        self.assertTrue(payload['animations'][0]['active'])
        self.assertEqual(payload['animations'][0]['parameter_values']['speed'], 2.5)
        html = self.client.get('/firmware-animations').get_data(as_text=True)
        self.assertIn('The controller report is stale.', html)
        self.assertIn('Stop receiver playback', html)

    def test_firmware_stop_is_guarded_by_reported_mode(self):
        inactive = self.client.post('/api/firmware-animations/stop')
        self.assertEqual(inactive.status_code, 409)
        self.assertIsNone(self.channel.read_control())
        self.channel.write_status({
            'mode': 'firmware_animation',
            'firmware_animation': {'package_id': 'native-rainbow'},
        })
        stopped = self.client.post('/api/firmware-animations/stop')
        self.assertEqual(stopped.status_code, 202)
        self.assertEqual(self.channel.read_control()['action'], 'stop')

    def test_install_endpoint_replaces_retry_alias(self):
        installed = self.client.post('/api/firmware-animations/native-rainbow/install')
        self.assertEqual(installed.status_code, 202)
        self.assertEqual(self.channel.read_control()['action'], 'firmware_install')
        self.assertEqual(
            self.client.post('/api/firmware-animations/native-rainbow/retry').status_code,
            404,
        )

    def test_missing_and_bad_upload_failures(self):
        self.assertEqual(self.client.post('/api/firmware-animations/upload').status_code, 400)
        self.assertEqual(self.client.post('/api/firmware-animations/upload', data={
            'package': (io.BytesIO(b'not a package'), 'sample.zip')
        }, content_type='multipart/form-data').status_code, 400)
        self.assertEqual(self.client.post('/api/firmware-animations/missing/play', json={}).status_code, 404)
        self.assertEqual(self.client.delete('/api/firmware-animations/missing').status_code, 404)
        self.assertEqual(
            self.client.get('/api/firmware-animations/native-rainbow/preview').status_code,
            404,
        )

    def test_sdk_unavailable_is_explicit_for_api_and_dashboard(self):
        unavailable = AnimationWebInterface(
            self.channel, self.interface.preview_manager, firmware_library=None
        ).app.test_client()
        payload = unavailable.get('/api/firmware-animations').get_json()
        self.assertFalse(payload['available'])
        self.assertEqual(
            unavailable.post('/api/firmware-animations/missing/play', json={}).status_code,
            503,
        )
        html = unavailable.get('/firmware-animations').get_data(as_text=True)
        self.assertIn('Package management is unavailable', html)


class ManagedPathTests(unittest.TestCase):
    def test_ipc_rejects_bytes_and_unmanaged_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); managed = root / 'managed'; managed.mkdir()
            package = managed / 'ok.lga'; package.write_bytes(b'x')
            outside = root / 'outside.lga'; outside.write_bytes(b'x')
            channel = FileControlChannel(str(root / 'c'), str(root / 's'), str(managed))
            command = channel.send_command('firmware_install', package_path=str(package), package_id='ok')
            self.assertEqual(command['data']['package_path'], str(package.resolve()))
            with self.assertRaises(ValueError): channel.send_command('firmware_install', package_path=str(outside))
            with self.assertRaises(ValueError): channel.send_command('firmware_install', package=b'large')


if __name__ == '__main__': unittest.main()

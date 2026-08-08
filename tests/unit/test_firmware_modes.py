import hashlib
import tempfile
import types
import unittest

from animation.core.manager import AnimationManager
from tools.deployment.preserve_deploy_settings import save_status, load_saved_state


class Controller:
    def __init__(self):
        self.strip_count = 32; self.leds_per_strip = 138; self.total_leds = 4416
        self.num_devices = 4; self.inline_show = True; self.calls = []
        self.stop_result = True
    def install_firmware_asset(self, asset): self.calls.append(('install', asset)); return {}
    def start_firmware_animation(self, asset, params): self.calls.append(('firmware_start', asset, params)); return True
    def stop_firmware_animation(self): self.calls.append(('firmware_stop',)); return self.stop_result
    def update_firmware_parameters(self, params): self.calls.append(('firmware_parameters', params)); return True
    def adopt_firmware_animation(self, asset, params): self.calls.append(('firmware_adopt', asset, params)); return True
    def remove_firmware_asset(self, asset): self.calls.append(('remove', asset)); return True
    def set_all_pixels(self, frame): self.calls.append(('frame', len(frame)))
    def clear(self): self.calls.append(('clear',))
    def configure(self): pass
    def get_stats(self): return {}


class Library:
    def __init__(self):
        self.data = [bytes([index]) * 5 for index in range(4)]
        self.item = types.SimpleNamespace(
            package_id='native-rainbow', name='Native Rainbow', version='1.0',
            kind='native', digest='a' * 64,
            manifest={
                'kind': 'native',
                'parameter_schema': {
                    'speed': {'type': 'float', 'min': 0.1, 'max': 4.0, 'default': 1.0},
                    'reverse': {'type': 'bool', 'default': False},
                },
            },
        )
        self.package = types.SimpleNamespace(
            digest=self.item.digest,
            manifest=self.item.manifest,
            payload_for_device=lambda index: self.data[index],
            verification_envelope=self.verification_envelope_for_device,
        )
    def get(self, package_id): return self.item if package_id == self.item.package_id else None
    def verified(self, package_id):
        if package_id != self.item.package_id:
            raise KeyError(package_id)
        return self.package
    def verification_envelope_for_device(self, index):
        data = self.data[index]
        return types.SimpleNamespace(
            package_digest='a' * 64, key_id='key-' + '0' * 16,
            kind='native', device_index=index, payload_size=len(data),
            payload_digest=hashlib.sha256(data).digest(),
            signed_index=b'I' * 176, signature=b'S' * 64,
        )
    def delete(self, _package_id): pass


class FirmwareModesTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller()
        self.manager = AnimationManager(
            self.controller, firmware_library=Library(),
            animation_speed_scale=1.0, auto_start=False,
        )

    def tearDown(self):
        self.manager.stop_animation(clear_leds=False)

    def test_idle_firmware_painter_python_idle_transitions(self):
        self.assertEqual(self.manager.get_current_status()['mode'], 'idle')
        self.assertTrue(self.manager.start_firmware_animation('native-rainbow', {'reverse': True}))
        firmware = self.manager.get_current_status()
        self.assertEqual(firmware['mode'], 'firmware_animation')
        self.assertEqual(firmware['provider'], 'firmware')
        self.assertIsNone(firmware['target_fps'])
        self.assertIsNone(firmware['plant_modifiers'])
        self.manager.set_painter_frame([(1, 2, 3)] * self.controller.total_leds)
        self.assertEqual(self.manager.get_current_status()['mode'], 'painter')
        self.assertTrue(self.manager.start_animation('gradient', {}))
        self.assertEqual(self.manager.get_current_status()['mode'], 'python')
        self.manager.stop_animation(clear_leds=False)
        self.assertEqual(self.manager.get_current_status()['mode'], 'idle')

    def test_tempo_maps_to_firmware_time_scale_only(self):
        self.manager.start_firmware_animation('native-rainbow', {})
        self.manager.set_animation_speed_scale(1.75)
        call = next(call for call in reversed(self.controller.calls) if call[0] == 'firmware_parameters')
        self.assertEqual(call[1]['time_scale'], 1.75)
        self.assertNotIn('plant_modifiers', call[1])

    def test_restart_adopts_only_verified_receiver_state(self):
        self.manager.adopt_firmware_state('native-rainbow', 'a' * 64, {'speed': 2.0})
        self.assertEqual(self.controller.calls[-1][0], 'firmware_adopt')
        status = self.manager.get_current_status()
        self.assertEqual(status['firmware_animation'], {
            'package_id': 'native-rainbow', 'digest': 'a' * 64,
            'parameters': {'speed': 2.0, 'reverse': False, 'time_scale': 1.0},
        })

    def test_restart_status_round_trip_preserves_only_firmware_controls(self):
        self.manager.adopt_firmware_state('native-rainbow', 'a' * 64, {
            'speed': 1.25, 'reverse': True,
        })
        with tempfile.TemporaryDirectory() as directory:
            state_path = __import__('pathlib').Path(directory) / 'state.json'
            save_status(self.manager.get_current_status(), __import__('pathlib').Path(directory), state_path)
            restored = load_saved_state(state_path)
        self.assertEqual(restored['provider'], 'firmware')
        self.assertEqual(restored['package_digest'], 'a' * 64)
        self.assertEqual(restored['parameters'], {
            'speed': 1.25, 'reverse': True, 'time_scale': 1.0,
        })
        self.assertNotIn('target_fps', restored)
        self.assertNotIn('plant_modifiers', restored)

    def test_controller_boundary_rejects_unmanaged_package_path(self):
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path
            managed = Path(directory) / 'managed.lga'; managed.write_bytes(b'x')
            outside = Path(directory) / 'outside.lga'; outside.write_bytes(b'x')
            self.manager.firmware_library.item.package_path = managed
            self.assertTrue(self.manager.validate_firmware_package_path('native-rainbow', str(managed)))
            with self.assertRaisesRegex(ValueError, 'managed library'):
                self.manager.validate_firmware_package_path('native-rainbow', str(outside))

    def test_failed_receiver_stop_retains_firmware_identity_and_blocks_takeover(self):
        self.assertTrue(self.manager.start_firmware_animation('native-rainbow', {}))
        before = self.manager.get_current_status()['firmware_animation']
        self.controller.stop_result = False

        self.assertFalse(self.manager.stop_animation(clear_leds=False))
        status = self.manager.get_current_status()
        self.assertEqual(status['mode'], 'firmware_animation')
        self.assertEqual(status['firmware_animation'], before)
        self.assertFalse(self.manager.start_animation('gradient', {}))
        self.assertFalse(self.manager.set_painter_frame([(0, 0, 0)] * 4416))
        self.assertFalse(any(call[0] in {'frame', 'clear'} for call in self.controller.calls))


if __name__ == '__main__':
    unittest.main()

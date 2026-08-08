import hashlib
import sys
import threading
import types
import unittest

if 'spidev' not in sys.modules:
    spidev_stub = types.ModuleType('spidev')
    spidev_stub.SpiDev = object
    sys.modules['spidev'] = spidev_stub

from drivers.multi_device import MultiDeviceLEDController
from drivers.spi_controller import (
    CAPABILITY_NATIVE, CAPABILITY_FRAME_TRACK, CAPABILITY_SIGNED_PACKAGES,
    CAPABILITY_ASSET_UPLOAD, CAPABILITY_TYPED_PARAMETERS,
    CAPABILITY_LOGICAL_DEVICE_IDENTITY, CAPABILITY_LOGICAL_DEVICE_SHIFT,
)

FULL_CAPABILITIES = (
    CAPABILITY_NATIVE | CAPABILITY_FRAME_TRACK | CAPABILITY_SIGNED_PACKAGES
    | CAPABILITY_ASSET_UPLOAD | CAPABILITY_TYPED_PARAMETERS
    | CAPABILITY_LOGICAL_DEVICE_IDENTITY
)


class Device:
    MAX_ASSET_CHUNK_BYTES = 4
    def __init__(self, present=False, fail_chunk_once=False, fail_probe=False,
                 fail_start=False, capabilities=FULL_CAPABILITIES,
                 status_version=3, fail_remove=False, sticky_remove=False,
                 active_digest=None, display_mode=0, quarantined=False,
                 fail_parameters=False, fail_abort=False, fail_stop=False,
                 sticky_abort=False, sticky_stop=False, fail_status_calls=None,
                 fail_parameter_calls=None):
        self.present = present; self.fail_chunk_once = fail_chunk_once; self.fail_probe = fail_probe; self.fail_start = fail_start
        self.capabilities = capabilities; self.status_version = status_version; self.fail_remove = fail_remove; self.sticky_remove = sticky_remove
        self.active_digest = active_digest; self.display_mode = display_mode; self.quarantined = quarantined
        self.fail_parameters = fail_parameters
        self.fail_abort = fail_abort; self.sticky_abort = sticky_abort
        self.fail_stop = fail_stop; self.sticky_stop = sticky_stop
        self.fail_status_calls = set(fail_status_calls or ())
        self.fail_parameter_calls = set(fail_parameter_calls or ())
        self.status_calls = 0; self.parameter_calls = 0
        self.logical_device = None
        self.calls = []
    def query_receiver_status(self):
        self.calls.append(('status',))
        self.status_calls += 1
        if self.status_calls in self.fail_status_calls:
            raise OSError('status unavailable')
        return {'receiver_status_version': self.status_version,
                'receiver_capabilities': self.capabilities | (
                    int(self.logical_device or 0) << CAPABILITY_LOGICAL_DEVICE_SHIFT
                ),
                'receiver_logical_device': self.logical_device,
                'receiver_display_mode': self.display_mode,
                'receiver_active_digest': self.active_digest,
                'receiver_quarantine_state': int(self.quarantined)}
    def asset_probe(self, digest):
        self.calls.append(('probe', digest))
        if self.fail_probe:
            return {'receiver_last_result': 10}
        return {'receiver_last_result': 1 if self.present else 15}
    def asset_begin(self, *args): self.calls.append(('begin',) + args); return {'receiver_last_result': 1}
    def asset_abort(self):
        self.calls.append(('abort',))
        if self.fail_abort: raise OSError('abort failed')
        return {
            'receiver_last_result': 1,
            'receiver_upload_state': 1 if self.sticky_abort else 0,
            'receiver_display_mode': 3 if self.sticky_abort else self.display_mode,
        }
    def asset_chunk(self, offset, chunk):
        self.calls.append(('chunk', offset, bytes(chunk)))
        if self.fail_chunk_once:
            self.fail_chunk_once = False
            raise OSError('crc loss')
        return {'receiver_last_result': 1}
    def asset_commit(self, digest): self.calls.append(('commit', digest)); self.present = True; return {'receiver_last_result': 1}
    def start_firmware_animation(self, digest, offset, params):
        self.calls.append(('start', digest, offset, params))
        self.active_digest = digest; self.display_mode = 2
        if self.fail_start: raise OSError('lost start acknowledgement')
        return {'receiver_last_result': 1}
    def stop_firmware_animation(self):
        self.calls.append(('stop',))
        if self.fail_stop: raise OSError('stop failed')
        if not self.sticky_stop:
            self.active_digest = None; self.display_mode = 0
        return {'receiver_last_result': 1}
    def restart_firmware_animation(self): self.calls.append(('restart',)); return {'receiver_last_result': 1}
    def update_firmware_parameters(self, params):
        self.calls.append(('parameters', dict(params)))
        self.parameter_calls += 1
        if self.fail_parameters or self.parameter_calls in self.fail_parameter_calls:
            raise OSError('parameter update failed')
        return {'receiver_last_result': 1}
    def asset_remove(self, digest):
        self.calls.append(('remove', digest))
        if self.fail_remove: raise OSError('rollback remove failed')
        if not self.sticky_remove:
            self.present = False
        return {'receiver_last_result': 1}
    def set_all_pixels(self, colors): self.calls.append(('frame', len(colors)))
    def get_stats(self): return {}


def asset():
    payloads = []
    signed_index = b'I' * 176
    signature = b'S' * 64
    for index in range(4):
        data = bytes([index]) * 9
        digest = hashlib.sha256(data).digest()
        envelope = types.SimpleNamespace(
            package_digest='a' * 64, key_id='key-' + '0' * 16,
            kind='native', device_index=index, payload_size=len(data),
            payload_digest=digest, signed_index=signed_index, signature=signature,
        )
        payloads.append({'logical_device': index, 'data': data,
                         'digest': digest.hex(), 'envelope': envelope})
    return {'package_digest': 'a' * 64, 'kind': 'native', 'abi': 1, 'payloads': payloads}


def controller(devices):
    item = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
    item.devices = devices; item.num_devices = 4; item.strips_per_device = 8
    item.leds_per_strip = 138; item.leds_per_device = 1104; item.total_leds = 4416
    item._transport_lock = __import__('threading').RLock(); item._firmware_active = False
    item._active_payload_digests = []; item._firmware_parameters = {}; item._firmware_install_status = {}
    item._firmware_runtime_state = 'stopped'; item._firmware_runtime_status = {}
    item._executor = None; item._devices_by_bus = {0: list(range(4))}; item._logical_frames_sent = 0
    for index, device in enumerate(devices):
        if device.logical_device is None:
            device.logical_device = index
    return item


class FirmwareHostOrchestrationTests(unittest.TestCase):
    def test_misprovisioned_receiver_identity_blocks_all_asset_io(self):
        devices = [Device(), Device(), Device(), Device()]
        devices[2].logical_device = 3
        item = controller(devices)
        devices[2].logical_device = 3

        with self.assertRaisesRegex(RuntimeError, 'capabilities'):
            item.install_firmware_asset(asset())

        report = item._firmware_install_status['capability_report']['devices'][2]
        self.assertFalse(report['identity_valid'])
        self.assertEqual(report['receiver_logical_device'], 3)
        self.assertFalse(any(call[0] in {'probe', 'begin', 'chunk', 'commit'}
                             for device in devices for call in device.calls))

    def test_cache_probe_skips_existing_and_retry_reuses_same_offset(self):
        devices = [Device(present=True), Device(fail_chunk_once=True), Device(), Device()]
        status = controller(devices).install_firmware_asset(asset())
        self.assertEqual(status['skipped_devices'], [0])
        self.assertFalse(any(call[0] == 'begin' for call in devices[0].calls))
        offsets = [call[1] for call in devices[1].calls if call[0] == 'chunk']
        self.assertEqual(offsets, [0, 0, 4, 8])
        begins = [next(call for call in device.calls if call[0] == 'begin') for device in devices[1:]]
        self.assertTrue(all(call[1].signed_index == b'I' * 176 for call in begins))
        self.assertTrue(all(call[1].signature == b'S' * 64 for call in begins))
        self.assertEqual([payload['envelope'].payload_digest.hex()
                          for payload in asset()['payloads']],
                         [payload['digest'] for payload in asset()['payloads']])

    def test_all_four_probe_failure_starts_no_subset(self):
        devices = [Device(present=True), Device(present=True), Device(), Device(present=True)]
        self.assertFalse(controller(devices).start_firmware_animation(asset(), {}))
        self.assertFalse(any(call[0] == 'start' for device in devices for call in device.calls))

    def test_probe_error_does_not_begin_upload(self):
        devices = [Device(), Device(fail_probe=True), Device(), Device()]
        with self.assertRaisesRegex(RuntimeError, 'probe failed'):
            controller(devices).install_firmware_asset(asset(), retries=1)
        self.assertFalse(any(call[0] in ('begin', 'chunk')
                             for device in devices for call in device.calls))

    def test_sequential_start_uses_logical_strip_offsets(self):
        devices = [Device(present=True) for _ in range(4)]
        self.assertTrue(controller(devices).start_firmware_animation(asset(), {'speed': 2}))
        starts = [next(call for call in device.calls if call[0] == 'start') for device in devices]
        self.assertEqual([call[2] for call in starts], [0, 8, 16, 24])

    def test_restart_adoption_requires_exact_state_on_all_receivers(self):
        descriptor = asset()
        devices = [
            Device(active_digest=payload['digest'], display_mode=2)
            for payload in descriptor['payloads']
        ]
        item = controller(devices)
        self.assertTrue(item.adopt_firmware_animation(descriptor, {'time_scale': 1.0}))
        self.assertTrue(item._firmware_active)

        devices[2].active_digest = '0' * 64
        rejected = controller(devices)
        self.assertFalse(rejected.adopt_firmware_animation(descriptor, {'time_scale': 1.0}))
        self.assertFalse(rejected._firmware_active)

    def test_parameter_failure_rolls_back_receivers_already_updated(self):
        devices = [Device(present=True) for _ in range(4)]
        item = controller(devices)
        self.assertTrue(item.start_firmware_animation(asset(), {'speed': 1.0}))
        devices[2].fail_parameters = True
        self.assertFalse(item.update_firmware_parameters({'speed': 2.0}))
        for device in devices[:2]:
            updates = [call[1] for call in device.calls if call[0] == 'parameters']
            self.assertEqual(updates[-2:], [{'speed': 2.0}, {'speed': 1.0}])

    def test_parameter_rollback_failure_is_recorded_without_escaping(self):
        devices = [Device(present=True, fail_parameter_calls={2}),
                   Device(present=True), Device(present=True), Device(present=True)]
        item = controller(devices)
        self.assertTrue(item.start_firmware_animation(asset(), {'speed': 1.0}))
        devices[2].fail_parameters = True

        self.assertFalse(item.update_firmware_parameters({'speed': 2.0}))

        self.assertEqual(item._firmware_runtime_state, 'degraded')
        self.assertEqual(item._firmware_parameters, {'speed': 1.0})
        runtime = item._firmware_runtime_status
        self.assertEqual(runtime['operation'], 'parameter_rollback')
        self.assertEqual([entry['logical_device']
                          for entry in runtime['command_errors']], [0])
        self.assertEqual(
            [call[1] for call in devices[1].calls if call[0] == 'parameters'][-2:],
            [{'speed': 2.0}, {'speed': 1.0}],
        )

    def test_all_receivers_get_shared_signature_and_device_bound_digest(self):
        devices = [Device() for _ in range(4)]
        descriptor = asset()
        controller(devices).install_firmware_asset(descriptor)
        envelopes = [next(call[1] for call in device.calls if call[0] == 'begin')
                     for device in devices]
        self.assertEqual({envelope.signed_index for envelope in envelopes}, {b'I' * 176})
        self.assertEqual({envelope.signature for envelope in envelopes}, {b'S' * 64})
        self.assertEqual([envelope.device_index for envelope in envelopes], [0, 1, 2, 3])
        self.assertEqual([envelope.payload_digest.hex() for envelope in envelopes],
                         [payload['digest'] for payload in descriptor['payloads']])

    def test_start_ack_failure_stops_every_receiver(self):
        devices = [Device(present=True), Device(present=True, fail_start=True),
                   Device(present=True), Device(present=True)]
        self.assertFalse(controller(devices).start_firmware_animation(asset(), {}))
        self.assertTrue(all(('stop',) in device.calls for device in devices))
        self.assertTrue(all(sum(call[0] == 'status' for call in device.calls) == 2
                            for device in devices))

    def test_start_rollback_retains_degraded_state_when_stop_is_not_proven(self):
        devices = [Device(present=True, sticky_stop=True),
                   Device(present=True, fail_start=True),
                   Device(present=True), Device(present=True)]
        item = controller(devices)

        self.assertFalse(item.start_firmware_animation(asset(), {'speed': 2.0}))

        self.assertTrue(item._firmware_active)
        self.assertEqual(item._firmware_runtime_state, 'degraded')
        self.assertEqual(item._firmware_parameters, {'speed': 2.0})
        runtime = item._firmware_runtime_status
        self.assertEqual(runtime['operation'], 'start_rollback')
        self.assertEqual(runtime['devices'][0]['display_mode'], 2)
        self.assertEqual(runtime['devices'][0]['active_digest'],
                         asset()['payloads'][0]['digest'])
        self.assertFalse(runtime['devices'][0]['stopped'])
        self.assertTrue(all(sum(call[0] == 'status' for call in device.calls) == 2
                            for device in devices))

    def test_stop_clears_active_state_only_after_unanimous_status(self):
        devices = [Device(present=True) for _ in range(4)]
        item = controller(devices)
        self.assertTrue(item.start_firmware_animation(asset(), {'speed': 1.0}))
        devices[2].sticky_stop = True

        self.assertFalse(item.stop_firmware_animation())
        self.assertTrue(item._firmware_active)
        self.assertEqual(item._firmware_runtime_state, 'degraded')
        self.assertEqual(item._firmware_runtime_status['devices'][2]['display_mode'], 2)

        devices[2].sticky_stop = False
        self.assertTrue(item.stop_firmware_animation())
        self.assertFalse(item._firmware_active)
        self.assertEqual(item._firmware_runtime_state, 'stopped')
        self.assertEqual(item._active_payload_digests, [])
        self.assertEqual(item._firmware_parameters, {})

    def test_install_failure_resumes_prior_local_mode(self):
        devices = [Device(), Device(), Device(), Device()]
        def fail(*_args): raise OSError('commit failed')
        devices[2].asset_commit = fail
        item = controller(devices); item._firmware_active = True
        with self.assertRaises(OSError): item.install_firmware_asset(asset(), retries=1)
        self.assertTrue(all(('restart',) in device.calls for device in devices))
        self.assertEqual(item._firmware_install_status['state'], 'retry')
        self.assertFalse(item._firmware_install_status['rollback']['partial_cache_publication'])
        self.assertTrue(item._firmware_install_status['rollback']['verified_absent'])
        self.assertFalse(any(device.present for device in devices[:3]))
        self.assertTrue(all(any(call[0] == 'probe' for call in device.calls)
                            for device in devices[:3]))

    def test_missing_commit_status_is_failure_and_rolls_back(self):
        devices = [Device() for _ in range(4)]
        devices[1].asset_commit = lambda _digest: None
        item = controller(devices)
        with self.assertRaisesRegex(RuntimeError, 'returned no status'):
            item.install_firmware_asset(asset(), retries=1)
        self.assertEqual(item._firmware_install_status['state'], 'retry')
        self.assertTrue(item._firmware_install_status['rollback']['verified_absent'])

    def test_failed_upload_aborts_every_possibly_begun_receiver_before_cleanup(self):
        devices = [Device() for _ in range(4)]
        events = []
        for index, device in enumerate(devices):
            original_abort = device.asset_abort
            original_remove = device.asset_remove
            device.asset_abort = lambda i=index, fn=original_abort: (
                events.append(('abort', i)), fn()
            )[1]
            device.asset_remove = lambda digest, i=index, fn=original_remove: (
                events.append(('remove', i)), fn(digest)
            )[1]

        def lost_begin_ack(*_args):
            devices[2].calls.append(('begin-lost',))
            raise OSError('lost begin acknowledgement')
        devices[2].asset_begin = lost_begin_ack
        item = controller(devices)

        with self.assertRaisesRegex(OSError, 'lost begin acknowledgement'):
            item.install_firmware_asset(asset(), retries=1)

        rollback = item._firmware_install_status['rollback']
        self.assertEqual(rollback['abort_attempted_devices'], [2, 1, 0])
        self.assertEqual(rollback['aborted_devices'], [2, 1, 0])
        self.assertTrue(rollback['upload_abort_complete'])
        self.assertEqual(events[:3], [('abort', 2), ('abort', 1), ('abort', 0)])
        self.assertTrue(all(kind == 'remove' for kind, _index in events[3:]))

    def test_abort_failure_is_reported_while_cache_cleanup_continues(self):
        devices = [Device(), Device(fail_abort=True), Device(), Device()]
        devices[2].asset_chunk = lambda *_args: (_ for _ in ()).throw(
            OSError('upload failed')
        )
        item = controller(devices)

        with self.assertRaisesRegex(OSError, 'upload failed'):
            item.install_firmware_asset(asset(), retries=1)

        rollback = item._firmware_install_status['rollback']
        self.assertFalse(rollback['upload_abort_complete'])
        self.assertEqual([entry['logical_device']
                          for entry in rollback['abort_failed_devices']], [1])
        self.assertTrue(any(call[0] == 'remove' for call in devices[0].calls))

    def test_abort_ok_status_still_fails_if_receiver_remains_in_maintenance(self):
        devices = [Device(), Device(sticky_abort=True), Device(), Device()]
        devices[2].asset_chunk = lambda *_args: (_ for _ in ()).throw(
            OSError('upload failed')
        )
        item = controller(devices)

        with self.assertRaisesRegex(OSError, 'upload failed'):
            item.install_firmware_asset(asset(), retries=1)

        rollback = item._firmware_install_status['rollback']
        self.assertFalse(rollback['upload_abort_complete'])
        self.assertEqual([entry['logical_device']
                          for entry in rollback['abort_failed_devices']], [1])
        self.assertIn('did not leave upload idle',
                      rollback['abort_failed_devices'][0]['error'])

    def test_capability_status_and_upload_share_one_transport_critical_section(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        status_entered = threading.Event()
        release_status = threading.Event()
        frame_done = threading.Event()
        errors = []
        original_status = devices[0].query_receiver_status

        def blocking_status():
            self.assertTrue(item._transport_lock._is_owned())
            status_entered.set()
            self.assertTrue(release_status.wait(1.0))
            return original_status()
        devices[0].query_receiver_status = blocking_status

        def install():
            try:
                item.install_firmware_asset(asset())
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        def frame():
            try:
                item.set_all_pixels([(0, 0, 0)] * item.total_leds)
                frame_done.set()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        install_thread = threading.Thread(target=install)
        frame_thread = threading.Thread(target=frame)
        install_thread.start()
        self.assertTrue(status_entered.wait(1.0))
        frame_thread.start()
        self.assertFalse(frame_done.wait(0.05))
        release_status.set()
        install_thread.join(2.0)
        frame_thread.join(2.0)

        self.assertFalse(install_thread.is_alive())
        self.assertFalse(frame_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(frame_done.is_set())

    def test_install_success_resumes_prior_local_mode(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices); item._firmware_active = True
        self.assertEqual(item.install_firmware_asset(asset())['state'], 'ready')
        self.assertTrue(all(('restart',) in device.calls for device in devices))

    def test_host_frame_stops_local_playback_first(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices); item._firmware_active = True
        item.set_all_pixels([(0, 0, 0)] * item.total_leds)
        self.assertTrue(all(device.calls[0] == ('stop',) for device in devices))
        self.assertFalse(item._firmware_active)

    def test_active_delete_is_rejected(self):
        item = controller([Device() for _ in range(4)])
        descriptor = asset(); item._firmware_active = True
        item._active_payload_digests = [p['digest'] for p in descriptor['payloads']]
        with self.assertRaisesRegex(ValueError, 'active'):
            item.remove_firmware_asset(descriptor)

    def test_missing_tampered_or_oversize_envelope_rejected_before_any_chunk(self):
        for mutation in ('missing', 'tampered', 'oversize', 'reordered'):
            with self.subTest(mutation=mutation):
                devices = [Device() for _ in range(4)]
                descriptor = asset()
                if mutation == 'missing':
                    descriptor['payloads'][0].pop('envelope')
                elif mutation == 'tampered':
                    descriptor['payloads'][2]['envelope'].signature = b'T' * 64
                else:
                    if mutation == 'oversize':
                        descriptor['payloads'][1]['envelope'].signed_index = b'I' * 177
                    else:
                        descriptor['payloads'][0], descriptor['payloads'][1] = (
                            descriptor['payloads'][1], descriptor['payloads'][0]
                        )
                with self.assertRaises(ValueError):
                    controller(devices).install_firmware_asset(descriptor)
                self.assertFalse(any(call[0] in ('begin', 'chunk')
                                     for device in devices for call in device.calls))

    def test_rollback_removes_only_new_publications_and_reports_cleanup_failure(self):
        devices = [Device(present=True), Device(), Device(fail_remove=True), Device()]
        def fail_commit(*_args): raise OSError('commit failed')
        devices[3].asset_commit = fail_commit
        item = controller(devices)
        with self.assertRaises(OSError): item.install_firmware_asset(asset(), retries=1)
        self.assertFalse(any(call[0] == 'remove' for call in devices[0].calls))
        rollback = item._firmware_install_status['rollback']
        self.assertEqual(rollback['attempted_devices'], [3, 2, 1])
        self.assertEqual([entry['logical_device'] for entry in rollback['failed_devices']], [2])
        self.assertTrue(rollback['partial_cache_publication'])
        self.assertTrue(devices[0].present)
        self.assertTrue(devices[2].present)

    def test_rollback_probe_detects_cache_entry_surviving_successful_remove_ack(self):
        devices = [Device(), Device(sticky_remove=True), Device(), Device()]
        def fail_commit(*_args): raise OSError('commit failed')
        devices[2].asset_commit = fail_commit
        item = controller(devices)
        with self.assertRaises(OSError):
            item.install_firmware_asset(asset(), retries=1)
        rollback = item._firmware_install_status['rollback']
        self.assertIn(1, rollback['removed_devices'])
        self.assertEqual(rollback['remaining_devices'], [1])
        self.assertFalse(rollback['verified_absent'])
        self.assertTrue(rollback['partial_cache_publication'])

    def test_heterogeneous_capabilities_block_chunks_and_starts(self):
        devices = [Device(present=True) for _ in range(4)]
        devices[1].capabilities &= ~CAPABILITY_NATIVE
        devices[2].capabilities &= ~CAPABILITY_SIGNED_PACKAGES
        install_item = controller(devices)
        with self.assertRaisesRegex(RuntimeError, 'capabilities'):
            install_item.install_firmware_asset(asset())
        self.assertEqual(install_item._firmware_install_status['state'], 'unsupported')
        self.assertFalse(any(call[0] in ('probe', 'begin', 'chunk')
                             for device in devices for call in device.calls))
        install_report = install_item._firmware_install_status['capability_report']
        self.assertEqual(install_report['devices'][1]['missing_capabilities'],
                         CAPABILITY_NATIVE)
        self.assertTrue(install_report['required_capabilities']
                        & CAPABILITY_ASSET_UPLOAD)

        devices[1].capabilities = FULL_CAPABILITIES
        devices[2].capabilities = FULL_CAPABILITIES
        devices[3].status_version = 2
        play_item = controller(devices)
        self.assertFalse(play_item.start_firmware_animation(asset(), {'speed': 1.0}))
        self.assertFalse(any(call[0] in ('probe', 'start')
                             for device in devices for call in device.calls))
        report = play_item._firmware_install_status['capability_report']
        self.assertFalse(report['devices'][3]['supported'])


if __name__ == '__main__':
    unittest.main()

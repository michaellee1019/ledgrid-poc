"""Real catalog -> guarded API -> controller activation, with mocked transport.

Every component declaration, parameter normalization, native managed artifact,
Scene renderer, Check token and receipt uses production code. Only receiver I/O
is simulated; these tests make no claim about a deployed wall.
"""
from __future__ import annotations

from copy import deepcopy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import numpy as np

from animation.core.activation_qualification import canonical_json_sha256
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.manager import AnimationManager
from animation.core.native_background_library import NativeBackgroundLibrary
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_transaction import FakeInstallationProfileWall, InstallationProfileTransaction
from ipc.playlist_runtime import PlaylistRunner
from ipc.runtime_control import restore_display_state
from tests.unit.test_receiver_native_product_manager import _Controller
from tests.unit.test_scene_activation_api import _global_settings, RELEASE_ID
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_descriptors
from web.local_control import LocalControlChannel
from web.scene_look_store import SceneLookStore
from web.starter_looks import get_starter, list_starters
from tools.deployment.preserve_deploy_settings import save_status, load_saved_state
from tools.deployment.receiver_identity_authority import ReceiverIdentity
from tools.qualification.catalog_live_sweep import (
    animation_components, browser_scene_requests, catalog_cases,
)

ROOT = Path(__file__).resolve().parents[2]


class _Transport(_Controller):
    """In-memory receiver control/telemetry peer, not physical evidence."""
    def __init__(self):
        super().__init__()
        self.reject_next = False
        self.reject_sparse_next = False
        self.sparse_failure_evidence = None
        self.corrupt_next = None
        self.foreground = None
        self.sparse_status = {}
        self.host_full_failure = None
        self.host_full_clear_failure = False
        self.wall_may_be_nonblack = False
        self.host_full_sequence = 0
        self.receiver_identity_authority_digest = "a" * 64
        self.receiver_identities = tuple(
            ReceiverIdentity(
                logical_device=index,
                spi_route=((0, index) if index < 2 else (1, index - 2)),
                hardware_serial=f"02:00:00:00:00:{index:02x}",
                firmware_sha256=f"{index + 1:x}" * 64,
            )
            for index in range(5)
        )
        self.profile_wall = FakeInstallationProfileWall(capacity_bytes=1024*1024)

    def installation_profile_wall(self):
        return self.profile_wall

    def install_installation_profile(self, candidate):
        return InstallationProfileTransaction(self.profile_wall).install(candidate)

    def set_brightness(self, value):
        self.current_brightness = value

    def activate_native_background(self, resolved, *, context, parameters=None, installation_profile_digest=None, deterministic_seed=0):
        if self.reject_next:
            self.reject_next = False
            self.native_status = {'state': 'compensated', 'error': 'receiver 2 rejected native activation: loader_failed', 'payload_digest': resolved.payload_digest}
            return False
        result = super().activate_native_background(resolved, context=context, parameters=parameters, installation_profile_digest=installation_profile_digest)
        self.native_status.update(effective_parameters=deepcopy(parameters), parameter_digest=canonical_json_sha256(parameters), context_digest=context.context_digest.hex(), installation_profile_digest=installation_profile_digest)
        if self.corrupt_next is not None:
            self.native_status.update(self.corrupt_next)
            self.corrupt_next = None
        return result

    def publish_sparse_overlay(self, pixels, **fields):
        self.foreground = pixels.copy()
        if self.reject_sparse_next:
            self.reject_sparse_next = False
            self.sparse_status = {
                'state': 'foreground_cleared',
                'operation': 'foreground_publish_failed',
                'error': (
                    'receiver 1 did not acknowledge foreground commit 0x32; '
                    'sequence 1712, expected 1713'
                ),
                'cleanup_errors': [],
            }
            if self.sparse_failure_evidence is not None:
                self.sparse_status['foreground_publish_evidence'] = deepcopy(self.sparse_failure_evidence)
            return False
        return super().publish_sparse_overlay(pixels, **fields)

    def get_stats(self):
        stats = super().get_stats()
        stats['aggregate']['local_background'] = dict(self.sparse_status)
        return stats

    def present_displayed_host_full_frame(self, request_id, frame, *, timeout_seconds=5.0):
        pixels = np.asarray(frame, dtype=np.uint8)
        self.operations.append(("host_full_display_proof", request_id, pixels.copy()))
        if not np.any(pixels):
            if self.host_full_clear_failure:
                raise RuntimeError("receiver 3 black display timeout")
        else:
            # Model an accepted first SET_ALL whose display is not proven.
            self.wall_may_be_nonblack = True
        if self.host_full_failure is not None:
            failure = self.host_full_failure
            self.host_full_failure = None
            raise RuntimeError(f"receiver 3 {failure}")
        self.host_full_sequence += 1
        self.wall_may_be_nonblack = bool(np.any(pixels))
        return {
            "request_id": request_id,
            "frame_digest": hashlib.sha256(pixels.tobytes()).hexdigest(),
            "authority_digest": "a" * 64,
            "displayed_receivers": [
                {
                    "logical_device": identity.logical_device,
                    "spi_route": list(identity.spi_route),
                    "hardware_serial": identity.hardware_serial,
                    "firmware_sha256": identity.firmware_sha256,
                    "receiver_displayed_sequence": self.host_full_sequence,
                }
                for identity in self.receiver_identities
            ],
        }


class _Channel(LocalControlChannel):
    def read_status(self):
        return {**super().read_status(), 'release_id': RELEASE_ID}


class CanonicalSceneActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web.composer_final_preview import NATIVE_AURORA_BUNDLE_DIGEST
        cls.native_build = ROOT/'run_state/native_background_builds/native_aurora'/NATIVE_AURORA_BUNDLE_DIGEST
        if not (cls.native_build/'bundle.zip').is_file():
            from tests.unit.test_native_aurora import toolchain_available
            if not toolchain_available():
                raise unittest.SkipTest('pinned PlatformIO Xtensa toolchain unavailable')
            from animation.native.builder import build_plugin
            build_plugin(ROOT, 'native_aurora', ROOT/'run_state/native_background_builds')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.library = NativeBackgroundLibrary(self.directory/'native')
        self.library.publish(self.native_build/'bundle.zip')
        self.controller = _Transport()
        self.profile_library = InstallationProfileLibrary(self.directory/'profiles')
        self.profile_digest = self.profile_library.publish((ROOT/'tests/fixtures/installation_profile_v1.bin').read_bytes()).id
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager = AnimationManager(self.controller, native_background_library=self.library, installation_profile_library=self.profile_library, installation_profile_digest=self.profile_digest, auto_start=False, feature_flags=AnimationPipelineFeatureFlags(receiver_local_background=True, receiver_sparse_overlay=True, receiver_geometry_profile=True, receiver_native_modules=True))
        self.manager._launch_animation_loop = lambda: None
        self.addCleanup(self.manager.stop_animation)
        self.channel = _Channel(self.manager)
        self.channel.activation_coordinator.observation_timeout = .02
        self.interface = AnimationWebInterface(self.channel, self.manager, local_mode=True, project_root=ROOT, release_id=RELEASE_ID, activation_enabled=True, activation_token_store_path=self.directory/'tokens.sqlite3')
        self.interface.composer_looks = SceneLookStore(self.directory/'looks.json')
        self.client = self.interface.app.test_client()
        asset_root = Path(os.environ['LEDGRID_TEST_RUNTIME_ASSETS']) if os.environ.get('LEDGRID_TEST_RUNTIME_ASSETS') else None
        self.catalog = self.interface._browser_composer_bootstrap(runtime_asset_root=asset_root)['components']
        self.interface._browser_scene_catalog = lambda: deepcopy(self.catalog)
        self.scene = self.interface._composer_canonical({'origin':'composer','scene': get_starter('aurora')['scene']}).scene
        self.globals = _global_settings(0)

    def envelope(self, scene):
        slots = [('background', scene['background']), ('animation', scene['animation'])]
        slots += [(f"widget:{widget['id']}", widget['component']) for widget in scene['widgets']]
        components = []
        for slot, component in slots:
            record = next(item for item in self.catalog if item['provider'] == component['provider'] and item['plugin_id'] == component['component_id'])
            managed = record['browser_capabilities'].get('managed_identity')
            self.assertIsNotNone(managed, component['component_id'])
            components.append({**{key:managed[key] for key in ('provider','component_id','component_digest','runtime_digest','parameter_schema_version')},'slot_id':slot,'parameters':deepcopy(component['parameters'])})
        return {'schema':'ledgrid.browser-scene-v2','schema_version':1,'scene':deepcopy(scene),'components':components,'installation_profile':{'digest':self.profile_digest}}

    def check(self, scene):
        self.globals['revision'] = self.channel.activation_coordinator.controller_status()['active_identity']['global_settings_identity']['revision']
        request = {'scene':self.envelope(scene),'global_settings':deepcopy(self.globals)}
        response = self.client.post('/api/v1/scene/checks', json=request)
        self.assertEqual(response.status_code,201,response.get_json())
        return request,response.get_json()

    def activate(self, scene):
        request, checked = self.check(scene)
        body = {**request,'check_token':checked['check_token'],'expected_controller_session_id':checked['basis']['controller']['session_id'],'expected_controller_state_revision':checked['basis']['controller']['state_revision']}
        response = self.client.put('/api/v1/scene', json=body, headers={'Idempotency-Key': checked['basis_digest']})
        self.assertIn(response.status_code,(200,202),response.get_json())
        activation_id = response.get_json()['activation_id']
        receipt = self.channel.read_activation_status(activation_id)
        return body,response,receipt

    def activate_browser_scene(self, browser_scene):
        self.globals['revision'] = self.channel.activation_coordinator.controller_status()['active_identity']['global_settings_identity']['revision']
        request = {'scene':browser_scene,'global_settings':deepcopy(self.globals)}
        response = self.client.post('/api/v1/scene/checks', json=request)
        self.assertEqual(response.status_code,201,response.get_json())
        checked = response.get_json()
        body = {**request,'check_token':checked['check_token'],'expected_controller_session_id':checked['basis']['controller']['session_id'],'expected_controller_state_revision':checked['basis']['controller']['state_revision']}
        accepted = self.client.put('/api/v1/scene',json=body,headers={'Idempotency-Key':checked['basis_digest']})
        self.assertIn(accepted.status_code,(200,202),accepted.get_json())
        receipt = self.channel.read_activation_status(accepted.get_json()['activation_id'])
        return body, accepted, receipt

    def test_playlist_runner_accepts_current_production_canonical_scene(self):
        coordinator = self.channel.activation_coordinator
        runner = PlaylistRunner(self.manager, coordinator)
        request = {
            "schema": "ledgrid.playlist-command", "schema_version": 1,
            "request_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()),
            "action": "start", "requested_at": time.time(),
            "playlist_id": str(uuid.uuid4()), "playlist_name": "Aurora",
            "expected_controller_session_id": coordinator.session_id,
            "expected_controller_state_revision": coordinator.state_revision,
            "entries": [{
                "entry_id": str(uuid.uuid4()), "label": "Aurora",
                "duration_seconds": 60, "scene": deepcopy(self.scene),
            }],
        }
        status = runner.start(request)
        self.assertEqual(status["phase"], "running", status)
        self.assertEqual(self.manager.get_scene_state()["schema"], "ledgrid.scene.v2")

    def test_playlist_preparation_failure_survives_restore_in_status_file(self):
        from ipc.control_channel import FileControlChannel
        from scripts.start_server import controller_status_payload

        coordinator = self.channel.activation_coordinator
        clock = [0.0]
        runner = PlaylistRunner(self.manager, coordinator, clock=lambda: clock[0])
        candidate = deepcopy(self.scene)
        candidate['look']['palette_id'] = 'ember'
        command = {
            'schema': 'ledgrid.playlist-command', 'schema_version': 1,
            'request_id': str(uuid.uuid4()), 'run_id': str(uuid.uuid4()),
            'action': 'start', 'requested_at': time.time(),
            'playlist_id': str(uuid.uuid4()), 'playlist_name': 'Preparation diagnostic',
            'expected_controller_session_id': coordinator.session_id,
            'expected_controller_state_revision': coordinator.state_revision,
            'entries': [
                {'entry_id': str(uuid.uuid4()), 'label': str(index),
                 'duration_seconds': 5, 'scene': deepcopy(scene)}
                for index, scene in enumerate((self.scene, candidate))
            ],
        }
        self.assertEqual(runner.start(command)['phase'], 'running')
        self.manager._receiver_last_failure = {'operation': 'older_sparse_failure'}
        prior_operations = len(self.controller.operations)
        error = 'native install failed; compensated=True: receiver 3 command 0x51 was not acknowledged'
        before = time.time()
        clock[0] = 6.0
        with patch.object(self.controller, 'install_native_background', side_effect=RuntimeError(error)):
            status = runner.advance()
        self.assertEqual(status['phase'], 'failed')
        self.assertEqual(status['error'], 'controller rejected playlist entry 2')
        self.assertEqual(self.manager.get_scene_state(), self.scene)
        self.assertEqual(len(self.controller.operations), prior_operations)
        failure = self.manager.get_current_status()['receiver_last_failure']
        self.assertEqual(failure['operation'], 'receiver_hybrid_preparation')
        self.assertEqual(failure['phase'], 'before_presentation_takeover')
        self.assertEqual(failure['scene_digest'], canonical_json_sha256(candidate))
        self.assertEqual(failure['background'], candidate['background']['component_id'])
        self.assertEqual(failure['bundle_digest'], candidate['background']['bundle_digest'])
        self.assertEqual(len(failure['payload_digest']), 64)
        self.assertEqual(failure['error'], error)
        self.assertGreaterEqual(failure['observed_at'], before)

        # Ordinary restoration retains the historical diagnostic, and the
        # production status sink copies it independently from current health.
        self.assertTrue(self.manager.start_scene(self.scene))
        channel = FileControlChannel(str(self.directory/'preparation-control.json'),
                                     str(self.directory/'preparation-status.json'))
        channel.write_status(controller_status_payload(
            self.manager, release_id=RELEASE_ID, last_command_id=None, updated_at=time.time(),
        ))
        self.assertEqual(channel.read_status()['receiver_last_failure'], failure)
        failure['error'] = 'changed detached copy'
        self.assertEqual(self.manager.get_current_status()['receiver_last_failure']['error'], error)

    def test_missing_or_stale_managed_profile_is_rejected_without_mutation(self):
        for digest in ('0'*64, 'f'*64):
            with self.subTest(profile=digest):
                request = {'scene': self.envelope(self.scene), 'global_settings': self.globals}
                request['scene']['installation_profile']['digest'] = digest
                before = deepcopy(self.controller.operations)
                response = self.client.post('/api/v1/scene/checks', json=request)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertIn('profile', response.get_json()['error'])
                self.assertEqual(self.controller.operations, before)
        self.manager.select_installation_profile(None)
        with self.assertRaisesRegex(ValueError, 'verified managed installation profile'):
            self.manager.start_scene(self.scene)

    def test_missing_or_wrong_receiver_receipt_rolls_back_exact_prior_state(self):
        _, _, prior = self.activate(self.scene)
        for corrupt in ({'context_digest': None}, {'payload_digest': 'f'*64}):
            with self.subTest(receipt=corrupt):
                candidate = deepcopy(self.scene)
                candidate['look']['pace'] = .91
                self.controller.corrupt_next = corrupt
                _, _, receipt = self.activate(candidate)
                self.assertEqual(receipt['phase'], 'timed_out', receipt)
                self.assertEqual(receipt['rollback']['result'], 'succeeded', receipt)
                self.assertEqual(receipt['observed_identity'], prior['observed_identity'])
                self.assertEqual(self.manager.get_scene_state(), self.scene)

    def test_file_channel_server_dispatch_publishes_exact_canonical_receipt(self):
        from ipc.control_channel import FileControlChannel
        from scripts.start_server import process_activation_commands
        channel = FileControlChannel(str(self.directory/'control.json'), str(self.directory/'status.json'), str(self.directory/'activations'))
        coordinator = self.channel.activation_coordinator
        coordinator._status_sink = channel.write_activation_status
        channel.write_status(self.channel.read_status())
        self.interface.control_channel = channel
        request, checked = self.check(self.scene)
        body = {**request, 'check_token': checked['check_token'], 'expected_controller_session_id': checked['basis']['controller']['session_id'], 'expected_controller_state_revision': checked['basis']['controller']['state_revision']}
        response = self.client.put('/api/v1/scene', json=body, headers={'Idempotency-Key': checked['basis_digest']})
        self.assertEqual(response.status_code, 202, response.get_json())
        activation_id = response.get_json()['activation_id']
        self.assertEqual(channel.read_activation_status(activation_id)['phase'], 'queued')
        with patch('web.composer_final_preview.ManagedNativeHostPreview', side_effect=AssertionError('receiver must not load desktop native artifact')):
            self.assertEqual(process_activation_commands(channel, coordinator), 1)
        receipt = channel.read_activation_status(activation_id)
        self.assertEqual(receipt['phase'], 'active', receipt)
        self.assertEqual(receipt['observed_identity']['scene_identity']['digest'], canonical_json_sha256(self.scene))
        self.assertEqual(process_activation_commands(channel, coordinator), 0)
        self.assertEqual(self.manager.get_scene_state(), self.scene)

    def test_receiver_activation_does_not_load_desktop_native_peer(self):
        with patch('web.composer_final_preview.ManagedNativeHostPreview', side_effect=AssertionError('receiver must not load desktop native artifact')):
            _, _, receipt = self.activate(self.scene)
        self.assertEqual(receipt['phase'], 'active', receipt)
        self.assertEqual(self.manager._receiver_hybrid_status_snapshot()['preview_kind'], 'host_foreground_only')

    def test_twilight_sparkle_uses_checked_host_full_first_display_proof(self):
        self.globals['output']['brightness'] = 0
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        _, _, receipt = self.activate(scene)
        self.assertEqual(receipt['phase'], 'active', receipt)
        self.assertEqual(receipt['observed_identity']['scene_identity']['digest'], canonical_json_sha256(scene))
        status = self.manager.get_current_status()
        self.assertEqual(status['scene']['provider_mode'], 'host_full_rgb')
        self.assertEqual(status['host_full']['scene_digest'], canonical_json_sha256(scene))
        self.assertEqual(status['host_full']['installation_profile_digest'], self.profile_digest)
        self.assertEqual(len([op for op in self.controller.operations if op[0] == 'host_full_display_proof']), 1)
        self.assertFalse(any(op[0] in ('activate', 'publish') for op in self.controller.operations))
        self.assertEqual(status['brightness'], 0)

    def test_checked_twilight_commit_status_is_saveable_without_native_driver(self):
        self.globals['output']['brightness'] = 0
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        _, _, activation = self.activate(scene)
        self.assertEqual(activation['phase'], 'active', activation)
        status = self.manager.get_current_status()
        self.assertNotIn('receiver_hybrid', status)
        state_path = self.directory/'host-full-desired.json'
        save_status(status, self.directory/'presets', state_path)
        saved = load_saved_state(state_path, provider_policy=self.manager.scene_provider_policy())
        self.assertEqual(saved['scene'], scene)
        self.assertEqual(saved['brightness'], 0)
        self.assertEqual(saved['host_full_expectation']['request_id'],
                         status['host_full']['first_frame_receipt']['request_id'])

    def test_sparkle_with_widget_retains_native_sparse_path(self):
        scene = get_starter('human_twilight_sparkle')['scene']
        scene['widgets'] = get_starter('aurora_clock')['scene']['widgets']
        scene = self.interface._composer_canonical({'origin': 'composer', 'scene': scene}).scene
        _, _, receipt = self.activate(scene)
        self.assertEqual(receipt['phase'], 'active', receipt)
        self.assertTrue(self.manager._receiver_hybrid_mode)
        self.assertFalse(self.manager._canonical_host_full_mode)
        self.assertTrue(any(op[0] == 'publish' for op in self.controller.operations))
        self.assertFalse(any(op[0] == 'host_full_display_proof' for op in self.controller.operations))

    def test_twilight_first_frame_receiver_three_failures_never_become_active(self):
        _, _, prior = self.activate(self.scene)
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        for failure in ('write failure', 'status-v8 integrity failure', 'display timeout'):
            with self.subTest(failure=failure):
                self.controller.host_full_failure = failure
                _, _, receipt = self.activate(scene)
                self.assertNotEqual(receipt['phase'], 'active', receipt)
                self.assertIn(failure, receipt['error'])
                self.assertEqual(self.manager.get_scene_state(), self.scene)
                self.assertEqual(receipt['observed_identity'], prior['observed_identity'])

    def test_twilight_streams_cached_rgb_on_output_ticks_and_stops_on_send_failure(self):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        _, _, receipt = self.activate(scene)
        self.assertEqual(receipt['phase'], 'active', receipt)
        self.manager.target_fps = 200
        self.manager._launch_animation_loop = AnimationManager._launch_animation_loop.__get__(self.manager)
        self.manager._launch_animation_loop()
        deadline = time.monotonic() + 1.0
        while len([op for op in self.controller.operations if op[0] == 'set_all']) < 4 and time.monotonic() < deadline:
            time.sleep(.005)
        sent = [op[1] for op in self.controller.operations if op[0] == 'set_all']
        self.assertGreaterEqual(len(sent), 4)
        np.testing.assert_array_equal(sent[0], sent[1])
        self.assertGreater(self.manager.frames_presented, 2)
        self.assertLess(self.manager._canonical_receiver_runtime._runtime._animation.instance.cadence_snapshot()['tick'],
                        self.manager.frames_presented)
        original_send = self.controller.set_all_pixels
        failure_started_at = time.time()
        self.controller.set_all_pixels = lambda _pixels: False
        deadline = time.monotonic() + 1.0
        while self.manager.is_running and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.manager.is_running)
        failure = self.manager.get_current_status()['receiver_last_failure']
        self.assertEqual(failure['operation'], 'host_full_runtime_failure')
        self.assertEqual(failure['clear_state'], 'verified')
        self.assertGreaterEqual(failure['observed_at'], failure_started_at)
        self.controller.set_all_pixels = original_send

    def test_checked_twilight_stop_requires_displayed_black_safe_idle(self):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        self.globals['output']['brightness'] = 0
        _, _, running = self.activate(scene)
        self.assertEqual(running['phase'], 'active', running)
        self.assertTrue(self.manager.is_running)  # Brightness zero is not power off.
        self.assertEqual(self.manager.output_brightness, 0)
        self.globals['output']['power'] = False
        _, _, stopped = self.activate(scene)
        self.assertEqual(stopped['phase'], 'active', stopped)
        self.assertFalse(self.manager.is_running)
        status = self.channel.read_status()
        evidence = status['host_full_safe_idle']
        self.assertEqual(evidence['state'], 'verified')
        self.assertEqual(evidence['request_id'], f"safe-idle-{stopped['activation_id']}")
        self.assertEqual(evidence['scene_digest'], canonical_json_sha256(scene))
        self.assertEqual(evidence['installation_profile_digest'], self.profile_digest)
        self.assertEqual(len(evidence['receipt']['displayed_receivers']), 5)
        self.assertTrue(any(op[0] == 'host_full_display_proof'
                            and op[1] == evidence['request_id']
                            and not np.any(op[2]) for op in self.controller.operations))
        observed = self.client.get('/api/v1/composer/operations/status').get_json()
        self.assertEqual(observed['reconciliation']['state'], 'current')
        self.assertEqual(observed['output_power']['state'], 'off')
        _, _, repeated = self.activate(scene)
        self.assertEqual(repeated['phase'], 'active', repeated)
        repeated_evidence = self.channel.read_status()['host_full_safe_idle']
        self.assertEqual(repeated_evidence['request_id'],
                         f"safe-idle-{repeated['activation_id']}")
        self.assertNotEqual(repeated_evidence['request_id'], evidence['request_id'])

    def test_checked_twilight_stop_black_failure_never_publishes_off_active(self):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        _, _, running = self.activate(scene)
        self.assertEqual(running['phase'], 'active', running)
        self.controller.host_full_clear_failure = True
        self.globals['output']['power'] = False
        _, _, stopped = self.activate(scene)
        self.assertNotEqual(stopped['phase'], 'active', stopped)
        self.assertIn('black display timeout', stopped['error'])
        self.assertEqual(stopped['rollback']['result'], 'succeeded', stopped)
        self.assertTrue(self.manager.is_running)
        self.assertEqual(self.manager.get_scene_state(), scene)
        self.assertEqual(self.channel.read_status()['receiver_last_failure']['operation'],
                         'host_full_safe_idle_failure')
        observed = self.client.get('/api/v1/composer/operations/status').get_json()
        self.assertNotEqual(observed['output_power']['state'], 'off')
        self.assertNotEqual(observed['reconciliation']['state'], 'current')

    def test_checked_twilight_stop_rejects_misidentified_detached_receipt(self):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        _, _, running = self.activate(scene)
        self.assertEqual(running['phase'], 'active', running)
        original_status = self.manager.get_current_status

        def misidentified_status():
            status = original_status()
            evidence = status.get('host_full_safe_idle')
            if evidence is not None and evidence['state'] == 'verified':
                evidence['receipt']['displayed_receivers'][3]['hardware_serial'] = 'wrong-receiver'
            return status

        self.manager.get_current_status = misidentified_status
        self.globals['output']['power'] = False
        _, _, stopped = self.activate(scene)
        self.assertNotEqual(stopped['phase'], 'active', stopped)
        self.assertIn('desired activation was not freshly observed', stopped['error'])
        self.assertEqual(stopped['rollback']['result'], 'succeeded', stopped)

    def test_checked_twilight_stop_failed_clear_and_restore_reports_degraded(self):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        _, _, running = self.activate(scene)
        self.assertEqual(running['phase'], 'active', running)
        original_present = self.controller.present_displayed_host_full_frame

        def reject_restore(request_id, frame, **kwargs):
            if request_id.startswith('scene-'):
                raise RuntimeError('receiver 3 restore display timeout')
            return original_present(request_id, frame, **kwargs)

        self.controller.present_displayed_host_full_frame = reject_restore
        self.controller.host_full_clear_failure = True
        self.globals['output']['power'] = False
        _, _, stopped = self.activate(scene)
        self.assertEqual(stopped['phase'], 'failed', stopped)
        self.assertEqual(stopped['rollback']['result'], 'failed', stopped)
        self.assertFalse(self.manager.is_running)
        observed = self.client.get('/api/v1/composer/operations/status').get_json()
        self.assertEqual(observed['output_power']['state'], 'failed')
        self.assertIsNone(observed['output_power']['observed'])
        self.assertNotEqual(observed['reconciliation']['state'], 'current')

    def test_native_sparse_power_off_keeps_its_receiver_observation_route(self):
        _, _, running = self.activate(self.scene)
        self.assertEqual(running['phase'], 'active', running)
        coordinator = self.channel.activation_coordinator
        original_evidence = coordinator._receiver_activation_evidence
        receiver_observations = []

        def observed_native(*args, **kwargs):
            receiver_observations.append(True)
            return original_evidence(*args, **kwargs)

        coordinator._receiver_activation_evidence = observed_native
        self.globals['output']['power'] = False
        self.activate(self.scene)
        self.assertTrue(receiver_observations)
        self.assertNotIn('host_full_safe_idle', self.channel.read_status())

    def _assert_runtime_black_clear_status(self, *, clear_fails):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        _, _, receipt = self.activate(scene)
        self.assertEqual(receipt['phase'], 'active', receipt)
        self.assertTrue(self.channel.read_status()['selected_output_power'])
        entered = threading.Event()
        release = threading.Event()
        original_clear = self.controller.present_displayed_host_full_frame

        def paused_clear(request_id, frame, **kwargs):
            if not np.any(frame):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('test black barrier was not released')
            return original_clear(request_id, frame, **kwargs)

        self.controller.present_displayed_host_full_frame = paused_clear
        self.controller.host_full_clear_failure = clear_fails
        worker = threading.Thread(
            target=self.manager._fail_canonical_host_full,
            args=(RuntimeError('complete frame rejected'),),
        )
        worker.start()
        try:
            self.assertTrue(entered.wait(2), 'black barrier did not start')
            pending = self.channel.read_status()
            self.assertFalse(pending['is_running'])
            self.assertEqual(pending['receiver_last_failure']['clear_state'], 'pending')
            observed = self.client.get('/api/v1/composer/operations/status').get_json()
            self.assertEqual(observed['reconciliation']['state'], 'diverged')
            self.assertEqual(observed['output_power']['state'], 'pending')
            self.assertIsNone(observed['output_power']['observed'])
        finally:
            release.set()
            worker.join(2)
            self.controller.present_displayed_host_full_frame = original_clear
        self.assertFalse(worker.is_alive())
        final = self.channel.read_status()['receiver_last_failure']
        self.assertEqual(final['clear_state'], 'failed' if clear_fails else 'verified')
        self.assertEqual(final['clear_error'] is not None, clear_fails)
        observed = self.client.get('/api/v1/composer/operations/status').get_json()
        self.assertEqual(observed['reconciliation']['state'], 'diverged')
        self.assertEqual(observed['output_power']['state'], 'failed' if clear_fails else 'off')

    def test_runtime_black_clear_is_unverified_until_displayed(self):
        self._assert_runtime_black_clear_status(clear_fails=False)

    def test_runtime_black_clear_failure_stays_unverified(self):
        self._assert_runtime_black_clear_status(clear_fails=True)

    def test_twilight_failed_display_and_unverified_rollback_never_claim_active(self):
        self.activate(self.scene)
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        self.controller.host_full_failure = 'display timeout'
        self.controller.reject_next = True
        _, _, receipt = self.activate(scene)
        self.assertEqual(receipt['phase'], 'failed', receipt)
        self.assertIsNone(receipt['observed_identity'])

    def test_failed_first_display_requires_verified_black_for_idle_rollback(self):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        self.controller.host_full_failure = 'display timeout after accepted SET_ALL'
        _, _, receipt = self.activate(scene)
        self.assertNotEqual(receipt['phase'], 'active', receipt)
        self.assertEqual(receipt['rollback']['result'], 'succeeded', receipt)
        self.assertFalse(self.controller.wall_may_be_nonblack)
        proofs = [op for op in self.controller.operations if op[0] == 'host_full_display_proof']
        self.assertEqual(len(proofs), 2)
        self.assertTrue(np.any(proofs[0][2]))
        self.assertFalse(np.any(proofs[1][2]))

    def test_failed_black_proof_cannot_report_idle_rollback(self):
        scene = self.interface._composer_canonical({
            'origin': 'composer', 'scene': get_starter('human_twilight_sparkle')['scene'],
        }).scene
        self.controller.host_full_failure = 'display timeout after accepted SET_ALL'
        self.controller.host_full_clear_failure = True
        _, _, receipt = self.activate(scene)
        self.assertEqual(receipt['phase'], 'failed', receipt)
        self.assertEqual(receipt['rollback']['result'], 'failed', receipt)
        self.assertIn('black display timeout', receipt['rollback']['error'])
        self.assertTrue(self.controller.wall_may_be_nonblack)
        self.assertTrue(self.manager._host_full_first_frame_uncertain)
        self.assertIn('black display timeout',
                      self.manager.get_current_status()['receiver_last_failure']['clear_error'])
        proofs = [op for op in self.controller.operations if op[0] == 'host_full_display_proof']
        self.assertGreaterEqual(len(proofs), 2)
        self.assertTrue(all(not np.any(op[2]) for op in proofs[1:]))

    def test_scene_state_snapshot_cannot_mutate_active_scene_identity(self):
        _, _, receipt = self.activate(self.scene)
        self.assertEqual(receipt['phase'], 'active', receipt)
        expected = deepcopy(self.scene)
        expected_digest = canonical_json_sha256(expected)
        snapshot = self.manager.get_scene_state()
        snapshot['look']['pace'] = .13
        snapshot['animation']['parameters'].clear()
        self.assertEqual(self.manager.get_scene_state(), expected)
        self.assertEqual(
            self.manager._canonical_receiver_scene.identity.digest, expected_digest
        )
        self.assertEqual(
            canonical_json_sha256(self.manager.get_scene_state()), expected_digest
        )

    def test_every_current_catalog_animation_reaches_real_controller_activation(self):
        animations = [item for item in self.catalog if item['role']=='animation' and item['provider']=='python']
        expected_ids = {
            descriptor.component_id for descriptor in current_component_descriptors()
            if descriptor.provider.value == 'python' and descriptor.role.value == 'animation'
        }
        self.assertEqual({item['plugin_id'] for item in animations}, expected_ids)
        self.assertEqual(len(animations), len(expected_ids))
        for item in animations:
            with self.subTest(animation=item['plugin_id']):
                candidate = deepcopy(self.scene)
                candidate['animation']={'component_id':item['plugin_id'],'version':1,'provider':'python','role':'animation','parameters':item['defaults']}
                candidate = self.interface._composer_canonical({'origin':'composer','scene':candidate}).scene
                _,_,receipt = self.activate(candidate)
                self.assertEqual(receipt['phase'],'active',receipt)
                self.assertEqual(self.manager.get_scene_state(),candidate)
                self.assertEqual(receipt['observed_identity']['scene_identity']['digest'],canonical_json_sha256(candidate))
                if item['plugin_id'] == 'sparkle':
                    self.assertEqual(self.manager.get_current_status()['host_full']['scene_digest'],
                                     canonical_json_sha256(candidate))
                else:
                    self.assertEqual(self.controller.context.canonical_final.scene_digest,
                                     canonical_json_sha256(candidate))
                if item['presets']:
                    candidate['animation']['parameters']=item['presets'][0]['params']
                    candidate = self.interface._composer_canonical({'origin':'composer','scene':candidate}).scene
                    self.check(candidate)

    def test_full_current_catalog_matrix_uses_browser_requests_preview_and_exact_receipts(self):
        bootstrap = {'components': deepcopy(self.catalog)}
        animations = animation_components(bootstrap)
        expected_ids = {
            descriptor.component_id for descriptor in current_component_descriptors()
            if descriptor.provider.value == 'python' and descriptor.role.value == 'animation'
        }
        self.assertEqual({item['plugin_id'] for item in animations}, expected_ids)

        presets = {}
        for component_id in sorted(expected_ids):
            response = self.client.get(f'/api/composer/components/{component_id}/presets')
            self.assertEqual(response.status_code, 200, response.get_json())
            presets[component_id] = response.get_json()['presets']
        starters = []
        for summary in list_starters():
            response = self.client.get(f"/api/composer/starters/{summary['id']}")
            self.assertEqual(response.status_code, 200, response.get_json())
            starters.append(response.get_json()['starter'])

        representative = []
        for index in (0, len(animations) // 2, len(animations) - 1):
            component = animations[index]
            scene = deepcopy(self.scene)
            scene['animation'] = {
                'component_id': component['plugin_id'], 'version': 1,
                'provider': 'python', 'role': 'animation',
                'parameters': deepcopy(component['defaults']),
            }
            canonical = self.interface._composer_canonical(
                {'origin': 'composer', 'scene': scene}
            )
            record = self.interface.composer_looks.save_as(
                f"Qualification {index}", canonical
            )
            reopened = self.client.get(f"/api/composer/looks/{record['id']}")
            self.assertEqual(reopened.status_code, 200, reopened.get_json())
            representative.append(reopened.get_json()['look'])

        cases = catalog_cases(
            bootstrap, base_scene=self.scene, presets=presets,
            starters=starters, looks=representative,
        )
        counts = {
            kind: sum(case['kind'] == kind for case in cases)
            for kind in ('default', 'preset', 'starter', 'look')
        }
        self.assertEqual(counts, {
            'default': len(expected_ids),
            'preset': sum(len(value) for value in presets.values()),
            'starter': len(list_starters()),
            'look': 3,
        })
        membership = json.loads(
            (ROOT/'web/composer_preset_membership.v1.json').read_text()
        )['components']
        self.assertEqual(
            counts['preset'],
            sum(len(membership[component_id]['preset_ids']) for component_id in expected_ids),
        )

        observation = self.client.get(
            '/api/v1/composer/settings/observed'
        ).get_json()
        envelopes = browser_scene_requests(
            bootstrap, observation, [case['scene'] for case in cases]
        )
        for case, envelope in zip(cases, envelopes, strict=True):
            with self.subTest(case=case['case_id']):
                self.assertEqual(envelope['schema'], 'ledgrid.browser-scene-v2')
                self.assertEqual(envelope['scene'], case['scene'])
                self.assertEqual(envelope['components'][1]['slot_id'], 'animation')
                preview = self.client.post('/api/composer/preview', json={
                    'origin': 'composer', 'scene': case['scene'],
                    'preview': {'monotonic_elapsed': 1.0,
                                'wall_time': '2026-08-31T13:47:10+00:00'},
                })
                self.assertEqual(preview.status_code, 200, preview.get_json())
                self.assertEqual(preview.get_json()['wall_mutations'], 0)
                with contextlib.redirect_stdout(io.StringIO()):
                    _, _, receipt = self.activate_browser_scene(envelope)
                self.assertEqual(receipt['phase'], 'active', receipt)
                self.assertEqual(
                    receipt['requested_identity'], receipt['observed_identity']
                )
                self.assertEqual(self.manager.get_scene_state(), case['scene'])

        before = len(self.controller.operations)
        invalid = deepcopy(envelopes[0])
        invalid['components'][1]['component_digest'] = '0' * 64
        rejected = self.client.post('/api/v1/scene/checks', json={
            'scene': invalid, 'global_settings': self.globals,
        })
        self.assertEqual(rejected.status_code, 400, rejected.get_json())
        self.assertEqual(
            rejected.get_json()['error'],
            'canonical browser scene animation managed identity is stale',
        )
        self.assertEqual(len(self.controller.operations), before)

    def test_full_composition_receipt_retry_stale_request_and_rollback_are_exact(self):
        scene = deepcopy(self.scene)
        scene['animation']['component_id']='fireworks'
        scene['animation']['parameters']={}
        scene['widgets']=[{'id':'status.clock','visible':True,'component':{'component_id':'clock_overlay','version':1,'provider':'python','role':'widget','parameters':{}},'placement':{'mode':'auto'}}]
        scene['widgets'].append({'id':'message','visible':True,'component':{'component_id':'emoji_arranger','version':1,'provider':'python','role':'widget','parameters':{}},'placement':{'mode':'auto'}})
        scene['widgets'].append({**deepcopy(scene['widgets'][0]),'id':'hidden.clock','visible':False})
        scene['look']={'palette_id':'ember','pace':.35,'presentation_brightness':1.75}
        scene['plants']['effects']={'version':1,'active':['shadow','hue_shift'],'strengths':{'shadow':.37,'hue_shift':.81}}
        scene=self.interface._composer_canonical({'origin':'composer','scene':scene}).scene
        body,response,receipt=self.activate(scene)
        self.assertEqual(receipt['phase'],'active',receipt)
        self.assertEqual([item['slot_id'] for item in receipt['observed_identity']['component_identities']],['background','animation','widget:status.clock','widget:message','widget:hidden.clock'])
        self.assertEqual(self.manager.get_scene_state(),scene)
        self.assertEqual(self.client.get('/api/v1/scene').get_json()['scene'],scene)
        operation_count=len(self.controller.operations)
        retry=self.client.put('/api/v1/scene',json=body,headers={'Idempotency-Key':receipt['basis_digest']})
        self.assertEqual(retry.get_json()['activation_id'],response.get_json()['activation_id'])
        self.assertEqual(len(self.controller.operations),operation_count)
        stale=deepcopy(body); stale['scene']['components'][1]['component_digest']='f'*64
        self.assertEqual(self.client.put('/api/v1/scene',json=stale,headers={'Idempotency-Key':receipt['basis_digest']}).status_code,409)
        self.assertEqual(self.manager.get_scene_state(),scene)
        candidate=deepcopy(scene); candidate['look']['pace']=.9
        request,checked=self.check(candidate)
        self.controller.reject_next=True
        failed={**request,'check_token':checked['check_token'],'expected_controller_session_id':checked['basis']['controller']['session_id'],'expected_controller_state_revision':checked['basis']['controller']['state_revision']}
        failed_response=self.client.put('/api/v1/scene',json=failed,headers={'Idempotency-Key':checked['basis_digest']})
        failed_receipt=self.channel.read_activation_status(failed_response.get_json()['activation_id'])
        self.assertEqual(failed_receipt['phase'],'rolled_back',failed_receipt)
        self.assertIn('receiver 2 rejected native activation: loader_failed', failed_receipt['error'])
        self.assertIn('payload_digest', failed_receipt['error'])
        self.assertEqual(self.manager.get_scene_state(),scene)
        self.assertEqual(failed_receipt['observed_identity'],receipt['observed_identity'])

    def test_sparse_snapshot_failure_receipt_retains_exact_driver_operation(self):
        _, _, prior = self.activate(self.scene)
        candidate = deepcopy(self.scene)
        candidate['look']['pace'] = .91
        request, checked = self.check(candidate)
        self.controller.reject_sparse_next = True
        failed = {
            **request,
            'check_token': checked['check_token'],
            'expected_controller_session_id': checked['basis']['controller']['session_id'],
            'expected_controller_state_revision': checked['basis']['controller']['state_revision'],
        }

        response = self.client.put(
            '/api/v1/scene',
            json=failed,
            headers={'Idempotency-Key': checked['basis_digest']},
        )
        receipt = self.channel.read_activation_status(
            response.get_json()['activation_id']
        )

        self.assertEqual(receipt['phase'], 'rolled_back', receipt)
        self.assertIn('foreground_publish_failed', receipt['error'])
        self.assertIn('receiver 1', receipt['error'])
        self.assertIn('commit 0x32', receipt['error'])
        self.assertIn('sequence 1712, expected 1713', receipt['error'])
        self.assertEqual(receipt['observed_identity'], prior['observed_identity'])
        self.assertEqual(self.manager.get_scene_state(), self.scene)

    def test_large_sparse_proof_uses_file_receipt_summary_and_survives_rollback(self):
        from ipc.control_channel import FileControlChannel
        from ipc.legacy_scene_contract import SceneValidationError, normalize_scene_activation_status
        from scripts.start_server import controller_status_payload, process_activation_commands

        _, _, prior = self.activate(self.scene)
        keys = (
            'receiver_status_version', 'receiver_packets', 'receiver_operation_sequence',
            'receiver_last_processed_command', 'receiver_overlay_committed_generation',
            'receiver_overlay_staged_generation', 'receiver_foreground_scene_revision',
            'receiver_foreground_scene_epoch', 'receiver_foreground_base_revision',
            'receiver_foreground_present_at_scene_time_us', 'receiver_overlay_lease_ms',
            'receiver_overlay_lease_remaining_ms', 'receiver_overlay_commits',
            'receiver_overlay_expirations', 'receiver_overlay_composite_frames',
            'receiver_crc_errors', 'receiver_spi_queue_errors', 'receiver_display_errors',
        )
        rows = [{'logical_device': index, 'status': {key: index + 1 for key in keys}}
                for index in range(5)]
        evidence = {'generation': 2, 'commit_acknowledgements': deepcopy(rows),
                    'post_commit_statuses': deepcopy(rows)}
        self.assertGreater(len(json.dumps(evidence).encode()), 4096)
        with self.assertRaisesRegex(SceneValidationError, '4096-byte limit'):
            normalize_scene_activation_status({
                **prior, 'phase': 'rolling_back', 'error': repr(evidence),
            })

        channel = FileControlChannel(str(self.directory/'large-proof-control.json'),
                                     str(self.directory/'large-proof-status.json'),
                                     str(self.directory/'large-proof-activations'))
        coordinator = self.channel.activation_coordinator
        coordinator._status_sink = channel.write_activation_status
        channel.write_status(self.channel.read_status())
        self.interface.control_channel = channel
        candidate = deepcopy(self.scene)
        candidate['look']['pace'] = .91
        request, checked = self.check(candidate)
        self.controller.reject_sparse_next = True
        self.controller.sparse_failure_evidence = evidence
        body = {**request, 'check_token': checked['check_token'],
                'expected_controller_session_id': checked['basis']['controller']['session_id'],
                'expected_controller_state_revision': checked['basis']['controller']['state_revision']}
        response = self.client.put('/api/v1/scene', json=body,
                                   headers={'Idempotency-Key': checked['basis_digest']})
        self.assertEqual(response.status_code, 202, response.get_json())
        activation_id = response.get_json()['activation_id']
        self.assertEqual(process_activation_commands(channel, coordinator), 1)
        receipt = channel.read_activation_status(activation_id)
        self.assertEqual(receipt['phase'], 'rolled_back', receipt)
        self.assertEqual(receipt['rollback']['result'], 'succeeded', receipt)
        self.assertLess(len(receipt['error'].encode()), 4096)
        self.assertIn('sequence 1712, expected 1713', receipt['error'])
        self.assertEqual(receipt['observed_identity'], prior['observed_identity'])
        self.assertEqual(self.manager.get_scene_state(), self.scene)

        # The normal status file carries detached historical proof, separately
        # from both current health and the bounded terminal receipt.
        channel.write_status(controller_status_payload(
            self.manager, release_id=RELEASE_ID, last_command_id=None, updated_at=time.time(),
        ))
        failure = channel.read_status()['receiver_last_failure']
        self.assertEqual(failure['scene_digest'], canonical_json_sha256(candidate))
        self.assertEqual(failure['publisher']['driver_status']['foreground_publish_evidence'], evidence)
        self.controller.sparse_failure_evidence['generation'] = 99
        self.assertEqual(self.manager.get_current_status()['receiver_last_failure']
                         ['publisher']['driver_status']['foreground_publish_evidence']['generation'], 2)
        self.assertEqual(process_activation_commands(channel, coordinator), 0)

    def test_saved_canonical_scene_roundtrip_and_stale_native_identity_preserve_bytes(self):
        _,_,receipt=self.activate(self.scene)
        self.assertEqual(receipt['phase'],'active',receipt)
        state_path=self.directory/'saved.json'
        status=self.channel.read_status();status['feature_flags']=self.manager.feature_flags.to_dict()
        save_status(status,self.directory/'presets',state_path)
        saved_bytes=state_path.read_bytes()
        saved=load_saved_state(state_path,provider_policy=self.manager.scene_provider_policy())
        self.assertEqual(saved['scene'],self.scene)
        self.assertIsNone(saved['fallback_scene'])
        self.assertTrue(restore_display_state(self.manager,saved))
        self.assertEqual(self.manager.get_scene_state(),self.scene)
        malformed=json.loads(saved_bytes);malformed['scene']['background']['bundle_digest']='d'*64
        state_path.write_text(json.dumps(malformed))
        bad_bytes=state_path.read_bytes()
        with self.assertRaises(RuntimeError):
            load_saved_state(state_path,provider_policy=self.manager.scene_provider_policy())
        self.assertEqual(state_path.read_bytes(),bad_bytes)
        self.assertEqual(self.manager.get_scene_state(),self.scene)

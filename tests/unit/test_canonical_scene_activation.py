"""Real catalog -> guarded API -> controller activation, with mocked transport.

Every component declaration, parameter normalization, native managed artifact,
Scene renderer, Check token and receipt uses production code. Only receiver I/O
is simulated; these tests make no claim about a deployed wall.
"""
from __future__ import annotations

from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from animation.core.activation_qualification import canonical_json_sha256
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.manager import AnimationManager
from animation.core.native_background_library import NativeBackgroundLibrary
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_transaction import FakeInstallationProfileWall, InstallationProfileTransaction
from ipc.runtime_control import restore_display_state
from tests.unit.test_receiver_native_product_manager import _Controller
from tests.unit.test_scene_activation_api import _global_settings, RELEASE_ID
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_descriptors
from web.local_control import LocalControlChannel
from web.scene_look_store import SceneLookStore
from web.starter_looks import get_starter, list_starters
from tools.deployment.preserve_deploy_settings import save_status, load_saved_state
from tools.qualification.catalog_live_sweep import (
    animation_components, browser_scene_requests, catalog_cases,
)

ROOT = Path(__file__).resolve().parents[2]


class _Transport(_Controller):
    """In-memory receiver control/telemetry peer, not physical evidence."""
    def __init__(self):
        super().__init__()
        self.reject_next = False
        self.corrupt_next = None
        self.foreground = None
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
            return False
        result = super().activate_native_background(resolved, context=context, parameters=parameters, installation_profile_digest=installation_profile_digest)
        self.native_status.update(effective_parameters=deepcopy(parameters), parameter_digest=canonical_json_sha256(parameters), context_digest=context.context_digest.hex(), installation_profile_digest=installation_profile_digest)
        if self.corrupt_next is not None:
            self.native_status.update(self.corrupt_next)
            self.corrupt_next = None
        return result

    def publish_sparse_overlay(self, pixels, **fields):
        self.foreground = pixels.copy()
        return super().publish_sparse_overlay(pixels, **fields)


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
                self.assertEqual(self.controller.context.canonical_final.scene_digest,canonical_json_sha256(candidate))
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
        self.assertEqual(self.manager.get_scene_state(),scene)
        self.assertEqual(failed_receipt['observed_identity'],receipt['observed_identity'])

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

"""Focused host-only playback and installed-client retirement contracts."""
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
import unittest
import time

from web.app import AnimationWebInterface
from tests.unit.test_composer_slice import _PreviewManager, _current_scene


class Channel:
    def __init__(self):
        self.commands = []
        self.results = {}
        self.status = {'written_at':time.time(), 'controller_session_id':'controller-1', 'controller_state_revision':2,
                       'last_applied_command_id':0, 'is_running':False, 'brightness':0, 'scene_state':None,
                       'receiver_status':{'connected':[0,1,2,3,4]}}
    def read_status(self):
        return deepcopy(self.status)
    def read_command_result(self, request_id):
        return deepcopy(self.results.get(request_id))
    def send_command(self, action, **data):
        command = {'request_id':'request-1', 'command_id':123, 'action':action, **data}
        self.commands.append(command)
        return command


class SimplePlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.channel = Channel()
        self.interface = AnimationWebInterface(self.channel, _PreviewManager(), project_root=Path(self.tmp.name), activation_enabled=True)
        self.client = self.interface.app.test_client()
    def test_deployment_readiness_uses_a_served_current_endpoint(self):
        import inspect
        from urllib.parse import urlsplit
        from tools.deployment.deploy_target import readiness
        url = inspect.signature(readiness).parameters['api_url'].default
        response = self.client.get(urlsplit(url).path)
        self.assertEqual(response.status_code, 200)
        self.assertIn('controller', response.get_json())

    def test_scene_requests_need_no_qualification_and_do_not_claim_displayed_proof(self):
        response = self.client.put('/api/v1/scene', json={'scene':_current_scene()})
        self.assertEqual(response.status_code, 202, response.get_json())
        self.assertEqual(response.get_json()['state'], 'requested')
        self.assertEqual(response.get_json()['request_id'], 'request-1')
        self.assertEqual(self.channel.commands[0]['action'], 'start_scene')
        self.assertFalse(self.channel.status['is_running'])
        for endpoint in ['/api/v1/scene/checks','/api/v1/scene/activations/old','/api/v1/receiver-native/recover','/api/v1/composer/maintenance','/api/v1/installation-profiles/old/draft']:
            self.assertEqual(self.client.post(endpoint, json={}).status_code, 404)
    def test_unsupported_saved_component_is_rejected_without_substitution(self):
        scene = _current_scene()
        scene['background'] = {'component_id':'native_aurora','version':1,'provider':'receiver_native','role':'background','bundle_digest':'a'*64,'parameters':{}}
        response = self.client.put('/api/v1/scene', json={'scene':scene})
        self.assertEqual(response.status_code, 400)
        self.assertIn('native_aurora', response.get_json()['error'])
        self.assertEqual(self.channel.commands, [])
        self.assertEqual(scene['background']['provider'], 'receiver_native')
    def test_controller_playback_and_receiver_connectivity_are_separate(self):
        self.channel.status.update(is_running=True, scene_state=_current_scene(), last_error='output interrupted', command_result={'request_id':'request-1','state':'failed','error':'output interrupted'})
        response = self.client.get('/api/v1/composer/operations/status').get_json()
        self.assertTrue(response['controller_playback']['running'])
        self.assertEqual(response['receiver_connectivity'], {'connected':[0,1,2,3,4]})
        self.assertNotIn('displayed', response)
        observation = self.client.get('/api/v1/composer/settings/observed').get_json()
        self.assertEqual(observation['brightness'], 0)
        self.assertEqual(observation['command_result']['state'], 'failed')
    def test_stale_controller_file_does_not_become_a_fresh_playback_claim(self):
        published_at = time.time() - 60
        self.channel.status.update(written_at=published_at, is_running=True, scene_state=_current_scene())
        first = self.client.get('/api/v1/composer/settings/observed').get_json()
        second = self.client.get('/api/v1/composer/settings/observed').get_json()
        self.assertEqual(first['observed_at'], published_at)
        self.assertEqual(second['observed_at'], published_at)
        self.assertEqual(first['freshness'], 'stale')
        operations = self.client.get('/api/v1/composer/operations/status').get_json()
        self.assertEqual(operations['controller_playback']['state'], 'unavailable')
        self.assertIsNone(operations['controller_playback']['running'])
        self.assertIsNotNone(first['scene'])
        script = Path('web/static/js/composer_slice.js').read_text()
        self.assertIn("observation.freshness === 'fresh'", script)

    def test_scene_command_failure_survives_a_later_unrelated_command_result(self):
        self.channel.results['00000000-0000-4000-8000-000000000001'] = {'request_id':'00000000-0000-4000-8000-000000000001','state':'failed','error':'renderer failed'}
        self.channel.status.update(command_result={'request_id':'00000000-0000-4000-8000-000000000002','state':'completed','error':None}, controller_playback={'state':'stopped','error':'renderer failed'})
        observed = self.client.get('/api/v1/composer/settings/observed?request_id=00000000-0000-4000-8000-000000000001').get_json()
        self.assertEqual(observed['command_result']['state'], 'failed')
        self.assertEqual(observed['command_result']['request_id'], '00000000-0000-4000-8000-000000000001')
        self.assertEqual(observed['last_error'], 'renderer failed')

    def test_invalid_command_result_request_id_returns_a_client_error(self):
        response = self.client.get('/api/v1/composer/settings/observed?request_id=invalid')
        self.assertEqual(response.status_code, 400)

    def test_retirement_worker_deletes_only_composer_caches_and_unregisters(self):
        source = Path('web/static/composer/composer_sw.js').read_text()
        script = r'''
const assert = require('node:assert/strict'); const vm = require('node:vm');
const handlers = {}, deleted = [], navigated = []; let unregistered = false, skipped = false, completed;
const context = {self:{addEventListener:(type,fn)=>handlers[type]=fn,skipWaiting:()=>{skipped=true},registration:{unregister:async()=>{unregistered=true}},clients:{matchAll:async()=>[{url:'http://local/',navigate:url=>navigated.push(url)}]}},caches:{keys:async()=>['composer-shell-v27','ledgrid-composer-shell-old','other-app'],delete:async name=>deleted.push(name)}};
vm.runInNewContext(process.argv[1],context);handlers.install();handlers.activate({waitUntil:value=>completed=value});
Promise.resolve(completed).then(()=>{assert.equal(skipped,true);assert.equal(unregistered,true);assert.deepEqual(deleted,['composer-shell-v27','ledgrid-composer-shell-old']);assert.deepEqual(navigated,['http://local/']);assert.equal(handlers.fetch,undefined)}).catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['node','-e',script,source], capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(source, Path('web/static/js/composer_service_worker.js').read_text())

    def test_home_assistant_settings_routes_validate_and_correlate_commands(self):
        guard = {
            "expected_controller_session_id": "session-a",
            "expected_controller_state_revision": 7,
            "expires_at": 4_000_000_000.0,
        }
        cases = (
            ("/api/config/power", {"power": False, **guard}, "set_device_state", {"power": False}),
            ("/api/config/brightness", {"brightness": 0, **guard}, "set_output_brightness", {"brightness": 0}),
            ("/api/config/brightness", {"brightness": 26, **guard}, "set_output_brightness", {"brightness": 26}),
            (
                "/api/config/animation-speed",
                {"multiplier": 1.5, **guard},
                "set_animation_speed_scale",
                {"animation_speed_scale": 0.3 * 1.5},
            ),
        )
        for path, body, action, setting in cases:
            with self.subTest(path=path, body=body):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 200, response.get_json())
                command = self.channel.commands[-1]
                self.assertEqual(response.get_json()["command_id"], command["command_id"])
                self.assertEqual(command["action"], action)
                self.assertEqual(
                    {key: command[key] for key in setting},
                    setting,
                )
                self.assertEqual(command["_controller_guard"], guard)

        before = len(self.channel.commands)
        invalid = (
            ("/api/config/power", {}),
            ("/api/config/power", {"power": 1}),
            ("/api/config/power", {"power": True, "animation": "gradient"}),
            ("/api/config/brightness", {"brightness": True}),
            ("/api/config/brightness", {"brightness": -1}),
            ("/api/config/brightness", {"brightness": 256}),
            ("/api/config/animation-speed", {"multiplier": 0}),
            ("/api/config/animation-speed", {"multiplier": "nan"}),
            ("/api/config/power", {
                "power": True,
                "expected_controller_session_id": "session-a",
            }),
        )
        for endpoint, body in invalid:
            with self.subTest(endpoint=endpoint, invalid=body):
                self.assertEqual(self.client.post(endpoint, json=body).status_code, 400)
        self.assertEqual(len(self.channel.commands), before)


    def test_guarded_request_refuses_an_old_controller_without_applied_id(self):
        self.channel.status.pop("last_applied_command_id")
        before = len(self.channel.commands)
        response = self.client.post("/api/config/power", json={
            "power": False,
            "expected_controller_session_id": "session-a",
            "expected_controller_state_revision": 7,
            "expires_at": 4_000_000_000.0,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.channel.commands), before)


    def test_observed_settings_distinguish_processed_and_applied_command_ids(self):
        self.channel.status.update({
            "controller_session_id": "session-a",
            "controller_state_revision": 9,
            "last_command_id": 13.0,
            "last_applied_command_id": 12.0,
            "brightness": 26,
            "animation_speed_scale": 1.5,
        })

        response = self.client.get("/api/v1/composer/settings/observed")

        self.assertEqual(response.status_code, 200)
        observed = response.get_json()
        self.assertEqual(observed["last_command_id"], 13.0)
        self.assertEqual(observed["last_applied_command_id"], 12.0)
        self.assertEqual(observed["brightness"], 26)

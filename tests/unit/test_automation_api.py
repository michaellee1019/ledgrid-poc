"""Exercise the automation HTTP boundary against the current Scene v2 controller."""
from copy import deepcopy
import json
import time
from unittest.mock import patch

import pytest

from animation.core.manager import AnimationManager, PreviewLEDController
from ipc.control_channel import FileControlChannel
from ipc.runtime_control import controller_activation_coordinator
from scripts.start_server import controller_status_payload, handle_command, process_control_command
from web.app import AnimationWebInterface
from web.automation_api import PREFIX, installation_identity
from web.starter_looks import get_starter


class Controller(PreviewLEDController):
    def get_stats(self):
        return {'devices': [{'device_index': i, 'receiver_connected': i != 2,
                             'crc_errors': 17 if i == 2 else 0} for i in range(5)]}


@pytest.fixture
def wall(tmp_path, monkeypatch):
    monkeypatch.delenv('LEDGRID_DATA_DIR', raising=False)
    manager = AnimationManager(Controller(33, 138), auto_start=False)
    manager.set_output_brightness(91)
    # Keep real startup, normalization and presentation. Disabling only the
    # background loop makes transitions deterministic without wall hardware.
    manager._launch_animation_loop = lambda: None
    assert handle_command(manager, 'start_scene', {'scene': get_starter('aurora')['scene']})
    channel = FileControlChannel(tmp_path / 'control.json', tmp_path / 'status.json')
    channel.write_status(controller_status_payload(manager))
    interface = AnimationWebInterface(channel, manager, project_root=tmp_path)
    yield interface, manager, channel
    manager.stop_animation()


def guard(interface):
    current = interface.automation.status()
    return {'expected_server_session_id': current['server_session_id'],
            'expected_controller_session_id': current['controller_session_id'],
            'expected_controller_state_revision': current['controller_state_revision'],
            'expires_at': time.time() + 10}


def submit(interface, **settings):
    return interface.app.test_client().post(PREFIX + '/commands', json={
        **guard(interface), 'action': 'set_device_state', **settings})


def execute(manager, channel):
    commands = channel.poll_commands()
    assert len(commands) == 1
    return process_control_command(channel, manager, commands[0])


def test_reads_are_inert_compact_and_identity_survives_web_restart_url_and_data_replacement(wall):
    interface, manager, channel = wall
    client = interface.app.test_client()
    before = controller_status_payload(manager)
    identity = client.get(PREFIX + '/capabilities').get_json()['installation_id']
    channel.write_status({**before, 'frame_data': ['NEVER PROJECT'] * 4554,
                          'animation_info': {'blob': 'NEVER PROJECT'}})
    status = client.get(PREFIX + '/status').get_json()
    assert 'NEVER PROJECT' not in json.dumps(status)
    assert len(status['receiver_connectivity']) == 5
    assert status['controller_playback']['running'] is True
    assert status['physical_display_verified'] is False
    assert channel.poll_commands() == []
    assert controller_status_payload(manager)['controller_state_revision'] == before['controller_state_revision']
    replacement = AnimationWebInterface(channel, manager, port=9876, project_root=interface.project_root)
    assert replacement.automation.installation_id == identity
    assert replacement.automation.server_session_id != interface.automation.server_session_id
    assert replacement.automation.identity_path.is_file()
    assert replacement.app.test_client().get(PREFIX + '/catalog').status_code == 200
    catalog = replacement.automation.catalog()
    assert any(item['component_id'] == 'snake' for item in catalog['components'])
    assert not any(item['provider'] != 'python' or item['component_id'] == 'world_flags' for item in catalog['components'])


def test_zero_and_omitted_settings_are_preserved_and_acceptance_is_not_completion(wall):
    interface, manager, channel = wall
    client = interface.app.test_client()
    original_scene = deepcopy(manager.get_scene_state())
    original_vibe = deepcopy(manager.get_vibe_status())
    original_plants = deepcopy(manager.plant_modifier_state.to_dict())
    response = submit(interface, brightness=0, pace=2)
    assert response.status_code == 202
    receipt = response.get_json()
    assert receipt['state'] == 'accepted'
    assert manager.output_brightness == 91
    assert client.get(receipt['result_url']).get_json()['state'] == 'accepted'
    execute(manager, channel)
    result = client.get(receipt['result_url'] + '?wait_seconds=2').get_json()
    assert result['state'] == 'completed'
    assert result['status']['settings']['brightness'] == 0
    assert result['status']['settings']['pace'] == 2
    assert result['controller_state_revision'] == result['status']['controller_state_revision']
    assert manager.get_scene_state() == original_scene
    assert manager.get_vibe_status() == original_vibe
    assert manager.plant_modifier_state.to_dict() == original_plants
    assert manager.is_running
    assert channel.poll_commands() == []


def test_power_off_on_with_combined_brightness_resumes_current_scene(wall):
    interface, manager, channel = wall
    expected_scene = deepcopy(manager.get_scene_state())
    assert submit(interface, power=False).status_code == 202
    execute(manager, channel)
    assert manager.is_running is False
    assert submit(interface, power=True, brightness=0, pace=1.3).status_code == 202
    execute(manager, channel)
    assert manager.is_running
    assert manager.output_brightness == 0
    assert manager.get_scene_state() == expected_scene
    assert manager.animation_speed_scale == pytest.approx(0.39)


@pytest.mark.parametrize('change,expected', [
    ({'expected_controller_session_id': 'old'}, 409),
    ({'expected_controller_state_revision': 99}, 409),
    ({'expected_server_session_id': 'old'}, 409),
    ({'expires_at': 0}, 400),
    ({'expires_at': 1e20}, 400),
    ({'brightness': True}, 400),
    ({'brightness': -1}, 400),
    ({'pace': 26}, 400),
    ({'pace': float('nan')}, 400),
    ({'power': 'true'}, 400),
    ({'unexpected': True}, 400),
])
def test_invalid_and_conflicting_commands_never_queue(wall, change, expected):
    interface, manager, channel = wall
    payload = {**guard(interface), 'action': 'set_device_state', 'brightness': 0, **change}
    response = interface.app.test_client().post(PREFIX + '/commands', json=payload)
    assert response.status_code == expected
    assert channel.poll_commands() == []
    assert manager.output_brightness == 91


def test_guard_is_rechecked_by_real_controller_after_interleaved_mutation(wall):
    interface, manager, channel = wall
    receipt = submit(interface, brightness=0).get_json()
    assert handle_command(manager, 'set_output_brightness', {'brightness': 77})
    result = execute(manager, channel)
    assert result['state'] == 'failed'
    assert 'revision changed' in result['error']
    assert manager.output_brightness == 77
    assert interface.app.test_client().get(receipt['result_url']).get_json()['state'] == 'failed'


def test_expiry_checked_at_execution_and_bounded_wait_reports_acceptance(wall):
    interface, manager, channel = wall
    receipt = submit(interface, brightness=0).get_json()
    assert interface.app.test_client().get(receipt['result_url'] + '?wait_seconds=0.01').get_json()['state'] == 'accepted'
    expiry = channel.poll_commands()[0]['data']['_controller_guard']['expires_at']
    with patch('ipc.runtime_control.time.time', return_value=expiry + 1):
        result = execute(manager, channel)
    assert result['state'] == 'failed'
    assert 'expired' in result['error']
    assert manager.output_brightness == 91


def test_controller_failure_is_correlated_and_does_not_restore(wall, monkeypatch):
    interface, manager, channel = wall
    receipt = submit(interface, brightness=0).get_json()
    monkeypatch.setattr(manager, 'set_output_brightness', lambda _: (_ for _ in ()).throw(RuntimeError('transport failed')))
    assert execute(manager, channel)['state'] == 'failed'
    result = interface.app.test_client().get(receipt['result_url']).get_json()
    assert result['error'] == 'transport failed'
    assert result['command_id'] == receipt['command_id']


def test_web_restart_never_replays_and_rejects_old_intent(wall):
    interface, manager, channel = wall
    old_guard = guard(interface)
    receipt = submit(interface, brightness=0).get_json()
    execute(manager, channel)
    replacement = AnimationWebInterface(channel, manager, project_root=interface.project_root)
    assert channel.poll_commands() == []
    client = replacement.app.test_client()
    assert client.get(receipt['result_url']).status_code == 404
    assert client.post(PREFIX + '/commands', json={**old_guard, 'action': 'set_device_state', 'brightness': 91}).status_code == 409
    assert channel.poll_commands() == []
    assert manager.output_brightness == 0


def test_controller_restart_invalidates_queued_command_and_timeout_never_retries(wall):
    interface, manager, channel = wall
    receipt = submit(interface, brightness=0).get_json()
    manager._controller_command_coordinator = None
    restarted = controller_activation_coordinator(manager)
    channel.write_status(controller_status_payload(manager))
    assert interface.app.test_client().get(receipt['result_url']).get_json()['state'] == 'failed'
    assert execute(manager, channel)['state'] == 'failed'
    assert manager.output_brightness == 91
    assert restarted.state_revision == 0
    next_receipt = submit(interface, brightness=0).get_json()
    with patch('web.automation_api.time.time', return_value=time.time()+11):
        result = interface.app.test_client().get(next_receipt['result_url']).get_json()
    assert result['state'] == 'failed'
    assert 'timed out' in result['error']
    assert len(channel.poll_commands()) == 1
    assert manager.output_brightness == 91


def test_stale_controller_or_disabled_activation_fail_closed(wall):
    interface, manager, channel = wall
    payload = {**guard(interface), 'action': 'set_device_state', 'brightness': 0}
    channel.write_status({**controller_status_payload(manager), 'written_at': time.time()-6})
    assert interface.app.test_client().post(PREFIX + '/commands', json=payload).status_code == 503
    assert channel.poll_commands() == []
    interface.activation_enabled = False
    assert interface.app.test_client().post(PREFIX + '/commands', json=payload).status_code == 503


def test_external_scene_reconciles_only_after_real_controller_completion(wall):
    interface, manager, channel = wall
    manager.set_output_brightness(0)
    next_scene = deepcopy(get_starter('aurora')['scene'])
    next_scene['look']['pace'] = 0.77
    payload = {**guard(interface), 'expected_composer_revision': 0}
    receipt = interface.automation.publish_scene(next_scene, payload)
    assert interface.composer_live.snapshot()['revision'] == 0
    assert not interface.working_draft.path.exists()
    execute(manager, channel)
    result = interface.automation.result(receipt['request_id'])
    assert result['state'] == 'completed'
    assert result['reconciliation']['state'] == 'completed'
    recovery = interface.working_draft.get()
    assert recovery['scene'] == manager.get_scene_state()
    assert interface.composer_live.snapshot(include_current_scene=True)['current_scene'] == manager.get_scene_state()
    assert manager.output_brightness == 0
    assert len(interface.composer_control.commands) == 1
    assert channel.poll_commands() == []


def test_interleaved_composer_edit_is_preserved_during_external_reconciliation(wall):
    interface, manager, channel = wall
    next_scene = deepcopy(get_starter('aurora')['scene'])
    next_scene['look']['pace'] = 0.77
    receipt = interface.automation.publish_scene(next_scene, {**guard(interface), 'expected_composer_revision': 0})
    newer_scene = deepcopy(next_scene)
    newer_scene['look']['pace'] = 1.2
    interface._composer_submit_scene(newer_scene, client_id='phone')
    execute(manager, channel)
    result = interface.automation.result(receipt['request_id'])
    assert result['state'] == 'completed'
    assert result['reconciliation']['state'] == 'conflict'
    assert interface.working_draft.get()['scene']['look']['pace'] == 1.2
    assert interface.composer_live.snapshot(include_current_scene=True)['current_scene']['look']['pace'] == 1.2
    with pytest.raises(ValueError, match='Composer revision'):
        interface.automation.publish_scene(next_scene, {**guard(interface), 'expected_composer_revision': 0})


def test_installation_identity_fails_closed_on_corruption(tmp_path):
    identity = tmp_path / 'installation.json'
    installation_identity(identity)
    identity.write_text('{broken')
    with pytest.raises(ValueError):
        installation_identity(identity)
    assert identity.read_text() == '{broken'


def test_scene_recovery_failure_is_separate_from_controller_completion(wall, monkeypatch):
    interface, manager, channel = wall
    scene = get_starter('aurora')['scene']
    receipt = interface.automation.publish_scene(scene, {**guard(interface), 'expected_composer_revision': 0})
    execute(manager, channel)
    monkeypatch.setattr(interface.working_draft, 'save', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    result = interface.automation.result(receipt['request_id'])
    assert result['state'] == 'completed'
    assert result['reconciliation'] == {'state': 'failed', 'error': 'disk full'}
    assert manager.is_running
    assert channel.poll_commands() == []


def test_result_rejects_wrong_correlation_and_invalid_wait(wall):
    interface, manager, channel = wall
    receipt = submit(interface, brightness=0).get_json()
    client = interface.app.test_client()
    assert client.get(receipt['result_url'] + '?wait_seconds=3').status_code == 400
    assert client.get(receipt['result_url'] + '?wait_seconds=nan').status_code == 400
    command = channel.poll_commands()[0]
    channel.acknowledge_command(command, {'request_id': receipt['request_id'], 'command_id': 1, 'state': 'completed'})
    assert client.get(receipt['result_url']).status_code == 502
    assert manager.output_brightness == 91

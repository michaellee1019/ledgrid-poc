"""Content HTTP actions execute the real Scene controller and playlist runner."""
from copy import deepcopy
import json
from pathlib import Path
import time
import uuid
from unittest.mock import patch

import pytest

from tests.unit.test_automation_api import wall, guard, execute
from ipc.playlist_runtime import PlaylistRunner
from ipc.runtime_control import controller_activation_coordinator
from scripts.start_server import controller_status_payload, handle_command, process_playlist_commands
from web.automation_api import PREFIX
from web.starter_looks import get_starter


@pytest.fixture
def content_wall(wall):
    interface, manager, channel = wall
    interface.composer_presets.root = Path(__file__).resolve().parents[2]
    return interface, manager, channel


def request_content(interface, identity, **changes):
    return interface.app.test_client().post(PREFIX + '/commands', json={
        **guard(interface), 'expected_composer_revision': interface.composer_live.snapshot()['revision'],
        'action': 'play_content', 'content_id': identity, **changes})


def playlist(interface, *, unsupported=False):
    scene = get_starter('aurora')['scene']
    if unsupported:
        scene['animation']['component_id'] = 'world_flags'
    return interface.composer_playlists.save({'name': 'Evening', 'entries': [
        {'entry_id': str(uuid.uuid4()), 'label': 'First', 'duration_seconds': 1, 'scene': scene},
        {'entry_id': str(uuid.uuid4()), 'label': 'Second', 'duration_seconds': 1, 'scene': get_starter('aurora_clock')['scene']}]})


def runner_for(manager, channel):
    clock = [0.0]
    runner = PlaylistRunner(manager, clock=lambda: clock[0], status_sink=channel.write_playlist_current_status)
    channel.write_playlist_current_status(runner.status())
    return runner, clock


def test_catalog_includes_every_current_animation_and_preset_without_activation(content_wall):
    interface, manager, channel = content_wall
    response = interface.app.test_client().get(PREFIX + '/catalog')
    assert response.status_code == 200
    entries = response.get_json()['content']
    animations = {item['id'] for item in entries if item['kind'] == 'animation'}
    assert animations == {f'animation:python:{d.component_id}:v{d.version}' for d in interface.composer_catalog.descriptors if d.role.value == 'animation'}
    presets = {item['id'] for item in entries if item['kind'] == 'preset'}
    assert presets == {f"preset:python:{d.component_id}:{p['preset_id']}" for d in interface.composer_catalog.descriptors
                       for p in interface._list_component_presets(d.component_id, 'python')}
    assert len(presets) > 100
    assert all(item['supported'] for item in entries)
    assert channel.poll_commands() == [] and channel.poll_playlist_commands('test') == []
    assert manager.output_brightness == 91


def test_animation_selection_starts_stopped_wall_preserving_zero_and_unrelated_scene(content_wall):
    interface, manager, channel = content_wall
    scene = get_starter('aurora_clock')['scene']
    assert handle_command(manager, 'start_scene', {'scene': scene})
    assert handle_command(manager, 'stop', {})
    manager.set_output_brightness(0)
    channel.write_status(controller_status_payload(manager))
    response = request_content(interface, 'animation:python:gradient:v1')
    assert response.status_code == 202
    receipt = response.get_json()
    assert receipt['state'] == 'accepted' and not manager.is_running
    execute(manager, channel)
    result = interface.automation.result(receipt['request_id'])
    assert result['state'] == 'completed' and result['reconciliation']['state'] == 'completed'
    current = manager.get_scene_state()
    assert manager.is_running and manager.output_brightness == 0
    assert current['animation']['component_id'] == 'gradient'
    for key in ('background', 'look', 'widgets', 'plants'):
        assert current[key] == scene[key]
    assert interface.working_draft.get()['scene'] == current
    assert result['status']['active_content']['animation'] == 'animation:python:gradient:v1'


def test_preset_applies_declared_parameters_effects_and_preserves_other_fields(content_wall):
    interface, manager, channel = content_wall
    descriptor = next(d for d in interface.composer_catalog.descriptors if d.component_id == 'lava_lamp')
    preset = next(p for p in interface.composer_presets.choices('lava_lamp') if p.get('installation_effects'))
    before = manager.get_scene_state()
    receipt = request_content(interface, f"preset:python:lava_lamp:{preset['preset_id']}").get_json()
    execute(manager, channel)
    result = interface.automation.result(receipt['request_id'])
    assert result['state'] == 'completed'
    current = manager.get_scene_state()
    assert current['animation']['parameters'] == preset['parameters']
    assert current['plants']['effects'] == preset['installation_effects']
    for key in ('look', 'widgets', 'background'):
        assert current[key] == before[key]
    assert manager.output_brightness == 91
    interface.automation.catalog()
    assert interface.automation.status()['active_content']['preset'] == f"preset:python:lava_lamp:{preset['preset_id']}"


def test_widget_preset_preserves_stable_widget_id_and_unrelated_widgets(content_wall):
    interface, manager, channel = content_wall
    scene = get_starter('aurora_clock')['scene']
    handle_command(manager, 'start_scene', {'scene': scene})
    channel.write_status(controller_status_payload(manager))
    preset = interface.composer_presets.choices('clock_overlay')[0]
    receipt = request_content(interface, f"preset:python:clock_overlay:{preset['preset_id']}").get_json()
    execute(manager, channel)
    assert interface.automation.result(receipt['request_id'])['state'] == 'completed'
    after = manager.get_scene_state()
    assert after['widgets'][0]['id'] == scene['widgets'][0]['id']
    assert after['animation'] == scene['animation']
    assert after['widgets'][0]['component']['parameters'] == preset['parameters']


def test_look_selection_complete_scene_observed_matching_rename_deletion(content_wall):
    interface, manager, channel = content_wall
    scene = get_starter('aurora_clock')['scene']
    canonical = interface._composer_canonical({'origin': 'composer', 'scene': scene})
    look = interface.composer_looks.save_as('Gradient Field', canonical)
    catalog = interface.automation.catalog()['content']
    item = next(item for item in catalog if item['id'] == 'look:' + look['id'])
    animation = next(item for item in catalog if item['id'] == 'animation:python:gradient:v1')
    assert item['label'] != animation['label']
    receipt = request_content(interface, item['id']).get_json()
    assert interface.automation.status()['active_content']['look'] is None
    execute(manager, channel)
    assert interface.automation.result(receipt['request_id'])['reconciliation']['state'] == 'completed'
    assert manager.get_scene_state() == canonical.scene
    assert interface.automation.status()['active_content']['look'] == item['id']
    assert interface._composer_opened_look_id == look['id']
    interface.composer_looks.rename(look['id'], 'Renamed')
    changed = next(entry for entry in interface.automation.catalog()['content'] if entry['id'] == item['id'])
    assert changed['name'] == 'Renamed' and changed['id'] == item['id']
    interface.composer_looks.delete(look['id'])
    assert request_content(interface, item['id']).status_code == 400
    assert channel.poll_commands() == []


def test_unsupported_saved_references_are_reported_and_preserved(content_wall):
    interface, manager, channel = content_wall
    record = playlist(interface, unsupported=True)
    before = interface.composer_playlists.path.read_bytes()
    entry = next(item for item in interface.automation.catalog()['content'] if item['id'] == 'playlist:' + record['id'])
    assert entry['supported'] is False and entry['error']
    response = request_content(interface, entry['id'])
    assert response.status_code == 400 and response.get_json()['code'] == 'unsupported_content'
    assert interface.composer_playlists.path.read_bytes() == before
    assert channel.poll_playlist_commands('test') == []


def test_playlist_start_advance_stop_retains_current_scene_and_reconciles(content_wall):
    interface, manager, channel = content_wall
    saved = playlist(interface)
    runner, clock = runner_for(manager, channel)
    manager.set_output_brightness(0)
    channel.write_status(controller_status_payload(manager))
    receipt = request_content(interface, 'playlist:' + saved['id']).get_json()
    assert manager.get_scene_state()['animation']['component_id'] == 'aurora_curtains'
    process_playlist_commands(channel, runner)
    result = interface.automation.result(receipt['request_id'])
    assert result['state'] == 'completed'
    assert result['controller_state_revision'] == 2
    assert result['status']['active_content']['playlist'] == 'playlist:' + saved['id']
    assert interface.working_draft.get()['scene'] == manager.get_scene_state()
    clock[0] = 1.1
    runner.advance()
    channel.write_status(controller_status_payload(manager))
    status = interface.automation.status()
    assert status['playlist']['current_index'] == 1
    assert interface.working_draft.get()['scene'] == manager.get_scene_state()
    retained = deepcopy(manager.get_scene_state())
    response = interface.app.test_client().post(PREFIX + '/commands', json={**guard(interface),
        'expected_composer_revision': status['composer_revision'], 'action': 'stop_playlist', 'run_id': runner.status()['run_id']})
    assert response.status_code == 202
    process_playlist_commands(channel, runner)
    stopped = interface.automation.result(response.get_json()['request_id'])
    assert stopped['state'] == 'completed' and runner.status()['phase'] == 'stopped'
    assert manager.is_running and manager.get_scene_state() == retained and manager.output_brightness == 0


def test_playlist_completion_failure_manual_takeover_and_editor_conflict(content_wall, monkeypatch):
    interface, manager, channel = content_wall
    saved = playlist(interface)
    runner, clock = runner_for(manager, channel)
    receipt = request_content(interface, 'playlist:' + saved['id']).get_json()
    process_playlist_commands(channel, runner)
    interface.automation.result(receipt['request_id'])
    interface._composer_submit_scene(get_starter('aurora_conway')['scene'], client_id='phone')
    clock[0] = 1.1
    runner.advance()
    channel.write_status(controller_status_payload(manager))
    assert interface.automation.status()['playlist']['reconciliation']['state'] == 'conflict'
    assert interface.working_draft.get()['scene']['animation']['component_id'] == 'fireworks'
    handle_command(manager, 'set_output_brightness', {'brightness': 0})
    assert runner.advance()['phase'] == 'overridden'
    channel.write_status(controller_status_payload(manager))
    receipt = request_content(interface, 'playlist:' + saved['id']).get_json()
    process_playlist_commands(channel, runner)
    assert interface.automation.result(receipt['request_id'])['state'] == 'completed'
    clock[0] += 1.1
    runner.advance()
    clock[0] += 1.1
    assert runner.advance()['phase'] == 'completed'
    assert not manager.is_running
    channel.write_status(controller_status_payload(manager))
    receipt = request_content(interface, 'playlist:' + saved['id']).get_json()
    process_playlist_commands(channel, runner)
    assert interface.automation.result(receipt['request_id'])['state'] == 'completed'
    monkeypatch.setattr(manager, 'start_scene', lambda _: False)
    clock[0] += 1.1
    assert runner.advance()['phase'] == 'failed'


def test_playlist_expiry_rechecked_and_stop_targets_current_run(content_wall):
    interface, manager, channel = content_wall
    saved = playlist(interface)
    runner, clock = runner_for(manager, channel)
    receipt = request_content(interface, 'playlist:' + saved['id']).get_json()
    queued = channel.poll_playlist_commands(runner.coordinator.session_id)[0]
    with patch('ipc.playlist_runtime.time.time', return_value=queued['expires_at'] + 1):
        process_playlist_commands(channel, runner)
    result = interface.automation.result(receipt['request_id'])
    assert result['state'] == 'failed' and 'expired' in result['error']
    assert runner.status()['phase'] == 'idle'
    channel.write_status(controller_status_payload(manager))
    response = interface.app.test_client().post(PREFIX + '/commands', json={**guard(interface),
        'expected_composer_revision': 0, 'action': 'stop_playlist', 'run_id': str(uuid.uuid4())})
    assert response.status_code == 409


def test_manual_controller_scene_is_adopted_by_ha_selection(content_wall):
    interface, manager, channel = content_wall
    handle_command(manager, 'start_scene', {'scene': get_starter('aurora_clock')['scene']})
    channel.write_status(controller_status_payload(manager))
    assert interface.automation.status()['active_content']['animation'] == 'animation:python:firefly_synchrony:v1'


def test_unsupported_saved_look_is_preserved_and_not_substituted(content_wall):
    interface, manager, channel = content_wall
    canonical = interface._composer_canonical({'origin': 'composer', 'scene': get_starter('aurora')['scene']})
    look = interface.composer_looks.save_as('Old reference', canonical)
    path = interface.composer_looks.path
    stored = json.loads(path.read_text())
    stored['looks'][0]['scene']['animation']['component_id'] = 'world_flags'
    path.write_text(json.dumps(stored))
    before = path.read_bytes()
    item = next(item for item in interface.automation.catalog()['content'] if item['id'] == 'look:' + look['id'])
    assert item['supported'] is False and item['error']
    assert request_content(interface, item['id']).status_code == 400
    assert path.read_bytes() == before
    assert channel.poll_commands() == []


def test_rejected_playlist_replacement_preserves_current_run_output_and_follow_context(content_wall):
    interface, manager, channel = content_wall
    saved = playlist(interface)
    runner, clock = runner_for(manager, channel)
    receipt = request_content(interface, 'playlist:' + saved['id']).get_json()
    process_playlist_commands(channel, runner)
    interface.automation.result(receipt['request_id'])
    expected_scene = manager.get_scene_state()
    expected_run = runner.status()['run_id']
    expected_context = interface.automation.content.follow
    response = request_content(interface, 'playlist:' + saved['id'])
    assert response.status_code == 409
    assert 'stop it before' in response.get_json()['error']
    assert runner.status()['run_id'] == expected_run
    assert manager.is_running and manager.get_scene_state() == expected_scene
    assert interface.automation.content.follow is expected_context
    assert interface.automation.status()['active_content']['playlist'] == 'playlist:' + saved['id']
    assert channel.poll_playlist_commands(runner.coordinator.session_id) == []


def test_playlist_stop_guard_rechecked_after_intervening_controller_change(content_wall):
    interface, manager, channel = content_wall
    saved = playlist(interface)
    runner, clock = runner_for(manager, channel)
    start = request_content(interface, 'playlist:' + saved['id']).get_json()
    process_playlist_commands(channel, runner)
    status = interface.automation.result(start['request_id'])['status']
    stop = interface.app.test_client().post(PREFIX + '/commands', json={**guard(interface),
        'expected_composer_revision': status['composer_revision'], 'action': 'stop_playlist', 'run_id': runner.status()['run_id']}).get_json()
    retained = manager.get_scene_state()
    handle_command(manager, 'set_output_brightness', {'brightness': 0})
    process_playlist_commands(channel, runner)
    result = interface.automation.result(stop['request_id'])
    assert result['state'] == 'failed' and 'revision changed' in result['error']
    assert manager.get_scene_state() == retained and manager.output_brightness == 0
    assert runner.advance()['phase'] == 'overridden'

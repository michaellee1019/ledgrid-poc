"""Advanced patches share current Scene normalizers and controller execution."""
from copy import deepcopy

import pytest

from tests.unit.test_automation_content import content_wall, request_content
from tests.unit.test_automation_api import wall, execute, guard
from scripts.start_server import controller_status_payload, handle_command
from web.automation_api import PREFIX
from web.starter_looks import get_starter


def advanced(interface, action='patch_scene', **fields):
    current = interface.automation.status()
    payload = {**guard(interface), 'action': action, **fields}
    if action != 'set_target_fps':
        payload.update(expected_composer_revision=current['composer_revision'], expected_scene_digest=current['scene_controls']['digest'])
    return interface.app.test_client().post(PREFIX + '/commands', json=payload)


def apply(interface, manager, channel, patch):
    response = advanced(interface, patch=patch)
    assert response.status_code == 202, response.get_json()
    execute(manager, channel)
    result = interface.automation.result(response.get_json()['request_id'])
    assert result['state'] == 'completed', result
    return result


@pytest.mark.parametrize('patch', [
    {'look': {'pace': 0}}, {'look': {'pace': 2}},
    {'look': {'presentation_brightness': 0}}, {'look': {'presentation_brightness': 2}},
    {'look': {'palette_id': 'neutral'}}, {'look': {'palette_id': 'mist'}},
    {'look': {'palette_id': 'ember'}}, {'look': {'palette_id': 'spectrum'}},
    {'background_brightness': 0}, {'background_brightness': 1},
    {'plants': {'illuminate': {'enabled': True, 'strength': 0}}},
    {'plants': {'illuminate': {'enabled': True, 'strength': 1}}},
    {'plants': {'shadow': {'enabled': True, 'strength': .2}}},
    {'plants': {'hue_shift': {'enabled': True, 'strength': .7}}},
])
def test_common_scene_controls_bounds_and_unrelated_fields(content_wall, patch):
    interface, manager, channel = content_wall
    before = deepcopy(manager.get_scene_state())
    manager.set_output_brightness(0)
    result = apply(interface, manager, channel, patch)
    after = manager.get_scene_state()
    assert manager.output_brightness == 0 and manager.is_running
    for key in ('animation', 'widgets'):
        assert after[key] == before[key]
    if 'look' not in patch:
        assert after['look'] == before['look']
    if 'plants' not in patch:
        assert after['plants'] == before['plants']
    if 'background_brightness' not in patch:
        assert after['background'] == before['background']
    assert result['reconciliation']['state'] == 'completed'
    assert interface.working_draft.get()['scene'] == after


@pytest.mark.parametrize('patch', [
    {}, {'unknown': 1}, {'look': {}}, {'look': {'pace': -1}}, {'look': {'pace': 2.1}},
    {'look': {'pace': True}}, {'look': {'presentation_brightness': float('nan')}},
    {'look': {'presentation_brightness': 3}}, {'look': {'palette_id': 'unknown'}},
    {'background_brightness': -.1}, {'background_brightness': 1.1},
    {'plants': {'unknown': {'enabled': True}}}, {'plants': {'illuminate': {'enabled': 'true'}}},
    {'plants': {'illuminate': {'enabled': True, 'strength': -1}}},
    {'plants': {'illuminate': {'strength': .3}}},
    {'plants': {'attractor': {'enabled': True}, 'repulsor': {'enabled': True}}},
    {'plants': {'obstacle': {'enabled': True}, 'portal': {'enabled': True}}},
    {'component': {'target': 'animation', 'component_id': 'aurora_curtains'}},
    {'component': {'target': 'animation', 'component_id': 'aurora_curtains', 'parameters': {'invented': 1}}},
    {'component': {'target': 'background', 'component_id': 'solid_background', 'parameters': {'gain': 3}}},
    {'component': {'target': 'background', 'component_id': 'solid_background', 'parameters': {'unknown': 1}}},
])
def test_invalid_patches_do_not_queue_or_change_scene(content_wall, patch):
    interface, manager, channel = content_wall
    before = deepcopy(manager.get_scene_state())
    response = advanced(interface, patch=patch)
    assert response.status_code == 400, response.get_json()
    assert channel.poll_commands() == []
    assert manager.get_scene_state() == before


@pytest.mark.parametrize('fps', [1, 200])
def test_target_fps_is_independent_and_preserves_stopped_power(content_wall, fps):
    interface, manager, channel = content_wall
    handle_command(manager, 'stop', {})
    manager.set_output_brightness(0)
    channel.write_status(controller_status_payload(manager))
    response = advanced(interface, 'set_target_fps', target_fps=fps)
    assert response.status_code == 202
    execute(manager, channel)
    assert interface.automation.result(response.get_json()['request_id'])['state'] == 'completed'
    assert manager.target_fps == fps and not manager.is_running and manager.output_brightness == 0


@pytest.mark.parametrize('fps', [0, 201, 1.5, True])
def test_invalid_target_fps_rejected(content_wall, fps):
    interface, manager, channel = content_wall
    assert advanced(interface, 'set_target_fps', target_fps=fps).status_code == 400
    assert channel.poll_commands() == []


def test_stopped_scene_edit_resumes_selected_scene_at_zero(content_wall):
    interface, manager, channel = content_wall
    handle_command(manager, 'stop', {})
    manager.set_output_brightness(0)
    channel.write_status(controller_status_payload(manager))
    apply(interface, manager, channel, {'look': {'pace': .2}})
    assert manager.is_running and manager.output_brightness == 0
    assert manager.get_scene_state()['look']['pace'] == .2


def test_component_parameters_text_emoji_and_gif_use_current_normalizers(content_wall):
    interface, manager, channel = content_wall
    for component_id, parameters in [('ascii_drop', {'phrase': 'HA WALL'}), ('emoji', {'face': 'heart'}),
                                     ('gif_animation', {'playback_speed': 1.25, 'fit_mode': 'contain'})]:
        response = request_content(interface, f'animation:python:{component_id}:v1')
        assert response.status_code == 202
        execute(manager, channel)
        interface.automation.result(response.get_json()['request_id'])
        before = deepcopy(manager.get_scene_state())
        apply(interface, manager, channel, {'component': {'target': 'animation', 'component_id': component_id, 'parameters': parameters}})
        after = manager.get_scene_state()
        for key, value in parameters.items():
            assert after['animation']['parameters'][key] == value
        assert after['look'] == before['look']


def test_multiple_widgets_require_stable_id_and_preserve_other_instances(content_wall):
    interface, manager, channel = content_wall
    scene = get_starter('aurora_clock')['scene']
    duplicate = deepcopy(scene['widgets'][0])
    duplicate['id'] = 'second-clock'
    scene['widgets'].append(duplicate)
    handle_command(manager, 'start_scene', {'scene': scene})
    channel.write_status(controller_status_payload(manager))
    apply(interface, manager, channel, {'component': {'target': 'widget', 'widget_id': 'second-clock',
        'component_id': 'clock_overlay', 'parameters': {'show_seconds': False}}})
    current = manager.get_scene_state()
    assert current['widgets'][0] == scene['widgets'][0]
    assert current['widgets'][1]['component']['parameters']['show_seconds'] is False
    apply(interface, manager, channel, {'widget': {'operation': 'update', 'id': 'second-clock', 'component_id': 'clock_overlay',
        'placement': {'mode': 'manual', 'strip_translation': 1, 'led_translation': 7}, 'visible': False}})
    current = manager.get_scene_state()
    assert current['widgets'][0] == scene['widgets'][0]
    assert current['widgets'][1]['placement']['strip_translation'] == 1
    assert current['widgets'][1]['visible'] is False
    widget = {'id': 'ha-message', 'component': {'component_id': 'emoji_arranger', 'provider': 'python', 'version': 1,
        'role': 'widget', 'parameters': {'text': 'HI🔥'}}, 'visible': True, 'placement': {'mode': 'auto'}}
    apply(interface, manager, channel, {'widget': {'operation': 'add', 'widget': widget}})
    apply(interface, manager, channel, {'widget': {'operation': 'remove', 'id': 'second-clock'}})
    assert [item['id'] for item in manager.get_scene_state()['widgets']] == ['clock', 'ha-message']
    assert advanced(interface, patch={'component': {'target': 'widget', 'component_id': 'clock_overlay', 'parameters': {'show_seconds': True}}}).status_code == 400
    assert advanced(interface, patch={'widget': {'operation': 'remove', 'id': 'missing'}}).status_code == 409
    assert advanced(interface, patch={'widget': {'operation': 'add', 'widget': widget}}).status_code == 400


def test_scene_and_component_identity_conflicts_do_not_queue(content_wall):
    interface, manager, channel = content_wall
    current = interface.automation.status()
    response = interface.app.test_client().post(PREFIX + '/commands', json={**guard(interface), 'action': 'patch_scene',
        'expected_composer_revision': current['composer_revision'], 'expected_scene_digest': 'old', 'patch': {'look': {'pace': .3}}})
    assert response.status_code == 409
    assert advanced(interface, patch={'component': {'target': 'animation', 'component_id': 'other', 'parameters': {'seed': 1}}}).status_code == 409
    assert channel.poll_commands() == []


def test_supported_gesture_reaches_active_renderer_without_reset_or_scene_replacement(content_wall, monkeypatch):
    interface, manager, channel = content_wall
    response = request_content(interface, 'animation:python:lava_lamp:v1')
    execute(manager, channel)
    interface.automation.result(response.get_json()['request_id'])
    manager.set_output_brightness(0)
    before = deepcopy(manager.get_scene_state())
    runtime = manager._canonical_receiver_runtime
    animation = runtime._runtime._animation.instance
    pending = len(animation._interactions)
    monkeypatch.setattr(manager, 'start_scene', lambda _: (_ for _ in ()).throw(AssertionError('gesture restarted Scene')))
    response = advanced(interface, 'interact', component_id='lava_lamp', kind='primary', x=12, y=70, strength=.7)
    assert response.status_code == 202
    execute(manager, channel)
    assert interface.automation.result(response.get_json()['request_id'])['state'] == 'completed'
    assert manager._canonical_receiver_runtime is runtime
    assert runtime._runtime._animation.instance is animation
    assert len(animation._interactions) == pending + 1
    assert manager.get_scene_state() == before and manager.output_brightness == 0
    for fields in [{'component_id': 'other'}, {'kind': 'invented'}, {'strength': 0}, {'x': 33}, {'y': 138}]:
        payload = dict(component_id='lava_lamp', kind='primary', x=12, y=70, strength=.7)
        payload.update(fields)
        assert advanced(interface, 'interact', **payload).status_code in {400, 409}
    assert channel.poll_commands() == []
    assert manager.get_scene_state() == before
    handle_command(manager, 'stop', {})
    channel.write_status(controller_status_payload(manager))
    assert advanced(interface, 'interact', component_id='lava_lamp', kind='primary', x=1, y=2).status_code == 400
    assert not manager.is_running and channel.poll_commands() == []


def test_legacy_global_component_parameters_and_invalid_background_seed_are_rejected(content_wall):
    interface, manager, channel = content_wall
    response = request_content(interface, 'animation:python:gif_animation:v1')
    execute(manager, channel)
    interface.automation.result(response.get_json()['request_id'])
    assert advanced(interface, patch={'component': {'target': 'animation', 'component_id': 'gif_animation', 'parameters': {'speed': 2}}}).status_code == 400
    assert advanced(interface, patch={'component': {'target': 'background', 'component_id': 'solid_background', 'parameters': {'seed': 'bad'}}}).status_code == 400
    assert channel.poll_commands() == []


def test_gesture_execution_guard_and_handler_rejection_preserve_renderer_state(content_wall, monkeypatch):
    interface, manager, channel = content_wall
    receipt = request_content(interface, 'animation:python:lava_lamp:v1').get_json()
    execute(manager, channel)
    interface.automation.result(receipt['request_id'])
    runtime = manager._canonical_receiver_runtime
    animation = runtime._runtime._animation.instance
    scene = manager.get_scene_state()
    response = advanced(interface, 'interact', component_id='lava_lamp', kind='primary', x=12, y=70)
    handle_command(manager, 'set_output_brightness', {'brightness': 0})
    execute(manager, channel)
    assert interface.automation.result(response.get_json()['request_id'])['state'] == 'failed'
    assert not animation._interactions
    monkeypatch.setattr(animation, 'handle_interaction', lambda *args: False)
    response = advanced(interface, 'interact', component_id='lava_lamp', kind='primary', x=12, y=70)
    execute(manager, channel)
    assert interface.automation.result(response.get_json()['request_id'])['state'] == 'failed'
    assert manager.get_scene_state() == scene and manager._canonical_receiver_runtime is runtime
